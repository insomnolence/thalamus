"""Pass gating: skip while inputs are unmoved, but never silently and never permanently.

A gate is a correctness claim, so these test the safety rules as hard as the skipping: fail open
on an unreadable token, force periodically so an incomplete token self-heals, never record a
token for a run that did not succeed, and stay fully removable.
"""

from __future__ import annotations

from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

from thalamus.core import Hemisphere, MemoryId, MemoryRecord, RepoId, Scope, TenantId
from thalamus.dreaming import (
    GatedPass,
    PassContext,
    PassKind,
    PassOutcome,
    PassStatus,
    brain1_token,
    combine,
    file_digest_token,
    file_state_token,
    manifest_token,
)
from thalamus.routing import DeterministicEncoder
from thalamus.store import InMemoryStore
from thalamus.structural import InMemoryFileManifest, ManifestEntry

SCOPE = Scope(TenantId("t"), RepoId("r"))
NOW = datetime(2026, 9, 21, tzinfo=UTC)


class _Counter:
    name = "counted"
    kind = PassKind.ACTOR

    def __init__(self, status: PassStatus = PassStatus.OK) -> None:
        self.runs = 0
        self._status = status

    def run(self, ctx: PassContext) -> PassOutcome:
        self.runs += 1
        return PassOutcome(status=self._status, summary=f"run {self.runs}")


class _Raises:
    name = "boom"
    kind = PassKind.ACTOR

    def __init__(self) -> None:
        self.runs = 0

    def run(self, ctx: PassContext) -> PassOutcome:
        self.runs += 1
        raise RuntimeError("backend down")


def _ctx(store: InMemoryStore | None = None) -> PassContext:
    return PassContext(scope=SCOPE, now=NOW, store=store)


def _store(*ids: str) -> InMemoryStore:
    encoder = DeterministicEncoder(dim=32)
    store = InMemoryStore(dim=32)
    for memory_id in ids:
        record = MemoryRecord(
            MemoryId(memory_id), Hemisphere.EXPERIENTIAL, "decision", memory_id, SCOPE, NOW,
            metadata={"source": "curated"},
        )
        store.add(record, encoder.encode([record.content])[0])
    return store


def test_skips_while_the_token_is_unmoved() -> None:
    inner = _Counter()
    gated = GatedPass(inner, brain1_token, force_every=0)
    store = _store("a")

    assert gated.run(_ctx(store)).status is PassStatus.OK
    outcome = gated.run(_ctx(store))
    assert outcome.status is PassStatus.SKIPPED
    assert outcome.details["gated"] is True
    assert inner.runs == 1


def test_runs_again_once_brain1_changes() -> None:
    inner = _Counter()
    gated = GatedPass(inner, brain1_token, force_every=0)
    store = _store("a")
    gated.run(_ctx(store))
    gated.run(_ctx(store))
    assert inner.runs == 1

    encoder = DeterministicEncoder(dim=32)
    new = MemoryRecord(
        MemoryId("b"), Hemisphere.EXPERIENTIAL, "decision", "b", SCOPE, NOW,
        metadata={"source": "curated"},
    )
    store.add(new, encoder.encode([new.content])[0])
    assert gated.run(_ctx(store)).status is PassStatus.OK
    assert inner.runs == 2


def test_fails_open_when_the_token_cannot_be_computed() -> None:
    """Unknown inputs must run the pass — never skip on uncertainty."""
    inner = _Counter()
    gated = GatedPass(inner, lambda ctx: None, force_every=0)
    for _ in range(3):
        assert gated.run(_ctx()).status is PassStatus.OK
    assert inner.runs == 3


def test_forcing_makes_an_incomplete_token_self_heal() -> None:
    """The safety net: a token that never moves must not silence the pass forever."""
    inner = _Counter()
    gated = GatedPass(inner, lambda ctx: "frozen", force_every=3)
    statuses = [gated.run(_ctx()).status for _ in range(7)]
    assert statuses[0] is PassStatus.OK
    assert inner.runs > 1, "a frozen token must still be re-run periodically"
    assert statuses.count(PassStatus.OK) == 3  # runs 1, 3 and 6


def test_a_failed_run_does_not_record_its_token() -> None:
    """A pass that raised has not done its work, so it must be retried, not gated out."""
    inner = _Raises()
    gated = GatedPass(inner, brain1_token, force_every=0)
    store = _store("a")
    for _ in range(3):
        with suppress(RuntimeError):
            gated.run(_ctx(store))
    assert inner.runs == 3


def test_a_self_skipped_run_does_not_record_its_token() -> None:
    """A pass that skipped for its own reasons (a missing handle) must retry when wired."""
    inner = _Counter(status=PassStatus.SKIPPED)
    gated = GatedPass(inner, brain1_token, force_every=0)
    store = _store("a")
    gated.run(_ctx(store))
    gated.run(_ctx(store))
    assert inner.runs == 2


