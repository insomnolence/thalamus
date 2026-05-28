"""Shared read of curated beliefs and their code footprints.

Both deterministic belief passes (link-resolution staleness, belief audit) work
over the same set: curated memories (decisions/constraints/gotchas/…) and the
footprint files each claims. Episodes are immutable history and are *not* audited
for staleness (§13.18), so they are excluded here. Depends on ``core`` only.
"""

from __future__ import annotations

from thalamus.core import MemoryRef, Scope, Store


def curated_footprints(store: Store, scope: Scope) -> list[tuple[MemoryRef, tuple[str, ...]]]:
    """Each curated memory in ``scope`` paired with its footprint files (possibly empty)."""
    out: list[tuple[MemoryRef, tuple[str, ...]]] = []
    for record in store.scan(scope):
        if record.metadata.get("source") == "curated":
            out.append((record.ref, tuple(record.metadata.get("footprint", ()))))
    return out
