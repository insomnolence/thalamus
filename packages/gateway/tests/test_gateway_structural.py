from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from thalamus.core.types import Hemisphere, MemoryId, MemoryRecord, RepoId, Scope, TenantId
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
    graph = InMemoryStructuralGraph()
    graph.add(PythonAstIngestor().ingest_path(src))

    # the first cross-hemisphere link: this memory is about this class
    links = InMemoryCrossLinkIndex()
    links.link(MemoryId("m_sqlite"), "class:backend.SqliteStore")

    gateway = Gateway(
        L0Retriever(encoder, store, now=lambda: NOW),
        k=3,
        graph=graph,
        links=links,
        structural_k_hop=1,
    )
    payload = gateway.recall(prompt="why did we move to sqlite", scope=SCOPE)

    assert payload.memories[0].memory_id == MemoryId("m_sqlite")
    struct_ids = {item.node_id for item in payload.structural}
    assert "class:backend.SqliteStore" in struct_ids
    assert "method:backend.SqliteStore.connect" in struct_ids  # reached via k_hop=1

    text = payload.render()
    assert "## Related code" in text
    assert "SqliteStore" in text


def test_no_structural_without_graph() -> None:
    encoder = DeterministicEncoder(dim=32)
    store = InMemoryStore(dim=32)
    _add(encoder, store, "m", "hello")
    gateway = Gateway(L0Retriever(encoder, store, now=lambda: NOW))
    payload = gateway.recall(prompt="hello", scope=SCOPE)
    assert list(payload.structural) == []
