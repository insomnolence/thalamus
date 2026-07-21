"""Trust-stamping ingestor decorator (§17.4 step 1 — provenance for threats T1/T3).

Trust is a property of the *corpus*, but nodes are emitted deep inside the language/format-specific
ingestors that don't know their corpus' declared trust. Rather than thread a trust argument through
every ingestor, this decorator wraps any :class:`~thalamus.structural.ingestor.Ingestor` and stamps
``metadata["trust"]`` onto every node it produces. The stamp travels with the node, so a node
surfaced later via a cross-link or graph edge still carries its provenance — the recall-path fence
(``gateway/payload.py``) reads it regardless of how the node reached the payload.

Applied only for non-operator corpora (the build path leaves operator content unstamped — operator
is the payload's implicit default), so it is a no-op on the common single-operator configuration.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from thalamus.core.trust import Trust
from thalamus.core.types import Scope
from thalamus.structural.ingestor import Ingestor
from thalamus.structural.schema import IngestResult


class TrustStampingIngestor:
    """Wraps an ingestor so every emitted node carries ``metadata["trust"] = trust`` (edges pass
    through untouched — trust is a property of content, and edges carry none)."""

    def __init__(self, inner: Ingestor, trust: Trust) -> None:
        self._inner = inner
        self._trust = trust.value

    def ingest_path(self, root: Path, scope: Scope) -> IngestResult:
        result = self._inner.ingest_path(root, scope)
        stamped = [
            replace(node, metadata={**node.metadata, "trust": self._trust})
            for node in result.nodes
        ]
        return IngestResult(nodes=stamped, edges=result.edges)
