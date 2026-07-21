"""Tests for the redaction telemetry log (§17.4 T2 auditable coverage)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from thalamus.core.redaction import RedactionEvent
from thalamus.instrumentation.redaction_log import (
    append_redaction,
    redaction_events_from_metadata,
    summarize_redaction_log,
)

NOW = lambda: datetime(2026, 6, 26, tzinfo=UTC)  # noqa: E731


def test_empty_summary_for_missing_log(tmp_path: Path) -> None:
    summary = summarize_redaction_log(tmp_path / "nope.jsonl")
    assert summary.total == 0
    assert summary.events == 0
    assert summary.by_kind == {}


def test_append_is_noop_for_no_events(tmp_path: Path) -> None:
    path = tmp_path / "redaction.jsonl"
    append_redaction(path, "remember", [], now=NOW)
    assert not path.exists()


def test_append_and_summarize_aggregates_by_kind(tmp_path: Path) -> None:
    path = tmp_path / "redaction.jsonl"
    append_redaction(path, "remember", [RedactionEvent("github-token", 1)], now=NOW)
    append_redaction(
        path,
        "episode",
        [RedactionEvent("github-token", 2), RedactionEvent("aws-access-key", 1)],
        now=NOW,
    )
    summary = summarize_redaction_log(path)
    assert summary.events == 2
    assert summary.total == 4
    assert summary.by_kind == {"aws-access-key": 1, "github-token": 3}


def test_append_merges_duplicate_kinds_in_one_event(tmp_path: Path) -> None:
    path = tmp_path / "redaction.jsonl"
    append_redaction(
        path, "remember", [RedactionEvent("api-key", 1), RedactionEvent("api-key", 1)], now=NOW
    )
    assert summarize_redaction_log(path).by_kind == {"api-key": 2}


def test_events_from_metadata_round_trips_the_stamp() -> None:
    metadata = {"redacted": [{"kind": "jwt", "count": 2}, {"kind": "api-key", "count": 1}]}
    events = redaction_events_from_metadata(metadata)
    assert sorted((e.kind, e.count) for e in events) == [("api-key", 1), ("jwt", 2)]


def test_events_from_metadata_empty_when_absent_or_malformed() -> None:
    assert redaction_events_from_metadata({}) == []
    assert redaction_events_from_metadata({"redacted": "nope"}) == []
