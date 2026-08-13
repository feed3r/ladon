"""typing.Protocol definitions for Ladon crawl plugins.

Adapters implement these protocols by structural subtyping — no
inheritance from this module is required. This keeps third-party
plugins decoupled from Ladon internals.

All adapters receive an object satisfying ``SyncHttpClientProtocol``: either
the native ``HttpClient`` or curl-cffi ``CurlHttpClient`` implementation. They
must not construct their own HTTP sessions or import ``requests`` directly.

The three-layer pipeline is:

    Source  →  [Expander, ...]  →  Sink

``Source[RefT]`` produces top-level refs. Each
``Expander[RefT, RecordT, ChildRawT]`` takes a ref and returns an
``Expansion`` (record + child refs). ``Sink[RefT, RecordT]`` takes a leaf
ref and returns a final record. ``CrawlPlugin[TopRefT, LeafRefT,
LeafRecordT]`` bundles all three.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import runtime_checkable

from typing_extensions import Any, Protocol, TypeVar

from ..networking.protocols import SyncHttpClientProtocol
from .models import Expansion

SourceRefT_co = TypeVar("SourceRefT_co", covariant=True, default=object)
ExpanderRefT_contra = TypeVar(
    "ExpanderRefT_contra", contravariant=True, default=object
)
ExpanderRecordT_co = TypeVar(
    "ExpanderRecordT_co", covariant=True, default=object
)
ExpanderChildRawT_co = TypeVar(
    "ExpanderChildRawT_co", covariant=True, default=object
)
SinkRefT_contra = TypeVar("SinkRefT_contra", contravariant=True, default=object)
SinkRecordT_co = TypeVar("SinkRecordT_co", covariant=True, default=object)
PluginTopRefT_co = TypeVar("PluginTopRefT_co", covariant=True, default=object)
PluginLeafRefT_contra = TypeVar(
    "PluginLeafRefT_contra", contravariant=True, default=object
)
PluginLeafRecordT_co = TypeVar(
    "PluginLeafRecordT_co", covariant=True, default=object
)


@runtime_checkable
class Source(Protocol[SourceRefT_co]):
    """Discover top-level refs from an external source."""

    def discover(
        self, client: SyncHttpClientProtocol
    ) -> Sequence[SourceRefT_co]:
        """Return all discoverable top-level references."""
        ...


@runtime_checkable
class Expander(
    Protocol[
        ExpanderRefT_contra,
        ExpanderRecordT_co,
        ExpanderChildRawT_co,
    ]
):
    """Expand one ref into a record plus child refs."""

    def expand(
        self, ref: ExpanderRefT_contra, client: SyncHttpClientProtocol
    ) -> Expansion[ExpanderRecordT_co, ExpanderChildRawT_co]:
        """Fetch ref, return its record and the child refs to process next.

        Raises:
            ExpansionNotReadyError: ref is not yet ready to be expanded.
            PartialExpansionError: child list is incomplete.
            ChildListUnavailableError: child list could not be retrieved.
        """
        ...


@runtime_checkable
class Sink(Protocol[SinkRefT_contra, SinkRecordT_co]):
    """Consume a leaf ref and return its final record."""

    def consume(
        self, ref: SinkRefT_contra, client: SyncHttpClientProtocol
    ) -> SinkRecordT_co:
        """Fetch and parse one leaf ref, returning a complete record.

        Context for the leaf (e.g. parent data) flows through
        ``ref.raw`` — no parent-record parameter is needed here.

        Raises:
            LeafUnavailableError: ref could not be fetched or parsed.
        """
        ...


@runtime_checkable
class CrawlPlugin(
    Protocol[
        PluginTopRefT_co,
        PluginLeafRefT_contra,
        PluginLeafRecordT_co,
    ]
):
    """Bundle of all adapters for one crawl domain.

    ``name`` is a short identifier used in log lines and error messages
    (e.g. ``"acme_shop"``, ``"widgetco"``). ``source`` produces
    top-level refs. ``expanders`` is an ordered list of expansion steps
    (one per tree level above the leaves). ``sink`` consumes the leaf
    refs produced by the last expander.

    The heterogeneous chain interior deliberately uses ``Any`` because a
    single homogeneous sequence cannot express per-stage types; see ADR-015.

    CLI convention
    --------------
    When loaded via ``ladon run --plugin module:Class``, the CLI
    instantiates the plugin as ``plugin_cls(client=client)``.  Adapters
    intended for CLI use **must** accept ``client`` as a keyword argument
    in ``__init__``.  This constraint is not enforced by the Protocol
    check — it is a CLI convention only and not part of this protocol.
    """

    @property
    def name(self) -> str: ...

    @property
    def source(self) -> Source[PluginTopRefT_co]: ...

    @property
    def expanders(self) -> Sequence[Expander[Any, Any, Any]]: ...

    @property
    def sink(self) -> Sink[PluginLeafRefT_contra, PluginLeafRecordT_co]: ...
