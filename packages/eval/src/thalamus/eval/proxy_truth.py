"""proxy↔truth monitor — keep ``utility@k`` honest against Tier-2 outcomes (OLR §13.12).

Polynoica's actual failure was the *metric*: a proxy that read well while measuring
nothing real. The guard is to hold the cheap trainable proxy (Tier-1 ``utility@k``)
against the meaningful truth (Tier-2 task outcomes) and watch them move together. If
utility is high but does not separate *successful* sessions from *failed* ones, the
proxy is being gamed — cut the layer it gates. This is the pre-committed instrument
(CLAUDE.md #4), built before any learned ranker; it yields a real verdict only once an
actuator generates correlated recall+outcome data (the single-user volume caveat §13.13).

The monitor itself is a pure computation over ``(utility, tier2_success)`` units;
:func:`session_utility` produces the Tier-1 half (per-session ``utility@k``) so a caller
can join it to per-session Tier-2 outcomes (``experiential.classify_outcome``) by session.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from statistics import fmean

from thalamus.core.types import EventId, MemoryId, SessionId
from thalamus.instrumentation import RetrievalEvent, UsageSignal


@dataclass(frozen=True, slots=True)
class ProxyTruthReport:
    n_units: int  # units with a known Tier-2 label
    success_rate: float  # fraction Tier-2-positive
    mean_utility: float  # overall Tier-1 utility
    mean_utility_success: float  # Tier-1 utility among Tier-2-positive units
    mean_utility_failure: float  # among Tier-2-negative units
    alignment: float  # success − failure (> 0: the proxy tracks the truth)
    reward_hacking_suspected: bool  # high proxy that fails to separate good from bad


def proxy_truth(
    units: Iterable[tuple[float, bool]], *, utility_floor: float = 0.5
) -> ProxyTruthReport:
    """Correlate Tier-1 ``utility`` with Tier-2 ``success`` over units (e.g. sessions).

    ``reward_hacking_suspected`` fires when the proxy looks good (mean utility ≥
    ``utility_floor``) yet does not rank successes above failures (alignment ≤ 0) while
    real failures exist — the static signature of a gamed proxy (§13.12)."""
    materialized = [(float(utility), bool(success)) for utility, success in units]
    if not materialized:
        return ProxyTruthReport(0, 0.0, 0.0, 0.0, 0.0, 0.0, False)
    succeeded = [u for u, ok in materialized if ok]
    failed = [u for u, ok in materialized if not ok]
    mean_all = fmean(u for u, _ in materialized)
    mean_success = fmean(succeeded) if succeeded else 0.0
    mean_failure = fmean(failed) if failed else 0.0
    alignment = mean_success - mean_failure
    return ProxyTruthReport(
        n_units=len(materialized),
        success_rate=len(succeeded) / len(materialized),
        mean_utility=mean_all,
        mean_utility_success=mean_success,
        mean_utility_failure=mean_failure,
        alignment=alignment,
        reward_hacking_suspected=bool(failed) and mean_all >= utility_floor and alignment <= 0.0,
    )


def session_utility(
    events: Iterable[RetrievalEvent], signals: Iterable[UsageSignal], k: int
) -> dict[SessionId, float]:
    """Per-session Tier-1 ``utility@k`` (the join key for the monitor).

    For each session, the mean over its outcome-captured retrievals of the fraction of
    top-k shown memories that earned a ``used`` signal. Events without a session id or
    without captured usage are excluded (missing data, not zero — cf. ``utility_at_k``)."""
    used_pairs: set[tuple[EventId, MemoryId]] = set()
    events_with_outcome: set[EventId] = set()
    for signal in signals:
        events_with_outcome.add(signal.event_id)
        if signal.used:
            used_pairs.add((signal.event_id, signal.memory_id))

    per_session: dict[SessionId, list[float]] = {}
    for event in events:
        if event.session_id is None or event.event_id not in events_with_outcome:
            continue
        top_k = sorted(event.shown, key=lambda item: item.rank)[:k]
        if not top_k:
            continue
        used = sum(1 for item in top_k if (event.event_id, item.memory_id) in used_pairs)
        per_session.setdefault(event.session_id, []).append(used / len(top_k))
    return {session: fmean(values) for session, values in per_session.items()}
