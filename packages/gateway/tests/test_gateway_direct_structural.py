"""Gateway fusion of direct structural retrieval into the related-code payload section."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from thalamus.core.types import (
    Cue,
    Hemisphere,
    MemoryId,
    MemoryRecord,
    RepoId,
    Scope,
    StructuralRef,
    TenantId,
)
from thalamus.gateway import Gateway
from thalamus.retrieval import L0Retriever
from thalamus.routing import DeterministicEncoder
from thalamus.store import InMemoryStore
from thalamus.structural import (
    InMemoryCrossLinkIndex,
    InMemoryStructuralGraph,
    PythonAstIngestor,
    ScoredNode,
    StructuralNode,
)

SCOPE = Scope(TenantId("acme"), RepoId("widgets"))
NOW = datetime(2026, 5, 26, tzinfo=UTC)


class _StubStructuralRetriever:
    """Returns preset direct hits (top-k), standing in for the real StructuralRetriever."""

    def __init__(self, hits: Sequence[ScoredNode]) -> None:
        self._hits = list(hits)

    def retrieve(self, cue: Cue, k: int) -> list[ScoredNode]:
        return self._hits[: max(k, 0)]


def _add(encoder: DeterministicEncoder, store: InMemoryStore, mid: str, content: str) -> None:
    store.add(
        MemoryRecord(MemoryId(mid), Hemisphere.EXPERIENTIAL, "episode", content, SCOPE, NOW),
        encoder.encode([content])[0],
    )


def _snode(node_id: str, label: str, kind: str = "function") -> StructuralNode:
    return StructuralNode(node_id=node_id, kind=kind, label=label, scope=SCOPE)


def test_direct_hits_surface_without_cross_links() -> None:
    encoder = DeterministicEncoder(dim=32)
    store = InMemoryStore(dim=32)
    _add(encoder, store, "m", "hello world")
    retriever = _StubStructuralRetriever(
        [
            ScoredNode(_snode("function:m.f", "f"), 0.90),
            ScoredNode(_snode("class:m.C", "C"), 0.80),
        ]
    )
    gateway = Gateway(
        L0Retriever(encoder, store, now=lambda: NOW), structural_retriever=retriever
    )
    payload = gateway.recall(prompt="hello", scope=SCOPE)

    assert {item.node_id for item in payload.structural} == {"function:m.f", "class:m.C"}
    assert all(item.relevance is not None for item in payload.structural)
    assert "[relevance 0.90]" in payload.render()


def test_direct_hits_dedup_against_cross_links(tmp_path: Path) -> None:
    encoder = DeterministicEncoder(dim=128)
    store = InMemoryStore(dim=128)
    _add(encoder, store, "m_sqlite", "switched the store to sqlite")
    src = tmp_path / "backend.py"
    src.write_text("class SqliteStore:\n    pass\n", encoding="utf-8")
    graph = InMemoryStructuralGraph(SCOPE)
    graph.add(PythonAstIngestor().ingest_path(src, SCOPE))
    links = InMemoryCrossLinkIndex()
    linked = graph.get(StructuralRef(SCOPE, "class:backend.SqliteStore"))
    assert linked is not None
    links.link(store.scan(SCOPE)[0].ref, linked.ref)

    # direct retrieval returns the already-linked node plus a fresh one
    retriever = _StubStructuralRetriever(
        [ScoredNode(linked, 0.95), ScoredNode(_snode("function:other.g", "g"), 0.70)]
    )
    gateway = Gateway(
        L0Retriever(encoder, store, now=lambda: NOW),
        graph=graph,
        links=links,
        structural_retriever=retriever,
    )
    payload = gateway.recall(prompt="why sqlite", scope=SCOPE)

    linked_items = [i for i in payload.structural if i.node_id == "class:backend.SqliteStore"]
    assert len(linked_items) == 1  # deduped, not surfaced twice
    assert linked_items[0].relevance is None  # kept as the cross-link (listed first), not a hit
    assert any(
        i.node_id == "function:other.g" and i.relevance is not None for i in payload.structural
    )


def test_merged_structural_respects_max_items(tmp_path: Path) -> None:
    encoder = DeterministicEncoder(dim=128)
    store = InMemoryStore(dim=128)
    _add(encoder, store, "m_sqlite", "switched the store to sqlite")
    src = tmp_path / "backend.py"
    src.write_text("class SqliteStore:\n    pass\n", encoding="utf-8")
    graph = InMemoryStructuralGraph(SCOPE)
    graph.add(PythonAstIngestor().ingest_path(src, SCOPE))
    links = InMemoryCrossLinkIndex()
    linked = graph.get(StructuralRef(SCOPE, "class:backend.SqliteStore"))
    assert linked is not None
    links.link(store.scan(SCOPE)[0].ref, linked.ref)

    retriever = _StubStructuralRetriever([ScoredNode(_snode("function:other.g", "g"), 0.70)])
    gateway = Gateway(
        L0Retriever(encoder, store, now=lambda: NOW),
        graph=graph,
        links=links,
        structural_retriever=retriever,
        max_structural_items=1,
    )
    payload = gateway.recall(prompt="why sqlite", scope=SCOPE)

    assert len(payload.structural) == 1  # the cross-link, bounded
    assert payload.structural_omitted == 1  # the direct hit overflowed the bound
