"""``thalamus dream`` — run one dreaming cycle over the brain, and the shared
cycle composition that ``serve`` ticks in the background.

A dreaming cycle runs the gated passes (LinkResolutionPass refreshes the gateway's
derived views from durable truth; BeliefAuditPass proposes retiring beliefs whose
code has vanished) and records each to the dream log. The standalone command runs
exactly one cycle against a freshly-built gateway and prints the report — useful
to inspect proposals and to validate the passes against the real (dogfood) brain;
the refresh it applies lives for the process. In ``serve`` the same scheduler runs
on a background thread so the long-running brain stays fresh (see MaintenanceTicker).
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from thalamus.cli.cochange import recent_commit_shas
from thalamus.core.protocols import Store, SupersessionIndex
from thalamus.core.types import MemoryId, MemoryRecord, RepoId, Scope, TenantId
from thalamus.dreaming import (
    BeliefAuditPass,
    CredibilityPass,
    CycleReport,
    DreamingPass,
    DreamLog,
    GatedPass,
    JsonlDreamLog,
    LinkResolutionPass,
    PassContext,
    PassStatus,
    Scheduler,
    StructuralRederivePass,
    StructuralRefreshPass,
    brain1_token,
    check_convergence,
    combine,
    manifest_token,
)
from thalamus.experiential import build_fate_context, compute_fate
from thalamus.gateway import Gateway
from thalamus.instrumentation import (
    UsageSignal,
    read_event_log,
    read_usage_log,
    reverted_shas,
)
from thalamus.retrieval import CentralityWeightsRef
from thalamus.routing import ENCODER_NAMES
from thalamus.structural import CoChangeRef, FileManifest


def build_dream_scheduler(
    gateway: Gateway,
    *,
    dream_log: DreamLog | None = None,
    credibility: DreamingPass | None = None,
    structural_rederive: StructuralRederivePass | None = None,
    attribution_refresh: DreamingPass | None = None,
    behavioral_consolidation: DreamingPass | None = None,
    usage_refresh: DreamingPass | None = None,
    centrality_refresh: DreamingPass | None = None,
    cochange_refresh: DreamingPass | None = None,
    manifest: FileManifest | None = None,
    scope: Scope | None = None,
    gate_passes: bool = True,
) -> Scheduler:
    """The v0 pass set, in dreaming.md DAG order.

    ``structural-rederive`` (actor, when supplied) re-derives Brain 2 itself from current source
    (new/changed/removed code), so it runs FIRST — before ``structural-refresh`` re-links episode
    footprints, which must see the freshly-added module nodes. ``structural-refresh`` (actor)
    re-links episodes to current code modules — included only when the gateway exposes a structural
    graph + link index (Brain 2 present). It takes its invalidation queue from
    ``structural_rederive`` so the re-link repairs the cross-hemisphere edges a rebuild destroys —
    derived here rather than passed in, because a caller that forgot to connect them would switch
    the repair off silently, with every test still green (the failure that produced it). With no
    re-derive there is nothing to invalidate and the repair layer is simply absent.
    ``link-resolution`` (actor) refreshes the gateway's
    derived views (superseded frontier + staleness). ``attribution-refresh`` (actor, when supplied)
    re-derives the footprint usage attribution from the freshly-derived graph + the logs — it runs
    AFTER re-derive/re-link (needs the current graph) and BEFORE ``usage-refresh`` (which consumes
    the attribution). ``usage-refresh`` / ``centrality-refresh`` (actors, when supplied) recompute
    the relevance-credibility recall rungs — the latter AFTER the re-derive + re-link passes, so it
    reads the freshly-derived graph topology. ``credibility`` (actor, when supplied) assesses
    each curated memory's fate-based standing — after link-resolution (it reads the refreshed
    superseded frontier) and before the proposer. ``belief-audit`` (proposer) records propose-only
    supersession suggestions."""
    passes: list[DreamingPass] = []
    if structural_rederive is not None:
        passes.append(structural_rederive)
    structural_refresh: StructuralRefreshPass | None = None
    if gateway.graph is not None and gateway.links is not None:
        structural_refresh = StructuralRefreshPass(
            gateway.graph,
            gateway.links,
            relink=structural_rederive.relink if structural_rederive is not None else None,
        )
        passes.append(structural_refresh)
    passes.append(LinkResolutionPass(gateway.refresh))
    if attribution_refresh is not None:  # re-derive footprint attribution before usage consumes it
        passes.append(attribution_refresh)
    if behavioral_consolidation is not None:  # fold the log WAL's usage into the brain (B)
        passes.append(behavioral_consolidation)
    if usage_refresh is not None:  # refresh the usage-weighted recall rung from accrued usage
        passes.append(usage_refresh)
    # Refresh the structural-centrality rung from the freshly-derived graph + links — AFTER the
    # re-derive + re-link passes above, so it reads the current topology, not the pre-tick one.
    if centrality_refresh is not None:
        # Tier 1 — this one FEEDS RECALL, so it was gated only after `--check-convergence` showed
        # `rung.centrality` identical across repeated cycles. Its inputs are the code graph and
        # the cross-links. Both are covered *durably*: the manifest changes iff Brain 2 was
        # rebuilt, and `link_by_footprint` — the sole writer of TOUCHES edges — only writes when
        # a memory is new (Brain 1 moved) or a rebuild destroyed links (the manifest moved). So
        # no separate links signal is needed, and unlike an in-process counter both halves are
        # visible to every process sharing the database.
        if gate_passes and manifest is not None and scope is not None:
            centrality_refresh = GatedPass(
                centrality_refresh, combine(brain1_token, manifest_token(manifest, scope))
            )
        passes.append(centrality_refresh)
    if cochange_refresh is not None:  # refresh the plan tool's file co-change index from new code
        # Tier 1 — also recall-feeding (the plan blast radius), gated after `plan.cochange` was
        # shown to converge. Inputs: the commit history it mines (HEAD) and the graph it maps
        # files onto (the manifest). Both durable, so a rebuild by another process is seen.
        if gate_passes and manifest is not None and scope is not None:
            cochange_refresh = GatedPass(
                cochange_refresh,
                combine(_git_head_token, manifest_token(manifest, scope)),
            )
        passes.append(cochange_refresh)
    if credibility is not None:
        passes.append(credibility)
    # NOT gated, measured: the pass runs in ~0.07s while a Brain-1 token over ~4.8k records costs
    # ~0.50s, so gating it was a 7x net loss. It became cheap when the cycle started sharing one
    # Brain-1 read; the historical figures that justified gating it predate that.
    passes.append(BeliefAuditPass())
    return Scheduler(passes, log=dream_log)


def build_credibility_pass(
    *,
    logs_dir: Path,
    code_repo: Path,
    supersession: SupersessionIndex | None,
    scope: Scope,
) -> CredibilityPass | None:
    """Wire the fate-based credibility pass to the brain's logs + git reverts (the composition that
    closes over the ``experiential`` fate primitives, keeping ``dreaming`` decoupled). ``logs_dir``
    holds ``.thalamus/logs`` (the data dir); ``code_repo`` is the git repo whose reverts are read
    (the code root — may differ from ``logs_dir``, e.g. a code-rich sample project). Needs the
    supersession index — the belief layer it assesses; returns ``None`` otherwise."""
    if supersession is None:
        return None
    logs = logs_dir / ".thalamus" / "logs"

    def assess(memories: Sequence[MemoryRecord]) -> dict[MemoryId, tuple[str, str]]:
        events = (
            list(read_event_log(logs / "retrieval.jsonl"))
            if (logs / "retrieval.jsonl").exists()
            else []
        )
        signals: list[UsageSignal] = []
        for name in ("usage.jsonl", "usage_attributed.jsonl"):
            path = logs / name
            if path.exists():
                signals.extend(read_usage_log(path))
        context = build_fate_context(
            supersession.superseded(scope), events, signals, reverted_shas=reverted_shas(code_repo)
        )
        return {
            memory_id: (verdict.polarity.value, verdict.tier.value)
            for memory_id, verdict in compute_fate(memories, context).items()
        }

    # NOT gated, measured: the pass costs ~0.43s and a token over Brain 1 plus its three logs
    # cost ~0.49s — the gate was slower than the work. See BeliefAuditPass above; the same shared
    # Brain-1 read made it cheap.
    return CredibilityPass(assess)


def make_dream_context_factory(
    *,
    store: Store,
    supersession: SupersessionIndex | None,
    scope: Scope,
    repo: Path,
    data_dir: Path | None = None,
) -> Callable[[], PassContext]:
    """A factory that stamps a fresh ``now`` on each cycle's read-only context."""

    def make() -> PassContext:
        return PassContext(
            scope=scope,
            now=datetime.now(UTC),
            store=store,
            supersession=supersession,
            repo_root=str(repo),
            data_root=str(data_dir) if data_dir is not None else None,
        )

    return make


