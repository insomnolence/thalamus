"""StructuralRefreshPass — keep cross-hemisphere links current in a long-running serve.

New episodes arrive (background sync → durable Brain 1) while the serve is up, but their links to
the code they touched are resolved only once, at composition. This actor re-links every episode's
footprint to the current code module nodes, writing to the *same* cross-link index the gateway
queries (Neo4j: any client sees the write; in-memory: the shared instance), so a new episode
becomes structurally recallable without a serve restart. Deterministic and idempotent
(``link_by_footprint`` dedups on stable node ids), so it may *act* (§14.3 firewall).

Granularity: re-link against the CURRENT graph's code nodes — modules *and* symbols, so a
line-aware footprint links to the smallest enclosing symbol (C-7) while a legacy/file-only
footprint falls back to the module. New git episodes carry changed-line metadata. Brain 2 is
re-derived before this pass, so an episode touching a file added after startup links on the next
re-parse.

That same re-derive **destroys** the links into any file it rebuilt (``DETACH DELETE`` takes the
``TOUCHES`` edges with the node; see ``structural/relink.py``), so the already-linked cache below
is invalidated for those files through a :class:`RelinkQueue` before it is consulted. Without that
the cache made the loss permanent for the process's life.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from thalamus.core.types import MemoryId
from thalamus.dreaming.base import PassContext, PassKind, PassOutcome
from thalamus.structural import (
    CrossLinkIndex,
    FootprintFile,
    RelinkQueue,
    StructuralGraph,
    footprint_from_metadata,
    link_by_footprint,
)

# Code-corpus node kinds re-linked against (module is the coarse fallback; the rest are symbols).
_CODE_KINDS = ("module", "interface", "class", "enum", "function", "method")


class StructuralRefreshPass:
    """Re-link episode footprints to current code modules so new episodes stay recallable."""

    name = "structural-refresh"
    kind = PassKind.ACTOR

    def __init__(
        self,
        graph: StructuralGraph,
        links: CrossLinkIndex,
        *,
        relink: RelinkQueue | None = None,
    ) -> None:
        # The same handles the gateway queries — updating them is seen by live recall.
        self._graph = graph
        self._links = links
        # Memories already linked this process, each mapped to the repo-relative footprint paths
        # it was linked against. A long-running serve then re-links only the NEW episodes each
        # tick, not all ~thousands every time — killing the per-`remember` re-link storm (the
        # ~5-min CPU spikes). A memory's link result is stable only while its files' nodes are;
        # ``relink`` carries the re-derive's invalidations, and the footprint paths are what make
        # the eviction precise instead of a full cache drop.
        self._linked: dict[MemoryId, frozenset[str]] = {}
        self._relink = relink

    def run(self, ctx: PassContext) -> PassOutcome:
        if ctx.store is None or ctx.repo_root is None:
            return PassOutcome.skipped("no store/repo_root handle wired")
        # Evict first, so a memory whose code was just rebuilt is re-linked in THIS cycle rather
        # than waiting for an unrelated write to surface it.
        evicted = self._evict_rebuilt()
        # All code nodes (module + symbols) so a line-aware footprint can link to the smallest
        # enclosing symbol (C-7); a file-only footprint still falls back to the module.
        code_nodes = [
            node
            for kind in _CODE_KINDS
            for node in self._graph.nodes_of_kind(ctx.scope, kind)
        ]
        footprints = [
            (record.ref, footprint_from_metadata(record.metadata))
            for record in ctx.memories()
            if record.memory_id not in self._linked  # only memories new since the last tick
        ]
        applied = link_by_footprint(
            footprints, code_nodes, self._links, repo_root=Path(ctx.repo_root)
        )
        self._linked.update(
            (ref.memory_id, _footprint_paths(footprint)) for ref, footprint in footprints
        )
        repaired = f", {evicted} repaired after re-derive" if evicted else ""
        return PassOutcome(
            summary=(
                f"re-linked {applied} link(s) over {len(footprints)} new memory(ies){repaired}"
            ),
            details={
                "links": applied,
                "new_memories": len(footprints),
                "code_nodes": len(code_nodes),
                "repaired": evicted,
            },
        )

    def _evict_rebuilt(self) -> int:
        """Forget memories whose code was rebuilt, so this cycle re-links them.

        A rebuild takes the ``TOUCHES`` edges into that file's nodes with it, so the cached
        "already linked" verdict is stale for exactly the memories whose footprint names a
        rebuilt file. Evicting only those keeps the cache's whole point (a long serve does not
        re-link thousands of memories a tick) while making the loss self-healing. Re-linking is
        idempotent, so an over-broad eviction costs a MERGE and never corrupts.
        """
        if self._relink is None:
            return 0
        rebuilt = self._relink.drain()
        if not rebuilt:
            return 0
        stale = [mid for mid, paths in self._linked.items() if not paths.isdisjoint(rebuilt)]
        for memory_id in stale:
            del self._linked[memory_id]
        return len(stale)


def _footprint_paths(footprint: Sequence[FootprintFile]) -> frozenset[str]:
    """The bare file paths of a footprint, dropping any line info — the eviction key.

    A footprint entry is either a path or a ``(path, lines)`` pair (C-8); both are invalidated by
    the same file being rebuilt, so only the path participates.
    """
    return frozenset(
        entry if isinstance(entry, str) else entry[0] for entry in footprint
    )
