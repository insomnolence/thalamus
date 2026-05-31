"""thalamus.experiential — Brain 1 (experiential hemisphere): episodes, the why, beliefs.

The ingestion spine derives episodes from the trajectory log (§13.11b) and
materializes them as Brain-1 records — the unfinished half of §10 step 1
("capture the why"). See ``docs/deep-dives/path-to-real-data.md``. Belief revision
(§13.18) and the derived why-view (§13.17) build on this foundation.
"""

from thalamus.experiential.episode import EpisodeBuilder, WhyComponent, WhyProvenance
from thalamus.experiential.fate import (
    FatePolarity,
    FateSignals,
    FateVerdict,
    OutcomeTier,
    assess_fate,
    fate_success,
)
from thalamus.experiential.ingest import ingest_episodes
from thalamus.experiential.neo4j_supersession import Neo4jSupersessionIndex
from thalamus.experiential.outcome import EpisodeOutcome, classify_outcome, is_success
from thalamus.experiential.recorded_outcome import (
    RecordedEvent,
    TextOutcome,
    parse_recorded_outcome,
)
from thalamus.experiential.segmentation import (
    CommitBoundedSegmenter,
    EpisodeSegmenter,
    EpisodeSpan,
    SessionBoundedSegmenter,
)
from thalamus.experiential.supersession import InMemorySupersessionIndex
from thalamus.experiential.sync import (
    Checkpoint,
    CommitSource,
    FileCheckpoint,
    GitEpisodeIngestor,
    InMemoryCheckpoint,
    SessionStampingSource,
)

__all__ = [
    "Checkpoint",
    "CommitBoundedSegmenter",
    "CommitSource",
    "EpisodeBuilder",
    "EpisodeOutcome",
    "EpisodeSegmenter",
    "EpisodeSpan",
    "FatePolarity",
    "FateSignals",
    "FateVerdict",
    "FileCheckpoint",
    "GitEpisodeIngestor",
    "InMemoryCheckpoint",
    "InMemorySupersessionIndex",
    "Neo4jSupersessionIndex",
    "OutcomeTier",
    "RecordedEvent",
    "SessionBoundedSegmenter",
    "SessionStampingSource",
    "TextOutcome",
    "WhyComponent",
    "WhyProvenance",
    "assess_fate",
    "classify_outcome",
    "fate_success",
    "ingest_episodes",
    "is_success",
    "parse_recorded_outcome",
]
