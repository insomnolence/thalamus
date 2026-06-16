"""Git-derived blast-radius recall — the honest, de-circularized eval for the plan tool.

The plan tool's claim is "the brief shows you what a change will break." The *circular* way to
test that (curate a gotcha, link it to a node, then check the brief surfaces it) measures only
that the link round-trips — the Polynoica self-reference trap in eval clothing. This instrument
avoids it by taking the ground truth from **git history**, authored by neither us nor the model:

> When a real **fix commit** touches two code symbols together, those symbols were *coupled* —
> the fix had to change both. So if a developer were about to edit one, the blast radius of that
> symbol *should* contain the other.

We mine such co-changed-in-a-fix symbol pairs (the mining lives in the CLI — it needs git + the
graph), then measure **recall**: for each pair, is the coupled symbol inside the computed blast
radius of the target? The radius comes from the structural graph; the pairs come from git — two
independent sources, so a recovered pair is real signal, and a miss is an honest miss (call-graph
reachability simply doesn't capture every kind of coupling — co-change includes logical/temporal
coupling a call edge never had). We report the number, we don't launder it into a helpfulness
claim (that is L3, gated on Tier-2 capture).

This module is the pure, dependency-light core: the pair/report types, the scorer over a
:class:`BlastRadiusOracle`, and the two pure git-diff helpers. Anything needing the structural
graph or a git subprocess lives in ``thalamus.cli.impact_eval``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from thalamus.core.types import StructuralRef


@runtime_checkable
class BlastRadiusOracle(Protocol):
    """Whatever can compute a blast radius from a known node — the planner satisfies this."""

    def blast_radius_refs(
        self, ref: StructuralRef, *, hops: int | None = None
    ) -> frozenset[StructuralRef]: ...

    def is_high_fanout(self, ref: StructuralRef) -> bool: ...


@dataclass(frozen=True, slots=True)
class ImpactPair:
    """Two symbols a real fix commit changed together — historical evidence of coupling."""

    target: StructuralRef  # the symbol a developer would target
    coupled: StructuralRef  # the symbol that was co-changed (should fall in target's radius)
    source_sha: str  # provenance: the fix commit that coupled them
    same_file: bool  # cross-file pairs are the "forest" cases a local LLM is blind to


@dataclass(frozen=True, slots=True)
class ImpactReport:
    """Blast-radius recall over a set of git-derived coupled pairs, with honesty cuts."""

    n_pairs: int
    recovered: int  # coupled ∈ radius(target)
    n_cross_file: int  # pairs whose two symbols live in different files
    recovered_cross_file: int
    n_target_high_fanout: int  # pairs whose target tripped the breaker (explains misses honestly)
    n_target_empty_radius: int  # pairs whose target has NO radius (starved graph, not a real miss)
    hops: int

    @property
    def recall(self) -> float:
        return self.recovered / self.n_pairs if self.n_pairs else 0.0

    @property
    def cross_file_recall(self) -> float:
        return self.recovered_cross_file / self.n_cross_file if self.n_cross_file else 0.0


def evaluate_impact(
    oracle: BlastRadiusOracle, pairs: Sequence[ImpactPair], *, hops: int = 2
) -> ImpactReport:
    """Measure what fraction of git-coupled pairs the blast radius recovers.

    Honours the tool's real behaviour (the fan-out breaker), so a hub target legitimately
    enumerates no callers — those misses are counted *and* attributed via
    ``n_target_high_fanout`` rather than hidden."""
    recovered = cross_file = recovered_cross = high_fanout = empty_radius = 0
    for pair in pairs:
        radius = oracle.blast_radius_refs(pair.target, hops=hops)
        hit = pair.coupled in radius
        recovered += hit
        if not pair.same_file:
            cross_file += 1
            recovered_cross += hit
        if oracle.is_high_fanout(pair.target):
            high_fanout += 1
        if not radius:
            empty_radius += 1
    return ImpactReport(
        n_pairs=len(pairs),
        recovered=recovered,
        n_cross_file=cross_file,
        recovered_cross_file=recovered_cross,
        n_target_high_fanout=high_fanout,
        n_target_empty_radius=empty_radius,
        hops=hops,
    )


_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def parse_changed_lines(diff_text: str) -> dict[str, list[tuple[int, int]]]:
    """Parse ``git show --unified=0 --format=`` output into ``{path: [(start, end), …]}``.

    Uses the *new-file* side of each hunk header (the lines that exist post-change). A pure
    deletion hunk (``+a,0``) records the single new-side line ``a`` it deletes around, so the
    surrounding symbol is still attributed. Deleted files (``+++ /dev/null``) are skipped."""
    changes: dict[str, list[tuple[int, int]]] = {}
    path: str | None = None
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            target = line[4:].strip()
            path = None if target == "/dev/null" else target.removeprefix("b/")
            continue
        if path is None:
            continue
        match = _HUNK.match(line)
        if match is None:
            continue
        start = int(match.group(1))
        length = int(match.group(2)) if match.group(2) is not None else 1
        if length <= 0:  # pure deletion — attribute the surrounding new-side line
            changes.setdefault(path, []).append((max(start, 1), max(start, 1)))
        else:
            changes.setdefault(path, []).append((start, start + length - 1))
    return changes


def map_changes_to_refs(
    changes: Mapping[str, Sequence[tuple[int, int]]],
    symbol_index: Mapping[str, Sequence[tuple[int, int, StructuralRef]]],
) -> set[StructuralRef]:
    """Map changed line-ranges to the symbols they touched — the narrowest enclosing one per range.

    ``symbol_index`` is ``{path: [(line_start, line_end, ref), …]}`` over code symbols. For each
    changed range, the *smallest-span* overlapping symbol is the most specific attribution (so a
    one-line edit inside a method tags the method, not also its class/module)."""
    touched: set[StructuralRef] = set()
    for path, ranges in changes.items():
        entries = symbol_index.get(path)
        if not entries:
            continue
        for start, end in ranges:
            best: tuple[int, StructuralRef] | None = None
            for s, e, ref in entries:
                if s <= end and e >= start:  # overlap
                    span = e - s
                    if best is None or span < best[0]:
                        best = (span, ref)
            if best is not None:
                touched.add(best[1])
    return touched
