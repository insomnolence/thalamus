"""Usage stability — is "used vs. ignored" a *stable per-memory property*, or noise?

A companion to :func:`thalamus.eval.utility.utility_at_k`. Where ``utility@k`` answers
"of the memories surfaced, what fraction proved used?" (a per-*event* proxy, the trainable
target), this answers a per-*memory* question the 2026-05-31 design decision pre-commits to:
**per-memory usefulness is read from the usage signal itself — used vs. surfaced-but-ignored —
no failure/outcome class required.** It is computed offline from the same two logs.

The metric is built so a *present* signal can't masquerade as a *useful* one (a signal is only
useful if it discriminates and is stable, not merely non-empty):

- **Separation (bimodality).** Among memories surfaced at least ``min_surfaced`` times, what
  fraction land cleanly at a usage rate of 0.0 (reliably ignored) or 1.0 (reliably used) rather
  than scattered around 0.5? High separation means usage two-classes memories as a property of
  the memory, not per-instance noise — and it makes "surfaced-but-ignored" a *stable* negative,
  not a coin-flip.
- **Cross-session reuse concentration.** How many memories are *used* across ≥2 distinct
  sessions, and what's the top count? This is the most volume-robust evidence of a reliably-
  useful core, since it doesn't depend on a memory being re-surfaced within any one time window.

Honest-measurement choices (mirroring ``utility_at_k``):
- ``min_surfaced`` excludes one-shot memories, whose 0/1 rate would be spurious; ``n_eligible``
  is exposed so the volume backing the number stays visible (§13.13).
- "Surfaced" is counted over the **outcome-captured** join (a memory's distinct ``event_id``s
  that carry *any* usage signal), the same universe ``utility@k`` scores — an event with no
  captured outcome is missing data, not an ignore.
- This validates **stable usefulness, not correctness**: a reliably-used memory could still be
  subtly wrong — catching that is the deferred reward-hacking guardrail (proxy↔truth), not this.
  Temporal persistence (does early usage predict late usage?) is a deeper probe deferred until
  usage volume supports more than a handful of re-surfaced memories.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from statistics import fmean

from thalamus.core.types import EventId, MemoryId, SessionId
from thalamus.instrumentation import RetrievalEvent, UsageSignal


@dataclass(frozen=True, slots=True)
class UsageStabilityReport:
    min_surfaced: int  # eligibility threshold: memories surfaced at least this many times
    n_eligible: int  # memories meeting the threshold (the set the rates describe)
    n_reliable: int  # eligible memories with usage rate == 1.0 (reliably used)
    n_ignored: int  # eligible memories with usage rate == 0.0 (the stable negative)
    n_mixed: int  # eligible memories with 0 < rate < 1 (ambiguous)
    mean_rate: float  # mean per-memory usage rate over the eligible set
    separation: float  # (n_reliable + n_ignored) / n_eligible — how cleanly usage two-classes
    n_reused: int  # memories *used* across >= 2 distinct sessions (reliably-useful core)
    max_reuse: int  # top distinct-session reuse count


def usage_stability(
    events: Iterable[RetrievalEvent],
    signals: Iterable[UsageSignal],
    *,
    min_surfaced: int = 2,
) -> UsageStabilityReport:
    """Per-memory usage stability over the retrieval-event and Tier-1 usage logs.

    A memory's *surfaced* instances are the distinct ``event_id``s for which it carries any
    usage signal; a surfaced instance counts as *used* if any signal for that
    ``(event_id, memory_id)`` has ``used=True`` (matching :func:`utility_at_k`'s "any signal"
    rule, so the two metrics agree on what "used" means). Cross-session reuse counts the
    distinct sessions (via the event→session map) in which a memory was used.
    """
    surfaced: dict[MemoryId, set[EventId]] = defaultdict(set)
    used: dict[MemoryId, set[EventId]] = defaultdict(set)
    for signal in signals:
        surfaced[signal.memory_id].add(signal.event_id)
        if signal.used:
            used[signal.memory_id].add(signal.event_id)

    session_of: dict[EventId, SessionId] = {
        event.event_id: event.session_id for event in events if event.session_id is not None
    }

    rates = {
        memory_id: len(used[memory_id]) / len(seen)
        for memory_id, seen in surfaced.items()
        if len(seen) >= min_surfaced
    }
    n_reliable = sum(1 for rate in rates.values() if rate == 1.0)
    n_ignored = sum(1 for rate in rates.values() if rate == 0.0)

    reuse = {
        memory_id: len({session_of[event_id] for event_id in seen if event_id in session_of})
        for memory_id, seen in used.items()
    }

    return UsageStabilityReport(
        min_surfaced=min_surfaced,
        n_eligible=len(rates),
        n_reliable=n_reliable,
        n_ignored=n_ignored,
        n_mixed=len(rates) - n_reliable - n_ignored,
        mean_rate=fmean(rates.values()) if rates else 0.0,
        separation=(n_reliable + n_ignored) / len(rates) if rates else 0.0,
        n_reused=sum(1 for count in reuse.values() if count >= 2),
        max_reuse=max(reuse.values(), default=0),
    )
