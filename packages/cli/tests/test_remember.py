from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from thalamus.cli import build_two_hemisphere_gateway
from thalamus.cli.remember import RememberConfig, build_retained_record, run_remember
from thalamus.core.exceptions import ThalamusError
from thalamus.core.types import MemoryId, RepoId, Scope, TenantId
from thalamus.experiential import InMemorySupersessionIndex
from thalamus.routing import DeterministicEncoder
from thalamus.store import InMemoryStore

NOW = datetime(2026, 5, 25, tzinfo=UTC)


def _config(repo: Path, **changes: object) -> RememberConfig:
    values: dict[str, object] = {
        "repo": repo,
        "tenant": "local",
        "repo_id": "repo",
        "dim": 64,
        "encoder": "deterministic",
        "kind": "constraint",
        "text": "Memory identities must remain scoped by tenant and repository.",
        "why": "unscoped ids overwrite another repository's retained knowledge",
        "files": (Path("pkg/store.py"),),
        "importance": 1.0,
        "memory_id": None,
        "supersedes": None,
        "neo4j_uri": None,
        "neo4j_user": "neo4j",
        "neo4j_password": None,
    }
    values.update(changes)
    return RememberConfig(**values)  # type: ignore[arg-type]


def test_build_retained_record_has_stable_id_and_curated_metadata(tmp_path: Path) -> None:
    config = _config(tmp_path)
    first = build_retained_record(config, now=lambda: NOW)
    second = build_retained_record(config, now=lambda: NOW)

    assert first.memory_id == second.memory_id
    assert str(first.memory_id).startswith("retained:")
    assert first.kind == "constraint"
    assert first.metadata["source"] == "curated"
    assert first.metadata["footprint"] == ["pkg/store.py"]
    assert build_retained_record(_config(tmp_path, memory_id="scope-rule")).memory_id == MemoryId(
        "retained:scope-rule"
    )


def test_remembered_fact_is_recallable_and_linked_to_focused_code(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "store.py").write_text("class Store:\n    pass\n", encoding="utf-8")
    config = _config(repo)
    encoder = DeterministicEncoder(dim=64)
    store = InMemoryStore(dim=64)

    record = run_remember(config, store=store, encoder=encoder)
    run_remember(config, store=store, encoder=encoder)
    assert len(store.scan(Scope(TenantId("local"), RepoId("repo")))) == 1

    gateway = build_two_hemisphere_gateway(
        repo,
        store=store,
        encoder=encoder,
        scope=record.scope,
        episodes=[record],
        k=1,
    )
    payload = gateway.recall(prompt="why must identities remain scoped", scope=record.scope)
    assert payload.memories[0].memory_id == record.memory_id
    assert payload.memories[0].retained is True
    assert "## Retained memory" in payload.render()
    assert any(item.node_id == "module:pkg.store" for item in payload.structural)


def test_remember_refuses_ephemeral_cli_storage(tmp_path: Path) -> None:
    with pytest.raises(ThalamusError, match="durable Brain 1 storage"):
        run_remember(_config(tmp_path))


def test_remember_rejects_file_outside_repository(tmp_path: Path) -> None:
    with pytest.raises(ThalamusError, match="outside the repository"):
        build_retained_record(
            _config(tmp_path / "repo", files=(tmp_path / "elsewhere.py",)),
            now=lambda: NOW,
        )


def test_remember_rejects_invalid_mcp_inputs(tmp_path: Path) -> None:
    with pytest.raises(ThalamusError, match="unsupported retained memory kind"):
        build_retained_record(_config(tmp_path, kind="conversation"), now=lambda: NOW)
    with pytest.raises(ThalamusError, match="must not be empty"):
        build_retained_record(_config(tmp_path, text="   "), now=lambda: NOW)


def test_remember_records_supersession_edge(tmp_path: Path) -> None:
    encoder = DeterministicEncoder(dim=64)
    store = InMemoryStore(dim=64)
    index = InMemorySupersessionIndex()
    scope = Scope(TenantId("local"), RepoId("repo"))

    old = run_remember(
        _config(tmp_path, text="we use the lexical usage signal", files=()),
        store=store, encoder=encoder,
    )
    new = run_remember(
        _config(
            tmp_path,
            text="we use the footprint usage signal",
            why="lexical under-counted real usage",
            files=(),
            supersedes=str(old.memory_id),
        ),
        store=store, encoder=encoder, supersession=index,
    )

    superseded = index.superseded(scope)
    assert set(superseded) == {old.ref}
    assert superseded[old.ref].superseded_by == new.memory_id
    assert superseded[old.ref].reason == "lexical under-counted real usage"
    # The old belief is kept, not deleted.
    assert store.get(old.ref) is not None


def test_supersede_unknown_memory_is_skipped_not_fatal(tmp_path: Path) -> None:
    # A dangling supersedes must NOT lose the new memory or raise — raising-after-save is what
    # made agents see an error and retry, creating duplicates. The memory is kept; edge skipped.
    store = InMemoryStore(dim=64)
    index = InMemorySupersessionIndex()
    record = run_remember(
        _config(tmp_path, files=(), supersedes="retained:does-not-exist"),
        store=store, encoder=DeterministicEncoder(dim=64), supersession=index,
    )
    assert store.get(record.ref) is not None  # memory kept
    assert index.superseded(record.scope) == {}  # no dangling edge forged


def test_supersede_without_an_index_is_skipped_not_fatal(tmp_path: Path) -> None:
    store = InMemoryStore(dim=64)
    record = run_remember(  # no supersession index -> can't record the edge, but must still save
        _config(tmp_path, files=(), supersedes="retained:whatever"),
        store=store, encoder=DeterministicEncoder(dim=64),
    )
    assert store.get(record.ref) is not None


def test_supersede_accepts_bare_hash_without_the_retained_prefix(tmp_path: Path) -> None:
    # Agents often pass the bare hash from a recall instead of the full retained:<hash> id;
    # the supersede target must still resolve.
    store = InMemoryStore(dim=64)
    index = InMemorySupersessionIndex()
    enc = DeterministicEncoder(dim=64)
    old = run_remember(_config(tmp_path, files=()), store=store, encoder=enc, supersession=index)
    bare = old.memory_id.removeprefix("retained:")  # what an agent might pass
    run_remember(
        _config(tmp_path, files=(), supersedes=bare, text="a newer, replacing belief"),
        store=store, encoder=enc, supersession=index,
    )
    assert old.ref in index.superseded(old.scope)  # resolved despite the missing prefix
