"""Fate-based credibility primitives — the Tier-2 / recorded-outcome producer (OLR §13.17;
see docs/deep-dives/dreaming.md, "Pass: fate-based credibility").

The pure core: a subject's observed *fate* (:class:`FateSignals` — what happened to it afterward,
read from external facts) combined into a polarity + grounding tier (:class:`FateVerdict`). The
fate *sources* (the supersession graph, git, the attribution map, the usage log) are wired in a
later step; this module is I/O-free so the combiner policy is unit-testable in isolation.

**Firewall (§13.7).** Objective fate — revert, the supersession *edge*, recurring reuse, survival,
churn (external facts) — is the strong tier; the supersession *reason* and any text-event are
*model-arbitrated*, so the verdict can report proxy↔truth **with and without** them. Committing is
not validated success, so positives must be *earned* objectively (survived + reused); the
model-arbitrated text-event contributes only the valuable **negative** (``UNDONE``), never a
manufactured positive.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from thalamus.experiential.recorded_outcome import RecordedEvent

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
    """How the polarity is grounded — the axis the verdict splits on for with/without reporting."""

    OBJECTIVE = "objective"  # an external act (git / graph edge / logs)
    MODEL_ARBITRATED = "model"  # model-written text — tiered below objective, calibrated


@dataclass(frozen=True, slots=True)
class FateSignals:
    """What happened to a subject afterward — populated from external sources (a later step)."""

    superseded: bool = False  # a SUPERSEDES edge targets it (graph fact)
    superseded_reason_negative: bool = False  # its supersession reason reads as failure (text)
    reverted: bool = False  # a sha it named was reverted (git)
    churn_ratio: float = 0.0  # fraction of its footprint rewritten within the horizon [0, 1]
    reuse_sessions: int = 0  # distinct later sessions that recalled + used it
    survived_activity: int = 0  # subsequent commits/events without supersession/revert
    text_event: RecordedEvent | None = None  # an optional grounded text-event (minor input)


@dataclass(frozen=True, slots=True)
class FateVerdict:
    """Combined verdict: polarity, the strongest tier of evidence behind it, and provenance."""

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
    """Combine observed fate into a polarity + grounding tier. Pure. Precedence:

    1. **Objective negative** — an external undo (revert) dominates any prior positive.
    2. **Superseded** — negative *iff* its reason reads as failure (model tier); else excluded
       (no longer current truth, but not a failure of the work).
    3. **Objective positive** — kept proving useful (reuse) and/or survived real later activity.
    4. **Weak objective negative** — its footprint was largely rewritten soon after (noisy → high
       threshold, lowest structural precedence; reached only if nothing above rescued it).
    5. **Model-arbitrated text event** — an *undone* act reported in text (never a positive: a
       reported/landed commit is not validated success).
    6. Otherwise **unknown**.
    """
    if signals.reverted:
        return FateVerdict(FatePolarity.NEGATIVE, OutcomeTier.OBJECTIVE, ("reverted",))

    if signals.superseded:
        if signals.superseded_reason_negative:
            return FateVerdict(
                FatePolarity.NEGATIVE,
                OutcomeTier.MODEL_ARBITRATED,
                ("superseded", "reason:negative"),
            )
        return FateVerdict(FatePolarity.UNKNOWN, OutcomeTier.OBJECTIVE, ("superseded:neutral",))

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

    if signals.text_event is RecordedEvent.UNDONE:
        return FateVerdict(FatePolarity.NEGATIVE, OutcomeTier.MODEL_ARBITRATED, ("text:undone",))
    # RecordedEvent.LANDED is intentionally NOT a positive (committing ≠ validated success);
    # it is kept upstream only for disambiguation + future difficulty grading.
    return FateVerdict(FatePolarity.UNKNOWN, OutcomeTier.OBJECTIVE, ())


def fate_success(verdict: FateVerdict) -> bool | None:
    """Map a verdict to a Tier-2 label: ``True`` / ``False`` / ``None`` (unknown — exclude),
    mirroring :func:`thalamus.experiential.outcome.is_success` for the proxy↔truth join."""
    if verdict.polarity is FatePolarity.POSITIVE:
        return True
    if verdict.polarity is FatePolarity.NEGATIVE:
        return False
    return None
