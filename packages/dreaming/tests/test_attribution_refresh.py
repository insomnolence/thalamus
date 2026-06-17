"""Tests for AttributionRefreshPass — recompute attribution and swap it through the seam.

Mirrors test_usage_refresh.py: the pass pipes its injected recompute → apply seam; the holder swaps
atomically so a consumer's snapshot is stable across a later refresh."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from thalamus.core.types import EventId, MemoryId, RepoId, Scope, TenantId
from thalamus.dreaming import AttributionRefreshPass, PassContext, PassKind
from thalamus.instrumentation import AttributedSignalsRef, UsageSignal

SCOPE = Scope(TenantId("t"), RepoId("r"))
NOW = datetime(2026, 6, 17, tzinfo=UTC)


def _ctx() -> PassContext:
    return PassContext(scope=SCOPE, now=NOW)


def _signal(mem: str, used: bool) -> UsageSignal:
    return UsageSignal(EventId("e1"), MemoryId(mem), "footprint", 1.0 if used else 0.0, used)


def test_recomputes_then_swaps_through_the_refresh_seam() -> None:
    swapped: dict[str, Sequence[UsageSignal]] = {}
    fresh = [_signal("retained:a", True), _signal("retained:b", False)]
    pass_ = AttributionRefreshPass(
        recompute=lambda: fresh,
        apply=lambda signals: swapped.__setitem__("s", signals),
    )
    outcome = pass_.run(_ctx())
    assert list(swapped["s"]) == fresh
    assert outcome.details["signals"] == 2
    assert pass_.kind is PassKind.ACTOR  # deterministic over logs + graph → may act (§14.3)


def test_empty_attribution_applies_an_empty_sequence() -> None:
    seen: dict[str, Sequence[UsageSignal]] = {}
    AttributionRefreshPass(recompute=list, apply=lambda s: seen.__setitem__("s", s)).run(_ctx())
    assert list(seen["s"]) == []


def test_pass_into_the_ref_makes_fresh_signals_readable() -> None:
    ref = AttributedSignalsRef()
    assert ref.signals == ()  # empty until refreshed
    fresh = [_signal("retained:a", True)]
    AttributionRefreshPass(recompute=lambda: fresh, apply=ref.refresh).run(_ctx())
    assert list(ref.signals) == fresh


def test_ref_refresh_swaps_atomically() -> None:
    ref = AttributedSignalsRef()
    ref.refresh([_signal("retained:a", True)])
    snapshot = ref.signals  # a consumer snapshots once
    ref.refresh([_signal("retained:b", False), _signal("retained:c", True)])
    assert len(snapshot) == 1  # the earlier snapshot is unaffected by the later swap
    assert len(ref.signals) == 2
