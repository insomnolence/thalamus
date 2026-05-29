"""Tests for the Markdown document ingestor (thalamus.structural.doc_ingestor)."""

from __future__ import annotations

from pathlib import Path

from thalamus.core.types import RepoId, Scope, TenantId
from thalamus.structural.doc_ingestor import DocIngestor
from thalamus.structural.schema import IngestResult

SCOPE = Scope(TenantId("t"), RepoId("r"))


def _ingest(root: Path) -> IngestResult:
    return DocIngestor().ingest_path(root, SCOPE)


def test_document_and_section_nodes(tmp_path: Path) -> None:
    (tmp_path / "design.md").write_text(
        "# Title\n\nIntro.\n\n## Goals\n\ndurable memory\n", encoding="utf-8"
    )
    result = _ingest(tmp_path)
    by_id = {n.node_id: n for n in result.nodes}
    assert by_id["document:design.md"].kind == "document"
    assert by_id["section:design.md:1"].kind == "section"
    assert by_id["section:design.md:5"].label == "Goals"


def test_section_embeds_its_content(tmp_path: Path) -> None:
    (tmp_path / "d.md").write_text("## Goals\n\nwe want durable memory\n", encoding="utf-8")
    section = next(n for n in _ingest(tmp_path).nodes if n.kind == "section")
    assert "durable memory" in section.metadata["text"]


def test_id_namespace_prefixes_node_ids(tmp_path: Path) -> None:
    # Multiple doc roots sharing a relative path (e.g. two README.md) must not collide in the
    # shared graph — a namespace prefixes the document/section ids.
    (tmp_path / "design.md").write_text("# Title\n\nIntro.\n\n## Goals\n\nx\n", encoding="utf-8")
    ids = {n.node_id for n in DocIngestor(id_namespace="design").ingest_path(tmp_path, SCOPE).nodes}
    assert "document:design:design.md" in ids
    assert "section:design:design.md:5" in ids


def test_heading_hierarchy_is_contains_edges(tmp_path: Path) -> None:
    (tmp_path / "d.md").write_text("# A\n\n## B\n\n### C\n\n## D\n", encoding="utf-8")
    edges = {(e.source_id, e.target_id) for e in _ingest(tmp_path).edges if e.type == "contains"}
    assert ("document:d.md", "section:d.md:1") in edges  # doc contains the top heading
    assert ("section:d.md:1", "section:d.md:3") in edges  # A contains B
    assert ("section:d.md:3", "section:d.md:5") in edges  # B contains C
    assert ("section:d.md:1", "section:d.md:7") in edges  # D dedents back under A


def test_ignores_non_markdown(tmp_path: Path) -> None:
    (tmp_path / "code.py").write_text("x = 1\n", encoding="utf-8")
    assert _ingest(tmp_path).nodes == []
