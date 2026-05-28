"""Neo4j-backed belief-supersession edges — §13.18 R1 in the shared graph substrate.

:class:`InMemorySupersessionIndex` is the v0 baseline; this persists the SUPERSEDES edge
as a native relationship ``(M_experiential)-[:SUPERSEDES {reason, at}]->(M_experiential)``
in the *same* database as Brain 1, so a fresh ``serve`` cold-loads the current-truth view
from durable state. Mirrors ``Neo4jCrossLinkIndex`` one hemisphere over (memory→memory
instead of memory→node); the driver is injected (``thalamus.store.connect``) so the index
shares one connection + database with the store and structural graph.

``neo4j`` is needed only for typing; all calls go through the injected driver.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Any

from thalamus.core.exceptions import StoreError, ThalamusError
from thalamus.core.types import MemoryId, MemoryRef, Scope, Supersession

if TYPE_CHECKING:
    from neo4j import Driver

_LABEL = "M_experiential"  # the label Neo4jStore writes for experiential memories
_SUPERSEDES = "SUPERSEDES"  # native belief-revision edge: superseded -> successor


def _run(driver: Driver, database: str, cypher: str, **params: Any) -> list[Any]:
    try:
        with driver.session(database=database) as session:
            return list(session.run(cypher, **params))
    except Exception as exc:
        raise StoreError(f"Neo4j supersession operation failed: {exc}") from exc


class Neo4jSupersessionIndex:
    """``SupersessionIndex`` as native ``SUPERSEDES`` edges between experiential memories.

    ``supersede`` only connects memories that already exist (it never fabricates a node) and
    keeps a single outgoing edge per superseded memory (re-superseding re-points it), matching
    the in-memory last-write-wins. The old memory's record is never modified or deleted.
    """

    def __init__(
        self,
        driver: Driver,
        scope: Scope,
        *,
        database: str = "neo4j",
        memory_label: str = _LABEL,
    ) -> None:
        self._driver = driver
        self._scope = scope
        self._database = database
        self._label = memory_label

    def supersede(self, *, old: MemoryRef, new: MemoryRef, reason: str, at: datetime) -> None:
        if old.scope != self._scope or new.scope != self._scope:
            raise ThalamusError("supersession endpoints must match index scope")
        if old == new:
            raise ThalamusError("a memory cannot supersede itself")
        _run(
            self._driver,
            self._database,
            f"MATCH (o:{self._label} {{tenant_id: $t, repo_id: $r, memory_id: $old}}), "
            f"(n:{self._label} {{tenant_id: $t, repo_id: $r, memory_id: $new}}) "
            "WITH o, n "
            f"OPTIONAL MATCH (o)-[prev:{_SUPERSEDES}]->() DELETE prev "
            f"MERGE (o)-[e:{_SUPERSEDES}]->(n) SET e.reason = $reason, e.at = $at",
            old=str(old.memory_id),
            new=str(new.memory_id),
            reason=reason,
            at=at.isoformat(),
            t=str(self._scope.tenant_id),
            r=str(self._scope.repo_id),
        )

    def superseded(self, scope: Scope) -> Mapping[MemoryRef, Supersession]:
        if scope != self._scope:
            return {}
        rows = _run(
            self._driver,
            self._database,
            f"MATCH (o:{self._label} {{tenant_id: $t, repo_id: $r}})"
            f"-[e:{_SUPERSEDES}]->(n:{self._label}) "
            "RETURN o.memory_id AS old, n.memory_id AS new, e.reason AS reason, e.at AS at",
            t=str(self._scope.tenant_id),
            r=str(self._scope.repo_id),
        )
        result: dict[MemoryRef, Supersession] = {}
        for row in rows:
            old_ref = MemoryRef(self._scope, MemoryId(str(row["old"])))
            result[old_ref] = Supersession(
                superseded_by=MemoryId(str(row["new"])),
                reason=str(row["reason"]),
                at=datetime.fromisoformat(str(row["at"])),
            )
        return result

    def close(self) -> None:
        self._driver.close()
