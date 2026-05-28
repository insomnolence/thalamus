"""Dreaming framework contracts: the pass protocol, its read-only context, and
the per-pass / per-cycle report types.

Dreaming (deep-dives/dreaming.md) is *an offline orchestrator running a DAG of
independent, individually-gated, individually-removable passes over the
immutable raw log*. This module is the seam every pass plugs into; it depends on
``core`` only, so the framework is testable and removable in isolation — the
passes that need the hemispheres (structural graph, gateway refresh) carry those
collaborators as their own constructor arguments, never on this contract.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from thalamus.core import Scope, Store, SupersessionIndex


class PassKind(StrEnum):
    """The §14.3 firewall, encoded as a type field.

    ``ACTOR`` passes are deterministic and may *act* — write derived views the
    gateway then serves. ``PROPOSER`` passes (LLM/learned) may only *propose* —
    their output is surfaced as a suggestion and earns credibility solely from
    external outcomes, never self-validation. The scheduler does not enforce a
    behavioural difference (a pass body is opaque); the kind makes the firewall
    auditable and lets callers gate proposer output differently from actor work.
    """

    ACTOR = "actor"
    PROPOSER = "proposer"


class PassStatus(StrEnum):
    """Terminal status the scheduler stamps on each pass run."""

    OK = "ok"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class PassContext:
    """Read-only handles handed to every pass for one cycle.

    Deliberately read-only and per-cycle: a pass reads raw (+ current derived
    state) and *returns* a :class:`PassOutcome`; it never mutates shared gateway
    state inline. Any resulting refresh is applied atomically by the caller
    (Commit 2+). Only the universal handles live here — ``scope``/``now`` plus
    the durable Brain-1 source of truth (``store``/``supersession``). They are
    optional so the framework runs with whatever the composition root wired; a
    pass needing an absent handle reports ``SKIPPED``. Pass-specific
    collaborators (structural graph, cross-link index, the gateway refresh hook,
    the repo root) are injected into the individual pass, keeping this contract
    on ``core`` only.
    """

    scope: Scope
    now: datetime
    store: Store | None = None
    supersession: SupersessionIndex | None = None
    repo_root: str | None = None


@dataclass(frozen=True, slots=True)
class PassOutcome:
    """What a pass reports on success.

    The scheduler stamps name/kind/timing/status around it; a raised exception
    becomes a ``FAILED`` report instead, so a pass signals failure by raising and
    signals "nothing to do" by returning an :meth:`empty` outcome (or
    ``status=SKIPPED`` via :meth:`skipped`). ``details`` must be JSON-serialisable
    (the producing pass owns that), mirroring the trajectory-event payload rule.
    """

    status: PassStatus = PassStatus.OK
    summary: str = ""
    details: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def skipped(cls, summary: str) -> PassOutcome:
        """A pass that had no handle / nothing applicable to do this cycle."""
        return cls(status=PassStatus.SKIPPED, summary=summary)


@runtime_checkable
class DreamingPass(Protocol):
    """One offline pass. Reads via :class:`PassContext`, returns a
    :class:`PassOutcome`. Implementations must be safe to re-run from raw
    (dreaming.md "safe to get wrong") — they write only regenerable derived
    views, never destructive truth."""

    @property
    def name(self) -> str:
        """Stable identifier for logs and gating."""
        ...

    @property
    def kind(self) -> PassKind:
        """The firewall classification (actor vs proposer)."""
        ...

    def run(self, ctx: PassContext) -> PassOutcome:
        """Execute the pass for one cycle."""
        ...


@dataclass(frozen=True, slots=True)
class PassReport:
    """The scheduler's record of one pass run within a cycle."""

    name: str
    kind: PassKind
    status: PassStatus
    summary: str
    details: Mapping[str, Any]
    error: str | None
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class CycleReport:
    """The outcome of one full scheduler cycle (one report per pass, in run order)."""

    started_at: datetime
    passes: tuple[PassReport, ...]

    @property
    def ok(self) -> bool:
        """True iff no pass failed (skips and successes both count as fine)."""
        return all(p.status is not PassStatus.FAILED for p in self.passes)
