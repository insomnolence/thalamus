"""Tests for the SCIP ingestor over the committed ``ts_sample`` fixture.

Deterministic and offline: reads the pinned ``index.scip`` (no Node, no indexer at test
time). Skipped when the ``scip`` extra (protobuf) is absent.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from thalamus.core.types import RepoId, Scope, TenantId
from thalamus.structural.schema import IngestResult
from thalamus.structural.scip_ingestor import ScipIngestor

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("google.protobuf") is None, reason="requires the scip extra"
)

SCOPE = Scope(TenantId("t"), RepoId("r"))
FIXTURE = Path(__file__).parent / "fixtures" / "ts_sample"


def _ingest() -> IngestResult:
    return ScipIngestor(FIXTURE / "index.scip").ingest_path(FIXTURE, SCOPE)


def test_module_nodes_per_file() -> None:
    nodes = {n.node_id: n for n in _ingest().nodes}
    for stem in ("shapes", "geometry", "circle", "index"):
        mod = f"module:src.{stem}"
        assert mod in nodes
        assert nodes[mod].kind == "module"


def test_kinds_inferred_from_suffix_and_doc() -> None:
    nodes = {n.node_id: n.kind for n in _ingest().nodes}
    assert nodes["interface:src.shapes.Shape"] == "interface"
    assert nodes["enum:src.shapes.Kind"] == "enum"
    assert nodes["class:src.circle.Circle"] == "class"
    assert nodes["function:src.geometry.circleArea"] == "function"
    assert nodes["function:src.index.makeCircle"] == "function"
    assert nodes["method:src.circle.Circle.area"] == "method"
    assert nodes["method:src.shapes.Shape.area"] == "method"


def test_constructor_is_a_method_node() -> None:
    ids = {n.node_id for n in _ingest().nodes}
    assert "method:src.circle.Circle.<constructor>" in ids


def test_terms_and_parameters_are_not_nodes() -> None:
    ids = {n.node_id for n in _ingest().nodes}
    # enum members, properties, and parameters are Term/Parameter descriptors — skipped.
    assert not any(i.endswith(".Round") or i.endswith(".Square") for i in ids)
    assert not any("kind" in i.split(":", 1)[-1].split(".")[-1] for i in ids)
    assert not any("radius" in i for i in ids)


def test_anchors_are_one_based_and_path_resolvable() -> None:
    nodes = {n.node_id: n for n in _ingest().nodes}
    shape = nodes["interface:src.shapes.Shape"]
    assert shape.anchor is not None
    # `export interface Shape` is the 3rd line of shapes.ts (after two comment lines).
    assert shape.anchor.line_start == 3
    # Every anchor path is the corpus-root-joined file and exists on disk.
    for node in nodes.values():
        assert node.anchor is not None
        assert node.anchor.path == str(FIXTURE / Path(node.anchor.path).relative_to(FIXTURE))
        assert Path(node.anchor.path).exists()


def test_embeddable_text_carries_the_signature() -> None:
    nodes = {n.node_id: n for n in _ingest().nodes}
    text = nodes["function:src.geometry.circleArea"].metadata["text"]
    assert "circleArea" in text
    assert "number" in text  # the signature from `documentation`


def test_wrong_root_raises() -> None:
    from thalamus.core.exceptions import ThalamusError

    with pytest.raises(ThalamusError):
        ScipIngestor(FIXTURE / "index.scip").ingest_path(Path("/nonexistent/root"), SCOPE)


def _edges() -> set[tuple[str, str, str]]:
    return {(e.source_id, e.target_id, e.type) for e in _ingest().edges}


def test_cross_file_call_edge() -> None:
    # Circle.area() calls the cross-file circleArea() — resolved via the enclosing range.
    assert (
        "method:src.circle.Circle.area",
        "function:src.geometry.circleArea",
        "calls",
    ) in _edges()


def test_constructor_call_edge() -> None:
    # makeCircle() calls the Circle constructor cross-file.
    assert (
        "function:src.index.makeCircle",
        "method:src.circle.Circle.<constructor>",
        "calls",
    ) in _edges()


def test_implements_edge() -> None:
    assert (
        "class:src.circle.Circle",
        "interface:src.shapes.Shape",
        "implements",
    ) in _edges()


def test_contains_edges() -> None:
    edges = _edges()
    assert ("module:src.circle", "class:src.circle.Circle", "contains") in edges
    assert ("class:src.circle.Circle", "method:src.circle.Circle.area", "contains") in edges
    assert ("module:src.geometry", "function:src.geometry.circleArea", "contains") in edges


def test_no_call_edge_for_imports_or_type_positions() -> None:
    # The import of circleArea (module-level) and the `: Circle` return type must NOT
    # produce call edges — only references inside a callable body do.
    calls = {(s, t) for s, t, k in _edges() if k == "calls"}
    assert ("module:src.circle", "function:src.geometry.circleArea") not in calls
    # No call edge targets the Circle class itself (a type, not callable).
    assert not any(t == "class:src.circle.Circle" for _, t in calls)


def test_no_self_calls() -> None:
    assert not any(s == t for s, t, k in _edges() if k == "calls")


def test_anchor_paths_match_the_typescript_enumerator() -> None:
    # The byte-equality contract that incremental_ingest depends on: every node's
    # anchor.path must be exactly a path the corpus file-enumerator yields, or the
    # manifest's file<->node attribution (and re-embed/removal) silently breaks.
    from thalamus.structural.sources import typescript_files

    enumerated = {str(p) for p in typescript_files(FIXTURE)}
    anchored = {n.anchor.path for n in _ingest().nodes if n.anchor is not None}
    assert anchored <= enumerated
    assert anchored == enumerated  # every source file produced at least a module node
