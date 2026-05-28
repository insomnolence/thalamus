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
from thalamus.dreaming.log import (
    DreamLog,
    DreamRecord,
    InMemoryDreamLog,
    JsonlDreamLog,
    deserialize_dream_record,
    read_dream_log,
    serialize_dream_record,
)
from thalamus.dreaming.scheduler import Scheduler

__all__ = [
    "CycleReport",
    "DreamLog",
    "DreamRecord",
    "DreamingPass",
    "InMemoryDreamLog",
    "JsonlDreamLog",
    "PassContext",
    "PassKind",
    "PassOutcome",
    "PassReport",
    "PassStatus",
    "Scheduler",
    "deserialize_dream_record",
    "read_dream_log",
    "serialize_dream_record",
]
