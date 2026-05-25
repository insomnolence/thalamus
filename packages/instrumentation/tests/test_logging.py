from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from thalamus.core.types import (
    Cue,
    EventId,
    Hemisphere,
    MemoryId,
    MemoryRecord,
    RepoId,
    Scope,
    SessionId,
    TenantId,
)
from thalamus.instrumentation import (
    InMemoryEventSink,
    JsonlEventSink,
    LoggingRetriever,
    serialize_event,
)
from thalamus.retrieval import L0Retriever
from thalamus.routing import DeterministicEncoder
from thalamus.store import InMemoryStore

SCOPE = Scope(tenant_id=TenantId("t1"), repo_id=RepoId("r1"))
NOW = datetime(2026, 5, 24, tzinfo=UTC)


def test_event_emitted_and_result_unchanged() -> None:
    encoder = DeterministicEncoder(dim=64)
    store = InMemoryStore(dim=64)
    docs = [("sqlite", "switch the database to sqlite"), ("async", "flaky async teardown")]
    for mid, text in docs:
        store.add(
            MemoryRecord(
                memory_id=MemoryId(mid),
                hemisphere=Hemisphere.EXPERIENTIAL,
                kind="episode",
                content=text,
                scope=SCOPE,
                created_at=NOW,
            ),
            encoder.encode([text])[0],
        )
    inner = L0Retriever(encoder, store, w_recency=0.0, w_importance=0.0, now=lambda: NOW)
    sink = InMemoryEventSink()
    counter = {"n": 0}

    def ids() -> EventId:
        counter["n"] += 1
        return EventId(f"e{counter['n']}")

    logged = LoggingRetriever(inner, sink, policy_id="L0", event_id_factory=ids, now=lambda: NOW)

    cue = Cue(text="why sqlite", scope=SCOPE, session_id=SessionId("s1"))
    result = logged.retrieve(cue, k=1)

    # Result is passed through unchanged.
    assert [s.record.memory_id for s in result.shown] == [MemoryId("sqlite")]
    # Exactly one event, capturing decision-time candidates + shown + propensity.
    assert len(sink.events) == 1
    event = sink.events[0]
    assert event.event_id == EventId("e1")
    assert event.session_id == SessionId("s1")
    assert event.policy_id == "L0"
    assert event.k_requested == 1
    assert {c.memory_id for c in event.candidates} == {MemoryId("sqlite"), MemoryId("async")}
    assert all("relevance" in c.features for c in event.candidates)
    assert [s.memory_id for s in event.shown] == [MemoryId("sqlite")]
    assert event.shown[0].rank == 0
    assert event.shown[0].propensity == 1.0


def test_jsonl_sink_roundtrip(tmp_path: Path) -> None:
    encoder = DeterministicEncoder(dim=16)
    store = InMemoryStore(dim=16)
    store.add(
        MemoryRecord(
            memory_id=MemoryId("m1"),
            hemisphere=Hemisphere.EXPERIENTIAL,
            kind="episode",
            content="only memory",
            scope=SCOPE,
            created_at=NOW,
        ),
        encoder.encode(["only memory"])[0],
    )
    inner = L0Retriever(encoder, store, now=lambda: NOW)
    path = tmp_path / "logs" / "events.jsonl"
    logged = LoggingRetriever(inner, JsonlEventSink(path), policy_id="L0", now=lambda: NOW)

    logged.retrieve(Cue(text="only", scope=SCOPE), k=1)
    logged.retrieve(Cue(text="only again", scope=SCOPE), k=1)

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    record = json.loads(lines[0])
    assert record["policy_id"] == "L0"
    assert record["scope"] == {"tenant_id": "t1", "repo_id": "r1"}
    assert record["shown"][0]["memory_id"] == "m1"
    assert record["shown"][0]["propensity"] == 1.0
    assert "features" in record["candidates"][0]


def test_serialize_event_is_json_safe() -> None:
    sink = InMemoryEventSink()
    encoder = DeterministicEncoder(dim=8)
    store = InMemoryStore(dim=8)
    store.add(
        MemoryRecord(
            memory_id=MemoryId("m1"),
            hemisphere=Hemisphere.EXPERIENTIAL,
            kind="episode",
            content="x",
            scope=SCOPE,
            created_at=NOW,
        ),
        encoder.encode(["x"])[0],
    )
    inner = L0Retriever(encoder, store, now=lambda: NOW)
    logged = LoggingRetriever(inner, sink, policy_id="L0", now=lambda: NOW)
    logged.retrieve(Cue(text="x", scope=SCOPE, embedding=encoder.encode(["x"])[0]), k=1)
    # Round-trips through json without error.
    json.dumps(serialize_event(sink.events[0]))
