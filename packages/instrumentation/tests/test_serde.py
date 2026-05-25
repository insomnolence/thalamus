"""Deserializers are faithful inverses of the serializers, and the JSONL log
readers round-trip through the sinks (the logs are re-loadable for offline eval)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from thalamus.core.types import EventId, MemoryId, RepoId, Scope, SessionId, TenantId
from thalamus.instrumentation import (
    CandidateLog,
    JsonlEventSink,
    JsonlUsageSink,
    RetrievalEvent,
    ShownItem,
    UsageSignal,
    deserialize_event,
    deserialize_usage,
    read_event_log,
    read_usage_log,
    serialize_event,
    serialize_usage,
)

SCOPE = Scope(tenant_id=TenantId("acme"), repo_id=RepoId("widgets"))
NOW = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)

EVENT = RetrievalEvent(
    event_id=EventId("e1"),
    timestamp=NOW,
    scope=SCOPE,
    policy_id="L0",
    cue_text="why sqlite",
    k_requested=2,
    candidates=[
        CandidateLog(MemoryId("a"), {"relevance": 0.5, "recency": 0.25}),
        CandidateLog(MemoryId("b"), {"relevance": 0.0, "recency": 1.0}),
    ],
    shown=[
        ShownItem(MemoryId("a"), rank=0, propensity=1.0),
        ShownItem(MemoryId("b"), rank=1, propensity=1.0),
    ],
    session_id=SessionId("s1"),
    cue_focus="store.py",
    cue_intent="lookup",
    cue_embedding=[0.5, 0.25, 0.125],
)


def test_event_roundtrip_is_exact() -> None:
    assert deserialize_event(serialize_event(EVENT)) == EVENT


def test_event_roundtrip_with_optional_fields_absent() -> None:
    bare = RetrievalEvent(
        event_id=EventId("e2"),
        timestamp=NOW,
        scope=SCOPE,
        policy_id="L0",
        cue_text="x",
        k_requested=1,
        candidates=[],
        shown=[],
    )
    assert deserialize_event(serialize_event(bare)) == bare


def test_usage_roundtrip_is_exact() -> None:
    signal = UsageSignal(EventId("e1"), MemoryId("a"), "overlap", 0.75, True)
    assert deserialize_usage(serialize_usage(signal)) == signal


def test_read_event_log_streams_back_events(tmp_path: Path) -> None:
    sink = JsonlEventSink(tmp_path / "events.jsonl")
    sink.emit(EVENT)
    sink.emit(EVENT)
    events = list(read_event_log(tmp_path / "events.jsonl"))
    assert events == [EVENT, EVENT]


def test_read_usage_log_streams_back_signals(tmp_path: Path) -> None:
    sink = JsonlUsageSink(tmp_path / "usage.jsonl")
    signals = [
        UsageSignal(EventId("e1"), MemoryId("a"), "overlap", 0.9, True),
        UsageSignal(EventId("e1"), MemoryId("b"), "overlap", 0.1, False),
    ]
    for signal in signals:
        sink.emit(signal)
    assert list(read_usage_log(tmp_path / "usage.jsonl")) == signals
