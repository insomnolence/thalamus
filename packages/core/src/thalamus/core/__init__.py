"""thalamus.core — the contract layer.

Shared protocols, types, identifiers, and exceptions that every other Thalamus
package depends on. Contains **no implementations, only interfaces** — and no
third-party dependencies.
"""

from thalamus.core.exceptions import (
    ConfigurationError,
    DimensionMismatchError,
    EncoderError,
    StoreError,
    ThalamusError,
    UserFacingError,
)
from thalamus.core.protocols import (
    EmbeddingStore,
    Encoder,
    Retriever,
    Router,
    Store,
    SupersessionIndex,
)
from thalamus.core.redaction import (
    RedactionEvent,
    RedactionResult,
    merge_redaction_events,
    redact_secrets,
)
from thalamus.core.serde import deserialize_memory_record, serialize_memory_record
from thalamus.core.taxonomy import (
    ACCEPTED_KINDS,
    KIND_SYNONYMS,
    RETAINED_KINDS,
    RememberKindInput,
    RetainedKind,
    normalize_kind,
)
from thalamus.core.trust import Trust
from thalamus.core.types import (
    Cue,
    EpisodeId,
    EventId,
    Hemisphere,
    MemoryId,
    MemoryRecord,
    MemoryRef,
    RepoId,
    RetrievalResult,
    Scope,
    ScoredMemory,
    SessionId,
    StructuralRef,
    Supersession,
    TenantId,
    Vector,
)

__all__ = [
    "ACCEPTED_KINDS",
    "ConfigurationError",
    "Cue",
    "DimensionMismatchError",
    "EmbeddingStore",
    "Encoder",
    "EncoderError",
    "EpisodeId",
    "EventId",
    "Hemisphere",
    "KIND_SYNONYMS",
    "MemoryId",
    "MemoryRef",
    "MemoryRecord",
    "RETAINED_KINDS",
    "RedactionEvent",
    "RedactionResult",
    "RememberKindInput",
    "RepoId",
    "RetainedKind",
    "RetrievalResult",
    "Retriever",
    "Router",
    "Scope",
    "ScoredMemory",
    "SessionId",
    "StructuralRef",
    "Store",
    "StoreError",
    "Supersession",
    "SupersessionIndex",
    "TenantId",
    "ThalamusError",
    "Trust",
    "UserFacingError",
    "Vector",
    "deserialize_memory_record",
    "merge_redaction_events",
    "normalize_kind",
    "redact_secrets",
    "serialize_memory_record",
]
