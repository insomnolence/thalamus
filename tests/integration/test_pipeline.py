"""End-to-end integration: drive the assembled brain and check it actually works.

Unlike the per-package unit tests, this wires the whole retrieval path together
(encoder -> store -> L0 retriever -> logging decorator -> JSONL log on disk) plus
the git trajectory observer, and exercises it the way the gateway will.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from thalamus.core.types import (
    Cue,
    Hemisphere,
    MemoryId,
    MemoryRecord,
    RepoId,
    Scope,
    SessionId,
    TenantId,
)
from thalamus.instrumentation import (
    GitObserver,
    JsonlEventSink,
    LoggingRetriever,
    TrajectoryEventKind,
)
from thalamus.retrieval import L0Retriever
from thalamus.routing import DeterministicEncoder
from thalamus.store import InMemoryStore

SCOPE = Scope(tenant_id=TenantId("acme"), repo_id=RepoId("widgets"))
NOW = datetime(2026, 5, 24, tzinfo=UTC)


def test_full_retrieval_pipeline_logs_to_disk(tmp_path: Path) -> None:
    encoder = DeterministicEncoder(dim=128)
    store = InMemoryStore(dim=128)
    memories = {
        "sqlite": "we switched the store to sqlite because the json file was too slow",
        "async": "the async teardown is flaky; await the close before asserting",
        "style": "the user prefers terse commit messages",
    }
    for mid, text in memories.items():
        store.add(
            MemoryRecord(MemoryId(mid), Hemisphere.EXPERIENTIAL, "episode", text, SCOPE, NOW),
            encoder.encode([text])[0],
        )

    log_path = tmp_path / "retrieval.jsonl"
    brain = LoggingRetriever(
        L0Retriever(encoder, store, now=lambda: NOW),
        JsonlEventSink(log_path),
        policy_id="L0",
        now=lambda: NOW,
    )

    cue = Cue(
        text="why did we move the database to sqlite",
        scope=SCOPE,
        session_id=SessionId("sess-1"),
    )
    result = brain.retrieve(cue, k=2)

    # The right memory surfaces first...
    assert result.shown[0].record.memory_id == MemoryId("sqlite")
    # ...and the decision was logged completely (full candidate pool + shown).
    logged = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert len(logged) == 1
    event = logged[0]
    assert event["policy_id"] == "L0"
    assert event["session_id"] == "sess-1"
    assert len(event["candidates"]) == 3
    assert event["shown"][0]["memory_id"] == "sqlite"
    assert event["shown"][0]["propensity"] == 1.0


def test_git_trajectory_capture(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (("init", "-q"), ("config", "user.email", "t@e.com"), ("config", "user.name", "T")):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    (repo / "db.py").write_text("import aiosqlite\n", encoding="utf-8")
    for args in (("add", "db.py"), ("commit", "-qm", "switch to sqlite")):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    events = GitObserver(repo, SCOPE).poll()
    assert len(events) == 1
    assert events[0].kind is TrajectoryEventKind.COMMIT
    assert events[0].payload["files"] == ["db.py"]
    assert events[0].payload["subject"] == "switch to sqlite"
