"""Corpus-agnostic structural-graph schema (Brain 2).

The structural hemisphere is a *re-derivable* graph over an external corpus.
**Code is the first corpus** (a precise, deterministic AST graph); other languages
and document corpora are future ingestors behind the same seam (see
``deep-dives/structural-hemisphere.md``). Node/edge *kinds* are an **open typed
vocabulary** so the schema is shared across corpora without being code-specific.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from thalamus.core.types import Scope, StructuralRef


@dataclass(frozen=True, slots=True)
class SourceAnchor:
    """Where a node lives in source — for excerpts and cross-hemisphere anchoring."""

    path: str
    line_start: int
    line_end: int


@dataclass(frozen=True, slots=True)
class StructuralNode:
    """A node in the structural graph.

    ``node_id`` is a stable identity that survives re-ingestion
    (e.g. ``"function:pkg.mod.func"``); ``kind`` is an open vocabulary
    (code: module/class/function/method; docs: section/...).
    """

    node_id: str
    kind: str
    label: str
    scope: Scope
    anchor: SourceAnchor | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def ref(self) -> StructuralRef:
        return StructuralRef(scope=self.scope, node_id=self.node_id)


@dataclass(frozen=True, slots=True)
class StructuralEdge:
    """A typed directed edge (code: contains/inherits/imports/calls; open vocab)."""

    source_id: str
    target_id: str
    type: str


@dataclass(frozen=True, slots=True)
class IngestResult:
    """The nodes + edges produced by an Ingestor over a corpus."""

    nodes: Sequence[StructuralNode]
    edges: Sequence[StructuralEdge]
