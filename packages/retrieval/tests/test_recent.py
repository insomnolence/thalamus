"""Temporal retrieval: newest-first selection + compact rendering."""

from __future__ import annotations

from datetime import UTC, datetime

from thalamus.core import Hemisphere, MemoryId, MemoryRecord, RepoId, Scope, TenantId
from thalamus.retrieval import render_recent, select_recent

SCOPE = Scope(TenantId("t"), RepoId("r"))


def _rec(mid: str, kind: str, day: int, content: str = "x") -> MemoryRecord:
    return MemoryRecord(
        MemoryId(mid), Hemisphere.EXPERIENTIAL, kind, content, SCOPE,
        datetime(2026, 5, day, tzinfo=UTC),
    )


def test_select_recent_newest_first_and_limit() -> None:
    records = [_rec("a", "decision", 1), _rec("c", "decision", 3), _rec("b", "episode", 2)]
    got = select_recent(records, limit=2)
    assert [r.memory_id for r in got] == [MemoryId("c"), MemoryId("b")]  # newest first, capped at 2


def test_select_recent_filters_by_kind() -> None:
    records = [_rec("a", "decision", 1), _rec("b", "episode", 3), _rec("c", "decision", 2)]
    got = select_recent(records, limit=10, kinds=("decision",))
    assert [r.memory_id for r in got] == [MemoryId("c"), MemoryId("a")]  # only decisions, newest


def test_render_recent_is_compact_and_ordered() -> None:
    pool = [_rec("a", "decision", 1, "first"), _rec("b", "gotcha", 2, "second")]
    records = select_recent(pool, limit=10)
    out = render_recent(records)
    assert out.splitlines()[0].startswith("# Most recent")
    assert "[gotcha · 2026-05-02] b" in out
    assert out.index(" b\n") < out.index(" a\n")  # newest (b) listed before older (a)


def test_render_recent_truncates_long_content() -> None:
    out = render_recent([_rec("a", "decision", 1, "word " * 200)], max_chars=50)
    body = out.splitlines()[-1]
    assert body.endswith("…")
    assert len(body.strip()) <= 60


def test_render_recent_empty() -> None:
    assert render_recent([]) == "No memories recorded yet."
