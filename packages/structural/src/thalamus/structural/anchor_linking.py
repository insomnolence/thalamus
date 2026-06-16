"""Anchor non-code nodes to the code they annotate (§13.19, C-2).

Non-code corpora — findings, docs, text — are directly retrievable but, until now, *islanded*:
a finding "SQL injection at ``src/db.py:42``" surfaces only when a cue semantically matches it,
never when you ask about ``db.py`` itself. C-2 closes that gap **deterministically**: a non-code
node that carries a source location (the code it is *about*) gets an ``annotates`` edge to the
code node living there, resolved by :class:`~thalamus.structural.symbol_resolution.SymbolResolver`
(smallest enclosing symbol, else module). The edge lets the gateway fuse the finding/doc into the
code node's context ("what the brain already knows about this code"), the way an episode cross-link
fuses the *why*.

**Where the annotated location comes from** (a small extractor, not a guess):
- a **finding** node carries ``metadata["source_path"]`` (+ ``source_line``) — the code file:line
  the tool reported, distinct from the node's own anchor (which points at the *findings file* so
  incremental re-embed fires correctly, see ``findings_ingestor``).
- any other node falls back to its ``SourceAnchor`` — correct when the anchor *is* the annotated
  location (e.g. a text chunk over a source file); a ``.md`` doc anchored to itself resolves to no
  code node and simply does not link (links are never forced, §13.19).

Edges are added to the same ``StructuralGraph`` recall already traverses, so no new index and no
new protocol — and ``contains``-style k-hop spreading reaches them. Idempotent: ``graph.add``
dedups identical edges (Neo4j ``MERGE`` / in-memory de-dup), so re-running over the same graph is a
no-op. Deterministic and tool-exact (§14.2): a graph/index join, never a learned score.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

from thalamus.core.types import Scope
from thalamus.structural.graph import StructuralGraph
from thalamus.structural.schema import IngestResult, StructuralEdge, StructuralNode
from thalamus.structural.symbol_resolution import SymbolResolver

# The edge type linking a non-code node to the code node it annotates.
ANNOTATES = "annotates"

# Code-corpus node kinds the resolver targets — non-code kinds are the *sources* of an annotation,
# never its target (a finding annotates code; code is never said to annotate a finding).
_CODE_KINDS = frozenset({"module", "interface", "class", "enum", "function", "method"})

# (source_path, line) the node annotates, or ``None`` if it annotates no code location.
AnnotationLocator = Callable[[StructuralNode], tuple[str, int | None] | None]


def default_annotation_location(node: StructuralNode) -> tuple[str, int | None] | None:
    """The code location a non-code ``node`` is about: finding metadata first, else its anchor.

    Returns ``None`` for code nodes (they are link *targets*, not sources) and for nodes with no
    resolvable source location."""
    if node.kind in _CODE_KINDS:
        return None
    source_path = node.metadata.get("source_path")
    if isinstance(source_path, str) and source_path:
        return source_path, _as_line(node.metadata.get("source_line"))
    if node.anchor is not None:
        return node.anchor.path, node.anchor.line_start
    return None


def _as_line(value: object) -> int | None:
    """A 1-based line number from a metadata value, or ``None`` if it isn't one."""
    if isinstance(value, bool):  # bool is an int subclass — exclude it explicitly
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    return None


def link_anchored_nodes(
    nodes: Iterable[StructuralNode],
    code_nodes: Iterable[StructuralNode],
    graph: StructuralGraph,
    scope: Scope,
    *,
    repo_root: Path,
    locate: AnnotationLocator = default_annotation_location,
) -> int:
    """Add an ``annotates`` edge from each non-code ``node`` to the code node it is about.

    ``code_nodes`` are the graph's code nodes (module + symbols) the annotations resolve against;
    ``nodes`` are the candidate annotators (findings/docs/text). Writes edges into ``graph`` and
    returns the number created. A node whose location resolves to no code node simply does not link
    (§13.19, never forced). Idempotent — the graph dedups identical edges."""
    resolver = SymbolResolver(code_nodes, repo_root=repo_root)
    edges: list[StructuralEdge] = []
    seen: set[tuple[str, str]] = set()
    for node in nodes:
        location = locate(node)
        if location is None:
            continue
        target = resolver.resolve(location[0], location[1])
        if target is None or target.node_id == node.node_id:
            continue
        key = (node.node_id, target.node_id)
        if key in seen:
            continue
        seen.add(key)
        edges.append(StructuralEdge(node.node_id, target.node_id, ANNOTATES))
    if edges:
        graph.add(IngestResult(nodes=[], edges=edges))
    return len(edges)


__all__ = ["ANNOTATES", "AnnotationLocator", "default_annotation_location", "link_anchored_nodes"]
