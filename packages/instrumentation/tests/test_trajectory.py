from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from thalamus.core.types import EventId, RepoId, Scope, SessionId, TenantId
from thalamus.instrumentation import (
    InMemoryTrajectorySink,
    JsonlTrajectorySink,
    TrajectoryEvent,
    TrajectoryEventKind,
    serialize_trajectory_event,
)

SCOPE = Scope(tenant_id=TenantId("t1"), repo_id=RepoId("r1"))


def _event() -> TrajectoryEvent:
    return TrajectoryEvent(
        event_id=EventId("e1"),
        timestamp=datetime(2026, 5, 24, tzinfo=UTC),
        scope=SCOPE,
        kind=TrajectoryEventKind.COMMIT,
        payload={"sha": "abc", "files": ["a.py"]},
        session_id=SessionId("s1"),
    )


def test_in_memory_sink_collects() -> None:
    sink = InMemoryTrajectorySink()
    sink.emit(_event())
    assert len(sink.events) == 1
    assert sink.events[0].kind is TrajectoryEventKind.COMMIT


def test_serialize_is_json_safe() -> None:
    payload = serialize_trajectory_event(_event())
    assert json.loads(json.dumps(payload))["kind"] == "commit"
    assert payload["payload"]["files"] == ["a.py"]
    assert payload["session_id"] == "s1"


def test_jsonl_sink_appends(tmp_path: Path) -> None:
    path = tmp_path / "logs" / "trajectory.jsonl"
    sink = JsonlTrajectorySink(path)
    sink.emit(_event())
    sink.emit(_event())
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["scope"]["repo_id"] == "r1"
