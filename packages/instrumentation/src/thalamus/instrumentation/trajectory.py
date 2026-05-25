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

from thalamus.core.types import EventId, Scope, SessionId
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
