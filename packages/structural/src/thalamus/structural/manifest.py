"""File manifest — what was ingested last build, for content-hashed incremental ingestion.

Maps each source file (by path) to the content hash it had when last ingested + the structural
node ids derived from it. A build hashes the current files, diffs against the manifest, and so
re-embeds only *changed/new* files' nodes and drops *vanished* files' nodes — O(changes), not
O(repo). The manifest is a **derived view** (§14.1): rebuilt from source on ``--rebuild``, never
a source of truth. Persisted in Neo4j alongside the graph (one substrate — no sidecar file to
drift out of sync with the persisted nodes, the lesson of the test-isolation incident).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from thalamus.core.types import Scope
from thalamus.structural.neo4j_graph import _run

if TYPE_CHECKING:
    from neo4j import Driver

_MANIFEST = "FileManifest"


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    """A file's last-ingested content hash and the node ids derived from it."""

    sha256: str
    node_ids: tuple[str, ...]


@runtime_checkable
class FileManifest(Protocol):
    """Persists, per scope, the path -> (content hash, node ids) of the last build."""

    def load(self, scope: Scope) -> dict[str, ManifestEntry]:
        """Return the last-saved manifest for ``scope`` (empty if none)."""
        ...

    def save(self, scope: Scope, entries: Mapping[str, ManifestEntry]) -> None:
        """Replace the manifest for ``scope`` with ``entries`` (a derived snapshot)."""
        ...


class InMemoryFileManifest:
    """In-process manifest. For tests and the non-persistent (always-full-rebuild) path."""

    def __init__(self) -> None:
        self._by_scope: dict[Scope, dict[str, ManifestEntry]] = {}

    def load(self, scope: Scope) -> dict[str, ManifestEntry]:
        return dict(self._by_scope.get(scope, {}))

    def save(self, scope: Scope, entries: Mapping[str, ManifestEntry]) -> None:
        self._by_scope[scope] = dict(entries)


class Neo4jFileManifest:
    """``FileManifest`` persisted as ``FileManifest`` nodes in the shared graph substrate."""

    def __init__(self, driver: Driver, scope: Scope, *, database: str = "neo4j") -> None:
        self._driver = driver
        self._scope = scope
        self._database = database

    def load(self, scope: Scope) -> dict[str, ManifestEntry]:
        if scope != self._scope:
            return {}
        rows = _run(
            self._driver,
            self._database,
            f"MATCH (m:{_MANIFEST} {{tenant_id: $tenant_id, repo_id: $repo_id}}) "
            "RETURN m.path AS path, m.sha256 AS sha256, m.node_ids AS node_ids",
            tenant_id=str(scope.tenant_id),
            repo_id=str(scope.repo_id),
        )
        return {
            str(row["path"]): ManifestEntry(
                sha256=str(row["sha256"]), node_ids=tuple(str(n) for n in row["node_ids"])
            )
            for row in rows
        }

    def save(self, scope: Scope, entries: Mapping[str, ManifestEntry]) -> None:
        if scope != self._scope:
            raise ValueError("manifest scope mismatch")
        # Replace the scope's manifest wholesale — it is a derived snapshot of the last build.
        _run(
            self._driver,
            self._database,
            f"MATCH (m:{_MANIFEST} {{tenant_id: $tenant_id, repo_id: $repo_id}}) DELETE m",
            tenant_id=str(scope.tenant_id),
            repo_id=str(scope.repo_id),
        )
        rows = [
            {"path": path, "sha256": entry.sha256, "node_ids": list(entry.node_ids)}
            for path, entry in entries.items()
        ]
        if rows:
            _run(
                self._driver,
                self._database,
                f"UNWIND $rows AS r CREATE (m:{_MANIFEST} "
                "{tenant_id: $tenant_id, repo_id: $repo_id, "
                "path: r.path, sha256: r.sha256, node_ids: r.node_ids})",
                rows=rows,
                tenant_id=str(scope.tenant_id),
                repo_id=str(scope.repo_id),
            )
