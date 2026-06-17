"""``thalamus.cli probe-eval`` — L1 verdict on real past questions.

Replays the substantive user prompts from this project's Claude Code transcripts
through the live experiential brain and a brain-off floor, reports surface-quality
metrics, and prints the lift the brain provides over the floor. The composition
deliberately omits Brain 2 (structural code/doc retrieval) — that's a parallel L1
question best answered separately; this verb stays focused on whether the brain's
experiential memory consistently surfaces confident hits on the questions the
actuator actually asked.

The supersession-aware view applies here too (the un-superseded frontier), so the
numbers reflect what a current recall would actually surface — not historical truth.
Strictly L1: does **not** answer "did surfacing make outcomes better" (L3 needs
outcome volume; see ``thalamus verdict``).
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from thalamus.cli.brain import build_store, close_store
from thalamus.cli.rung_arms import compose_rung_arms
from thalamus.core.exceptions import ThalamusError
from thalamus.core.protocols import Encoder, Retriever, Store
from thalamus.core.types import (
    Hemisphere,
    MemoryId,
    MemoryRef,
    RepoId,
    Scope,
    Supersession,
    TenantId,
)

if TYPE_CHECKING:
    from neo4j import Driver
from thalamus.eval import (
    ProbeReport,
    TranscriptProbe,
    compare_probes,
    default_transcripts_dir,
    extract_probes,
    find_transcripts,
)
from thalamus.experiential import Neo4jSupersessionIndex, reuse_by_memory
from thalamus.instrumentation import read_event_log, read_usage_log
from thalamus.retrieval import (
    CentralityWeightsRef,
    L0Retriever,
)
from thalamus.routing import BgeEncoder, DeterministicEncoder
from thalamus.store import Neo4jStore, connect
from thalamus.structural import memory_centrality

_DEFAULT_DIM = 128
_DEFAULT_ENCODER = "bge-small"


@dataclass(frozen=True, slots=True)
class ProbeEvalConfig:
    repo: Path
    tenant: str
    repo_id: str
    dim: int
    encoder: str
    k: int
    threshold: float
    max_probes: int  # 0 = no cap
    rungs: bool  # also ablate the Brain-2-signal relevance rungs (structrel / centrality / full)
    transcripts_dir: Path | None  # None = default for this repo
    neo4j_uri: str | None
    neo4j_user: str
    neo4j_password: str | None


@dataclass(frozen=True, slots=True)
class ProbeEvalReport:
    n_probes: int
    n_sessions: int
    k: int
    threshold: float
    by_retriever: dict[str, ProbeReport]


def add_probe_eval_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--repo", type=Path, default=Path.cwd(),
        help="repo whose transcripts and brain are evaluated (default: cwd)",
    )
    parser.add_argument("--tenant", default="local", help="tenant id for scoping")
    parser.add_argument(
        "--repo-id", default=None, help="repo id for scoping (default: repo dir name)"
    )
    parser.add_argument("--dim", type=int, default=_DEFAULT_DIM, help="embedding dimensionality")
    parser.add_argument(
        "--encoder", choices=("bge-small", "deterministic"), default=_DEFAULT_ENCODER,
        help="embedding model (default: bge-small; deterministic for offline smoke tests)",
    )
    parser.add_argument("--k", type=int, default=5, help="top-k cutoff per probe")
    parser.add_argument(
        "--threshold", type=float, default=0.5,
        help=(
            "relevance floor a top-1 hit must clear to count as 'surfaced' (default 0.5 — "
            "conservative for BGE; lower for other encoders or to count any nonzero match)"
        ),
    )
    parser.add_argument(
        "--max-probes", type=int, default=0,
        help="bound the corpus to the first N probes (0 = all, ~100s typical)",
    )
    parser.add_argument(
        "--transcripts-dir", type=Path, default=None,
        help="override the transcripts directory (default: ~/.claude/projects/<sanitized-cwd>)",
    )
    parser.add_argument(
        "--rungs", action="store_true",
        help="also ablate the Brain-2-signal relevance rungs (+structrel/+central/+full); "
        "needs Neo4j (builds Brain 2) — shows each rung's marginal lift over brain-on",
    )


def probe_eval_config(args: argparse.Namespace) -> ProbeEvalConfig:
    repo = Path(args.repo).resolve()
    return ProbeEvalConfig(
        repo=repo,
        tenant=str(args.tenant),
        repo_id=str(args.repo_id) if args.repo_id else repo.name,
        dim=int(args.dim),
        encoder=str(args.encoder),
        k=int(args.k),
        threshold=float(args.threshold),
        max_probes=int(args.max_probes),
        rungs=bool(args.rungs),
        transcripts_dir=Path(args.transcripts_dir) if args.transcripts_dir else None,
        neo4j_uri=os.environ.get("THALAMUS_NEO4J_URI"),
        neo4j_user=os.environ.get("THALAMUS_NEO4J_USER", "neo4j"),
        neo4j_password=os.environ.get("THALAMUS_NEO4J_PASSWORD"),
    )


def compute_probe_eval(
    probes: Sequence[TranscriptProbe],
    retrievers: dict[str, Retriever],
    *,
    scope: Scope,
    k: int,
    threshold: float,
) -> ProbeEvalReport:
    """Pure: run the ablation switch over a probe corpus and a set of named retrievers.

    Unit-testable; ``run_probe_eval`` is the live composition that builds the
    retrievers from durable Brain 1."""
    return ProbeEvalReport(
        n_probes=len(probes),
        n_sessions=len({p.session_id for p in probes}),
        k=k,
        threshold=threshold,
        by_retriever=compare_probes(
            retrievers, probes, scope=scope, k=k, threshold=threshold
        ),
    )


def _render(report: ProbeEvalReport) -> str:
    lines = [
        f"Thalamus probe-eval — k={report.k}, threshold={report.threshold:.2f}",
        f"corpus: {report.n_probes} substantive probes from {report.n_sessions} session(s)",
        "",
        f"{'retriever':<14}  {'surface@k':>10}  {'mean_rel':>10}  {'median':>10}  {'p90':>10}",
    ]
    for label, r in report.by_retriever.items():
        lines.append(
            f"{label:<14}  {r.surface_rate:>10.3f}  {r.mean_top_relevance:>10.3f}  "
            f"{r.median_top_relevance:>10.3f}  {r.p90_top_relevance:>10.3f}"
        )
    # Lift over the brain-off floor — with NullRetriever the floor is 0.0, but
    # state it explicitly so a future lexical baseline makes the lift meaningful.
    if "brain-off" in report.by_retriever:
        floor = report.by_retriever["brain-off"].mean_top_relevance
        for label, r in report.by_retriever.items():
            if label == "brain-off":
                continue
            lift = r.mean_top_relevance - floor
            lines.append(f"  lift({label} - brain-off) = {lift:+.3f} mean relevance")
    return "\n".join(lines)


def _encoder(config: ProbeEvalConfig) -> Encoder:
    """The embedding model for the eval — BGE (real) or deterministic (offline smoke)."""
    if config.encoder == "bge-small":
        return BgeEncoder("BAAI/bge-small-en-v1.5")
    return DeterministicEncoder(dim=config.dim)


def _build_brain_on(
    config: ProbeEvalConfig, scope: Scope
) -> tuple[Retriever, Mapping[MemoryRef, Supersession], Store, Driver | None]:
    """Compose the durable Brain-1 relevance base + the superseded frontier.

    Returns (L0 base, superseded_map, store, driver_or_None) so the caller can compose the
    ablation variants over the *same* store and close them. The supersession-demoting view is
    applied by the caller so the eval reflects current truth (§13.18 R1)."""
    encoder = _encoder(config)
    superseded_map: dict[MemoryRef, Supersession] = {}
    driver: Driver | None = None
    if config.neo4j_uri is not None:
        driver = connect(config.neo4j_uri, config.neo4j_user, config.neo4j_password or "")
        store: Store = Neo4jStore(
            dim=encoder.dim, driver=driver, hemisphere=Hemisphere.EXPERIENTIAL,
            encoder_id=config.encoder,
        )
        superseded_map = dict(Neo4jSupersessionIndex(driver, scope).superseded(scope))
    else:
        store = build_store(
            dim=encoder.dim, neo4j_uri=None, neo4j_user=config.neo4j_user,
            neo4j_password=config.neo4j_password, encoder_id=config.encoder,
        )
    return L0Retriever(encoder, store), superseded_map, store, driver


def _usage_weights(config: ProbeEvalConfig) -> dict[MemoryId, float]:
    """Per-memory cross-session usage weight from the durable logs (the L-R1 rung's input)."""
    logs = config.repo / ".thalamus" / "logs"
    events = list(read_event_log(logs / "retrieval.jsonl"))
    signals = list(read_usage_log(logs / "usage.jsonl")) + list(
        read_usage_log(logs / "usage_attributed.jsonl")
    )
    return {mid: float(n) for mid, n in reuse_by_memory(events, signals).items()}


def run_probe_eval(config: ProbeEvalConfig) -> ProbeEvalReport:
    """Live: replay the local transcripts, ablating brain-off / brain-on / brain-on+usage."""
    scope = Scope(TenantId(config.tenant), RepoId(config.repo_id))
    directory = config.transcripts_dir or default_transcripts_dir(config.repo)
    transcripts = find_transcripts(directory)
    if not transcripts:
        raise ThalamusError(f"no transcripts found under {directory}")
    probes = extract_probes(transcripts)
    if config.max_probes > 0:
        probes = probes[: config.max_probes]
    if not probes:
        raise ThalamusError(f"no substantive probes extracted from {len(transcripts)} transcripts")

    usage_weights = _usage_weights(config)
    if config.rungs:
        report = _run_with_rungs(config, scope, probes, usage_weights)
    else:
        base, superseded_map, store, driver = _build_brain_on(config, scope)
        try:
            retrievers = compose_rung_arms(
                base, superseded_map, usage_weights=usage_weights
            )
            report = compute_probe_eval(
                probes, retrievers, scope=scope, k=config.k, threshold=config.threshold
            )
        finally:
            close_store(store)
            if driver is not None:
                driver.close()
    print(_render(report))
    return report


def _run_with_rungs(
    config: ProbeEvalConfig,
    scope: Scope,
    probes: Sequence[TranscriptProbe],
    usage_weights: Mapping[MemoryId, float],
) -> ProbeEvalReport:
    """Ablate the Brain-2-signal rungs over the probe corpus: +structrel, +central, and the full
    stack, each a marginal arm over brain-on. Needs Brain 2, so it builds the full gateway (reusing
    its graph / links / structural retrievers) and composes each rung over the same L0 base.

    The structural rungs re-rank *experiential* candidates by Brain-2 signal (cross-link relevance
    / centrality) — firewall-clean, never grading prose — so this stays a recall-quality question,
    just structure-informed. No graph (experiential-only) raises rather than dropping arms."""
    # Lazy import: serve pulls heavy optional wiring; keep the CLI light until --rungs is used.
    from thalamus.cli.serve import ServeConfig, build_serve_gateway

    encoder = _encoder(config)
    serve_config = ServeConfig(
        repo=config.repo, tenant=config.tenant, repo_id=config.repo_id, dim=config.dim,
        encoder=config.encoder, k=config.k, k_hop=1, resolve_calls=True,
        structural_min_relevance=0.6, max_structural_items=12, max_memory_chars=1000,
        neo4j_uri=config.neo4j_uri, neo4j_user=config.neo4j_user,
        neo4j_password=config.neo4j_password, session=False,
    )
    gateway, store, *_rest = build_serve_gateway(serve_config, encoder=encoder)
    try:
        graph, links = gateway.graph, gateway.links
        if graph is None or links is None:
            raise ThalamusError("--rungs needs a Brain-2 graph (Neo4j); none was built")
        structural_retrievers = gateway.structural_retrievers
        base = L0Retriever(encoder, store)
        superseded = _superseded(config, scope)
        centrality = CentralityWeightsRef(
            memory_centrality([record.ref for record in store.scan(scope)], graph, links)
        )

        arms = compose_rung_arms(
            base, superseded, usage_weights=usage_weights,
            links=links, structural_retrievers=structural_retrievers, centrality=centrality,
        )
        return compute_probe_eval(
            probes, arms, scope=scope, k=config.k, threshold=config.threshold
        )
    finally:
        close_store(store)


def _superseded(config: ProbeEvalConfig, scope: Scope) -> Mapping[MemoryRef, Supersession]:
    """The current superseded frontier from durable state (empty without Neo4j)."""
    if config.neo4j_uri is None:
        return {}
    driver = connect(config.neo4j_uri, config.neo4j_user, config.neo4j_password or "")
    try:
        return dict(Neo4jSupersessionIndex(driver, scope).superseded(scope))
    finally:
        driver.close()
