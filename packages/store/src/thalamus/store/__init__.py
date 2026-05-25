"""thalamus.store — shared storage substrate.

In-memory baseline store + Neo4j-backed store (one graph, per-hemisphere vector
indexes), both behind the ``core.Store`` protocol. Neo4j needs the ``neo4j`` extra;
importing this package does not require it (the driver is lazy-loaded).
"""

from thalamus.store.in_memory import InMemoryStore
from thalamus.store.neo4j_store import Neo4jStore, connect

__all__ = ["InMemoryStore", "Neo4jStore", "connect"]
