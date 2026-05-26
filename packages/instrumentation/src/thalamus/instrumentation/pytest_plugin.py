"""Live pytest capture — emit one TEST_RUN trajectory event per pytest session.

The in-process complement to :class:`JUnitObserver` (offline, JUnit-XML based): the
design's "critical capture unlock" for the *prohibitive-memory* signal — failures and
fail->pass transitions captured live, with no actuator cooperation (OLR §13.10/§13.11b).
Both paths build the event through :func:`build_test_run_event`, so they are identical
to ``ingest_episodes``.

**Opt-in and inert by default.** Registered as a ``pytest11`` plugin, so it loads for
every pytest run in the venv — but it does nothing unless ``THALAMUS_PYTEST_CAPTURE`` is
truthy, so unrelated runs (including this project's own suite) are unaffected. A capture
failure never breaks the test run.

Configuration via environment (mirrors the CLI conventions in ``thalamus.cli``):

==========================  ===========================================================
``THALAMUS_PYTEST_CAPTURE`` enable capture (``1``/``true``/``yes``/``on``)
``THALAMUS_TRAJECTORY_LOG`` explicit trajectory log path
                            (default ``<THALAMUS_REPO|cwd>/.thalamus/logs/trajectory.jsonl``)
``THALAMUS_REPO``           repo root for the default log path + repo id (default ``.``)
``THALAMUS_TENANT``         scope tenant id (default ``local``)
``THALAMUS_REPO_ID``        scope repo id (default: repo dir name)
``THALAMUS_SESSION_ID``     correlate the run with a gateway recall session (optional;
                            falls back to the serve-published session context file)
``THALAMUS_PYTEST_TERMINAL`` mark the run terminal Tier-2 validation (optional)
==========================  ===========================================================
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from thalamus.core.types import EventId, RepoId, Scope, SessionId, TenantId
from thalamus.instrumentation.session import FileSessionContextStore, default_session_path
from thalamus.instrumentation.trajectory import (
    JsonlTrajectorySink,
    TrajectorySink,
    build_test_run_event,
)

_ENABLE_ENV = "THALAMUS_PYTEST_CAPTURE"
_TRUTHY = {"1", "true", "yes", "on"}


def _truthy(value: str | None) -> bool:
    return value is not None and value.strip().lower() in _TRUTHY


def _resolve_session_id(repo: Path) -> SessionId | None:
    """The session to tag this run with: explicit ``THALAMUS_SESSION_ID`` wins, else the
    session published by a running ``serve`` (read from its context file), else none. So a
    live test run joins the active serve session without the actuator setting any env."""
    env = os.environ.get("THALAMUS_SESSION_ID")
    if env:
        return SessionId(env)
    ctx = FileSessionContextStore(default_session_path(repo)).read()
    return ctx.session_id if ctx is not None else None


def _failure_message(report: pytest.TestReport) -> str:
    """A short, bounded failure message (mirrors JUnit's ``message`` attribute)."""
    longrepr = report.longrepr
    crash_message = getattr(getattr(longrepr, "reprcrash", None), "message", None)
    if isinstance(crash_message, str) and crash_message:
        return crash_message
    text = report.longreprtext or str(longrepr or "")
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""


class _ThalamusPytestCapture:
    """Tallies per-test outcomes and emits one TEST_RUN event at session end."""

    def __init__(
        self,
        *,
        sink: TrajectorySink,
        scope: Scope,
        session_id: SessionId | None,
        terminal: bool,
    ) -> None:
        self._sink = sink
        self._scope = scope
        self._session_id = session_id
        self._terminal = terminal
        self._passed = 0
        self._failures = 0
        self._errors = 0
        self._skipped = 0
        self._failed: list[dict[str, str]] = []

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if report.when == "call" and report.passed:
            self._passed += 1
        elif report.when == "call" and report.failed:
            self._failures += 1
            self._failed.append(
                {"id": report.nodeid, "type": "failure", "message": _failure_message(report)}
            )
        elif report.failed:  # a setup/teardown failure is an error, not a test failure
            self._errors += 1
            self._failed.append(
                {"id": report.nodeid, "type": "error", "message": _failure_message(report)}
            )
        elif report.skipped and report.when in ("setup", "call"):
            self._skipped += 1

    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        try:
            event = build_test_run_event(
                event_id=EventId(uuid.uuid4().hex),
                timestamp=datetime.now(UTC),
                scope=self._scope,
                suite=str(self._scope.repo_id),
                tests=int(getattr(session, "testscollected", 0)),
                failures=self._failures,
                errors=self._errors,
                skipped=self._skipped,
                failed=self._failed,
                terminal=self._terminal,
                session_id=self._session_id,
            )
            self._sink.emit(event)
        except Exception as exc:  # observability must never break the test run
            print(f"thalamus: pytest capture failed: {exc}", file=sys.stderr)


def pytest_configure(config: pytest.Config) -> None:
    """Register the capture plugin only when capture is explicitly enabled."""
    if not _truthy(os.environ.get(_ENABLE_ENV)):
        return
    repo = Path(os.environ.get("THALAMUS_REPO", ".")).resolve()
    log_env = os.environ.get("THALAMUS_TRAJECTORY_LOG")
    log_path = Path(log_env) if log_env else repo / ".thalamus" / "logs" / "trajectory.jsonl"
    scope = Scope(
        tenant_id=TenantId(os.environ.get("THALAMUS_TENANT", "local")),
        repo_id=RepoId(os.environ.get("THALAMUS_REPO_ID", repo.name)),
    )
    plugin = _ThalamusPytestCapture(
        sink=JsonlTrajectorySink(log_path),
        scope=scope,
        session_id=_resolve_session_id(repo),
        terminal=_truthy(os.environ.get("THALAMUS_PYTEST_TERMINAL")),
    )
    config.pluginmanager.register(plugin, "thalamus-pytest-capture")
