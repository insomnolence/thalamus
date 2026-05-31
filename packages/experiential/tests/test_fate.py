"""Tests for the fate combiner — the pure policy that turns observed fate into a Tier-2 verdict.

Each branch is exercised with hand-built :class:`FateSignals` (no I/O), including the firewall
properties: an objective undo dominates any prior positive, a model-arbitrated text ``LANDED`` is
NOT a positive, and the objective/model tier is reported so the verdict can split with/without it.
"""

from __future__ import annotations

from thalamus.experiential.fate import (
    FatePolarity,
    FateSignals,
    OutcomeTier,
    assess_fate,
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
