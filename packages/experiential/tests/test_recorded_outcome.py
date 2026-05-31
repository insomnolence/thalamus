"""Tests for grounded outcome-event extraction (the de-reviewer-ified text input).

Phrases are drawn from real curated memories on the live dollhouse brain (the reviewer dialect)
plus single-agent self-report phrasings — to prove the extractor is author-agnostic and keys on
the *action*, not adjectives, and that real-world false-positive traps ("cleanup", "commits to
the database") do not fire.
"""

from __future__ import annotations

import pytest
from thalamus.experiential.recorded_outcome import RecordedEvent, parse_recorded_outcome


@pytest.mark.parametrize(
    ("text", "sha"),
    [
        ("chunk 2a committed as 7d9b69ca after re-review; addressed should-fix", "7d9b69ca"),
        ("M7 slice-3 committed as 47c2ebf8, bundled with a Sonar-mirror lint cleanup", "47c2ebf8"),
        ("the date-sensitivity fix, committed in 4ee65d50 on the feature branch", "4ee65d50"),
    ],
)
def test_landed_with_sha(text: str, sha: str) -> None:
    outcome = parse_recorded_outcome(text)
    assert outcome is not None
    assert outcome.event is RecordedEvent.LANDED
    assert outcome.sha == sha


@pytest.mark.parametrize(
    "text",
    [
        "M7 slice-2 (security-invalidation readiness boundary) reviewed clean, committing.",
        "M6 chunk 2c (user session metrics) reviewed — clean, no push-back. New seam added.",
    ],
)
def test_landed_clean_review_without_explicit_sha(text: str) -> None:
    outcome = parse_recorded_outcome(text)
    assert outcome is not None
    assert outcome.event is RecordedEvent.LANDED
    assert outcome.sha is None


@pytest.mark.parametrize(
    "text",
    [
        "Reviewed M6 chunk 2b (user session logs). Pushed back (NOT committed).",
        "Reviewer pushback on pre-M7 kernel projection cleanup: must fix problem-body projection",
        "Implemented the SSE slice but it didn't hold, so I reverted it and redid it differently",
        "had to redo the descriptor mount plumbing after it broke the gate",
    ],
)
def test_undone_actions(text: str) -> None:
    outcome = parse_recorded_outcome(text)
    assert outcome is not None
    assert outcome.event is RecordedEvent.UNDONE


def test_landed_dominates_when_committed_after_pushback() -> None:
    # Committed *after* addressing push-back still landed — must not read as undone.
    text = "M6 chunk 2b committed as 7fffd17b after re-review; the dev addressed all push-back"
    outcome = parse_recorded_outcome(text)
    assert outcome is not None
    assert outcome.event is RecordedEvent.LANDED
    assert outcome.sha == "7fffd17b"


@pytest.mark.parametrize(
    "text",
    [
        # Adjective-only opinion — the firewall: never an outcome label on its own.
        "This was a really good idea and the code was well written and elegant.",
        # "cleanup" must not match a clean-review acceptance.
        "M7 doc-only planning cleanup completed; updated the implementation status date.",
        # "commits to" must not match a commit verb.
        "The telemetry service commits to the database on each flush.",
        # A plain decision with no outcome action.
        "We will derive activationProfile from the authoritative assertHostedActivation signal.",
    ],
)
def test_no_grounded_action_returns_none(text: str) -> None:
    assert parse_recorded_outcome(text) is None
