"""Tests for the shared file co-change builder helpers (graph-only; the git path is exercised
end-to-end by `impact-eval`)."""

from __future__ import annotations

from pathlib import Path

from thalamus.cli.cochange import code_globs, symbol_file_maps
from thalamus.core.types import RepoId, Scope, TenantId
from thalamus.structural import InMemoryStructuralGraph
from thalamus.structural.schema import IngestResult, SourceAnchor, StructuralNode

S = Scope(TenantId("t"), RepoId("r"))
REPO = Path("/repo")


def _node(node_id: str, kind: str, path: str) -> StructuralNode:
    return StructuralNode(
        node_id=node_id, kind=kind, label=node_id.split(":")[-1], scope=S,
        anchor=SourceAnchor(path=str(REPO / path), line_start=1, line_end=9),
    )


def test_code_globs_by_language() -> None:
    assert code_globs("typescript") == ("*.ts", "*.tsx")
    assert code_globs("python") == ("*.py",)


def test_symbol_file_maps_groups_symbols_by_repo_relative_file() -> None:
    graph = InMemoryStructuralGraph(S)
    graph.add(
        IngestResult(
            nodes=[
                _node("function:a.foo", "function", "a.py"),
                _node("method:a.Bar.m", "method", "a.py"),
                _node("class:b.Baz", "class", "b.py"),
                _node("module:a", "module", "a.py"),  # modules are skipped (not a symbol kind)
            ],
            edges=[],
        )
    )

    ref_file, file_refs = symbol_file_maps(graph, S, REPO)

    # anchors (absolute) are reduced to repo-relative paths matching git diff output
    assert set(file_refs) == {"a.py", "b.py"}
    assert {r.node_id for r in file_refs["a.py"]} == {"function:a.foo", "method:a.Bar.m"}
    assert ref_file == {
        r: ("a.py" if r.node_id.split(":")[1].startswith("a.") else "b.py")
        for r in ref_file
    }
    assert all("module:" not in r.node_id for r in ref_file)  # module node excluded
