"""Shared read of curated beliefs and their code footprints.

Both deterministic belief passes (link-resolution staleness, belief audit) work
over the same set: curated memories (decisions/constraints/gotchas/…) and the
footprint files each claims. Episodes are immutable history and are *not* audited
for staleness (§13.18), so they are excluded here. Depends on ``core`` only.
"""

from __future__ import annotations

from collections.abc import Iterable

from thalamus.core import MemoryRecord, MemoryRef


def curated_footprints(
    records: Iterable[MemoryRecord],
) -> list[tuple[MemoryRef, tuple[str, ...]]]:
    """Each curated memory paired with its footprint files (possibly empty).

    Takes the cycle's already-read records rather than the store, so the two belief passes
    share :meth:`PassContext.memories` instead of each re-enumerating Brain 1.
    """
    out: list[tuple[MemoryRef, tuple[str, ...]]] = []
    for record in records:
        if record.metadata.get("source") == "curated":
            out.append((record.ref, tuple(record.metadata.get("footprint", ()))))
    return out
