"""Tests for CoChangeRefreshPass — recompute the file co-change index and swap it via the seam."""

from __future__ import annotations

from datetime import UTC, datetime

from thalamus.core.types import RepoId, Scope, StructuralRef, TenantId
from thalamus.dreaming import CoChangeRefreshPass, PassContext, PassKind
from thalamus.structural import CoChangeIndex, InMemoryCoChangeIndex

SCOPE = Scope(TenantId("t"), RepoId("r"))
NOW = datetime(2026, 6, 16, tzinfo=UTC)


def _ctx() -> PassContext:
    return PassContext(scope=SCOPE, now=NOW)


def _ref(nid: str) -> StructuralRef:
    return StructuralRef(scope=SCOPE, node_id=nid)


def test_recomputes_then_swaps_through_the_refresh_seam() -> None:
    fresh = InMemoryCoChangeIndex()
    fresh.add_commit([_ref("a"), _ref("b")])
    swapped: dict[str, CoChangeIndex] = {}

    pass_ = CoChangeRefreshPass(
        recompute=lambda: fresh,
        refresh=lambda index: swapped.__setitem__("index", index),
    )
    outcome = pass_.run(_ctx())

    assert swapped["index"] is fresh  # the freshly-built index was swapped in
    assert outcome.details["files"] == len(fresh)  # Sized → reported
    assert pass_.kind is PassKind.ACTOR  # deterministic over git → may act (firewall §14.3)


def test_an_index_without_a_len_reports_zero_not_a_crash() -> None:
    class _Bare:
        def cochanged(self, ref: StructuralRef) -> list[tuple[StructuralRef, int]]:
            return []

    seen: dict[str, CoChangeIndex] = {}
    outcome = CoChangeRefreshPass(_Bare, lambda i: seen.__setitem__("i", i)).run(_ctx())
    assert isinstance(seen["i"], _Bare)
    assert outcome.details["files"] == 0  # not Sized → 0, no crash