def test_the_gate_is_removable() -> None:
    """§14: ablating the layer must reproduce the ungated cycle exactly."""
    inner = _Counter()
    gated = GatedPass(inner, brain1_token, enabled=False)
    store = _store("a")
    for _ in range(3):
        assert gated.run(_ctx(store)).status is PassStatus.OK
    assert inner.runs == 3


def test_name_and_kind_pass_through() -> None:
    """The dream log and the DAG order key on these, so the wrapper must be transparent."""
    inner = _Counter()
    gated = GatedPass(inner, brain1_token)
    assert gated.name == "counted"
    assert gated.kind is PassKind.ACTOR
    assert gated.inner is inner


def test_file_state_token_tracks_writes_and_absence(tmp_path: Path) -> None:
    log = tmp_path / "retrieval.jsonl"
    token = file_state_token([str(log)])

    absent = token(_ctx())
    log.write_text("{}\n", encoding="utf-8")
    created = token(_ctx())
    assert absent != created

    log.write_text("{}\n{}\n", encoding="utf-8")
    assert token(_ctx()) != created


def test_combine_is_unknown_if_any_part_is_unknown() -> None:
    assert combine(lambda ctx: "a", lambda ctx: None)(_ctx()) is None
    assert combine(lambda ctx: "a", lambda ctx: "b")(_ctx()) is not None
    # Order matters: two different input sets must not collide.
    assert combine(lambda ctx: "a", lambda ctx: "b")(_ctx()) != combine(
        lambda ctx: "b", lambda ctx: "a"
    )(_ctx())


def test_digest_token_settles_for_a_file_rewritten_with_identical_content(
    tmp_path: Path,
) -> None:
    """The case that defeated the first credibility gate: a derived view is deleted and
    rewritten every cycle, so its mtime always moves even though its content never does."""
    derived = tmp_path / "usage_attributed.jsonl"
    derived.write_text('{"a":1}\n', encoding="utf-8")

    by_state = file_state_token([str(derived)])
    by_content = file_digest_token([str(derived)])
    state_before, content_before = by_state(_ctx()), by_content(_ctx())

    derived.unlink()
    derived.write_text('{"a":1}\n', encoding="utf-8")  # same bytes, new mtime

    assert by_state(_ctx()) != state_before, "mtime moved — the cheap token cannot settle"
    assert by_content(_ctx()) == content_before, "content is identical — the gate must fire"

    derived.write_text('{"a":2}\n', encoding="utf-8")
    assert by_content(_ctx()) != content_before


def test_manifest_token_moves_only_when_brain2_is_rebuilt() -> None:
    """The Tier-1 graph signal. It must settle on a quiet cycle (or the gate never fires) and
    move on a real rebuild (or centrality/co-change serve a stale rung)."""
    manifest = InMemoryFileManifest()
    manifest.save(SCOPE, {"a.py": ManifestEntry("sha-a", ("module:a",))})
    token = manifest_token(manifest, SCOPE)
    settled = token(_ctx())

    assert token(_ctx()) == settled  # nothing rebuilt
    manifest.save(SCOPE, {"a.py": ManifestEntry("sha-a2", ("module:a",))})
    assert token(_ctx()) != settled  # a file's content changed


def test_manifest_token_is_insensitive_to_entry_order_and_node_ids() -> None:
    """Order would make every cycle look like a rebuild; node_ids are a function of the sha,
    so including them would only add noise."""
    a, b = InMemoryFileManifest(), InMemoryFileManifest()
    a.save(SCOPE, {"a.py": ManifestEntry("s1", ("module:a",)),
                   "b.py": ManifestEntry("s2", ("module:b",))})
    b.save(SCOPE, {"b.py": ManifestEntry("s2", ("module:b", "function:b.f")),
                   "a.py": ManifestEntry("s1", ())})
    assert manifest_token(a, SCOPE)(_ctx()) == manifest_token(b, SCOPE)(_ctx())


def test_manifest_token_sees_another_process_rebuild() -> None:
    """The reason this replaced an in-process counter: a durable manifest is shared, so a
    rebuild performed by a *different* reader of the same store still busts the gate."""
    shared = InMemoryFileManifest()  # stands in for the durable Neo4j manifest
    mine = manifest_token(shared, SCOPE)
    shared.save(SCOPE, {"a.py": ManifestEntry("sha-a", ())})
    before = mine(_ctx())

    # Someone else rebuilds Brain 2 and writes the manifest; my process did nothing.
    shared.save(SCOPE, {"a.py": ManifestEntry("sha-a-rebuilt", ())})
    assert mine(_ctx()) != before


def test_manifest_token_fails_open_when_the_manifest_cannot_be_read() -> None:
    class _Broken:
        def load(self, scope: Scope) -> dict[str, ManifestEntry]:
            raise RuntimeError("backend down")

        def save(self, scope: Scope, entries: object) -> None: ...

    assert manifest_token(_Broken(), SCOPE)(_ctx()) is None  # type: ignore[arg-type]
