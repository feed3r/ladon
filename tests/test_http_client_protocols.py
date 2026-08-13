# pyright: reportUnknownMemberType=false
"""Static conformance fixtures and runtime smoke tests for HTTP protocols."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from ladon import (
    AsyncCrawlPlugin,
    AsyncCurlHttpClient,
    AsyncExpander,
    AsyncHttpClient,
    AsyncHttpClientProtocol,
    AsyncSink,
    AsyncSource,
    CrawlPlugin,
    CurlHttpClient,
    Expander,
    Expansion,
    HttpClient,
    HttpClientConfig,
    Sink,
    Source,
    SyncHttpClientProtocol,
    async_run_crawl,
    async_run_plugin,
    execute_plan,
    execute_plan_sync,
    make_async_http_client,
    make_http_client,
    plan_crawl,
    plan_crawl_sync,
    run_crawl,
    run_plugin,
)
from ladon.plugins import MultiSourceSink, Ref
from ladon.runner import RunConfig


class _Source:
    def __init__(self, ref: object = "root") -> None:
        self.ref = ref
        self.seen_client: SyncHttpClientProtocol | None = None

    def discover(self, client: SyncHttpClientProtocol) -> Sequence[object]:
        self.seen_client = client
        return (self.ref,)


class _Expander:
    def expand(self, ref: object, client: SyncHttpClientProtocol) -> Expansion:
        return Expansion(record=ref, child_refs=(Ref("leaf"),))


class _Sink:
    def __init__(self) -> None:
        self.seen_client: SyncHttpClientProtocol | None = None

    def consume(self, ref: object, client: SyncHttpClientProtocol) -> object:
        self.seen_client = client
        return ref


class _Plugin:
    name = "protocol-smoke"

    def __init__(self) -> None:
        self.source = _Source()
        self.expanders = (_Expander(),)
        self.sink = _Sink()


class _AsyncSource:
    def __init__(self, ref: object = "root") -> None:
        self.ref = ref
        self.seen_client: AsyncHttpClientProtocol | None = None

    async def discover(
        self, client: AsyncHttpClientProtocol
    ) -> Sequence[object]:
        self.seen_client = client
        return (self.ref,)


class _AsyncExpander:
    async def expand(
        self, ref: object, client: AsyncHttpClientProtocol
    ) -> Expansion:
        return Expansion(record=ref, child_refs=(Ref("leaf"),))


class _AsyncSink:
    def __init__(self) -> None:
        self.seen_client: AsyncHttpClientProtocol | None = None

    async def consume(
        self, ref: object, client: AsyncHttpClientProtocol
    ) -> object:
        self.seen_client = client
        return ref


class _AsyncPlugin:
    name = "async-protocol-smoke"

    def __init__(self) -> None:
        self.source = _AsyncSource()
        self.expanders = (_AsyncExpander(),)
        self.sink = _AsyncSink()


class _ResolutionSink(MultiSourceSink):
    def __init__(self) -> None:
        super().__init__([])

    def _fetch_from_source(
        self,
        source: object,
        ref: Ref,
        client: SyncHttpClientProtocol,
    ) -> bytes | None:
        return None

    def typecheck_fetch(self, client: SyncHttpClientProtocol) -> None:
        self._fetch_from_source(object(), Ref("https://example.test"), client)


def _accept_sync_adapters(
    source: Source,
    expander: Expander,
    sink: Sink,
    client: SyncHttpClientProtocol,
) -> None:
    source.discover(client)
    expansion = expander.expand("root", client)
    sink.consume(expansion.child_refs[0], client)


async def _accept_async_adapters(
    source: AsyncSource,
    expander: AsyncExpander,
    sink: AsyncSink,
    client: AsyncHttpClientProtocol,
) -> None:
    await source.discover(client)
    expansion = await expander.expand("root", client)
    await sink.consume(expansion.child_refs[0], client)


def _strict_pyright_sync_fixture(config: HttpClientConfig) -> None:
    native: SyncHttpClientProtocol = HttpClient(config)
    curl: SyncHttpClientProtocol = CurlHttpClient(
        config, impersonate="chrome136"
    )
    factory_result: SyncHttpClientProtocol = make_http_client(config)
    _accept_sync_adapters(_Source(), _Expander(), _Sink(), factory_result)
    plugin: CrawlPlugin = _Plugin()
    run_crawl("root", plugin, factory_result, RunConfig())
    run_plugin(plugin, factory_result, RunConfig())
    plan = plan_crawl_sync("root", plugin, factory_result)
    execute_plan_sync(plan, plugin, factory_result, RunConfig())
    resolution_sink = _ResolutionSink()
    resolution_sink.resolve_multi(Ref("https://example.test"), factory_result)
    resolution_sink.typecheck_fetch(factory_result)
    native.close()
    curl.close()
    factory_result.close()


async def _strict_pyright_async_fixture(config: HttpClientConfig) -> None:
    native: AsyncHttpClientProtocol = AsyncHttpClient(config)
    curl: AsyncHttpClientProtocol = AsyncCurlHttpClient(
        config, impersonate="chrome136"
    )
    factory_result: AsyncHttpClientProtocol = make_async_http_client(config)
    await _accept_async_adapters(
        _AsyncSource(), _AsyncExpander(), _AsyncSink(), factory_result
    )
    plugin: AsyncCrawlPlugin = _AsyncPlugin()
    await async_run_crawl("root", plugin, factory_result, RunConfig())
    await async_run_plugin(plugin, factory_result, RunConfig())
    plan = await plan_crawl("root", plugin, factory_result)
    await execute_plan(plan, plugin, factory_result, RunConfig())
    await native.aclose()
    await curl.aclose()
    await factory_result.aclose()


def test_public_http_client_imports_remain_available() -> None:
    assert SyncHttpClientProtocol is not None
    assert AsyncHttpClientProtocol is not None
    assert HttpClient is not None
    assert CurlHttpClient is not None
    assert AsyncHttpClient is not None
    assert AsyncCurlHttpClient is not None
    assert make_http_client is not None
    assert make_async_http_client is not None


def test_native_client_satisfies_sync_runtime_protocol() -> None:
    with HttpClient(HttpClientConfig()) as client:
        assert isinstance(client, SyncHttpClientProtocol)


async def test_native_client_satisfies_async_runtime_protocol() -> None:
    async with AsyncHttpClient(HttpClientConfig()) as client:
        assert isinstance(client, AsyncHttpClientProtocol)


def test_curl_client_runs_through_sync_plugin_path() -> None:
    pytest.importorskip("curl_cffi", reason="curl-cffi not installed")
    client = CurlHttpClient(HttpClientConfig(), impersonate="chrome136")
    plugin = _Plugin()
    try:
        assert isinstance(client, SyncHttpClientProtocol)
        result = run_plugin(plugin, client, RunConfig())
    finally:
        client.close()

    assert result.leaves_consumed == 1
    assert plugin.source.seen_client is client
    assert plugin.sink.seen_client is client


async def test_curl_client_runs_through_async_plugin_path() -> None:
    pytest.importorskip("curl_cffi", reason="curl-cffi not installed")
    client = AsyncCurlHttpClient(HttpClientConfig(), impersonate="chrome136")
    plugin = _AsyncPlugin()
    try:
        assert isinstance(client, AsyncHttpClientProtocol)
        result = await async_run_plugin(plugin, client, RunConfig())
    finally:
        await client.aclose()

    assert result.leaves_consumed == 1
    assert plugin.source.seen_client is client
    assert plugin.sink.seen_client is client


# These assignments also prove the complete plugin structures under strict
# Pyright without requiring the optional curl-cffi dependency at runtime.
_sync_plugin: CrawlPlugin = _Plugin()
_async_plugin: AsyncCrawlPlugin = _AsyncPlugin()
_static_fixtures = (
    _strict_pyright_sync_fixture,
    _strict_pyright_async_fixture,
)
