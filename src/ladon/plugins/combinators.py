"""Composable boolean predicates for multi-source fetch resolution.

The combinators in this module turn existing
:class:`~ladon.plugins.resolution.FetchPredicate` implementations into new
predicates.  They can be nested to describe an adapter's acceptance policy
without changing :class:`~ladon.plugins.resolution.MultiSourceSink` or the
individual predicates that supply its domain-specific checks.

Each combinator preserves Python's boolean short-circuit behaviour.  Later
predicates are therefore not evaluated once the result is known, which is
important when a predicate is expensive or records diagnostic state.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Ref
from .resolution import FetchPredicate


def _validated_predicates(
    predicates: tuple[object, ...],
) -> tuple[FetchPredicate, ...]:
    """Return *predicates* after validating their structural contract."""
    validated: list[FetchPredicate] = []
    for predicate in predicates:
        try:
            accepts = getattr(predicate, "accepts", None)
        except Exception as exc:
            raise TypeError(
                "predicate must implement callable accepts(data, ref); "
                f"got {type(predicate).__name__}"
            ) from exc
        if not callable(accepts) or not isinstance(predicate, FetchPredicate):
            raise TypeError(
                "predicate must implement callable accepts(data, ref); "
                f"got {type(predicate).__name__}"
            )
        validated.append(predicate)
    return tuple(validated)


@dataclass(frozen=True, slots=True, init=False)
class AllOf:
    """Accept only when every component predicate accepts.

    Predicates are evaluated from left to right and evaluation stops at the
    first rejection.  ``AllOf()`` accepts by vacuous truth, matching
    ``all(())``.  Components may themselves be :class:`AllOf`,
    :class:`AnyOf`, or :class:`Not` instances, allowing policies to be nested.

    .. note::
        Components are retained as an immutable tuple.  The predicate objects
        themselves are not copied; any state they intentionally maintain is
        shared with the combinator.
    """

    _predicates: tuple[FetchPredicate, ...]

    def __init__(
        self,
        *predicates: FetchPredicate,
        _predicates: tuple[FetchPredicate, ...] | None = None,
    ) -> None:
        if predicates and _predicates is not None:
            raise TypeError(
                "predicates cannot be supplied both positionally and by field"
            )
        components = predicates if _predicates is None else _predicates
        object.__setattr__(
            self, "_predicates", _validated_predicates(components)
        )

    def accepts(self, data: bytes, ref: Ref) -> bool:
        """Return True if every component accepts *data* for *ref*."""
        return all(
            predicate.accepts(data, ref) for predicate in self._predicates
        )


@dataclass(frozen=True, slots=True, init=False)
class AnyOf:
    """Accept when at least one component predicate accepts.

    Predicates are evaluated from left to right and evaluation stops at the
    first acceptance.  ``AnyOf()`` rejects, matching ``any(())``.  Components
    may themselves be :class:`AllOf`, :class:`AnyOf`, or :class:`Not`
    instances, allowing policies to be nested.

    .. note::
        Components are retained as an immutable tuple.  The predicate objects
        themselves are not copied; any state they intentionally maintain is
        shared with the combinator.
    """

    _predicates: tuple[FetchPredicate, ...]

    def __init__(
        self,
        *predicates: FetchPredicate,
        _predicates: tuple[FetchPredicate, ...] | None = None,
    ) -> None:
        if predicates and _predicates is not None:
            raise TypeError(
                "predicates cannot be supplied both positionally and by field"
            )
        components = predicates if _predicates is None else _predicates
        object.__setattr__(
            self, "_predicates", _validated_predicates(components)
        )

    def accepts(self, data: bytes, ref: Ref) -> bool:
        """Return True if any component accepts *data* for *ref*."""
        return any(
            predicate.accepts(data, ref) for predicate in self._predicates
        )


@dataclass(frozen=True, slots=True)
class Not:
    """Accept when one component predicate rejects, and vice versa.

    The component may be an :class:`AllOf`, :class:`AnyOf`, or another
    :class:`Not`, so negation composes with arbitrarily nested policies.  Only
    the wrapped predicate is evaluated.

    .. note::
        The predicate object is retained rather than copied, so any state it
        intentionally maintains is shared with the combinator.
    """

    _predicate: FetchPredicate

    def __post_init__(self) -> None:
        _validated_predicates((self._predicate,))

    def accepts(self, data: bytes, ref: Ref) -> bool:
        """Return the boolean negation of the component's result."""
        return not self._predicate.accepts(data, ref)
