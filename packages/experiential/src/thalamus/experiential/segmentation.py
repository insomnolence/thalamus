"""Episode segmentation — cutting the trajectory log into episodes (§13.16).

An episode is both the storage atom of Brain 1 and the training-pair unit for
hindsight relabeling, so mis-cutting it is a *silent poison* (§13.16). The key
discipline: **segmentation is a derived view, not a live decision** — only the raw
trajectory log (§13.11b) is irreversible; the grouping into episodes is recomputed
from it anytime, so we cut with coarse *deterministic* boundaries now and re-segment
offline (in dreaming) as methods improve (§14.1 capture-raw-derive-views).

The seam is :class:`EpisodeSegmenter`. The first implementation is **S1
(commit-bounded)**: work between commits is one episode, the commit its terminal
outcome — self-contained over the trajectory log alone, no join required. S0
(request-bounded, anchored on the retrieval cue), S2 (footprint discontinuity), and
S3 (LLM, dreaming-only) slot in behind the same protocol later. See
``docs/deep-dives/path-to-real-data.md``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from thalamus.instrumentation import TrajectoryEvent, TrajectoryEventKind


@dataclass(frozen=True, slots=True)
class EpisodeSpan:
    """A contiguous slice of the trajectory log treated as one episode.

    A *view* over the raw log: it holds the events that compose the episode, in
    chronological order. ``closed`` is True when the span ends in a terminal
    boundary (a commit); a trailing open span is uncommitted work-in-progress.
    """

    events: Sequence[TrajectoryEvent]
    closed: bool


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
