"""Tests for BehavioralConsolidationPass — run the injected consolidation seam, report the count."""

from __future__ import annotations

from datetime import UTC, datetime

from thalamus.core.types import RepoId, Scope, TenantId
from thalamus.dreaming import BehavioralConsolidationPass, PassContext, PassKind

SCOPE = Scope(TenantId("t"), RepoId("r"))
NOW = datetime(2026, 6, 17, tzinfo=UTC)


def _ctx() -> PassContext:
    return PassContext(scope=SCOPE, now=NOW)


def test_runs_the_consolidation_seam_and_reports_the_count() -> None:
    calls = {"n": 0}

    def consolidate() -> int:
        calls["n"] += 1
        return 3

    pass_ = BehavioralConsolidationPass(consolidate)
    outcome = pass_.run(_ctx())
    assert calls["n"] == 1
    assert outcome.details["memories"] == 3
    assert pass_.kind is PassKind.ACTOR  # deterministic over logs → may act (§14.3)


def test_zero_consolidated_is_reported_cleanly() -> None:
    outcome = BehavioralConsolidationPass(lambda: 0).run(_ctx())
    assert outcome.details["memories"] == 0
