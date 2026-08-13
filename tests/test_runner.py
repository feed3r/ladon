# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportArgumentType=false
# pyright: reportUnknownParameterType=false, reportMissingParameterType=false
"""Contract tests for CrawlPlan, plan_crawl_sync, and execute_plan_sync.

The ``client`` parameter is ``None`` throughout — mock expanders and sinks
never use it; the runner passes it through without inspecting it.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

import pytest

from ladon.networking.protocols import SyncHttpClientProtocol
from ladon.plugins.errors import (
    AssetDownloadError,
    ChildListUnavailableError,
    ExpansionNotReadyError,
    LeafUnavailableError,
    PartialExpansionError,
)
from ladon.plugins.models import Expansion, Ref
from ladon.plugins.protocol import CrawlPlugin, Expander, Sink, Source
from ladon.runner import (
    CrawlPlan,
    RunConfig,
    RunResult,
    execute_plan_sync,
    plan_crawl_sync,
    run_crawl,
    run_plugin,
)

# ---------------------------------------------------------------------------
# Domain-neutral test types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _DemoRecord:
    name: str = "demo"


@dataclass(frozen=True)
class _DemoLeafRecord:
    leaf_id: str
    url: str


# ---------------------------------------------------------------------------
# Mock plugin helpers
# ---------------------------------------------------------------------------


class _MockExpander:
    def __init__(self, child_refs: list[Ref]) -> None:
        self._child_refs = child_refs

    def expand(self, ref: object, client: object) -> Expansion:
        return Expansion(record=_DemoRecord(), child_refs=self._child_refs)


class _MockSink:
    def consume(self, ref: object, client: object) -> _DemoLeafRecord:
        r = ref if isinstance(ref, Ref) else Ref(url=str(ref))
        return _DemoLeafRecord(leaf_id=r.url.split("/")[-1], url=r.url)


class _MockSource:
    def discover(self, client: SyncHttpClientProtocol) -> Sequence[object]:
        return ()


class _MockPlugin:
    def __init__(self, child_refs: list[Ref]) -> None:
        self.source: Source = _MockSource()
        self.expanders: Sequence[Expander] = (_MockExpander(child_refs),)
        self.sink: Sink = _MockSink()

    @property
    def name(self) -> str:
        return "mock_plugin"


_typed_plugin: CrawlPlugin = _MockPlugin([])


class _PreciseSource:
    def discover(
        self, client: SyncHttpClientProtocol
    ) -> Sequence[Ref[dict[str, str]]]:
        return (Ref("https://typed.example/top", {"kind": "top"}),)


class _PreciseExpander:
    def expand(
        self,
        ref: Ref[dict[str, str]],
        client: SyncHttpClientProtocol,
    ) -> Expansion[_DemoRecord, dict[str, int]]:
        return Expansion(
            _DemoRecord(),
            (Ref(f"{ref.url}/leaf", {"leaf_id": 7}),),
        )


class _PreciseSink:
    def consume(
        self,
        ref: Ref[dict[str, int]],
        client: SyncHttpClientProtocol,
    ) -> _DemoLeafRecord:
        return _DemoLeafRecord(str(ref.raw["leaf_id"]), ref.url)


@dataclass(frozen=True)
class _PrecisePlugin:
    name: str = "precise_plugin"
    source: _PreciseSource = _PreciseSource()
    expanders: Sequence[_PreciseExpander] = (_PreciseExpander(),)
    sink: _PreciseSink = _PreciseSink()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def child_refs() -> list[Ref]:
    return [
        Ref(url="https://demo.example.com/leaf/1"),
        Ref(url="https://demo.example.com/leaf/2"),
        Ref(url="https://demo.example.com/leaf/3"),
    ]


@pytest.fixture()
def plugin(child_refs: list[Ref]) -> _MockPlugin:
    return _MockPlugin(child_refs)


@pytest.fixture()
def top_ref() -> Ref:
    return Ref(url="https://demo.example.com/top/1")


@pytest.fixture()
def two_level_plugin() -> _MockPlugin:
    section = Ref(url="https://demo.example.com/section/1")
    leaf = Ref(url="https://demo.example.com/leaf/1")

    class _RootExpander:
        def expand(self, ref: object, client: object) -> Expansion:
            return Expansion(_DemoRecord("root"), (section,))

    class _SectionExpander:
        def expand(self, ref: object, client: object) -> Expansion:
            return Expansion(_DemoRecord("direct-parent"), (leaf,))

    plugin = _MockPlugin([])
    plugin.expanders = (_RootExpander(), _SectionExpander())
    return plugin


class _UnexpectedFailureSink:
    def consume(self, ref: object, client: object) -> _DemoLeafRecord:
        r = ref if isinstance(ref, Ref) else Ref(url=str(ref))
        if r.url.endswith("/2"):
            raise RuntimeError("parser bug")
        return _DemoLeafRecord(leaf_id=r.url.split("/")[-1], url=r.url)


class _KeyboardInterruptSink:
    def consume(self, ref: object, client: object) -> object:
        raise KeyboardInterrupt("stop now")


class _AssetDownloadFailureSink:
    def consume(self, ref: object, client: object) -> object:
        raise AssetDownloadError("asset unavailable")


class _TypedExpansionFailureSink:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def consume(self, ref: object, client: object) -> object:
        raise self._error


# ---------------------------------------------------------------------------
# run_crawl — Phase 3 exception semantics
# ---------------------------------------------------------------------------


class TestRunCrawlExceptionSemantics:
    def test_run_result_documents_both_phase_3_error_formats(self) -> None:
        assert RunResult.__doc__ is not None
        assert '"ref[N] consume failed: ..."' in RunResult.__doc__
        assert '"ref[N] callback failed: ..."' in RunResult.__doc__

    def test_shared_result_and_config_docs_name_both_callback_contracts(
        self,
    ) -> None:
        assert RunConfig.__doc__ is not None
        assert RunResult.__doc__ is not None
        for callback_name in ("``on_leaf``", "``on_planned_leaf``"):
            assert callback_name in RunConfig.__doc__
            assert callback_name in RunResult.__doc__

    def test_run_crawl_documents_fatal_exceptions(self) -> None:
        assert run_crawl.__doc__ is not None
        assert "Raises:" in run_crawl.__doc__
        assert "BaseException" in run_crawl.__doc__

    def test_run_plugin_documents_fatal_exceptions(self) -> None:
        assert run_plugin.__doc__ is not None
        assert "Raises:" in run_plugin.__doc__
        assert "AssetDownloadError" in run_plugin.__doc__
        assert "ExpansionNotReadyError" in run_plugin.__doc__
        assert "PartialExpansionError" in run_plugin.__doc__
        assert "ChildListUnavailableError" in run_plugin.__doc__
        assert "BaseException" in run_plugin.__doc__

    def test_unexpected_consume_exception_is_recorded_and_run_continues(
        self,
        top_ref: Ref,
        child_refs: list[Ref],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        plugin = _MockPlugin(child_refs)
        plugin.sink = _UnexpectedFailureSink()

        with caplog.at_level(logging.ERROR, logger="ladon.runner"):
            result = run_crawl(top_ref, plugin, None, RunConfig())  # type: ignore[arg-type]

        assert result.leaves_consumed == 2
        assert result.leaves_persisted == 2
        assert result.leaves_failed == 1
        assert result.errors == ("ref[1] consume failed: parser bug",)
        assert caplog.records[0].getMessage() == (
            "leaf consume failed — ref[1] error=parser bug"
        )
        assert caplog.records[0].error_type == "RuntimeError"  # type: ignore[attr-defined]

    def test_asset_download_error_from_consume_propagates(
        self,
        top_ref: Ref,
        child_refs: list[Ref],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        plugin = _MockPlugin(child_refs)
        plugin.sink = _AssetDownloadFailureSink()

        with (
            caplog.at_level(logging.ERROR, logger="ladon.runner"),
            pytest.raises(AssetDownloadError, match="asset unavailable"),
        ):
            run_crawl(top_ref, plugin, None, RunConfig())  # type: ignore[arg-type]

        assert caplog.records[0].ref_index == 0  # type: ignore[attr-defined]
        assert caplog.records[0].error_type == "AssetDownloadError"  # type: ignore[attr-defined]

    def test_expansion_not_ready_from_consume_propagates(
        self, top_ref: Ref, child_refs: list[Ref]
    ) -> None:
        plugin = _MockPlugin(child_refs)
        plugin.sink = _TypedExpansionFailureSink(
            ExpansionNotReadyError("not ready yet")
        )

        with pytest.raises(ExpansionNotReadyError, match="not ready yet"):
            run_crawl(top_ref, plugin, None, RunConfig())  # type: ignore[arg-type]

    def test_keyboard_interrupt_from_consume_propagates(
        self,
        top_ref: Ref,
        child_refs: list[Ref],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        plugin = _MockPlugin(child_refs)
        plugin.sink = _KeyboardInterruptSink()

        with (
            caplog.at_level(logging.ERROR, logger="ladon.runner"),
            pytest.raises(KeyboardInterrupt, match="stop now"),
        ):
            run_crawl(top_ref, plugin, None, RunConfig())  # type: ignore[arg-type]

        assert "failed" not in caplog.records[0].getMessage()
        assert caplog.records[0].ref_index == 0  # type: ignore[attr-defined]
        assert caplog.records[0].error_type == "KeyboardInterrupt"  # type: ignore[attr-defined]

    def test_keyboard_interrupt_from_callback_is_logged_and_propagates(
        self,
        top_ref: Ref,
        child_refs: list[Ref],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        plugin = _MockPlugin(child_refs)

        def cancelled_callback(record: object, parent: object) -> None:
            raise KeyboardInterrupt("cancel persistence")

        with (
            caplog.at_level(logging.ERROR, logger="ladon.runner"),
            pytest.raises(KeyboardInterrupt, match="cancel persistence"),
        ):
            run_crawl(
                top_ref,
                plugin,
                None,  # type: ignore[arg-type]
                RunConfig(),
                on_leaf=cancelled_callback,
            )

        assert caplog.records[0].ref_index == 0  # type: ignore[attr-defined]
        assert caplog.records[0].error_type == "KeyboardInterrupt"  # type: ignore[attr-defined]
        assert "failed" not in caplog.records[0].getMessage()


# ---------------------------------------------------------------------------
# CrawlPlan — value semantics
# ---------------------------------------------------------------------------


class TestCrawlPlan:
    def test_frozen(self) -> None:
        plan = CrawlPlan(record=_DemoRecord(), leaves=(), errors=())
        with pytest.raises(AttributeError):
            plan.leaves = ()  # type: ignore[misc]

    def test_excluding_removes_matching_leaves(self) -> None:
        refs = [Ref(url=f"https://x.example.com/{i}") for i in range(4)]
        plan = CrawlPlan(record=_DemoRecord(), leaves=tuple(refs), errors=())
        filtered = plan.excluding(lambda r: r.url.endswith("/1") or r.url.endswith("/3"))  # type: ignore[union-attr]
        assert len(filtered.leaves) == 2
        remaining_urls = {r.url for r in filtered.leaves}  # type: ignore[union-attr]
        assert "https://x.example.com/0" in remaining_urls
        assert "https://x.example.com/2" in remaining_urls

    def test_excluding_preserves_errors(self) -> None:
        refs = [Ref(url="https://x.example.com/1")]
        plan = CrawlPlan(
            record=_DemoRecord(),
            leaves=tuple(refs),
            errors=("branch 'x': failed",),
        )
        filtered = plan.excluding(lambda _: True)
        assert filtered.errors == ("branch 'x': failed",)
        assert len(filtered.leaves) == 0

    def test_excluding_nothing_returns_same_count(self) -> None:
        refs = [Ref(url=f"https://x.example.com/{i}") for i in range(3)]
        plan = CrawlPlan(record=_DemoRecord(), leaves=tuple(refs), errors=())
        filtered = plan.excluding(lambda _: False)
        assert len(filtered.leaves) == 3

    def test_limited_to_caps_leaves(self) -> None:
        refs = [Ref(url=f"https://x.example.com/{i}") for i in range(5)]
        plan = CrawlPlan(record=_DemoRecord(), leaves=tuple(refs), errors=())
        capped = plan.limited_to(3)
        assert len(capped.leaves) == 3
        assert capped.leaves[0].url == "https://x.example.com/0"  # type: ignore[union-attr]
        assert capped.leaves[2].url == "https://x.example.com/2"  # type: ignore[union-attr]

    def test_limited_to_larger_than_count_is_noop(self) -> None:
        refs = [Ref(url=f"https://x.example.com/{i}") for i in range(2)]
        plan = CrawlPlan(record=_DemoRecord(), leaves=tuple(refs), errors=())
        capped = plan.limited_to(100)
        assert len(capped.leaves) == 2

    def test_limited_to_preserves_errors(self) -> None:
        refs = [Ref(url=f"https://x.example.com/{i}") for i in range(3)]
        plan = CrawlPlan(
            record=_DemoRecord(),
            leaves=tuple(refs),
            errors=("branch error",),
        )
        capped = plan.limited_to(1)
        assert capped.errors == ("branch error",)

    def test_record_preserved_through_filter(self) -> None:
        original_record = _DemoRecord(name="original")
        refs = [Ref(url="https://x.example.com/1")]
        plan = CrawlPlan(record=original_record, leaves=tuple(refs), errors=())
        filtered = plan.excluding(lambda _: True)
        assert filtered.record is original_record

    def test_limited_to_zero_raises(self) -> None:
        plan = CrawlPlan(record=_DemoRecord(), leaves=(), errors=())
        with pytest.raises(ValueError, match="positive integer"):
            plan.limited_to(0)

    def test_limited_to_negative_raises(self) -> None:
        plan = CrawlPlan(record=_DemoRecord(), leaves=(), errors=())
        with pytest.raises(ValueError, match="positive integer"):
            plan.limited_to(-1)


# ---------------------------------------------------------------------------
# plan_crawl_sync — happy path
# ---------------------------------------------------------------------------


class TestPlanCrawlSync:
    def test_returns_crawl_plan(
        self, top_ref: Ref, plugin: _MockPlugin
    ) -> None:
        plan = plan_crawl_sync(top_ref, plugin, None)  # type: ignore[arg-type]
        assert isinstance(plan, CrawlPlan)

    def test_leaf_count_matches_expander_output(
        self, top_ref: Ref, plugin: _MockPlugin
    ) -> None:
        plan = plan_crawl_sync(top_ref, plugin, None)  # type: ignore[arg-type]
        assert len(plan.leaves) == 3

    def test_record_set_from_first_expander(
        self, top_ref: Ref, plugin: _MockPlugin
    ) -> None:
        plan = plan_crawl_sync(top_ref, plugin, None)  # type: ignore[arg-type]
        assert isinstance(plan.record, _DemoRecord)

    def test_no_errors_on_clean_run(
        self, top_ref: Ref, plugin: _MockPlugin
    ) -> None:
        plan = plan_crawl_sync(top_ref, plugin, None)  # type: ignore[arg-type]
        assert plan.errors == ()

    def test_empty_expander_produces_empty_plan(self, top_ref: Ref) -> None:
        p = _MockPlugin([])
        plan = plan_crawl_sync(top_ref, p, None)  # type: ignore[arg-type]
        assert len(plan.leaves) == 0
        assert plan.errors == ()

    def test_multi_expander_chain(self, top_ref: Ref) -> None:
        parent_refs = [
            Ref(url="https://x.example.com/parent/1"),
            Ref(url="https://x.example.com/parent/2"),
        ]
        child_refs = [
            Ref(url="https://x.example.com/child/a"),
            Ref(url="https://x.example.com/child/b"),
        ]
        first_expander = _MockExpander(parent_refs)
        second_expander = _MockExpander(child_refs)
        p = _MockPlugin([])
        p.expanders = [first_expander, second_expander]
        plan = plan_crawl_sync(top_ref, p, None)  # type: ignore[arg-type]
        # 2 parents × 2 children each = 4 total leaves
        assert len(plan.leaves) == 4

    def test_empty_plugin_raises_value_error(
        self, top_ref: Ref, child_refs: list[Ref]
    ) -> None:
        p = _MockPlugin(child_refs)
        p.expanders = []
        with pytest.raises(ValueError, match="no expanders configured"):
            plan_crawl_sync(top_ref, p, None)  # type: ignore[arg-type]

    def test_expansion_not_ready_propagates(self, top_ref: Ref) -> None:
        class _NotReadyExpander:
            def expand(self, ref: object, client: object) -> Expansion:
                raise ExpansionNotReadyError("not ready")

        p = _MockPlugin([])
        p.expanders = [_NotReadyExpander()]
        with pytest.raises(ExpansionNotReadyError):
            plan_crawl_sync(top_ref, p, None)  # type: ignore[arg-type]

    def test_partial_expansion_propagates_from_first(
        self, top_ref: Ref
    ) -> None:
        class _PartialExpander:
            def expand(self, ref: object, client: object) -> Expansion:
                raise PartialExpansionError("partial")

        p = _MockPlugin([])
        p.expanders = [_PartialExpander()]
        with pytest.raises(PartialExpansionError):
            plan_crawl_sync(top_ref, p, None)  # type: ignore[arg-type]

    def test_child_list_unavailable_propagates_from_first(
        self, top_ref: Ref
    ) -> None:
        class _UnavailableExpander:
            def expand(self, ref: object, client: object) -> Expansion:
                raise ChildListUnavailableError("unavail")

        p = _MockPlugin([])
        p.expanders = [_UnavailableExpander()]
        with pytest.raises(ChildListUnavailableError):
            plan_crawl_sync(top_ref, p, None)  # type: ignore[arg-type]

    def test_branch_error_on_non_first_expander_is_isolated(
        self, top_ref: Ref
    ) -> None:
        good_refs = [
            Ref(url="https://x.example.com/good/1"),
            Ref(url="https://x.example.com/good/2"),
        ]
        first_expander = _MockExpander(good_refs)

        class _BranchErrorExpander:
            def expand(self, ref: object, client: object) -> Expansion:
                if "good/1" in str(ref):
                    raise PartialExpansionError("branch failed")
                return Expansion(
                    record=_DemoRecord(),
                    child_refs=[Ref(url="https://x.example.com/leaf/ok")],
                )

        p = _MockPlugin([])
        p.expanders = [first_expander, _BranchErrorExpander()]
        plan = plan_crawl_sync(top_ref, p, None)  # type: ignore[arg-type]
        assert len(plan.leaves) == 1  # good/2 succeeded; good/1 branch failed
        assert len(plan.errors) == 1
        assert "branch" in plan.errors[0]

    def test_expansion_not_ready_from_non_first_expander_propagates(
        self, top_ref: Ref
    ) -> None:
        parent_refs = [Ref(url="https://x.example.com/parent/1")]
        first_expander = _MockExpander(parent_refs)

        class _NotReadySecondExpander:
            def expand(self, ref: object, client: object) -> Expansion:
                raise ExpansionNotReadyError("not ready yet")

        p = _MockPlugin([])
        p.expanders = [first_expander, _NotReadySecondExpander()]
        with pytest.raises(ExpansionNotReadyError):
            plan_crawl_sync(top_ref, p, None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# execute_plan_sync — happy path
# ---------------------------------------------------------------------------


class TestExecutePlanSync:
    def test_precise_plan_leaf_type_flows_to_callback(self) -> None:
        plugin = _PrecisePlugin()
        plan = plan_crawl_sync(
            Ref("https://typed.example/top", {"kind": "top"}),
            plugin,
            None,  # type: ignore[arg-type]
        )
        leaf_ref: Ref[dict[str, int]] = plan.leaves[0]
        seen: list[tuple[_DemoLeafRecord, Ref[dict[str, int]]]] = []

        def on_planned_leaf(
            record: _DemoLeafRecord, ref: Ref[dict[str, int]]
        ) -> None:
            seen.append((record, ref))

        execute_plan_sync(
            plan,
            plugin,
            None,  # type: ignore[arg-type]
            RunConfig(),
            on_planned_leaf=on_planned_leaf,
        )
        assert leaf_ref.raw["leaf_id"] == 7
        assert seen == [(_DemoLeafRecord("7", leaf_ref.url), leaf_ref)]

    def test_returns_run_result(
        self, top_ref: Ref, plugin: _MockPlugin
    ) -> None:
        plan = plan_crawl_sync(top_ref, plugin, None)  # type: ignore[arg-type]
        result = execute_plan_sync(plan, plugin, None, RunConfig())  # type: ignore[arg-type]
        assert isinstance(result, RunResult)

    def test_all_leaves_consumed(
        self, top_ref: Ref, plugin: _MockPlugin
    ) -> None:
        plan = plan_crawl_sync(top_ref, plugin, None)  # type: ignore[arg-type]
        result = execute_plan_sync(plan, plugin, None, RunConfig())  # type: ignore[arg-type]
        assert result.leaves_consumed == 3
        assert result.leaves_persisted == 3
        assert result.leaves_failed == 0

    def test_run_crawl_uses_direct_parent_from_second_expander(
        self, top_ref: Ref, two_level_plugin: _MockPlugin
    ) -> None:
        parents: list[object] = []

        # ParentT is deliberately object because ADR-015 erases chain interiors.
        def on_leaf(record: _DemoLeafRecord, parent: object) -> None:
            parents.append(parent)

        result = run_crawl(
            top_ref,
            two_level_plugin,
            None,  # type: ignore[arg-type]
            RunConfig(),
            on_leaf=on_leaf,
        )
        assert result.leaves_consumed == 1
        assert parents == [_DemoRecord("direct-parent")]

    def test_record_carried_from_plan(
        self, top_ref: Ref, plugin: _MockPlugin
    ) -> None:
        plan = plan_crawl_sync(top_ref, plugin, None)  # type: ignore[arg-type]
        result = execute_plan_sync(plan, plugin, None, RunConfig())  # type: ignore[arg-type]
        assert result.record is plan.record

    def test_on_leaf_receives_leaf_record_and_leaf_ref(
        self, top_ref: Ref, plugin: _MockPlugin, child_refs: list[Ref]
    ) -> None:
        plan = plan_crawl_sync(top_ref, plugin, None)  # type: ignore[arg-type]
        calls: list[tuple[object, object]] = []

        def on_leaf(record: object, ref: object) -> None:
            calls.append((record, ref))

        execute_plan_sync(
            plan,
            plugin,
            None,  # type: ignore[arg-type]
            RunConfig(),
            on_planned_leaf=on_leaf,
        )
        assert len(calls) == 3
        # Second arg must be the leaf ref (a Ref), not a parent record
        for _, ref in calls:
            assert isinstance(ref, Ref)
        received_urls = {ref.url for _, ref in calls}  # type: ignore[union-attr]
        expected_urls = {r.url for r in child_refs}
        assert received_urls == expected_urls

    def test_on_leaf_not_called_when_consume_fails(self, top_ref: Ref) -> None:
        class _FailingSink:
            def consume(self, ref: object, client: object) -> _DemoLeafRecord:
                raise LeafUnavailableError("gone")

        p = _MockPlugin([Ref(url="https://x.example.com/leaf/1")])
        p.sink = _FailingSink()
        plan = plan_crawl_sync(top_ref, p, None)  # type: ignore[arg-type]
        calls: list[object] = []
        execute_plan_sync(
            plan,
            p,
            None,  # type: ignore[arg-type]
            RunConfig(),
            on_planned_leaf=lambda r, ref: calls.append(r),
        )
        assert len(calls) == 0

    def test_leaf_unavailable_counted_as_failed(self, top_ref: Ref) -> None:
        refs = [
            Ref(url="https://x.example.com/leaf/1"),
            Ref(url="https://x.example.com/leaf/2"),
            Ref(url="https://x.example.com/leaf/3"),
        ]

        class _PartialSink:
            def consume(self, ref: object, client: object) -> _DemoLeafRecord:
                r = ref if isinstance(ref, Ref) else Ref(url=str(ref))
                if r.url.endswith("/2"):
                    raise LeafUnavailableError("missing")
                return _DemoLeafRecord(leaf_id=r.url.split("/")[-1], url=r.url)

        p = _MockPlugin(refs)
        p.sink = _PartialSink()
        plan = plan_crawl_sync(top_ref, p, None)  # type: ignore[arg-type]
        result = execute_plan_sync(plan, p, None, RunConfig())  # type: ignore[arg-type]
        assert result.leaves_consumed == 2
        assert result.leaves_failed == 1
        assert result.leaves_consumed + result.leaves_failed == 3
        assert len(result.errors) == 1
        assert "consume failed" in result.errors[0]

    def test_unexpected_consume_exception_is_recorded_and_run_continues(
        self,
        top_ref: Ref,
        child_refs: list[Ref],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        plugin = _MockPlugin(child_refs)
        plugin.sink = _UnexpectedFailureSink()
        plan = plan_crawl_sync(top_ref, plugin, None)  # type: ignore[arg-type]
        progress_calls: list[tuple[int, int]] = []

        with caplog.at_level(logging.ERROR, logger="ladon.runner"):
            result = execute_plan_sync(
                plan,
                plugin,
                None,  # type: ignore[arg-type]
                RunConfig(),
                on_progress=lambda done, total: progress_calls.append(
                    (done, total)
                ),
            )

        assert result.leaves_consumed == 2
        assert result.leaves_persisted == 2
        assert result.leaves_failed == 1
        assert result.errors == ("ref[1] consume failed: parser bug",)
        assert progress_calls == [(1, 3), (2, 3), (3, 3)]
        assert caplog.records[0].getMessage() == (
            "leaf consume failed — ref[1] error=parser bug"
        )
        assert caplog.records[0].error_type == "RuntimeError"  # type: ignore[attr-defined]

    def test_asset_download_error_from_consume_propagates(
        self, child_refs: list[Ref], caplog: pytest.LogCaptureFixture
    ) -> None:
        plugin = _MockPlugin(child_refs)
        plugin.sink = _AssetDownloadFailureSink()
        plan = CrawlPlan(
            record=_DemoRecord(), leaves=tuple(child_refs), errors=()
        )
        progress_calls: list[tuple[int, int]] = []

        with (
            caplog.at_level(logging.ERROR, logger="ladon.runner"),
            pytest.raises(AssetDownloadError, match="asset unavailable"),
        ):
            execute_plan_sync(
                plan,
                plugin,
                None,  # type: ignore[arg-type]
                RunConfig(leaf_limit=1),
                on_progress=lambda done, total: progress_calls.append(
                    (done, total)
                ),
            )

        assert progress_calls == [(1, 1)]
        assert caplog.records[0].ref_index == 0  # type: ignore[attr-defined]
        assert caplog.records[0].error_type == "AssetDownloadError"  # type: ignore[attr-defined]

    def test_partial_expansion_error_from_consume_propagates(
        self, child_refs: list[Ref]
    ) -> None:
        plugin = _MockPlugin(child_refs)
        plugin.sink = _TypedExpansionFailureSink(
            PartialExpansionError("partial expansion")
        )
        plan = CrawlPlan(
            record=_DemoRecord(), leaves=tuple(child_refs), errors=()
        )
        progress_calls: list[tuple[int, int]] = []

        with pytest.raises(PartialExpansionError, match="partial expansion"):
            execute_plan_sync(
                plan,
                plugin,
                None,  # type: ignore[arg-type]
                RunConfig(leaf_limit=1),
                on_progress=lambda done, total: progress_calls.append(
                    (done, total)
                ),
            )

        assert progress_calls == [(1, 1)]

    def test_execute_plan_sync_documents_asset_download_error(self) -> None:
        assert execute_plan_sync.__doc__ is not None
        assert "Raises:" in execute_plan_sync.__doc__
        assert "AssetDownloadError" in execute_plan_sync.__doc__

    def test_execute_plan_sync_documents_fatal_exceptions(self) -> None:
        assert execute_plan_sync.__doc__ is not None
        assert "Raises:" in execute_plan_sync.__doc__
        assert "BaseException" in execute_plan_sync.__doc__

    def test_keyboard_interrupt_from_consume_propagates(
        self, child_refs: list[Ref], caplog: pytest.LogCaptureFixture
    ) -> None:
        plugin = _MockPlugin(child_refs)
        plugin.sink = _KeyboardInterruptSink()
        plan = CrawlPlan(
            record=_DemoRecord(), leaves=tuple(child_refs), errors=()
        )
        progress_calls: list[tuple[int, int]] = []

        with (
            caplog.at_level(logging.ERROR, logger="ladon.runner"),
            pytest.raises(KeyboardInterrupt, match="stop now"),
        ):
            execute_plan_sync(
                plan,
                plugin,
                None,  # type: ignore[arg-type]
                RunConfig(),
                on_progress=lambda done, total: progress_calls.append(
                    (done, total)
                ),
            )

        assert progress_calls == [(1, 3)]
        assert "failed" not in caplog.records[0].getMessage()
        assert caplog.records[0].ref_index == 0  # type: ignore[attr-defined]
        assert caplog.records[0].error_type == "KeyboardInterrupt"  # type: ignore[attr-defined]

    def test_on_leaf_callback_failure_counted(
        self, top_ref: Ref, plugin: _MockPlugin
    ) -> None:
        plan = plan_crawl_sync(top_ref, plugin, None)  # type: ignore[arg-type]

        def bad_callback(record: object, ref: object) -> None:
            raise RuntimeError("db exploded")

        result = execute_plan_sync(
            plan,
            plugin,
            None,  # type: ignore[arg-type]
            RunConfig(),
            on_planned_leaf=bad_callback,
        )
        assert result.leaves_consumed == 3
        assert result.leaves_persisted == 0
        assert result.leaves_failed == 0
        assert len(result.errors) == 3
        for err in result.errors:
            assert "callback failed" in err

    def test_leaf_limit_applied(
        self, top_ref: Ref, plugin: _MockPlugin
    ) -> None:
        plan = plan_crawl_sync(top_ref, plugin, None)  # type: ignore[arg-type]
        result = execute_plan_sync(plan, plugin, None, RunConfig(leaf_limit=2))  # type: ignore[arg-type]
        assert result.leaves_consumed == 2

    def test_zero_leaf_limit_means_no_limit(
        self, top_ref: Ref, plugin: _MockPlugin
    ) -> None:
        plan = plan_crawl_sync(top_ref, plugin, None)  # type: ignore[arg-type]
        result = execute_plan_sync(plan, plugin, None, RunConfig(leaf_limit=0))  # type: ignore[arg-type]
        assert result.leaves_consumed == 3

    def test_plan_errors_carried_into_result(
        self, top_ref: Ref, plugin: _MockPlugin
    ) -> None:
        plan = CrawlPlan(
            record=_DemoRecord(),
            leaves=(Ref(url="https://x.example.com/leaf/1"),),
            errors=("expander branch 'x': failed",),
        )
        result = execute_plan_sync(plan, plugin, None, RunConfig())  # type: ignore[arg-type]
        assert any("expander branch" in e for e in result.errors)

    def test_on_progress_called_after_each_leaf(
        self, top_ref: Ref, plugin: _MockPlugin
    ) -> None:
        plan = plan_crawl_sync(top_ref, plugin, None)  # type: ignore[arg-type]
        progress_calls: list[tuple[int, int]] = []

        def on_progress(done: int, total: int) -> None:
            progress_calls.append((done, total))

        execute_plan_sync(plan, plugin, None, RunConfig(), on_progress=on_progress)  # type: ignore[arg-type]
        assert len(progress_calls) == 3
        assert progress_calls[0] == (1, 3)
        assert progress_calls[1] == (2, 3)
        assert progress_calls[2] == (3, 3)

    def test_on_progress_called_on_failure_too(self, top_ref: Ref) -> None:
        class _FailingSink:
            def consume(self, ref: object, client: object) -> _DemoLeafRecord:
                raise LeafUnavailableError("gone")

        refs = [Ref(url="https://x.example.com/leaf/1")]
        p = _MockPlugin(refs)
        p.sink = _FailingSink()
        plan = plan_crawl_sync(top_ref, p, None)  # type: ignore[arg-type]
        progress_calls: list[tuple[int, int]] = []
        execute_plan_sync(
            plan,
            p,
            None,
            RunConfig(),
            on_progress=lambda d, t: progress_calls.append((d, t)),  # type: ignore[arg-type]
        )
        assert progress_calls == [(1, 1)]

    def test_no_on_leaf_leaves_persisted_equals_consumed(
        self, top_ref: Ref, plugin: _MockPlugin
    ) -> None:
        plan = plan_crawl_sync(top_ref, plugin, None)  # type: ignore[arg-type]
        result = execute_plan_sync(plan, plugin, None, RunConfig())  # type: ignore[arg-type]
        assert result.leaves_persisted == result.leaves_consumed

    def test_empty_plan_returns_zero_result(self, plugin: _MockPlugin) -> None:
        plan = CrawlPlan(
            record=_DemoRecord(),
            leaves=(),
            errors=("branch err",),
        )
        result = execute_plan_sync(plan, plugin, None, RunConfig())  # type: ignore[arg-type]
        assert result.leaves_consumed == 0
        assert result.leaves_persisted == 0
        assert result.leaves_failed == 0
        assert result.errors == ("branch err",)

    def test_empty_plan_on_progress_never_called(
        self, plugin: _MockPlugin
    ) -> None:
        plan = CrawlPlan(record=_DemoRecord(), leaves=(), errors=())
        calls: list[object] = []
        execute_plan_sync(
            plan,
            plugin,
            None,  # type: ignore[arg-type]
            RunConfig(),
            on_progress=lambda d, t: calls.append((d, t)),
        )
        assert calls == []

    def test_on_progress_called_after_on_leaf_failure(
        self, top_ref: Ref
    ) -> None:
        refs = [Ref(url="https://x.example.com/leaf/1")]
        p = _MockPlugin(refs)
        plan = plan_crawl_sync(top_ref, p, None)  # type: ignore[arg-type]
        progress_calls: list[tuple[int, int]] = []

        def failing_callback(record: object, ref: object) -> None:
            raise RuntimeError("db fail")

        execute_plan_sync(
            plan,
            p,
            None,  # type: ignore[arg-type]
            RunConfig(),
            on_planned_leaf=failing_callback,
            on_progress=lambda d, t: progress_calls.append((d, t)),
        )
        assert progress_calls == [(1, 1)]

    def test_on_progress_called_before_fatal_on_leaf_propagates(
        self, top_ref: Ref, caplog: pytest.LogCaptureFixture
    ) -> None:
        refs = [Ref(url="https://x.example.com/leaf/1")]
        plugin = _MockPlugin(refs)
        plan = plan_crawl_sync(top_ref, plugin, None)  # type: ignore[arg-type]
        progress_calls: list[tuple[int, int]] = []

        def cancelled_callback(record: object, ref: object) -> None:
            raise KeyboardInterrupt("cancel persistence")

        with (
            caplog.at_level(logging.ERROR, logger="ladon.runner"),
            pytest.raises(KeyboardInterrupt, match="cancel persistence"),
        ):
            execute_plan_sync(
                plan,
                plugin,
                None,  # type: ignore[arg-type]
                RunConfig(),
                on_planned_leaf=cancelled_callback,
                on_progress=lambda done, total: progress_calls.append(
                    (done, total)
                ),
            )

        assert progress_calls == [(1, 1)]
        assert caplog.records[0].ref_index == 0  # type: ignore[attr-defined]
        assert caplog.records[0].error_type == "KeyboardInterrupt"  # type: ignore[attr-defined]
        assert "failed" not in caplog.records[0].getMessage()
        assert execute_plan_sync.__doc__ is not None
        assert "Sink or ``on_planned_leaf``" in execute_plan_sync.__doc__

    def test_on_progress_exception_is_swallowed(
        self, top_ref: Ref, plugin: _MockPlugin
    ) -> None:
        plan = plan_crawl_sync(top_ref, plugin, None)  # type: ignore[arg-type]

        def exploding_progress(done: int, total: int) -> None:
            raise RuntimeError("progress bar closed")

        result = execute_plan_sync(
            plan, plugin, None, RunConfig(), on_progress=exploding_progress  # type: ignore[arg-type]
        )
        assert result.leaves_consumed == 3

    def test_on_progress_base_exception_preempts_fatal_consume_error(
        self,
    ) -> None:
        asset_error = AssetDownloadError("asset failed")

        class _FailingSink:
            def consume(self, ref: object, client: object) -> object:
                raise asset_error

        interruption = KeyboardInterrupt("progress bar died")

        def interrupting_progress(done: int, total: int) -> None:
            raise interruption

        leaf_ref = Ref(url="https://x.example.com/leaf/1")
        plugin = _MockPlugin([leaf_ref])
        plugin.sink = _FailingSink()
        plan = CrawlPlan(record=_DemoRecord(), leaves=(leaf_ref,), errors=())

        with pytest.raises(
            KeyboardInterrupt, match="progress bar died"
        ) as exc_info:
            execute_plan_sync(
                plan,
                plugin,
                None,  # type: ignore[arg-type]
                RunConfig(),
                on_progress=interrupting_progress,
            )

        assert exc_info.value is interruption
        assert exc_info.value.__context__ is asset_error


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


class TestRunnerLogging:
    def test_plan_crawl_sync_start_and_finish_logged(
        self,
        top_ref: Ref,
        plugin: _MockPlugin,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.INFO, logger="ladon.runner"):
            plan_crawl_sync(top_ref, plugin, None)  # type: ignore[arg-type]
        messages = [r.message for r in caplog.records]
        assert any("plan_crawl_sync started" in m for m in messages)
        assert any("plan_crawl_sync finished" in m for m in messages)

    def test_plan_crawl_sync_start_has_plugin_and_ref(
        self,
        top_ref: Ref,
        plugin: _MockPlugin,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.INFO, logger="ladon.runner"):
            plan_crawl_sync(top_ref, plugin, None)  # type: ignore[arg-type]
        start = next(r for r in caplog.records if "started" in r.message)
        assert start.plugin == "mock_plugin"  # type: ignore[attr-defined]
        assert start.ref == str(top_ref)  # type: ignore[attr-defined]

    def test_execute_plan_sync_start_and_finish_logged(
        self,
        top_ref: Ref,
        plugin: _MockPlugin,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        plan = plan_crawl_sync(top_ref, plugin, None)  # type: ignore[arg-type]
        with caplog.at_level(logging.INFO, logger="ladon.runner"):
            execute_plan_sync(plan, plugin, None, RunConfig())  # type: ignore[arg-type]
        messages = [r.message for r in caplog.records]
        assert any("execute_plan_sync started" in m for m in messages)
        assert any("execute_plan_sync finished" in m for m in messages)

    def test_execute_plan_sync_finish_has_counts(
        self,
        top_ref: Ref,
        plugin: _MockPlugin,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        plan = plan_crawl_sync(top_ref, plugin, None)  # type: ignore[arg-type]
        with caplog.at_level(logging.INFO, logger="ladon.runner"):
            execute_plan_sync(plan, plugin, None, RunConfig())  # type: ignore[arg-type]
        finish = next(r for r in caplog.records if "finished" in r.message)
        assert finish.leaves_consumed == 3  # type: ignore[attr-defined]
        assert finish.leaves_persisted == 3  # type: ignore[attr-defined]
        assert finish.leaves_failed == 0  # type: ignore[attr-defined]
