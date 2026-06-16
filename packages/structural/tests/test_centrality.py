"""Tests for memory_centrality / node_degree — the deterministic L-R2 structural-centrality signal.

Firewall check (§14.2/§14.3): every weight here is derived purely from the cross-link topology +
graph degree — no node/memory text or embedding is consulted anywhere in the computation.
"""

from __future__ import annotations

from thalamus.core.types import MemoryId, MemoryRef, RepoId, Scope, StructuralRef, TenantId
from thalamus.structural import (
    IngestResult,
    InMemoryCrossLinkIndex,
    InMemoryStructuralGraph,
    StructuralEdge,
    StructuralNode,
    memory_centrality,
    node_degree,
)

SCOPE = Scope(tenant_id=TenantId("t1"), repo_id=RepoId("r1"))


def _node(node_id: str) -> StructuralNode:
    return StructuralNode(node_id=node_id, kind="function", label=node_id, scope=SCOPE)


def _mem(mid: str) -> MemoryRef:
    return MemoryRef(scope=SCOPE, memory_id=MemoryId(mid))


def _node_ref(node_id: str) -> StructuralRef:
    return StructuralRef(scope=SCOPE, node_id=node_id)


def _graph() -> InMemoryStructuralGraph:
    # A small graph: `hub` is central (calls a, b, c — degree 3); `leaf` calls nothing (degree 0).
    graph = InMemoryStructuralGraph(SCOPE)
    graph.add(
        IngestResult(
            nodes=[_node("hub"), _node("a"), _node("b"), _node("c"), _node("leaf")],
            edges=[
                StructuralEdge("hub", "a", "calls"),
                StructuralEdge("hub", "b", "calls"),
                StructuralEdge("hub", "c", "calls"),
            ],
        )
    )
    return graph


def test_node_degree_counts_neighbours_both_directions() -> None:
    graph = _graph()
    assert node_degree(graph, _node_ref("hub")) == 3  # out: a, b, c
    assert node_degree(graph, _node_ref("a")) == 1  # in: hub
    assert node_degree(graph, _node_ref("leaf")) == 0  # isolated


def test_centrality_rewards_linking_to_a_high_degree_hub() -> None:
    graph = _graph()
    links = InMemoryCrossLinkIndex()
    links.link(_mem("M_hub"), _node_ref("hub"))  # linked to the central hub
    links.link(_mem("M_leaf"), _node_ref("leaf"))  # linked to an isolated leaf
    weights = memory_centrality([_mem("M_hub"), _mem("M_leaf")], graph, links)
    assert weights[_mem("M_hub")] == 1.0 + 3.0  # 1 (breadth) + degree 3
    assert weights[_mem("M_leaf")] == 1.0 + 0.0  # 1 (breadth) + degree 0
    assert weights[_mem("M_hub")] > weights[_mem("M_leaf")]  # depth (hub) outweighs a leaf


def test_centrality_rewards_breadth_of_links() -> None:
    graph = _graph()
    links = InMemoryCrossLinkIndex()
    links.link(_mem("M_broad"), _node_ref("a"))  # degree 1
    links.link(_mem("M_broad"), _node_ref("b"))  # degree 1
    links.link(_mem("M_narrow"), _node_ref("a"))  # degree 1
    weights = memory_centrality([_mem("M_broad"), _mem("M_narrow")], graph, links)
    assert weights[_mem("M_broad")] == (1.0 + 1.0) + (1.0 + 1.0)  # two nodes, each 1+degree 1
    assert weights[_mem("M_narrow")] == 1.0 + 1.0
    assert weights[_mem("M_broad")] > weights[_mem("M_narrow")]  # more links → more central


def test_unlinked_memory_is_absent_from_the_weights() -> None:
    graph = _graph()
    links = InMemoryCrossLinkIndex()
    links.link(_mem("M_hub"), _node_ref("hub"))
    weights = memory_centrality([_mem("M_hub"), _mem("M_unlinked")], graph, links)
    assert _mem("M_unlinked") not in weights  # no links → weight 0 → relevance-only downstream
    assert _mem("M_hub") in weights


def test_stale_link_contributes_only_the_breadth_term() -> None:
    # A link to a node no longer in the graph (the §13.18-D2 stale case): degree 0, so only the
    # `1` breadth term — never a phantom hub weight.
    graph = _graph()
    links = InMemoryCrossLinkIndex()
    links.link(_mem("M_stale"), _node_ref("gone"))  # node not in the graph
    weights = memory_centrality([_mem("M_stale")], graph, links)
    assert weights[_mem("M_stale")] == 1.0
