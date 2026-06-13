"""StructuralRederivePass — re-derive Brain 2 from source in a long-running serve.

Brain 2 (the structural code/doc graph) is a re-derivable view of the source (§14.1), built once
at serve start. While the serve runs, code added/changed/removed since startup is invisible to it
until a restart — the gap this pass closes. Each cycle it re-runs the incremental ingest over the
SAME graph + per-corpus indexes the gateway queries: content-hash the corpus files, and ONLY when
something changed re-parse, drop vanished/changed nodes, re-MERGE, and re-embed the changed files
(the §14.1 re-derive oracle, live). A no-change tick is O(hash files) — no parse, no embed — so it
is cheap to run on every maintenance cycle.

It is genuinely a dreaming pass (unlike experiential capture): it writes only a *regenerable
derived view*, so it may **act** (§14.3 firewall). It runs before ``StructuralRefreshPass`` so
freshly-added code modules exist before episode footprints are re-linked against them.

``regen`` (optional) refreshes any *external* index artifact a corpus depends on (e.g. running
``scip-typescript`` to rebuild a ``.scip``) before the ingest reads it — the hook the declarative
``[[corpus]]`` ``regen_command`` plugs into; ``None`` re-ingests the artifacts as they stand.

Honest limit: the underlying ingest removes-then-re-MERGEs changed files' nodes, so a recall in
that sub-second window could miss a node. Brain 2 is a derived view and the next recall is correct;
a single-transaction swap is the documented robustness follow-up.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from thalamus.core.protocols import Encoder
from thalamus.dreaming.base import PassContext, PassKind, PassOutcome
from thalamus.structural import CorpusSpec, FileManifest, StructuralGraph, incremental_ingest


class StructuralRederivePass:
    """Re-derive Brain 2 from current source into the live graph + indexes (hash-gated)."""

    name = "structural-rederive"
    kind = PassKind.ACTOR

    def __init__(
        self,
        corpora: Sequence[CorpusSpec],
        graph: StructuralGraph,
        manifest: FileManifest,
        encoder: Encoder,
        *,
        regen: Callable[[Sequence[CorpusSpec]], None] | None = None,
    ) -> None:
        # The same graph/index/manifest handles the gateway queries — re-ingesting into them is
        # seen by live recall, no restart. ``corpora`` are the exact specs the startup build used.
        self._corpora = list(corpora)
        self._graph = graph
        self._manifest = manifest
        self._encoder = encoder
        self._regen = regen

    def run(self, ctx: PassContext) -> PassOutcome:
        if ctx.repo_root is None:
            return PassOutcome.skipped("no repo_root handle wired")
        if self._regen is not None:
            self._regen(self._corpora)
        result = incremental_ingest(
            Path(ctx.repo_root),
            ctx.scope,
            corpora=self._corpora,
            graph=self._graph,
            manifest=self._manifest,
            encoder=self._encoder,
        )
        if not result.rebuilt:
            return PassOutcome.skipped("no source changes")
        stats = result.stats
        return PassOutcome(
            summary=(
                f"re-derived Brain 2: {stats.changed} changed / {stats.vanished} vanished file(s), "
                f"{stats.embedded} node(s) re-embedded, {stats.removed} dropped"
            ),
            details={
                "changed": stats.changed,
                "vanished": stats.vanished,
                "embedded": stats.embedded,
                "removed": stats.removed,
            },
        )
