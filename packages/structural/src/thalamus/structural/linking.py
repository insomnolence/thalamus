"""Cross-hemisphere auto-linking from the trajectory footprint (§13.19).

The deterministic backbone of the cross-hemisphere link: an episode is linked to the
structural nodes of the files in its commit footprint — *no inference* (§13.19,
"Creation — deterministic backbone, mostly free"). v0 links at **module granularity**
(one link per touched source file); k-hop expansion at recall reaches the contained
classes/functions, so coarse links + graph spreading give fine-grained "related code"
without finer footprint data than git's per-file diff provides.

Footprint files are repo-relative (git ``diff-tree``); structural anchors carry the
path the ingestor saw (absolute, when ingested from an absolute root), so anchors are
normalized to repo-relative POSIX before matching. A footprint file with no module
node simply does not link — links are never forced (§13.19), and a file that no longer
resolves is the §13.18-D2 staleness signal (surfaced as a flag later, not here).
Symbol-identity re-resolution across renames and outcome-weighted link credibility are
the gated layers on top (§13.19); this is the deterministic floor they build on.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path

from thalamus.core.types import MemoryId
from thalamus.structural.cross_link import CrossLinkIndex
from thalamus.structural.schema import StructuralNode


def _module_index(nodes: Iterable[StructuralNode], repo_root: Path) -> dict[str, str]:
    """Map each module's repo-relative POSIX path to its stable node id."""
    root = repo_root.resolve()
    index: dict[str, str] = {}
    for node in nodes:
        if node.kind != "module" or node.anchor is None:
            continue
        try:
            rel = Path(node.anchor.path).resolve().relative_to(root).as_posix()
        except ValueError:
            continue  # anchored outside the repo root — not addressable by a footprint
        index[rel] = node.node_id
    return index


def link_by_footprint(
    items: Iterable[tuple[MemoryId, Sequence[str]]],
    nodes: Iterable[StructuralNode],
    links: CrossLinkIndex,
    *,
    repo_root: Path,
) -> int:
    """Link each ``(memory_id, footprint_files)`` to the module nodes of those files.

    ``nodes`` is the ingested graph's nodes (e.g. ``IngestResult.nodes``). Returns the
    number of links created. Deterministic and idempotent: it keys on stable node ids,
    so re-running over the same AST yields the same links (``CrossLinkIndex`` dedups)."""
    index = _module_index(nodes, repo_root)
    created = 0
    for memory_id, files in items:
        linked: set[str] = set()
        for file in files:
            node_id = index.get(file)
            if node_id is not None and node_id not in linked:
                links.link(memory_id, node_id)
                linked.add(node_id)
                created += 1
    return created
