from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from thalamus.core.types import (
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
)

SCOPE = Scope(tenant_id=TenantId("acme"), repo_id=RepoId("widgets"))
NOW = datetime(2026, 5, 25, tzinfo=UTC)


def _add(encoder: DeterministicEncoder, store: InMemoryStore, mid: str, content: str) -> None:
    store.add(
        MemoryRecord(MemoryId(mid), Hemisphere.EXPERIENTIAL, "episode", content, SCOPE, NOW),
        encoder.encode([content])[0],
    )


def test_recall_surfaces_linked_code(tmp_path: Path) -> None:
    encoder = DeterministicEncoder(dim=128)
    store = InMemoryStore(dim=128)
    _add(encoder, store, "m_sqlite", "switched the store to sqlite")

    src = tmp_path / "backend.py"
    src.write_text(
        "class SqliteStore:\n    def connect(self):\n        return 1\n", encoding="utf-8"
    )
    graph = InMemoryStructuralGraph(SCOPE)
    graph.add(PythonAstIngestor().ingest_path(src, SCOPE))

    # the first cross-hemisphere link: this memory is about this class
    links = InMemoryCrossLinkIndex()
    record = store.scan(SCOPE)[0]
    node = graph.get(StructuralRef(SCOPE, "class:backend.SqliteStore"))
    assert node is not None
    links.link(record.ref, node.ref)

    gateway = Gateway(
        L0Retriever(encoder, store, now=lambda: NOW),
        k=3,
        graph=graph,
        links=links,
        structural_k_hop=1,
        max_structural_items=1,
    )
    payload = gateway.recall(prompt="why did we move to sqlite", scope=SCOPE)

    assert payload.memories[0].memory_id == MemoryId("m_sqlite")
    struct_ids = {item.node_id for item in payload.structural}
    assert "class:backend.SqliteStore" in struct_ids
    assert "method:backend.SqliteStore.connect" not in struct_ids  # bounded out of the payload
    assert payload.structural_omitted >= 1

    text = payload.render()
    assert "## Related code" in text
    assert "SqliteStore" in text
    assert "additional related item(s) omitted" in text


def test_no_structural_without_graph() -> None:
    encoder = DeterministicEncoder(dim=32)
    store = InMemoryStore(dim=32)
    _add(encoder, store, "m", "hello")
    gateway = Gateway(L0Retriever(encoder, store, now=lambda: NOW))
    payload = gateway.recall(prompt="hello", scope=SCOPE)
    assert list(payload.structural) == []


def test_zero_structural_limit_reports_omitted_context(tmp_path: Path) -> None:
    encoder = DeterministicEncoder(dim=32)
    store = InMemoryStore(dim=32)
    _add(encoder, store, "m", "linked constraint")
    src = tmp_path / "a.py"
    src.write_text("x = 1\n", encoding="utf-8")
    graph = InMemoryStructuralGraph(SCOPE)
    graph.add(PythonAstIngestor().ingest_path(src, SCOPE))
    links = InMemoryCrossLinkIndex()
    links.link(store.scan(SCOPE)[0].ref, StructuralRef(SCOPE, "module:a"))
    gateway = Gateway(
        L0Retriever(encoder, store, now=lambda: NOW),
        graph=graph,
        links=links,
        max_structural_items=0,
    )
    payload = gateway.recall(prompt="linked constraint", scope=SCOPE)
    assert payload.structural == []
    assert payload.structural_omitted == 1
    assert "additional related item(s) omitted" in payload.render()
