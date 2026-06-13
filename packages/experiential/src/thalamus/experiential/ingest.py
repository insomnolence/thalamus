"""The experiential ingestion spine — trajectory log → episode records in Brain 1.

The keystone that turns captured activity into a populated experiential hemisphere
(``docs/deep-dives/path-to-real-data.md``): segment the trajectory log (§13.16),
materialize each span as an episode ``MemoryRecord`` (§13.17), embed its content,
and store it. Depends only on the ``core`` ``Encoder``/``Store`` protocols and the
swappable segmenter/builder seams — so the boring base stays measurable and any
piece is replaceable (§14.5).

Idempotent: episode ids are stable, so re-running over the same (growing) log
refreshes records in place rather than duplicating — segmentation stays a derived
view over the raw, irreversible log (§14.1).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from thalamus.core.protocols import Encoder, Store
from thalamus.core.types import MemoryRecord
from thalamus.experiential.episode import EpisodeBuilder
from thalamus.experiential.segmentation import CommitBoundedSegmenter, EpisodeSegmenter
from thalamus.instrumentation import TrajectoryEvent


def ingest_episodes(
    events: Sequence[TrajectoryEvent],
    *,
    encoder: Encoder,
    store: Store,
    segmenter: EpisodeSegmenter | None = None,
    builder: EpisodeBuilder | None = None,
    skip_existing: Callable[[str], bool] | None = None,
) -> list[MemoryRecord]:
    """Segment ``events`` into episodes, materialize and store them, return the records.

    ``segmenter``/``builder`` default to the deterministic S1 commit-bounded spine;
    pass alternatives to swap the cut or the materialization behind their seams.

    ``skip_existing``, when given, is consulted with each candidate episode's id; ids it
    accepts are *not* re-encoded or re-stored, and are absent from the returned list. This
    is the incremental path: segmentation over a growing log is cheap, but embedding is not,
    so a caller that already holds an episode (e.g. the warm serve's capture tick) avoids the
    BGE cost on everything but genuinely new spans. Omit it for the full re-embed-in-place
    refresh the idempotent §14.1 re-derive relies on (changed builder logic → fresh vector).
    """
    segmenter = segmenter or CommitBoundedSegmenter()
    builder = builder or EpisodeBuilder()
    records: list[MemoryRecord] = []
    for span in segmenter.segment(events):
        record = builder.build(span)
        if record is None:
            continue
        if skip_existing is not None and skip_existing(str(record.memory_id)):
            continue
        store.add(record, encoder.encode([record.content])[0])
        records.append(record)
    return records
