"""Multi-source resolution utilities for Ladon Sink implementations.

Provides the ``FetchPredicate`` protocol and ``MultiSourceSink`` base class,
which together encode the *try-until-accepted* resolution loop used by any
Sink that resolves a record field from multiple ranked sources.

The loop pattern
----------------

::

    for source in sources (ordered best-first):
        if not _should_try_source(source, ref):   # tier-skip, guards
            continue
        try:
            data = _fetch_from_source(source, ref, client)
        except recoverable source error:
            continue                              # unexpected errors propagate
        if not data:
            continue                               # source returned nothing / empty bytes
        if _is_better_candidate(data, ...):        # update best-seen fallback
            best = (data, source)
        if not predicates or all predicates pass:
            return (data, source)                  # accepted — stop
    return best                                    # best-seen fallback (may be None)

``FetchPredicate`` is the extension point: adapters inject domain-specific
acceptance criteria (image width, placeholder detection, price tolerance …)
without modifying the loop mechanics.

ADR: ADR-013
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Protocol, Sequence, runtime_checkable

from ..networking.errors import HttpClientError
from ..networking.protocols import SyncHttpClientProtocol
from ..observability import DecisionEvent, DecisionTracker, NullDecisionTracker
from .errors import LeafUnavailableError
from .models import Ref

logger = logging.getLogger(__name__)

_NULL_TRACKER = NullDecisionTracker()


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class FetchPredicate(Protocol):
    """Acceptance criterion on a raw fetch result.

    Returns ``True`` if *data* is good enough to stop the resolution loop.
    Returns ``False`` to keep *data* as a fallback candidate and continue to
    the next source in search of a better result.

    .. note::
        ``isinstance(obj, FetchPredicate)`` only checks that an ``accepts``
        attribute *exists* — it does not verify callability or signature.
        Passing a mis-shaped object will raise ``TypeError`` at call time
        inside :meth:`MultiSourceSink.resolve_multi`, not at construction.

    Predicates may optionally expose a duck-typed, zero-argument
    ``rejection_info() -> dict[str, Any]`` method; it is deliberately not a
    Protocol member, so existing predicates without it continue to work.
    When a predicate rejects a result, :meth:`MultiSourceSink.resolve_multi`
    merges the returned mapping into that ``predicate_rejected`` event's
    metadata. Exceptions from this method are swallowed and logged at DEBUG
    level. Its ``predicate_name`` value, if any, is always overwritten by the
    authoritative rejecting predicate name.
    """

    def accepts(self, data: bytes, ref: Ref) -> bool:
        """Return True if this result is acceptable."""
        ...


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


class MultiSourceSink:
    """Base for Sink implementations that resolve from multiple ranked sources.

    Subclasses must override :meth:`_fetch_from_source` to adapt their
    specific source interface. Three optional hooks control loop behaviour:

    ``_should_try_source(source, ref) → bool``
        Pre-fetch guard. Default: always try. Override to skip sources whose
        tier cannot improve on the existing record, or to enforce rate limits.

    ``_is_better_candidate(data, source, best_data, best_source, ref) → bool``
        Fallback selection. Default: first non-empty result wins. Override for
        domain-specific ranking (e.g., prefer wider images when multiple
        below-threshold results exist).

    ``_all_predicates_pass(data, ref) → bool``
        Acceptance customization when at least one predicate is registered.
        Default: all registered predicates must accept. Override to add
        criteria beyond the registered predicates. The predicate-free fast
        path accepts the first non-empty result without calling this hook.

    ``_fetch_from_source(source, ref, client) → bytes | None``
        Calls the source's native interface. Must be overridden; there is no
        default implementation because source interfaces vary per adapter.

    The main entry point is :meth:`resolve_multi`, which runs the loop and
    returns ``(data, source)`` for the best accepted result, or the best
    fallback if no result passed all predicates.

    A ``tracker`` may be injected at construction time to persist a structured
    decision trail. If omitted, :class:`NullDecisionTracker` is used (no-op,
    zero overhead). See :mod:`ladon.observability` for the protocol and
    :mod:`ladon.contrib.sqlite_tracker` for a ready-made SQLite backend.
    """

    def __init__(
        self,
        sources: list[Any],
        predicates: Sequence[FetchPredicate] = (),
        tracker: DecisionTracker = _NULL_TRACKER,
    ) -> None:
        self._ms_sources: list[Any] = list(sources)
        self._ms_predicates: list[FetchPredicate] = list(predicates)
        self._tracker = tracker

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------

    @property
    def sources(self) -> list[Any]:
        """Priority-ordered source list (read-only copy)."""
        return list(self._ms_sources)

    # ------------------------------------------------------------------
    # Hooks — override in subclasses
    # ------------------------------------------------------------------

    def _fetch_from_source(
        self, _source: Any, _ref: Ref, _client: SyncHttpClientProtocol
    ) -> bytes | None:
        """Fetch raw bytes from *source* for *ref*. Must be overridden."""
        raise NotImplementedError(
            f"{type(self).__name__} must implement _fetch_from_source"
        )

    def _should_try_source(self, _source: Any, _ref: Ref) -> bool:
        """Return True if *source* should be attempted for *ref*.

        Default: always try. Override to implement tier-skip, cooldown
        guards, or any other pre-fetch filtering.
        """
        return True

    def _is_better_candidate(
        self,
        _data: bytes,
        _source: Any,
        _best_data: bytes | None,
        best_source: Any | None,
        _ref: Ref,
    ) -> bool:
        """Return True if *(data, source)* should replace the current best.

        Default: first non-empty result wins (``best_source is None``).
        Override for domain-specific fallback ranking.

        .. warning::
            If a subclass overrides this to always return ``False`` (e.g. due
            to a bug in the ranking logic), ``best_data`` is never updated and
            :meth:`resolve_multi` returns ``(None, None)`` even when sources
            produce data.  The caller cannot distinguish this from "no sources
            returned data" without inspecting logs.
        """
        return best_source is None

    # ------------------------------------------------------------------
    # Loop
    # ------------------------------------------------------------------

    def _first_failing_predicate(
        self, data: bytes, ref: Ref
    ) -> FetchPredicate | None:
        """Return the first registered predicate that rejects *data*, evaluating
        each predicate at most once. Returns None if every predicate accepts
        (or none are registered)."""
        for p in self._ms_predicates:
            if not p.accepts(data, ref):
                return p
        return None

    def _all_predicates_pass(self, data: bytes, ref: Ref) -> bool:
        """Return True if *data* satisfies every registered predicate.

        Override to add acceptance criteria beyond registered predicates.
        This hook is not called when no predicates are registered.
        """
        return self._first_failing_predicate(data, ref) is None

    def resolve_multi(
        self, ref: Ref, client: SyncHttpClientProtocol, *, run_id: str = ""
    ) -> tuple[bytes | None, Any | None]:
        """Try sources in priority order; return the best accepted result.

        Returns ``(data, source)`` for the first result that passes all
        predicates, or the best fallback seen if no result was accepted.
        Returns ``(None, None)`` if no source produced any result.

        ``run_id`` is forwarded to every :class:`~ladon.observability.DecisionEvent`
        emitted during this call. Pass the runner's ``run_id`` for cross-item
        correlation; omit it to get a fresh UUID scoped to this resolution.

        .. note::
            :class:`~ladon.networking.errors.HttpClientError` and
            :class:`~ladon.plugins.errors.LeafUnavailableError` raised by
            :meth:`_fetch_from_source` are recorded as ``source_failed`` and
            the loop continues to the next source. Other exceptions propagate;
            :exc:`NotImplementedError` is re-raised immediately so the
            "subclass must override" contract is preserved.
        """
        _run_id = run_id or str(uuid.uuid4())
        best_data: bytes | None = None
        best_source: Any | None = None

        for source in self._ms_sources:
            source_name: str = getattr(source, "name", str(source))

            if not self._should_try_source(source, ref):
                self._tracker.record(
                    DecisionEvent(
                        run_id=_run_id,
                        timestamp=datetime.now(timezone.utc),
                        ref=ref.url,
                        source=source_name,
                        event="source_skipped",
                        reason="source guard returned False",
                    )
                )
                logger.debug(
                    "resolution: skipping source %r for %s",
                    source_name,
                    ref.url,
                )
                continue

            try:
                data = self._fetch_from_source(source, ref, client)
            except NotImplementedError:
                raise
            except (HttpClientError, LeafUnavailableError) as exc:
                self._tracker.record(
                    DecisionEvent(
                        run_id=_run_id,
                        timestamp=datetime.now(timezone.utc),
                        ref=ref.url,
                        source=source_name,
                        event="source_failed",
                        reason=str(exc),
                        metadata={
                            "exception_type": type(exc).__name__,
                            "status_code": getattr(exc, "status_code", None),
                        },
                    )
                )
                logger.warning(
                    "resolution: source %r raised %s for %s; trying next",
                    source_name,
                    type(exc).__name__,
                    ref.url,
                )
                continue

            if not data:
                logger.debug(
                    "resolution: %r returned no data for %s",
                    source_name,
                    ref.url,
                )
                continue

            # Update the best-seen fallback before checking predicates.
            # If predicates pass we return data/source directly (not best_data),
            # so the update is a no-op in that path. If predicates fail, the
            # updated best_data becomes the last-resort fallback after the loop.
            if self._is_better_candidate(
                data, source, best_data, best_source, ref
            ):
                best_data = data
                best_source = source
                self._tracker.record(
                    DecisionEvent(
                        run_id=_run_id,
                        timestamp=datetime.now(timezone.utc),
                        ref=ref.url,
                        source=source_name,
                        event="candidate_accepted",
                        reason="new best candidate",
                    )
                )
                logger.debug(
                    "resolution: %r is new best candidate for %s",
                    source_name,
                    ref.url,
                )
            else:
                self._tracker.record(
                    DecisionEvent(
                        run_id=_run_id,
                        timestamp=datetime.now(timezone.utc),
                        ref=ref.url,
                        source=source_name,
                        event="candidate_rejected",
                        reason="not stored as best-seen fallback; may still be resolved if predicates pass",
                    )
                )

            if not self._ms_predicates:
                accepted, failing = True, None
            else:
                predicate_check = self._all_predicates_pass
                if (
                    getattr(predicate_check, "__func__", None)
                    is MultiSourceSink._all_predicates_pass
                ):
                    # Default path: one evaluation pass serves both the accept
                    # decision and (on rejection) the failing predicate's identity
                    # for metadata.
                    failing = self._first_failing_predicate(data, ref)
                    accepted = failing is None
                else:
                    # A dynamically resolved _all_predicates_pass override has
                    # logic we can't introspect; call it for the decision. Only
                    # re-scan registered predicates (a second, unavoidable pass)
                    # to attribute the rejection if it rejects.
                    accepted = predicate_check(data, ref)
                    failing = (
                        None
                        if accepted
                        else self._first_failing_predicate(data, ref)
                    )

            if accepted:
                self._tracker.record(
                    DecisionEvent(
                        run_id=_run_id,
                        timestamp=datetime.now(timezone.utc),
                        ref=ref.url,
                        source=source_name,
                        event="resolved",
                        reason="accepted from active source",
                        metadata={"via_fallback": False},
                    )
                )
                logger.debug(
                    "resolution: accepted result from %r for %s",
                    source_name,
                    ref.url,
                )
                return data, source

            # If failing is None, the rejection came from an
            # _all_predicates_pass override rather than a registered predicate.
            predicate_name = (
                type(failing).__name__
                if failing is not None
                else "<subclass-override>"
            )
            rejection_meta: dict[str, Any] = {}
            if failing is not None:
                _get_info = getattr(failing, "rejection_info", None)
                if callable(_get_info):
                    try:
                        rejection_meta.update(_get_info())  # type: ignore[arg-type]
                    except Exception as _info_exc:
                        logger.debug(
                            "rejection_info() on %r raised %s — ignoring",
                            predicate_name,
                            _info_exc,
                        )
            # predicate_name is written last so a badly-behaved rejection_info()
            # cannot overwrite it.
            rejection_meta["predicate_name"] = predicate_name
            self._tracker.record(
                DecisionEvent(
                    run_id=_run_id,
                    timestamp=datetime.now(timezone.utc),
                    ref=ref.url,
                    source=source_name,
                    event="predicate_rejected",
                    reason="one or more predicates rejected the result",
                    metadata=rejection_meta,
                )
            )
            logger.debug(
                "resolution: %r did not pass all predicates for %s; trying next",
                source_name,
                ref.url,
            )

        if best_data is not None:
            self._tracker.record(
                DecisionEvent(
                    run_id=_run_id,
                    timestamp=datetime.now(timezone.utc),
                    ref=ref.url,
                    source=(
                        getattr(best_source, "name", str(best_source))
                        if best_source is not None
                        else None
                    ),
                    event="resolved",
                    reason="best-seen fallback returned after loop exhausted",
                    metadata={"via_fallback": True},
                )
            )
        else:
            self._tracker.record(
                DecisionEvent(
                    run_id=_run_id,
                    timestamp=datetime.now(timezone.utc),
                    ref=ref.url,
                    source=None,
                    event="no_result",
                    reason="no source produced a usable result",
                )
            )

        return best_data, best_source
