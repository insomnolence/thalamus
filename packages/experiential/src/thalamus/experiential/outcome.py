"""Tier-2 outcome capture — the deterministic *truth* signal (OLR §13.8).

Tier-1 (usage / content-overlap) is the cheap *training target*; Tier-2 is the
meaningful-but-confounded *gate*: did the work actually succeed? Classified
deterministically from a trajectory span — test pass/fail, commit, revert — never
from the model's own judgement (the external-outcome discipline, §13.7). It is the
truth side the proxy↔truth monitor (§13.12) holds ``utility@k`` against.

Coarse v0 over the signals current observers emit (COMMIT, TEST_RUN, ERROR, REVERT).
``kept-vs-reverted`` sharpens once a revert/reflog observer lands; ``accept/modify/
reject`` and ``later-contradicted`` are richer Tier-2 signals for later.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum

from thalamus.instrumentation import TrajectoryEvent, TrajectoryEventKind


class EpisodeOutcome(StrEnum):
    """A span's terminal outcome, ordered from clear-success to clear-failure."""

    PASSED = "passed"  # tests green (strong positive)
    COMMITTED = "committed"  # committed, no terminal validation (not truth-labelled)
    OPEN = "open"  # no terminal signal — unknown, not a Tier-2 label
    FAILED = "failed"  # tests red (negative)
    REVERTED = "reverted"  # work undone after committing (negative)


def is_success(outcome: EpisodeOutcome) -> bool | None:
    """Map an outcome to a Tier-2 label: True / False / None (unknown — exclude)."""
    if outcome is EpisodeOutcome.PASSED:
        return True
    if outcome in (EpisodeOutcome.FAILED, EpisodeOutcome.REVERTED):
        return False
    return None  # COMMITTED/OPEN — weak or absent signal; exclude from truth metrics


def classify_outcome(events: Sequence[TrajectoryEvent]) -> EpisodeOutcome:
    """Classify a trajectory span's Tier-2 outcome, deterministically.

    Precedence: a revert after the last commit dominates; else a test explicitly
    marked as terminal validation; else a bare commit; else nothing observed."""
    ordered = sorted(events, key=lambda event: event.timestamp)
    commit_times = [e.timestamp for e in ordered if e.kind is TrajectoryEventKind.COMMIT]
    revert_times = [e.timestamp for e in ordered if e.kind is TrajectoryEventKind.REVERT]
    if revert_times and (not commit_times or max(revert_times) > max(commit_times)):
        return EpisodeOutcome.REVERTED

    test_runs = [
        e
        for e in ordered
        if e.kind is TrajectoryEventKind.TEST_RUN and bool(e.payload.get("terminal", False))
    ]
    if test_runs:
        last = test_runs[-1]
        failed = int(last.payload.get("failures", 0)) + int(last.payload.get("errors", 0))
        return EpisodeOutcome.FAILED if failed > 0 else EpisodeOutcome.PASSED

    return EpisodeOutcome.COMMITTED if commit_times else EpisodeOutcome.OPEN
