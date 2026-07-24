"""``thalamus rung-eval`` — utility-join ablation: do the rungs rank the USED memory higher?

The honest validation of the relevance rungs (L-R1 usage, L-R2 structural relevance / centrality).
``probe-eval --rungs`` measures top-1 *cosine*, which saturates and is biased against re-rankers;
this measures the right thing: take each past recall's cue → the memory actually USED for it
(behavioral label, from the usage / attribution logs), re-run the cue through each rung, and report
**recall@k / MRR / hit@k of the used memory** per arm. A rung earns its place only if it ranks the
used memory higher than brain-on does. ``--split`` de-leaks the usage rung (weights from the older
recalls, tested on the newest fraction). Offline (reads durable Neo4j + the logs); no serve needed.

Reuses ``serve``'s argument + config machinery, so a config-rich brain (a sample project:
``code_root`` + ``data_dir`` split, SCIP / docs / findings corpora) is built as the live tool
builds it — pass ``--config <repo>/thalamus.toml``. Logs are read from the brain's ``data_dir``
(else its repo).
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from thalamus.cli.brain import close_store
from thalamus.cli.rung_arms import compose_rung_arms
from thalamus.cli.serve import ServeConfig, add_serve_arguments, build_serve_gateway, serve_config
from thalamus.core.exceptions import ThalamusError
from thalamus.core.protocols import Encoder
from thalamus.core.types import MemoryId, MemoryRef, RepoId, Scope, Supersession, TenantId
from thalamus.eval import EvalReport, cases_from_usage, compare
from thalamus.experiential import Neo4jSupersessionIndex, reuse_by_memory
from thalamus.instrumentation import RetrievalEvent, UsageSignal, read_event_log, read_usage_log
from thalamus.retrieval import CentralityWeightsRef, L0Retriever
from thalamus.routing import build_encoder
from thalamus.store import connect
from thalamus.structural import memory_centrality


@dataclass(frozen=True, slots=True)
class RungEvalConfig:
    serve: ServeConfig  # via serve_config — carries repo / data_dir / corpora / encoder / neo4j
    split: float  # 0.0 = no split (leaky); >0 = newest fraction is TEST, older trains usage weights
    usage_weight: float  # usage rung RRF strength (verdict: 1.0 over-promotes, hurts recall@k)


def add_rung_eval_arguments(parser: argparse.ArgumentParser) -> None:
    add_serve_arguments(parser)  # repo / data_dir / corpora / encoder / scip / k / neo4j-via-env …
    parser.add_argument(
        "--split", type=float, default=0.0,
        help="temporal split to de-leak the usage rung: the newest FRACTION of recalls become the "
        "TEST cases; usage weights are mined only from the OLDER remainder (0.0 = no split, leaky)",
    )
    parser.add_argument(
        "--usage-weight", type=float, default=1.0,
        help="usage rung RRF strength in +usage/+full (lower = gentler re-rank; 0 ablates it)",
    )


def rung_eval_config(args: argparse.Namespace) -> RungEvalConfig:
    return RungEvalConfig(
        serve=serve_config(args), split=float(args.split), usage_weight=float(args.usage_weight)
    )


def _encoder(serve: ServeConfig) -> Encoder:
    return build_encoder(serve.encoder, dim=serve.dim)


def _load_logs(data_dir: Path) -> tuple[list[RetrievalEvent], list[UsageSignal]]:
    """Retrieval events + usage signals from a brain's logs (explicit + attributed usage)."""
    logs = data_dir / ".thalamus" / "logs"
    retrieval = logs / "retrieval.jsonl"
    events = list(read_event_log(retrieval)) if retrieval.exists() else []
    signals: list[UsageSignal] = []
    for name in ("usage.jsonl", "usage_attributed.jsonl"):
        path = logs / name
        if path.exists():
            signals.extend(read_usage_log(path))
    return events, signals


