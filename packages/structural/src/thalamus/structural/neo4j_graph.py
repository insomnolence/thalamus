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
from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from thalamus.core.exceptions import StoreError
from thalamus.core.types import MemoryId, MemoryRef, RepoId, Scope, StructuralRef, TenantId
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


def _node_props(node: StructuralNode) -> dict[str, Any]:
    """Flatten a node to SNode properties (one schema, shared by graph + index)."""
    anchor = node.anchor
    return {
        "node_id": node.node_id,
        "tenant_id": str(node.scope.tenant_id),
        "repo_id": str(node.scope.repo_id),
        "kind": node.kind,
        "label": node.label,
        "anchor_path": None if anchor is None else anchor.path,
        "anchor_line_start": None if anchor is None else anchor.line_start,
        "anchor_line_end": None if anchor is None else anchor.line_end,
        "metadata_json": json.dumps(dict(node.metadata)),
    }


def _to_node(props: Mapping[str, Any]) -> StructuralNode:
    """Reconstruct a :class:`StructuralNode` from SNode properties."""
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
        scope=Scope(
            tenant_id=TenantId(str(props["tenant_id"])), repo_id=RepoId(str(props["repo_id"]))
        ),
        anchor=anchor,
        metadata=json.loads(str(props.get("metadata_json", "{}"))),
    )


def _ensure_snode_schema(driver: Driver, database: str = "neo4j") -> None:
    _run(
        driver,
        database,
        f"CREATE CONSTRAINT snode_scope IF NOT EXISTS "
        f"FOR (m:{_NODE}) REQUIRE (m.tenant_id, m.repo_id, m.node_id) IS UNIQUE",
    )
    _run(
        driver,
        database,
        f"CREATE INDEX snode_scope_lookup IF NOT EXISTS "
        f"FOR (m:{_NODE}) ON (m.tenant_id, m.repo_id)",
    )
    _run(
        driver,
        database,
        f"CREATE INDEX snode_kind_lookup IF NOT EXISTS "
        f"FOR (m:{_NODE}) ON (m.tenant_id, m.repo_id, m.kind)",
    )


