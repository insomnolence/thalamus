from __future__ import annotations

from pathlib import Path

from thalamus.core.types import EventId, RepoId, Scope, TenantId
from thalamus.instrumentation import JUnitObserver, TrajectoryEventKind

SCOPE = Scope(tenant_id=TenantId("t1"), repo_id=RepoId("r1"))

ONE_FAIL = """<?xml version="1.0"?>
<testsuite name="suite" tests="2" failures="1" errors="0" skipped="0">
  <testcase classname="test_db" name="test_connect" time="0.01"/>
  <testcase classname="test_db" name="test_query" time="0.02">
    <failure message="AssertionError: blocking call in async loop">traceback...</failure>
  </testcase>
</testsuite>
"""

SUITES_WRAPPER = """<?xml version="1.0"?>
<testsuites>
  <testsuite name="s1" tests="1" failures="0" errors="0" skipped="0">
    <testcase classname="t" name="ok" time="0.0"/>
  </testsuite>
</testsuites>
"""


def _ids() -> EventId:
    return EventId("e")


def test_ingest_captures_failure_messages(tmp_path: Path) -> None:
    path = tmp_path / "report.xml"
    path.write_text(ONE_FAIL, encoding="utf-8")
    (event,) = JUnitObserver(SCOPE, event_id_factory=_ids).ingest(path)
    assert event.kind is TrajectoryEventKind.TEST_RUN
    assert event.payload["tests"] == 2
    assert event.payload["failures"] == 1
    assert len(event.payload["failed"]) == 1
    failed = event.payload["failed"][0]
    assert failed["id"] == "test_db::test_query"
    assert failed["type"] == "failure"
    assert "blocking call in async loop" in failed["message"]


def test_ingest_testsuites_wrapper_all_pass(tmp_path: Path) -> None:
    path = tmp_path / "report.xml"
    path.write_text(SUITES_WRAPPER, encoding="utf-8")
    (event,) = JUnitObserver(SCOPE, event_id_factory=_ids).ingest(path)
    assert event.payload["suite"] == "s1"
    assert event.payload["failed"] == []
