from __future__ import annotations

from pathlib import Path

from thalamus.core.types import EventId, RepoId, Scope, SessionId, TenantId
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


def test_terminal_result_carries_session_for_tier2_join(tmp_path: Path) -> None:
    path = tmp_path / "report.xml"
    path.write_text(SUITES_WRAPPER, encoding="utf-8")
    (event,) = JUnitObserver(SCOPE, event_id_factory=_ids).ingest(
        path, session_id=SessionId("s1"), terminal=True
    )
    assert event.session_id == SessionId("s1")
    assert event.payload["terminal"] is True


# jest-junit emits a <testsuites> root with one <testsuite> per test FILE — here the second
# file has a failing test. Without aggregation the classifier would only see the last suite.
JEST_MULTISUITE_ONE_FAIL = """<?xml version="1.0"?>
<testsuites name="jest tests" tests="3" failures="1" errors="0" time="1.2">
  <testsuite name="src/a.test.ts" tests="2" failures="0" errors="0" skipped="0" time="0.5">
    <testcase classname="a" name="adds" time="0.1"/>
    <testcase classname="a" name="subtracts" time="0.1"/>
  </testsuite>
  <testsuite name="src/b.test.ts" tests="1" failures="1" errors="0" skipped="0" time="0.7">
    <testcase classname="b" name="divides">
      <failure message="Expected 2 but received 3">at Object.&lt;anonymous&gt;</failure>
    </testcase>
  </testsuite>
</testsuites>
"""


def test_jest_multisuite_per_suite_is_one_event_each(tmp_path: Path) -> None:
    path = tmp_path / "junit.xml"
    path.write_text(JEST_MULTISUITE_ONE_FAIL, encoding="utf-8")
    events = JUnitObserver(SCOPE, event_id_factory=_ids).ingest(path)
    assert len(events) == 2  # one TEST_RUN per jest file-suite
    assert {e.payload["suite"] for e in events} == {"src/a.test.ts", "src/b.test.ts"}


def test_jest_aggregate_collapses_to_one_failing_run(tmp_path: Path) -> None:
    # The terminal-validation shape: one TEST_RUN summing all files; any red file -> failures>0.
    path = tmp_path / "junit.xml"
    path.write_text(JEST_MULTISUITE_ONE_FAIL, encoding="utf-8")
    (event,) = JUnitObserver(SCOPE, event_id_factory=_ids).ingest(path, aggregate=True)
    assert event.payload["tests"] == 3
    assert event.payload["failures"] == 1
    assert event.payload["suite"] == "aggregate"
    assert event.payload["failed"][0]["id"] == "b::divides"
