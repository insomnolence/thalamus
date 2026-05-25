"""The retrieval-event log schema (logging contract §13.11a).

This is the *irreversible-if-deferred* capture: decision-time candidate features
and the shown set + ranks + propensities cannot be reconstructed after the fact.
Tier-1/2/3 outcome signals are **not** stored here — they are joined later by
``event_id`` (so the log stays append-only and the join is explicit).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from thalamus.core.types import EventId, MemoryId, RepoId, Scope, SessionId, TenantId, Vector


@dataclass(frozen=True, slots=True)
class CandidateLog:
    """A single candidate's decision-time features (the inputs that ranked it)."""

    memory_id: MemoryId
    features: Mapping[str, float]


@dataclass(frozen=True, slots=True)
class ShownItem:
    """A surfaced memory: its rank and the selection propensity under the policy.

    ``propensity`` is the probability the policy would surface this item — 1.0 for
    a deterministic top-k policy, < 1.0 once stochastic/exploring rungs arrive.
    Required for unbiased off-policy estimation (IPS); the slot exists from day one.
    """

    memory_id: MemoryId
    rank: int
    propensity: float


@dataclass(frozen=True, slots=True)
class RetrievalEvent:
    """One retrieval, captured at decision time."""

    event_id: EventId
    timestamp: datetime
    scope: Scope
    policy_id: str
    cue_text: str
    k_requested: int
    candidates: Sequence[CandidateLog]
    shown: Sequence[ShownItem]
    session_id: SessionId | None = None
    cue_focus: str | None = None
    cue_intent: str | None = None
    cue_embedding: Vector | None = None


def serialize_event(event: RetrievalEvent) -> dict[str, Any]:
    """Convert a :class:`RetrievalEvent` to a JSON-serializable dict."""
    return {
        "event_id": str(event.event_id),
        "timestamp": event.timestamp.isoformat(),
        "scope": {"tenant_id": str(event.scope.tenant_id), "repo_id": str(event.scope.repo_id)},
        "policy_id": event.policy_id,
        "session_id": None if event.session_id is None else str(event.session_id),
        "cue": {
            "text": event.cue_text,
            "focus": event.cue_focus,
            "intent": event.cue_intent,
            "embedding": None if event.cue_embedding is None else list(event.cue_embedding),
        },
        "k_requested": event.k_requested,
        "candidates": [
            {"memory_id": str(c.memory_id), "features": dict(c.features)} for c in event.candidates
        ],
        "shown": [
            {"memory_id": str(s.memory_id), "rank": s.rank, "propensity": s.propensity}
            for s in event.shown
        ],
    }


def deserialize_event(obj: Mapping[str, Any]) -> RetrievalEvent:
    """Reconstruct a :class:`RetrievalEvent` from :func:`serialize_event`'s output.

    The faithful inverse of the serializer — the retrieval-event log is re-loadable
    for off-policy learning and offline eval (the logs *are* the eval substrate, §13.20).
    """
    cue = obj["cue"]
    embedding = cue["embedding"]
    session_id = obj["session_id"]
    return RetrievalEvent(
        event_id=EventId(str(obj["event_id"])),
        timestamp=datetime.fromisoformat(obj["timestamp"]),
        scope=Scope(
            tenant_id=TenantId(str(obj["scope"]["tenant_id"])),
            repo_id=RepoId(str(obj["scope"]["repo_id"])),
        ),
        policy_id=str(obj["policy_id"]),
        cue_text=str(cue["text"]),
        k_requested=int(obj["k_requested"]),
        candidates=[
            CandidateLog(
                memory_id=MemoryId(str(c["memory_id"])),
                features={str(name): float(value) for name, value in c["features"].items()},
            )
            for c in obj["candidates"]
        ],
        shown=[
            ShownItem(
                memory_id=MemoryId(str(s["memory_id"])),
                rank=int(s["rank"]),
                propensity=float(s["propensity"]),
            )
            for s in obj["shown"]
        ],
        session_id=None if session_id is None else SessionId(str(session_id)),
        cue_focus=None if cue["focus"] is None else str(cue["focus"]),
        cue_intent=None if cue["intent"] is None else str(cue["intent"]),
        cue_embedding=None if embedding is None else [float(x) for x in embedding],
    )
