"""Faithful (de)serialization of :class:`MemoryRecord` to/from JSON-safe dicts.

The inverse pair backup/restore (and any store migration) rely on. Kept in ``core``
because it is dependency-free and shared by the store implementations' callers. Mirrors
the per-type ``serialize_*``/``deserialize_*`` discipline in ``instrumentation`` (faithful
round-trip; the test asserts ``deserialize(serialize(r)) == r``). The actual JSONL I/O lives
with the caller — this only converts a record to/from a plain dict.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from thalamus.core.types import Hemisphere, MemoryId, MemoryRecord, RepoId, Scope, TenantId


def serialize_memory_record(record: MemoryRecord) -> dict[str, Any]:
    """Convert a :class:`MemoryRecord` to a JSON-serializable dict."""
    return {
        "memory_id": str(record.memory_id),
        "hemisphere": record.hemisphere.value,
        "kind": record.kind,
        "content": record.content,
        "tenant_id": str(record.scope.tenant_id),
        "repo_id": str(record.scope.repo_id),
        "created_at": record.created_at.isoformat(),
        "metadata": dict(record.metadata),
    }


def deserialize_memory_record(obj: Mapping[str, Any]) -> MemoryRecord:
    """Reconstruct a record persisted by :func:`serialize_memory_record` (faithful inverse)."""
    return MemoryRecord(
        memory_id=MemoryId(str(obj["memory_id"])),
        hemisphere=Hemisphere(str(obj["hemisphere"])),
        kind=str(obj["kind"]),
        content=str(obj["content"]),
        scope=Scope(TenantId(str(obj["tenant_id"])), RepoId(str(obj["repo_id"]))),
        created_at=datetime.fromisoformat(str(obj["created_at"])),
        metadata=dict(obj["metadata"]),
    )
