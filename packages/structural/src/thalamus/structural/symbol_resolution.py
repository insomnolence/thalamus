"""File:line → smallest-enclosing code node — the deterministic symbol resolver (§13.19).

The shared backbone of *symbol-level* cross-linking. Given the structural graph's code
nodes (which carry real ``SourceAnchor`` line ranges for symbols), this resolves a source
location ``(file, line)`` to the **smallest enclosing symbol** node — the function/class/method
whose anchor range contains that line — falling back to the **module** node when no finer
symbol encloses it (or no line is known). One source of truth so both consumers resolve
identically:

- **C-7** (``link_by_footprint`` with line-aware footprints): a memory's diff lines → the
  enclosing symbol, not just the touched file's module.
- **C-2** (``link_anchored_nodes``): a non-code node's ``SourceAnchor`` (e.g. a finding at
  ``src/foo.py:42``) → the code symbol it annotates.

Deterministic and tool-exact (§14.2/§14.5): the answer is computed from AST line ranges, never
approximated. A file with no code node simply does not resolve (links are never forced, §13.19).
The resolver only ranges over code-corpus kinds; doc/text/finding nodes are inputs, never targets.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from thalamus.structural.schema import StructuralNode

# Code-corpus kinds the resolver may target (open vocab across the AST + SCIP ingestors).
# ``module`` is the coarse fallback; the rest are the finer symbols we prefer when a line encloses.
_MODULE_KIND = "module"
_SYMBOL_KINDS = frozenset({"interface", "class", "enum", "function", "method"})


def _norm(path: str, root: Path) -> str | None:
    """A node/anchor path normalized to repo-relative POSIX under ``root``; ``None`` if outside.

    Mirrors :func:`thalamus.structural.linking.module_index` so footprint files, finding source
    paths, and node anchors all key identically. Tolerates already-relative paths (a finding's
    reported ``src/foo.py``) by joining them onto ``root`` before resolving."""
    p = Path(path)
    candidate = p if p.is_absolute() else root / p
    try:
        return candidate.resolve().relative_to(root).as_posix()
    except ValueError:
        return None


class SymbolResolver:
    """Resolve ``(file, line)`` to the smallest enclosing code node (symbol, else module).

    Built once over the current graph's code nodes (``module`` + the symbol kinds) and reused
    for every resolution — the per-file symbol lists are sorted so a lookup is a linear scan of
    one file's nodes. Re-derive it when the graph changes (it is a cheap view, never persisted).
    """

    def __init__(self, code_nodes: Iterable[StructuralNode], *, repo_root: Path) -> None:
        root = repo_root.resolve()
        self._root = root
        # Per repo-relative file: the module node (fallback) and the symbol nodes (preferred).
        self._modules: dict[str, StructuralNode] = {}
        self._symbols: dict[str, list[StructuralNode]] = {}
        for node in code_nodes:
            if node.anchor is None:
                continue
            rel = _norm(node.anchor.path, root)
            if rel is None:
                continue
            if node.kind == _MODULE_KIND:
                self._modules.setdefault(rel, node)
            elif node.kind in _SYMBOL_KINDS:
                self._symbols.setdefault(rel, []).append(node)

    def resolve(self, source_path: str, line: int | None) -> StructuralNode | None:
        """The smallest code node enclosing ``source_path:line``; the module if none / no line.

        Returns ``None`` when the file has no code node at all (unmatched → never forced). When
        ``line`` is ``None`` (no line info — e.g. today's file-only footprints) it resolves to the
        module, which is the honest coarse fallback the symbol layer degrades to."""
        rel = _norm(source_path, self._root)
        if rel is None:
            return None
        module = self._modules.get(rel)
        if line is None:
            return module
        enclosing = self._smallest_enclosing(rel, line)
        return enclosing if enclosing is not None else module

    def _smallest_enclosing(self, rel: str, line: int) -> StructuralNode | None:
        """The symbol with the tightest anchor range covering ``line`` (smallest span wins ties)."""
        best: StructuralNode | None = None
        best_span = -1
        for node in self._symbols.get(rel, ()):
            anchor = node.anchor
            if anchor is None or not (anchor.line_start <= line <= anchor.line_end):
                continue
            span = anchor.line_end - anchor.line_start
            if best is None or span < best_span:
                best, best_span = node, span
        return best


__all__ = ["SymbolResolver"]
