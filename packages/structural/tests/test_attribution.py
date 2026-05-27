from __future__ import annotations

from pathlib import Path

from thalamus.core.types import EventId, MemoryId, RepoId, Scope, TenantId
from thalamus.structural import (
    FootprintAttributor,
    InMemoryStructuralGraph,
    ShownMemory,
)
from thalamus.structural.schema import IngestResult, SourceAnchor, StructuralEdge, StructuralNode

SCOPE = Scope(TenantId("t"), RepoId("r"))


def _module(name: str, repo: Path) -> StructuralNode:
    # anchors are absolute (as the ingestor records them); module_index normalizes to repo-relative.
    return StructuralNode(
        node_id=f"module:{name}", kind="module", label=name, scope=SCOPE,
        anchor=SourceAnchor(path=str(repo / f"{name}.py"), line_start=1, line_end=1),
    )


def _graph(repo: Path) -> tuple[InMemoryStructuralGraph, list[StructuralNode]]:
    # a imports b; c is unconnected. So k_hop(a) reaches b but not c.
    nodes = [_module("a", repo), _module("b", repo), _module("c", repo)]
    edges = [StructuralEdge("module:a", "module:b", "imports")]
    graph = InMemoryStructuralGraph(SCOPE)
    graph.add(IngestResult(nodes=nodes, edges=edges))
    return graph, nodes


def _attributor(repo: Path, *, k_hop: int = 1) -> FootprintAttributor:
    graph, nodes = _graph(repo)
    return FootprintAttributor(graph, nodes, repo_root=repo, k_hop=k_hop)


def _recall(event: str, memory: str, footprint: list[str]) -> tuple[EventId, list[ShownMemory]]:
    return (EventId(event), [ShownMemory(MemoryId(memory), footprint)])


def test_direct_footprint_overlap_is_used(tmp_path: Path) -> None:
    (result,) = _attributor(tmp_path).attribute([_recall("e1", "m1", ["a.py"])], ["a.py"])
    assert result.used is True
    assert result.connection == "footprint"
    assert result.value == 1.0


def test_khop_overlap_is_used_but_tagged_khop(tmp_path: Path) -> None:
    # work touched a.py; the memory is about b.py, which a imports (1 hop away)
    (result,) = _attributor(tmp_path).attribute([_recall("e1", "m1", ["b.py"])], ["a.py"])
    assert result.used is True
    assert result.connection == "footprint-khop"


def test_unconnected_footprint_is_not_used(tmp_path: Path) -> None:
    # c.py is neither touched nor within a hop of the work
    (result,) = _attributor(tmp_path).attribute([_recall("e1", "m1", ["c.py"])], ["a.py"])
    assert result.used is False
    assert result.connection == "none"
    assert result.value == 0.0


def test_empty_memory_footprint_is_not_used(tmp_path: Path) -> None:
    # code-agnostic memory (no footprint) cannot be attributed by this signal
    (result,) = _attributor(tmp_path).attribute([_recall("e1", "m1", [])], ["a.py"])
    assert result.used is False
    assert result.connection == "none"


def test_no_work_footprint_attributes_nothing(tmp_path: Path) -> None:
    (result,) = _attributor(tmp_path).attribute([_recall("e1", "m1", ["a.py"])], [])
    assert result.used is False


def test_value_is_the_connected_fraction_of_the_footprint(tmp_path: Path) -> None:
    # memory about two files; only a.py connects to the work -> 1/2
    (result,) = _attributor(tmp_path).attribute([_recall("e1", "m1", ["a.py", "c.py"])], ["a.py"])
    assert result.used is True
    assert result.connection == "footprint"  # a direct hit dominates the partial miss
    assert result.value == 0.5


def test_k_hop_zero_disables_neighbourhood_expansion(tmp_path: Path) -> None:
    # with k_hop=0 the b.py memory no longer connects through a's import edge
    (result,) = _attributor(tmp_path, k_hop=0).attribute([_recall("e1", "m1", ["b.py"])], ["a.py"])
    assert result.used is False


def test_attribution_is_deterministic(tmp_path: Path) -> None:
    attributor = _attributor(tmp_path)
    recalls = [_recall("e1", "m1", ["a.py"]), _recall("e2", "m2", ["c.py"])]
    assert attributor.attribute(recalls, ["a.py"]) == attributor.attribute(recalls, ["a.py"])
