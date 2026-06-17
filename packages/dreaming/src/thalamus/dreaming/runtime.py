"""MaintenanceTicker — run the serve's periodic background upkeep off the request path.

The long-running serve does two kinds of upkeep that must never stall recall, on one
background daemon thread ``interval_seconds`` apart:

* **perceive** — poll the code repo's git history into Brain 1 episodes (the *capture* phase);
* **consolidate** — run the dreaming scheduler, refreshing the gateway's derived views (the
  superseded frontier + footprint staleness) as writes accumulate (the *dream* phase).

A periodic wake runs *perceive then consolidate*, so the same cycle that captures a commit
immediately links and credibility-scores it. A write to the brain (``remember`` /
``--supersedes``) instead *triggers* a consolidate-only cycle, refreshing the views the write
dirtied without paying for a git poll.

Capture is deliberately a **sibling phase, not a dreaming pass**: it writes raw, source-of-truth
episodes from an *external* git source, whereas a :class:`DreamingPass` reads via a read-only
context and writes only regenerable derived views (the §14.3 firewall). Same clock, distinct
contracts — so the pass protocol stays clean and capture keeps its write access to the store.

Off the FastMCP event loop by construction — a separate OS thread — so a multi-second cycle
never blocks concurrent sessions. The only state shared with recall is the gateway's
``DerivedViewsRef``, swapped atomically (one ``STORE_ATTR`` under the GIL), so no lock is needed.
Each phase is isolated: a capture hiccup never skips consolidation, and the per-cycle context
construction is guarded so a transient backend error can never kill the thread — it just retries.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable

from thalamus.dreaming.base import CycleReport, PassContext
from thalamus.dreaming.scheduler import Scheduler


class MaintenanceTicker:
    """Drives the serve's perceive→consolidate cycle periodically and on demand.

    ``capture`` (optional) is the perception phase — typically the warm
    ``GitEpisodeIngestor.sync`` — run once per *periodic* cycle before consolidation; it may
    return a small JSON-able summary (e.g. a count of new episodes) or ``None``. The dreaming
    ``scheduler`` (optional) is the consolidation phase, also run on the *triggered* cycle after
    a write. Either may be ``None`` (a capture-only or dream-only serve); with both ``None`` the
    ticker does nothing, so callers simply don't start one.
    """

    def __init__(
        self,
        scheduler: Scheduler | None,
        context_factory: Callable[[], PassContext] | None,
        *,
        capture: Callable[[], object] | None = None,
        housekeeping: Callable[[], object] | None = None,
        interval_seconds: float,
    ) -> None:
        self._scheduler = scheduler
        self._context_factory = context_factory
        self._capture = capture
        self._housekeeping = housekeeping
        self._interval = interval_seconds
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def run_once(self) -> CycleReport | None:
        """Run one full cycle (housekeep, perceive, then consolidate) synchronously in-caller."""
        self._run_housekeeping()
        self._run_capture()
        return self._run_dream()

    def _run_housekeeping(self) -> None:
        """The housekeeping phase — log rotation/retention. A sibling of capture (writes the file
        system, not derived views), failure-isolated so a rotation hiccup never skips the cycle."""
        if self._housekeeping is None:
            return
        try:
            self._housekeeping()
        except Exception as exc:  # housekeeping must never skip perceive/consolidate
            print(
                f"thalamus: housekeeping phase errored (will retry next tick): {exc}",
                file=sys.stderr,
            )

    def _run_capture(self) -> None:
        if self._capture is None:
            return
        try:
            self._capture()
        except Exception as exc:  # a capture hiccup must never skip consolidation
            print(
                f"thalamus: capture phase errored (will retry next tick): {exc}", file=sys.stderr
            )

    def _run_dream(self) -> CycleReport | None:
        if self._scheduler is None or self._context_factory is None:
            return None
        return self._scheduler.run(self._context_factory())

    def trigger(self) -> None:
        """Request a consolidation cycle as soon as possible (the write-trigger). Thread-safe."""
        self._wake.set()

    def start(self) -> None:
        """Start the background thread (idempotent)."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._loop, name="thalamus-maintenance", daemon=True
        )
        self._thread.start()

    def stop(self, *, join_timeout: float = 5.0) -> None:
        """Signal the thread to finish its current cycle and exit."""
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=join_timeout)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            # wait() returns True when woken by a trigger (a write), False on interval timeout.
            triggered = self._wake.wait(timeout=self._interval)
            if self._stop.is_set():
                break
            # Clear before running so a trigger arriving mid-cycle schedules another pass
            # (at most one redundant cycle) rather than being lost.
            self._wake.clear()
            try:
                if not triggered:
                    # Periodic wake: housekeep (rotate oversized logs), then perceive new commits,
                    # so consolidation in this same cycle links and scores what was just captured.
                    # A write-trigger skips both — a remember changed neither the logs' size nor the
                    # git history, only the views refreshed below.
                    self._run_housekeeping()
                    self._run_capture()
                self._run_dream()
            except Exception as exc:  # never let the background thread die on a transient error
                print(f"thalamus: maintenance cycle errored (will retry): {exc}", file=sys.stderr)
