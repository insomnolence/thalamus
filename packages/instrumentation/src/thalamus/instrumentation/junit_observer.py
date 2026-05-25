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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from thalamus.core.types import EventId, Scope
from thalamus.instrumentation.trajectory import TrajectoryEvent, TrajectoryEventKind


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

    def ingest(self, junit_xml_path: Path) -> list[TrajectoryEvent]:
        root = ET.parse(junit_xml_path).getroot()
        suites = root.findall("testsuite") if root.tag == "testsuites" else [root]
        timestamp = self._now()
        events: list[TrajectoryEvent] = []
        for suite in suites:
            failed: list[dict[str, str]] = []
            for case in suite.findall("testcase"):
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
            payload: dict[str, Any] = {
                "suite": suite.get("name", ""),
                "tests": int(suite.get("tests", "0")),
                "failures": int(suite.get("failures", "0")),
                "errors": int(suite.get("errors", "0")),
                "skipped": int(suite.get("skipped", "0")),
                "failed": failed,
            }
            events.append(
                TrajectoryEvent(
                    event_id=self._event_id_factory(),
                    timestamp=timestamp,
                    scope=self._scope,
                    kind=TrajectoryEventKind.TEST_RUN,
                    payload=payload,
                )
            )
        return events
