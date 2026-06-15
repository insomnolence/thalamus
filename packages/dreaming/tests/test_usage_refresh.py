"""Tests for UsageRefreshPass — recompute usage weights and swap them through the refresh seam."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

from thalamus.core.types import MemoryId, RepoId, Scope, TenantId
from thalamus.dreaming import PassContext, PassKind, UsageRefreshPass

SCOPE = Scope(TenantId("t"), RepoId("r"))
NOW = datetime(2026, 6, 15, tzinfo=UTC)


def _ctx() -> PassContext:
    return PassContext(scope=SCOPE, now=NOW)


def test_recomputes_then_swaps_through_the_refresh_seam() -> None:
    swapped: dict[str, Mapping[MemoryId, float]] = {}
    pass_ = UsageRefreshPass(
        recompute=lambda: {MemoryId("retained:a"): 3.0, MemoryId("retained:b"): 1.0},
        refresh=lambda weights: swapped.__setitem__("w", weights),
    )
    outcome = pass_.run(_ctx())
    assert swapped["w"] == {MemoryId("retained:a"): 3.0, MemoryId("retained:b"): 1.0}
    assert outcome.details["weighted"] == 2
    assert pass_.kind is PassKind.ACTOR  # deterministic over logs → may act


def test_empty_usage_swaps_an_empty_mapping() -> None:
    seen: dict[str, Mapping[MemoryId, float]] = {}
    UsageRefreshPass(recompute=dict, refresh=lambda w: seen.__setitem__("w", w)).run(_ctx())
    assert seen["w"] == {}
