"""Regression: cross-hemisphere links must survive a mid-serve Brain-2 re-derive (Neo4j).

This bug class is **invisible on the in-memory backend**, which is why 786 unit tests missed it
for months. The two ``StructuralGraph`` implementations diverge on ``remove``:

* ``Neo4jStructuralGraph.remove`` is ``DETACH DELETE`` — it deletes the node *and every
  relationship on it*, and ``Neo4jCrossLinkIndex`` stores links as native
  ``(memory)-[:TOUCHES]->(SNode)`` edges. So a rebuild destroys them.
* ``InMemoryStructuralGraph.remove`` pops from its own adjacency only; ``InMemoryCrossLinkIndex``
  is a separate object and loses nothing.

``incremental_ingest`` drops and re-MERGEs a changed file's nodes every re-derive, so on Neo4j
every source edit silently severed that file's memories from the code graph — draining structural
recall and the L-R2 centrality rung — and ``StructuralRefreshPass``'s already-linked cache made
the loss permanent for the life of the process. Observed live: 35 of 35 centrality-weight drops in
the dogfood dream log were immediately preceded by a real rebuild, one shedding 183 memories.

These tests therefore run against a real driver. They are cross-package (structural + dreaming +
store) and backend-bound, hence integration rather than a package unit test.

ISOLATION (do not regress): they DELETE data, so they require a DISPOSABLE Neo4j via
``THALAMUS_TEST_NEO4J_URI`` — never the dogfood instance ``THALAMUS_NEO4J_URI`` serves. Cleanup is
scoped to the test tenant as a second guard.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from thalamus.core.types import (
    Hemisphere,
    MemoryId,
    MemoryRecord,
    MemoryRef,
    RepoId,
    Scope,
    TenantId,
)
from thalamus.dreaming import PassContext, StructuralRederivePass, StructuralRefreshPass
from thalamus.routing import DeterministicEncoder
from thalamus.store import connect
from thalamus.structural import (
    CorpusSpec,
    InMemoryFileManifest,
    InMemoryStructuralIndex,
    Neo4jCrossLinkIndex,
    Neo4jStructuralGraph,
    PythonAstIngestor,
    python_files,
)

_URI = os.environ.get("THALAMUS_TEST_NEO4J_URI")
pytestmark = pytest.mark.skipif(
    _URI is None,
    reason="set THALAMUS_TEST_NEO4J_URI (a DISPOSABLE Neo4j, never the dogfood instance)",
)
_TEST_TENANT = "t_relink"
SCOPE = Scope(TenantId(_TEST_TENANT), RepoId("r"))
NOW = datetime(2026, 9, 21, tzinfo=UTC)
MEMORY = MemoryRef(SCOPE, MemoryId("ep1"))


def _clean(driver: Any) -> None:
    # Scoped to this test's own tenant only — never a blanket wipe of another tenant's data.
    with driver.session() as session:
        session.run("MATCH (n:SNode {tenant_id: $t}) DETACH DELETE n", t=_TEST_TENANT)
        session.run("MATCH (m:M_experiential {tenant_id: $t}) DETACH DELETE m", t=_TEST_TENANT)
        session.run("MATCH (f:FileManifest {tenant_id: $t}) DETACH DELETE f", t=_TEST_TENANT)


@pytest.fixture
def driver() -> Iterator[Any]:
    handle = connect(
        os.environ["THALAMUS_TEST_NEO4J_URI"],
        os.environ.get("THALAMUS_TEST_NEO4J_USER", "neo4j"),
        os.environ.get("THALAMUS_TEST_NEO4J_PASSWORD", ""),
    )
    _clean(handle)
    try:
        yield handle
    finally:
        _clean(handle)
        handle.close()


class _Brain1:
    """The only ``Store`` surface ``StructuralRefreshPass`` uses — one episode with a footprint."""

    def __init__(self, footprint: str) -> None:
        self._records = [
            MemoryRecord(
                MemoryId("ep1"), Hemisphere.EXPERIENTIAL, "episode", "why we did it", SCOPE, NOW,
                metadata={"footprint": [footprint]},
            )
        ]

    def scan(self, scope: Scope) -> list[MemoryRecord]:
        return list(self._records) if scope == SCOPE else []


def _memory_node(driver: Any) -> None:
    with driver.session() as session:
        session.run(
            "MERGE (m:M_experiential {tenant_id: $t, repo_id: 'r', memory_id: 'ep1'})",
            t=_TEST_TENANT,
        )


def _build(
    driver: Any,
) -> tuple[Neo4jStructuralGraph, StructuralRederivePass, Neo4jCrossLinkIndex]:
    """A Brain 2 on the real backend: the durable graph, the re-derive, and the link index."""
    graph = Neo4jStructuralGraph(driver, SCOPE)
    corpora = [
        CorpusSpec(PythonAstIngestor(), InMemoryStructuralIndex(dim=32), python_files, "code")
    ]
    rederive = StructuralRederivePass(
        corpora, graph, InMemoryFileManifest(), DeterministicEncoder(dim=32)
    )
    return graph, rederive, Neo4jCrossLinkIndex(driver, SCOPE)


def _ctx(repo: Path, store: Any) -> PassContext:
    return PassContext(scope=SCOPE, now=NOW, store=store, repo_root=str(repo))


def test_rebuild_destroys_cross_links_on_neo4j(driver: Any, tmp_path: Path) -> None:
    """Pins the root cause, so a future backend change that fixes it is noticed here first."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    graph, rederive, links = _build(driver)
    store = _Brain1("mod.py")
    _memory_node(driver)

    rederive.run(_ctx(repo, store))
    StructuralRefreshPass(graph, links).run(_ctx(repo, store))
    assert [node.node_id for node in links.nodes_for(MEMORY)] == ["module:mod"]

    # Edit the file -> the next re-derive drops and re-MERGEs its nodes.
    (repo / "mod.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    rederive.run(_ctx(repo, store))

    # The node is back under its canonical id...
    assert [node.node_id for node in graph.nodes_of_kind(SCOPE, "module")] == ["module:mod"]
    # ...but its TOUCHES edge went with the DETACH DELETE. This is the bug, pinned.
    assert links.nodes_for(MEMORY) == []


def test_refresh_repairs_the_links_a_rebuild_destroyed(driver: Any, tmp_path: Path) -> None:
    """The fix: the re-derive publishes what it rebuilt and the re-link repairs it next cycle."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    graph, rederive, links = _build(driver)
    store = _Brain1("mod.py")
    _memory_node(driver)
    refresh = StructuralRefreshPass(graph, links, relink=rederive.relink)

    rederive.run(_ctx(repo, store))
    refresh.run(_ctx(repo, store))
    assert [node.node_id for node in links.nodes_for(MEMORY)] == ["module:mod"]

    # A full cycle over a changed file: re-derive (destroys the edge), then re-link (repairs it).
    (repo / "mod.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    rederive.run(_ctx(repo, store))
    outcome = refresh.run(_ctx(repo, store))

    assert outcome.details["repaired"] == 1
    assert [node.node_id for node in links.nodes_for(MEMORY)] == ["module:mod"]


def test_links_survive_repeated_rebuilds(driver: Any, tmp_path: Path) -> None:
    """The live symptom was a monotonic decay over a long serve, so one repair is not enough."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("def f():\n    return 0\n", encoding="utf-8")
    graph, rederive, links = _build(driver)
    store = _Brain1("mod.py")
    _memory_node(driver)
    refresh = StructuralRefreshPass(graph, links, relink=rederive.relink)

    for i in range(1, 5):
        (repo / "mod.py").write_text(f"def f():\n    return {i}\n", encoding="utf-8")
        rederive.run(_ctx(repo, store))
        refresh.run(_ctx(repo, store))
        assert [node.node_id for node in links.nodes_for(MEMORY)] == ["module:mod"], (
            f"link lost on rebuild {i}"
        )


def test_an_unchanged_file_does_no_relink_work(driver: Any, tmp_path: Path) -> None:
    """The repair must not cost the cache's purpose: a quiet tick still re-links nothing."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    graph, rederive, links = _build(driver)
    store = _Brain1("mod.py")
    _memory_node(driver)
    refresh = StructuralRefreshPass(graph, links, relink=rederive.relink)

    rederive.run(_ctx(repo, store))
    refresh.run(_ctx(repo, store))

    rederive.run(_ctx(repo, store))  # hash-gated no-op
    outcome = refresh.run(_ctx(repo, store))
    assert outcome.details["repaired"] == 0
    assert outcome.details["new_memories"] == 0
