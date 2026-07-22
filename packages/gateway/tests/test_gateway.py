from __future__ import annotations

from datetime import UTC, datetime

from thalamus.core.types import (
    Hemisphere,
    MemoryId,
    MemoryRecord,
    RepoId,
    Scope,
    SessionId,
    TenantId,
)
from thalamus.gateway import Gateway
from thalamus.instrumentation import InMemoryEventSink, LoggingRetriever
from thalamus.retrieval import L0Retriever
from thalamus.routing import DeterministicEncoder
from thalamus.store import InMemoryStore

SCOPE = Scope(tenant_id=TenantId("acme"), repo_id=RepoId("widgets"))
NOW = datetime(2026, 5, 25, tzinfo=UTC)


def _gateway() -> tuple[Gateway, InMemoryEventSink]:
    encoder = DeterministicEncoder(dim=128)
    store = InMemoryStore(dim=128)
    records = [
        ("sqlite", "switched to sqlite; json too slow", "json store too slow at scale"),
        ("async", "the async teardown is flaky in tests", None),
    ]
    for mid, content, why in records:
        metadata = {"why": why} if why is not None else {}
        store.add(
            MemoryRecord(
                MemoryId(mid), Hemisphere.EXPERIENTIAL, "episode", content, SCOPE, NOW, metadata
            ),
            encoder.encode([content])[0],
        )
    sink = InMemoryEventSink()
    retriever = LoggingRetriever(
        L0Retriever(encoder, store, now=lambda: NOW), sink, policy_id="L0", now=lambda: NOW
    )
    return Gateway(retriever, k=2), sink


def test_recall_returns_relevant_payload_and_logs() -> None:
    gateway, sink = _gateway()
    payload = gateway.recall(
        prompt="why did we move to sqlite", scope=SCOPE, session_id=SessionId("s1")
    )

    assert payload.memories[0].memory_id == MemoryId("sqlite")
    assert payload.memories[0].why == "json store too slow at scale"

    text = payload.render()
    assert "sqlite" in text
    assert "why: json store too slow at scale" in text

    # Every query through the gateway is logged (it wraps a LoggingRetriever).
    assert len(sink.events) == 1
    assert sink.events[0].session_id == SessionId("s1")


def test_empty_payload_renders() -> None:
    encoder = DeterministicEncoder(dim=32)
    store = InMemoryStore(dim=32)
    gateway = Gateway(L0Retriever(encoder, store, now=lambda: NOW))
    payload = gateway.recall(prompt="anything", scope=SCOPE)
    assert list(payload.memories) == []
    assert "no relevant memories" in payload.render()


def test_curated_memory_renders_separately_and_is_bounded() -> None:
    encoder = DeterministicEncoder(dim=32)
    store = InMemoryStore(dim=32)
    content = "use the scoped store identity " + ("because it prevents collisions " * 4)
    store.add(
        MemoryRecord(
            MemoryId("decision"),
            Hemisphere.EXPERIENTIAL,
            "decision",
            content,
            SCOPE,
            NOW,
            {"source": "curated", "why": "tenant isolation " * 6, "importance": 1.0},
        ),
        encoder.encode([content])[0],
    )
    gateway = Gateway(
        L0Retriever(encoder, store, now=lambda: NOW), k=1, max_memory_chars=32
    )
    payload = gateway.recall(prompt="scoped store identity", scope=SCOPE)

    assert payload.memories[0].retained is True
    assert len(payload.memories[0].content) <= 32
    assert len(payload.memories[0].why or "") <= 32
    text = payload.render()
    assert "## Retained memory" in text
    assert "## Prior episodes" not in text


def test_gateway_eagerly_encodes_cue_embedding_once() -> None:
    class CountingEncoder:
        def __init__(self, base: DeterministicEncoder) -> None:
            self._base = base
            self.dim = base.dim
            self.count = 0

        def encode(self, texts: list[str]) -> list[list[float]]:
            self.count += len(texts)
            return self._base.encode(texts)

    encoder = CountingEncoder(DeterministicEncoder(dim=32))
    store = InMemoryStore(dim=32)
    l0 = L0Retriever(encoder, store, now=lambda: NOW)
    gateway = Gateway(l0, encoder=encoder, k=2)

    assert encoder.count == 0
    gateway.recall(prompt="test prompt", scope=SCOPE)
    # Encoded exactly once during recall entry, reused across all downstream legs
    assert encoder.count == 1
