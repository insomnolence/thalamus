"""Event sinks — where retrieval events are persisted.

``EventSink`` is the swappable seam; concrete sinks (in-memory for tests, JSONL
for durable append-only logging) sit behind it. Lives in ``instrumentation``
until a second package needs it, then promotes to ``core``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from thalamus.instrumentation._jsonl import append_jsonl
from thalamus.instrumentation.events import RetrievalEvent, serialize_event


@runtime_checkable
class EventSink(Protocol):
    """Persists retrieval events. Implementations must be append-only."""

    def emit(self, event: RetrievalEvent) -> None:
        """Record one retrieval event."""
        ...


class InMemoryEventSink:
    """Collects events in a list. For tests and short-lived analysis."""

    def __init__(self) -> None:
        self.events: list[RetrievalEvent] = []

    def emit(self, event: RetrievalEvent) -> None:
        self.events.append(event)


class JsonlEventSink:
    """Appends one JSON object per line to a file (newline-delimited JSON).

    Append-only and crash-friendly: each event is a self-contained line.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    def emit(self, event: RetrievalEvent) -> None:
        append_jsonl(self._path, serialize_event(event))
