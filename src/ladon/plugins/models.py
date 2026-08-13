"""Immutable data models for Ladon plugin adapters.

All models are frozen dataclasses. Adapters produce them; the runner
consumes them. The ``raw`` field on ``Ref`` carries house-specific data
that does not fit the shared schema.

``Ref[RawT]`` preserves adapter-specific raw context. ``Expansion[RecordT,
ChildRawT]`` carries the current record and typed child refs to the next stage.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Generic, overload

from typing_extensions import TypeVar

RawT = TypeVar("RawT", covariant=True, default=Mapping[str, object])
RecordT_co = TypeVar("RecordT_co", covariant=True, default=object)
ChildRawT_co = TypeVar("ChildRawT_co", covariant=True, default=object)

_MISSING_RAW = object()


def _empty_raw() -> dict[str, object]:
    """Return a typed empty dict for frozen dataclass ``raw`` fields."""
    return {}


@dataclass(frozen=True, init=False)
class Ref(Generic[RawT]):
    """Generic reference to any crawlable resource.

    ``url`` is the canonical URL of the resource. ``raw`` carries any
    house-specific data discovered alongside the URL (e.g. an ID or code
    needed by the expander). Omitting ``raw`` selects the default
    ``Mapping[str, object]`` specialization and supplies an empty dict.
    """

    url: str
    # The constructor overloads constrain omission to the default specialization.
    raw: RawT = field(default_factory=_empty_raw)  # type: ignore[assignment]

    @overload
    def __init__(self: Ref[Mapping[str, object]], url: str) -> None: ...

    @overload
    def __init__(self, url: str, raw: RawT) -> None: ...

    def __init__(self, url: str, raw: object = _MISSING_RAW) -> None:
        """Initialize a reference while preserving frozen-dataclass semantics."""
        object.__setattr__(self, "url", url)
        object.__setattr__(
            self, "raw", _empty_raw() if raw is _MISSING_RAW else raw
        )


@dataclass(frozen=True)
class Expansion(Generic[RecordT_co, ChildRawT_co]):
    """Result of an Expander.expand() call.

    Carries the record for the expanded node plus the child refs to be
    processed next (either expanded further or consumed by a Sink).
    """

    record: RecordT_co
    child_refs: Sequence[Ref[ChildRawT_co]]
