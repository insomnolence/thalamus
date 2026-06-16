"""thalamus.dreaming — the offline scheduler of gated, removable passes.

The framework (this package) depends on ``core`` only. Individual passes that
need a hemisphere (structural graph, gateway refresh) live behind the
:class:`DreamingPass` seam and carry those collaborators themselves, so the
scheduler stays removable and the §14.3 actor/proposer firewall stays auditable.
"""

from thalamus.dreaming.base import (
    CycleReport,
    DreamingPass,
    PassContext,
    PassKind,
    PassOutcome,
    PassReport,
    PassStatus,
)
from thalamus.dreaming.belief_audit import BeliefAuditPass, SupersessionProposal
from thalamus.dreaming.centrality_refresh import CentralityRefreshPass
from thalamus.dreaming.cochange_refresh import CoChangeRefreshPass
from thalamus.dreaming.credibility import CredibilityAssessor, CredibilityPass
from thalamus.dreaming.link_resolution import LinkResolutionPass
from thalamus.dreaming.log import (
    DreamLog,
    DreamRecord,
    InMemoryDreamLog,
    JsonlDreamLog,
    deserialize_dream_record,
    read_dream_log,
    serialize_dream_record,
)
from thalamus.dreaming.runtime import MaintenanceTicker
from thalamus.dreaming.scheduler import Scheduler
from thalamus.dreaming.structural_rederive import StructuralRederivePass
from thalamus.dreaming.structural_refresh import StructuralRefreshPass
from thalamus.dreaming.usage_refresh import UsageRefreshPass

__all__ = [
    "BeliefAuditPass",
    "CentralityRefreshPass",
    "CoChangeRefreshPass",
    "CredibilityAssessor",
    "CredibilityPass",
    "CycleReport",
    "DreamLog",
    "DreamRecord",
    "DreamingPass",
    "InMemoryDreamLog",
    "JsonlDreamLog",
    "LinkResolutionPass",
    "MaintenanceTicker",
    "PassContext",
    "PassKind",
    "PassOutcome",
    "PassReport",
    "PassStatus",
    "Scheduler",
    "StructuralRederivePass",
    "StructuralRefreshPass",
    "SupersessionProposal",
    "UsageRefreshPass",
    "deserialize_dream_record",
    "read_dream_log",
    "serialize_dream_record",
]
