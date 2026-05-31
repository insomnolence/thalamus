"""Tests for the fate combiner + aggregator — purely structural fate, no prose-reading.

Each branch is exercised with hand-built FateSignals (no I/O). The firewall property under test:
credibility is derived only from what happened to a memory (superseded / reverted / reused /
survived / churn), never from its text — so a memory whose body merely *discusses* reverts is not
flagged.
"""

from __future__ import annotations

from datetime import UTC, datetime

from thalamus.core.types import (
    EventId,
    Hemisphere,
    MemoryId,
    MemoryRecord,
    MemoryRef,
    RepoId,
    Scope,
    SessionId,
    Supersession,
    TenantId,
)
from thalamus.experiential.fate import (
    FateContext,
    FatePolarity,
    FateSignals,
    OutcomeTier,
    assess_fate,
    build_fate_context,
    compute_fate,
    fate_success,
    reuse_by_memory,
)
from thalamus.instrumentation import RetrievalEvent, ShownItem, UsageSignal


def test_reverted_is_an_objective_negative() -> None:
    verdict = assess_fate(FateSignals(reverted=True))
    assert verdict.polarity is FatePolarity.NEGATIVE
    assert verdict.tier is OutcomeTier.OBJECTIVE
    assert "reverted" in verdict.evidence


def test_revert_dominates_prior_reuse() -> None:
    verdict = assess_fate(FateSignals(reverted=True, reuse_sessions=9, survived_activity=20))
    assert verdict.polarity is FatePolarity.NEGATIVE


def test_superseded_is_an_objective_negative() -> None:
    # The SUPERSEDES edge is a fact: the belief was revised away → demote. No reason text is read.
    verdict = assess_fate(FateSignals(superseded=True))
    assert verdict.polarity is FatePolarity.NEGATIVE
    assert verdict.tier is OutcomeTier.OBJECTIVE
    assert "superseded" in verdict.evidence


def test_superseded_dominates_prior_reuse() -> None:
    verdict = assess_fate(FateSignals(superseded=True, reuse_sessions=9, survived_activity=20))
    assert verdict.polarity is FatePolarity.NEGATIVE


def test_recurring_reuse_is_an_objective_positive() -> None:
    verdict = assess_fate(FateSignals(reuse_sessions=3))
    assert verdict.polarity is FatePolarity.POSITIVE
    assert verdict.tier is OutcomeTier.OBJECTIVE
    assert "reused" in verdict.evidence


def test_survival_is_an_objective_positive() -> None:
    verdict = assess_fate(FateSignals(survived_activity=10))
    assert verdict.polarity is FatePolarity.POSITIVE
    assert verdict.evidence == ("survived",)


def test_reuse_and_survival_both_recorded() -> None:
    verdict = assess_fate(FateSignals(reuse_sessions=5, survived_activity=8))
    assert verdict.polarity is FatePolarity.POSITIVE
    assert set(verdict.evidence) == {"reused", "survived"}


def test_churn_away_is_a_weak_objective_negative() -> None:
    verdict = assess_fate(FateSignals(churn_ratio=0.85))
    assert verdict.polarity is FatePolarity.NEGATIVE
    assert verdict.tier is OutcomeTier.OBJECTIVE


def test_survival_outranks_churn() -> None:
    # A subject that survived substantial later work is positive even if early churn was high.
    verdict = assess_fate(FateSignals(survived_activity=10, churn_ratio=0.9))
    assert verdict.polarity is FatePolarity.POSITIVE


def test_no_signals_is_unknown() -> None:
    assert assess_fate(FateSignals()).polarity is FatePolarity.UNKNOWN


def test_thresholds_are_configurable() -> None:
    signals = FateSignals(reuse_sessions=1, survived_activity=1)
    assert assess_fate(signals).polarity is FatePolarity.UNKNOWN  # below defaults
    assert assess_fate(signals, reuse_threshold=1).polarity is FatePolarity.POSITIVE


def test_fate_success_mapping() -> None:
    assert fate_success(assess_fate(FateSignals(reuse_sessions=3))) is True
    assert fate_success(assess_fate(FateSignals(reverted=True))) is False
    assert fate_success(assess_fate(FateSignals())) is None


# --- compute_fate: the pure aggregator over a pre-loaded fate context ---

_SCOPE = Scope(tenant_id=TenantId("t"), repo_id=RepoId("r"))


