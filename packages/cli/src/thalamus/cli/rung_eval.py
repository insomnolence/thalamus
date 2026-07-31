"""``thalamus rung-eval`` — utility-join ablation: do the rungs rank the USED memory higher?

The honest validation of the relevance rungs (L-R1 usage, L-R2 structural relevance / centrality).
``probe-eval --rungs`` measures top-1 *cosine*, which saturates and is biased against re-rankers;
this measures the right thing: take each past recall's cue → the memory actually USED for it
(behavioral label, from the usage / attribution logs), re-run the cue through each rung, and report
**recall@k / MRR / hit@k of the used memory** per arm. A rung earns its place only if it ranks the
used memory higher than brain-on does. ``--split`` de-leaks the usage rung (weights from the older
events, tested on the newest fraction). ``--label-kind declared`` selects the clean actuator label;
``--source plan`` keeps the plan population separate and reports the one-sided graph-delivery cases
that flat recall misses. Offline (reads durable Neo4j + the logs); no serve needed.

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
from thalamus.core.protocols import Encoder, Retriever
from thalamus.core.types import MemoryId, MemoryRef, RepoId, Scope, Supersession, TenantId
from thalamus.eval import BenchmarkCase, EvalReport, cases_from_usage, compare
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
    source: str  # recall or plan — their event populations must stay separate
    label_kind: str  # all (legacy) or one explicit UsageSignal.kind


@dataclass(frozen=True, slots=True)
class LabelSummary:
    """Coverage of the selected behavioral label, including explicit all-negative events."""

    labeled_events: int
    used_events: int
    no_use_events: int


def add_rung_eval_arguments(parser: argparse.ArgumentParser) -> None:
    add_serve_arguments(parser)  # repo / data_dir / corpora / encoder / scip / k / neo4j-via-env …
    parser.add_argument(
        "--split", type=float, default=0.0,
        help="temporal split to de-leak the usage rung: the newest FRACTION of selected events "
        "become the TEST cases; usage weights are mined only from the OLDER remainder "
        "(0.0 = no split, leaky)",
    )
    parser.add_argument(
        "--usage-weight", type=float, default=1.0,
        help="usage rung RRF strength in +usage/+full (lower = gentler re-rank; 0 ablates it)",
    )
    parser.add_argument(
        "--source", choices=("recall", "plan"), default="recall",
        help="event stream to evaluate (default: recall; plan telemetry stays a separate "
        "population)",
    )
    parser.add_argument(
        "--label-kind",
        choices=("all", "declared", "citation", "footprint"),
        default="all",
        help="usage label to score (default: all, preserving legacy behavior; use declared for the "
        "normal-use flat-vs-graph test)",
    )


def rung_eval_config(args: argparse.Namespace) -> RungEvalConfig:
    return RungEvalConfig(
        serve=serve_config(args),
        split=float(args.split),
        usage_weight=float(args.usage_weight),
        source=str(args.source),
        label_kind=str(args.label_kind),
    )


def _encoder(serve: ServeConfig) -> Encoder:
    return build_encoder(serve.encoder, dim=serve.dim)


def _load_logs(
    data_dir: Path, *, source: str = "recall", label_kind: str = "all"
) -> tuple[list[RetrievalEvent], list[UsageSignal]]:
    """One event population plus its selected usage labels.

    Recall and plan stay separate because their budgets and selection mechanisms differ.  Usage
    signals share one append-only stream and are joined back to the selected events by ``event_id``.
    """
    logs = data_dir / ".thalamus" / "logs"
    event_log = logs / ("retrieval.jsonl" if source == "recall" else "plan.jsonl")
    events = list(read_event_log(event_log)) if event_log.exists() else []
    signals: list[UsageSignal] = []
    for name in ("usage.jsonl", "usage_attributed.jsonl"):
        path = logs / name
        if path.exists():
            signals.extend(read_usage_log(path))
    if label_kind != "all":
        signals = [signal for signal in signals if signal.kind == label_kind]
    event_ids = {event.event_id for event in events}
    signals = [signal for signal in signals if signal.event_id in event_ids]
    return events, signals


def _label_summary(
    events: list[RetrievalEvent], signals: list[UsageSignal]
) -> LabelSummary:
    """Count selected-label events, retaining ``used=False`` as evidence rather than dropping it."""
    source_ids = {event.event_id for event in events}
    labeled = {signal.event_id for signal in signals if signal.event_id in source_ids}
    used = {
        signal.event_id
        for signal in signals
        if signal.event_id in source_ids and signal.used
    }
    return LabelSummary(
        labeled_events=len(labeled),
        used_events=len(used),
        no_use_events=len(labeled - used),
    )


def _plan_delivery_misses(
    flat: Retriever, cases: list[BenchmarkCase], k: int
) -> tuple[int, int]:
    """Count plan-delivered, declared-used memories absent from a current flat replay.

    This is deliberately one-sided: the label is conditioned on the plan having surfaced the
    memory, so it can demonstrate graph delivery that flat retrieval missed but cannot observe a
    useful flat-only memory the plan never showed.
    """
    missed = 0
    total = 0
    for case in cases:
        result = flat.retrieve(case.cue, k)
        shown = {item.record.memory_id for item in result.shown}
        missed += len(case.relevant - shown)
        total += len(case.relevant)
    return missed, total


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


def _render(
    reports: dict[str, EvalReport],
    k: int,
    n_cases: int,
    split: float,
    *,
    source: str = "recall",
    label_kind: str = "all",
    labels: LabelSummary | None = None,
    plan_delivery: tuple[int, int] | None = None,
) -> str:
    mode = (
        f"temporal split: usage weights from older {source} events, "
        f"tested on the newest {split:.0%}"
        if split > 0.0
        else f"NO split (leaky: usage weights and labels share the same {source} events)"
    )
    lines = [
        f"Thalamus rung-eval — {source} utility join over {n_cases} used-memory case(s), k={k}",
        f"({mode})",
        f"(label kind = {label_kind}; higher = the rung ranks the used memory better)",
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
    if labels is not None:
        lines.extend(
            [
                "",
                f"  labeled {source} events = {labels.labeled_events}: "
                f"{labels.used_events} used context, {labels.no_use_events} none used",
            ]
        )
    if plan_delivery is not None:
        missed, total = plan_delivery
        lines.extend(
            [
                f"  one-sided graph delivery = {missed}/{total} declared-used plan "
                "memory instance(s) absent from flat brain-on top-k",
                "  (conditioned on plan having shown the memory; not a symmetric or causal win)",
            ]
        )
    return "\n".join(lines)


def run_rung_eval(config: RungEvalConfig) -> None:
    """Build the rung arms over durable Brain 1+2, score them on the used-memory cases, report."""
    serve = config.serve
    scope = Scope(TenantId(serve.tenant), RepoId(serve.repo_id))
    events, signals = _load_logs(
        serve.data_dir or serve.repo, source=config.source, label_kind=config.label_kind
    )
    labels = _label_summary(events, signals)
    train_events, test_events, train_signals, test_signals = _temporal_split(
        events, signals, config.split
    )
    cases = cases_from_usage(test_events, test_signals)
    if not cases:
        detail = (
            f"{labels.labeled_events} selected-label event(s) exist but none declared a used memory"
            if labels.labeled_events
            else "no events carry the selected label"
        )
        raise ThalamusError(
            f"no used-memory cases in the {config.source} logs for label kind "
            f"{config.label_kind!r} — {detail}. Nothing to validate the rungs against."
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
        plan_delivery = (
            _plan_delivery_misses(arms["brain-on"], cases, serve.k)
            if config.source == "plan"
            else None
        )
        print(
            _render(
                compare(arms, cases, serve.k),
                serve.k,
                len(cases),
                config.split,
                source=config.source,
                label_kind=config.label_kind,
                labels=labels,
                plan_delivery=plan_delivery,
            )
        )
    finally:
        close_store(store)
