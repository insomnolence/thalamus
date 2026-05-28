"""The scheduler runs passes in order, isolates failures, and logs every run."""

from __future__ import annotations

from datetime import UTC, datetime

from thalamus.core import RepoId, Scope, TenantId
from thalamus.dreaming import (
    InMemoryDreamLog,
    PassContext,
    PassKind,
    PassOutcome,
    PassStatus,
    Scheduler,
)


def _ctx() -> PassContext:
    return PassContext(
        scope=Scope(tenant_id=TenantId("t"), repo_id=RepoId("r")),
        now=datetime(2026, 5, 28, 12, 0, tzinfo=UTC),
    )


class _CountingPass:
    def __init__(self, name: str, calls: list[str]) -> None:
        self._name = name
        self._calls = calls

    @property
    def name(self) -> str:
        return self._name

    @property
    def kind(self) -> PassKind:
        return PassKind.ACTOR

    def run(self, ctx: PassContext) -> PassOutcome:
        self._calls.append(self._name)
        return PassOutcome(summary=f"{self._name} ran", details={"order": len(self._calls)})


class _SkippingPass:
    name = "skipper"
    kind = PassKind.PROPOSER

    def run(self, ctx: PassContext) -> PassOutcome:
        return PassOutcome.skipped("no handle wired")


class _BoomPass:
    name = "boom"
    kind = PassKind.ACTOR

    def run(self, ctx: PassContext) -> PassOutcome:
        raise ValueError("kaboom")


def test_passes_run_in_order_and_report_ok() -> None:
    calls: list[str] = []
    sched = Scheduler([_CountingPass("a", calls), _CountingPass("b", calls)])

    report = sched.run(_ctx())

    assert calls == ["a", "b"]
    assert [p.name for p in report.passes] == ["a", "b"]
    assert all(p.status is PassStatus.OK for p in report.passes)
    assert report.ok
    assert report.passes[1].details == {"order": 2}


def test_a_failing_pass_is_isolated_and_the_cycle_continues() -> None:
    calls: list[str] = []
    sched = Scheduler([_CountingPass("a", calls), _BoomPass(), _CountingPass("c", calls)])

    report = sched.run(_ctx())

    # the pass after the failure still ran
    assert calls == ["a", "c"]
    statuses = {p.name: p.status for p in report.passes}
    assert statuses == {"a": PassStatus.OK, "boom": PassStatus.FAILED, "c": PassStatus.OK}
    boom = next(p for p in report.passes if p.name == "boom")
    assert boom.error is not None and "kaboom" in boom.error
    assert not report.ok  # a failure makes the whole cycle not-ok


def test_skipped_pass_is_recorded_but_keeps_the_cycle_ok() -> None:
    sched = Scheduler([_SkippingPass()])

    report = sched.run(_ctx())

    assert report.passes[0].status is PassStatus.SKIPPED
    assert report.passes[0].kind is PassKind.PROPOSER
    assert report.ok


def test_every_pass_run_is_emitted_to_the_dream_log() -> None:
    log = InMemoryDreamLog()
    calls: list[str] = []
    sched = Scheduler([_CountingPass("a", calls), _BoomPass()], log=log)

    sched.run(_ctx())

    assert [r.report.name for r in log.records] == ["a", "boom"]
    assert [r.report.status for r in log.records] == [PassStatus.OK, PassStatus.FAILED]
    assert all(r.scope.repo_id == RepoId("r") for r in log.records)
