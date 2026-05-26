"""thalamus.gateway — the single conduit the actuator talks to.

``Gateway`` is the pure orchestration core (cue -> ``ContextPayload``), optionally
enriched with Brain 2 structural context via cross-hemisphere links (§13.19). The
MCP transport (``build_server``) is a thin optional adapter requiring the ``mcp`` extra.
"""

from thalamus.gateway.gateway import Gateway, StructuralLinkedRetriever
from thalamus.gateway.payload import ContextPayload, MemoryItem, StructuralItem
from thalamus.gateway.server import build_server

__all__ = [
    "ContextPayload",
    "Gateway",
    "MemoryItem",
    "StructuralItem",
    "StructuralLinkedRetriever",
    "build_server",
]
