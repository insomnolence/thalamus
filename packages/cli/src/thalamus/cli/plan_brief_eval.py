"""``thalamus plan-brief-eval`` — gotcha-case recall for the plan tool's GATHER (M-2).

Reads a JSON case file (targets + the pre-existing, human-judged-relevant context each *should*
surface), runs every case through the live planner over durable Brain 1+2, and reports the gather
recall — how often the brief actually surfaced the context the brain holds about in-scope code.

This is the gather-side companion to ``impact-eval`` (which measures the structural radius). The
honesty discipline lives in ``thalamus.eval.plan_brief``: a case is meaningful only over
pre-existing, human-judged-relevant content — otherwise it just measures that a cross-link
round-trips (the circular trap). Authoring a good case set is the ongoing measurement work; this
CLI is the runner.

Case file — a JSON list of objects::

    [
      {"name": "auth-gotcha", "target": "validateToken",
       "expect_memory_id": "retained:0d382ca086df6147"},
      {"name": "store-design", "target": "IUserIntegrationStore",
       "expect_text": "provider hardcoding", "hops": 2}
    ]

Each case needs ``target`` plus at least one of ``expect_memory_id`` (the full store id — NOT the
8-hex short form used in prose) or ``expect_text`` (a substring of the brief). ``hops`` is optional.
A target that names a code symbol resolves without the encoder (lexical resolution); an NL target
needs the brain's real ``--encoder``/``--dim`` (taken from ``thalamus.toml`` when present).
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path

from thalamus.core.types import RepoId, Scope, TenantId
from thalamus.eval import (
    BriefEvalReport,
    GotchaCase,
    PlanBriefView,
    evaluate_plan_briefs,
    view_from_brief,
)

_DEFAULT_DIM = 128
_DEFAULT_ENCODER = "bge-small"


@dataclass(frozen=True, slots=True)
class PlanBriefEvalConfig:
    repo: Path
    tenant: str
    repo_id: str
    dim: int
    encoder: str
    cases_path: Path
    hops: int
    plan_cochange_commits: int
    memory_budget: int
    neo4j_uri: str | None
    neo4j_user: str
    neo4j_password: str | None


def add_plan_brief_eval_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("cases", type=Path, help="path to a JSON gotcha-case file")
    parser.add_argument(
        "--repo", type=Path, default=Path.cwd(),
        help="repo whose brain is evaluated (default: cwd)",
    )
    parser.add_argument("--tenant", default="local", help="tenant id for scoping")
    parser.add_argument(
        "--repo-id", default=None, help="repo id for scoping (default: repo dir name)"
    )
    parser.add_argument("--dim", type=int, default=_DEFAULT_DIM, help="embedding dimensionality")
    parser.add_argument(
        "--encoder", choices=("bge-small", "deterministic"), default=_DEFAULT_ENCODER,
        help="embedding model (default: bge-small; symbol-name targets resolve without it)",
    )
    parser.add_argument("--hops", type=int, default=2, help="default blast-radius depth per case")
    parser.add_argument(
        "--memory-budget", type=int, default=30,
        help="memories a brief gathers — sweep this to measure the budget/recall tradeoff",
    )
    parser.add_argument(
        "--plan-cochange-commits", type=int, default=500,
        help="recent commits to mine for the co-change radius layer (0 = off; matches live `plan`)",
    )


def plan_brief_eval_config(args: argparse.Namespace) -> PlanBriefEvalConfig:
    repo = Path(args.repo).resolve()
    return PlanBriefEvalConfig(
        repo=repo,
        tenant=str(args.tenant),
        repo_id=str(args.repo_id) if args.repo_id else repo.name,
        dim=int(args.dim),
        encoder=str(args.encoder),
        cases_path=Path(args.cases),
        hops=int(args.hops),
        plan_cochange_commits=int(args.plan_cochange_commits),
        memory_budget=int(args.memory_budget),
        neo4j_uri=os.environ.get("THALAMUS_NEO4J_URI"),
        neo4j_user=os.environ.get("THALAMUS_NEO4J_USER", "neo4j"),
        neo4j_password=os.environ.get("THALAMUS_NEO4J_PASSWORD"),
    )


def _opt_str(value: object) -> str | None:
    """A non-empty string from a JSON value, else ``None``."""
    if isinstance(value, str):
        return value.strip() or None
    return None


def load_brief_cases(path: Path) -> list[GotchaCase]:
    """Parse a JSON gotcha-case file into :class:`GotchaCase`s (validating each entry)."""
    import json

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("cases file must be a JSON list of case objects")
    cases: list[GotchaCase] = []
    for index, entry in enumerate(data):
        if not isinstance(entry, dict) or "target" not in entry:
            raise ValueError(f"case {index} must be an object with a 'target'")
        raw_hops = entry.get("hops")
        cases.append(
            GotchaCase(
                name=str(entry.get("name", f"case-{index}")),
                target=str(entry["target"]),
                expect_memory_id=_opt_str(entry.get("expect_memory_id")),
                expect_text=_opt_str(entry.get("expect_text")),
                hops=int(raw_hops) if raw_hops is not None else None,
            )
        )
    return cases


def _render(report: BriefEvalReport) -> str:
    lines = [
        f"Thalamus plan-brief-eval — gather recall: "
        f"{report.covered}/{report.total} = {report.recall:.2f}",
        "",
    ]
    for result in report.results:
        mark = "HIT " if result.covered else "MISS"
        lines.append(f"  [{mark}] {result.name}: {result.detail}")
    if report.misses:
        lines += [
            "",
            "Misses are gather gaps to investigate: the case asserts pre-existing, relevant "
            "content the brief did not surface (linked elsewhere, ranked out by the budget, or — "
            "if mis-authored — not actually in the brain).",
        ]
    return "\n".join(lines) + "\n"


def run_plan_brief_eval(config: PlanBriefEvalConfig) -> None:
    """Build the live brain, run the case set through the planner, print the recall report."""
    # Lazy import: serve pulls heavy optional wiring; keep the CLI import-light until invoked.
    from thalamus.cli.brain import build_planner, close_store
    from thalamus.cli.cochange import build_file_cochange, recent_commit_shas
    from thalamus.cli.serve import ServeConfig, build_serve_gateway
    from thalamus.gateway import PlannerConfig

    cases = load_brief_cases(config.cases_path)
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
    gateway, store, *_rest = build_serve_gateway(serve_config)
    scope = Scope(TenantId(config.tenant), RepoId(config.repo_id))
    try:
        # Mine the co-change radius layer the live `plan` tool uses, so the eval measures the same
        # radius (not a weaker call-graph-only one). Skipped without a graph or when disabled.
        cochange = None
        if config.plan_cochange_commits > 0 and gateway.graph is not None:
            cochange = build_file_cochange(
                config.repo,
                gateway.graph,
                scope,
                recent_commit_shas(config.repo, config.plan_cochange_commits),
            )
        planner = build_planner(
            gateway, store, cochange=cochange,
            config=PlannerConfig(memory_budget=config.memory_budget),
        )
        if planner is None:
            print("plan-brief-eval: experiential-only brain — no planner to evaluate")
            return

        def resolve(case: GotchaCase) -> PlanBriefView:
            hops = case.hops if case.hops is not None else config.hops
            return view_from_brief(planner.plan(target=case.target, scope=scope, hops=hops))

        print(_render(evaluate_plan_briefs(cases, resolve)))
    finally:
        close_store(store)
