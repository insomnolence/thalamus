"""Neo4j-backed structural hemisphere — Brain 2 in the shared graph substrate.

``InMemoryStructuralGraph`` / ``InMemoryCrossLinkIndex`` are the v0 baselines; these
persist Brain 2 in the *same* Neo4j database as Brain 1 (the foundation §4 decision:
"one graph substrate, separate namespaces"), so the cross-hemisphere link is a
**native edge** between an experiential-memory node and a structural node — not an
external index. Structural AST nodes carry the ``SNode`` label; experiential memory
nodes are the ones ``Neo4jStore`` writes (label ``M_<hemisphere>``); the link is a
``TOUCHES`` relationship between them (§13.19).

The driver is **injected** (created via ``thalamus.store.connect``) so the structural
graph and the memory store share one connection + database. ``neo4j`` is needed only
for typing here; all calls go through the injected driver (matching ``Neo4jStore``).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from thalamus.core.exceptions import StoreError
from thalamus.core.types import MemoryId
from thalamus.structural.graph import Direction
from thalamus.structural.schema import IngestResult, SourceAnchor, StructuralNode

if TYPE_CHECKING:
    from neo4j import Driver

_NODE = "SNode"  # structural AST node label
_EDGE = "STRUCT_EDGE"  # one relationship type; the edge kind is a `type` property
_TOUCHES = "TOUCHES"  # native cross-hemisphere edge: memory -> structural node


def _run(driver: Driver, database: str, cypher: str, **params: Any) -> list[Any]:
    try:
        with driver.session(database=database) as session:
            return list(session.run(cypher, **params))
    except Exception as exc:
        raise StoreError(f"Neo4j structural operation failed: {exc}") from exc


def _arrows(direction: Direction) -> tuple[str, str]:
    """Left/right relationship arrows for a traversal direction."""
    if direction == "out":
        return "-", "->"
    if direction == "in":
        return "<-", "-"
    return "-", "-"  # both (undirected)


class Neo4jStructuralGraph:
    """``StructuralGraph`` over Neo4j: persisted nodes/edges + Cypher k-hop traversal.

    Edge targets absent from the corpus (e.g. external imports) become bare stub nodes
    (no ``kind``); reads filter ``kind IS NOT NULL`` so stubs never surface as results,
    matching the in-memory graph's tolerance of dangling targets."""

    def __init__(self, driver: Driver, *, database: str = "neo4j") -> None:
        self._driver = driver
        self._database = database

    def add(self, result: IngestResult) -> None:
        nodes = [self._node_props(node) for node in result.nodes]
        if nodes:
            _run(
                self._driver,
                self._database,
                f"UNWIND $nodes AS n MERGE (m:{_NODE} {{node_id: n.node_id}}) "
                "SET m.kind = n.kind, m.label = n.label, m.anchor_path = n.anchor_path, "
                "m.anchor_line_start = n.anchor_line_start, m.anchor_line_end = n.anchor_line_end, "
                "m.metadata_json = n.metadata_json",
                nodes=nodes,
            )
        edges = [{"s": e.source_id, "t": e.target_id, "type": e.type} for e in result.edges]
        if edges:
            _run(
                self._driver,
                self._database,
                f"UNWIND $edges AS e MERGE (a:{_NODE} {{node_id: e.s}}) "
                f"MERGE (b:{_NODE} {{node_id: e.t}}) "
                f"MERGE (a)-[:{_EDGE} {{type: e.type}}]->(b)",
                edges=edges,
            )

    def get(self, node_id: str) -> StructuralNode | None:
        rows = _run(
            self._driver,
            self._database,
            f"MATCH (m:{_NODE} {{node_id: $node_id}}) WHERE m.kind IS NOT NULL RETURN m",
            node_id=node_id,
        )
        return None if not rows else self._to_node(dict(rows[0]["m"]))

    def neighbors(
        self,
        node_id: str,
        *,
        edge_types: Sequence[str] | None = None,
        direction: Direction = "out",
    ) -> list[StructuralNode]:
        left, right = _arrows(direction)
        type_filter = "AND r.type IN $types " if edge_types is not None else ""
        rows = _run(
            self._driver,
            self._database,
            f"MATCH (n:{_NODE} {{node_id: $node_id}}){left}[r:{_EDGE}]{right}(m:{_NODE}) "
            f"WHERE m.kind IS NOT NULL {type_filter}RETURN DISTINCT m",
            node_id=node_id,
            types=list(edge_types) if edge_types is not None else None,
        )
        return [self._to_node(dict(row["m"])) for row in rows]

    def k_hop(
        self,
        node_id: str,
        k: int,
        *,
        edge_types: Sequence[str] | None = None,
        direction: Direction = "both",
    ) -> list[StructuralNode]:
        if k <= 0:
            return []
        left, right = _arrows(direction)
        type_filter = (
            "AND ALL(rel IN relationships(p) WHERE rel.type IN $types) "
            if edge_types is not None
            else ""
        )
        rows = _run(
            self._driver,
            self._database,
            f"MATCH p = (n:{_NODE} {{node_id: $node_id}}){left}[:{_EDGE}*1..{int(k)}]{right}"
            f"(m:{_NODE}) "
            f"WHERE m.node_id <> $node_id AND m.kind IS NOT NULL {type_filter}RETURN DISTINCT m",
            node_id=node_id,
            types=list(edge_types) if edge_types is not None else None,
        )
        return [self._to_node(dict(row["m"])) for row in rows]

    def close(self) -> None:
        self._driver.close()

    @staticmethod
    def _node_props(node: StructuralNode) -> dict[str, Any]:
        anchor = node.anchor
        return {
            "node_id": node.node_id,
            "kind": node.kind,
            "label": node.label,
            "anchor_path": None if anchor is None else anchor.path,
            "anchor_line_start": None if anchor is None else anchor.line_start,
            "anchor_line_end": None if anchor is None else anchor.line_end,
            "metadata_json": json.dumps(dict(node.metadata)),
        }

    @staticmethod
    def _to_node(props: Mapping[str, Any]) -> StructuralNode:
        anchor: SourceAnchor | None = None
        if props.get("anchor_path") is not None:
            anchor = SourceAnchor(
                path=str(props["anchor_path"]),
                line_start=int(props["anchor_line_start"]),
                line_end=int(props["anchor_line_end"]),
            )
        return StructuralNode(
            node_id=str(props["node_id"]),
            kind=str(props["kind"]),
            label=str(props["label"]),
            anchor=anchor,
            metadata=json.loads(str(props.get("metadata_json", "{}"))),
        )


