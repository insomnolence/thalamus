"""thalamus.experiential — Brain 1 (experiential hemisphere): episodes, the why, beliefs.

The ingestion spine derives episodes from the trajectory log (§13.11b) and
materializes them as Brain-1 records — the unfinished half of §10 step 1
("capture the why"). See ``docs/deep-dives/path-to-real-data.md``. Belief revision
(§13.18) and the derived why-view (§13.17) build on this foundation.
"""

from thalamus.experiential.episode import EpisodeBuilder, WhyComponent, WhyProvenance
from thalamus.experiential.ingest import ingest_episodes
from thalamus.experiential.segmentation import (
    CommitBoundedSegmenter,
    EpisodeSegmenter,
    EpisodeSpan,
)
from thalamus.experiential.sync import (
    Checkpoint,
    CommitSource,
    FileCheckpoint,
    GitEpisodeIngestor,
    InMemoryCheckpoint,
)

__all__ = [
    "Checkpoint",
    "CommitBoundedSegmenter",
    "CommitSource",
    "EpisodeBuilder",
    "EpisodeSegmenter",
    "EpisodeSpan",
    "FileCheckpoint",
    "GitEpisodeIngestor",
    "InMemoryCheckpoint",
    "WhyComponent",
    "WhyProvenance",
    "ingest_episodes",
]
