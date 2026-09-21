"""The gating instrument — does a cycle actually converge, and does a gate change its result?

§14 says a novel layer must be *removable and measured against the boring baseline*. For pass
gating (skip a pass whose inputs have not changed) the baseline is the ungated cycle, and the
measurement that matters is **not** "is it faster" — it is "does the derived state come out the
same." A gate that misses a dependency serves stale views silently, which is strictly worse than
being slow, so the speed number is meaningless without this check beside it.

**Why convergence, not a two-run A/B.** Comparing a gated run against an ungated run from the same
starting state would need to *reset* that state between runs — impossible against a durable Neo4j
brain. The equivalent invariant needs no reset:

    running the cycle again must change nothing.

If a pass is convergent, a gate that skips it is sound by construction (the skipped work would
have produced what is already there). If a pass is *not* convergent, no gate over it can be
trusted — the difference between "skipped" and "ran" is then observable, and gating it would
change what recall serves. So this harness is a **precondition for gating**, useful before a
single gate exists: it says which passes are safe to gate at all.

Once gates do exist the same harness validates them, by running a gated cycle and then a forced
one: any view that moves names the pass whose change-token is incomplete.

**Probes, not introspection.** Derived state lives in many places (ref holders, the cross-link
index, the graph, log-derived views), and ``dreaming`` deliberately does not import the retrieval
rungs or the gateway. So the caller supplies named probes — thunks returning whatever represents
that view — and the harness digests them. What a probe returns is the caller's contract; the
harness only requires that equal state digests equally, which ``_canonical`` guarantees for the
ordinary shapes (mappings, sequences, sets, dataclasses, refs, numbers).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping, Sequence, Set
from dataclasses import dataclass, fields, is_dataclass

from thalamus.dreaming.base import CycleReport, PassContext
from thalamus.dreaming.scheduler import Scheduler

#: A thunk returning the current value of one derived view. Called between cycles, so it must
#: read live state rather than close over a value captured at construction.
StateProbe = Callable[[], object]


def _canonical(value: object) -> object:
    """A deterministic, JSON-able rendering of ``value`` for digesting.

    Mappings sort by key and sets by rendered element, so iteration order never shows up as a
    false difference; dataclasses render field-wise; everything else falls back to ``repr``,
    which is stable for the refs and ids these views hold. Floats keep full precision — a
    centrality or usage weight that shifts in the last place is a real difference, not noise.
    """
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    if isinstance(value, Mapping):
        items = sorted(
            ((repr(_canonical(k)), _canonical(v)) for k, v in value.items()), key=lambda kv: kv[0]
        )
        return {"__map__": items}
    if isinstance(value, Set):
        return {"__set__": sorted(repr(_canonical(v)) for v in value)}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return [_canonical(v) for v in value]
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "__dc__": type(value).__name__,
            "fields": [[f.name, _canonical(getattr(value, f.name))] for f in fields(value)],
        }
    if isinstance(value, Iterable) and not isinstance(value, str | bytes):
        return [_canonical(v) for v in value]  # generators/iterators: order is the producer's
    return repr(value)


def digest(value: object) -> str:
    """A stable short digest of one derived view's value."""
    blob = json.dumps(_canonical(value), sort_keys=True, separators=(",", ":"), default=repr)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class DerivedState:
    """A digest per named derived view — what the passes collectively produced."""

    digests: Mapping[str, str]

    def differences(self, other: DerivedState) -> tuple[str, ...]:
        """Names whose digest differs (a name missing on either side counts as a difference)."""
        names = set(self.digests) | set(other.digests)
        return tuple(
            sorted(n for n in names if self.digests.get(n) != other.digests.get(n))
        )


def snapshot(probes: Mapping[str, StateProbe]) -> DerivedState:
    """Read every probe now and digest it.

    A probe that raises is recorded as an ``error:`` digest rather than aborting the snapshot —
    a view that cannot be read is itself a difference worth surfacing, and one broken probe must
    not hide the others.
    """
    digests: dict[str, str] = {}
    for name, probe in probes.items():
        try:
            digests[name] = digest(probe())
        except Exception as exc:
            digests[name] = f"error:{type(exc).__name__}"
    return DerivedState(digests)


@dataclass(frozen=True, slots=True)
class ConvergenceReport:
    """Whether repeating the cycle left the derived state alone.

    ``drifted`` names the views that moved on a repeat run. Non-empty means those passes are not
    convergent, so **they cannot be safely gated** — and it may also mean a real bug, since a
    settled brain recomputing a different answer from unchanged inputs is rarely intended.
    """

    rounds: int
    states: tuple[DerivedState, ...]
    drifted: tuple[str, ...]
    reports: tuple[CycleReport, ...]

    @property
    def converged(self) -> bool:
        return not self.drifted

    def render(self) -> str:
        head = (
            f"convergence over {self.rounds} cycle(s): "
            + ("CONVERGED — every derived view identical" if self.converged
               else f"DRIFT in {len(self.drifted)} view(s)")
        )
        lines = [head]
        for name in sorted(set().union(*(set(s.digests) for s in self.states)) if self.states
                           else set()):
            seen = [s.digests.get(name, "-") for s in self.states]
            mark = "DRIFT" if name in self.drifted else "ok   "
            lines.append(f"  [{mark}] {name}: {' -> '.join(seen)}")
        return "\n".join(lines)


def check_convergence(
    scheduler: Scheduler,
    context_factory: Callable[[], PassContext],
    probes: Mapping[str, StateProbe],
    *,
    rounds: int = 2,
) -> ConvergenceReport:
    """Run the cycle ``rounds`` times over unchanging inputs and report any view that moved.

    The first cycle settles the brain (a cold or stale view legitimately changes then); every
    cycle after it must be a no-op. Drift is measured from the *first* post-cycle snapshot, so a
    view that is merely slow to settle is still reported rather than excused.
    """
    if rounds < 2:
        raise ValueError("convergence needs at least 2 rounds to compare")
    states: list[DerivedState] = []
    reports: list[CycleReport] = []
    for _ in range(rounds):
        reports.append(scheduler.run(context_factory()))
        states.append(snapshot(probes))
    baseline = states[0]
    drifted = sorted({name for later in states[1:] for name in baseline.differences(later)})
    return ConvergenceReport(
        rounds=rounds,
        states=tuple(states),
        drifted=tuple(drifted),
        reports=tuple(reports),
    )
