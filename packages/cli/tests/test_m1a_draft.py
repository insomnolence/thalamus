from __future__ import annotations

from datetime import UTC, datetime

from thalamus.cli.m1a_draft import M1aDraftConfig, build_draft, render_brief, render_memory
from thalamus.core.types import Hemisphere, MemoryId, MemoryRecord, RepoId, Scope, TenantId

SCOPE = Scope(TenantId("local"), RepoId("dollhouse"))


def _rec(memory_id: str, kind: str, content: str, why: str | None = None) -> MemoryRecord:
    metadata = {"why": why} if why else {}
    return MemoryRecord(
        MemoryId(memory_id), Hemisphere.EXPERIENTIAL, kind, content, SCOPE,
        datetime(2026, 1, 1, tzinfo=UTC), metadata,
    )


def _config(memory_id: str = "retained:decisive", k: int = 8) -> M1aDraftConfig:
    return M1aDraftConfig(
        neo4j_uri="bolt://localhost:7688", neo4j_user="neo4j", neo4j_password=None,
        encoder="bge-small", dim=384, tenant="local", repo_id="dollhouse",
        task="change the embedding encoder", memory_id=memory_id, case_set="positive", k=k,
    )


def test_render_brief_empty_is_the_placebo() -> None:
    assert "No specific prior project context" in render_brief([])


def test_render_memory_includes_kind_and_why() -> None:
    line = render_memory(_rec("m1", "decision", "rebuild the index", why="encoders differ"))
    assert "[decision]" in line and "rebuild the index" in line and "why: encoders differ" in line


def test_build_draft_isolates_the_decisive_memory() -> None:
    shown = [
        _rec("retained:decisive", "decision", "changing the encoder requires rebuilding indexes"),
        _rec("retained:other", "gotcha", "the serve must be restarted to pick up new code"),
    ]
    draft = build_draft(_config(), shown)
    full = draft["arms"]["full"]
    ablation = draft["arms"]["content_ablation"]
    assert "rebuilding indexes" in full
    assert "rebuilding indexes" not in ablation  # the decisive memory is removed
    assert "must be restarted" in ablation  # other context survives
    assert "No specific prior" in draft["arms"]["off"]
    assert "Watch out for known gotchas" in draft["arms"]["salience"]


def test_build_draft_warns_when_decisive_not_recalled() -> None:
    shown = [_rec("retained:other", "gotcha", "unrelated")]
    draft = build_draft(_config(memory_id="retained:missing"), shown)
    assert any("NOT in the top-" in line for line in draft["_review"])
