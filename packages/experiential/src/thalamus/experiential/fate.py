"""Fate-based credibility primitives — the credibility producer (OLR §13.17;
see docs/deep-dives/dreaming.md, "Pass: fate-based credibility").

The pure core: a subject's observed *fate* (:class:`FateSignals` — what happened to it afterward,
read from external facts) combined into a polarity (:class:`FateVerdict`). The fate *sources* (the
supersession graph, git, the attribution map, the usage log) are wired at the I/O edge; this module
is I/O-free so the combiner policy is unit-testable in isolation.

**Read fate, not words (§13.7 firewall).** Credibility is derived purely from *what happened to a
memory* — superseded (a graph edge), reverted (git), reused / survived (logs + time) — never from
parsing the memory's prose for good/bad sentiment (that is the Polynoica self-reference trap: the
model's opinion becoming its own label). Every signal here is therefore an external fact, tier
``OBJECTIVE``. ``OutcomeTier.MODEL_ARBITRATED`` is reserved for a future LLM-judge proposer
(propose-only, calibrated against objective fate, not yet built) — no current signal uses it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from thalamus.core.types import (
    EventId,
    MemoryId,
    MemoryRecord,
    MemoryRef,
    SessionId,
    Supersession,
)
from thalamus.instrumentation import RetrievalEvent, UsageSignal

# Defaults are conservative starting points; tuned on the settled backlog (dreaming.md open Qs).
_DEFAULT_SURVIVAL_THRESHOLD = 5  # subsequent commits/events survived without supersession/revert
_DEFAULT_REUSE_THRESHOLD = 2  # distinct later sessions that recalled *and used* it
_DEFAULT_CHURN_THRESHOLD = 0.7  # footprint fraction rewritten soon after → churned-away


class FatePolarity(StrEnum):
    """A subject's outcome polarity. ``UNKNOWN`` is excluded from truth metrics — never counted."""

    POSITIVE = "positive"
    NEGATIVE = "negative"
    UNKNOWN = "unknown"


class OutcomeTier(StrEnum):
    """How the polarity is grounded. Everything is ``OBJECTIVE`` today (external facts only);
    ``MODEL_ARBITRATED`` is reserved for a future LLM-judge proposer (propose-only, calibrated)."""

    OBJECTIVE = "objective"  # an external act (git / graph edge / logs)
    MODEL_ARBITRATED = "model"  # reserved: a future LLM judge, tiered below objective


@dataclass(frozen=True, slots=True)
class FateSignals:
    """What happened to a subject afterward — external facts only, populated at the I/O edge."""

    superseded: bool = False  # a SUPERSEDES edge targets it (graph fact)
    reverted: bool = False  # the commit it is (episode:<sha>) was reverted (git)
    churn_ratio: float = 0.0  # fraction of its footprint rewritten within the horizon [0, 1]
    reuse_sessions: int = 0  # distinct later sessions that recalled + used it
    survived_activity: int = 0  # subsequent commits/events without supersession/revert


@dataclass(frozen=True, slots=True)
class FateVerdict:
    """Combined verdict: polarity, the tier of evidence behind it, and provenance."""

    polarity: FatePolarity
    tier: OutcomeTier
    evidence: tuple[str, ...]


def assess_fate(
    signals: FateSignals,
    *,
    survival_threshold: int = _DEFAULT_SURVIVAL_THRESHOLD,
    reuse_threshold: int = _DEFAULT_REUSE_THRESHOLD,
    churn_threshold: float = _DEFAULT_CHURN_THRESHOLD,
) -> FateVerdict:
    """Combine observed fate into a polarity. Pure, all-objective. Precedence:

    1. **Reverted** — an external undo dominates any prior positive.
    2. **Superseded** — the belief was revised away (the SUPERSEDES edge is a fact); it is no
       longer current truth → a demote (negative). We do *not* read the reason text to guess
       wrong-vs-evolved — that would be reading words, not fate.
    3. **Positive** — kept proving useful (reuse) and/or survived substantial later activity.
    4. **Weak negative** — its footprint was largely rewritten soon after (noisy → high threshold,
       lowest precedence; reached only if nothing above applied).
    5. Otherwise **unknown** (insufficient fate — excluded, never counted "good").
    """
    if signals.reverted:
        return FateVerdict(FatePolarity.NEGATIVE, OutcomeTier.OBJECTIVE, ("reverted",))
    if signals.superseded:
        return FateVerdict(FatePolarity.NEGATIVE, OutcomeTier.OBJECTIVE, ("superseded",))

    reused = signals.reuse_sessions >= reuse_threshold
    survived = signals.survived_activity >= survival_threshold
    if reused or survived:
        evidence = tuple(
            name for name, fired in (("reused", reused), ("survived", survived)) if fired
        )
        return FateVerdict(FatePolarity.POSITIVE, OutcomeTier.OBJECTIVE, evidence)

    if signals.churn_ratio >= churn_threshold:
        return FateVerdict(
            FatePolarity.NEGATIVE, OutcomeTier.OBJECTIVE, (f"churn:{signals.churn_ratio:.2f}",)
        )

    return FateVerdict(FatePolarity.UNKNOWN, OutcomeTier.OBJECTIVE, ())


