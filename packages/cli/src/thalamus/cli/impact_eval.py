"""`impact-eval` — git-derived blast-radius recall for the plan tool (the honest non-circular eval).

Composition + git archaeology around the pure scorer in :mod:`thalamus.eval.impact`:

1. Re-derive the repo's **code graph** in memory (Python AST + jedi `calls` edges) — no Neo4j,
   no memories: blast radius is a function of the call graph alone.
2. Index code symbols by ``file → (line_start, line_end, ref)`` from the graph's own anchors.
3. Mine **fix commits** from git; for each, map its changed lines to the symbols it touched and
   emit the co-changed-symbol pairs (real coupling, authored by git, not by us).
4. Score: for each pair, is the coupled symbol in the **target's blast radius**? Recall, with a
   cross-file cut (the "forest" cases) and a high-fan-out attribution for honest miss accounting.

Read-only; safe to run anywhere there's a git repo + Python source. Caveat it reports: changed
lines are mapped against HEAD's anchors, so older fix commits drift — mining is bounded to recent
commits to keep the mapping tight.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from thalamus.cli.brain import build_code_graph
from thalamus.cli.cochange import build_file_cochange, code_globs, git_output, rel_path
from thalamus.core.types import RepoId, Scope, StructuralRef, TenantId
from thalamus.eval.impact import (
    ImpactPair,
    ImpactReport,
    evaluate_impact,
    map_changes_to_refs,
    parse_changed_lines,
)
from thalamus.gateway import Planner, PlannerConfig
from thalamus.gateway.views import DerivedViewsRef
from thalamus.store import InMemoryStore
from thalamus.structural import (
    CoChangeIndex,
    InMemoryCoChangeIndex,
    InMemoryCrossLinkIndex,
    StructuralGraph,
)

_SYMBOL_KINDS = ("function", "method", "class", "interface", "enum")  # last two: TS types
# Subjects that signal a repair — the strongest "this code needed fixing" git signal.
_FIX_TERMS = ("fix", "bug", "revert", "regression", "broke", "oops", "hotfix", "incorrect")


@dataclass(frozen=True, slots=True)
class ImpactEvalConfig:
    repo: Path
    tenant: str
    repo_id: str
    code_language: str  # python (AST) | typescript (needs a prebuilt --scip-index)
    scip_index: Path | None
    hops: int
    fanout_threshold: int
    max_commits: int  # how many recent fix commits to mine (recent ⇒ tight line mapping)
    max_symbols_per_commit: int  # skip sprawling refactors (not a focused coupling signal)
    cochange_commits: int  # 0 = call-only; >0 = build co-change from this many OLDER commits
    cochange_min_support: int  # min historical co-changes to count a pair as coupled
    cochange_mode: str  # "file" (drift-immune, proven) | "symbol" (drift-starved scaffold)
    cochange_max_nodes: int  # cap on co-change nodes folded into the radius


def add_impact_eval_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--repo", type=Path, default=Path.cwd(), help="repo to evaluate (default: cwd)"
    )
    parser.add_argument("--tenant", default="local", help="tenant id for scoping")
    parser.add_argument("--repo-id", default=None, help="repo id (default: repo dir name)")
    parser.add_argument(
        "--code-language", choices=("python", "typescript"), default="python",
        help="code corpus language (typescript needs a prebuilt --scip-index)",
    )
    parser.add_argument(
        "--scip-index", type=Path, default=None,
        help="prebuilt .scip index (required for --code-language typescript)",
    )
    parser.add_argument("--hops", type=int, default=2, help="blast-radius depth bound")
    parser.add_argument(
        "--fanout-threshold", type=int, default=25, help="hub circuit-breaker caller degree"
    )
    parser.add_argument(
        "--max-commits", type=int, default=200, help="recent fix commits to mine (default 200)"
    )
    parser.add_argument(
        "--max-symbols-per-commit", type=int, default=8,
        help="skip fix commits touching more symbols than this (sprawling = noisy)",
    )
    parser.add_argument(
        "--cochange-commits", type=int, default=0,
        help="0 = call-only; >0 measures the co-change lift, building the index from this many "
        "commits OLDER than the test fix commits (temporal split, no leakage)",
    )
    parser.add_argument(
        "--cochange-min-support", type=int, default=2,
        help="min historical co-changes for a pair to count as coupled (default 2)",
    )
    parser.add_argument(
        "--cochange-mode", choices=("file", "symbol"), default="file",
        help="file = drift-immune file co-change (proven); symbol = the drift-starved scaffold",
    )
    parser.add_argument(
        "--cochange-max-nodes", type=int, default=15,
        help="max co-change nodes folded into the radius (default 15)",
    )


def impact_eval_config(args: argparse.Namespace) -> ImpactEvalConfig:
    repo = Path(args.repo).resolve()
    return ImpactEvalConfig(
        repo=repo,
        tenant=str(args.tenant),
        repo_id=str(args.repo_id) if args.repo_id else repo.name,
        code_language=str(args.code_language),
        scip_index=Path(args.scip_index).resolve() if args.scip_index else None,
        hops=int(args.hops),
        fanout_threshold=int(args.fanout_threshold),
        max_commits=int(args.max_commits),
        max_symbols_per_commit=int(args.max_symbols_per_commit),
        cochange_commits=int(args.cochange_commits),
        cochange_min_support=int(args.cochange_min_support),
        cochange_mode=str(args.cochange_mode),
        cochange_max_nodes=int(args.cochange_max_nodes),
    )


def _symbol_index(
    graph: StructuralGraph, scope: Scope, repo: Path
) -> tuple[dict[str, list[tuple[int, int, StructuralRef]]], dict[StructuralRef, str]]:
    """``{file: [(start, end, ref)]}`` over code symbols, plus a ``ref → file`` map."""
    index: dict[str, list[tuple[int, int, StructuralRef]]] = {}
    ref_path: dict[StructuralRef, str] = {}
    for kind in _SYMBOL_KINDS:
        for node in graph.nodes_of_kind(scope, kind):
            if node.anchor is None:
                continue
            path = rel_path(node.anchor.path, repo)
            entry = (node.anchor.line_start, node.anchor.line_end, node.ref)
            index.setdefault(path, []).append(entry)
            ref_path[node.ref] = path
    return index, ref_path


def _is_fix(subject: str) -> bool:
    lowered = subject.lower()
    return any(term in lowered for term in _FIX_TERMS)


def _commit_log(repo: Path, n: int) -> list[tuple[str, str]]:
    """Up to ``n`` commits, newest first, as ``(sha, subject)`` pairs."""
    out = git_output(repo, "log", "--no-merges", "--format=%H%x1f%s", f"-n{n}")
    if not out:
        return []
    rows: list[tuple[str, str]] = []
    for line in out.splitlines():
        sha, _, subject = line.partition("\x1f")
        if sha.strip():
            rows.append((sha.strip(), subject))
    return rows


def _touched_symbols(
    repo: Path,
    sha: str,
    index: dict[str, list[tuple[int, int, StructuralRef]]],
    globs: tuple[str, ...],
    max_symbols: int,
) -> list[StructuralRef] | None:
    """Symbols a commit changed, mapped via anchors. ``None`` if not a usable coupling commit
    (fewer than 2 symbols, or a sprawling refactor over ``max_symbols``)."""
    diff = git_output(repo, "show", "--unified=0", "--format=", sha, "--", *globs)
    if not diff:
        return None
    touched = map_changes_to_refs(parse_changed_lines(diff), index)
    if len(touched) < 2 or len(touched) > max_symbols:
        return None
    return sorted(touched, key=lambda r: r.node_id)


def _pairs_from(
    repo: Path,
    shas: list[str],
    index: dict[str, list[tuple[int, int, StructuralRef]]],
    ref_path: dict[StructuralRef, str],
    globs: tuple[str, ...],
    max_symbols: int,
) -> tuple[list[ImpactPair], int]:
    """The eval set: every co-changed-in-a-fix symbol pair, deduped → ``(pairs, n_used)``."""
    pairs: dict[tuple[StructuralRef, StructuralRef], ImpactPair] = {}
    used = 0
    for sha in shas:
        touched = _touched_symbols(repo, sha, index, globs, max_symbols)
        if touched is None:
            continue
        used += 1
        for target in touched:
            for coupled in touched:
                if target == coupled or (target, coupled) in pairs:
                    continue
                pairs[(target, coupled)] = ImpactPair(
                    target=target,
                    coupled=coupled,
                    source_sha=sha,
                    same_file=ref_path.get(target) == ref_path.get(coupled),
                )
    return list(pairs.values()), used


def _cochange_from(
    repo: Path,
    shas: list[str],
    index: dict[str, list[tuple[int, int, StructuralRef]]],
    globs: tuple[str, ...],
    max_symbols: int,
) -> InMemoryCoChangeIndex:
    """Symbol-level co-change from training commits (the drift-starved scaffold)."""
    cochange = InMemoryCoChangeIndex()
    for sha in shas:
        touched = _touched_symbols(repo, sha, index, globs, max_symbols)
        if touched is not None:
            cochange.add_commit(touched)
    return cochange


def _build_planner(
    graph: StructuralGraph, cfg: ImpactEvalConfig, cochange: CoChangeIndex | None = None
) -> Planner:
    """A radius-only planner: real graph, empty links/store/retrievers (the eval calls
    ``blast_radius_refs`` directly, bypassing resolution + gather)."""
    return Planner(
        graph=graph,
        links=InMemoryCrossLinkIndex(),
        store=InMemoryStore(dim=1),
        structural_retrievers=[],
        views=DerivedViewsRef(),
        cochange=cochange,
        config=PlannerConfig(
            hops=cfg.hops,
            fanout_threshold=cfg.fanout_threshold,
            cochange_min_support=cfg.cochange_min_support,
            cochange_max_nodes=cfg.cochange_max_nodes,
        ),
    )


def _metrics(report: ImpactReport) -> list[str]:
    return [
        f"  recall:            {report.recovered}/{report.n_pairs} = {report.recall:.3f}",
        f"  cross-file recall: {report.recovered_cross_file}/{report.n_cross_file} "
        f"= {report.cross_file_recall:.3f}   (the 'forest' cases a local LLM is blind to)",
    ]


def _render_single(report: ImpactReport, n_commits: int) -> str:
    lines = [
        "# Impact eval — git-derived blast-radius recall (call-graph only)",
        "",
        f"Mined {report.n_pairs} pair(s) from {n_commits} fix commit(s); hops={report.hops}.",
        "",
        *_metrics(report),
        f"  high-fan-out targets: {report.n_target_high_fanout};  "
        f"empty-radius targets: {report.n_target_empty_radius}/{report.n_pairs} "
        "(0 = graph not starved → misses are real)",
    ]
    if report.n_pairs == 0:
        lines += ["", "(No pairs mined — too few multi-symbol fix commits, or no `calls` edges.)"]
    return "\n".join(lines) + "\n"


def _render_lift(
    base: ImpactReport, treat: ImpactReport, n_test: int, n_train: int, n_keys: int, mode: str
) -> str:
    d_all = treat.recall - base.recall
    d_cross = treat.cross_file_recall - base.cross_file_recall
    unit = "file" if mode == "file" else "symbol"
    return "\n".join(
        [
            f"# Impact eval — {mode}-level co-change lift (temporal split, no leakage)",
            "",
            f"Test: {base.n_pairs} pair(s) from {n_test} recent fix commit(s). "
            f"Train: co-change over {n_keys} {unit}(s) from {n_train} OLDER commit(s). "
            f"hops={base.hops}.",
            "",
            "call-graph only:",
            *_metrics(base),
            "",
            "call-graph + co-change:",
            *_metrics(treat),
            "",
            f"LIFT: recall {base.recall:.3f} → {treat.recall:.3f} ({d_all:+.3f});  "
            f"cross-file {base.cross_file_recall:.3f} → {treat.cross_file_recall:.3f} "
            f"({d_cross:+.3f})",
        ]
    ) + "\n"


def run_impact_eval(cfg: ImpactEvalConfig) -> ImpactReport:
    scope = Scope(TenantId(cfg.tenant), RepoId(cfg.repo_id))
    graph, _nodes = build_code_graph(
        cfg.repo, scope, resolve_calls=True,
        code_language=cfg.code_language, scip_index=cfg.scip_index,
    )
    index, ref_path = _symbol_index(graph, scope, cfg.repo)
    globs = code_globs(cfg.code_language)

    if cfg.cochange_commits <= 0:  # call-only mode
        shas = [sha for sha, subj in _commit_log(cfg.repo, cfg.max_commits) if _is_fix(subj)]
        pairs, used = _pairs_from(
            cfg.repo, shas, index, ref_path, globs, cfg.max_symbols_per_commit
        )
        report = evaluate_impact(_build_planner(graph, cfg), pairs, hops=cfg.hops)
        print(_render_single(report, used))
        return report

    # Temporal split: TEST = recent fix commits; TRAIN = the older commits before them (no overlap),
    # so the co-change index can never have seen a test commit — the prediction is honest.
    log = _commit_log(cfg.repo, cfg.max_commits + cfg.cochange_commits)
    test_shas = [sha for sha, subj in log[: cfg.max_commits] if _is_fix(subj)]
    train_shas = [sha for sha, _ in log[cfg.max_commits :]]
    pairs, used = _pairs_from(
        cfg.repo, test_shas, index, ref_path, globs, cfg.max_symbols_per_commit
    )
    cochange = (
        build_file_cochange(cfg.repo, graph, scope, train_shas, code_language=cfg.code_language)
        if cfg.cochange_mode == "file"
        else _cochange_from(cfg.repo, train_shas, index, globs, cfg.max_symbols_per_commit)
    )

    base = evaluate_impact(_build_planner(graph, cfg), pairs, hops=cfg.hops)
    treat = evaluate_impact(_build_planner(graph, cfg, cochange), pairs, hops=cfg.hops)
    print(_render_lift(base, treat, used, len(train_shas), len(cochange), cfg.cochange_mode))
    return treat
