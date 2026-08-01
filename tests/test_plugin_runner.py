# pyright: reportUnknownMemberType=false
"""Contract tests for the source-driven sync runner entry point."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import cast

import pytest

from ladon.networking.client import HttpClient
from ladon.plugins.errors import ExpansionNotReadyError, LeafUnavailableError
from ladon.plugins.models import Expansion, Ref
from ladon.plugins.protocol import CrawlPlugin, Expander, Sink, Source
from ladon.runner import PluginRunResult, RunConfig, run_plugin


@dataclass(frozen=True)
class _Record:
    url: str


class _Source:
    def __init__(self, refs: Sequence[Ref]) -> None:
        self._refs = refs
        self.calls = 0

    def discover(self, client: HttpClient) -> Sequence[Ref]:
        self.calls += 1
        return self._refs


class _Expander:
    def __init__(
        self,
        error: Exception | None = None,
        error_for: Callable[[Ref], Exception | None] | None = None,
    ) -> None:
        self._error = error
        self._error_for = error_for

    def expand(self, ref: object, client: HttpClient) -> Expansion:
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


class _Sink:
    def __init__(self, fail: Callable[[Ref], bool] | None = None) -> None:
        self._fail: Callable[[Ref], bool] = fail or _never_fail

    def consume(self, ref: object, client: HttpClient) -> _Record:
        assert isinstance(ref, Ref)
        if self._fail(ref):
            raise LeafUnavailableError("unavailable")
        return _Record(ref.url)


class _Plugin:
    name = "whole-plugin"

    def __init__(
        self,
        refs: Sequence[Ref],
        *,
        expander: _Expander | None = None,
        sink: _Sink | None = None,
    ) -> None:
        self.source: Source = _Source(refs)
        self.expanders: Sequence[Expander] = (expander or _Expander(),)
        self.sink: Sink = sink or _Sink()


def _never_fail(_ref: Ref) -> bool:
    return False


def _refs() -> tuple[Ref, Ref]:
    return (
        Ref("https://example.test/top/1"),
        Ref("https://example.test/top/2"),
    )


def test_sync_whole_plugin_api_is_exported_from_ladon() -> None:
    from ladon import PluginRunResult as exported_result
    from ladon import run_plugin as exported_run_plugin

    assert exported_result is PluginRunResult
    assert exported_run_plugin is run_plugin


def test_runs_every_discovered_root_and_aggregates_results() -> None:
    plugin = _Plugin(_refs())

    result = run_plugin(
        cast(CrawlPlugin, plugin), cast(HttpClient, None), RunConfig()
    )

    assert isinstance(result, PluginRunResult)
    assert isinstance(plugin.source, _Source)
    assert plugin.source.calls == 1
    assert result.top_refs == _refs()
    assert len(result.results) == 2
    assert result.leaves_consumed == 4
    assert result.leaves_persisted == 4
    assert result.leaves_failed == 0
    assert result.errors == ()


def test_empty_discovery_returns_zero_count_aggregate() -> None:
    plugin = _Plugin(())

    result = run_plugin(
        cast(CrawlPlugin, plugin), cast(HttpClient, None), RunConfig()
    )

    assert isinstance(plugin.source, _Source)
    assert plugin.source.calls == 1
    assert result.top_refs == ()
    assert result.results == ()
    assert result.leaves_consumed == 0
    assert result.leaves_persisted == 0
    assert result.leaves_failed == 0
    assert result.errors == ()


def test_forwards_on_leaf_to_each_single_root_run() -> None:
    plugin = _Plugin(_refs())
    received: list[tuple[_Record, _Record]] = []

    def on_leaf(leaf: object, parent: object) -> None:
        assert isinstance(leaf, _Record)
        assert isinstance(parent, _Record)
        received.append((leaf, parent))

    result = run_plugin(
        cast(CrawlPlugin, plugin),
        cast(HttpClient, None),
        RunConfig(),
        on_leaf=on_leaf,
    )

    assert result.leaves_persisted == 4
    assert len(received) == 4


def test_applies_leaf_limit_to_each_discovered_root() -> None:
    result = run_plugin(
        cast(CrawlPlugin, _Plugin(_refs())),
        cast(HttpClient, None),
        RunConfig(leaf_limit=1),
    )

    assert result.leaves_consumed == 2
    assert tuple(run.leaves_consumed for run in result.results) == (1, 1)


def test_aggregates_leaf_errors_with_root_index() -> None:
    plugin = _Plugin(
        _refs(), sink=_Sink(lambda ref: ref.url.endswith("top/2/leaf/2"))
    )

    result = run_plugin(
        cast(CrawlPlugin, plugin), cast(HttpClient, None), RunConfig()
    )

    assert result.leaves_consumed == 3
    assert result.leaves_failed == 1
    assert result.errors == ("top_ref[1]: ref[1] consume failed: unavailable",)
    assert result.results[0].errors == ()
    assert result.results[1].errors == ("ref[1] consume failed: unavailable",)


def test_discovery_errors_propagate() -> None:
    class _FailingSource(_Source):
        def discover(self, client: HttpClient) -> Sequence[Ref]:
            raise RuntimeError("discovery failed")

    plugin = _Plugin(_refs())
    plugin.source = _FailingSource(())

    with pytest.raises(RuntimeError, match="discovery failed"):
        run_plugin(
            cast(CrawlPlugin, plugin), cast(HttpClient, None), RunConfig()
        )


def test_globally_fatal_root_errors_propagate() -> None:
    plugin = _Plugin(
        _refs(), expander=_Expander(ExpansionNotReadyError("later"))
    )

    with pytest.raises(ExpansionNotReadyError, match="later"):
        run_plugin(
            cast(CrawlPlugin, plugin), cast(HttpClient, None), RunConfig()
        )


def test_later_fatal_root_preserves_earlier_callback_side_effects() -> None:
    persisted: list[str] = []
    plugin = _Plugin(
        _refs(),
        expander=_Expander(
            error_for=lambda ref: (
                ExpansionNotReadyError("later")
                if ref.url.endswith("top/2")
                else None
            )
        ),
    )

    def on_leaf(leaf: object, _parent: object) -> None:
        assert isinstance(leaf, _Record)
        persisted.append(leaf.url)

    with pytest.raises(ExpansionNotReadyError, match="later"):
        run_plugin(
            cast(CrawlPlugin, plugin),
            cast(HttpClient, None),
            RunConfig(),
            on_leaf=on_leaf,
        )

    assert persisted == [
        "https://example.test/top/1/leaf/1",
        "https://example.test/top/1/leaf/2",
    ]
