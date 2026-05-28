"""The dreaming scheduler — runs a sequence of passes with per-pass failure
isolation, recording each to the dream log.

dreaming.md frames dreaming as *an offline orchestrator running a DAG of
independent, individually-gated, individually-removable passes*. v0 runs the DAG
flattened to its topological order as a linear sequence; the graph structure can
return behind this same seam without changing callers. Two invariants matter:

* **Failure isolation** — each pass is wrapped so one failure can neither abort
  the cycle nor corrupt its siblings (dreaming.md "safe to get wrong"). A failed
  pass is recorded and the cycle continues.
* **Synchronous by design** — ``run`` blocks. ``serve`` runs it via
  ``asyncio.to_thread`` (Commit 4) so a tick never blocks the event loop in the
  long-running, many-session server; keeping the engine itself plain makes it
  trivially unit-testable.
"""

from __future__ import annotations

import time
from collections.abc import Sequence

from thalamus.dreaming.base import (
    CycleReport,
    DreamingPass,
    PassContext,
    PassReport,
    PassStatus,
)
from thalamus.dreaming.log import DreamLog, DreamRecord


class Scheduler:
    """Runs an ordered set of dreaming passes for one cycle."""

    def __init__(self, passes: Sequence[DreamingPass], log: DreamLog | None = None) -> None:
        self._passes = tuple(passes)
        self._log = log

    def run(self, ctx: PassContext) -> CycleReport:
        """Run every pass in order, isolating failures, and return the cycle report."""
        reports: list[PassReport] = []
        for dpass in self._passes:
            report = self._run_one(dpass, ctx)
            reports.append(report)
            if self._log is not None:
                self._log.emit(DreamRecord(timestamp=ctx.now, scope=ctx.scope, report=report))
        return CycleReport(started_at=ctx.now, passes=tuple(reports))

    @staticmethod
    def _run_one(dpass: DreamingPass, ctx: PassContext) -> PassReport:
        start = time.perf_counter()
        try:
            outcome = dpass.run(ctx)
        except Exception as exc:  # failure-isolated: a bad pass never aborts the cycle
            return PassReport(
                name=dpass.name,
                kind=dpass.kind,
                status=PassStatus.FAILED,
                summary="",
                details={},
                error=f"{type(exc).__name__}: {exc}",
                duration_seconds=time.perf_counter() - start,
            )
        return PassReport(
            name=dpass.name,
            kind=dpass.kind,
            status=outcome.status,
            summary=outcome.summary,
            details=dict(outcome.details),
            error=None,
            duration_seconds=time.perf_counter() - start,
        )
