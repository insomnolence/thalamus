from __future__ import annotations

import json
from datetime import UTC, datetime

from thalamus.core import (
    Hemisphere,
    MemoryId,
    MemoryRecord,
    RepoId,
    Scope,
    TenantId,
    deserialize_memory_record,
    serialize_memory_record,
)

SCOPE = Scope(TenantId("t"), RepoId("r"))


def test_round_trips_faithfully() -> None:
    record = MemoryRecord(
        memory_id=MemoryId("retained:abc"),
        hemisphere=Hemisphere.EXPERIENTIAL,
        kind="gotcha",
        content="a curated memory with structure",
        scope=SCOPE,
        created_at=datetime(2026, 5, 27, 12, 0, tzinfo=UTC),
        metadata={"footprint": ["a.py", "b.py"], "importance": 2.0, "why": "because"},
    )
    assert deserialize_memory_record(serialize_memory_record(record)) == record


def test_empty_metadata_round_trips() -> None:
    record = MemoryRecord(
        MemoryId("m"), Hemisphere.STRUCTURAL, "episode", "c", SCOPE,
        datetime(2026, 5, 27, tzinfo=UTC),
    )
    assert deserialize_memory_record(serialize_memory_record(record)).metadata == {}


def test_serialized_form_is_json_safe() -> None:
    record = MemoryRecord(
        MemoryId("m"), Hemisphere.EXPERIENTIAL, "episode", "c", SCOPE,
        datetime(2026, 5, 27, tzinfo=UTC), metadata={"footprint": ["x.py"]},
    )
    restored = json.loads(json.dumps(serialize_memory_record(record)))
    assert restored["memory_id"] == "m"
    assert restored["hemisphere"] == "experiential"
