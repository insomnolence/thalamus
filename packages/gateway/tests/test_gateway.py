from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from thalamus.core.types import (
    Cue,
    Hemisphere,
    MemoryId,
    MemoryRecord,
    RepoId,
    Scope,
    SessionId,
    TenantId,
    Vector,
)
from thalamus.gateway import Gateway, StructuralLinkedRetriever, StructuralRelevanceRetriever
from thalamus.instrumentation import InMemoryEventSink, LoggingRetriever
from thalamus.retrieval import L0Retriever
from thalamus.routing import DeterministicEncoder
from thalamus.store import InMemoryStore
from thalamus.structural import (
    InMemoryCrossLinkIndex,
    InMemoryStructuralGraph,
    InMemoryStructuralIndex,
    StructuralNode,
    StructuralRetriever,
)
from thalamus.structural.schema import IngestResult

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


def test_gateway_eagerly_encodes_cue_once_across_all_vector_retrievals() -> None:
    class CountingEncoder:
        def __init__(self, base: DeterministicEncoder) -> None:
            self._base = base
            self.dim = base.dim
            self.count = 0

        def encode(self, texts: Sequence[str]) -> list[Vector]:
            self.count += len(texts)
            return self._base.encode(texts)

    encoder = CountingEncoder(DeterministicEncoder(dim=32))
    store = InMemoryStore(dim=32)
    l0 = L0Retriever(encoder, store, now=lambda: NOW)
    structural = [
        StructuralRetriever(encoder, InMemoryStructuralIndex(dim=32), corpus="code"),
        StructuralRetriever(encoder, InMemoryStructuralIndex(dim=32), corpus="docs"),
    ]
    # Exercise both structural-relevance retrieval inside the memory chain and the Gateway's
    # separate direct structural retrieval. All four structural queries must reuse the same cue.
    base = StructuralRelevanceRetriever(l0, InMemoryCrossLinkIndex(), structural)
    gateway = Gateway(base, encoder=encoder, k=2, structural_retrievers=structural)

    assert encoder.count == 0
    gateway.recall(prompt="test prompt", scope=SCOPE)
    # Encoded exactly once during recall entry, reused by L0 and every structural query.
    assert encoder.count == 1


def test_focused_link_promotion_preserves_l0_score_in_logged_features() -> None:
    encoder = DeterministicEncoder(dim=32)
    store = InMemoryStore(dim=32)
    record = MemoryRecord(
        MemoryId("linked"),
        Hemisphere.EXPERIENTIAL,
        "episode",
        "avoid blocking writes in this adapter",
        SCOPE,
        NOW,
    )
    store.add(record, encoder.encode([record.content])[0])

    graph = InMemoryStructuralGraph(SCOPE)
    node = StructuralNode("module:pkg.store", "module", "pkg.store", SCOPE)
    graph.add(IngestResult(nodes=[node], edges=[]))
    links = InMemoryCrossLinkIndex()
    links.link(record.ref, node.ref)

    cue = Cue(
        text="avoid blocking writes",
        scope=SCOPE,
        focus="pkg/store.py",
        embedding=encoder.encode(["avoid blocking writes"])[0],
    )
    l0 = L0Retriever(
        encoder,
        store,
        w_recency=0.0,
        w_importance=0.0,
        now=lambda: NOW,
    )
    l0_score = l0.retrieve(cue, k=1).candidates[0].score
    sink = InMemoryEventSink()
    focused = StructuralLinkedRetriever(l0, store, graph, links)
    logged = LoggingRetriever(focused, sink, policy_id="L0+structural", now=lambda: NOW)

    result = logged.retrieve(cue, k=1)

    assert result.candidates[0].score == 2.0  # intentional explicit-focus promotion
    event_candidate = sink.events[0].candidates[0]
    assert event_candidate.memory_id == MemoryId("linked")
    assert event_candidate.features["score"] == l0_score
    assert event_candidate.features["structural_link"] == 1.0
