# pyright: reportUnknownMemberType=false
"""Contract tests for the source-driven async runner entry point."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import cast

import pytest

from ladon.async_runner import async_run_plugin
from ladon.networking.async_client import AsyncHttpClient
from ladon.plugins.async_protocol import (
    AsyncCrawlPlugin,
    AsyncExpander,
    AsyncSink,
    AsyncSource,
)
from ladon.plugins.errors import (
    AssetDownloadError,
    ExpansionNotReadyError,
    LeafUnavailableError,
)
from ladon.plugins.models import Expansion, Ref
from ladon.runner import PluginRunResult, RunConfig


@dataclass(frozen=True)
class _Record:
    url: str


class _AsyncSource:
    def __init__(self, refs: Sequence[Ref]) -> None:
        self._refs = refs
        self.calls = 0

    async def discover(self, client: AsyncHttpClient) -> Sequence[Ref]:
        self.calls += 1
        return self._refs


class _AsyncExpander:
    def __init__(
        self,
        error: Exception | None = None,
        error_for: Callable[[Ref], Exception | None] | None = None,
    ) -> None:
        self._error = error
        self._error_for = error_for

    async def expand(self, ref: object, client: AsyncHttpClient) -> Expansion:
        if self._error is not None:
            raise self._error
        assert isinstance(ref, Ref)
        if self._error_for is not None:
            error = self._error_for(ref)
            if error is not None:
                raise error
        return Expansion(
            record=_Record(ref.url),
            child_refs=(Ref(f"{ref.url}/leaf/1"), Ref(f"{ref.url}/leaf/2")),
        )


class _AsyncSink:
    def __init__(self, fail: Callable[[Ref], bool] | None = None) -> None:
        self._fail: Callable[[Ref], bool] = fail or _never_fail

    async def consume(self, ref: object, client: AsyncHttpClient) -> _Record:
        assert isinstance(ref, Ref)
        if self._fail(ref):
            raise LeafUnavailableError("unavailable")
        return _Record(ref.url)


class _AsyncPlugin:
    name = "whole-async-plugin"

    def __init__(
        self,
        refs: Sequence[Ref],
        *,
        expander: _AsyncExpander | None = None,
        sink: _AsyncSink | None = None,
    ) -> None:
        self.source: AsyncSource = _AsyncSource(refs)
        self.expanders: Sequence[AsyncExpander] = (
            expander or _AsyncExpander(),
        )
        self.sink: AsyncSink = sink or _AsyncSink()


def _never_fail(_ref: Ref) -> bool:
    return False


def _refs() -> tuple[Ref, Ref]:
    return (
        Ref("https://example.test/top/1"),
        Ref("https://example.test/top/2"),
    )


def test_async_whole_plugin_api_is_exported_from_ladon() -> None:
    from ladon import async_run_plugin as exported_async_run_plugin

    assert exported_async_run_plugin is async_run_plugin


async def test_runs_every_discovered_root_and_aggregates_results() -> None:
    plugin = _AsyncPlugin(_refs())

    result = await async_run_plugin(
        cast(AsyncCrawlPlugin, plugin), cast(AsyncHttpClient, None), RunConfig()
    )

    assert isinstance(result, PluginRunResult)
    assert isinstance(plugin.source, _AsyncSource)
    assert plugin.source.calls == 1
    assert result.top_refs == _refs()
    assert len(result.results) == 2
    assert result.leaves_consumed == 4
    assert result.leaves_persisted == 4
    assert result.leaves_failed == 0
    assert result.errors == ()


async def test_empty_discovery_returns_zero_count_aggregate() -> None:
    plugin = _AsyncPlugin(())

    result = await async_run_plugin(
        cast(AsyncCrawlPlugin, plugin), cast(AsyncHttpClient, None), RunConfig()
    )

    assert isinstance(plugin.source, _AsyncSource)
    assert plugin.source.calls == 1
    assert result.top_refs == ()
    assert result.results == ()
    assert result.leaves_consumed == 0
    assert result.leaves_persisted == 0
    assert result.leaves_failed == 0
    assert result.errors == ()


async def test_forwards_on_leaf_to_each_single_root_run() -> None:
    plugin = _AsyncPlugin(_refs())
    received: list[tuple[_Record, _Record]] = []

    async def on_leaf(leaf: object, parent: object) -> None:
        assert isinstance(leaf, _Record)
        assert isinstance(parent, _Record)
        received.append((leaf, parent))

    result = await async_run_plugin(
        cast(AsyncCrawlPlugin, plugin),
        cast(AsyncHttpClient, None),
        RunConfig(),
        on_leaf=on_leaf,
    )

    assert result.leaves_persisted == 4
    assert len(received) == 4


async def test_applies_leaf_limit_to_each_discovered_root() -> None:
    result = await async_run_plugin(
        cast(AsyncCrawlPlugin, _AsyncPlugin(_refs())),
        cast(AsyncHttpClient, None),
        RunConfig(leaf_limit=1),
    )

    assert result.leaves_consumed == 2
    assert tuple(run.leaves_consumed for run in result.results) == (1, 1)


async def test_aggregates_leaf_errors_with_root_index() -> None:
    plugin = _AsyncPlugin(
        _refs(), sink=_AsyncSink(lambda ref: ref.url.endswith("top/2/leaf/2"))
    )

    result = await async_run_plugin(
        cast(AsyncCrawlPlugin, plugin), cast(AsyncHttpClient, None), RunConfig()
    )

    assert result.leaves_consumed == 3
    assert result.leaves_failed == 1
    assert result.errors == ("top_ref[1]: ref[1] consume failed: unavailable",)
    assert result.results[0].errors == ()
    assert result.results[1].errors == ("ref[1] consume failed: unavailable",)


async def test_discovery_errors_propagate() -> None:
    class _FailingSource(_AsyncSource):
        async def discover(self, client: AsyncHttpClient) -> Sequence[Ref]:
            raise RuntimeError("discovery failed")

    plugin = _AsyncPlugin(_refs())
    plugin.source = _FailingSource(())

    with pytest.raises(RuntimeError, match="discovery failed"):
        await async_run_plugin(
            cast(AsyncCrawlPlugin, plugin),
            cast(AsyncHttpClient, None),
            RunConfig(),
        )


async def test_globally_fatal_root_errors_propagate() -> None:
    plugin = _AsyncPlugin(
        _refs(), expander=_AsyncExpander(ExpansionNotReadyError("later"))
    )

    with pytest.raises(ExpansionNotReadyError, match="later"):
        await async_run_plugin(
            cast(AsyncCrawlPlugin, plugin),
            cast(AsyncHttpClient, None),
            RunConfig(),
        )


async def test_asset_download_error_from_root_sink_propagates() -> None:
    class _FatalSink:
        async def consume(
            self, ref: object, client: AsyncHttpClient
        ) -> _Record:
            raise AssetDownloadError("asset host unavailable")

    plugin = _AsyncPlugin(_refs())
    plugin.sink = _FatalSink()

    with pytest.raises(AssetDownloadError, match="asset host unavailable"):
        await async_run_plugin(
            cast(AsyncCrawlPlugin, plugin),
            cast(AsyncHttpClient, None),
            RunConfig(),
        )


def test_async_run_plugin_documents_fatal_root_errors() -> None:
    assert async_run_plugin.__doc__ is not None
    assert "Raises:" in async_run_plugin.__doc__
    assert "AssetDownloadError" in async_run_plugin.__doc__
    assert "ExpansionNotReadyError" in async_run_plugin.__doc__
    assert "PartialExpansionError" in async_run_plugin.__doc__
    assert "ChildListUnavailableError" in async_run_plugin.__doc__


async def test_later_fatal_root_preserves_earlier_callback_side_effects() -> (
    None
):
    persisted: list[str] = []
    plugin = _AsyncPlugin(
        _refs(),
        expander=_AsyncExpander(
            error_for=lambda ref: (
                ExpansionNotReadyError("later")
                if ref.url.endswith("top/2")
                else None
            )
        ),
    )

    async def on_leaf(leaf: object, _parent: object) -> None:
        assert isinstance(leaf, _Record)
        persisted.append(leaf.url)

    with pytest.raises(ExpansionNotReadyError, match="later"):
        await async_run_plugin(
            cast(AsyncCrawlPlugin, plugin),
            cast(AsyncHttpClient, None),
            RunConfig(),
            on_leaf=on_leaf,
        )

    assert persisted == [
        "https://example.test/top/1/leaf/1",
        "https://example.test/top/1/leaf/2",
    ]
