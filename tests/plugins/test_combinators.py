# pyright: reportUnknownMemberType=false
"""Tests for composable fetch-predicate combinators."""

from __future__ import annotations

from dataclasses import replace
from typing import cast

import pytest

from ladon.networking.protocols import SyncHttpClientProtocol
from ladon.plugins import AllOf, AnyOf, FetchPredicate, MultiSourceSink, Not
from ladon.plugins.models import Ref


def _ref(url: str = "https://example.com/item") -> Ref:
    return Ref(url)


class _StaticPredicate:
    """Adapter-shaped predicate returning one configured result."""

    def __init__(self, result: bool) -> None:
        self._result = result

    def accepts(self, data: bytes, ref: Ref) -> bool:
        return self._result


class _PayloadIs:
    def __init__(self, expected: bytes) -> None:
        self._expected = expected

    def accepts(self, data: bytes, ref: Ref) -> bool:
        return data == self._expected


class _RaisesIfCalled:
    def accepts(self, data: bytes, ref: Ref) -> bool:
        raise AssertionError("should not have been called")


class _NonCallableAccepts:
    accepts = True


class _BrokenAcceptsDescriptor:
    @property
    def accepts(self) -> object:
        raise RuntimeError("broken descriptor")


# Static conformance check: a concrete adapter-shaped predicate can be passed
# through each combinator under strict Pyright checking.
_typed_predicate: FetchPredicate = _StaticPredicate(True)
_typed_all: FetchPredicate = AllOf(_typed_predicate)
_typed_any: FetchPredicate = AnyOf(_typed_predicate)
_typed_not: FetchPredicate = Not(_typed_predicate)


@pytest.mark.parametrize(
    ("results", "expected"),
    [
        ((), True),
        ((True,), True),
        ((False,), False),
        ((True, True), True),
        ((True, False), False),
        ((False, True), False),
        ((False, False), False),
    ],
)
def test_all_of_truth_table(results: tuple[bool, ...], expected: bool) -> None:
    predicate = AllOf(*(_StaticPredicate(result) for result in results))
    assert predicate.accepts(b"data", _ref()) is expected


@pytest.mark.parametrize(
    ("results", "expected"),
    [
        ((), False),
        ((True,), True),
        ((False,), False),
        ((True, True), True),
        ((True, False), True),
        ((False, True), True),
        ((False, False), False),
    ],
)
def test_any_of_truth_table(results: tuple[bool, ...], expected: bool) -> None:
    predicate = AnyOf(*(_StaticPredicate(result) for result in results))
    assert predicate.accepts(b"data", _ref()) is expected


def test_all_of_empty_input_accepts() -> None:
    assert AllOf().accepts(b"data", _ref()) is True


def test_any_of_empty_input_rejects() -> None:
    assert AnyOf().accepts(b"data", _ref()) is False


@pytest.mark.parametrize(
    ("component_result", "expected"), [(True, False), (False, True)]
)
def test_not_truth_table(component_result: bool, expected: bool) -> None:
    assert (
        Not(_StaticPredicate(component_result)).accepts(b"data", _ref())
        is expected
    )


def test_combinators_nest() -> None:
    predicate = AllOf(
        AnyOf(_PayloadIs(b"preferred"), _PayloadIs(b"acceptable")),
        Not(_PayloadIs(b"blocked")),
    )

    assert predicate.accepts(b"acceptable", _ref("https://example.com/7"))


def test_all_of_short_circuits_after_rejection() -> None:
    predicate = AllOf(_StaticPredicate(False), _RaisesIfCalled())

    assert predicate.accepts(b"data", _ref()) is False


def test_any_of_short_circuits_after_acceptance() -> None:
    predicate = AnyOf(_StaticPredicate(True), _RaisesIfCalled())

    assert predicate.accepts(b"data", _ref()) is True


def test_combinators_satisfy_fetch_predicate_at_runtime() -> None:
    assert isinstance(AllOf(), FetchPredicate)
    assert isinstance(AnyOf(), FetchPredicate)
    assert isinstance(Not(_StaticPredicate(True)), FetchPredicate)


def test_non_predicates_are_rejected_at_construction() -> None:
    for value in ("not a predicate", _NonCallableAccepts()):
        invalid = cast(FetchPredicate, value)

        with pytest.raises(TypeError, match="must implement callable accepts"):
            AllOf(invalid)
        with pytest.raises(TypeError, match="must implement callable accepts"):
            AnyOf(invalid)
        with pytest.raises(TypeError, match="must implement callable accepts"):
            Not(invalid)


def test_broken_accepts_descriptor_is_rejected_with_type_error() -> None:
    invalid = cast(FetchPredicate, _BrokenAcceptsDescriptor())

    with pytest.raises(TypeError, match="must implement callable accepts"):
        AllOf(invalid)
    with pytest.raises(TypeError, match="must implement callable accepts"):
        AnyOf(invalid)
    with pytest.raises(TypeError, match="must implement callable accepts"):
        Not(invalid)


def test_dataclasses_replace_preserves_variadic_combinators() -> None:
    predicate = _StaticPredicate(True)
    all_of = AllOf(predicate)
    any_of = AnyOf(predicate)

    assert replace(all_of) == all_of
    assert replace(any_of) == any_of


class _Source:
    def __init__(self, name: str, data: bytes) -> None:
        self.name = name
        self._data = data
        self.calls = 0

    def fetch(self) -> bytes:
        self.calls += 1
        return self._data


class _Sink(MultiSourceSink):
    def _fetch_from_source(
        self,
        source: object,
        ref: Ref,
        client: SyncHttpClientProtocol,
    ) -> bytes | None:
        if not isinstance(source, _Source):
            raise TypeError("test sink requires _Source instances")
        return source.fetch()


def _unused_client() -> SyncHttpClientProtocol:
    """Return a typed stand-in; the test sink never calls the client."""
    return cast(SyncHttpClientProtocol, object())


def test_multi_source_sink_falls_through_until_combinator_accepts() -> None:
    first = _Source("first", b"blocked")
    second = _Source("second", b"accepted")
    predicate = AllOf(
        AnyOf(_PayloadIs(b"blocked"), _PayloadIs(b"accepted")),
        Not(_PayloadIs(b"blocked")),
    )
    sink = _Sink(sources=[first, second], predicates=[predicate])

    data, source = sink.resolve_multi(_ref(), _unused_client())

    assert data == b"accepted"
    assert source is second
    assert first.calls == 1
    assert second.calls == 1


def test_multi_source_sink_returns_fallback_after_combinator_rejects() -> None:
    source = _Source("only", b"fallback")
    sink = _Sink(
        sources=[source],
        predicates=[
            AllOf(_PayloadIs(b"accepted"), Not(_PayloadIs(b"blocked")))
        ],
    )

    data, resolved_source = sink.resolve_multi(_ref(), _unused_client())

    assert data == b"fallback"
    assert resolved_source is source
