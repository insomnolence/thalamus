"""DreamTicker drives the scheduler off-thread: on demand via trigger(), and
without dying when a cycle errors."""

from __future__ import annotations

import threading
from datetime import UTC, datetime

from thalamus.core import RepoId, Scope, TenantId
from thalamus.dreaming import (
    DreamTicker,
    PassContext,
    PassKind,
    PassOutcome,
    Scheduler,
)


def _ctx() -> PassContext:
    return PassContext(
        scope=Scope(tenant_id=TenantId("t"), repo_id=RepoId("r")),
        now=datetime(2026, 5, 28, 12, 0, tzinfo=UTC),
    )


class _SignallingPass:
    """Counts runs and fires an event each cycle so a test can wait deterministically."""

    name = "signal"
    kind = PassKind.ACTOR

    def __init__(self) -> None:
        self.runs = 0
        self.ran = threading.Event()

    def run(self, ctx: PassContext) -> PassOutcome:
        self.runs += 1
        self.ran.set()
        return PassOutcome(summary="ok")


def test_run_once_runs_the_scheduler_synchronously() -> None:
    dpass = _SignallingPass()
    ticker = DreamTicker(Scheduler([dpass]), _ctx, interval_seconds=3600)
    report = ticker.run_once()
    assert dpass.runs == 1
    assert report.ok


def test_trigger_runs_a_cycle_without_waiting_for_the_interval() -> None:
    dpass = _SignallingPass()
    # Long interval: only a trigger should make it run within the test's lifetime.
    ticker = DreamTicker(Scheduler([dpass]), _ctx, interval_seconds=3600)
    ticker.start()
    try:
        ticker.trigger()
        assert dpass.ran.wait(timeout=5.0), "trigger did not run a cycle"
        assert dpass.runs >= 1
    finally:
        ticker.stop()


def test_a_failing_context_factory_does_not_kill_the_thread() -> None:
    dpass = _SignallingPass()
    calls = {"n": 0}
    first_attempted = threading.Event()

    def flaky_ctx() -> PassContext:
        calls["n"] += 1
        if calls["n"] == 1:
            first_attempted.set()
            raise RuntimeError("transient backend hiccup")
        return _ctx()

    ticker = DreamTicker(Scheduler([dpass]), flaky_ctx, interval_seconds=3600)
    ticker.start()
    try:
        ticker.trigger()  # first cycle: context factory raises
        assert first_attempted.wait(timeout=5.0), "first cycle never ran"
        ticker.trigger()  # second cycle: the thread must still be alive to run it
        assert dpass.ran.wait(timeout=5.0), "thread died after the first cycle errored"
        assert dpass.runs >= 1
    finally:
        ticker.stop()
