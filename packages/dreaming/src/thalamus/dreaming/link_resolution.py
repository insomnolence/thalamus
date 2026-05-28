"""LinkResolutionPass — the actor that keeps the gateway's derived views fresh.

The headline operational win of dreaming-as-refresh. In a long-running serve the
gateway's ``superseded`` frontier and ``stale_references`` map are frozen at
composition (views.py): a live ``remember --supersedes`` persists to the durable
index but the in-memory map never sees it, and code deleted after start-up never
flags the beliefs about it. This pass re-reads both from durable truth and swaps
a fresh :class:`DerivedViews` in through the injected refresh hook
(``Gateway.refresh``). Deterministic ⇒ it may *act* (§14.3 firewall).

v0 scope is exactly the two frozen-at-composition dicts. Re-linking new episodes
to module nodes (``link_by_footprint``) and Brain-2 re-parse are the same
mechanism applied to other derived state — deferred, and partly unnecessary
already because the Neo4j cross-link index serves links live per recall.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from thalamus.core import MemoryRef
from thalamus.dreaming._curated import curated_footprints
from thalamus.dreaming.base import PassContext, PassKind, PassOutcome
from thalamus.gateway import DerivedViews
from thalamus.structural import footprint_staleness


class LinkResolutionPass:
    """Recompute {superseded, stale_references} from durable truth and refresh."""

    name = "link-resolution"
    kind = PassKind.ACTOR

    def __init__(self, refresh: Callable[[DerivedViews], None]) -> None:
        # The gateway's refresh seam, injected so this pass never imports the Gateway itself.
        self._refresh = refresh

    def run(self, ctx: PassContext) -> PassOutcome:
        if ctx.store is None or ctx.supersession is None:
            return PassOutcome.skipped("no store/supersession handle wired")
        superseded = dict(ctx.supersession.superseded(ctx.scope))
        stale: dict[MemoryRef, list[str]] = {}
        if ctx.repo_root is not None:
            stale = footprint_staleness(
                curated_footprints(ctx.store, ctx.scope), repo_root=Path(ctx.repo_root)
            )
        self._refresh(DerivedViews(superseded=superseded, stale_references=dict(stale)))
        return PassOutcome(
            summary=f"refreshed views: {len(superseded)} superseded, {len(stale)} stale",
            details={"superseded": len(superseded), "stale": len(stale)},
        )
