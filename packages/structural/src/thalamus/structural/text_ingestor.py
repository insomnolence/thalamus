"""Generic text ingestor — arbitrary plain text as a re-derivable Brain-2 corpus.

:class:`~thalamus.structural.doc_ingestor.DocIngestor` handles Markdown by its heading
structure; this is its sibling for *headingless* arbitrary text (notes, logs, specs,
transcripts, exported chat). Per file → one ``document`` node (whole-file anchor) plus N
``chunk`` nodes, each a contiguous span of lines carrying its body as ``metadata["text"]``
so retrieval embeds meaning rather than a filename. ``contains`` edges hang the chunks off
their document.

Chunking is **line-anchored** (a chunk's id keys on its first line) and **content-aware**
(:func:`chunk_lines` prefers blank-line/paragraph breaks, forces a break at a character
budget, and carries a small overlap so a fact split across a boundary still surfaces).
Zero-dependency and deterministic, like the AST/Doc ingestors. The two ingestors coexist:
Markdown keeps its heading hierarchy via ``DocIngestor``; ``TextIngestor`` is for text that
has no such structure.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from thalamus.core.types import Scope
from thalamus.structural.schema import IngestResult, SourceAnchor, StructuralEdge, StructuralNode
from thalamus.structural.sources import _rel, text_files

logger = logging.getLogger(__name__)

# Default chunk budget matches DocIngestor's section cap so the two ingestors embed comparably
# sized units; a small overlap carries trailing context across a forced break.
DEFAULT_CHUNK_CHARS = 600
DEFAULT_OVERLAP_CHARS = 80
_LABEL_CHARS = 60  # a chunk's display label: its first words, single-lined
_INTRO_CHARS = 600  # the document node's embeddable preview (file head)


def chunk_lines(
    lines: list[str], *, chunk_chars: int, overlap_chars: int
) -> list[tuple[int, int, str]]:
    """Split ``lines`` into overlapping, line-anchored chunks.

    Returns ``(line_start, line_end, text)`` triples with **1-based inclusive** line numbers.
    Greedily fills up to ``chunk_chars`` (counting a newline per line); when the budget is hit
    mid-text it prefers to end on a blank-line (paragraph) boundary, else force-breaks at the
    budget. Each next chunk steps back enough trailing lines to carry ~``overlap_chars`` of
    context. The start always advances by ≥1 line, so an over-long single line becomes its own
    chunk (no infinite loop) and empty input yields no chunks. Pure and deterministic — the
    unit under test, independent of the I/O around it."""
    n = len(lines)
    chunks: list[tuple[int, int, str]] = []
    start = 0  # 0-based index of the current chunk's first line
    while start < n:
        size = 0
        end = start  # 0-based exclusive end as we grow the chunk
        last_para_break = -1  # exclusive end just past a consumed blank line, within budget
        while end < n:
            line_len = len(lines[end]) + 1  # +1 for the newline
            if end > start and size + line_len > chunk_chars:
                break  # budget hit; keep at least one line per chunk
            size += line_len
            end += 1
            if end < n and lines[end - 1].strip() == "":
                last_para_break = end
        # Forced to stop mid-text? Prefer a paragraph boundary over a hard mid-line cut.
        if end < n and last_para_break > start:
            end = last_para_break
        # Trim trailing blank lines from the *content* (the consumed extent still drives advance).
        content_end = end
        while content_end > start and lines[content_end - 1].strip() == "":
            content_end -= 1
        text = "\n".join(lines[start:content_end]).strip()
        if text:
            chunks.append((start + 1, content_end, text))
        if end >= n:
            break
        # Advance with overlap: step back from ``end`` to carry ~overlap_chars, but never to or
        # before ``start`` (guarantees forward progress, so the loop always terminates).
        next_start = end
        carried = 0
        while next_start > start + 1 and carried < overlap_chars:
            carried += len(lines[next_start - 1]) + 1
            next_start -= 1
        start = next_start
    return chunks


class TextIngestor:
    """Ingests plain-text files into one ``document`` node + N ``chunk`` nodes each."""

    def __init__(
        self,
        *,
        files: Callable[[Path], list[Path]] | None = None,
        id_namespace: str | None = None,
        chunk_chars: int = DEFAULT_CHUNK_CHARS,
        overlap_chars: int = DEFAULT_OVERLAP_CHARS,
        max_intro_chars: int = _INTRO_CHARS,
    ) -> None:
        # ``files`` MUST enumerate exactly what this ingestor reads — the producer hands the same
        # enumerator to both the ingestor and the corpus' change detection, so incremental
        # re-embed never drifts from what was parsed (default: the ``.txt`` walker).
        self._files = files if files is not None else text_files
        # Prefixes node ids so two corpora sharing a relative path get distinct ids (no collision).
        self._id_prefix = f"{id_namespace}:" if id_namespace else ""
        self._chunk_chars = chunk_chars
        self._overlap_chars = overlap_chars
        self._max_intro_chars = max_intro_chars

    def ingest_path(self, root: Path, scope: Scope) -> IngestResult:
        nodes: list[StructuralNode] = []
        edges: list[StructuralEdge] = []
        for path in self._files(root):
            self._ingest_file(path, root, scope, nodes, edges)
        return IngestResult(nodes=nodes, edges=edges)

    def _ingest_file(
        self,
        path: Path,
        root: Path,
        scope: Scope,
        nodes: list[StructuralNode],
        edges: list[StructuralEdge],
    ) -> None:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("skipping %s: %s", path, exc)  # binary/unreadable → skip, don't fail
            return

        rel = _rel(path, root)
        doc_id = f"document:{self._id_prefix}{rel}"
        intro = "\n".join(lines).strip()[: self._max_intro_chars]
        nodes.append(
            StructuralNode(
                node_id=doc_id,
                kind="document",
                label=rel,
                scope=scope,
                anchor=SourceAnchor(path=str(path), line_start=1, line_end=max(len(lines), 1)),
                metadata={"text": intro} if intro else {},
            )
        )
        for line_start, line_end, text in chunk_lines(
            lines, chunk_chars=self._chunk_chars, overlap_chars=self._overlap_chars
        ):
            chunk_id = f"chunk:{self._id_prefix}{rel}:{line_start}"
            nodes.append(
                StructuralNode(
                    node_id=chunk_id,
                    kind="chunk",
                    label=" ".join(text.split())[:_LABEL_CHARS],
                    scope=scope,
                    anchor=SourceAnchor(path=str(path), line_start=line_start, line_end=line_end),
                    metadata={"text": text},
                )
            )
            edges.append(StructuralEdge(doc_id, chunk_id, "contains"))
