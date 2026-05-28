"""BeliefAuditPass (proposer) proposes — never acts — and only on beliefs whose
code footprint has wholly vanished."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from thalamus.core import Hemisphere, MemoryId, MemoryRecord, RepoId, Scope, TenantId
from thalamus.dreaming import BeliefAuditPass, PassContext, PassKind, PassStatus
from thalamus.routing import DeterministicEncoder
from thalamus.store import InMemoryStore

SCOPE = Scope(TenantId("t"), RepoId("r"))
NOW = datetime(2026, 5, 28, 12, 0, tzinfo=UTC)


def _curated(mid: str, footprint: tuple[str, ...]) -> MemoryRecord:
    return MemoryRecord(
        MemoryId(mid), Hemisphere.EXPERIENTIAL, "decision", f"belief {mid}", SCOPE, NOW,
        metadata={"source": "curated", "footprint": list(footprint)},
    )


def _store(encoder: DeterministicEncoder, *records: MemoryRecord) -> InMemoryStore:
    store = InMemoryStore(dim=32)
    for record in records:
        store.add(record, encoder.encode([record.content])[0])
    return store


def _ctx(store: InMemoryStore, repo_root: str) -> PassContext:
    return PassContext(scope=SCOPE, now=NOW, store=store, repo_root=repo_root)


def test_proposes_only_when_the_whole_footprint_is_gone(tmp_path: Path) -> None:
    encoder = DeterministicEncoder(dim=32)
    (tmp_path / "kept.py").write_text("x = 1\n")
    # gone: both files absent -> propose. partial: one file remains -> no proposal. live: present.
    gone = _curated("gone", ("removed_a.py", "removed_b.py"))
    partial = _curated("partial", ("kept.py", "removed_c.py"))
    live = _curated("live", ("kept.py",))
    store = _store(encoder, gone, partial, live)

    outcome = BeliefAuditPass().run(_ctx(store, str(tmp_path)))

    assert outcome.status is PassStatus.OK
    proposals = outcome.details["proposals"]
    assert [p["memory_id"] for p in proposals] == ["gone"]
    assert proposals[0]["evidence"] == ["removed_a.py", "removed_b.py"]
    assert "2 footprint file(s) removed" in proposals[0]["reason"]


def test_is_a_proposer_and_emits_no_proposals_when_all_code_is_present(tmp_path: Path) -> None:
    encoder = DeterministicEncoder(dim=32)
    (tmp_path / "here.py").write_text("x = 1\n")
    store = _store(encoder, _curated("ok", ("here.py",)))

    pass_ = BeliefAuditPass()
    assert pass_.kind is PassKind.PROPOSER  # firewall: propose-only

    outcome = pass_.run(_ctx(store, str(tmp_path)))
    assert outcome.summary == "no supersession proposals"
    assert "proposals" not in outcome.details


def test_ignores_beliefs_with_no_footprint(tmp_path: Path) -> None:
    encoder = DeterministicEncoder(dim=32)
    store = _store(encoder, _curated("no_footprint", ()))
    outcome = BeliefAuditPass().run(_ctx(store, str(tmp_path)))
    assert outcome.summary == "no supersession proposals"


def test_skips_without_a_repo_root() -> None:
    outcome = BeliefAuditPass().run(PassContext(scope=SCOPE, now=NOW, store=InMemoryStore(dim=32)))
    assert outcome.status is PassStatus.SKIPPED
