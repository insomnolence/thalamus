"""JUnitObserver — ingests JUnit-XML test reports into TEST_RUN trajectory events.

Runner-agnostic (pytest's ``--junitxml`` and most runners emit JUnit XML),
out-of-band, and deterministic — far more unit-testable than an in-process pytest
plugin, and not coupled to pytest internals. Captures per-run totals and the
failed tests **with their messages**: those error payloads are the
prohibitive-memory signal hindsight relabeling needs (OLR §13.10).

Note: parses local, trusted test reports with the stdlib XML parser. If reports
ever come from an untrusted source, harden with ``defusedxml`` (XXE).
"""

from __future__ import annotations

import uuid
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from thalamus.core.types import EventId, Scope, SessionId
from thalamus.instrumentation.trajectory import TrajectoryEvent, build_test_run_event


@dataclass(frozen=True, slots=True)
class _Suite:
    """One parsed JUnit ``<testsuite>`` (or the aggregate of several)."""

    name: str
    tests: int
    failures: int
    errors: int
    skipped: int
    failed: list[dict[str, str]] = field(default_factory=list)


def _uuid_event_id() -> EventId:
    return EventId(uuid.uuid4().hex)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class JUnitObserver:
    """Turns a JUnit-XML report into TEST_RUN trajectory events (one per suite)."""

    def __init__(
        self,
        scope: Scope,
        *,
        event_id_factory: Callable[[], EventId] = _uuid_event_id,
        now: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._scope = scope
        self._event_id_factory = event_id_factory
        self._now = now

    def ingest(
        self,
        junit_xml_path: Path,
        *,
        session_id: SessionId | None = None,
        terminal: bool = False,
        aggregate: bool = False,
    ) -> list[TrajectoryEvent]:
        """Parse a JUnit report into TEST_RUN events.

        ``aggregate`` collapses all ``<testsuite>``s into a **single** TEST_RUN summing their
        totals — the right shape for a *terminal validation* of a runner that emits one suite
        per file (e.g. jest, one per test file), so any red suite makes the whole run FAILED
        rather than the classifier seeing only the last suite. Default (per-suite) is unchanged.
        """
        root = ET.parse(junit_xml_path).getroot()
        elements = root.findall("testsuite") if root.tag == "testsuites" else [root]
        suites = [self._parse_suite(element) for element in elements]
        if aggregate:
            suites = [_merge_suites(suites)]
        timestamp = self._now()
        return [
            build_test_run_event(
                event_id=self._event_id_factory(),
                timestamp=timestamp,
                scope=self._scope,
                terminal=terminal,
                session_id=session_id,
                suite=suite.name,
                tests=suite.tests,
                failures=suite.failures,
                errors=suite.errors,
                skipped=suite.skipped,
                failed=suite.failed,
            )
            for suite in suites
        ]

    @staticmethod
    def _parse_suite(element: ET.Element) -> _Suite:
        failed: list[dict[str, str]] = []
        for case in element.findall("testcase"):
            problem = case.find("failure")
            problem_kind = "failure"
            if problem is None:
                problem = case.find("error")
                problem_kind = "error"
            if problem is None:
                continue
            classname = case.get("classname", "")
            name = case.get("name", "")
            failed.append(
                {
                    "id": f"{classname}::{name}" if classname else name,
                    "type": problem_kind,
                    "message": problem.get("message", ""),
                }
            )
        return _Suite(
            name=element.get("name", ""),
            tests=int(element.get("tests", "0")),
            failures=int(element.get("failures", "0")),
            errors=int(element.get("errors", "0")),
            skipped=int(element.get("skipped", "0")),
            failed=failed,
        )


# Cap the merged failure list so an all-red aggregate run can't produce an unbounded payload.
_MAX_AGGREGATE_FAILURES = 50


def _merge_suites(suites: list[_Suite]) -> _Suite:
    """Sum suite totals into one — the aggregate terminal-validation TEST_RUN."""
    failed: list[dict[str, str]] = []
    for suite in suites:
        failed.extend(suite.failed)
    return _Suite(
        name="aggregate",
        tests=sum(s.tests for s in suites),
        failures=sum(s.failures for s in suites),
        errors=sum(s.errors for s in suites),
        skipped=sum(s.skipped for s in suites),
        failed=failed[:_MAX_AGGREGATE_FAILURES],
    )
