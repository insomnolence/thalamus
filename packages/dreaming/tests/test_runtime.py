"""MaintenanceTicker drives the serve's upkeep off-thread: a periodic wake perceives
(capture) then consolidates (dream); a write-trigger consolidates only; and a hiccup in
either phase never kills the daemon thread."""

from __future__ import annotations

import threading
from datetime import UTC, datetime

from thalamus.core import RepoId, Scope, TenantId
from thalamus.dreaming import (
    MaintenanceTicker,
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


class _Capture:
    """A stand-in capture phase: counts calls, fires an event, optionally raises."""

    def __init__(self, *, fail: bool = False) -> None:
        self.runs = 0
        self.ran = threading.Event()
        self._fail = fail

    def __call__(self) -> object:
        self.runs += 1
        self.ran.set()
        if self._fail:
            raise RuntimeError("capture backend hiccup")
        return {"ingested": 0}


def test_run_once_runs_capture_then_the_scheduler_synchronously() -> None:
    dpass = _SignallingPass()
    capture = _Capture()
    ticker = MaintenanceTicker(Scheduler([dpass]), _ctx, capture=capture, interval_seconds=3600)
    report = ticker.run_once()
    assert capture.runs == 1
    assert dpass.runs == 1
    assert report is not None and report.ok


def test_run_once_housekeeps_before_capture_then_consolidates() -> None:
    order: list[str] = []
    ticker = MaintenanceTicker(
        Scheduler([_SignallingPass()]),
        _ctx,
        capture=lambda: order.append("capture"),
        housekeeping=lambda: order.append("housekeeping"),
        interval_seconds=3600,
    )
    report = ticker.run_once()
    assert order == ["housekeeping", "capture"]  # housekeeping is a sibling phase, run first
    assert report is not None and report.ok


def test_a_housekeeping_failure_does_not_skip_capture_or_consolidation() -> None:
    dpass = _SignallingPass()
    capture = _Capture()

    def boom() -> object:
        raise RuntimeError("rotation backend hiccup")

    ticker = MaintenanceTicker(
        Scheduler([dpass]), _ctx, capture=capture, housekeeping=boom, interval_seconds=3600
    )
    report = ticker.run_once()  # must not raise
    assert capture.runs == 1  # capture still ran despite the housekeeping error
    assert dpass.runs == 1 and report is not None and report.ok


def test_trigger_consolidates_only_and_does_not_capture() -> None:
    dpass = _SignallingPass()
    capture = _Capture()
    # Long interval: only a trigger should run within the test's lifetime, and a write-trigger
    # must refresh views WITHOUT polling git (no capture).
    ticker = MaintenanceTicker(Scheduler([dpass]), _ctx, capture=capture, interval_seconds=3600)
    ticker.start()
    try:
        ticker.trigger()
        assert dpass.ran.wait(timeout=5.0), "trigger did not run a consolidation cycle"
        assert capture.runs == 0, "a write-trigger must not run the capture phase"
    finally:
        ticker.stop()


def test_periodic_wake_captures_then_consolidates() -> None:
    dpass = _SignallingPass()
    capture = _Capture()
    # Short interval, no trigger: the only path that runs is the periodic one, which must
    # perceive (capture) and then consolidate (dream).
    ticker = MaintenanceTicker(Scheduler([dpass]), _ctx, capture=capture, interval_seconds=0.05)
    ticker.start()
    try:
        assert capture.ran.wait(timeout=5.0), "periodic wake never captured"
        assert dpass.ran.wait(timeout=5.0), "periodic wake never consolidated"
    finally:
        ticker.stop()


def test_a_capture_failure_does_not_skip_consolidation() -> None:
    dpass = _SignallingPass()
    capture = _Capture(fail=True)
    ticker = MaintenanceTicker(Scheduler([dpass]), _ctx, capture=capture, interval_seconds=0.05)
    ticker.start()
    try:
        assert capture.ran.wait(timeout=5.0), "capture never attempted"
        assert dpass.ran.wait(timeout=5.0), "a capture failure wrongly skipped consolidation"
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

    ticker = MaintenanceTicker(Scheduler([dpass]), flaky_ctx, interval_seconds=3600)
    ticker.start()
    try:
        ticker.trigger()  # first cycle: context factory raises
        assert first_attempted.wait(timeout=5.0), "first cycle never ran"
        ticker.trigger()  # second cycle: the thread must still be alive to run it
        assert dpass.ran.wait(timeout=5.0), "thread died after the first cycle errored"
        assert dpass.runs >= 1
    finally:
        ticker.stop()


def test_dream_only_ticker_with_no_capture_still_consolidates() -> None:
    dpass = _SignallingPass()
    ticker = MaintenanceTicker(Scheduler([dpass]), _ctx, interval_seconds=3600)
    report = ticker.run_once()
    assert dpass.runs == 1
    assert report is not None and report.ok
