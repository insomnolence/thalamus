"""The structural graph store + k-hop traversal (Brain 2's retrieval substrate).

Deterministic graph traversal (k-hop) is how the structural hemisphere surfaces
*connected* code — the HippoRAG-style associative spreading (§13.19), done by the
graph, not a model. ``InMemoryStructuralGraph`` is the v0 baseline; a Neo4j-backed
implementation (reference: an earlier project of ours) slots
in behind the same protocol for scale + persistence.

Edges may reference targets not present in the graph (e.g. imports of external
modules); traversal tolerates these dangling targets.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Sequence
from threading import RLock
from typing import Literal, Protocol, runtime_checkable

from thalamus.core.types import Scope, StructuralRef
from thalamus.structural.schema import IngestResult, StructuralEdge, StructuralNode

Direction = Literal["out", "in", "both"]


@runtime_checkable
class StructuralGraph(Protocol):
    """A re-derivable graph of structural nodes + typed edges, with k-hop traversal."""

    def add(self, result: IngestResult) -> None: ...
    def replace(self, result: IngestResult) -> None: ...
    def remove(self, refs: Iterable[StructuralRef]) -> None: ...
    def nodes_of_kind(self, scope: Scope, kind: str) -> list[StructuralNode]: ...
    def get(self, ref: StructuralRef) -> StructuralNode | None: ...
    def neighbors(
        self,
        ref: StructuralRef,
        *,
        edge_types: Sequence[str] | None = None,
        direction: Direction = "out",
    ) -> list[StructuralNode]: ...
    def neighbors_many(
        self,
        refs: Sequence[StructuralRef],
        *,
        edge_types: Sequence[str] | None = None,
        direction: Direction = "out",
    ) -> dict[StructuralRef, list[StructuralNode]]: ...
    def k_hop(
        self,
        ref: StructuralRef,
        k: int,
        *,
        edge_types: Sequence[str] | None = None,
        direction: Direction = "both",
    ) -> list[StructuralNode]: ...


class InMemoryStructuralGraph:
    """Pure-Python structural graph: node lookup, neighbours, and k-hop BFS."""

    def __init__(self, scope: Scope) -> None:
        self._scope = scope
        self._lock = RLock()
        self._nodes: dict[str, StructuralNode] = {}
        self._out: dict[str, list[StructuralEdge]] = {}
        self._in: dict[str, list[StructuralEdge]] = {}

    def add(self, result: IngestResult) -> None:
        for node in result.nodes:
            if node.scope != self._scope:
                raise ValueError("structural node scope does not match graph scope")
        with self._lock:
            for node in result.nodes:
                self._nodes[node.node_id] = node
            for edge in result.edges:
                out = self._out.setdefault(edge.source_id, [])
                if edge not in out:
                    out.append(edge)
                inbound = self._in.setdefault(edge.target_id, [])
                if edge not in inbound:
                    inbound.append(edge)

    def replace(self, result: IngestResult) -> None:
        for node in result.nodes:
            if node.scope != self._scope:
                raise ValueError("structural node scope does not match graph scope")
        with self._lock:
            self._nodes.clear()
            self._out.clear()
            self._in.clear()
            self.add(result)

    def remove(self, refs: Iterable[StructuralRef]) -> None:
        with self._lock:
            ids = {ref.node_id for ref in refs if ref.scope == self._scope}
            if not ids:
                return
            for node_id in ids:
                self._nodes.pop(node_id, None)
                self._out.pop(node_id, None)
                self._in.pop(node_id, None)
            # Drop edges referencing a removed node from the surviving adjacency lists.
            for adjacency in (self._out, self._in):
                for node_id, edges in list(adjacency.items()):
                    adjacency[node_id] = [
                        edge for edge in edges
                        if edge.source_id not in ids and edge.target_id not in ids
                    ]

    def nodes_of_kind(self, scope: Scope, kind: str) -> list[StructuralNode]:
        if scope != self._scope:
            return []
        with self._lock:
            return [node for node in self._nodes.values() if node.kind == kind]

    def get(self, ref: StructuralRef) -> StructuralNode | None:
        if ref.scope != self._scope:
            return None
        with self._lock:
            return self._nodes.get(ref.node_id)

    def _adjacent_ids(
        self, node_id: str, edge_types: Sequence[str] | None, direction: Direction
    ) -> list[str]:
        allowed = set(edge_types) if edge_types is not None else None
        ids: list[str] = []
        if direction in ("out", "both"):
            for edge in self._out.get(node_id, []):
                if allowed is None or edge.type in allowed:
                    ids.append(edge.target_id)
        if direction in ("in", "both"):
            for edge in self._in.get(node_id, []):
                if allowed is None or edge.type in allowed:
                    ids.append(edge.source_id)
        return ids

    def neighbors(
        self,
        ref: StructuralRef,
        *,
        edge_types: Sequence[str] | None = None,
        direction: Direction = "out",
    ) -> list[StructuralNode]:
        if ref.scope != self._scope:
            return []
        with self._lock:
            out: list[StructuralNode] = []
            seen: set[str] = set()
            for adjacent_id in self._adjacent_ids(ref.node_id, edge_types, direction):
                node = self._nodes.get(adjacent_id)
                if node is not None and adjacent_id not in seen:
                    seen.add(adjacent_id)
                    out.append(node)
            return out

    def neighbors_many(
        self,
        refs: Sequence[StructuralRef],
        *,
        edge_types: Sequence[str] | None = None,
        direction: Direction = "out",
    ) -> dict[StructuralRef, list[StructuralNode]]:
        if not refs:
            return {}
        with self._lock:
            return {
                ref: self.neighbors(ref, edge_types=edge_types, direction=direction)
                for ref in refs
            }

    def k_hop(
        self,
        ref: StructuralRef,
        k: int,
        *,
        edge_types: Sequence[str] | None = None,
        direction: Direction = "both",
    ) -> list[StructuralNode]:
        """BFS up to depth ``k`` from ``node_id`` (excluded); returns reached nodes."""
        if ref.scope != self._scope:
            return []
        with self._lock:
            visited = {ref.node_id}
            out: list[StructuralNode] = []
            frontier: deque[tuple[str, int]] = deque([(ref.node_id, 0)])
            while frontier:
                current, depth = frontier.popleft()
                if depth >= k:
                    continue
                for adjacent_id in self._adjacent_ids(current, edge_types, direction):
                    if adjacent_id in visited:
                        continue
                    visited.add(adjacent_id)
                    node = self._nodes.get(adjacent_id)
                    if node is not None:
                        out.append(node)
                    frontier.append((adjacent_id, depth + 1))
            return out