class Neo4jCrossLinkIndex:
    """``CrossLinkIndex`` as native edges ``(memory)-[:TOUCHES]->(structural node)``.

    Links connect experiential-memory nodes (``Neo4jStore`` writes them as
    ``memory_label``, default ``M_experiential``) to structural ``SNode``s in the same
    database — the §13.19 link as a first-class graph edge. ``link`` only connects nodes
    that already exist; it never fabricates a memory or code node."""

    def __init__(
        self, driver: Driver, *, database: str = "neo4j", memory_label: str = "M_experiential"
    ) -> None:
        self._driver = driver
        self._database = database
        self._memory_label = memory_label

    def link(self, memory_id: MemoryId, node_id: str) -> None:
        _run(
            self._driver,
            self._database,
            f"MATCH (m:{self._memory_label} {{memory_id: $mid}}), (s:{_NODE} {{node_id: $nid}}) "
            f"MERGE (m)-[:{_TOUCHES}]->(s)",
            mid=str(memory_id),
            nid=node_id,
        )

    def nodes_for(self, memory_id: MemoryId) -> list[str]:
        rows = _run(
            self._driver,
            self._database,
            f"MATCH (m:{self._memory_label} {{memory_id: $mid}})-[:{_TOUCHES}]->(s:{_NODE}) "
            "RETURN s.node_id AS node_id ORDER BY node_id",
            mid=str(memory_id),
        )
        return [str(row["node_id"]) for row in rows]

    def memories_for(self, node_id: str) -> list[MemoryId]:
        rows = _run(
            self._driver,
            self._database,
            f"MATCH (m:{self._memory_label})-[:{_TOUCHES}]->(s:{_NODE} {{node_id: $nid}}) "
            "RETURN m.memory_id AS memory_id ORDER BY memory_id",
            nid=node_id,
        )
        return [MemoryId(str(row["memory_id"])) for row in rows]

    def close(self) -> None:
        self._driver.close()
