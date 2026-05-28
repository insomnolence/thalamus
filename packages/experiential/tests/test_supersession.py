"""Belief-supersession index (§13.18 R1): records edges, never deletes, view is the complement."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from thalamus.core.exceptions import ThalamusError
from thalamus.core.types import MemoryId, MemoryRef, RepoId, Scope, TenantId
from thalamus.experiential import InMemorySupersessionIndex

SCOPE = Scope(TenantId("t"), RepoId("r"))
OTHER = Scope(TenantId("t"), RepoId("other"))
NOW = datetime(2026, 5, 27, tzinfo=UTC)


def _ref(mid: str, scope: Scope = SCOPE) -> MemoryRef:
    return MemoryRef(scope, MemoryId(mid))


def test_supersede_records_reason_and_successor() -> None:
    index = InMemorySupersessionIndex()
    index.supersede(old=_ref("old"), new=_ref("new"), reason="switched to Y because Z", at=NOW)

    superseded = index.superseded(SCOPE)
    assert set(superseded) == {_ref("old")}
    record = superseded[_ref("old")]
    assert record.superseded_by == MemoryId("new")
    assert record.reason == "switched to Y because Z"
    assert record.at == NOW


def test_unsuperseded_memory_is_absent_from_the_view() -> None:
    index = InMemorySupersessionIndex()
    index.supersede(old=_ref("old"), new=_ref("new"), reason="r", at=NOW)
    # "new" is current truth — the frontier is the complement, so it has no entry.
    assert _ref("new") not in index.superseded(SCOPE)


def test_view_is_scope_filtered() -> None:
    index = InMemorySupersessionIndex()
    index.supersede(old=_ref("a", OTHER), new=_ref("b", OTHER), reason="r", at=NOW)
    assert index.superseded(SCOPE) == {}
    assert set(index.superseded(OTHER)) == {_ref("a", OTHER)}


def test_re_supersession_repoints_to_the_latest() -> None:
    index = InMemorySupersessionIndex()
    index.supersede(old=_ref("old"), new=_ref("b"), reason="first", at=NOW)
    index.supersede(old=_ref("old"), new=_ref("c"), reason="second", at=NOW)
    record = index.superseded(SCOPE)[_ref("old")]
    assert record.superseded_by == MemoryId("c")
    assert record.reason == "second"


def test_self_supersession_is_rejected() -> None:
    index = InMemorySupersessionIndex()
    with pytest.raises(ThalamusError):
        index.supersede(old=_ref("x"), new=_ref("x"), reason="r", at=NOW)


def test_cross_scope_supersession_is_rejected() -> None:
    index = InMemorySupersessionIndex()
    with pytest.raises(ThalamusError):
        index.supersede(old=_ref("a", SCOPE), new=_ref("b", OTHER), reason="r", at=NOW)
