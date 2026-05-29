"""Document ingestor — Markdown as a re-derivable Brain-2 corpus.

Brain 2 is structure over a *re-derivable corpus*; code is the first, prose the second
(structural-hemisphere.md: "a document corpus you can re-ingest is the same kind … a new
ingestor, not a new brain"). This parses Markdown into a ``document`` node plus a ``section``
node per heading — the heading hierarchy as ``contains`` edges — and stores each section's
**content** (``metadata["text"]``) so retrieval embeds meaning, not just the heading line.

Zero-dependency and line-based (ATX ``#`` headings), deterministic like the AST ingestor.
Docs live in their own per-corpus vector index (the no-pollution principle); the composition
keeps them separate from code at retrieval time.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from thalamus.core.types import Scope
from thalamus.structural.schema import IngestResult, SourceAnchor, StructuralEdge, StructuralNode
from thalamus.structural.sources import IGNORE_DIRS, markdown_files

logger = logging.getLogger(__name__)

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_DEFAULT_MAX_SECTION_CHARS = 600


class DocIngestor:
    """Ingests Markdown documents into document/section structural nodes."""

    def __init__(
        self,
        *,
        ignore_dirs: frozenset[str] = IGNORE_DIRS,
        max_section_chars: int = _DEFAULT_MAX_SECTION_CHARS,
        id_namespace: str | None = None,
    ) -> None:
        self._ignore_dirs = ignore_dirs
        self._max_section_chars = max_section_chars
        # Prefixes node ids when set, so multiple doc roots (e.g. design docs + project docs)
        # can't collide on a shared relative path (both having a ``README.md``) in the one graph.
        self._id_prefix = f"{id_namespace}:" if id_namespace else ""

    def ingest_path(self, root: Path, scope: Scope) -> IngestResult:
        nodes: list[StructuralNode] = []
        edges: list[StructuralEdge] = []
        for path in markdown_files(root, self._ignore_dirs):
            self._ingest_file(path, root, scope, nodes, edges)
        return IngestResult(nodes=nodes, edges=edges)

    @staticmethod
    def _rel(path: Path, root: Path) -> str:
        if root.is_file():
            return path.name
        try:
            return path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            return path.name

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
            logger.warning("skipping %s: %s", path, exc)
            return

        rel = self._rel(path, root)
        doc_id = f"document:{self._id_prefix}{rel}"
        intro = "\n".join(lines).strip()[: self._max_section_chars]
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

        headings = [
            (lineno, len(m.group(1)), m.group(2).strip())
            for lineno, line in enumerate(lines, start=1)
            if (m := _HEADING.match(line)) is not None
        ]
        stack: list[tuple[int, str]] = []  # (heading level, node id) for containment
        for index, (lineno, level, text) in enumerate(headings):
            end = headings[index + 1][0] - 1 if index + 1 < len(headings) else len(lines)
            body = "\n".join(lines[lineno - 1 : end]).strip()
            section_id = f"section:{self._id_prefix}{rel}:{lineno}"
            nodes.append(
                StructuralNode(
                    node_id=section_id,
                    kind="section",
                    label=text,
                    scope=scope,
                    anchor=SourceAnchor(path=str(path), line_start=lineno, line_end=end),
                    metadata={"text": body[: self._max_section_chars]},
                )
            )
            while stack and stack[-1][0] >= level:
                stack.pop()
            parent = stack[-1][1] if stack else doc_id
            edges.append(StructuralEdge(parent, section_id, "contains"))
            stack.append((level, section_id))