def _mem(memory_id: str, content: str = "") -> MemoryRecord:
    return MemoryRecord(
        memory_id=MemoryId(memory_id),
        hemisphere=Hemisphere.EXPERIENTIAL,
        kind="decision",
        content=content,
        scope=_SCOPE,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _superseded(memory_id: str) -> dict[MemoryId, Supersession]:
    record = Supersession(
        superseded_by=MemoryId("newer"), reason="replaced", at=datetime(2026, 1, 2, tzinfo=UTC)
    )
    return {MemoryId(memory_id): record}


def test_compute_fate_positive_from_reuse() -> None:
    context = FateContext(superseded={}, reuse_sessions={MemoryId("m1"): 3})
    assert compute_fate([_mem("m1")], context)[MemoryId("m1")].polarity is FatePolarity.POSITIVE


def test_compute_fate_negative_from_reverted_episode_sha() -> None:
    context = FateContext(superseded={}, reverted_shas=frozenset({"deadbee"}))
    verdict = compute_fate([_mem("episode:deadbee")], context)[MemoryId("episode:deadbee")]
    assert verdict.polarity is FatePolarity.NEGATIVE


def test_compute_fate_superseded_is_negative() -> None:
    verdict = compute_fate([_mem("m1")], FateContext(superseded=_superseded("m1")))[MemoryId("m1")]
    assert verdict.polarity is FatePolarity.NEGATIVE
    assert verdict.tier is OutcomeTier.OBJECTIVE


def test_compute_fate_plain_memory_is_unknown() -> None:
    verdict = compute_fate([_mem("m1")], FateContext(superseded={}))[MemoryId("m1")]
    assert verdict.polarity is FatePolarity.UNKNOWN


def test_curated_body_is_never_read_for_fate() -> None:
    # Credibility is structural: a curated memory whose body merely *discusses* reverts/redo — with
    # no supersession/revert/reuse fate of its own — stays unknown (we never parse its prose).
    memory = _mem("m1", "Decided to add git revert detection; the dev pushed back the CI approach.")
    verdict = compute_fate([memory], FateContext(superseded={}))[MemoryId("m1")]
    assert verdict.polarity is FatePolarity.UNKNOWN


# --- the log-derived loaders (reuse / context assembly) ---


def _event(eid: str, session: str, shown: list[str]) -> RetrievalEvent:
    return RetrievalEvent(
        event_id=EventId(eid),
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        scope=_SCOPE,
        policy_id="L0",
        cue_text="q",
        k_requested=len(shown),
        candidates=[],
        shown=[ShownItem(MemoryId(m), rank=i, propensity=1.0) for i, m in enumerate(shown)],
        session_id=SessionId(session),
    )


def _signal(eid: str, mid: str, *, used: bool) -> UsageSignal:
    return UsageSignal(EventId(eid), MemoryId(mid), "footprint", 1.0 if used else 0.0, used)


def test_reuse_by_memory_counts_distinct_used_sessions() -> None:
    events = [_event("e1", "s1", ["m1"]), _event("e2", "s2", ["m1"]), _event("e3", "s1", ["m1"])]
    signals = [_signal(e, "m1", used=True) for e in ("e1", "e2", "e3")]
    # used in sessions s1 (e1, e3) and s2 (e2) → 2 distinct sessions
    assert reuse_by_memory(events, signals) == {MemoryId("m1"): 2}


def test_reuse_by_memory_ignores_unused_and_unkeyed_events() -> None:
    events = [_event("e1", "s1", ["m1"])]
    signals = [_signal("e1", "m1", used=False), _signal("eX", "m2", used=True)]
    assert reuse_by_memory(events, signals) == {}


def test_build_fate_context_feeds_compute_fate() -> None:
    superseded = {
        MemoryRef(_SCOPE, MemoryId("old")): Supersession(
            superseded_by=MemoryId("new"), reason="replaced", at=datetime(2026, 1, 2, tzinfo=UTC)
        )
    }
    events = [_event("e1", "s1", ["hot"]), _event("e2", "s2", ["hot"])]
    signals = [_signal("e1", "hot", used=True), _signal("e2", "hot", used=True)]
    context = build_fate_context(superseded, events, signals)
    assert context.reuse_sessions[MemoryId("hot")] == 2
    assert MemoryId("old") in context.superseded

    verdicts = compute_fate([_mem("hot"), _mem("old")], context)
    assert verdicts[MemoryId("hot")].polarity is FatePolarity.POSITIVE  # reused in 2 sessions
    assert verdicts[MemoryId("old")].polarity is FatePolarity.NEGATIVE  # superseded
