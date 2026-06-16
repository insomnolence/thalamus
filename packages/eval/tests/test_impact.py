"""Tests for the git-derived blast-radius recall eval (pure pieces — no git, no graph)."""

from __future__ import annotations

from thalamus.core.types import RepoId, Scope, StructuralRef, TenantId
from thalamus.eval.impact import (
    ImpactPair,
    evaluate_impact,
    map_changes_to_refs,
    parse_changed_lines,
)

S = Scope(TenantId("t"), RepoId("r"))


def _ref(nid: str) -> StructuralRef:
    return StructuralRef(scope=S, node_id=nid)


def test_parse_changed_lines_reads_the_new_side_of_each_hunk() -> None:
    diff = (
        "diff --git a/foo.py b/foo.py\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -10,2 +10,3 @@ def foo():\n"
        "+    new line\n"
        "@@ -40,1 +41,0 @@ def bar():\n"  # pure deletion → attribute new-side line 41
        "diff --git a/gone.py b/gone.py\n"
        "--- a/gone.py\n"
        "+++ /dev/null\n"  # deleted file → skipped
        "@@ -1,3 +0,0 @@\n"
    )
    changes = parse_changed_lines(diff)
    assert changes == {"foo.py": [(10, 12), (41, 41)]}


def test_map_changes_to_refs_picks_the_smallest_enclosing_symbol() -> None:
    index = {
        "foo.py": [
            (1, 50, _ref("mod:Cls")),  # the class spans the whole region
            (10, 20, _ref("mod:Cls.method")),  # the method is the tighter span
        ]
    }
    # a change at lines 12-13 is inside the method → attribute the method, not the class
    refs = map_changes_to_refs({"foo.py": [(12, 13)]}, index)
    assert refs == {_ref("mod:Cls.method")}


def test_map_changes_ignores_files_with_no_indexed_symbols() -> None:
    refs = map_changes_to_refs({"unknown.py": [(1, 5)]}, {"foo.py": [(1, 9, _ref("mod:foo"))]})
    assert refs == set()


class _Oracle:
    """Fake blast-radius oracle: a fixed target→radius map + a hub set."""

    def __init__(
        self, radius: dict[StructuralRef, set[StructuralRef]], hubs: set[StructuralRef]
    ) -> None:
        self._radius = radius
        self._hubs = hubs

    def blast_radius_refs(
        self, ref: StructuralRef, *, hops: int | None = None
    ) -> frozenset[StructuralRef]:
        return frozenset(self._radius.get(ref, set()))

    def is_high_fanout(self, ref: StructuralRef) -> bool:
        return ref in self._hubs


def test_evaluate_impact_counts_recall_cross_file_and_high_fanout() -> None:
    a, b, c, hub = _ref("m:a"), _ref("m:b"), _ref("m:c"), _ref("m:hub")
    # a's radius contains b (recovered); hub's radius is empty (breaker) — c is a miss.
    oracle = _Oracle(radius={a: {b}, hub: set()}, hubs={hub})
    pairs = [
        ImpactPair(target=a, coupled=b, source_sha="s1", same_file=True),  # recovered, same-file
        ImpactPair(target=hub, coupled=c, source_sha="s2", same_file=False),  # miss; cross-file hub
    ]
    report = evaluate_impact(oracle, pairs, hops=2)

    assert report.n_pairs == 2
    assert report.recovered == 1
    assert report.recall == 0.5
    assert report.n_cross_file == 1
    assert report.recovered_cross_file == 0
    assert report.cross_file_recall == 0.0
    assert report.n_target_high_fanout == 1  # the hub target, explaining its miss
    assert report.n_target_empty_radius == 1  # the hub's radius is empty (breaker)


def test_evaluate_impact_empty_is_zeroed_not_a_crash() -> None:
    report = evaluate_impact(_Oracle({}, set()), [], hops=2)
    assert report.n_pairs == 0
    assert report.recall == 0.0
    assert report.cross_file_recall == 0.0
