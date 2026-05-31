"""CredibilityPass orchestrates an injected fate assessor over the curated belief layer."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from thalamus.core import Hemisphere, MemoryId, MemoryRecord, RepoId, Scope, TenantId
from thalamus.dreaming import CredibilityPass, PassContext, PassStatus
from thalamus.routing import DeterministicEncoder
from thalamus.store import InMemoryStore

SCOPE = Scope(TenantId("t"), RepoId("r"))
NOW = datetime(2026, 5, 30, 12, 0, tzinfo=UTC)


def _mem(memory_id: str, kind: str = "decision") -> MemoryRecord:
    return MemoryRecord(MemoryId(memory_id), Hemisphere.EXPERIENTIAL, kind, "text", SCOPE, NOW)


def _store(*memories: MemoryRecord) -> InMemoryStore:
    encoder = DeterministicEncoder(dim=16)
    store = InMemoryStore(dim=16)
    for memory in memories:
        store.add(memory, encoder.encode([memory.content])[0])
    return store


def test_assesses_curated_only_and_reports_the_distribution() -> None:
    store = _store(_mem("retained:a"), _mem("retained:b"), _mem("episode:c", "episode"))
    seen: dict[str, list[str]] = {}

    def assess(memories: Sequence[MemoryRecord]) -> Mapping[MemoryId, tuple[str, str]]:
        seen["ids"] = [str(memory.memory_id) for memory in memories]
        result: dict[MemoryId, tuple[str, str]] = {}
        for memory in memories:
            polarity = "positive" if str(memory.memory_id).endswith("a") else "unknown"
            result[memory.memory_id] = (polarity, "objective")
        return result

    outcome = CredibilityPass(assess).run(PassContext(scope=SCOPE, now=NOW, store=store))
    assert outcome.status is PassStatus.OK
    assert "episode:c" not in seen["ids"]  # episodes are not part of the belief-layer credibility
    assert outcome.details["polarity"] == {"positive": 1, "unknown": 1}


def test_lists_the_negatives() -> None:
    store = _store(_mem("retained:x"), _mem("retained:y"))

    def assess(memories: Sequence[MemoryRecord]) -> Mapping[MemoryId, tuple[str, str]]:
        return {memory.memory_id: ("negative", "objective") for memory in memories}

    outcome = CredibilityPass(assess).run(PassContext(scope=SCOPE, now=NOW, store=store))
    assert outcome.details["negatives"] == ["retained:x", "retained:y"]


def test_skips_without_a_store_handle() -> None:
    outcome = CredibilityPass(lambda _memories: {}).run(PassContext(scope=SCOPE, now=NOW))
    assert outcome.status is PassStatus.SKIPPED
