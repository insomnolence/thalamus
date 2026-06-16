"""Tests for InMemoryCoChangeIndex — symmetric pairwise co-change counts from commits."""

from __future__ import annotations

from thalamus.core.types import RepoId, Scope, StructuralRef, TenantId
from thalamus.structural import CoChangeRef, FileCoChangeIndex, InMemoryCoChangeIndex

S = Scope(TenantId("t"), RepoId("r"))


def _ref(nid: str) -> StructuralRef:
    return StructuralRef(scope=S, node_id=nid)


def test_co_change_is_symmetric_and_counted() -> None:
    idx = InMemoryCoChangeIndex()
    idx.add_commit([_ref("a"), _ref("b")])
    idx.add_commit([_ref("a"), _ref("b")])  # again → count 2
    idx.add_commit([_ref("a"), _ref("c")])  # a–c once

    assert idx.cochanged(_ref("a")) == [(_ref("b"), 2), (_ref("c"), 1)]  # sorted desc
    assert idx.cochanged(_ref("b")) == [(_ref("a"), 2)]  # symmetric
    assert idx.cochanged(_ref("c")) == [(_ref("a"), 1)]


def test_unknown_symbol_has_no_partners() -> None:
    idx = InMemoryCoChangeIndex()
    idx.add_commit([_ref("a"), _ref("b")])
    assert idx.cochanged(_ref("z")) == []


def test_a_commit_touching_one_symbol_records_nothing() -> None:
    idx = InMemoryCoChangeIndex()
    idx.add_commit([_ref("a")])  # no pair
    assert idx.cochanged(_ref("a")) == []
    assert len(idx) == 0


def test_duplicate_symbols_in_a_commit_are_deduped() -> None:
    idx = InMemoryCoChangeIndex()
    idx.add_commit([_ref("a"), _ref("a"), _ref("b")])  # a appears twice → one a–b pair
    assert idx.cochanged(_ref("a")) == [(_ref("b"), 1)]


# --- file-level co-change: foo.py holds {foo}; bar.py holds {bar1, bar2}
FOO, BAR1, BAR2 = _ref("foo"), _ref("bar1"), _ref("bar2")
_REF_FILE = {FOO: "foo.py", BAR1: "bar.py", BAR2: "bar.py"}
_FILE_REFS = {"foo.py": [FOO], "bar.py": [BAR1, BAR2]}


def test_file_cochange_expands_to_symbols_in_co_changed_files() -> None:
    idx = FileCoChangeIndex(_REF_FILE, _FILE_REFS, min_cooccur=1, max_file_frequency=1.0)
    idx.add_commit(["foo.py", "bar.py"])  # the two files changed together

    # foo co-changes with bar.py → both symbols that live in bar.py surface (scored by lift)
    assert {r for r, _ in idx.cochanged(FOO)} == {BAR1, BAR2}
    assert idx.cochanged(BAR1)[0][0] == FOO  # symmetric


def test_file_cochange_min_cooccur_drops_one_offs() -> None:
    idx = FileCoChangeIndex(_REF_FILE, _FILE_REFS, min_cooccur=2, max_file_frequency=1.0)
    idx.add_commit(["foo.py", "bar.py"])  # co-changed only once < min_cooccur 2
    assert idx.cochanged(FOO) == []


def test_file_cochange_lift_excludes_hub_files() -> None:
    """A sweep/hub file that changes in (almost) every commit is filtered out as a partner."""
    a, b, hub = _ref("A"), _ref("B"), _ref("H")
    ref_file = {a: "a.ts", b: "b.ts", hub: "hub.ts"}
    file_refs = {"a.ts": [a], "b.ts": [b], "hub.ts": [hub]}
    idx = FileCoChangeIndex(ref_file, file_refs, min_cooccur=2, max_file_frequency=0.9)
    idx.add_commit(["a.ts", "b.ts", "hub.ts"])  # a–b real coupling
    idx.add_commit(["a.ts", "b.ts", "hub.ts"])
    idx.add_commit(["hub.ts", "x.ts"])  # hub changes everywhere (3/3 commits → a sweep file)

    partners = dict(idx.cochanged(a))
    assert b in partners  # tightly-coupled, low base-rate partner survives
    assert hub not in partners  # hub excluded (base rate above the frequency cap)


def test_file_cochange_is_empty_without_a_co_changed_file() -> None:
    idx = FileCoChangeIndex(_REF_FILE, _FILE_REFS)
    idx.add_commit(["foo.py"])  # single file → no file pair
    assert idx.cochanged(FOO) == []


def test_file_cochange_ignores_a_symbol_with_no_known_file() -> None:
    idx = FileCoChangeIndex(_REF_FILE, _FILE_REFS, min_cooccur=1)
    idx.add_commit(["foo.py", "bar.py"])
    assert idx.cochanged(_ref("orphan")) == []


def test_cochange_ref_is_empty_until_refreshed_then_delegates() -> None:
    ref = CoChangeRef()
    a, b = _ref("a"), _ref("b")
    assert ref.cochanged(a) == []  # empty holder is a safe no-op

    inner = InMemoryCoChangeIndex()
    inner.add_commit([a, b])
    ref.refresh(inner)
    assert ref.cochanged(a) == [(b, 1)]  # now delegates to the swapped-in index
