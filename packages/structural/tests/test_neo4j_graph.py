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
    Neo4jCrossLinkIndex,
    Neo4jStructuralGraph,
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
