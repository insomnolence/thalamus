"""``utility@k`` — the L1 proxy computed on **real** signals (OLR §13.12, §13.20).

Unlike :mod:`thalamus.eval.metrics` (recall@k/precision@k against *synthetic*
benchmark labels — the frozen regression guard), ``utility@k`` is computed
**offline from the logs**: the retrieval-event log says what was shown at what
rank; the Tier-1 usage log says which surfaced memories earned a deterministic
``used`` signal. Joined by ``event_id``, the answer to "of the memories we put in
front of the actuator, what fraction proved useful?" — the primary *trainable*
metric the design pre-commits to (§13.12). Still a directional proxy: the truth
(L2 task outcomes) and the verdict (L3 brain-on/off) need an actuator.

Honest-measurement choices:
- **Macro mean over events** (each retrieval weighted equally, like recall@k in
  the harness; robust to fan-out skew). Pooled ``n_shown``/``n_used`` are exposed
  so a micro ratio is derivable.
- An event that surfaced memories but has **no usage signal is *missing data***
  (its outcome was never captured), not zero utility — it is excluded from the
  metric and tracked via ``coverage``. Conflating "we don't know" with "nothing
  was useful" would silently depress the number; ``coverage`` is the honest caveat
  on how much Tier-1 signal actually backs it (the §13.13 data-volume concern).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from statistics import fmean

from thalamus.core.types import EventId, MemoryId
from thalamus.instrumentation import RetrievalEvent, UsageSignal


@dataclass(frozen=True, slots=True)
class UtilityReport:
    k: int
    n_events: int  # scored events: surfaced >=1 in top-k AND had a captured outcome
    n_shown: int  # pooled shown-in-top-k over scored events
    n_used: int  # of those, how many earned a Tier-1 ``used`` signal
    utility_at_k: float  # macro mean of per-event utility (the headline metric)
    coverage: float  # scored events / surfacing events: how much Tier-1 signal exists


def utility_at_k(
    events: Iterable[RetrievalEvent],
    signals: Iterable[UsageSignal],
    k: int,
) -> UtilityReport:
    """Join the retrieval-event log and the Tier-1 usage log by ``event_id`` and
    compute ``utility@k`` — the fraction of top-k shown memories that earned a
    ``used`` signal, averaged over events for which an outcome was captured.

    A memory counts as used if *any* Tier-1 signal for its ``(event_id, memory_id)``
    has ``used=True`` (forward-compatible with future overlap/citation/constraint
    signal kinds). Events with no captured outcome are excluded (see module docs).
    """
    used_pairs: set[tuple[EventId, MemoryId]] = set()
    events_with_outcome: set[EventId] = set()
    for signal in signals:
        events_with_outcome.add(signal.event_id)
        if signal.used:
            used_pairs.add((signal.event_id, signal.memory_id))

    per_event_utility: list[float] = []
    n_shown = 0
    n_used = 0
    n_surfacing = 0
    for event in events:
        top_k = sorted(event.shown, key=lambda item: item.rank)[:k]
        if not top_k:
            continue
        n_surfacing += 1
        if event.event_id not in events_with_outcome:
            continue  # outcome not captured — missing data, not zero utility
        used = sum(1 for item in top_k if (event.event_id, item.memory_id) in used_pairs)
        n_shown += len(top_k)
        n_used += used
        per_event_utility.append(used / len(top_k))

    return UtilityReport(
        k=k,
        n_events=len(per_event_utility),
        n_shown=n_shown,
        n_used=n_used,
        utility_at_k=fmean(per_event_utility) if per_event_utility else 0.0,
        coverage=len(per_event_utility) / n_surfacing if n_surfacing else 0.0,
    )
