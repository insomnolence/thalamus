"""DreamTicker — run a scheduler off the serve's request path.

The long-running serve must refresh derived views without ever stalling recall.
This runs the (synchronous) scheduler on a background daemon thread: once every
``interval_seconds``, or immediately when :meth:`trigger` is called after a write
that dirties the views (``remember`` / ``remember --supersedes``), whichever
comes first. It is off the FastMCP event loop *by construction* — a separate OS
thread — so a multi-second cycle never blocks concurrent sessions. The only state
shared with recall is the gateway's ``DerivedViewsRef``, swapped atomically (one
``STORE_ATTR`` under the GIL), so no lock is needed on either side.

The scheduler already isolates per-pass failures; this additionally guards the
per-cycle context construction so a transient backend hiccup can never kill the
thread — the next tick simply retries.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable

from thalamus.dreaming.base import CycleReport, PassContext
from thalamus.dreaming.scheduler import Scheduler


class DreamTicker:
    """Drives a :class:`Scheduler` periodically and on demand from a daemon thread."""

    def __init__(
        self,
        scheduler: Scheduler,
        context_factory: Callable[[], PassContext],
        *,
        interval_seconds: float,
    ) -> None:
        self._scheduler = scheduler
        self._context_factory = context_factory
        self._interval = interval_seconds
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def run_once(self) -> CycleReport:
        """Run one cycle synchronously in the caller's thread (used by the one-shot CLI)."""
        return self._scheduler.run(self._context_factory())

    def trigger(self) -> None:
        """Request a cycle as soon as possible (the write-trigger). Cheap and thread-safe."""
        self._wake.set()

    def start(self) -> None:
        """Start the background thread (idempotent)."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="thalamus-dream", daemon=True)
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
            self._wake.wait(timeout=self._interval)
            if self._stop.is_set():
                break
            # Clear before running so a trigger arriving mid-cycle schedules another pass
            # (at most one redundant cycle) rather than being lost.
            self._wake.clear()
            try:
                self.run_once()
            except Exception as exc:  # never let the background thread die on a transient error
                print(f"thalamus: dream cycle errored (will retry): {exc}", file=sys.stderr)
