"""Tests for CLI co-change index builder and bulk git file fetching."""

from __future__ import annotations

from pathlib import Path

from thalamus.cli.cochange import build_file_cochange, bulk_changed_files_by_sha
from thalamus.core.types import RepoId, Scope, TenantId
from thalamus.structural import IngestResult, InMemoryStructuralGraph, SourceAnchor, StructuralNode

SCOPE = Scope(TenantId("t"), RepoId("r"))


def test_bulk_changed_files_by_sha(tmp_path: Path) -> None:
    # Test bulk_changed_files_by_sha with empty shas
    res = bulk_changed_files_by_sha(tmp_path, [])
    assert res == {}


def test_build_file_cochange_empty_shas(tmp_path: Path) -> None:
    graph = InMemoryStructuralGraph(SCOPE)
    index = build_file_cochange(tmp_path, graph, SCOPE, [])
    assert len(index) == 0


def test_build_file_cochange_with_graph_refs(tmp_path: Path) -> None:
    graph = InMemoryStructuralGraph(SCOPE)
    node_a = StructuralNode("func:a", "function", "a", SCOPE, SourceAnchor("foo.py", 1, 10))
    node_b = StructuralNode("func:b", "function", "b", SCOPE, SourceAnchor("bar.py", 1, 10))
    graph.add(IngestResult(nodes=[node_a, node_b], edges=[]))

    # Passing non-existent shas returns empty index without crashing
    dummy_sha = "0" * 40
    index = build_file_cochange(tmp_path, graph, SCOPE, [dummy_sha])
    assert len(index) == 0