def _git_head_token(ctx: PassContext) -> str | None:
    """The code repo's HEAD sha — the co-change index is a function of the commits it mines.

    Lives here rather than in ``dreaming`` because shelling out to git is the composition root's
    business, and ``recent_commit_shas`` already reads HEAD-first history: if HEAD has not moved,
    neither has the window the index is built from. Returns ``None`` (run the pass) when the repo
    root is absent or git cannot answer — never a guess.
    """
    if ctx.repo_root is None:
        return None
    shas = recent_commit_shas(Path(ctx.repo_root), 1)
    return shas[0] if shas else None


def dream_log_path(repo: Path) -> Path:
    return repo / ".thalamus" / "logs" / "dream.jsonl"


@dataclass(frozen=True, slots=True)
class DreamConfig:
    repo: Path
    tenant: str
    repo_id: str
    dim: int
    encoder: str
    resolve_calls: bool
    check_convergence: bool = False
    rounds: int = 2
    pass_gating: bool = True


def add_dream_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--repo", type=Path, default=Path.cwd(), help="repo whose AST is Brain 2 (default: cwd)"
    )
    parser.add_argument("--tenant", default="local", help="tenant id")
    parser.add_argument("--repo-id", default=None, help="repo id (default: repo dir name)")
    parser.add_argument("--dim", type=int, default=128, help="embedding dimensionality")
    parser.add_argument(
        "--encoder", choices=ENCODER_NAMES, default="bge-small",
        help="embedding model (default: bge-small; deterministic is for smoke tests)",
    )
    parser.add_argument(
        "--resolve-calls", action=argparse.BooleanOptionalAction, default=False,
        help="resolve Brain-2 call edges with jedi (off by default — a dream cycle does not "
        "need the call graph, and skipping it keeps the cycle fast)",
    )
    parser.add_argument(
        "--pass-gating", action=argparse.BooleanOptionalAction, default=True,
        help="skip a pass whose inputs have not changed (default on); --no-pass-gating is the "
        "ungated baseline — pair it with --check-convergence to compare the two",
    )
    parser.add_argument(
        "--check-convergence", action="store_true",
        help="run the cycle repeatedly over unchanging inputs and report any derived view that "
        "moves. A pass that keeps moving cannot be safely gated (and is probably a bug), so this "
        "is the precondition for any pass-gating work — see dreaming/equivalence.py",
    )
    parser.add_argument(
        "--rounds", type=int, default=2,
        help="cycles to run with --check-convergence (default 2; more catches slow drift)",
    )


