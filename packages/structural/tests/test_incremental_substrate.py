from __future__ import annotations

from thalamus.core.types import RepoId, Scope, StructuralRef, TenantId
from thalamus.structural import (
    IngestResult,
    InMemoryFileManifest,
    InMemoryStructuralGraph,
    InMemoryStructuralIndex,
    ManifestEntry,
    SourceAnchor,
    StructuralEdge,
    StructuralNode,
)

SCOPE = Scope(TenantId("t"), RepoId("r"))


def _node(node_id: str, kind: str = "function") -> StructuralNode:
    return StructuralNode(node_id, kind, node_id, SCOPE, SourceAnchor("m.py", 1, 2))


def test_graph_remove_drops_nodes_and_their_edges() -> None:
    graph = InMemoryStructuralGraph(SCOPE)
    graph.add(
        IngestResult(
            nodes=[_node("a"), _node("b"), _node("c")],
            edges=[StructuralEdge("a", "b", "calls"), StructuralEdge("b", "c", "calls")],
        )
    )
    graph.remove([StructuralRef(SCOPE, "b")])
    assert graph.get(StructuralRef(SCOPE, "b")) is None
    assert graph.get(StructuralRef(SCOPE, "a")) is not None
    assert graph.neighbors(StructuralRef(SCOPE, "a"), direction="out") == []  # a->b gone
    assert graph.neighbors(StructuralRef(SCOPE, "c"), direction="in") == []  # b->c gone


def test_index_remove_excludes_from_search() -> None:
    index = InMemoryStructuralIndex(dim=2)
    index.add(_node("a"), [1.0, 0.0])
    index.add(_node("b"), [0.0, 1.0])
    index.remove([StructuralRef(SCOPE, "a")])
    assert [r.node.node_id for r in index.search([1.0, 0.0], k=5, scope=SCOPE)] == ["b"]


def test_in_memory_file_manifest_round_trips_and_replaces() -> None:
    manifest = InMemoryFileManifest()
    assert manifest.load(SCOPE) == {}
    entries = {"m.py": ManifestEntry("sha1", ("function:a", "function:b"))}
    manifest.save(SCOPE, entries)
    assert manifest.load(SCOPE) == entries
    manifest.save(SCOPE, {"n.py": ManifestEntry("sha2", ("function:c",))})  # replaces wholesale
    assert set(manifest.load(SCOPE)) == {"n.py"}
