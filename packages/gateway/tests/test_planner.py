"""Tests for the Planner — the deterministic plan/impact brief (resolve → radius → gather).

These also serve as the deterministic-core eval gates: provably-correct blast radius
(differential vs the raw edge set), gather completeness vs the cross-link index, coverage
honesty (unverified absence), and the high-fan-out circuit-breaker.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from thalamus.core.types import (
    Hemisphere,
    MemoryId,
    MemoryRecord,
    MemoryRef,
    RepoId,
    Scope,
    StructuralRef,
    TenantId,
)
from thalamus.gateway import Planner, PlannerConfig
from thalamus.gateway.views import DerivedViewsRef
from thalamus.structural import (
    CoChangeIndex,
    InMemoryCoChangeIndex,
    InMemoryCrossLinkIndex,
    InMemoryStructuralGraph,
    ScoredNode,
    StructuralNode,
)
from thalamus.structural.schema import IngestResult, SourceAnchor, StructuralEdge

SCOPE = Scope(TenantId("t"), RepoId("r"))
NOW = datetime(2026, 6, 16, tzinfo=UTC)


def _node(node_id: str, kind: str = "function", label: str | None = None) -> StructuralNode:
    return StructuralNode(
        node_id=node_id,
        kind=kind,
        label=label or node_id.split(":")[-1],
        scope=SCOPE,
        anchor=SourceAnchor(path=f"{node_id}.py", line_start=1, line_end=9),
    )


def _record(mid: str, kind: str, content: str) -> MemoryRecord:
    return MemoryRecord(
        MemoryId(mid),
        Hemisphere.EXPERIENTIAL,
        kind,
        content,
        SCOPE,
        NOW,
        metadata={"why": f"why-{mid}", "source": "curated"},
    )


class _Store:
    """Minimal Store stub — Planner only calls ``get``."""

    def __init__(self, records: Sequence[MemoryRecord]) -> None:
        self._records = {r.memory_id: r for r in records}

    def get(self, ref: MemoryRef) -> MemoryRecord | None:
        return self._records.get(ref.memory_id)


class _Retr:
    """Structural-retriever stub returning fixed scored hits for any cue."""

    corpus = "code"

    def __init__(self, hits: Sequence[tuple[StructuralNode, float]]) -> None:
        self._hits = hits

    def retrieve(self, cue: object, k: int) -> list[ScoredNode]:
        return [ScoredNode(node=n, score=s) for n, s in self._hits][:k]


# --- a small call graph: bar() and baz() call foo(); foo() calls helper(); module contains foo()
FOO = _node("mod:foo")
BAR = _node("mod:bar")
BAZ = _node("mod:baz")
HELPER = _node("mod:helper")
MODULE = _node("module:mod", kind="module", label="mod")

SIBLING = _node("mod:sibling")  # no call/contains edge to foo — only reachable via co-change
_NODES = [FOO, BAR, BAZ, HELPER, MODULE, SIBLING]
_EDGES = [
    StructuralEdge("mod:bar", "mod:foo", "calls"),
    StructuralEdge("mod:baz", "mod:foo", "calls"),
    StructuralEdge("mod:foo", "mod:helper", "calls"),
    StructuralEdge("module:mod", "mod:foo", "contains"),
]


def _ref(node: StructuralNode) -> StructuralRef:
    return node.ref


def _mref(mid: str) -> MemoryRef:
    return MemoryRef(SCOPE, MemoryId(mid))


def _build(
    *,
    hits: Sequence[tuple[StructuralNode, float]],
    links: Sequence[tuple[str, StructuralNode]] = (),
    records: Sequence[MemoryRecord] = (),
    config: PlannerConfig | None = None,
    cochange: CoChangeIndex | None = None,
) -> Planner:
    graph = InMemoryStructuralGraph(SCOPE)
    graph.add(IngestResult(nodes=_NODES, edges=_EDGES))
    link_index = InMemoryCrossLinkIndex()
    for mid, node in links:
        link_index.link(_mref(mid), _ref(node))
    return Planner(
        graph=graph,
        links=link_index,
        store=_Store(records),
        structural_retrievers=[_Retr(hits)],
        views=DerivedViewsRef(),
        cochange=cochange,
        config=config,
    )


def test_resolves_target_and_taggs_blast_radius_by_relation() -> None:
    planner = _build(hits=[(FOO, 0.9), (BAR, 0.2)])
    brief = planner.plan(target="foo", scope=SCOPE)

    assert brief.integration_point is not None
    assert brief.integration_point.node_id == "mod:foo"
    assert brief.resolution_relevance == 0.9
    by_relation = {(rn.item.node_id, rn.relation) for rn in brief.blast_radius}
    assert ("mod:bar", "caller") in by_relation  # what breaks
    assert ("mod:baz", "caller") in by_relation
    assert ("mod:helper", "callee") in by_relation  # what it uses
    assert ("module:mod", "container") in by_relation


def test_blast_radius_callers_match_the_raw_edge_set() -> None:
    """Differential correctness: the caller set equals the calls-edges-into-foo, exactly."""
    planner = _build(hits=[(FOO, 0.9)])
    brief = planner.plan(target="foo", scope=SCOPE)

    callers = {rn.item.node_id for rn in brief.blast_radius if rn.relation == "caller"}
    expected = {e.source_id for e in _EDGES if e.type == "calls" and e.target_id == "mod:foo"}
    assert callers == expected == {"mod:bar", "mod:baz"}


def test_gathers_cross_linked_memories_partitioned_by_kind() -> None:
    records = [
        _record("m-gotcha", "gotcha", "foo mutates shared state"),
        _record("m-constraint", "constraint", "bar must stay idempotent"),
        _record("m-decision", "decision", "helper was extracted for reuse"),
    ]
    planner = _build(
        hits=[(FOO, 0.9)],
        links=[("m-gotcha", FOO), ("m-constraint", BAR), ("m-decision", HELPER)],
        records=records,
    )
    brief = planner.plan(target="foo", scope=SCOPE)

    assert {m.memory_id for m in brief.constraints} == {
        MemoryId("m-gotcha"),
        MemoryId("m-constraint"),
    }
    assert {m.memory_id for m in brief.context} == {MemoryId("m-decision")}


def test_coverage_reports_unverified_absence() -> None:
    """baz has no attached memory — coverage must count it as a no-data node, not 'clean'."""
    records = [_record("m1", "gotcha", "foo gotcha")]
    planner = _build(hits=[(FOO, 0.9)], links=[("m1", FOO)], records=records)
    brief = planner.plan(target="foo", scope=SCOPE)

    cov = brief.coverage
    assert cov.radius_nodes == 1 + len(brief.blast_radius)  # foo + radius
    assert cov.nodes_with_context == 1  # only foo carries a memory
    assert cov.nodes_without_context >= 1  # bar/baz/helper/module are unverified-absence
    # the per-node signal is exposed too
    baz = next(rn for rn in brief.blast_radius if rn.item.node_id == "mod:baz")
    assert baz.linked_memory_count == 0


def test_memory_dedup_across_radius_nodes() -> None:
    """A memory linked to two in-scope nodes appears once."""
    records = [_record("shared", "decision", "touches foo and bar")]
    planner = _build(
        hits=[(FOO, 0.9)], links=[("shared", FOO), ("shared", BAR)], records=records
    )
    brief = planner.plan(target="foo", scope=SCOPE)
    all_ids = [m.memory_id for m in (*brief.constraints, *brief.context)]
    assert all_ids.count(MemoryId("shared")) == 1


def test_high_fanout_breaker_suppresses_caller_enumeration() -> None:
    planner = _build(hits=[(FOO, 0.9)], config=PlannerConfig(fanout_threshold=1))
    brief = planner.plan(target="foo", scope=SCOPE)

    assert brief.high_fanout is True
    assert brief.fanout_callers == 2  # bar + baz
    assert all(rn.relation != "caller" for rn in brief.blast_radius)  # callers not enumerated
    assert any(rn.relation == "callee" for rn in brief.blast_radius)  # callees still shown
    assert "shared infrastructure" in brief.render()


def test_node_budget_caps_the_radius_and_reports_omitted() -> None:
    planner = _build(hits=[(FOO, 0.9)], config=PlannerConfig(node_budget=1))
    brief = planner.plan(target="foo", scope=SCOPE)
    assert len(brief.blast_radius) == 1
    assert brief.radius_omitted >= 1


def test_unresolved_target_returns_an_honest_empty_brief() -> None:
    planner = _build(hits=[])
    brief = planner.plan(target="nonexistent", scope=SCOPE)
    assert brief.integration_point is None
    assert brief.blast_radius == ()
    rendered = brief.render()
    assert "Could not resolve" in rendered


def test_ambiguous_resolution_is_flagged_with_alternatives() -> None:
    planner = _build(hits=[(FOO, 0.90), (BAR, 0.89)], config=PlannerConfig(ambiguity_ratio=0.95))
    brief = planner.plan(target="foo", scope=SCOPE)
    assert brief.resolution_ambiguous is True
    assert any(a.node_id == "mod:bar" for a in brief.alternatives)
    assert "ambiguous" in brief.render()


def test_blast_radius_refs_from_a_known_node_matches_the_graph() -> None:
    """The eval entrypoint: radius from a known ref (no resolution/gather), breaker honoured."""
    planner = _build(hits=[(FOO, 0.9)])
    refs = planner.blast_radius_refs(FOO.ref)
    ids = {r.node_id for r in refs}
    assert {"mod:bar", "mod:baz", "mod:helper"} <= ids  # callers + callee
    assert planner.is_high_fanout(FOO.ref) is False


def test_blast_radius_refs_suppresses_callers_under_fanout() -> None:
    planner = _build(hits=[(FOO, 0.9)], config=PlannerConfig(fanout_threshold=1))
    assert planner.is_high_fanout(FOO.ref) is True
    ids = {r.node_id for r in planner.blast_radius_refs(FOO.ref)}
    assert "mod:bar" not in ids and "mod:baz" not in ids  # callers suppressed
    assert "mod:helper" in ids  # callee still present


def _cochange(
    *commits: tuple[StructuralNode, StructuralNode], times: int = 1
) -> InMemoryCoChangeIndex:
    idx = InMemoryCoChangeIndex()
    for _ in range(times):
        for a, b in commits:
            idx.add_commit([a.ref, b.ref])
    return idx


def test_cochange_folds_in_a_non_call_coupled_node() -> None:
    """The whole point: a sibling reachable by neither calls nor contains, only co-change."""
    cc = _cochange((FOO, SIBLING), times=2)  # count 2 ≥ default min_support 2
    with_cc = _build(hits=[(FOO, 0.9)], cochange=cc)
    without = _build(hits=[(FOO, 0.9)])

    assert "mod:sibling" in {r.node_id for r in with_cc.blast_radius_refs(FOO.ref)}
    assert "mod:sibling" not in {r.node_id for r in without.blast_radius_refs(FOO.ref)}
    # and it surfaces in the brief tagged as co-change
    brief = with_cc.plan(target="foo", scope=SCOPE)
    tagged = {(rn.item.node_id, rn.relation) for rn in brief.blast_radius}
    assert ("mod:sibling", "co-change") in tagged


def test_cochange_min_support_drops_one_offs() -> None:
    cc = _cochange((FOO, SIBLING), times=1)  # count 1 < default min_support 2
    planner = _build(hits=[(FOO, 0.9)], cochange=cc)
    assert "mod:sibling" not in {r.node_id for r in planner.blast_radius_refs(FOO.ref)}


def test_render_surfaces_coverage_and_constraints() -> None:
    records = [_record("m1", "gotcha", "foo mutates shared state")]
    planner = _build(hits=[(FOO, 0.9)], links=[("m1", FOO)], records=records)
    rendered = planner.plan(target="foo", scope=SCOPE).render()
    assert "# Plan brief: foo" in rendered
    assert "Known constraints & gotchas" in rendered
    assert "foo mutates shared state" in rendered
    assert "unverified" in rendered  # the coverage-honesty line
