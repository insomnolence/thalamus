from __future__ import annotations

from thalamus.structural import (
    IngestResult,
    InMemoryStructuralGraph,
    StructuralEdge,
    StructuralNode,
)


def _result() -> IngestResult:
    nodes = [
        StructuralNode("module:m", "module", "m"),
        StructuralNode("class:m.A", "class", "m.A"),
        StructuralNode("method:m.A.f", "method", "m.A.f"),
        StructuralNode("function:m.g", "function", "m.g"),
    ]
    edges = [
        StructuralEdge("module:m", "class:m.A", "contains"),
        StructuralEdge("class:m.A", "method:m.A.f", "contains"),
        StructuralEdge("module:m", "function:m.g", "contains"),
        StructuralEdge("module:m", "module:os", "imports"),  # dangling external target
    ]
    return IngestResult(nodes=nodes, edges=edges)


def test_get_and_neighbors() -> None:
    graph = InMemoryStructuralGraph()
    graph.add(_result())
    node = graph.get("class:m.A")
    assert node is not None
    assert node.label == "m.A"
    assert graph.get("missing") is None
    out = {n.node_id for n in graph.neighbors("module:m", direction="out")}
    assert out == {"class:m.A", "function:m.g"}  # dangling module:os excluded


def test_edge_type_filter() -> None:
    graph = InMemoryStructuralGraph()
    graph.add(_result())
    contains = {
        n.node_id
        for n in graph.neighbors("module:m", edge_types=["contains"], direction="out")
    }
    assert contains == {"class:m.A", "function:m.g"}


def test_k_hop_traversal() -> None:
    graph = InMemoryStructuralGraph()
    graph.add(_result())
    up = {n.node_id for n in graph.k_hop("method:m.A.f", k=2, direction="in")}
    assert up == {"class:m.A", "module:m"}
    one = {n.node_id for n in graph.k_hop("module:m", k=1, direction="out")}
    assert one == {"class:m.A", "function:m.g"}
