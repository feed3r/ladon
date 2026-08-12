# pyright: reportPrivateUsage=false, reportUnknownMemberType=false
"""Concurrency contracts shared by both asynchronous HTTP backends."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from time import monotonic
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from ladon.networking._async_policy_base import AsyncPolicyBase
from ladon.networking.circuit_breaker import CircuitState
from ladon.networking.config import HttpClientConfig
from ladon.networking.errors import (
    CircuitOpenError,
    RateLimitedError,
    TransientNetworkError,
)
from ladon.networking.types import Err, Result


class _TransportFailure(Exception):
    pass


@dataclass
class _Response:
    url: str
    status_code: int = 200
    content: bytes = b"ok"
    reason: str = "OK"
    headers: dict[str, str] = field(default_factory=lambda: {})
    elapsed: timedelta = field(default_factory=timedelta)


_Attempt = Callable[[str], Awaitable[_Response]]


class _PolicyClient(AsyncPolicyBase):
    """Minimal transport used to exercise the shared policy pipeline."""

    def __init__(self, config: HttpClientConfig, attempt: _Attempt) -> None:
        super().__init__(config)
        self._attempt = attempt

    async def aclose(self) -> None:
        self._clear_concurrency_state()

    def _is_transport_exception(self, exc: Exception) -> bool:
        return isinstance(exc, _TransportFailure)

    def _is_retryable_exception(self, method: str, exc: Exception) -> bool:
        return False

    async def _execute_attempt(
        self,
        request_fn: Any,
        proxy: Mapping[str, str] | None,
    ) -> Any:
        return await request_fn()

    def _handle_request_exception(
        self,
        method: str,
        request_url: str,
        e: Exception,
        context: Mapping[str, Any] | None,
        attempts: int,
        timeout: Any,
    ) -> Result[Any, Exception]:
        return Err(TransientNetworkError(str(e)), meta={"attempts": attempts})

    async def get(self, url: str) -> Result[_Response, Exception]:
        async def request_fn() -> _Response:
            return await self._attempt(url)

        return await self._request(
            method="GET",
            url=url,
            context=None,
            timeout=1.0,
            request_fn=request_fn,
            value_builder=lambda response: response,
        )


async def test_repeated_5xx_responses_open_circuit_at_threshold() -> None:
    statuses = iter([500, 500])

    async def attempt(url: str) -> _Response:
        return _Response(url, status_code=next(statuses))

    client = _PolicyClient(
        HttpClientConfig(circuit_breaker_failure_threshold=2), attempt
    )
    url = "https://breaker-status.example/item"

    first = await client.get(url)
    assert first.ok
    assert client.circuit_state(url) is CircuitState.CLOSED

    second = await client.get(url)
    assert second.ok
    assert client.circuit_state(url) is CircuitState.OPEN


async def test_2xx_response_resets_accumulated_5xx_failures() -> None:
    statuses = iter([500, 200, 500, 500])

    async def attempt(url: str) -> _Response:
        return _Response(url, status_code=next(statuses))

    client = _PolicyClient(
        HttpClientConfig(circuit_breaker_failure_threshold=2), attempt
    )
    url = "https://breaker-status.example/item"

    assert (await client.get(url)).ok
    assert (await client.get(url)).ok
    assert (await client.get(url)).ok
    assert client.circuit_state(url) is CircuitState.CLOSED

    assert (await client.get(url)).ok
    assert client.circuit_state(url) is CircuitState.OPEN


async def test_repeated_4xx_responses_do_not_open_circuit() -> None:
    async def attempt(url: str) -> _Response:
        return _Response(url, status_code=404)

    client = _PolicyClient(
        HttpClientConfig(circuit_breaker_failure_threshold=2), attempt
    )
    url = "https://breaker-status.example/item"

    results = [await client.get(url) for _ in range(5)]

    assert all(result.ok for result in results)
    assert client.circuit_state(url) is CircuitState.CLOSED


async def test_retryable_500_counts_once_after_retries_exhausted() -> None:
    attempts = 0

    async def attempt(url: str) -> _Response:
        nonlocal attempts
        attempts += 1
        return _Response(url, status_code=500)

    client = _PolicyClient(
        HttpClientConfig(
            retries=2,
            retry_on_status=frozenset({500}),
            circuit_breaker_failure_threshold=2,
        ),
        attempt,
    )
    url = "https://breaker-status.example/item"

    first = await client.get(url)
    assert not first.ok
    assert isinstance(first.error, RateLimitedError)
    assert first.meta["attempts"] == 3
    assert attempts == 3
    assert client.circuit_state(url) is CircuitState.CLOSED

    second = await client.get(url)
    assert not second.ok
    assert isinstance(second.error, RateLimitedError)
    assert attempts == 6
    assert client.circuit_state(url) is CircuitState.OPEN


async def test_retry_sequence_settling_on_5xx_counts_once() -> None:
    statuses = iter([503, 503, 500, 500])
    attempts = 0

    async def attempt(url: str) -> _Response:
        nonlocal attempts
        attempts += 1
        return _Response(url, status_code=next(statuses))

    client = _PolicyClient(
        HttpClientConfig(
            retries=2,
            retry_on_status=frozenset({503}),
            circuit_breaker_failure_threshold=2,
        ),
        attempt,
    )
    url = "https://breaker-status.example/item"

    first = await client.get(url)
    assert first.ok
    assert first.value is not None
    assert first.value.status_code == 500
    assert first.meta["attempts"] == 3
    assert attempts == 3
    assert client.circuit_state(url) is CircuitState.CLOSED

    assert (await client.get(url)).ok
    assert attempts == 4
    assert client.circuit_state(url) is CircuitState.OPEN


async def test_same_host_concurrent_requests_reserve_spaced_slots() -> None:
    interval = 0.02
    starts: list[float] = []

    async def attempt(url: str) -> _Response:
        starts.append(monotonic())
        return _Response(url)

    client = _PolicyClient(
        HttpClientConfig(min_request_interval_seconds=interval), attempt
    )

    results = await asyncio.gather(
        *(
            client.get(f"https://same.example/item/{index}")
            for index in range(10)
        )
    )

    assert all(result.ok for result in results)
    assert len(starts) == 10
    assert all(
        later - earlier >= interval * 0.8
        for earlier, later in zip(starts, starts[1:], strict=False)
    )


async def test_retry_merges_crawl_delay_into_one_locked_sleep() -> None:
    responses = iter(
        [
            _Response(
                "https://retry.example/item",
                status_code=429,
                headers={"Retry-After": "5"},
            ),
            _Response("https://retry.example/item"),
        ]
    )

    async def attempt(url: str) -> _Response:
        return next(responses)

    client = _PolicyClient(
        HttpClientConfig(
            retries=1,
            min_request_interval_seconds=1.0,
        ),
        attempt,
    )
    client.set_crawl_delay("retry.example", 10.0)

    with (
        patch(
            "ladon.networking._async_policy_base.monotonic",
            side_effect=[90.0, 100.0, 100.0, 110.0, 110.0],
        ),
        patch(
            "ladon.networking._async_policy_base.asyncio.sleep",
            new_callable=AsyncMock,
        ) as mock_sleep,
    ):
        result = await client.get("https://retry.example/item")

    assert result.ok
    mock_sleep.assert_awaited_once_with(10.0)


async def test_different_hosts_do_not_share_a_rate_limit_lock() -> None:
    both_started = asyncio.Event()
    release = asyncio.Event()
    started = 0

    async def attempt(url: str) -> _Response:
        nonlocal started
        started += 1
        if started == 2:
            both_started.set()
        await release.wait()
        return _Response(url)

    client = _PolicyClient(
        HttpClientConfig(min_request_interval_seconds=60.0), attempt
    )
    requests = [
        asyncio.create_task(client.get("https://alpha.example/item")),
        asyncio.create_task(client.get("https://beta.example/item")),
    ]

    await asyncio.wait_for(both_started.wait(), timeout=0.5)
    release.set()
    results = await asyncio.gather(*requests)

    assert all(result.ok for result in results)


async def test_cancelled_rate_limit_wait_releases_the_host_lock() -> None:
    async def attempt(url: str) -> _Response:
        return _Response(url)

    client = _PolicyClient(
        HttpClientConfig(min_request_interval_seconds=60.0), attempt
    )
    url = "https://cancel-rate.example/item"
    await client.get(url)

    waiting = asyncio.create_task(client.get(url))
    await asyncio.sleep(0)
    host_lock = client._rate_limit_locks["cancel-rate.example"]
    assert host_lock.locked()

    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting

    assert not host_lock.locked()
    client._last_request_time["cancel-rate.example"] = monotonic() - 60.0
    result = await asyncio.wait_for(client.get(url), timeout=0.5)
    assert result.ok


async def test_half_open_allows_exactly_one_concurrent_probe() -> None:
    probe_started = asyncio.Event()
    release_probe = asyncio.Event()
    attempts = 0

    async def attempt(url: str) -> _Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise _TransportFailure("open circuit")
        probe_started.set()
        await release_probe.wait()
        return _Response(url)

    client = _PolicyClient(
        HttpClientConfig(
            circuit_breaker_failure_threshold=1,
            circuit_breaker_recovery_seconds=60.0,
        ),
        attempt,
    )
    url = "https://half-open.example/item"
    failed = await client.get(url)
    assert not failed.ok

    breaker = client._get_circuit_breaker("half-open.example")
    assert breaker is not None
    breaker._opened_at = monotonic() - 61.0

    probe = asyncio.create_task(client.get(url))
    await asyncio.wait_for(probe_started.wait(), timeout=0.5)
    blocked = await asyncio.gather(*(client.get(url) for _ in range(4)))

    assert attempts == 2
    assert all(
        not result.ok and isinstance(result.error, CircuitOpenError)
        for result in blocked
    )

    release_probe.set()
    probe_result = await probe
    assert probe_result.ok
    assert client.circuit_state(url) is CircuitState.CLOSED


async def test_concurrent_failures_open_at_the_configured_threshold() -> None:
    all_started = asyncio.Event()
    release = asyncio.Event()
    attempts = 0

    async def attempt(url: str) -> _Response:
        nonlocal attempts
        attempts += 1
        if attempts == 5:
            all_started.set()
        await release.wait()
        raise _TransportFailure("failed")

    client = _PolicyClient(
        HttpClientConfig(
            circuit_breaker_failure_threshold=3,
            circuit_breaker_recovery_seconds=60.0,
        ),
        attempt,
    )
    url = "https://threshold.example/item"
    requests = [asyncio.create_task(client.get(url)) for _ in range(5)]
    await asyncio.wait_for(all_started.wait(), timeout=0.5)
    release.set()
    results = await asyncio.gather(*requests)

    assert all(not result.ok for result in results)
    assert client.circuit_state(url) is CircuitState.OPEN
    blocked = await client.get(url)
    assert not blocked.ok
    assert isinstance(blocked.error, CircuitOpenError)
    assert attempts == 5


async def test_cancelled_half_open_probe_releases_its_reservation() -> None:
    probe_started = asyncio.Event()
    hold_probe = asyncio.Event()
    attempts = 0

    async def attempt(url: str) -> _Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise _TransportFailure("open circuit")
        if attempts == 2:
            probe_started.set()
            await hold_probe.wait()
        return _Response(url)

    client = _PolicyClient(
        HttpClientConfig(
            circuit_breaker_failure_threshold=1,
            circuit_breaker_recovery_seconds=60.0,
        ),
        attempt,
    )
    url = "https://cancel-probe.example/item"
    await client.get(url)
    breaker = client._get_circuit_breaker("cancel-probe.example")
    assert breaker is not None
    breaker._opened_at = monotonic() - 61.0

    cancelled_probe = asyncio.create_task(client.get(url))
    await asyncio.wait_for(probe_started.wait(), timeout=0.5)
    cancelled_probe.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_probe

    replacement = await asyncio.wait_for(client.get(url), timeout=0.5)
    assert replacement.ok
    assert attempts == 3
    assert client.circuit_state(url) is CircuitState.CLOSED


async def test_stale_closed_result_cannot_complete_a_new_probe() -> None:
    slow_started = asyncio.Event()
    release_slow = asyncio.Event()
    probe_started = asyncio.Event()
    release_probe = asyncio.Event()

    async def attempt(url: str) -> _Response:
        if url.endswith("/slow"):
            slow_started.set()
            await release_slow.wait()
            return _Response(url)
        if url.endswith("/fail"):
            raise _TransportFailure("open circuit")
        probe_started.set()
        await release_probe.wait()
        return _Response(url)

    client = _PolicyClient(
        HttpClientConfig(
            circuit_breaker_failure_threshold=1,
            circuit_breaker_recovery_seconds=60.0,
        ),
        attempt,
    )
    host = "generation.example"
    slow = asyncio.create_task(client.get(f"https://{host}/slow"))
    await asyncio.wait_for(slow_started.wait(), timeout=0.5)
    await client.get(f"https://{host}/fail")

    breaker = client._get_circuit_breaker(host)
    assert breaker is not None
    breaker._opened_at = monotonic() - 61.0
    probe = asyncio.create_task(client.get(f"https://{host}/probe"))
    await asyncio.wait_for(probe_started.wait(), timeout=0.5)

    release_slow.set()
    assert (await slow).ok
    assert client.circuit_state(f"https://{host}") is CircuitState.HALF_OPEN

    blocked = await client.get(f"https://{host}/blocked")
    assert not blocked.ok
    assert isinstance(blocked.error, CircuitOpenError)

    release_probe.set()
    assert (await probe).ok
    assert client.circuit_state(f"https://{host}") is CircuitState.CLOSED
