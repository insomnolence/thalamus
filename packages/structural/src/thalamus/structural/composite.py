"""Compose multiple ingestors into one — behind the same ``Ingestor`` seam.

Brain 2 is built by more than one pass: the AST structure pass (nodes +
contains/imports/inherits) and the jedi call-resolution pass (``calls`` edges),
with future passes (other languages, document corpora) to follow. ``CompositeIngestor``
runs a sequence of ingestors over the same corpus and merges their nodes+edges into
one ``IngestResult`` — so each pass stays a small, independent, removable ingestor and
the composition just lists them.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from thalamus.core.types import Scope
from thalamus.structural.ingestor import Ingestor
from thalamus.structural.schema import IngestResult, StructuralEdge, StructuralNode


class CompositeIngestor:
    """Runs each ingestor over the corpus and concatenates their results.

    Node identity is shared across passes (``structural.ids``), so the graph layer
    dedups overlapping nodes by id on insert; edges from later passes (e.g. ``calls``)
    reference nodes produced by earlier ones.
    """

    def __init__(self, ingestors: Sequence[Ingestor]) -> None:
        self._ingestors = tuple(ingestors)

    def ingest_path(self, root: Path, scope: Scope) -> IngestResult:
        nodes: list[StructuralNode] = []
        edges: list[StructuralEdge] = []
        for ingestor in self._ingestors:
            result = ingestor.ingest_path(root, scope)
            nodes.extend(result.nodes)
            edges.extend(result.edges)
        return IngestResult(nodes=nodes, edges=edges)
