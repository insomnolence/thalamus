"""Tests for CompositeIngestor (thalamus.structural.composite)."""

from __future__ import annotations

from pathlib import Path

from thalamus.core.types import RepoId, Scope, TenantId
from thalamus.structural.composite import CompositeIngestor
from thalamus.structural.schema import IngestResult, StructuralEdge, StructuralNode

SCOPE = Scope(TenantId("t"), RepoId("r"))


class _StubIngestor:
    def __init__(self, result: IngestResult) -> None:
        self._result = result

    def ingest_path(self, root: Path, scope: Scope) -> IngestResult:
        return self._result


def test_composite_merges_nodes_and_edges() -> None:
    n1 = StructuralNode("module:a", "module", "a", SCOPE)
    n2 = StructuralNode("function:a.f", "function", "a.f", SCOPE)
    structure = IngestResult(
        nodes=[n1, n2], edges=[StructuralEdge("module:a", "function:a.f", "contains")]
    )
    calls = IngestResult(nodes=[], edges=[StructuralEdge("function:a.f", "function:a.g", "calls")])

    result = CompositeIngestor([_StubIngestor(structure), _StubIngestor(calls)]).ingest_path(
        Path("."), SCOPE
    )

    assert {n.node_id for n in result.nodes} == {"module:a", "function:a.f"}
    assert {(e.source_id, e.target_id, e.type) for e in result.edges} == {
        ("module:a", "function:a.f", "contains"),
        ("function:a.f", "function:a.g", "calls"),
    }


def test_composite_of_nothing_is_empty() -> None:
    result = CompositeIngestor([]).ingest_path(Path("."), SCOPE)
    assert list(result.nodes) == []
    assert list(result.edges) == []
