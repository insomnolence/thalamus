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
)
from thalamus.core.protocols import (
    EmbeddingStore,
    Encoder,
    Retriever,
    Router,
    Store,
    SupersessionIndex,
)
from thalamus.core.serde import deserialize_memory_record, serialize_memory_record
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
    "ConfigurationError",
    "Cue",
    "DimensionMismatchError",
    "EmbeddingStore",
    "Encoder",
    "EncoderError",
    "EpisodeId",
    "EventId",
    "Hemisphere",
    "MemoryId",
    "MemoryRef",
    "MemoryRecord",
    "RepoId",
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
    "Vector",
    "deserialize_memory_record",
    "serialize_memory_record",
]
