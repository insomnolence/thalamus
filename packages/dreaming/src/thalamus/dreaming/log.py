"""The dream log — an append-only record of what each cycle's passes did.

Mirrors the ``instrumentation`` sink pattern (``EventSink``/``TrajectorySink``):
a swappable :class:`DreamLog` protocol with an in-memory sink for tests and a
JSONL sink for durable, crash-friendly logging. Because dreaming writes only
regenerable derived views, this log is also where a *proposer* pass leaves its
suggestions (Commit 3) — a record, not an authority.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from thalamus.core import RepoId, Scope, TenantId
from thalamus.dreaming.base import PassKind, PassReport, PassStatus


@dataclass(frozen=True, slots=True)
class DreamRecord:
    """One logged pass run, scope- and time-stamped for the cycle it ran in."""

    timestamp: datetime
    scope: Scope
    report: PassReport


def serialize_dream_record(record: DreamRecord) -> dict[str, Any]:
    """Convert a :class:`DreamRecord` to a JSON-serialisable dict."""
    report = record.report
    return {
        "timestamp": record.timestamp.isoformat(),
        "scope": {
            "tenant_id": str(record.scope.tenant_id),
            "repo_id": str(record.scope.repo_id),
        },
        "name": report.name,
        "kind": report.kind.value,
        "status": report.status.value,
        "summary": report.summary,
        "details": dict(report.details),
        "error": report.error,
        "duration_seconds": report.duration_seconds,
    }


def deserialize_dream_record(obj: dict[str, Any]) -> DreamRecord:
    """Reconstruct a record persisted by :func:`serialize_dream_record`."""
    return DreamRecord(
        timestamp=datetime.fromisoformat(str(obj["timestamp"])),
        scope=Scope(
            tenant_id=TenantId(str(obj["scope"]["tenant_id"])),
            repo_id=RepoId(str(obj["scope"]["repo_id"])),
        ),
        report=PassReport(
            name=str(obj["name"]),
            kind=PassKind(str(obj["kind"])),
            status=PassStatus(str(obj["status"])),
            summary=str(obj["summary"]),
            details=dict(obj["details"]),
            error=None if obj.get("error") is None else str(obj["error"]),
            duration_seconds=float(obj["duration_seconds"]),
        ),
    )


@runtime_checkable
class DreamLog(Protocol):
    """Persists dream records. Implementations must be append-only."""

    def emit(self, record: DreamRecord) -> None:
        """Record one pass run."""
        ...


class InMemoryDreamLog:
    """Collects records in a list. For tests and short-lived inspection."""

    def __init__(self) -> None:
        self.records: list[DreamRecord] = []

    def emit(self, record: DreamRecord) -> None:
        self.records.append(record)


class JsonlDreamLog:
    """Appends one JSON object per line to a file (newline-delimited JSON).

    Append-only and crash-friendly: each record is a self-contained line.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    def emit(self, record: DreamRecord) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(serialize_dream_record(record), separators=(",", ":")) + "\n")


def read_dream_log(path: Path) -> Iterator[DreamRecord]:
    """Yield each record in ``path`` (the inverse of :class:`JsonlDreamLog`)."""
    if not path.exists():
        return
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if stripped:
                yield deserialize_dream_record(json.loads(stripped))
