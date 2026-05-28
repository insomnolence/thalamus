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

    def run(self, ctx: PassContext) -> PassOutcome:
        if ctx.store is None or ctx.repo_root is None:
            return PassOutcome.skipped("no store/repo_root handle wired")
        modules = self._graph.nodes_of_kind(ctx.scope, "module")
        footprints = [
            (record.ref, tuple(record.metadata.get("footprint", ())))
            for record in ctx.store.scan(ctx.scope)
        ]
        applied = link_by_footprint(
            footprints, modules, self._links, repo_root=Path(ctx.repo_root)
        )
        return PassOutcome(
            summary=f"re-linked footprints: {applied} link(s) over {len(modules)} module(s)",
            details={"links": applied, "modules": len(modules)},
        )
