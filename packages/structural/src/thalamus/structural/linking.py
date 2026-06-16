"""Cross-hemisphere auto-linking from the trajectory footprint (§13.19).

The deterministic backbone of the cross-hemisphere link: an episode is linked to the
structural nodes of the files in its commit footprint — *no inference* (§13.19,
"Creation — deterministic backbone, mostly free").

**Granularity (C-7).** When a footprint entry carries the *lines* it touched, the link
is made to the **smallest enclosing symbol** (function/class/method) via
:class:`~thalamus.structural.symbol_resolution.SymbolResolver`; when it does not (a bare
file path — git's per-file ``diff-tree`` footprint, the only data captured today), it
falls back to the **module** node. So symbol-level linking is the honest end-state and
module-level the degraded floor — the same code path, chosen by whether line info exists.
k-hop expansion at recall still reaches contained/containing nodes either way.

Footprint files are repo-relative (git ``diff-tree``); structural anchors carry the
path the ingestor saw (absolute, when ingested from an absolute root), so anchors are
normalized to repo-relative POSIX before matching. A footprint file with no code
node simply does not link — links are never forced (§13.19), and a file that no longer
resolves is the §13.18-D2 staleness signal (surfaced as a flag later, not here).
Symbol-identity re-resolution across renames and outcome-weighted link credibility are
the gated layers on top (§13.19); this is the deterministic floor they build on.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path

from thalamus.core.types import MemoryRef, StructuralRef
from thalamus.structural.cross_link import CrossLinkIndex
from thalamus.structural.schema import StructuralNode
from thalamus.structural.symbol_resolution import SymbolResolver

# A footprint entry is either a bare file path (no line info → module-level link) or a
# ``(file, touched_lines)`` pair (line info → smallest-enclosing-symbol link). Both shapes
# coexist so today's file-only footprints keep working while line-aware producers link finer.
FootprintFile = str | tuple[str, Sequence[int]]


def module_index(nodes: Iterable[StructuralNode], repo_root: Path) -> dict[str, StructuralRef]:
    """Map each module's repo-relative POSIX path to its stable node ref.

    Shared by :func:`link_by_footprint` and the footprint usage attributor so that
    footprint files and work files normalize to node refs identically — one source of
    truth for the repo-relative POSIX normalization."""
    root = repo_root.resolve()
    index: dict[str, StructuralRef] = {}
    for node in nodes:
        if node.kind != "module" or node.anchor is None:
            continue
        try:
            rel = Path(node.anchor.path).resolve().relative_to(root).as_posix()
        except ValueError:
            continue  # anchored outside the repo root — not addressable by a footprint
        index[rel] = node.ref
    return index


def _file_and_lines(entry: FootprintFile) -> tuple[str, Sequence[int] | None]:
    """Normalize a footprint entry to ``(file, touched_lines_or_None)``."""
    if isinstance(entry, str):
        return entry, None
    file, lines = entry
    return file, lines


def link_by_footprint(
    items: Iterable[tuple[MemoryRef, Sequence[FootprintFile]]],
    nodes: Iterable[StructuralNode],
    links: CrossLinkIndex,
    *,
    repo_root: Path,
) -> int:
    """Link each ``(memory_id, footprint)`` to the code node(s) its footprint touches.

    Each footprint entry is either a bare file path → its **module** node, or a
    ``(file, touched_lines)`` pair → the **smallest enclosing symbol** of each touched line
    (falling back to the module when no symbol encloses it). ``nodes`` is the ingested graph's
    nodes (e.g. ``IngestResult.nodes``). Returns the number of links created. Deterministic and
    idempotent: it keys on stable node ids, so re-running over the same AST yields the same links
    (``CrossLinkIndex`` dedups)."""
    resolver = SymbolResolver(nodes, repo_root=repo_root)
    created = 0
    for memory, footprint in items:
        linked: set[StructuralRef] = set()
        for entry in footprint:
            file, lines = _file_and_lines(entry)
            # No lines → one module-level resolution; lines → one resolution per touched line
            # (the set of enclosing symbols the diff actually hit), deduped by the linked set.
            targets = (resolver.resolve(file, None),) if not lines else (
                resolver.resolve(file, line) for line in lines
            )
            for node in targets:
                if node is not None and node.ref not in linked:
                    links.link(memory, node.ref)
                    linked.add(node.ref)
                    created += 1
    return created


def footprint_staleness(
    items: Iterable[tuple[MemoryRef, Sequence[FootprintFile]]],
    *,
    repo_root: Path,
) -> dict[MemoryRef, list[str]]:
    """For each memory, the footprint files no longer present on disk under ``repo_root``.

    The §13.18-D2 staleness signal as a deterministic disk check: a memory whose footprint
    references a file that has been deleted or moved is a *staleness candidate* — the code it
    was about is gone. The mirror of :func:`link_by_footprint` (which links the files that *do*
    resolve). Surfaced as a review flag, never auto-deleted (§14.4: conservative against silent
    poisons — heavy refactors throw false positives, so time + outcomes arbitrate). Returns only
    memories with at least one missing file; order preserved for stable reporting.
    """
    root = repo_root.resolve()
    stale: dict[MemoryRef, list[str]] = {}
    for memory, footprint in items:
        files = [_file_and_lines(entry)[0] for entry in footprint]
        missing = [file for file in files if not (root / file).exists()]
        if missing:
            stale[memory] = missing
    return stale