def _temporal_split(
    events: list[RetrievalEvent], signals: list[UsageSignal], split: float
) -> tuple[list[RetrievalEvent], list[RetrievalEvent], list[UsageSignal], list[UsageSignal]]:
    """Partition by time into (train_events, test_events, train_signals, test_signals).

    De-leaks the usage rung: the TEST cases are the newest ``split`` fraction of *labeled* recalls
    (those with a used signal), and usage weights are mined from everything else (all other events),
    so a test recall's own usage never feeds the weight that ranks it. ``split <= 0`` → no split:
    train == test == all (the leaky default, where a memory's weight and its used-label share data).

    We split *labeled* events, not all events, because attributed usage is backward-looking — a
    recall is labeled "used" only once later commits touch its footprint, so the very newest recalls
    are unlabeled; splitting all events by time would put every label in train, test empty."""
    if split <= 0.0:
        return events, events, signals, signals
    used_ids = {signal.event_id for signal in signals if signal.used}
    labeled = sorted(
        (event for event in events if event.event_id in used_ids), key=lambda e: e.timestamp
    )
    cut = max(1, int(len(labeled) * (1.0 - split)))
    test_ids = {event.event_id for event in labeled[cut:]}  # newest labeled fraction = test
    test_events = [event for event in events if event.event_id in test_ids]
    train_events = [event for event in events if event.event_id not in test_ids]
    train_signals = [signal for signal in signals if signal.event_id not in test_ids]
    test_signals = [signal for signal in signals if signal.event_id in test_ids]
    return train_events, test_events, train_signals, test_signals


def _superseded(serve: ServeConfig, scope: Scope) -> dict[MemoryRef, Supersession]:
    """The current superseded frontier from durable state (empty without Neo4j)."""
    if serve.neo4j_uri is None:
        return {}
    driver = connect(serve.neo4j_uri, serve.neo4j_user, serve.neo4j_password or "")
    try:
        return dict(Neo4jSupersessionIndex(driver, scope).superseded(scope))
    finally:
        driver.close()


def _render(reports: dict[str, EvalReport], k: int, n_cases: int, split: float) -> str:
    mode = (
        f"temporal split: usage weights from the older recalls, tested on the newest {split:.0%}"
        if split > 0.0
        else "NO split (leaky: usage weights and labels share the same recalls)"
    )
    lines = [
        f"Thalamus rung-eval — utility join over {n_cases} used-memory case(s), k={k}",
        f"({mode})",
        "(label = the memory actually USED for each recall; higher = the rung ranks it better)",
        "",
        f"{'retriever':<20}  {'recall@k':>9}  {'mrr':>7}  {'hit@k':>7}",
    ]
    for name, report in reports.items():
        lines.append(
            f"{name:<20}  {report.recall_at_k:>9.3f}  {report.mrr:>7.3f}  {report.hit_rate:>7.3f}"
        )
    base = reports.get("brain-on")
    if base is not None:
        lines.append("")
        for name, report in reports.items():
            if name in ("brain-off", "brain-on"):
                continue
            lines.append(
                f"  lift({name} - brain-on) = {report.mrr - base.mrr:+.3f} mrr, "
                f"{report.recall_at_k - base.recall_at_k:+.3f} recall@k"
            )
    return "\n".join(lines)


def run_rung_eval(config: RungEvalConfig) -> None:
    """Build the rung arms over durable Brain 1+2, score them on the used-memory cases, report."""
    serve = config.serve
    scope = Scope(TenantId(serve.tenant), RepoId(serve.repo_id))
    events, signals = _load_logs(serve.data_dir or serve.repo)
    train_events, test_events, train_signals, test_signals = _temporal_split(
        events, signals, config.split
    )
    cases = cases_from_usage(test_events, test_signals)
    if not cases:
        raise ThalamusError(
            "no used-memory cases in the logs — need retrieval events joined to used:true usage "
            "signals (explicit record_usage or attributed). Nothing to validate the rungs against."
        )

    encoder = _encoder(serve)
    gateway, store, *_rest = build_serve_gateway(serve, encoder=encoder)
    try:
        graph, links = gateway.graph, gateway.links
        if graph is None or links is None:
            raise ThalamusError("rung-eval needs a Brain-2 graph (Neo4j); none was built")
        base = L0Retriever(encoder, store)
        superseded = _superseded(serve, scope)
        centrality = CentralityWeightsRef(
            memory_centrality([record.ref for record in store.scan(scope)], graph, links)
        )
        usage_weights: dict[MemoryRef | MemoryId, float] = {
            mid: float(n) for mid, n in reuse_by_memory(train_events, train_signals).items()
        }
        arms = compose_rung_arms(
            base, superseded, usage_weights=usage_weights, usage_weight=config.usage_weight,
            links=links, structural_retrievers=gateway.structural_retrievers, centrality=centrality,
        )
        print(_render(compare(arms, cases, serve.k), serve.k, len(cases), config.split))
    finally:
        close_store(store)
