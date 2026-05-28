from __future__ import annotations

import argparse
import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pytest
from thalamus.cli.serve import (
    ServeConfig,
    add_serve_arguments,
    build_remember_writer,
    build_serve_gateway,
    serve_config,
)
from thalamus.core.types import Hemisphere, MemoryId, MemoryRecord, RepoId, Scope, TenantId
from thalamus.gateway import build_server
from thalamus.instrumentation import read_event_log, read_usage_log
from thalamus.routing import DeterministicEncoder
from thalamus.store import InMemoryStore

NOW = datetime(2026, 5, 25, tzinfo=UTC)


def test_serve_config_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("THALAMUS_NEO4J_URI", raising=False)
    parser = argparse.ArgumentParser()
    add_serve_arguments(parser)
    config = serve_config(
        parser.parse_args(["--repo", str(tmp_path), "--k-hop", "2", "--encoder", "deterministic"])
    )
    assert config.repo == tmp_path.resolve()
    assert config.repo_id == tmp_path.name  # defaults to the repo dir name
    assert config.k_hop == 2
    assert config.max_structural_items == 12
    assert config.max_memory_chars == 1000
    assert config.neo4j_uri is None
    assert config.session is True  # session tagging on by default
    assert config.session_id is None  # minted per process unless overridden


def test_serve_config_session_flags(tmp_path: Path) -> None:
    parser = argparse.ArgumentParser()
    add_serve_arguments(parser)
    off = serve_config(parser.parse_args(["--repo", str(tmp_path), "--no-session"]))
    assert off.session is False
    explicit = serve_config(parser.parse_args(["--repo", str(tmp_path), "--session-id", "abc123"]))
    assert explicit.session is True
    assert explicit.session_id == "abc123"


def test_build_serve_gateway_scans_brain1_and_relinks(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "store.py").write_text(
        "class Store:\n    def add(self):\n        return 1\n", encoding="utf-8"
    )
    scope = Scope(tenant_id=TenantId("local"), repo_id=RepoId("repo"))

    # a populated durable Brain 1 (injected in place of Neo4j)
    encoder = DeterministicEncoder(dim=64)
    store = InMemoryStore(dim=64)
    episode = MemoryRecord(
        MemoryId("ep1"), Hemisphere.EXPERIENTIAL, "episode", "reworked the store add path",
        scope, NOW, metadata={"footprint": ["pkg/store.py"]},
    )
    store.add(episode, encoder.encode([episode.content])[0])

    config = ServeConfig(
        repo=repo, tenant="local", repo_id="repo", dim=64, encoder="deterministic", k=5, k_hop=1,
        resolve_calls=False, structural_min_relevance=0.0,
        max_structural_items=12, max_memory_chars=1000,
        neo4j_uri=None, neo4j_user="neo4j", neo4j_password=None,
    )
    gateway, returned, episodes, _ = build_serve_gateway(config, store=store, encoder=encoder)

    assert returned is store
    assert [e.memory_id for e in episodes] == [MemoryId("ep1")]  # scanned from Brain 1

    payload = gateway.recall(prompt="reworked the store add path", scope=scope)
    assert [m.memory_id for m in payload.memories] == [MemoryId("ep1")]
    assert any(item.node_id == "module:pkg.store" for item in payload.structural)  # re-linked


@pytest.mark.skipif(importlib.util.find_spec("fastmcp") is None, reason="fastmcp not installed")
async def test_served_mcp_path_persists_recall_and_usage_logs(tmp_path: Path) -> None:
    from fastmcp import Client

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "store.py").write_text("def connect():\n    return 1\n", encoding="utf-8")
    scope = Scope(TenantId("local"), RepoId("repo"))
    encoder = DeterministicEncoder(dim=64)
    store = InMemoryStore(dim=64)
    record = MemoryRecord(
        MemoryId("m"), Hemisphere.EXPERIENTIAL, "episode", "use aiosqlite async store", scope, NOW
    )
    store.add(record, encoder.encode([record.content])[0])
    config = ServeConfig(
        repo=repo, tenant="local", repo_id="repo", dim=64, encoder="deterministic", k=1, k_hop=1,
        resolve_calls=False, structural_min_relevance=0.0,
        max_structural_items=12, max_memory_chars=1000,
        neo4j_uri=None, neo4j_user="neo4j", neo4j_password=None,
    )
    gateway, _, _, _ = build_serve_gateway(config, store=store, encoder=encoder)
    async with Client(build_server(gateway, scope)) as client:
        await client.call_tool("recall", {"prompt": "async store", "session_id": "session"})
        (event,) = list(read_event_log(repo / ".thalamus" / "logs" / "retrieval.jsonl"))
        await client.call_tool(
            "record_usage",
            {"event_id": str(event.event_id), "output_text": "use aiosqlite for async store"},
        )
    assert len(list(read_usage_log(repo / ".thalamus" / "logs" / "usage.jsonl"))) == 1


@pytest.mark.skipif(importlib.util.find_spec("fastmcp") is None, reason="fastmcp not installed")
async def test_served_mcp_remember_is_immediately_recallable(tmp_path: Path) -> None:
    from fastmcp import Client

    repo = tmp_path / "repo"
    repo.mkdir()
    scope = Scope(TenantId("local"), RepoId("repo"))
    encoder = DeterministicEncoder(dim=64)
    store = InMemoryStore(dim=64)
    config = ServeConfig(
        repo=repo, tenant="local", repo_id="repo", dim=64, encoder="deterministic", k=1, k_hop=1,
        resolve_calls=False, structural_min_relevance=0.0,
        max_structural_items=12, max_memory_chars=1000,
        neo4j_uri=None, neo4j_user="neo4j", neo4j_password=None,
    )
    gateway, _, _, _ = build_serve_gateway(config, store=store, encoder=encoder)
    server = build_server(
        gateway,
        scope,
        remember_writer=build_remember_writer(config, store=store, encoder=encoder),
    )
    async with Client(server) as client:
        saved = await client.call_tool(
            "remember",
            {"kind": "gotcha", "text": "sqlite migrations require a rollback test"},
        )
        recalled = await client.call_tool(
            "recall", {"prompt": "rollback test for sqlite migration"}
        )
    assert "remembered retained:" in str(saved.data)
    assert "sqlite migrations require a rollback test" in str(recalled.data)
    (record,) = store.scan(scope)
    assert record.metadata["source"] == "curated"
