from __future__ import annotations

from datetime import UTC, datetime

from thalamus.core.types import (
    EventId,
    Hemisphere,
    MemoryId,
    MemoryRecord,
    RepoId,
    Scope,
    TenantId,
)
from thalamus.gateway import Gateway
from thalamus.instrumentation import InMemoryEventSink, InMemoryUsageSink, LoggingRetriever
from thalamus.retrieval import L0Retriever
from thalamus.routing import DeterministicEncoder
from thalamus.store import InMemoryStore

SCOPE = Scope(tenant_id=TenantId("acme"), repo_id=RepoId("widgets"))
NOW = datetime(2026, 5, 25, tzinfo=UTC)


def _add(encoder: DeterministicEncoder, store: InMemoryStore, mid: str, content: str) -> None:
    store.add(
        MemoryRecord(MemoryId(mid), Hemisphere.EXPERIENTIAL, "episode", content, SCOPE, NOW),
        encoder.encode([content])[0],
    )


def test_recall_then_record_outcome_closes_the_loop() -> None:
    encoder = DeterministicEncoder(dim=128)
    store = InMemoryStore(dim=128)
    _add(encoder, store, "m_sqlite", "use aiosqlite for the async store")
    _add(encoder, store, "m_style", "prefer terse commit messages")

    usage_sink = InMemoryUsageSink()
    retriever = LoggingRetriever(
        L0Retriever(encoder, store, now=lambda: NOW),
        InMemoryEventSink(),
        policy_id="L0",
        now=lambda: NOW,
    )
    gateway = Gateway(retriever, k=2, usage_sink=usage_sink)

    payload = gateway.recall(prompt="how do we do async db work", scope=SCOPE)
    assert payload.event_id is not None  # the event id flows out for correlation

    # the actuator's output uses the sqlite memory's content, not the style memory's
    output = "import aiosqlite\n# the async store now connects"
    signals = gateway.record_outcome(payload, output)

    by_id = {s.memory_id: s for s in signals}
    assert by_id[MemoryId("m_sqlite")].used is True
    assert by_id[MemoryId("m_style")].used is False
    # logged to the usage sink, all keyed by the same retrieval event id
    assert len(usage_sink.signals) == 2
    assert all(s.event_id == payload.event_id for s in usage_sink.signals)


def test_record_outcome_for_records_from_reconstructed_shown() -> None:
    # The durable-fallback path: no live payload, just an event_id + (memory_id, content) pairs
    # rebuilt from the log + store. Proves the citation signal survives a missing payload.
    encoder = DeterministicEncoder(dim=32)
    store = InMemoryStore(dim=32)
    usage_sink = InMemoryUsageSink()
    gateway = Gateway(L0Retriever(encoder, store, now=lambda: NOW), usage_sink=usage_sink)

    event_id = EventId("evt-123")
    shown = [
        (MemoryId("m_sqlite"), "use aiosqlite for the async store"),
        (MemoryId("m_style"), "prefer terse commit messages"),
    ]
    signals = gateway.record_outcome_for(event_id, shown, "import aiosqlite for the async store")

    by_id = {s.memory_id: s for s in signals}
    assert by_id[MemoryId("m_sqlite")].used is True
    assert by_id[MemoryId("m_style")].used is False
    assert len(usage_sink.signals) == 2
    assert all(s.event_id == event_id for s in usage_sink.signals)


def test_record_outcome_noop_without_sink() -> None:
    encoder = DeterministicEncoder(dim=32)
    store = InMemoryStore(dim=32)
    _add(encoder, store, "m", "hello world")
    gateway = Gateway(L0Retriever(encoder, store, now=lambda: NOW))
    payload = gateway.recall(prompt="hello world", scope=SCOPE)
    assert gateway.record_outcome(payload, "hello world") == []
