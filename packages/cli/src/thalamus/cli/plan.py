"""``thalamus plan`` — the blast-radius brief for a target, on the command line.

The same brief the ``plan`` MCP tool hands an agent, printed to stdout. This exists so the
flagship capability can be seen (and reported against) without standing up an MCP client:
one command, one target, the rendered brief.

It deliberately reuses :func:`thalamus.cli.serve.build_plan_reader` — the *identical* backend the
MCP tool calls — so the CLI and the served tool cannot drift into showing different things.

Read-only against the brain: it resolves the target, walks the blast radius, gathers the recorded
why, and renders. No memory is written and no session is opened — but the gather *is* logged to
``.thalamus/logs/plan.jsonl``, exactly as the served tool logs it, so a CLI brief is not a
telemetry blind spot.

Like the served tool, it mines the co-change radius layer from recent commits
(``--plan-cochange-commits``, default 500, matching ``serve``) so the CLI brief is the *full* one,
not a weaker call-graph-only variant.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path

from thalamus.core.types import RepoId, Scope, TenantId
from thalamus.routing import ENCODER_NAMES

_DEFAULT_DIM = 128
_DEFAULT_ENCODER = "bge-small"


@dataclass(frozen=True, slots=True)
class PlanConfig:
    target: str
    repo: Path
    tenant: str
    repo_id: str
    dim: int
    encoder: str
    hops: int
    plan_cochange_commits: int
    neo4j_uri: str | None
    neo4j_user: str
    neo4j_password: str | None


def add_plan_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "target",
        help="a symbol name or a short description of what you're about to change",
    )
    parser.add_argument(
        "--repo", type=Path, default=Path.cwd(), help="repo whose brain is queried (default: cwd)"
    )
    parser.add_argument("--tenant", default="local", help="tenant id for scoping")
    parser.add_argument(
        "--repo-id", default=None, help="repo id for scoping (default: repo dir name)"
    )
    parser.add_argument("--dim", type=int, default=_DEFAULT_DIM, help="embedding dimensionality")
    parser.add_argument(
        "--encoder", choices=ENCODER_NAMES, default=_DEFAULT_ENCODER,
        help="embedding model (default: bge-small; symbol-name targets resolve without it)",
    )
    parser.add_argument("--hops", type=int, default=2, help="blast-radius depth bound")
    parser.add_argument(
        "--plan-cochange-commits", type=int, default=500,
        help="recent commits to mine for the co-change radius layer (0 = off; matches `serve`)",
    )


def plan_config(args: argparse.Namespace) -> PlanConfig:
    repo = Path(args.repo).resolve()
    return PlanConfig(
        target=str(args.target),
        repo=repo,
        tenant=str(args.tenant),
        repo_id=str(args.repo_id) if args.repo_id else repo.name,
        dim=int(args.dim),
        encoder=str(args.encoder),
        hops=int(args.hops),
        plan_cochange_commits=int(args.plan_cochange_commits),
        neo4j_uri=os.environ.get("THALAMUS_NEO4J_URI"),
        neo4j_user=os.environ.get("THALAMUS_NEO4J_USER", "neo4j"),
        neo4j_password=os.environ.get("THALAMUS_NEO4J_PASSWORD"),
    )


def run_plan(config: PlanConfig) -> int:
    """Build the brain, render one plan brief to stdout. Returns a process exit code."""
    # Lazy import: serve pulls heavy optional wiring; keep the CLI import-light until invoked.
    from thalamus.cli.brain import build_planner, close_store
    from thalamus.cli.cochange import build_file_cochange, recent_commit_shas
    from thalamus.cli.serve import ServeConfig, build_plan_reader, build_serve_gateway
    from thalamus.instrumentation import JsonlEventSink

    serve_config = ServeConfig(
        repo=config.repo,
        tenant=config.tenant,
        repo_id=config.repo_id,
        dim=config.dim,
        encoder=config.encoder,
        k=5,
        k_hop=1,
        resolve_calls=True,
        structural_min_relevance=0.6,
        max_structural_items=12,
        max_memory_chars=1000,
        neo4j_uri=config.neo4j_uri,
        neo4j_user=config.neo4j_user,
        neo4j_password=config.neo4j_password,
        session=False,
    )
    (
        gateway,
        store,
        _episodes,
        _supersession,
        _rederive,
        _attribution_refresh,
        _behavioral_consolidation,
        _usage_refresh,
        _centrality_refresh,
        usage_ref,
    ) = build_serve_gateway(serve_config)
    scope = Scope(TenantId(config.tenant), RepoId(config.repo_id))
    try:
        # Mirror the served tool: mine the same co-change layer, so the printed brief is the one
        # an agent would actually receive.
        cochange = None
        if config.plan_cochange_commits > 0 and gateway.graph is not None:
            cochange = build_file_cochange(
                config.repo,
                gateway.graph,
                scope,
                recent_commit_shas(config.repo, config.plan_cochange_commits),
            )
        # Log the gather like the served tool does — a CLI brief hands the same memories to a
        # reader, so it belongs in the same telemetry rather than being a blind spot.
        logs = config.repo / ".thalamus" / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        planner = build_planner(
            gateway,
            store,
            cochange=cochange,
            usage_weights=usage_ref,
            event_sink=JsonlEventSink(logs / "plan.jsonl"),
        )
        if planner is None:
            print(
                "plan: this brain has no structural hemisphere, so there is no blast radius to "
                "compute. Point `--repo` at a repo whose code corpus has been built (see "
                "`serve --help`), or use `recall` for experiential-only context."
            )
            return 1
        print(build_plan_reader(planner, scope)(config.target, config.hops))
    finally:
        close_store(store)
    return 0
