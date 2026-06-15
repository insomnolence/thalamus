"""Tests for the generic text ingestor (thalamus.structural.text_ingestor).

Two layers: the pure ``chunk_lines`` chunker (paragraph/budget/overlap/edge cases) and the
``TextIngestor`` document+chunk graph it produces (kinds, ids, anchors, embeddable text, edges).
"""

from __future__ import annotations

from pathlib import Path

from thalamus.core.types import RepoId, Scope, TenantId
from thalamus.structural.index import node_text
from thalamus.structural.schema import IngestResult
from thalamus.structural.text_ingestor import TextIngestor, chunk_lines

SCOPE = Scope(TenantId("t"), RepoId("r"))


def _ingest(root: Path, **kwargs: object) -> IngestResult:
    return TextIngestor(**kwargs).ingest_path(root, SCOPE)  # type: ignore[arg-type]


# ── the pure chunker ─────────────────────────────────────────────────────────────────────────


def test_chunk_empty_input_yields_nothing() -> None:
    assert chunk_lines([], chunk_chars=600, overlap_chars=80) == []


def test_chunk_prefers_a_paragraph_break_over_a_mid_text_cut() -> None:
    # Budget is hit partway through; the blank line at row 3 is the preferred break point, and
    # its trailing blank is trimmed from the chunk's content + anchor.
    lines = ["aaaa", "bbbb", "", "cccc", "dddd"]
    chunks = chunk_lines(lines, chunk_chars=12, overlap_chars=0)
    assert chunks == [(1, 2, "aaaa\nbbbb"), (4, 5, "cccc\ndddd")]


def test_chunk_force_breaks_at_the_budget_without_a_paragraph() -> None:
    lines = ["aaaa", "bbbb", "cccc", "dddd"]  # no blank lines → break at the char budget
    chunks = chunk_lines(lines, chunk_chars=12, overlap_chars=0)
    assert chunks == [(1, 2, "aaaa\nbbbb"), (3, 4, "cccc\ndddd")]


def test_chunk_carries_overlap_into_the_next_chunk() -> None:
    lines = ["aaaa", "bbbb", "cccc", "dddd"]
    chunks = chunk_lines(lines, chunk_chars=12, overlap_chars=6)
    # Each chunk steps back a line, so consecutive chunks share a boundary line (1-2, 2-3, 3-4).
    assert [(s, e) for s, e, _ in chunks] == [(1, 2), (2, 3), (3, 4)]


def test_chunk_over_long_single_line_becomes_its_own_chunk() -> None:
    # The line blows the budget alone; the chunker still advances (no infinite loop).
    chunks = chunk_lines(["x" * 1000], chunk_chars=100, overlap_chars=10)
    assert chunks == [(1, 1, "x" * 1000)]


# ── the ingestor ─────────────────────────────────────────────────────────────────────────────


def test_document_and_chunk_nodes(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text(
        "First paragraph line one.\nFirst paragraph line two.\n\nSecond paragraph here.\n",
        encoding="utf-8",
    )
    nodes = {n.node_id: n for n in _ingest(tmp_path).nodes}
    doc = nodes["document:note.txt"]
    chunk = nodes["chunk:note.txt:1"]
    assert doc.kind == "document"
    assert chunk.kind == "chunk"
    assert doc.anchor is not None and doc.anchor.line_start == 1 and doc.anchor.line_end == 4
    assert "Second paragraph here." in chunk.metadata["text"]


def test_chunk_text_is_what_gets_embedded(tmp_path: Path) -> None:
    (tmp_path / "n.txt").write_text("a distinctive memorable phrase\n", encoding="utf-8")
    chunk = next(n for n in _ingest(tmp_path).nodes if n.kind == "chunk")
    # node_text embeds metadata["text"] (the chunk body), not the bare label/id.
    assert node_text(chunk) == "a distinctive memorable phrase"


def test_every_node_carries_an_anchor(tmp_path: Path) -> None:
    # incremental_ingest re-embeds only nodes whose anchor.path changed — a node without an
    # anchor would silently never re-embed, so every text node MUST be anchored.
    (tmp_path / "n.txt").write_text("alpha\n\nbeta\n", encoding="utf-8")
    assert all(n.anchor is not None for n in _ingest(tmp_path).nodes)


def test_document_contains_its_chunks(tmp_path: Path) -> None:
    (tmp_path / "n.txt").write_text("alpha beta gamma\n", encoding="utf-8")
    result = _ingest(tmp_path)
    chunk_ids = {n.node_id for n in result.nodes if n.kind == "chunk"}
    contains = {
        e.target_id
        for e in result.edges
        if e.type == "contains" and e.source_id == "document:n.txt"
    }
    assert chunk_ids and contains == chunk_ids


def test_id_namespace_prefixes_ids_so_corpora_dont_collide(tmp_path: Path) -> None:
    # Two corpora ingesting the same relative path get distinct ids via their namespace prefix.
    (tmp_path / "shared.txt").write_text("content\n", encoding="utf-8")
    a = {n.node_id for n in _ingest(tmp_path, id_namespace="alpha").nodes}
    b = {n.node_id for n in _ingest(tmp_path, id_namespace="beta").nodes}
    assert "document:alpha:shared.txt" in a
    assert "document:beta:shared.txt" in b
    assert a.isdisjoint(b)  # no id collision between the two corpora


def test_empty_file_yields_a_document_but_no_chunks(tmp_path: Path) -> None:
    (tmp_path / "empty.txt").write_text("", encoding="utf-8")
    kinds = [n.kind for n in _ingest(tmp_path).nodes]
    assert kinds == ["document"]  # anchored document node, no chunks


def test_binary_file_is_skipped_with_a_warning(tmp_path: Path) -> None:
    (tmp_path / "blob.txt").write_bytes(b"\xff\xfe\x00\x01not utf-8\x80")
    assert _ingest(tmp_path).nodes == []  # unreadable → skipped, not a crash
