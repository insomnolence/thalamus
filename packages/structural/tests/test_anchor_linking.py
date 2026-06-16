from __future__ import annotations

import json
from pathlib import Path

from thalamus.core.types import RepoId, Scope, StructuralRef, TenantId
from thalamus.structural import (
    ANNOTATES,
    FindingsIngestor,
    InMemoryStructuralGraph,
    PythonAstIngestor,
    SourceAnchor,
    StructuralNode,
    default_annotation_location,
    findings_files,
    link_anchored_nodes,
)
from thalamus.structural.graph import StructuralGraph
from thalamus.structural.schema import IngestResult


def _with_annotators(graph: StructuralGraph, annotators: list[StructuralNode]) -> None:
    """Annotators live in the graph (real ingest puts them there); add them so neighbour lookups
    resolve the edge's source node — link_anchored_nodes only writes the ``annotates`` edges."""
    graph.add(IngestResult(nodes=annotators, edges=[]))


SCOPE = Scope(TenantId("t"), RepoId("r"))

_SRC = """\
def login():
    return 1


class Db:
    def query(self):
        return 2
"""


def _code_graph(tmp_path: Path) -> tuple[StructuralGraph, list[StructuralNode]]:
    (tmp_path / "app.py").write_text(_SRC, encoding="utf-8")
    nodes = list(PythonAstIngestor().ingest_path(tmp_path, SCOPE).nodes)
    graph = InMemoryStructuralGraph(SCOPE)
    graph.add(PythonAstIngestor().ingest_path(tmp_path, SCOPE))
    return graph, nodes


def _findings_node(tmp_path: Path, *findings: dict[str, object]) -> list[StructuralNode]:
    findings_path = tmp_path / "scan.json"
    findings_path.write_text(json.dumps({"findings": list(findings)}), encoding="utf-8")
    ingestor = FindingsIngestor(files=findings_files("scan.json"))
    return list(ingestor.ingest_path(tmp_path, SCOPE).nodes)


def test_finding_links_to_enclosing_symbol(tmp_path: Path) -> None:
    graph, code_nodes = _code_graph(tmp_path)
    # A finding about app.py line 7 (inside Db.query).
    findings = _findings_node(
        tmp_path, {"path": "app.py", "line": 7, "rule": "X", "message": "leak"}
    )
    _with_annotators(graph, findings)
    created = link_anchored_nodes(findings, code_nodes, graph, SCOPE, repo_root=tmp_path)
    assert created == 1

    method = StructuralRef(SCOPE, "method:app.Db.query")
    annotators = graph.neighbors(method, edge_types=(ANNOTATES,), direction="in")
    assert [n.kind for n in annotators] == ["finding"]


def test_finding_without_enclosing_symbol_links_to_module(tmp_path: Path) -> None:
    graph, code_nodes = _code_graph(tmp_path)
    # Line 3 is a blank line between symbols → module fallback.
    findings = _findings_node(
        tmp_path, {"path": "app.py", "line": 3, "rule": "X", "message": "blank"}
    )
    _with_annotators(graph, findings)
    created = link_anchored_nodes(findings, code_nodes, graph, SCOPE, repo_root=tmp_path)
    assert created == 1
    module = StructuralRef(SCOPE, "module:app")
    annotators = graph.neighbors(module, edge_types=(ANNOTATES,), direction="in")
    assert [n.kind for n in annotators] == ["finding"]


def test_finding_about_unknown_file_does_not_link(tmp_path: Path) -> None:
    graph, code_nodes = _code_graph(tmp_path)
    findings = _findings_node(
        tmp_path, {"path": "ghost.py", "line": 1, "rule": "X", "message": "nope"}
    )
    created = link_anchored_nodes(findings, code_nodes, graph, SCOPE, repo_root=tmp_path)
    assert created == 0  # links are never forced


def test_idempotent(tmp_path: Path) -> None:
    graph, code_nodes = _code_graph(tmp_path)
    findings = _findings_node(
        tmp_path, {"path": "app.py", "line": 1, "rule": "X", "message": "login leak"}
    )
    _with_annotators(graph, findings)
    link_anchored_nodes(findings, code_nodes, graph, SCOPE, repo_root=tmp_path)
    second = link_anchored_nodes(findings, code_nodes, graph, SCOPE, repo_root=tmp_path)
    # The graph dedups identical edges, so a node sees exactly one annotator after a re-run.
    func = StructuralRef(SCOPE, "function:app.login")
    assert len(graph.neighbors(func, edge_types=(ANNOTATES,), direction="in")) == 1
    assert second == 1  # re-run still reports the edge it would (re-)create


def test_code_node_is_never_an_annotator() -> None:
    code = StructuralNode("function:m.f", "function", "f", SCOPE, SourceAnchor("m.py", 1, 2))
    assert default_annotation_location(code) is None


def test_anchor_fallback_when_no_source_metadata(tmp_path: Path) -> None:
    # A non-finding non-code node with no source_path metadata uses its own anchor as the location.
    graph, code_nodes = _code_graph(tmp_path)
    chunk = StructuralNode(
        node_id="chunk:app.py:1",
        kind="chunk",
        label="excerpt",
        scope=SCOPE,
        anchor=SourceAnchor(path=str(tmp_path / "app.py"), line_start=1, line_end=1),
        metadata={"text": "def login()"},
    )
    _with_annotators(graph, [chunk])
    created = link_anchored_nodes([chunk], code_nodes, graph, SCOPE, repo_root=tmp_path)
    assert created == 1
    func = StructuralRef(SCOPE, "function:app.login")
    annotators = graph.neighbors(func, edge_types=(ANNOTATES,), direction="in")
    assert [n.node_id for n in annotators] == ["chunk:app.py:1"]
