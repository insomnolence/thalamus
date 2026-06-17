"""thalamus.gateway — the single conduit the actuator talks to.

``Gateway`` is the pure orchestration core (cue -> ``ContextPayload``), optionally
enriched with Brain 2 structural context via cross-hemisphere links (§13.19). The
MCP transport (``build_server``) is a thin optional adapter requiring the ``mcp`` extra.
"""

from thalamus.gateway.gateway import (
    Gateway,
    StructuralLinkedRetriever,
    StructuralRelevanceRetriever,
    SupersededDemotingRetriever,
)
from thalamus.gateway.payload import (
    ContextPayload,
    FindingItem,
    MemoryItem,
    StructuralItem,
    SupersededNote,
)
from thalamus.gateway.planner import (
    CoverageReport,
    PlanBrief,
    Planner,
    PlannerConfig,
    RadiusNode,
)
from thalamus.gateway.server import build_server
from thalamus.gateway.views import DerivedViews, DerivedViewsRef

__all__ = [
    "ContextPayload",
    "CoverageReport",
    "DerivedViews",
    "DerivedViewsRef",
    "FindingItem",
    "Gateway",
    "MemoryItem",
    "PlanBrief",
    "Planner",
    "PlannerConfig",
    "RadiusNode",
    "StructuralItem",
    "StructuralLinkedRetriever",
    "StructuralRelevanceRetriever",
    "SupersededDemotingRetriever",
    "SupersededNote",
    "build_server",
]
