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
from thalamus.core.protocols import Encoder, Retriever, Router, Store
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
    TenantId,
    Vector,
)

__all__ = [
    "ConfigurationError",
    "Cue",
    "DimensionMismatchError",
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
    "TenantId",
    "ThalamusError",
    "Vector",
]
