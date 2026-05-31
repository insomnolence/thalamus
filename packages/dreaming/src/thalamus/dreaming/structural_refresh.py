"""StructuralRefreshPass — keep cross-hemisphere links current in a long-running serve.

New episodes arrive (background sync → durable Brain 1) while the serve is up, but their links to
the code they touched are resolved only once, at composition. This actor re-links every episode's
footprint to the current code module nodes, writing to the *same* cross-link index the gateway
queries (Neo4j: any client sees the write; in-memory: the shared instance), so a new episode
becomes structurally recallable without a serve restart. Deterministic and idempotent
(``link_by_footprint`` dedups on stable node ids), so it may *act* (§14.3 firewall).

v0 scope: re-link against the CURRENT graph's module nodes. Re-deriving Brain 2 itself
(``incremental_ingest``, so files added/changed since startup appear as nodes) is the documented
follow-up — an episode touching a file added after startup links on the next re-parse.
"""

from __future__ import annotations

from pathlib import Path

from thalamus.core.types import MemoryId
from thalamus.dreaming.base import PassContext, PassKind, PassOutcome
from thalamus.structural import CrossLinkIndex, StructuralGraph, link_by_footprint


class StructuralRefreshPass:
    """Re-link episode footprints to current code modules so new episodes stay recallable."""

    name = "structural-refresh"
    kind = PassKind.ACTOR

    def __init__(self, graph: StructuralGraph, links: CrossLinkIndex) -> None:
        # The same handles the gateway queries — updating them is seen by live recall.
        self._graph = graph
        self._links = links
        # Memory ids already linked this process. A long-running serve then re-links only the NEW
        # episodes each tick, not all ~thousands every time — killing the per-`remember` re-link
        # storm (the ~5-min CPU spikes). Safe to skip a seen memory: Brain 2's modules are fixed
        # at serve start (not re-derived mid-serve), so a memory's link result is stable for life.
        self._linked: set[MemoryId] = set()

    def run(self, ctx: PassContext) -> PassOutcome:
        if ctx.store is None or ctx.repo_root is None:
            return PassOutcome.skipped("no store/repo_root handle wired")
        modules = self._graph.nodes_of_kind(ctx.scope, "module")
        footprints = [
            (record.ref, tuple(record.metadata.get("footprint", ())))
            for record in ctx.store.scan(ctx.scope)
            if record.memory_id not in self._linked  # only memories new since the last tick
        ]
        applied = link_by_footprint(
            footprints, modules, self._links, repo_root=Path(ctx.repo_root)
        )
        self._linked.update(ref.memory_id for ref, _footprint in footprints)
        return PassOutcome(
            summary=f"re-linked {applied} link(s) over {len(footprints)} new memory(ies)",
            details={"links": applied, "new_memories": len(footprints), "modules": len(modules)},
        )