class Neo4jStructuralGraph:
    """``StructuralGraph`` over Neo4j: persisted nodes/edges + Cypher k-hop traversal.

    Edge targets absent from the corpus (e.g. external imports) become bare stub nodes
    (no ``kind``); reads filter ``kind IS NOT NULL`` so stubs never surface as results,
    matching the in-memory graph's tolerance of dangling targets."""

    def __init__(self, driver: Driver, scope: Scope, *, database: str = "neo4j") -> None:
        self._driver = driver
        self._scope = scope
        self._database = database
        _ensure_snode_schema(self._driver, self._database)

    def add(self, result: IngestResult) -> None:
        nodes = [_node_props(node) for node in result.nodes]
        if nodes:
            _run(
                self._driver,
                self._database,
                f"UNWIND $nodes AS n MERGE (m:{_NODE} "
                "{tenant_id: n.tenant_id, repo_id: n.repo_id, node_id: n.node_id}) "
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
                f"UNWIND $edges AS e MERGE (a:{_NODE} "
                "{tenant_id: $tenant_id, repo_id: $repo_id, node_id: e.s}) "
                f"MERGE (b:{_NODE} "
                "{tenant_id: $tenant_id, repo_id: $repo_id, node_id: e.t}) "
                f"MERGE (a)-[:{_EDGE} {{type: e.type}}]->(b)",
                edges=edges,
                tenant_id=str(self._scope.tenant_id),
                repo_id=str(self._scope.repo_id),
            )

    def replace(self, result: IngestResult) -> None:
        _run(
            self._driver,
            self._database,
            f"MATCH (m:{_NODE} {{tenant_id: $tenant_id, repo_id: $repo_id}}) DETACH DELETE m",
            tenant_id=str(self._scope.tenant_id),
            repo_id=str(self._scope.repo_id),
        )
        self.add(result)

    def remove(self, refs: Iterable[StructuralRef]) -> None:
        ids = [ref.node_id for ref in refs if ref.scope == self._scope]
        if not ids:
            return
        _run(
            self._driver,
            self._database,
            f"MATCH (m:{_NODE} {{tenant_id: $tenant_id, repo_id: $repo_id}}) "
            "WHERE m.node_id IN $ids DETACH DELETE m",
            tenant_id=str(self._scope.tenant_id),
            repo_id=str(self._scope.repo_id),
            ids=ids,
        )

    def nodes_of_kind(self, scope: Scope, kind: str) -> list[StructuralNode]:
        if scope != self._scope:
            return []
        rows = _run(
            self._driver,
            self._database,
            f"MATCH (m:{_NODE} {{tenant_id: $tenant_id, repo_id: $repo_id, kind: $kind}}) RETURN m",
            tenant_id=str(scope.tenant_id),
            repo_id=str(scope.repo_id),
            kind=kind,
        )
        return [_to_node(dict(row["m"])) for row in rows]

    def get(self, ref: StructuralRef) -> StructuralNode | None:
        if ref.scope != self._scope:
            return None
        rows = _run(
            self._driver,
            self._database,
            f"MATCH (m:{_NODE} "
            "{tenant_id: $tenant_id, repo_id: $repo_id, node_id: $node_id}) "
            "WHERE m.kind IS NOT NULL RETURN m",
            node_id=ref.node_id,
            tenant_id=str(ref.scope.tenant_id),
            repo_id=str(ref.scope.repo_id),
        )
        return None if not rows else _to_node(dict(rows[0]["m"]))

    def neighbors(
        self,
        ref: StructuralRef,
        *,
        edge_types: Sequence[str] | None = None,
        direction: Direction = "out",
    ) -> list[StructuralNode]:
        if ref.scope != self._scope:
            return []
        left, right = _arrows(direction)
        type_filter = "AND r.type IN $types " if edge_types is not None else ""
        rows = _run(
            self._driver,
            self._database,
            f"MATCH (n:{_NODE} "
            f"{{tenant_id: $tenant_id, repo_id: $repo_id, node_id: $node_id}})"
            f"{left}[r:{_EDGE}]{right}(m:{_NODE}) "
            f"WHERE m.tenant_id = $tenant_id AND m.repo_id = $repo_id "
            f"AND m.kind IS NOT NULL {type_filter}RETURN DISTINCT m",
            node_id=ref.node_id,
            tenant_id=str(ref.scope.tenant_id),
            repo_id=str(ref.scope.repo_id),
            types=list(edge_types) if edge_types is not None else None,
        )
        return [_to_node(dict(row["m"])) for row in rows]

    def neighbors_many(
        self,
        refs: Sequence[StructuralRef],
        *,
        edge_types: Sequence[str] | None = None,
        direction: Direction = "out",
    ) -> dict[StructuralRef, list[StructuralNode]]:
        if not refs:
            return {}
        nids = [ref.node_id for ref in refs if ref.scope == self._scope]
        if not nids:
            return {ref: [] for ref in refs}
        left, right = _arrows(direction)
        type_filter = "AND r.type IN $types " if edge_types is not None else ""
        rows = _run(
            self._driver,
            self._database,
            f"UNWIND $nids AS nid "
            f"MATCH (n:{_NODE} {{tenant_id: $tenant_id, repo_id: $repo_id, node_id: nid}})"
            f"{left}[r:{_EDGE}]{right}(m:{_NODE}) "
            f"WHERE m.tenant_id = $tenant_id AND m.repo_id = $repo_id "
            f"AND m.kind IS NOT NULL {type_filter}RETURN DISTINCT nid, m",
            nids=nids,
            tenant_id=str(self._scope.tenant_id),
            repo_id=str(self._scope.repo_id),
            types=list(edge_types) if edge_types is not None else None,
        )
        result: dict[StructuralRef, list[StructuralNode]] = {ref: [] for ref in refs}
        for row in rows:
            sref = StructuralRef(self._scope, str(row["nid"]))
            node = _to_node(dict(row["m"]))
            if sref in result:
                result[sref].append(node)
        return result

    def k_hop(
        self,
        ref: StructuralRef,
        k: int,
        *,
        edge_types: Sequence[str] | None = None,
        direction: Direction = "both",
    ) -> list[StructuralNode]:
        if k <= 0 or ref.scope != self._scope:
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
            f"MATCH p = (n:{_NODE} "
            f"{{tenant_id: $tenant_id, repo_id: $repo_id, node_id: $node_id}})"
            f"{left}[:{_EDGE}*1..{int(k)}]{right}"
            f"(m:{_NODE}) "
            f"WHERE m.node_id <> $node_id AND m.tenant_id = $tenant_id "
            f"AND m.repo_id = $repo_id AND m.kind IS NOT NULL {type_filter}RETURN DISTINCT m",
            node_id=ref.node_id,
            tenant_id=str(ref.scope.tenant_id),
            repo_id=str(ref.scope.repo_id),
            types=list(edge_types) if edge_types is not None else None,
        )
        return [_to_node(dict(row["m"])) for row in rows]

    def close(self) -> None:
        self._driver.close()


