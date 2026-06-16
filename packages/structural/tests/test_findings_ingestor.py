"""Tests for the findings ingestor — SARIF + generic JSON analysis results as Brain-2 nodes."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from thalamus.core.types import RepoId, Scope, TenantId
from thalamus.structural import FindingsIngestor, parse_findings
from thalamus.structural.schema import StructuralNode

S = Scope(TenantId("t"), RepoId("r"))


def _ingest(tmp: Path, content: str) -> list[StructuralNode]:
    path = tmp / "report.sarif"
    path.write_text(content, encoding="utf-8")
    ingestor = FindingsIngestor(files=lambda root: [path], id_namespace="findings")
    return list(ingestor.ingest_path(tmp, S).nodes)


def test_generic_findings_json_becomes_finding_nodes(tmp_path: Path) -> None:
    content = json.dumps(
        {"findings": [
            {"path": "src/foo.ts", "line": 42, "end_line": 45, "rule": "no-any",
             "severity": "warning", "message": "avoid any", "tool": "eslint"}
        ]}
    )
    nodes = _ingest(tmp_path, content)
    assert len(nodes) == 1
    n = nodes[0]
    assert n.kind == "finding"
    assert "no-any" in n.label and "foo.ts:42" in n.label and "avoid any" in n.label
    assert n.metadata["source_path"] == "src/foo.ts"
    assert n.metadata["source_line"] == 42
    assert n.metadata["severity"] == "warning"
    assert "avoid any" in n.metadata["text"] and "eslint" in n.metadata["text"]


def test_sarif_results_become_finding_nodes(tmp_path: Path) -> None:
    sarif = {"runs": [{
        "tool": {"driver": {"name": "codeql"}},
        "results": [{
            "ruleId": "sql-injection", "level": "error",
            "message": {"text": "tainted input reaches a query"},
            "locations": [{"physicalLocation": {
                "artifactLocation": {"uri": "src/db.ts"},
                "region": {"startLine": 10, "endLine": 12},
            }}],
        }],
    }]}
    nodes = _ingest(tmp_path, json.dumps(sarif))
    assert len(nodes) == 1
    n = nodes[0]
    assert n.metadata["source_path"] == "src/db.ts" and n.metadata["source_line"] == 10
    assert n.metadata["rule"] == "sql-injection" and n.metadata["tool"] == "codeql"
    assert "tainted" in n.metadata["text"]


def test_anchor_is_the_findings_file_not_the_source(tmp_path: Path) -> None:
    """Incremental re-embed keys on anchor.path being in the corpus' files — so a finding must
    anchor to the findings file it was read from, not the source line it is about."""
    nodes = _ingest(tmp_path, json.dumps([{"path": "src/a.ts", "line": 1, "message": "x"}]))
    assert nodes[0].anchor is not None
    assert nodes[0].anchor.path.endswith("report.sarif")  # the findings file, not src/a.ts


def test_a_bare_list_is_accepted(tmp_path: Path) -> None:
    nodes = _ingest(tmp_path, json.dumps([{"path": "a.ts", "line": 3, "rule": "r1"}]))
    assert len(nodes) == 1 and nodes[0].metadata["rule"] == "r1"


def test_junk_entries_are_skipped(tmp_path: Path) -> None:
    # no path, or no message AND no rule → not a usable finding
    content = json.dumps([
        {"line": 1, "message": "no path"},
        {"path": "a.ts", "line": 2},  # no message, no rule
        {"path": "a.ts", "line": 3, "message": "kept"},
    ])
    nodes = _ingest(tmp_path, content)
    assert [n.metadata["source_line"] for n in nodes] == [3]


def test_invalid_json_is_skipped_not_fatal(tmp_path: Path) -> None:
    assert _ingest(tmp_path, "{not json") == []


def test_identical_findings_in_one_file_are_deduped(tmp_path: Path) -> None:
    dup = {"path": "a.ts", "line": 5, "rule": "r", "message": "same"}
    nodes = _ingest(tmp_path, json.dumps([dup, dict(dup)]))
    assert len(nodes) == 1


def test_parse_findings_is_pure_over_text() -> None:
    findings: Sequence[object] = parse_findings('[{"path":"a.ts","line":1,"message":"m"}]')
    assert len(findings) == 1