def dream_config(args: argparse.Namespace) -> DreamConfig:
    repo = Path(args.repo).resolve()
    return DreamConfig(
        repo=repo,
        tenant=str(args.tenant),
        repo_id=str(args.repo_id) if args.repo_id else repo.name,
        dim=int(args.dim),
        encoder=str(args.encoder),
        resolve_calls=bool(args.resolve_calls),
        pass_gating=bool(getattr(args, "pass_gating", True)),
        check_convergence=bool(getattr(args, "check_convergence", False)),
        rounds=int(getattr(args, "rounds", 2)),
    )


def _print_report(report: CycleReport) -> None:
    print(f"thalamus dream — cycle at {report.started_at.isoformat()}", file=sys.stderr)
    marks = {PassStatus.OK: "ok", PassStatus.SKIPPED: "skip", PassStatus.FAILED: "FAIL"}
    for p in report.passes:
        detail = p.summary or p.error or ""
        print(f"  [{marks[p.status]}] {p.name} ({p.kind.value}): {detail}", file=sys.stderr)
        for proposal in p.details.get("proposals", []):
            print(
                f"      ⚠ proposed supersede {proposal['memory_id']}: {proposal['reason']}",
                file=sys.stderr,
            )


_PROBE_KINDS = ("module", "interface", "class", "enum", "function", "method", "document",
                "section", "chunk", "finding")


