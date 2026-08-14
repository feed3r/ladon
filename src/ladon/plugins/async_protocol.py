"""typing.Protocol definitions for async Ladon crawl plugins.

Async adapters implement these protocols by structural subtyping — no
inheritance from this module is required.

All async adapters receive an object satisfying ``AsyncHttpClientProtocol``:
either the native ``AsyncHttpClient`` or curl-cffi ``AsyncCurlHttpClient``
implementation. They must not construct their own HTTP sessions or import
``httpx`` directly.

The three-layer pipeline is:

    AsyncSource  →  [AsyncExpander, ...]  →  AsyncSink

``AsyncSource[RefT]`` discovers top-level refs. Each
``AsyncExpander[RefT, RecordT, ChildRawT]`` awaits a ref and returns an
``Expansion``. ``AsyncSink[RefT, RecordT]`` awaits a leaf ref and returns a
final record. ``AsyncCrawlPlugin[TopRefT, LeafRefT, LeafRecordT]`` bundles
all three.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import runtime_checkable

from typing_extensions import Any, Protocol, TypeVar

from ..networking.protocols import AsyncHttpClientProtocol
from .models import Expansion

AsyncSourceRefT_co = TypeVar(
    "AsyncSourceRefT_co", covariant=True, default=object
)
AsyncExpanderRefT_contra = TypeVar(
    "AsyncExpanderRefT_contra", contravariant=True, default=object
)
AsyncExpanderRecordT_co = TypeVar(
    "AsyncExpanderRecordT_co", covariant=True, default=object
)
AsyncExpanderChildRawT_co = TypeVar(
    "AsyncExpanderChildRawT_co", covariant=True, default=object
)
AsyncSinkRefT_contra = TypeVar(
    "AsyncSinkRefT_contra", contravariant=True, default=object
)
AsyncSinkRecordT_co = TypeVar(
    "AsyncSinkRecordT_co", covariant=True, default=object
)
AsyncPluginTopRefT_co = TypeVar(
    "AsyncPluginTopRefT_co", covariant=True, default=object
)
AsyncPluginLeafRefT_contra = TypeVar(
    "AsyncPluginLeafRefT_contra", contravariant=True, default=object
)
AsyncPluginLeafRecordT_co = TypeVar(
    "AsyncPluginLeafRecordT_co", covariant=True, default=object
)


@runtime_checkable
class AsyncSource(Protocol[AsyncSourceRefT_co]):
    """Discover top-level refs from an external source, asynchronously."""

    async def discover(
        self, client: AsyncHttpClientProtocol
    ) -> Sequence[AsyncSourceRefT_co]:
        """Return all discoverable top-level references."""
        ...


@runtime_checkable
class AsyncExpander(
    Protocol[
        AsyncExpanderRefT_contra,
        AsyncExpanderRecordT_co,
        AsyncExpanderChildRawT_co,
    ]
):
    """Expand one ref into a record plus child refs, asynchronously."""

    async def expand(
        self,
        ref: AsyncExpanderRefT_contra,
        client: AsyncHttpClientProtocol,
    ) -> Expansion[AsyncExpanderRecordT_co, AsyncExpanderChildRawT_co]:
        """Fetch ref, return its record and the child refs to process next.

        Raises:
            ExpansionNotReadyError: ref is not yet ready to be expanded.
            PartialExpansionError: child list is incomplete.
            ChildListUnavailableError: child list could not be retrieved.
        """
        ...


@runtime_checkable
class AsyncSink(Protocol[AsyncSinkRefT_contra, AsyncSinkRecordT_co]):
    """Consume a leaf ref and return its final record, asynchronously."""

    async def consume(
        self, ref: AsyncSinkRefT_contra, client: AsyncHttpClientProtocol
    ) -> AsyncSinkRecordT_co:
        """Fetch and parse one leaf ref, returning a complete record.

        Context for the leaf flows through ``ref.raw`` — no parent-record
        parameter is needed here.

        Raises:
            LeafUnavailableError: ref could not be fetched or parsed.
        """
        ...


@runtime_checkable
class AsyncCrawlPlugin(
    Protocol[
        AsyncPluginTopRefT_co,
        AsyncPluginLeafRefT_contra,
        AsyncPluginLeafRecordT_co,
    ]
):
    """Bundle of all async adapters for one crawl domain.

    ``name`` is a short identifier used in log lines and error messages.
    ``source`` discovers top-level refs. ``expanders`` is an ordered list
    of async expansion steps. ``sink`` consumes the leaf refs produced by
    the last expander.

    The heterogeneous chain interior deliberately uses ``Any`` because a
    single homogeneous sequence cannot express per-stage types; see ADR-015.
    """

    @property
    def name(self) -> str: ...

    @property
    def source(self) -> AsyncSource[AsyncPluginTopRefT_co]: ...

    @property
    def expanders(self) -> Sequence[AsyncExpander[Any, Any, Any]]: ...

    @property
    def sink(
        self,
    ) -> AsyncSink[AsyncPluginLeafRefT_contra, AsyncPluginLeafRecordT_co]: ...
