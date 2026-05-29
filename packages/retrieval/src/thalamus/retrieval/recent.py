"""Temporal retrieval — the most recently recorded memories, newest first.

A deterministic view over data we already capture: every ``MemoryRecord`` carries
``created_at``, so "what's the latest / what did we just do" is an exact sort, not a
ranking. This is *not* semantic recall (relevance to a cue) and deliberately stays
separate from it — recall answers "most relevant", this answers "most recent". It is
a derived view computed on demand from the durable store (§14.1), so it is always
current and never hand-maintained.

Pure functions over already-scanned records (the caller does ``Store.scan``), so they
are testable in isolation; for a large store a backend ``ORDER BY created_at DESC LIMIT``
can replace the scan+sort behind the same call site.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from thalamus.core import MemoryRecord


def select_recent(
    records: Iterable[MemoryRecord], *, limit: int, kinds: Sequence[str] | None = None
) -> list[MemoryRecord]:
    """The ``limit`` most-recently-created records (newest first), optionally filtered by kind."""
    pool = [r for r in records if kinds is None or r.kind in kinds]
    pool.sort(key=lambda r: r.created_at, reverse=True)
    return pool[: max(limit, 0)]


def render_recent(records: Sequence[MemoryRecord], *, max_chars: int = 200) -> str:
    """A compact newest-first listing: kind, date, id, and a one-line content preview."""
    if not records:
        return "No memories recorded yet."
    lines = ["# Most recent memories (newest first)"]
    for record in records:
        when = record.created_at.date().isoformat()
        preview = " ".join(record.content.split())
        if len(preview) > max_chars:
            preview = preview[: max_chars - 1].rstrip() + "…"
        lines.append(f"- [{record.kind} · {when}] {record.memory_id}\n    {preview}")
    return "\n".join(lines)
