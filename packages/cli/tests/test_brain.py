from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from thalamus.cli import build_two_hemisphere_gateway
from thalamus.core.types import Hemisphere, MemoryId, MemoryRecord, RepoId, Scope, TenantId
from thalamus.routing import DeterministicEncoder
from thalamus.store import InMemoryStore

SCOPE = Scope(tenant_id=TenantId("t"), repo_id=RepoId("r"))
NOW = datetime(2026, 5, 25, tzinfo=UTC)


def test_recall_fuses_episode_with_touched_code(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "store.py").write_text(
        "class Store:\n    def add(self):\n        return 1\n", encoding="utf-8"
    )

    encoder = DeterministicEncoder(dim=64)
    store = InMemoryStore(dim=64)
    episode = MemoryRecord(
        MemoryId("ep1"), Hemisphere.EXPERIENTIAL, "episode",
        "reworked the store add path", SCOPE, NOW,
        metadata={"footprint": ["pkg/store.py"]},  # the episode's commit footprint
    )
    store.add(episode, encoder.encode([episode.content])[0])

    gateway = build_two_hemisphere_gateway(
        repo, store=store, encoder=encoder, scope=SCOPE, episodes=[episode]
    )
    payload = gateway.recall(prompt="reworked the store add path", scope=SCOPE)

    # experiential recall surfaces the episode...
    assert [m.memory_id for m in payload.memories] == [MemoryId("ep1")]
    # ...and the footprint link surfaces the code it touched (module), plus k-hop the class
    node_ids = {item.node_id for item in payload.structural}
    assert "module:pkg.store" in node_ids
    assert any("Store" in item.label for item in payload.structural)


def test_no_episodes_means_no_structural_section(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    gateway = build_two_hemisphere_gateway(
        tmp_path,
        store=InMemoryStore(dim=32),
        encoder=DeterministicEncoder(dim=32),
        scope=SCOPE,
        episodes=[],
    )
    payload = gateway.recall(prompt="anything", scope=SCOPE)
    assert payload.memories == []
    assert payload.structural == []


def test_focus_path_recovers_linked_memory_over_semantic_distractor(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "store.py").write_text("def write():\n    return 1\n", encoding="utf-8")
    encoder = DeterministicEncoder(dim=64)
    store = InMemoryStore(dim=64)
    linked = MemoryRecord(
        MemoryId("linked"), Hemisphere.EXPERIENTIAL, "episode",
        "avoid blocking writes in this adapter", SCOPE, NOW,
        metadata={"footprint": ["pkg/store.py"]},
    )
    distractor = MemoryRecord(
        MemoryId("distractor"), Hemisphere.EXPERIENTIAL, "episode",
        "rename database connector", SCOPE, NOW,
    )
    for record in (linked, distractor):
        store.add(record, encoder.encode([record.content])[0])
    gateway = build_two_hemisphere_gateway(
        repo, store=store, encoder=encoder, scope=SCOPE, episodes=[linked], k=1
    )
    payload = gateway.recall(
        prompt="rename database connector", focus="pkg/store.py", scope=SCOPE
    )
    assert payload.memories[0].memory_id == MemoryId("linked")
