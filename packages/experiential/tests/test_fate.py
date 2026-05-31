"""Tests for the fate combiner — the pure policy that turns observed fate into a Tier-2 verdict.

Each branch is exercised with hand-built :class:`FateSignals` (no I/O), including the firewall
properties: an objective undo dominates any prior positive, a model-arbitrated text ``LANDED`` is
NOT a positive, and the objective/model tier is reported so the verdict can split with/without it.
"""

from __future__ import annotations

from datetime import UTC, datetime

from thalamus.core.types import (
    Hemisphere,
    MemoryId,
    MemoryRecord,
    RepoId,
    Scope,
    Supersession,
    TenantId,
)
from thalamus.experiential.fate import (
    FateContext,
    FatePolarity,
    FateSignals,
    OutcomeTier,
    assess_fate,
    compute_fate,
    fate_success,
)
from thalamus.experiential.recorded_outcome import RecordedEvent


def test_reverted_is_an_objective_negative() -> None:
    verdict = assess_fate(FateSignals(reverted=True))
    assert verdict.polarity is FatePolarity.NEGATIVE
    assert verdict.tier is OutcomeTier.OBJECTIVE
    assert "reverted" in verdict.evidence


def test_revert_dominates_prior_reuse() -> None:
    # An external undo dominates any prior positive — reuse must not rescue a reverted subject.
    verdict = assess_fate(FateSignals(reverted=True, reuse_sessions=9, survived_activity=20))
    assert verdict.polarity is FatePolarity.NEGATIVE


def test_superseded_with_negative_reason_is_a_model_tier_negative() -> None:
    verdict = assess_fate(FateSignals(superseded=True, superseded_reason_negative=True))
    assert verdict.polarity is FatePolarity.NEGATIVE
    assert verdict.tier is OutcomeTier.MODEL_ARBITRATED


def test_superseded_neutral_is_excluded_not_a_failure() -> None:
    # No longer current truth, but not a failure of the work → unknown (excluded), objective edge.
    verdict = assess_fate(FateSignals(superseded=True, superseded_reason_negative=False))
    assert verdict.polarity is FatePolarity.UNKNOWN
    assert verdict.tier is OutcomeTier.OBJECTIVE


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


def test_text_undone_is_a_model_tier_negative() -> None:
    verdict = assess_fate(FateSignals(text_event=RecordedEvent.UNDONE))
    assert verdict.polarity is FatePolarity.NEGATIVE
    assert verdict.tier is OutcomeTier.MODEL_ARBITRATED


def test_text_landed_is_not_a_positive() -> None:
    # Firewall: committing / a model approval is not validated success → excluded, never positive.
    verdict = assess_fate(FateSignals(text_event=RecordedEvent.LANDED))
    assert verdict.polarity is FatePolarity.UNKNOWN


def test_no_signals_is_unknown() -> None:
    verdict = assess_fate(FateSignals())
    assert verdict.polarity is FatePolarity.UNKNOWN


def test_thresholds_are_configurable() -> None:
    signals = FateSignals(reuse_sessions=1, survived_activity=1)
    assert assess_fate(signals).polarity is FatePolarity.UNKNOWN  # below defaults
    loosened = assess_fate(signals, reuse_threshold=1)
    assert loosened.polarity is FatePolarity.POSITIVE


def test_fate_success_mapping() -> None:
    assert fate_success(assess_fate(FateSignals(reuse_sessions=3))) is True
    assert fate_success(assess_fate(FateSignals(reverted=True))) is False
    assert fate_success(assess_fate(FateSignals())) is None


# --- compute_fate: the pure aggregator that assembles signals from pre-loaded fate context ---

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


def _superseded(memory_id: str, reason: str) -> dict[MemoryId, Supersession]:
    record = Supersession(
        superseded_by=MemoryId("newer"), reason=reason, at=datetime(2026, 1, 2, tzinfo=UTC)
    )
    return {MemoryId(memory_id): record}


def test_compute_fate_positive_from_reuse() -> None:
    context = FateContext(superseded={}, reuse_sessions={MemoryId("m1"): 3})
    verdicts = compute_fate([_mem("m1")], context)
    assert verdicts[MemoryId("m1")].polarity is FatePolarity.POSITIVE


def test_compute_fate_negative_when_a_sha_named_in_text_was_reverted() -> None:
    context = FateContext(superseded={}, reverted_shas=frozenset({"abc1234"}))
    verdicts = compute_fate([_mem("m1", "committed as abc1234 then it broke")], context)
    verdict = verdicts[MemoryId("m1")]
    assert verdict.polarity is FatePolarity.NEGATIVE  # revert dominates the LANDED text-event
    assert verdict.tier is OutcomeTier.OBJECTIVE


def test_compute_fate_negative_from_reverted_episode_sha() -> None:
    context = FateContext(superseded={}, reverted_shas=frozenset({"deadbee"}))
    verdict = compute_fate([_mem("episode:deadbee")], context)[MemoryId("episode:deadbee")]
    assert verdict.polarity is FatePolarity.NEGATIVE


def test_compute_fate_superseded_with_undo_reason_is_model_negative() -> None:
    context = FateContext(superseded=_superseded("m1", "reverted the approach and redid it"))
    verdict = compute_fate([_mem("m1")], context)[MemoryId("m1")]
    assert verdict.polarity is FatePolarity.NEGATIVE
    assert verdict.tier is OutcomeTier.MODEL_ARBITRATED


def test_compute_fate_superseded_neutral_reason_is_excluded() -> None:
    context = FateContext(superseded=_superseded("m1", "switched to Y; requirements changed"))
    assert compute_fate([_mem("m1")], context)[MemoryId("m1")].polarity is FatePolarity.UNKNOWN


def test_compute_fate_plain_memory_is_unknown() -> None:
    memory = _mem("m1", "We will derive the profile from the host signal.")
    verdict = compute_fate([memory], FateContext(superseded={}))[MemoryId("m1")]
    assert verdict.polarity is FatePolarity.UNKNOWN
