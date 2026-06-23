"""M-1a statistics — the pre-committed analysis (pure Python, no SciPy).

Unit of analysis is the CASE (trials are repeated measures). The primary contrast is
``full − content_ablation`` over the positive set; success additionally requires the
positive effect to clear the negative-control null and the salience null. See
``docs/eval/m1a_preregistration.md`` §"Success criteria".

This implements the core, decision-relevant statistics: per-case δ, a distribution
-free sign test, and a seeded cluster bootstrap CI on the mean δ. The pre-registration
also lists Wilcoxon signed-rank, a hierarchical Beta-Binomial posterior, and anytime
-valid (e-process) CIs — those are additive refinements for the final frozen run and
are intentionally NOT yet implemented (a run that needs them must add them first).
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass
from math import comb

from thalamus.eval.m1a.harness import CaseResult


def _deltas(results: Sequence[CaseResult], better: str, worse: str) -> list[float]:
    return [r.avoid_rate(better) - r.avoid_rate(worse) for r in results]


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def sign_test_p(deltas: Sequence[float]) -> float:
    """Two-sided exact sign test p-value on sign(δ); zeros are dropped (no info)."""
    pos = sum(1 for d in deltas if d > 0)
    neg = sum(1 for d in deltas if d < 0)
    n = pos + neg
    if n == 0:
        return 1.0
    k = min(pos, neg)
    tail = sum(comb(n, i) for i in range(k + 1)) / (2.0**n)
    return min(1.0, 2.0 * tail)


def bootstrap_ci(
    values: Sequence[float], *, iters: int = 5000, alpha: float = 0.10, seed: int = 0
) -> tuple[float, float]:
    """Seeded percentile bootstrap CI on the mean (cluster = resample cases)."""
    if not values:
        return (0.0, 0.0)
    rng = random.Random(seed)
    n = len(values)
    means = sorted(mean([values[rng.randrange(n)] for _ in range(n)]) for _ in range(iters))
    lo = means[int((alpha / 2) * iters)]
    hi = means[min(iters - 1, int((1 - alpha / 2) * iters))]
    return (lo, hi)


@dataclass(frozen=True)
class Contrast:
    label: str
    n_cases: int
    mean_delta: float
    ci_low: float
    ci_high: float
    sign_p: float
    per_case: tuple[float, ...]


@dataclass(frozen=True)
class M1aReport:
    primary: Contrast  # full - content_ablation (positive set)
    salience: Contrast  # full - salience (positive set)
    bundle: Contrast  # full - off (positive set)
    negative_control_delta: float  # mean(full - content_ablation) over the control set
    criteria: dict[str, bool]

    @property
    def passes(self) -> bool:
        return all(self.criteria.values())


def _contrast(
    label: str, results: Sequence[CaseResult], better: str, worse: str, seed: int
) -> Contrast:
    deltas = _deltas(results, better, worse)
    lo, hi = bootstrap_ci(deltas, seed=seed)
    return Contrast(
        label=label,
        n_cases=len(deltas),
        mean_delta=mean(deltas),
        ci_low=lo,
        ci_high=hi,
        sign_p=sign_test_p(deltas),
        per_case=tuple(deltas),
    )


def summarize(results: Sequence[CaseResult], *, seed: int = 0) -> M1aReport:
    """Compute the pre-committed contrasts and check the four success criteria."""
    positive = [r for r in results if r.case_set == "positive"]
    control = [r for r in results if r.case_set == "negative_control"]

    primary = _contrast("full - content_ablation", positive, "full", "content_ablation", seed)
    salience = _contrast("full - salience", positive, "full", "salience", seed)
    bundle = _contrast("full - off", positive, "full", "off", seed)
    control_delta = mean(_deltas(control, "full", "content_ablation"))

    criteria = {
        # 1. primary contrast positive with the 90% CI excluding 0
        "primary_delta_ci_excludes_0": primary.mean_delta > 0.0 and primary.ci_low > 0.0,
        # 2. positive-set effect clears the negative-control null
        "clears_negative_control": primary.mean_delta > control_delta,
        # 3. the win survives subtracting salience
        "beats_salience": salience.mean_delta > 0.0,
        # 4. a negative-control set was actually provided (else 2 is vacuous)
        "has_negative_control": len(control) > 0,
    }
    return M1aReport(
        primary=primary,
        salience=salience,
        bundle=bundle,
        negative_control_delta=control_delta,
        criteria=criteria,
    )


def format_report(report: M1aReport) -> str:
    lines: list[str] = ["M-1a result"]
    for c in (report.primary, report.salience, report.bundle):
        lines.append(
            f"  {c.label:<26} n={c.n_cases:<3} mean δ={c.mean_delta:+.3f} "
            f"90% CI [{c.ci_low:+.3f}, {c.ci_high:+.3f}]  sign p={c.sign_p:.3f}"
        )
    lines.append(f"  negative-control δ (should be ~0): {report.negative_control_delta:+.3f}")
    lines.append("  success criteria (all must hold for a positive M-1a):")
    for name, ok in report.criteria.items():
        lines.append(f"    [{'PASS' if ok else 'FAIL'}] {name}")
    lines.append(f"  VERDICT: {'POSITIVE' if report.passes else 'NOT ESTABLISHED'}")
    return "\n".join(lines)
