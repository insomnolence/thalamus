"""Plan-brief gotcha-case recall — an L1.5 instrument for the GATHER, not the radius (M-2).

The plan tool has two halves. The structural **blast radius** is measured non-circularly by
``impact.py`` (git-derived coupling). This module measures the other half — the experiential
**gather**: when you plan a target, does the brief actually surface the decision / constraint /
gotcha / finding the brain already holds about the in-scope code?

THE CIRCULARITY HAZARD — read before authoring cases. The trap ``impact.py`` names applies here in
full: author a gotcha, cross-link it to the target, then "verify" the brief surfaces it. That
measures only that the link round-trips through the gather — which the planner unit tests already
prove — and tells you NOTHING about the brain's real usefulness. It is the self-validation
trap in eval clothing (§13.7 / §14.2).

A gotcha-case is honest only if BOTH hold:
  1. the expected memory is **pre-existing brain content**, recorded in the ordinary course of
     work — NOT authored or linked for the test; and
  2. its relevance to the target is judged by a **human** (or an independent signal), not by the
     cross-link we are measuring.
Then a MISS is real signal: the brain HOLDS the relevant knowledge, but the gather (cross-link
granularity, module rollup, ranking, the memory budget) failed to surface it — a coverage gap
worth fixing. A HIT means the pipeline delivered it.

This is the pure, dependency-light core: the case/result/report types and the scorer over a
caller-supplied ``resolve`` (which runs the real planner and adapts its brief — kept out of here so
``eval`` stays free of a ``gateway`` dependency, mirroring ``impact.py``). Authoring the honest case
set against the live brain is the ongoing measurement work, exactly as impact-eval's pairs come from
real git history.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class GotchaCase:
    """One gotcha-case: planning ``target`` *should* surface a known-relevant piece of context.

    Provide ``expect_memory_id`` (precise — a pre-existing memory / finding id the brief should
    surface) and/or ``expect_text`` (a substring the rendered brief should contain). At least one
    is required; ``expect_memory_id`` is the stronger, less gameable signal.
    """

    name: str
    target: str
    expect_memory_id: str | None = None
    expect_text: str | None = None
    hops: int | None = None  # optional per-case radius depth override

    def __post_init__(self) -> None:
        if self.expect_memory_id is None and self.expect_text is None:
            raise ValueError(f"gotcha-case {self.name!r} needs expect_memory_id or expect_text")


@dataclass(frozen=True, slots=True)
class PlanBriefView:
    """The slice of a plan brief a gotcha-case checks — decouples the eval from the gateway.

    ``surfaced_ids`` are the ids the brief actually surfaced (gathered memory ids + finding node
    ids); ``rendered`` is the brief's text. Build one with :func:`view_from_brief`.
    """

    surfaced_ids: frozenset[str]
    rendered: str


def view_from_brief(brief: Any) -> PlanBriefView:
    """Adapt a ``PlanBrief`` to a :class:`PlanBriefView` (duck-typed, so ``eval`` needs no gateway).

    Collects the gathered memory ids (constraints + context) and finding node ids — the things a
    gotcha-case asserts should be present — plus the rendered text for substring expectations."""
    ids = {str(item.memory_id) for item in (*brief.constraints, *brief.context)}
    ids |= {str(finding.node_id) for finding in brief.findings}
    return PlanBriefView(surfaced_ids=frozenset(ids), rendered=brief.render())


@dataclass(frozen=True, slots=True)
class BriefCaseResult:
    """The outcome of one gotcha-case."""

    name: str
    covered: bool
    detail: str


@dataclass(frozen=True, slots=True)
class BriefEvalReport:
    """Aggregate gotcha-case recall — how often the gather surfaced the known-relevant context."""

    results: tuple[BriefCaseResult, ...]

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def covered(self) -> int:
        return sum(1 for result in self.results if result.covered)

    @property
    def recall(self) -> float:
        """Fraction of cases whose expected context the brief surfaced (0.0 when empty)."""
        return self.covered / self.total if self.results else 0.0

    @property
    def misses(self) -> tuple[BriefCaseResult, ...]:
        """The cases the gather failed — the actionable coverage gaps."""
        return tuple(result for result in self.results if not result.covered)


def score_case(case: GotchaCase, view: PlanBriefView) -> BriefCaseResult:
    """Score one case against the brief produced for its target. Pure."""
    if case.expect_memory_id is not None and case.expect_memory_id in view.surfaced_ids:
        return BriefCaseResult(case.name, True, f"surfaced {case.expect_memory_id}")
    if case.expect_text is not None and case.expect_text in view.rendered:
        return BriefCaseResult(case.name, True, "expected text present in brief")
    if case.expect_memory_id is not None:
        # Honest miss: the case asserts this is real, relevant, pre-existing content; the brief did
        # not surface it. Either a gather gap (linked elsewhere / ranked out by budget) or — if the
        # case is mis-authored — the memory isn't in the brain. The case author owns that.
        return BriefCaseResult(case.name, False, f"{case.expect_memory_id} not surfaced")
    return BriefCaseResult(case.name, False, "expected text absent from brief")


def evaluate_plan_briefs(
    cases: Iterable[GotchaCase],
    resolve: Callable[[GotchaCase], PlanBriefView],
) -> BriefEvalReport:
    """Run each case through ``resolve`` (caller runs the real planner + adapts) and score it.

    ``resolve`` is supplied by the caller so this module stays dependency-light: a CLI wires it to
    ``planner.plan(target=case.target, scope=…, hops=case.hops)`` followed by
    :func:`view_from_brief`. Tests pass a fake ``resolve`` to exercise the scoring."""
    results: Sequence[BriefCaseResult] = [score_case(case, resolve(case)) for case in cases]
    return BriefEvalReport(tuple(results))


__all__ = [
    "BriefCaseResult",
    "BriefEvalReport",
    "GotchaCase",
    "PlanBriefView",
    "evaluate_plan_briefs",
    "score_case",
    "view_from_brief",
]
