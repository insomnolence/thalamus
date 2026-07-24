"""Belief supersession (§13.18 R1) — the experiential belief layer's revision substrate.

A superseded belief is **never deleted**: its record stays immutable and a SUPERSEDES
edge marks it replaced, carrying the *reason* and timestamp (the §13.17 "used X until May,
switched to Y because Z" narrative — the crown jewel a flat delete would destroy).
"Current truth" is then a derived view (§14.1): the memories no edge points at.

v0 covers **R1** (this representation) + **D1** (explicit/announced supersession, recorded
at ``remember``-time). Automatic detection — **D2** (AST-drift audit) and **D3** (semantic) —
and credibility reweighting from accumulated outcomes are deferred to the dreaming
reconciliation pass, behind this same ``core.SupersessionIndex`` seam. Mirrors the
structural ``CrossLinkIndex`` (memory↔node) one hemisphere over.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from threading import RLock

from thalamus.core.exceptions import ThalamusError
from thalamus.core.types import MemoryRef, Scope, Supersession


class InMemorySupersessionIndex:
    """In-memory belief-supersession edges, keyed by the superseded memory.

    One edge per superseded memory (re-superseding re-points it; last write wins) — the
    current-truth view only needs "is this replaced, and by what".
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._by_old: dict[MemoryRef, Supersession] = {}

    def supersede(self, *, old: MemoryRef, new: MemoryRef, reason: str, at: datetime) -> None:
        if old.scope != new.scope:
            raise ThalamusError("supersession endpoints must share one scope")
        if old == new:
            raise ThalamusError("a memory cannot supersede itself")
        with self._lock:
            self._by_old[old] = Supersession(superseded_by=new.memory_id, reason=reason, at=at)

    def superseded(self, scope: Scope) -> Mapping[MemoryRef, Supersession]:
        with self._lock:
            return {ref: record for ref, record in self._by_old.items() if ref.scope == scope}
