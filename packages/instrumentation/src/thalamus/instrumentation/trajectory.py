"""Episode-trajectory log (logging contract §13.11b).

Out-of-band, actuator-agnostic capture of what actually happened — commits,
edits, reverts, test runs, errors. This is the offline dataset hindsight
relabeling later mines (OLR §13.10); here we define the event schema and sinks.
Observers (git, file watcher, pytest hook) produce these events — see
``git_observer`` for the first, deterministic one.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from thalamus.core.types import EventId, RepoId, Scope, SessionId, TenantId
from thalamus.instrumentation._jsonl import append_jsonl


class TrajectoryEventKind(StrEnum):
    """Kinds of trajectory event. Extended as observers mature."""

    COMMIT = "commit"
    EDIT = "edit"
    REVERT = "revert"
    TEST_RUN = "test_run"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class TrajectoryEvent:
    """One observed trajectory event.

    ``payload`` is kind-specific and must be JSON-serializable (the producing
    observer is responsible for that).
    """

    event_id: EventId
    timestamp: datetime
    scope: Scope
    kind: TrajectoryEventKind
    payload: Mapping[str, Any]
    session_id: SessionId | None = None


def serialize_trajectory_event(event: TrajectoryEvent) -> dict[str, Any]:
    """Convert a :class:`TrajectoryEvent` to a JSON-serializable dict."""
    return {
        "event_id": str(event.event_id),
        "timestamp": event.timestamp.isoformat(),
        "scope": {"tenant_id": str(event.scope.tenant_id), "repo_id": str(event.scope.repo_id)},
        "kind": event.kind.value,
        "session_id": None if event.session_id is None else str(event.session_id),
        "payload": dict(event.payload),
    }


def deserialize_trajectory_event(obj: Mapping[str, Any]) -> TrajectoryEvent:
    """Reconstruct a trajectory event persisted by :func:`serialize_trajectory_event`."""
    session_id = obj.get("session_id")
    return TrajectoryEvent(
        event_id=EventId(str(obj["event_id"])),
        timestamp=datetime.fromisoformat(str(obj["timestamp"])),
        scope=Scope(
            tenant_id=TenantId(str(obj["scope"]["tenant_id"])),
            repo_id=RepoId(str(obj["scope"]["repo_id"])),
        ),
        kind=TrajectoryEventKind(str(obj["kind"])),
        payload=dict(obj["payload"]),
        session_id=None if session_id is None else SessionId(str(session_id)),
    )


def build_test_run_event(
    *,
    event_id: EventId,
    timestamp: datetime,
    scope: Scope,
    tests: int,
    failures: int,
    errors: int,
    skipped: int,
    failed: list[dict[str, str]],
    terminal: bool,
    suite: str = "",
    session_id: SessionId | None = None,
) -> TrajectoryEvent:
    """Canonical TEST_RUN event constructor.

    Shared by the offline :class:`JUnitObserver` and the live pytest plugin so both
    produce *identical* events for ``ingest_episodes`` — one source of truth for the
    TEST_RUN payload schema. ``failed`` is a list of ``{"id", "type", "message"}``
    entries (the per-failure error payloads are the prohibitive-memory signal, §13.10).
    """
    payload: dict[str, Any] = {
        "suite": suite,
        "tests": tests,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
        "failed": failed,
        "terminal": terminal,
    }
    return TrajectoryEvent(
        event_id=event_id,
        timestamp=timestamp,
        scope=scope,
        kind=TrajectoryEventKind.TEST_RUN,
        payload=payload,
        session_id=session_id,
    )


@runtime_checkable
class TrajectorySink(Protocol):
    """Persists trajectory events. Implementations must be append-only."""

    def emit(self, event: TrajectoryEvent) -> None:
        """Record one trajectory event."""
        ...


class InMemoryTrajectorySink:
    """Collects trajectory events in a list. For tests and short-lived analysis."""

    def __init__(self) -> None:
        self.events: list[TrajectoryEvent] = []

    def emit(self, event: TrajectoryEvent) -> None:
        self.events.append(event)


class JsonlTrajectorySink:
    """Appends one JSON object per line (newline-delimited JSON)."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def emit(self, event: TrajectoryEvent) -> None:
        append_jsonl(self._path, serialize_trajectory_event(event))
