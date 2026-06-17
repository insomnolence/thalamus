"""Episode materialization — an :class:`EpisodeSpan` → a Brain-1 ``MemoryRecord``.

Deterministic and *evidenced-only*: we build the episode from what the trajectory
log actually records, never a confabulated narrative. Per §13.17, every "why"
component is tagged by provenance — **evidenced** (grounded in the trajectory /
structure: a commit's footprint, a real failed test) vs **asserted** (an author
narrative such as a commit subject) — so a later reader (or dreaming pass) never
mistakes a story the brain told itself for a fact. An unmarked inferred why is the
historical-narrative cousin of the Polynoica self-reference trap (§13.7).

The episode id is **stable and idempotent**: re-running the spine over the same logs
rebuilds the same record (derived-view discipline, §14.1) — `Store.add` overwrites
by id rather than duplicating. Richer whys (rejected alternatives reconstructed from
dead-ends, computed constraints from Brain 2) and belief extraction come later.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from thalamus.core.types import Hemisphere, MemoryId, MemoryRecord
from thalamus.experiential.outcome import classify_outcome
from thalamus.experiential.segmentation import EpisodeSpan
from thalamus.instrumentation import TrajectoryEvent, TrajectoryEventKind


class WhyProvenance(StrEnum):
    """Provenance of a why-component (§13.17)."""

    EVIDENCED = "evidenced"  # grounded in the trajectory/structure (deterministic)
    ASSERTED = "asserted"  # an author/model narrative — lower trust


@dataclass(frozen=True, slots=True)
class WhyComponent:
    """One component of an episode's *why*, tagged with its provenance."""

    kind: str  # e.g. "goal" | "rejected-alternative" | "constraint"
    text: str
    provenance: WhyProvenance


def _dead_ends(events: tuple[TrajectoryEvent, ...]) -> list[dict[str, str]]:
    """Evidenced failures the work actually hit — the prohibitive-memory signal
    (§13.10): failed tests (with messages) and error events."""
    dead_ends: list[dict[str, str]] = []
    for event in events:
        if event.kind is TrajectoryEventKind.TEST_RUN:
            for failed in event.payload.get("failed", []):
                dead_ends.append(
                    {
                        "source": "test",
                        "id": str(failed.get("id", "")),
                        "type": str(failed.get("type", "")),
                        "message": str(failed.get("message", "")),
                    }
                )
        elif event.kind is TrajectoryEventKind.ERROR:
            dead_ends.append({"source": "error", "message": str(event.payload.get("message", ""))})
    return dead_ends


def _merge_footprint_lines(commits: list[TrajectoryEvent]) -> dict[str, list[int]]:
    """Per-file changed line numbers across the span's commits — the line-aware footprint (C-8).

    Each COMMIT event carries ``payload["file_lines"]`` (path → new-side changed lines, from
    ``GitObserver``); the span's value is their per-file union, sorted. Empty when no commit
    captured line data (e.g. pre-C-8 events) — the footprint then links at module level."""
    merged: dict[str, set[int]] = {}
    for commit in commits:
        file_lines = commit.payload.get("file_lines", {})
        if not isinstance(file_lines, dict):
            continue
        for path, lines in file_lines.items():
            if isinstance(lines, (list, tuple)):
                merged.setdefault(str(path), set()).update(int(n) for n in lines)
    return {path: sorted(nums) for path, nums in merged.items() if nums}


def _render_content(
    subject: str | None, footprint: list[str], dead_ends: list[dict[str, str]]
) -> str:
    """A deterministic, evidenced summary — the text that gets embedded for recall."""
    lines = [f"Worked toward: {subject}" if subject else "Uncommitted work in progress"]
    if footprint:
        lines.append("Touched: " + ", ".join(footprint))
    if dead_ends:
        lines.append("Hit dead-ends: " + "; ".join(d["id"] or d["message"] for d in dead_ends))
    return "\n".join(lines)


class EpisodeBuilder:
    """Builds an episode ``MemoryRecord`` from an :class:`EpisodeSpan`."""

    def build(self, span: EpisodeSpan) -> MemoryRecord | None:
        events = tuple(span.events)
        if not events:
            return None
        scope = events[0].scope
        commits = [event for event in events if event.kind is TrajectoryEventKind.COMMIT]
        terminal = commits[-1] if commits else None
        footprint = sorted({f for c in commits for f in c.payload.get("files", [])})
        footprint_lines = _merge_footprint_lines(commits)  # C-8: per-file touched line numbers
        dead_ends = _dead_ends(events)

        why: list[WhyComponent] = []
        subject = str(terminal.payload.get("subject", "")) if terminal is not None else None
        if subject:
            why.append(WhyComponent("goal", subject, WhyProvenance.ASSERTED))
        why.extend(
            WhyComponent("rejected-alternative", d["id"] or d["message"], WhyProvenance.EVIDENCED)
            for d in dead_ends
        )

        sha = str(terminal.payload["sha"]) if terminal is not None else None
        outcome: dict[str, Any] | None = (
            {"kind": "commit", "sha": sha, "subject": subject or ""}
            if terminal is not None
            else None
        )

        if span.key is not None:  # the segmenter owns identity (e.g. session-bounded)
            memory_id = MemoryId(f"episode:{span.key}")
            created_at = terminal.timestamp if terminal is not None else events[-1].timestamp
        elif terminal is not None:  # commit-bounded default: stable id from the sha
            memory_id = MemoryId(f"episode:{sha}")
            created_at = terminal.timestamp
        else:  # open, uncommitted work-in-progress
            memory_id = MemoryId(f"episode:open:{events[0].event_id}")
            created_at = events[-1].timestamp

        metadata: dict[str, Any] = {
            "segmentation": span.segmentation,
            "closed": span.closed,
            "footprint": footprint,
            "footprint_lines": footprint_lines,
            "terminal_outcome": outcome,
            "outcome": classify_outcome(events).value,  # Tier-2 truth signal (§13.8)
            "dead_ends": dead_ends,
            "source_event_ids": [str(event.event_id) for event in events],
            "why": [
                {"kind": w.kind, "text": w.text, "provenance": w.provenance.value} for w in why
            ],
        }
        return MemoryRecord(
            memory_id=memory_id,
            hemisphere=Hemisphere.EXPERIENTIAL,
            kind="episode",
            content=_render_content(subject, footprint, dead_ends),
            scope=scope,
            created_at=created_at,
            metadata=metadata,
        )
