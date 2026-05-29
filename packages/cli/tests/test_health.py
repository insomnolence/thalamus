"""Tests for the `thalamus health` one-screen brain view."""

from __future__ import annotations

from pathlib import Path

import pytest
from thalamus.cli.health import HealthConfig, run_health


def test_health_on_empty_brain_warns_about_no_negative_signal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # No logs at all — health must not crash, and must flag the missing negative signal.
    run_health(HealthConfig(repo=tmp_path, k=5))
    out = capsys.readouterr().out
    assert "Thalamus health" in out
    assert "Tier-1 utility@5" in out
    assert "no negative outcomes captured" in out  # the discrimination warning


def test_health_counts_reverts_and_red_runs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from datetime import UTC, datetime

    from thalamus.core.types import EventId, RepoId, Scope, TenantId
    from thalamus.instrumentation import JsonlTrajectorySink, TrajectoryEvent, TrajectoryEventKind

    scope = Scope(TenantId("t"), RepoId("r"))
    now = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)
    sink = JsonlTrajectorySink(tmp_path / ".thalamus" / "logs" / "trajectory.jsonl")
    # A revert + a red terminal test run → counted, the negative-signal warning suppressed.
    sink.emit(TrajectoryEvent(EventId("e1"), now, scope, TrajectoryEventKind.REVERT, {}))
    sink.emit(
        TrajectoryEvent(
            EventId("e2"), now, scope, TrajectoryEventKind.TEST_RUN,
            {"tests": 3, "failures": 1, "errors": 0, "failed": [], "terminal": True},
        )
    )
    run_health(HealthConfig(repo=tmp_path, k=5))
    out = capsys.readouterr().out
    assert "reverts=1" in out
    assert "red=1" in out
    assert "no negative outcomes captured" not in out
