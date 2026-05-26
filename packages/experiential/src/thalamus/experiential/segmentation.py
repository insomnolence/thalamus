"""Episode segmentation — cutting the trajectory log into episodes (§13.16).

An episode is both the storage atom of Brain 1 and the training-pair unit for
hindsight relabeling, so mis-cutting it is a *silent poison* (§13.16). The key
discipline: **segmentation is a derived view, not a live decision** — only the raw
trajectory log (§13.11b) is irreversible; the grouping into episodes is recomputed
from it anytime, so we cut with coarse *deterministic* boundaries now and re-segment
offline (in dreaming) as methods improve (§14.1 capture-raw-derive-views).

The seam is :class:`EpisodeSegmenter`. **S1 (commit-bounded,
:class:`CommitBoundedSegmenter`)**: work between commits is one episode, the commit
its terminal outcome — self-contained over the trajectory log alone, no join
required. **S0 (session-bounded, :class:`SessionBoundedSegmenter`)**: the events of
one MCP session are one episode, aligning the Tier-2 outcome unit with the Tier-1
recall ``session_id`` so the proxy↔truth monitor can join them (§13.12). S2
(footprint discontinuity) and S3 (LLM, dreaming-only) slot in behind the same
protocol later. See ``docs/deep-dives/path-to-real-data.md``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from thalamus.core.types import SessionId
from thalamus.instrumentation import TrajectoryEvent, TrajectoryEventKind


@dataclass(frozen=True, slots=True)
class EpisodeSpan:
    """A contiguous slice of the trajectory log treated as one episode.

    A *view* over the raw log: it holds the events that compose the episode, in
    chronological order. ``closed`` is True when the span carries a terminal outcome
    signal (a commit, a revert, or a terminal test); a trailing open span is
    uncommitted work-in-progress.

    ``segmentation`` tags which strategy cut the span (``"S1-commit"`` /
    ``"S0-session"``). ``key``, when set, is the segmenter-chosen stable id seed for
    the episode: the **segmenter owns episode identity** so :class:`EpisodeBuilder`
    stays decoupled from the cut strategy (and a session episode never collides with
    a commit episode). ``key=None`` keeps the builder's default commit-sha identity.
    """

    events: Sequence[TrajectoryEvent]
    closed: bool
    segmentation: str = "S1-commit"
    key: str | None = None


@runtime_checkable
class EpisodeSegmenter(Protocol):
    """Cuts an ordered trajectory-event stream into episode spans."""

    def segment(self, events: Sequence[TrajectoryEvent]) -> list[EpisodeSpan]:
        """Group ``events`` into episodes. Must be deterministic and re-runnable."""
        ...


class CommitBoundedSegmenter:
    """S1 (§13.16): each commit closes an episode; events since the last commit
    (inclusive) form its span. Trailing uncommitted events become one *open* span.

    Operates on a single coherent stream (one scope/repo); per-session splitting is
    a later refinement. Events are sorted by timestamp first, so capture order does
    not affect the cut (determinism)."""

    def segment(self, events: Sequence[TrajectoryEvent]) -> list[EpisodeSpan]:
        ordered = sorted(events, key=lambda event: event.timestamp)
        spans: list[EpisodeSpan] = []
        current: list[TrajectoryEvent] = []
        for event in ordered:
            current.append(event)
            if event.kind is TrajectoryEventKind.COMMIT:
                spans.append(EpisodeSpan(events=tuple(current), closed=True))
                current = []
        if current:
            spans.append(EpisodeSpan(events=tuple(current), closed=False))
        return spans


def _has_terminal_signal(events: Sequence[TrajectoryEvent]) -> bool:
    """True if a span carries a terminal outcome signal — a commit, a revert, or a
    test run explicitly marked terminal (what ``classify_outcome`` treats as closing).
    A non-terminal mid-work test run does not close a session."""
    for event in events:
        if event.kind in (TrajectoryEventKind.COMMIT, TrajectoryEventKind.REVERT):
            return True
        if event.kind is TrajectoryEventKind.TEST_RUN and bool(event.payload.get("terminal")):
            return True
    return False


class SessionBoundedSegmenter:
    """S0 (§13.16): each MCP session is one episode — the trajectory events sharing a
    ``session_id`` form its span. This aligns the Tier-2 outcome *unit* with the
    Tier-1 recall ``session_id`` key, so the proxy↔truth monitor (§13.12) can join a
    session's recalls to the outcome of the work they informed.

    Events without a ``session_id`` are **excluded** — a missing key is missing data,
    not a synthetic catch-all episode (the §13.16 "down-weight ambiguous, never
    fabricate a pair" discipline; the same missing-data treatment as ``utility_at_k``).
    Events within a span and the spans themselves are time-ordered for determinism;
    ``key`` namespaces the episode id by session so it never collides with a
    commit-bounded episode."""

    def segment(self, events: Sequence[TrajectoryEvent]) -> list[EpisodeSpan]:
        ordered = sorted(events, key=lambda event: event.timestamp)
        by_session: dict[SessionId, list[TrajectoryEvent]] = {}
        for event in ordered:
            if event.session_id is None:
                continue
            by_session.setdefault(event.session_id, []).append(event)
        spans = [
            EpisodeSpan(
                events=tuple(session_events),
                closed=_has_terminal_signal(session_events),
                segmentation="S0-session",
                key=f"session:{session_id}",
            )
            for session_id, session_events in by_session.items()
        ]
        spans.sort(key=lambda span: (span.events[0].timestamp, span.key or ""))
        return spans
