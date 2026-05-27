from __future__ import annotations

import json
from pathlib import Path

from thalamus.core.types import EventId, MemoryId
from thalamus.instrumentation import (
    InMemoryUsageSink,
    JsonlUsageSink,
    UsageSignal,
    attribute_overlap,
    serialize_usage,
)


def test_attribute_overlap_detects_use() -> None:
    shown = [
        (MemoryId("m_used"), "use aiosqlite for the async store"),
        (MemoryId("m_unused"), "prefer terse commit messages"),
    ]
    output = "import aiosqlite\nclass AsyncStore: ...  # the async store connects"
    signals = {s.memory_id: s for s in attribute_overlap(EventId("e1"), shown, output)}

    assert signals[MemoryId("m_used")].used is True
    assert signals[MemoryId("m_used")].value > 0.5
    assert signals[MemoryId("m_unused")].used is False
    assert signals[MemoryId("m_unused")].value == 0.0
    assert all(s.event_id == EventId("e1") for s in signals.values())
    # output-overlap is the secondary citation signal, not the deterministic primary
    assert all(s.kind == "citation" for s in signals.values())


def test_in_memory_sink() -> None:
    sink = InMemoryUsageSink()
    sink.emit(UsageSignal(EventId("e"), MemoryId("m"), "overlap", 1.0, True))
    assert len(sink.signals) == 1


def test_jsonl_sink(tmp_path: Path) -> None:
    sink = JsonlUsageSink(tmp_path / "usage.jsonl")
    sink.emit(UsageSignal(EventId("e1"), MemoryId("m1"), "overlap", 0.75, True))
    obj = json.loads((tmp_path / "usage.jsonl").read_text(encoding="utf-8").strip())
    assert obj["event_id"] == "e1"
    assert obj["used"] is True
    assert obj["value"] == 0.75


def test_serialize_is_json_safe() -> None:
    payload = serialize_usage(UsageSignal(EventId("e"), MemoryId("m"), "overlap", 0.5, True))
    assert json.loads(json.dumps(payload))["memory_id"] == "m"
