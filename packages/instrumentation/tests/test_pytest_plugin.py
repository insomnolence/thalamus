"""Tests for the live pytest capture plugin (thalamus.instrumentation.pytest_plugin)."""

from __future__ import annotations

import json
import types
from datetime import UTC, datetime
from pathlib import Path

import pytest
from thalamus.core.types import RepoId, Scope, SessionId, TenantId
from thalamus.instrumentation import (
    FileSessionContextStore,
    InMemoryTrajectorySink,
    SessionContext,
    TrajectoryEventKind,
    default_session_path,
)
from thalamus.instrumentation.pytest_plugin import (
    _full_suite_run,
    _resolve_session_id,
    _ThalamusPytestCapture,
    _truthy,
)

pytest_plugins = ["pytester"]

_SCOPE = Scope(TenantId("local"), RepoId("thalamus"))


def _report(*, when, outcome, nodeid="t::x", message="boom"):
    """A minimal stand-in for pytest's TestReport (duck-typed)."""
    longrepr = types.SimpleNamespace(reprcrash=types.SimpleNamespace(message=message))
    return types.SimpleNamespace(
        when=when,
        passed=(outcome == "passed"),
        failed=(outcome == "failed"),
        skipped=(outcome == "skipped"),
        nodeid=nodeid,
        longrepr=longrepr,
        longreprtext=message,
    )


def test_emits_one_test_run_event_with_tallies() -> None:
    sink = InMemoryTrajectorySink()
    plugin = _ThalamusPytestCapture(
        sink=sink, scope=_SCOPE, session_id=SessionId("s1"), terminal=True
    )
    plugin.pytest_runtest_logreport(_report(when="call", outcome="passed", nodeid="t::a"))
    plugin.pytest_runtest_logreport(_report(when="call", outcome="passed", nodeid="t::b"))
    plugin.pytest_runtest_logreport(
        _report(when="call", outcome="failed", nodeid="t::c", message="AssertionError: nope")
    )
    plugin.pytest_runtest_logreport(
        _report(when="setup", outcome="failed", nodeid="t::d", message="fixture blew up")
    )
    plugin.pytest_runtest_logreport(_report(when="setup", outcome="skipped", nodeid="t::e"))
    plugin.pytest_sessionfinish(types.SimpleNamespace(testscollected=5), 1)

    assert len(sink.events) == 1
    event = sink.events[0]
    assert event.kind is TrajectoryEventKind.TEST_RUN
    assert event.session_id == SessionId("s1")
    payload = event.payload
    assert payload["tests"] == 5
    assert payload["failures"] == 1
    assert payload["errors"] == 1
    assert payload["skipped"] == 1
    assert payload["terminal"] is True
    failed = {entry["id"]: entry for entry in payload["failed"]}
    assert failed["t::c"]["type"] == "failure"
    assert failed["t::c"]["message"] == "AssertionError: nope"
    assert failed["t::d"]["type"] == "error"  # setup failure -> error, not test failure


def test_capture_failure_never_breaks_the_run() -> None:
    class _BrokenSink:
        def emit(self, event: object) -> None:
            raise RuntimeError("disk full")

    plugin = _ThalamusPytestCapture(
        sink=_BrokenSink(), scope=_SCOPE, session_id=None, terminal=False
    )
    plugin.pytest_runtest_logreport(_report(when="call", outcome="passed"))
    plugin.pytest_sessionfinish(types.SimpleNamespace(testscollected=1), 0)  # must not raise


def test_truthy() -> None:
    assert all(_truthy(v) for v in ("1", "true", "YES", "on", " On "))
    assert not any(_truthy(v) for v in (None, "", "0", "no", "off"))


def _config(file_or_dir=None, keyword="", markexpr=""):
    option = types.SimpleNamespace(
        file_or_dir=file_or_dir or [], keyword=keyword, markexpr=markexpr
    )
    return types.SimpleNamespace(option=option)


def test_full_suite_run_detection() -> None:
    assert _full_suite_run(_config()) is True  # bare `pytest` -> validates everything -> terminal
    assert _full_suite_run(_config(file_or_dir=["tests/test_x.py"])) is False  # targeted subset
    assert _full_suite_run(_config(keyword="foo")) is False  # -k narrows -> not a full validation
    assert _full_suite_run(_config(markexpr="slow")) is False  # -m narrows


def test_resolve_session_id_prefers_explicit_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("THALAMUS_SESSION_ID", "from-env")
    # a context file exists, but the explicit env still wins
    FileSessionContextStore(default_session_path(tmp_path)).publish(
        SessionContext(SessionId("from-file"), datetime(2026, 5, 26, tzinfo=UTC))
    )
    assert _resolve_session_id(tmp_path) == SessionId("from-env")


def test_resolve_session_id_falls_back_to_published_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("THALAMUS_SESSION_ID", raising=False)
    FileSessionContextStore(default_session_path(tmp_path)).publish(
        SessionContext(SessionId("from-file"), datetime(2026, 5, 26, tzinfo=UTC))
    )
    assert _resolve_session_id(tmp_path) == SessionId("from-file")


def test_resolve_session_id_none_when_no_env_and_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("THALAMUS_SESSION_ID", raising=False)
    assert _resolve_session_id(tmp_path) is None


def test_inert_without_enable_env(pytester, monkeypatch) -> None:
    """With capture disabled, a pytest run writes no trajectory log."""
    log = pytester.path / "trajectory.jsonl"
    monkeypatch.delenv("THALAMUS_PYTEST_CAPTURE", raising=False)
    monkeypatch.setenv("THALAMUS_TRAJECTORY_LOG", str(log))
    pytester.makepyfile("def test_ok():\n    assert True\n")
    pytester.runpytest_subprocess().assert_outcomes(passed=1)
    assert not log.exists()


def test_captures_a_real_run_end_to_end(pytester, monkeypatch) -> None:
    """With capture enabled, the hook fires and one TEST_RUN event is written."""
    log = pytester.path / "trajectory.jsonl"
    monkeypatch.setenv("THALAMUS_PYTEST_CAPTURE", "1")
    monkeypatch.setenv("THALAMUS_TRAJECTORY_LOG", str(log))
    pytester.makepyfile(
        "def test_ok():\n    assert 1 == 1\n\ndef test_bad():\n    assert 1 == 2\n"
    )
    pytester.runpytest_subprocess().assert_outcomes(passed=1, failed=1)

    lines = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert obj["kind"] == "test_run"
    assert obj["payload"]["tests"] == 2
    assert obj["payload"]["failures"] == 1
    assert obj["payload"]["failed"][0]["type"] == "failure"
