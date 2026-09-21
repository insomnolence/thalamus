"""The gating instrument: does repeating a cycle leave the derived state alone?

A harness that only ever reports "converged" is worthless, so these tests check both directions —
a settled cycle converges, and a pass that keeps moving is *caught and named*.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from thalamus.core.types import MemoryId, MemoryRef, RepoId, Scope, TenantId
from thalamus.dreaming import (
    PassContext,
    PassKind,
    PassOutcome,
    Scheduler,
    check_convergence,
    digest,
    snapshot,
)

SCOPE = Scope(TenantId("t"), RepoId("r"))
NOW = datetime(2026, 9, 21, tzinfo=UTC)


def _ctx() -> PassContext:
    return PassContext(scope=SCOPE, now=NOW)


class _WritesOnce:
    """A convergent actor: it settles a view on the first run and then re-writes the same value."""

    name = "settles"
    kind = PassKind.ACTOR

    def __init__(self, sink: dict[str, object]) -> None:
        self._sink = sink

    def run(self, ctx: PassContext) -> PassOutcome:
        self._sink["view"] = {"a": 1, "b": 2}
        return PassOutcome(summary="settled")


class _NeverSettles:
    """A non-convergent actor — the shape a gate must never be placed over."""

    name = "drifts"
    kind = PassKind.ACTOR

    def __init__(self, sink: dict[str, object]) -> None:
        self._sink = sink
        self._n = 0

    def run(self, ctx: PassContext) -> PassOutcome:
        self._n += 1
        self._sink["view"] = {"count": self._n}
        return PassOutcome(summary=f"ran {self._n}x")


def _probe(sink: dict[str, object], key: str = "view") -> Callable[[], object]:
    return lambda: sink.get(key)


def test_a_settled_cycle_converges() -> None:
    sink: dict[str, object] = {}
    report = check_convergence(
        Scheduler([_WritesOnce(sink)]), _ctx, {"settled": _probe(sink)}
    )
    assert report.converged
    assert report.drifted == ()
    assert "CONVERGED" in report.render()


def test_drift_is_caught_and_named() -> None:
    """The load-bearing test: without this the harness could pass by doing nothing."""
    sink: dict[str, object] = {}
    report = check_convergence(
        Scheduler([_NeverSettles(sink)]), _ctx, {"unstable": _probe(sink)}
    )
    assert not report.converged
    assert report.drifted == ("unstable",)
    assert "DRIFT" in report.render()


def test_only_the_drifting_view_is_named() -> None:
    """A report that blamed every view would be useless for locating the bad gate."""
    stable: dict[str, object] = {}
    moving: dict[str, object] = {}
    report = check_convergence(
        Scheduler([_WritesOnce(stable), _NeverSettles(moving)]),
        _ctx,
        {"stable": _probe(stable), "moving": _probe(moving)},
    )
    assert report.drifted == ("moving",)


def test_drift_is_measured_from_the_first_settled_snapshot() -> None:
    """A view that settles late must still be reported, not excused by a longer run."""
    sink: dict[str, object] = {}
    pass_ = _NeverSettles(sink)
    report = check_convergence(Scheduler([pass_]), _ctx, {"v": _probe(sink)}, rounds=4)
    assert report.rounds == 4
    assert report.drifted == ("v",)
    assert len({s.digests["v"] for s in report.states}) == 4  # every round differed


def test_fewer_than_two_rounds_is_rejected() -> None:
    try:
        check_convergence(Scheduler([]), _ctx, {}, rounds=1)
    except ValueError as exc:
        assert "at least 2" in str(exc)
    else:  # pragma: no cover - the raise is the contract
        raise AssertionError("expected ValueError")


def test_digest_ignores_iteration_order_but_not_content() -> None:
    """Order-insensitive for mappings and sets, or every run would look like drift."""
    assert digest({"a": 1, "b": 2}) == digest({"b": 2, "a": 1})
    assert digest({"a", "b"}) == digest({"b", "a"})
    assert digest({"a": 1}) != digest({"a": 2})
    # Sequences are ordered: a reordered ranking IS a different derived view.
    assert digest([1, 2]) != digest([2, 1])


def test_digest_is_sensitive_to_small_weight_changes() -> None:
    """Centrality/usage weights are floats; a last-place shift is a real difference."""
    assert digest({"m": 0.1234567890123}) != digest({"m": 0.1234567890124})


def test_digest_handles_the_ref_types_the_views_hold() -> None:
    a = {MemoryRef(SCOPE, MemoryId("m1")): 0.5}
    b = {MemoryRef(SCOPE, MemoryId("m1")): 0.5}
    c = {MemoryRef(SCOPE, MemoryId("m2")): 0.5}
    assert digest(a) == digest(b)
    assert digest(a) != digest(c)


def test_a_broken_probe_is_recorded_not_raised() -> None:
    """One unreadable view must not hide the others."""

    def boom() -> object:
        raise RuntimeError("backend down")

    state = snapshot({"ok": lambda: 1, "broken": boom})
    assert state.digests["broken"].startswith("error:RuntimeError")
    assert not state.digests["ok"].startswith("error:")


def test_a_view_appearing_or_vanishing_counts_as_a_difference() -> None:
    assert snapshot({"a": lambda: 1}).differences(snapshot({})) == ("a",)