def fate_success(verdict: FateVerdict) -> bool | None:
    """Map a verdict to a Tier-2 label: ``True`` / ``False`` / ``None`` (unknown — exclude),
    mirroring :func:`thalamus.experiential.outcome.is_success` for the proxy↔truth join."""
    if verdict.polarity is FatePolarity.POSITIVE:
        return True
    if verdict.polarity is FatePolarity.NEGATIVE:
        return False
    return None


_HEX = frozenset("0123456789abcdef")


@dataclass(frozen=True, slots=True)
class FateContext:
    """Pre-loaded fate inputs, keyed by memory id — populated from external sources at the I/O
    edge (the supersession index, git, the attribution map, the retrieval/usage logs) so the
    aggregation below stays pure and unit-testable in isolation."""

    superseded: Mapping[MemoryId, Supersession]
    reverted_shas: frozenset[str] = frozenset()
    reuse_sessions: Mapping[MemoryId, int] = field(default_factory=dict)
    survived_activity: Mapping[MemoryId, int] = field(default_factory=dict)
    churn_ratio: Mapping[MemoryId, float] = field(default_factory=dict)


def _episode_sha(memory_id: str) -> str | None:
    """The git sha a commit-episode id encodes (``episode:<sha>``); None for session episodes
    (``episode:session:…``) or curated memories (``retained:…``)."""
    prefix = "episode:"
    if not memory_id.startswith(prefix):
        return None
    rest = memory_id[len(prefix) :]
    if not rest or rest.startswith("session:"):
        return None
    return rest if all(char in _HEX for char in rest.lower()) else None


def _sha_reverted(sha: str | None, reverted_shas: frozenset[str]) -> bool:
    """Prefix-tolerant membership (git abbreviates shas, so neither side's length is fixed)."""
    if sha is None or not reverted_shas:
        return False
    return any(
        other == sha or other.startswith(sha) or sha.startswith(other) for other in reverted_shas
    )


def fate_signals_for(memory: MemoryRecord, context: FateContext) -> FateSignals:
    """Assemble one memory's :class:`FateSignals` from the pre-loaded context. Pure — reads only
    external facts about the memory's fate, never its prose. A curated memory (``retained:…``) is
    never "reverted" (revert is for committed work — an ``episode:<sha>`` whose commit git shows
    reverted); its credibility comes from supersession + reuse + survival."""
    return FateSignals(
        superseded=memory.memory_id in context.superseded,
        reverted=_sha_reverted(_episode_sha(memory.memory_id), context.reverted_shas),
        churn_ratio=context.churn_ratio.get(memory.memory_id, 0.0),
        reuse_sessions=context.reuse_sessions.get(memory.memory_id, 0),
        survived_activity=context.survived_activity.get(memory.memory_id, 0),
    )


def compute_fate(
    memories: Sequence[MemoryRecord],
    context: FateContext,
    *,
    survival_threshold: int = _DEFAULT_SURVIVAL_THRESHOLD,
    reuse_threshold: int = _DEFAULT_REUSE_THRESHOLD,
    churn_threshold: float = _DEFAULT_CHURN_THRESHOLD,
) -> dict[MemoryId, FateVerdict]:
    """Assess every memory's fate → a verdict per memory id. Pure (I/O is the caller's job)."""
    return {
        memory.memory_id: assess_fate(
            fate_signals_for(memory, context),
            survival_threshold=survival_threshold,
            reuse_threshold=reuse_threshold,
            churn_threshold=churn_threshold,
        )
        for memory in memories
    }


def reuse_by_memory(
    events: Iterable[RetrievalEvent], signals: Iterable[UsageSignal]
) -> dict[MemoryId, int]:
    """Count the distinct later sessions in which each memory was recalled **and used** — the
    recurring-usefulness fate signal. Joins each ``used`` usage signal to its recall event's
    session (an unkeyed event is skipped). Pure."""
    session_of: dict[EventId, SessionId] = {
        event.event_id: event.session_id for event in events if event.session_id is not None
    }
    sessions: dict[MemoryId, set[SessionId]] = {}
    for signal in signals:
        if not signal.used:
            continue
        session = session_of.get(signal.event_id)
        if session is not None:
            sessions.setdefault(signal.memory_id, set()).add(session)
    return {memory_id: len(found) for memory_id, found in sessions.items()}


def build_fate_context(
    superseded: Mapping[MemoryRef, Supersession],
    events: Iterable[RetrievalEvent],
    signals: Iterable[UsageSignal],
    *,
    reverted_shas: frozenset[str] = frozenset(),
) -> FateContext:
    """Assemble a :class:`FateContext` from the supersession index + recall/usage logs (+ optional
    git ``reverted_shas``) — the loaders that fire on our own backlog. Churn (attribution map) and
    survival are added later; until then those signals stay at their empty defaults, so the
    credibility view is honest about what it can and cannot yet see."""
    return FateContext(
        superseded={ref.memory_id: record for ref, record in superseded.items()},
        reverted_shas=reverted_shas,
        reuse_sessions=reuse_by_memory(events, signals),
    )
