"""The dream log round-trips through JSONL faithfully."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from thalamus.core import RepoId, Scope, TenantId
from thalamus.dreaming import (
    DreamRecord,
    JsonlDreamLog,
    PassKind,
    PassReport,
    PassStatus,
    deserialize_dream_record,
    read_dream_log,
    serialize_dream_record,
)


def _record(name: str, status: PassStatus, error: str | None = None) -> DreamRecord:
    return DreamRecord(
        timestamp=datetime(2026, 5, 28, 12, 0, tzinfo=UTC),
        scope=Scope(tenant_id=TenantId("t"), repo_id=RepoId("r")),
        report=PassReport(
            name=name,
            kind=PassKind.ACTOR,
            status=status,
            summary="did a thing",
            details={"count": 3},
            error=error,
            duration_seconds=0.01,
        ),
    )


def test_serialize_deserialize_is_a_faithful_inverse() -> None:
    record = _record("link-resolution", PassStatus.OK)
    assert deserialize_dream_record(serialize_dream_record(record)) == record


def test_failed_record_preserves_the_error() -> None:
    record = _record("boom", PassStatus.FAILED, error="ValueError: kaboom")
    assert deserialize_dream_record(serialize_dream_record(record)) == record


def test_jsonl_log_appends_and_reads_back(tmp_path: Path) -> None:
    path = tmp_path / "dreams" / "dream.jsonl"
    log = JsonlDreamLog(path)
    log.emit(_record("a", PassStatus.OK))
    log.emit(_record("b", PassStatus.SKIPPED))

    records = list(read_dream_log(path))
    assert [r.report.name for r in records] == ["a", "b"]
    assert [r.report.status for r in records] == [PassStatus.OK, PassStatus.SKIPPED]


def test_read_missing_log_is_empty(tmp_path: Path) -> None:
    assert list(read_dream_log(tmp_path / "nope.jsonl")) == []