def _convergence_probes(
    gateway: Gateway,
    store: Store,
    scope: Scope,
    *,
    centrality_ref: CentralityWeightsRef | None = None,
    cochange_ref: CoChangeRef | None = None,
) -> dict[str, Callable[[], object]]:
    """Named readers for the derived views reachable from an assembled brain.

    Coverage is deliberately explicit rather than magical: these cover ``link-resolution``
    (the served views), ``structural-refresh`` (cross-links), and ``structural-rederive``
    (the graph's node set). ``centrality-refresh`` and ``cochange-refresh`` hold their state in
    refs the composition root does not hand back, so they are **not** covered yet — each should
    add its probe here when its gate is built, which is the point at which it matters.
    """
    probes: dict[str, Callable[[], object]] = {
        "views.superseded": lambda: dict(gateway.views.views.superseded),
        "views.stale_references": lambda: {
            str(ref): list(paths) for ref, paths in gateway.views.views.stale_references.items()
        },
    }
    graph, links = gateway.graph, gateway.links
    if graph is not None:
        probes["brain2.nodes"] = lambda: sorted(
            node.node_id for kind in _PROBE_KINDS for node in graph.nodes_of_kind(scope, kind)
        )
    if links is not None:
        probes["crosslinks"] = lambda: {
            str(record.ref): sorted(n.node_id for n in links.nodes_for(record.ref))
            for record in store.scan(scope)
        }
    # The two recall-feeding rungs. Unlike the views above, a stale value here is served to the
    # actuator, so their gates may not be trusted until these probes show them converging.
    if centrality_ref is not None:
        probes["rung.centrality"] = lambda: {
            str(ref): weight for ref, weight in centrality_ref.weights.items()
        }
    if cochange_ref is not None and graph is not None:
        # Probed through ``cochanged`` — the exact surface the planner queries — rather than the
        # index's internals, so the view compared is the one that reaches a plan brief. Order is
        # kept rather than sorted: the planner consumes the ranking, so a reordering IS a change.
        probes["plan.cochange"] = lambda: {
            node.node_id: [
                (str(ref), weight) for ref, weight in cochange_ref.cochanged(node.ref)
            ]
            for node in graph.nodes_of_kind(scope, "module")
        }
    return probes


def run_dream(config: DreamConfig) -> None:
    """Build the brain from durable state, run one dreaming cycle, and report."""
    # Lazy import breaks the serve<->dream module cycle (serve imports the cycle builders above).
    from thalamus.cli.brain import close_store
    from thalamus.cli.serve import ServeConfig, build_serve_gateway

    serve_config = ServeConfig(
        repo=config.repo,
        tenant=config.tenant,
        repo_id=config.repo_id,
        dim=config.dim,
        encoder=config.encoder,
        k=5,
        k_hop=1,
        resolve_calls=config.resolve_calls,
        structural_min_relevance=0.6,
        max_structural_items=12,
        max_memory_chars=1000,
        neo4j_uri=os.environ.get("THALAMUS_NEO4J_URI"),
        neo4j_user=os.environ.get("THALAMUS_NEO4J_USER", "neo4j"),
        neo4j_password=os.environ.get("THALAMUS_NEO4J_PASSWORD"),
        session=False,
    )
    brain = build_serve_gateway(serve_config)
    gateway, store, supersession, rederive = (
        brain.gateway, brain.store, brain.supersession, brain.rederive
    )
    scope = Scope(TenantId(config.tenant), RepoId(config.repo_id))
    try:
        scheduler = build_dream_scheduler(
            gateway,
            dream_log=JsonlDreamLog(dream_log_path(config.repo)),
            credibility=build_credibility_pass(
                logs_dir=config.repo, code_repo=config.repo, supersession=supersession,
                scope=scope,
            ),
            structural_rederive=rederive,
            attribution_refresh=brain.attribution_refresh,
            behavioral_consolidation=brain.behavioral_consolidation,
            usage_refresh=brain.usage_refresh,
            centrality_refresh=brain.centrality_refresh,
            # Now built by build_serve_gateway, so the dream cycle exercises the same pass set
            # the serve does — it previously had no co-change pass at all.
            cochange_refresh=brain.cochange_refresh,
            manifest=brain.manifest,
            scope=brain.scope,
            gate_passes=config.pass_gating,
        )
        context = make_dream_context_factory(
            store=store, supersession=supersession, scope=scope, repo=config.repo,
            data_dir=config.repo,
        )
        if config.check_convergence:
            report = check_convergence(
                scheduler,
                context,
                _convergence_probes(
                    gateway, store, scope,
                    centrality_ref=brain.centrality_ref, cochange_ref=brain.cochange_ref,
                ),
                rounds=config.rounds,
            )
            for cycle in report.reports:
                _print_report(cycle)
            print(report.render(), file=sys.stderr)
            if not report.converged:
                raise SystemExit(1)
            return
        _print_report(scheduler.run(context()))
    finally:
        close_store(store)
