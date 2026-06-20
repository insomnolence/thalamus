"""Thalamus cross-package contracts, defined as ``typing.Protocol`` classes.

Components satisfy these via structural subtyping — no inheritance required.
A protocol lives in ``core`` only when more than one package depends on it;
package-local seams (e.g. trajectory observers) start in their own package and
are promoted here when a second consumer appears.

These are the **swappable seams** the design relies on: each encoder, store,
router, and retrieval rung is an interchangeable implementation behind one of
these interfaces (design-notes §14; build-discipline memory). **v0** — expected
to evolve. (Protocol/structural-subtyping pattern from an earlier project of
ours; its model-specific contracts are not carried over.)
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from thalamus.core.types import (
    Cue,
    MemoryRecord,
    MemoryRef,
    RetrievalResult,
    Scope,
    ScoredMemory,
    Supersession,
    Vector,
)


@runtime_checkable
class Encoder(Protocol):
    """Turns text into dense vectors (e.g. a frozen BGE wrapper)."""

    @property
    def dim(self) -> int:
        """Dimensionality of the vectors this encoder produces."""
        ...

    def encode(self, texts: Sequence[str]) -> list[Vector]:
        """Encode a batch of texts into vectors of length :attr:`dim`."""
        ...


@runtime_checkable
class Store(Protocol):
    """A per-hemisphere store: record CRUD + vector search over one index.

    The substrate is a single graph with *separate vector indexes per
    hemisphere* (deep-dives/foundation.md). Graph/link operations are added by
    the structural package when needed, not assumed here.
    """

    def add(self, record: MemoryRecord, embedding: Vector) -> None:
        """Persist a record and index its embedding."""
        ...

    def get(self, ref: MemoryRef) -> MemoryRecord | None:
        """Fetch a record by scoped identity, or ``None`` if absent."""
        ...

    def search(self, query: Vector, k: int, scope: Scope) -> list[ScoredMemory]:
        """Return the ``k`` nearest records within ``scope`` by vector similarity."""
        ...

    def scan(self, scope: Scope) -> list[MemoryRecord]:
        """Return all records within ``scope`` (unordered).

        The enumeration access pattern (vs. ``search``'s top-k): re-deriving
        cross-hemisphere links at serve time, dreaming re-segmentation, belief
        audits. Distinct from ``search`` because it is not query-driven.
        """
        ...


@runtime_checkable
class EmbeddingStore(Protocol):
    """A :class:`Store` that can additionally enumerate records *with* their embeddings.

    The normal read path (:meth:`Store.scan`) deliberately omits embeddings; this exposes
    them for faithful export (backup/restore, store migration) so a snapshot can be restored
    bit-for-bit without re-encoding — independent of which encoder is installed. A separate
    protocol so ordinary ``Store`` consumers are not forced to surface vectors; a store opts
    in by implementing it.
    """

    def scan_with_embeddings(self, scope: Scope) -> list[tuple[MemoryRecord, Vector]]:
        """Return all ``(record, embedding)`` pairs within ``scope`` (unordered)."""
        ...


@runtime_checkable
class SupersessionIndex(Protocol):
    """Records and reports belief-supersession edges (§13.18 R1).

    A directed memory↔memory edge — ``new`` supersedes ``old`` — carrying the reason
    and timestamp. The "current truth" the gateway surfaces is the *complement*: a
    memory is current iff :meth:`superseded` returns no entry for it (a derived view,
    §14.1). Mirrors the structural ``CrossLinkIndex`` (memory↔node) one hemisphere over;
    the in-memory and Neo4j implementations live in the ``experiential`` package.
    """

    def supersede(self, *, old: MemoryRef, new: MemoryRef, reason: str, at: datetime) -> None:
        """Mark ``old`` superseded by ``new`` with a reason. Never deletes ``old``."""
        ...

    def superseded(self, scope: Scope) -> Mapping[MemoryRef, Supersession]:
        """Map each superseded memory within ``scope`` to its supersession record."""
        ...


@runtime_checkable
class Router(Protocol):
    """Classifies a cue's intent for routing (BGE + labeled classifier)."""

    def route(self, cue: Cue) -> str:
        """Return an intent label for the cue."""
        ...


@runtime_checkable
class Retriever(Protocol):
    """The central swappable seam.

    L0 (frozen-BGE + recency + importance) is the first implementation; gated
    rungs (bandit reweighting, bent geometry) are later implementations behind
    this *same* interface, toggled by the eval harness
    (outcome-learned-retrieval §13.5, §13.20). This is what makes
    "switch off and measure against the baseline" a config choice, not a rewrite.
    """

    def retrieve(self, cue: Cue, k: int) -> RetrievalResult:
        """Retrieve up to ``k`` memories for ``cue``."""
        ...