class Neo4jCrossLinkIndex:
    """``CrossLinkIndex`` as native edges ``(memory)-[:TOUCHES]->(structural node)``.

    Links connect experiential-memory nodes (``Neo4jStore`` writes them as
    ``memory_label``, default ``M_experiential``) to structural ``SNode``s in the same
    database — the §13.19 link as a first-class graph edge. ``link`` only connects nodes
    that already exist; it never fabricates a memory or code node."""

    def __init__(
        self,
        driver: Driver,
        scope: Scope,
        *,
        database: str = "neo4j",
        memory_label: str = "M_experiential",
    ) -> None:
        self._driver = driver
        self._scope = scope
        self._database = database
        self._memory_label = memory_label
        _ensure_snode_schema(self._driver, self._database)

    def link(self, memory: MemoryRef, node: StructuralRef) -> None:
        self.link_many([(memory, node)])

    def link_many(self, pairs: Iterable[tuple[MemoryRef, StructuralRef]]) -> None:
        rows = [
            {
                "mid": str(memory.memory_id),
                "nid": node.node_id,
                "tenant_id": str(self._scope.tenant_id),
                "repo_id": str(self._scope.repo_id),
            }
            for memory, node in pairs
            if memory.scope == self._scope and node.scope == self._scope
        ]
        if not rows:
            return
        _run(
            self._driver,
            self._database,
            f"UNWIND $rows AS r "
            f"MATCH (m:{self._memory_label} "
            "{tenant_id: r.tenant_id, repo_id: r.repo_id, memory_id: r.mid}), "
            f"(s:{_NODE} {{tenant_id: r.tenant_id, repo_id: r.repo_id, node_id: r.nid}}) "
            f"MERGE (m)-[:{_TOUCHES}]->(s)",
            rows=rows,
        )

    def nodes_for(self, memory: MemoryRef) -> list[StructuralRef]:
        if memory.scope != self._scope:
            return []
        rows = _run(
            self._driver,
            self._database,
            f"MATCH (m:{self._memory_label} "
            "{tenant_id: $tenant_id, repo_id: $repo_id, memory_id: $mid})"
            f"-[:{_TOUCHES}]->(s:{_NODE} {{tenant_id: $tenant_id, repo_id: $repo_id}}) "
            "RETURN s.node_id AS node_id ORDER BY node_id",
            mid=str(memory.memory_id),
            tenant_id=str(self._scope.tenant_id),
            repo_id=str(self._scope.repo_id),
        )
        return [StructuralRef(self._scope, str(row["node_id"])) for row in rows]

    def nodes_for_many(
        self, memories: Sequence[MemoryRef]
    ) -> dict[MemoryRef, list[StructuralRef]]:
        if not memories:
            return {}
        mids = [str(m.memory_id) for m in memories if m.scope == self._scope]
        if not mids:
            return {memory: [] for memory in memories}
        rows = _run(
            self._driver,
            self._database,
            f"UNWIND $mids AS mid "
            f"MATCH (m:{self._memory_label} "
            "{tenant_id: $tenant_id, repo_id: $repo_id, memory_id: mid})"
            f"-[:{_TOUCHES}]->(s:{_NODE} {{tenant_id: $tenant_id, repo_id: $repo_id}}) "
            "RETURN mid, s.node_id AS node_id ORDER BY node_id",
            mids=mids,
            tenant_id=str(self._scope.tenant_id),
            repo_id=str(self._scope.repo_id),
        )
        result: dict[MemoryRef, list[StructuralRef]] = {memory: [] for memory in memories}
        for row in rows:
            mref = MemoryRef(self._scope, MemoryId(str(row["mid"])))
            sref = StructuralRef(self._scope, str(row["node_id"]))
            if mref in result:
                result[mref].append(sref)
        return result

    def memories_for(self, node: StructuralRef) -> list[MemoryRef]:
        if node.scope != self._scope:
            return []
        rows = _run(
            self._driver,
            self._database,
            f"MATCH (m:{self._memory_label} "
            "{tenant_id: $tenant_id, repo_id: $repo_id})-[:"
            f"{_TOUCHES}]->(s:{_NODE} "
            "{tenant_id: $tenant_id, repo_id: $repo_id, node_id: $nid}) "
            "RETURN m.memory_id AS memory_id ORDER BY memory_id",
            nid=node.node_id,
            tenant_id=str(self._scope.tenant_id),
            repo_id=str(self._scope.repo_id),
        )
        return [MemoryRef(self._scope, MemoryId(str(row["memory_id"]))) for row in rows]

    def memories_for_many(
        self, nodes: Sequence[StructuralRef]
    ) -> dict[StructuralRef, list[MemoryRef]]:
        if not nodes:
            return {}
        nids = [node.node_id for node in nodes if node.scope == self._scope]
        if not nids:
            return {node: [] for node in nodes}
        rows = _run(
            self._driver,
            self._database,
            f"UNWIND $nids AS nid "
            f"MATCH (m:{self._memory_label} {{tenant_id: $tenant_id, repo_id: $repo_id}})-[:"
            f"{_TOUCHES}]->(s:{_NODE} {{tenant_id: $tenant_id, repo_id: $repo_id, node_id: nid}}) "
            "RETURN nid, m.memory_id AS memory_id ORDER BY memory_id",
            nids=nids,
            tenant_id=str(self._scope.tenant_id),
            repo_id=str(self._scope.repo_id),
        )
        result: dict[StructuralRef, list[MemoryRef]] = {node: [] for node in nodes}
        for row in rows:
            sref = StructuralRef(self._scope, str(row["nid"]))
            mref = MemoryRef(self._scope, MemoryId(str(row["memory_id"])))
            if sref in result:
                result[sref].append(mref)
        return result

    def close(self) -> None:
        self._driver.close()
