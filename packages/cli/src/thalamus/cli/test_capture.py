"""Capture terminal test evidence into the raw trajectory log."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from thalamus.core.types import RepoId, Scope, SessionId, TenantId
from thalamus.instrumentation import JsonlTrajectorySink, JUnitObserver


@dataclass(frozen=True, slots=True)
class TestCaptureConfig:
    repo: Path
    junit: Path
    tenant: str
    repo_id: str
    session_id: str
    terminal: bool


def add_test_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--repo", type=Path, default=Path.cwd(), help="repository owning the test run"
    )
    parser.add_argument("--junit", type=Path, required=True, help="JUnit XML result to capture")
    parser.add_argument("--tenant", default="local", help="tenant id for scoping")
    parser.add_argument("--repo-id", default=None, help="repo id (default: repo directory name)")
    parser.add_argument("--session-id", required=True, help="retrieval/evaluation session id")
    parser.add_argument(
        "--terminal",
        action="store_true",
        help="mark this run as terminal Tier-2 validation for the session",
    )


def test_config(args: argparse.Namespace) -> TestCaptureConfig:
    repo = Path(args.repo).resolve()
    return TestCaptureConfig(
        repo=repo,
        junit=Path(args.junit),
        tenant=str(args.tenant),
        repo_id=str(args.repo_id) if args.repo_id else repo.name,
        session_id=str(args.session_id),
        terminal=bool(args.terminal),
    )


def run_test_capture(config: TestCaptureConfig) -> int:
    scope = Scope(TenantId(config.tenant), RepoId(config.repo_id))
    events = JUnitObserver(scope).ingest(
        config.junit, session_id=SessionId(config.session_id), terminal=config.terminal
    )
    sink = JsonlTrajectorySink(config.repo / ".thalamus" / "logs" / "trajectory.jsonl")
    for event in events:
        sink.emit(event)
    print(f"captured {len(events)} test-run event(s) [{config.repo_id}]")
    return len(events)
