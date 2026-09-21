"""RelinkQueue — files whose structural nodes were rebuilt, pending cross-link repair.

A re-derive drops a changed file's nodes and re-MERGEs them (:func:`incremental_ingest`
steps 4–5). On the Neo4j backend ``remove`` is ``DETACH DELETE``, which also deletes the
``(memory)-[:TOUCHES]->(node)`` cross-hemisphere edges pointing at those nodes; the re-MERGE
restores the node under its canonical id but **not** the edges. The in-memory backend keeps
its links in a separate index and so loses nothing — the two implementations of the same
protocol diverge here, which is why this went unnoticed.

``StructuralRefreshPass`` links each memory once and then skips it (the cache that stops a long
serve re-linking thousands of memories every tick), so without an explicit signal the memories
whose code was rebuilt stay unlinked for the life of the process — silently draining structural
recall and the L-R2 centrality rung, which scores a memory by the degree of the nodes it links to.

This queue is that signal, and deliberately a *seam between two passes* rather than a direct call:
the re-derive publishes what it rebuilt, the refresh drains and repairs. Either side may be absent
(an experiential-only brain, the in-memory shell) without the other noticing, so the repair layer
is removable like every other (§14). Repair is idempotent — ``link_by_footprint`` MERGEs — so
over-publishing costs a re-link and never corrupts.

Paths are **repo-relative POSIX**, the normalization ``module_index`` and footprints already share,
so a consumer intersects them against a memory's footprint without re-normalizing.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable


class RelinkQueue:
    """Thread-safe set of repo-relative paths awaiting cross-link repair.

    Producer and consumer run on the maintenance thread today, but a write-trigger arrives from
    an MCP request thread, so the lock is kept rather than relying on that staying true.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: set[str] = set()

    def publish(self, paths: Iterable[str]) -> None:
        """Record that ``paths``' nodes were rebuilt (a union — never drops a pending path)."""
        with self._lock:
            self._pending.update(paths)

    def drain(self) -> frozenset[str]:
        """Take and clear the pending paths. Empty when nothing was rebuilt since the last drain."""
        with self._lock:
            pending = frozenset(self._pending)
            self._pending.clear()
            return pending

    def __len__(self) -> int:
        with self._lock:
            return len(self._pending)
