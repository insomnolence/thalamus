"""Thalamus core types: identifiers, scope, and the records that travel across
package boundaries.

Design notes:
- IDs are :class:`typing.NewType` over ``str`` — checked at type-check time,
  free at runtime.
- :class:`Scope` carries tenant/repo identifiers on *every* record and cue, per
  the multi-user "scope-now, defer-features" decision (design-notes §14,
  deep-dives/foundation.md). We operate single-tenant but never bake in
  single-tenant assumptions.
- Records are frozen, slotted dataclasses — immutable bundles that travel
  together. This is **v0** and is expected to evolve as Brain 1's episode/why
  schema is refined (deep-dives/outcome-learned-retrieval.md §13.16-13.17).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, NewType

# --- Identifiers -----------------------------------------------------------

TenantId = NewType("TenantId", str)
RepoId = NewType("RepoId", str)
MemoryId = NewType("MemoryId", str)
EpisodeId = NewType("EpisodeId", str)
EventId = NewType("EventId", str)
SessionId = NewType("SessionId", str)

# --- Vectors ---------------------------------------------------------------

type Vector = Sequence[float]
"""A dense embedding. Concrete implementations back this with numpy arrays;
``core`` stays dependency-free, so the contract is a plain float sequence."""


class Hemisphere(StrEnum):
    """The two separated memory hemispheres (design-notes §4)."""

    STRUCTURAL = "structural"  # Brain 2: re-derivable AST/code graph
    EXPERIENTIAL = "experiential"  # Brain 1: irreplaceable episodes / why / beliefs


# --- Records ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Scope:
    """Tenant + repository scoping applied to every record and cue.

    Present from day one so multi-tenant pooling needs no migration later;
    single-tenant operation simply uses a constant tenant.
    """

    tenant_id: TenantId
    repo_id: RepoId


@dataclass(frozen=True, slots=True)
class MemoryRef:
    """Stable identity of a memory within its isolation scope."""

    scope: Scope
    memory_id: MemoryId


@dataclass(frozen=True, slots=True)
class StructuralRef:
    """Stable identity of a structural node within its isolation scope."""

    scope: Scope
    node_id: str


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    """A single stored memory in either hemisphere. (v0 schema.)"""

    memory_id: MemoryId
    hemisphere: Hemisphere
    kind: str  # e.g. "episode" | "why" | "belief" | "code_symbol" — refined later
    content: str
    scope: Scope
    created_at: datetime
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def ref(self) -> MemoryRef:
        """Scoped identity used by stores and cross-hemisphere links."""
        return MemoryRef(scope=self.scope, memory_id=self.memory_id)


@dataclass(frozen=True, slots=True)
class Cue:
    """A retrieval request: the prompt + current focus the gateway routes."""

    text: str
    scope: Scope
    focus: str | None = None
    intent: str | None = None
    embedding: Vector | None = None
    session_id: SessionId | None = None


@dataclass(frozen=True, slots=True)
class ScoredMemory:
    """A candidate memory with its score and the features that produced it.

    ``features`` captures the decision-time signals (relevance, recency,
    importance, usage, ...) that the logging contract must persist for
    off-policy learning (outcome-learned-retrieval §13.11). Recorded at decision
    time so the value is never reconstructed (and mis-stated) after the fact.
    """

    record: MemoryRecord
    score: float
    features: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """The outcome of a retrieval.

    ``candidates`` is the *full* ranked pool considered — kept so the logging
    contract can record decision-time features for every candidate, not just the
    surfaced ones (§13.11). ``shown`` is the surfaced subset, in order — what the
    actuator actually receives (top-k for a deterministic policy; possibly an
    explored item once stochastic rungs arrive).
    """

    cue: Cue
    candidates: Sequence[ScoredMemory]
    shown: Sequence[ScoredMemory]
    event_id: EventId | None = None  # set by the logging layer; correlates outcomes to it
