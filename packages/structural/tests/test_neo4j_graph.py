"""Neo4j structural-graph integration tests.

ISOLATION (do not regress): these tests DELETE data, so they require a DISPOSABLE Neo4j via
``THALAMUS_TEST_NEO4J_URI`` — never the dogfood instance ``THALAMUS_NEO4J_URI`` serves.
Cleanup is also scoped to the test tenant (``t``) as a second guard. (A shared instance +
unscoped ``DETACH DELETE`` once destroyed accumulated curated memories.)
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest
from thalamus.core.types import MemoryId, MemoryRef, RepoId, Scope, StructuralRef, TenantId
from thalamus.store import connect
from thalamus.structural import (
    IngestResult,
    InMemoryStructuralIndex,
    Neo4jCrossLinkIndex,
    Neo4jStructuralGraph,
    Neo4jStructuralIndex,
    SourceAnchor,
    StructuralEdge,
    StructuralNode,
)

_URI = os.environ.get("THALAMUS_TEST_NEO4J_URI")
pytestmark = pytest.mark.skipif(
    _URI is None,
    reason="set THALAMUS_TEST_NEO4J_URI (a DISPOSABLE Neo4j, never the dogfood instance)",
)
_TEST_TENANT = "t"
SCOPE = Scope(TenantId(_TEST_TENANT), RepoId("r"))


def _clean(driver: Any) -> None:
    # Scoped to the test tenant only — never a blanket wipe of another tenant's data.
    with driver.session() as session:
        session.run("MATCH (n:SNode {tenant_id: $t}) DETACH DELETE n", t=_TEST_TENANT)
        session.run("MATCH (m:M_experiential {tenant_id: $t}) DETACH DELETE m", t=_TEST_TENANT)


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


def _result() -> IngestResult:
    return IngestResult(
        nodes=[
            StructuralNode("module:m", "module", "m", SCOPE, SourceAnchor("m.py", 1, 10)),
            StructuralNode(
                "class:m.C", "class", "m.C", SCOPE, SourceAnchor("m.py", 2, 8), {"bases": ["B"]}
            ),
            StructuralNode("method:m.C.f", "method", "m.C.f", SCOPE, SourceAnchor("m.py", 3, 5)),
        ],
        edges=[
            StructuralEdge("module:m", "class:m.C", "contains"),
            StructuralEdge("class:m.C", "method:m.C.f", "contains"),
            StructuralEdge("module:m", "module:os", "imports"),  # dangling target -> stub
        ],
    )


def test_add_get_roundtrip(driver: Any) -> None:
    graph = Neo4jStructuralGraph(driver, SCOPE)
    graph.add(_result())

    node = graph.get(StructuralRef(SCOPE, "class:m.C"))
    assert node is not None
    assert node.kind == "class"
    assert node.metadata == {"bases": ["B"]}
    assert node.anchor == SourceAnchor("m.py", 2, 8)
    assert graph.get(StructuralRef(SCOPE, "missing")) is None
    assert graph.get(StructuralRef(SCOPE, "module:os")) is None


def test_neighbors_directed_and_typed(driver: Any) -> None:
    graph = Neo4jStructuralGraph(driver, SCOPE)
    graph.add(_result())

    out = graph.neighbors(StructuralRef(SCOPE, "module:m"), direction="out")
    assert {n.node_id for n in out} == {"class:m.C"}  # os stub excluded
    typed = graph.neighbors(
        StructuralRef(SCOPE, "module:m"), edge_types=["imports"], direction="out"
    )
    assert typed == []  # only edge is to a stub


def test_k_hop_depth_and_type_filter(driver: Any) -> None:
    graph = Neo4jStructuralGraph(driver, SCOPE)
    graph.add(_result())

    one = {n.node_id for n in graph.k_hop(StructuralRef(SCOPE, "module:m"), 1, direction="out")}
    assert one == {"class:m.C"}
    two = {n.node_id for n in graph.k_hop(StructuralRef(SCOPE, "module:m"), 2, direction="out")}
    assert two == {"class:m.C", "method:m.C.f"}
    assert graph.k_hop(StructuralRef(SCOPE, "module:m"), 0, direction="out") == []


def test_native_cross_links(driver: Any) -> None:
    Neo4jStructuralGraph(driver, SCOPE).add(_result())
    with driver.session() as session:
        session.run(
            "MERGE (m:M_experiential {tenant_id: 't', repo_id: 'r', memory_id: 'ep1'})"
        )
    links = Neo4jCrossLinkIndex(driver, SCOPE)

    memory, node = MemoryRef(SCOPE, MemoryId("ep1")), StructuralRef(SCOPE, "module:m")
    links.link(memory, node)
    links.link(memory, node)  # idempotent
    assert links.nodes_for(memory) == [node]
    assert links.memories_for(node) == [memory]

    links.link(MemoryRef(SCOPE, MemoryId("ghost")), node)  # no memory -> no edge
    assert links.memories_for(node) == [memory]


def _fn(node_id: str, label: str) -> StructuralNode:
    return StructuralNode(node_id, "function", label, SCOPE, SourceAnchor("m.py", 1, 2))


def test_structural_index_matches_in_memory(driver: Any) -> None:
    # the persisted index returns the same top-k (order + reconstructed node) as the in-memory one
    nodes = {"function:m.alpha": [1.0, 0.0, 0.0, 0.0], "function:m.beta": [0.0, 1.0, 0.0, 0.0]}
    neo = Neo4jStructuralIndex(driver, SCOPE, dim=4, corpus="code")
    mem = InMemoryStructuralIndex(dim=4)
    for node_id, emb in nodes.items():
        node = _fn(node_id, node_id.split(".")[-1])
        neo.add(node, emb)
        mem.add(node, emb)
    query = [1.0, 0.0, 0.0, 0.0]
    neo_top = [r.node.node_id for r in neo.search(query, k=2, scope=SCOPE)]
    mem_top = [r.node.node_id for r in mem.search(query, k=2, scope=SCOPE)]
    assert neo_top == mem_top == ["function:m.alpha", "function:m.beta"]
    best = neo.search(query, k=1, scope=SCOPE)[0].node
    assert best.kind == "function" and best.anchor == SourceAnchor("m.py", 1, 2)  # faithful node


def test_structural_index_corpus_isolation(driver: Any) -> None:
    code = Neo4jStructuralIndex(driver, SCOPE, dim=4, corpus="code")
    docs = Neo4jStructuralIndex(driver, SCOPE, dim=4, corpus="docs")
    code.add(_fn("function:f", "f"), [1.0, 0.0, 0.0, 0.0])
    docs.add(
        StructuralNode("section:s", "section", "s", SCOPE, SourceAnchor("d.md", 1, 1)),
        [1.0, 0.0, 0.0, 0.0],
    )
    query = [1.0, 0.0, 0.0, 0.0]
    assert [r.node.node_id for r in code.search(query, k=5, scope=SCOPE)] == ["function:f"]
    assert [r.node.node_id for r in docs.search(query, k=5, scope=SCOPE)] == ["section:s"]


def test_graph_remove_detaches_nodes(driver: Any) -> None:
    graph = Neo4jStructuralGraph(driver, SCOPE)
    graph.add(_result())
    graph.remove([StructuralRef(SCOPE, "class:m.C")])
    assert graph.get(StructuralRef(SCOPE, "class:m.C")) is None
    # the contains edge module:m -> class:m.C is gone with the node
    out = graph.neighbors(StructuralRef(SCOPE, "module:m"), direction="out")
    assert out == []  # the only out-edge pointed at the removed class


def test_file_manifest_persists_and_replaces(driver: Any) -> None:
    from thalamus.structural import ManifestEntry, Neo4jFileManifest

    manifest = Neo4jFileManifest(driver, SCOPE)
    assert manifest.load(SCOPE) == {}
    entries = {"m.py": ManifestEntry("sha1", ("module:m", "class:m.C"))}
    manifest.save(SCOPE, entries)
    assert manifest.load(SCOPE) == entries
    manifest.save(SCOPE, {"n.py": ManifestEntry("sha2", ("module:n",))})  # wholesale replace
    assert set(manifest.load(SCOPE)) == {"n.py"}
