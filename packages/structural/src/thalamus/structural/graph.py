"""The structural graph store + k-hop traversal (Brain 2's retrieval substrate).

Deterministic graph traversal (k-hop) is how the structural hemisphere surfaces
*connected* code — the HippoRAG-style associative spreading (§13.19), done by the
graph, not a model. ``InMemoryStructuralGraph`` is the v0 baseline; a Neo4j-backed
implementation (reference: Polynoica ``Neo4jKnowledgeGraph.query_subgraph``) slots
in behind the same protocol for scale + persistence.

Edges may reference targets not present in the graph (e.g. imports of external
modules); traversal tolerates these dangling targets.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from typing import Literal, Protocol, runtime_checkable

from thalamus.structural.schema import IngestResult, StructuralEdge, StructuralNode

Direction = Literal["out", "in", "both"]


@runtime_checkable
class StructuralGraph(Protocol):
    """A re-derivable graph of structural nodes + typed edges, with k-hop traversal."""

    def add(self, result: IngestResult) -> None: ...
    def get(self, node_id: str) -> StructuralNode | None: ...
    def neighbors(
        self, node_id: str, *, edge_types: Sequence[str] | None = None, direction: Direction = "out"
    ) -> list[StructuralNode]: ...
    def k_hop(
        self,
        node_id: str,
        k: int,
        *,
        edge_types: Sequence[str] | None = None,
        direction: Direction = "both",
    ) -> list[StructuralNode]: ...


class InMemoryStructuralGraph:
    """Pure-Python structural graph: node lookup, neighbours, and k-hop BFS."""

    def __init__(self) -> None:
        self._nodes: dict[str, StructuralNode] = {}
        self._out: dict[str, list[StructuralEdge]] = {}
        self._in: dict[str, list[StructuralEdge]] = {}

    def add(self, result: IngestResult) -> None:
        for node in result.nodes:
            self._nodes[node.node_id] = node
        for edge in result.edges:
            self._out.setdefault(edge.source_id, []).append(edge)
            self._in.setdefault(edge.target_id, []).append(edge)

    def get(self, node_id: str) -> StructuralNode | None:
        return self._nodes.get(node_id)

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
        self, node_id: str, *, edge_types: Sequence[str] | None = None, direction: Direction = "out"
    ) -> list[StructuralNode]:
        out: list[StructuralNode] = []
        seen: set[str] = set()
        for adjacent_id in self._adjacent_ids(node_id, edge_types, direction):
            node = self._nodes.get(adjacent_id)
            if node is not None and adjacent_id not in seen:
                seen.add(adjacent_id)
                out.append(node)
        return out

    def k_hop(
        self,
        node_id: str,
        k: int,
        *,
        edge_types: Sequence[str] | None = None,
        direction: Direction = "both",
    ) -> list[StructuralNode]:
        """BFS up to depth ``k`` from ``node_id`` (excluded); returns reached nodes."""
        visited = {node_id}
        out: list[StructuralNode] = []
        frontier: deque[tuple[str, int]] = deque([(node_id, 0)])
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
