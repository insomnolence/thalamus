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
    build_shown_resolver,
    serve_config,
)
from thalamus.core.types import (
    EventId,
    Hemisphere,
    MemoryId,
    MemoryRecord,
    RepoId,
    Scope,
    TenantId,
)
from thalamus.gateway import build_server
from thalamus.instrumentation import (
    JsonlEventSink,
    RetrievalEvent,
    ShownItem,
    read_event_log,
    read_usage_log,
)
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


def test_serve_config_transport_flags(tmp_path: Path) -> None:
    parser = argparse.ArgumentParser()
    add_serve_arguments(parser)
    default = serve_config(parser.parse_args(["--repo", str(tmp_path)]))
    assert default.transport == "stdio"  # stdio default keeps Claude Code / .mcp.json unaffected
    assert default.host == "127.0.0.1"
    assert default.port == 8000
    http = serve_config(
        parser.parse_args(
            ["--repo", str(tmp_path), "--transport", "http", "--host", "0.0.0.0", "--port", "8765"]
        )
    )
    assert http.transport == "http"
    assert http.host == "0.0.0.0"
    assert http.port == 8765


def test_serve_config_http_security_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parser = argparse.ArgumentParser()
    add_serve_arguments(parser)
    monkeypatch.delenv("THALAMUS_HTTP_TOKEN", raising=False)
    monkeypatch.delenv("THALAMUS_HTTP_ALLOWED_ORIGINS", raising=False)
    bare = serve_config(parser.parse_args(["--repo", str(tmp_path)]))
    assert bare.http_token is None
    assert bare.allowed_origins == ()

    monkeypatch.setenv("THALAMUS_HTTP_TOKEN", "s3cret")
    monkeypatch.setenv("THALAMUS_HTTP_ALLOWED_ORIGINS", "https://a.example, https://b.example")
    cfg = serve_config(parser.parse_args(["--repo", str(tmp_path)]))
    assert cfg.http_token == "s3cret"
    assert cfg.allowed_origins == ("https://a.example", "https://b.example")


def test_serve_config_session_flags(tmp_path: Path) -> None:
    parser = argparse.ArgumentParser()
    add_serve_arguments(parser)
    off = serve_config(parser.parse_args(["--repo", str(tmp_path), "--no-session"]))
    assert off.session is False
    explicit = serve_config(parser.parse_args(["--repo", str(tmp_path), "--session-id", "abc123"]))
    assert explicit.session is True
    assert explicit.session_id == "abc123"


def test_build_shown_resolver_reconstructs_from_log_and_store(tmp_path: Path) -> None:
    # The record_usage durability fallback: rebuild a recall's shown (memory_id, content) pairs
    # from the durable retrieval-event log + store, with no live in-memory payload.
    scope = Scope(tenant_id=TenantId("local"), repo_id=RepoId("repo"))
    encoder = DeterministicEncoder(dim=32)
    store = InMemoryStore(dim=32)
    for mid, content in (("m1", "alpha content"), ("m2", "beta content")):
        store.add(
            MemoryRecord(MemoryId(mid), Hemisphere.EXPERIENTIAL, "decision", content, scope, NOW),
            encoder.encode([content])[0],
        )

    log = tmp_path / "retrieval.jsonl"
    sink = JsonlEventSink(log)
    sink.emit(RetrievalEvent(
        event_id=EventId("evt-1"), timestamp=NOW, scope=scope, policy_id="L0",
        cue_text="q", k_requested=2, candidates=[],
        shown=[ShownItem(MemoryId("m1"), 0, 1.0), ShownItem(MemoryId("m2"), 1, 1.0)],
    ))
    # an event whose second shown memory was deleted since the recall
    sink.emit(RetrievalEvent(
        event_id=EventId("evt-2"), timestamp=NOW, scope=scope, policy_id="L0",
        cue_text="q2", k_requested=2, candidates=[],
        shown=[ShownItem(MemoryId("m1"), 0, 1.0), ShownItem(MemoryId("ghost"), 1, 1.0)],
    ))

    resolve = build_shown_resolver(store, scope, log)

    assert resolve(EventId("evt-1")) == [
        (MemoryId("m1"), "alpha content"),
        (MemoryId("m2"), "beta content"),
    ]
    # a memory gone from the store is skipped; the survivor still resolves
    assert resolve(EventId("evt-2")) == [(MemoryId("m1"), "alpha content")]
    # a genuinely unknown event id -> None (record_usage then raises, as before)
    assert resolve(EventId("missing")) is None


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
    gateway, returned, episodes, *_ = build_serve_gateway(config, store=store, encoder=encoder)

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
    gateway, *_ = build_serve_gateway(config, store=store, encoder=encoder)
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
    gateway, *_ = build_serve_gateway(config, store=store, encoder=encoder)
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


def _regen_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[object, object, list[str], Path, Path]:
    from thalamus.cli import serve as serve_mod
    from thalamus.cli.project import CorpusConfig
    from thalamus.structural import CorpusSpec, glob_files

    src = tmp_path / "src"
    src.mkdir()
    ts = src / "a.ts"
    ts.write_text("v1", encoding="utf-8")
    scip = tmp_path / "i.scip"
    scip.write_text("idx", encoding="utf-8")
    cfg = CorpusConfig(
        name="ts", root=src, kind="scip", scip_index=scip,
        include=("*.ts",), regen_command="build-index",
    )
    ran: list[str] = []
    monkeypatch.setattr(serve_mod, "_run_regen", lambda corpus: ran.append(corpus.name))
    hook = serve_mod.build_regen_hook([cfg])
    spec = CorpusSpec(None, None, glob_files("*.ts"), "ts", root=src)  # type: ignore[arg-type]
    return hook, spec, ran, ts, scip


def test_regen_runs_only_when_source_is_newer_than_the_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    hook, spec, ran, ts, scip = _regen_fixture(tmp_path, monkeypatch)
    assert hook is not None
    os.utime(ts, (1000, 1000))
    os.utime(scip, (2000, 2000))  # artifact built AFTER the source → fresh → no regen
    hook([spec])
    assert ran == []
    os.utime(ts, (3000, 3000))  # source edited after the artifact → regen, once
    hook([spec])
    assert ran == ["ts"]


def test_regen_fires_when_the_artifact_is_stale_at_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    hook, spec, ran, ts, scip = _regen_fixture(tmp_path, monkeypatch)
    assert hook is not None
    os.utime(scip, (1000, 1000))
    os.utime(ts, (2000, 2000))  # source changed while the serve was down → stale index at startup
    hook([spec])
    assert ran == ["ts"]  # the very first tick catches it (an mtime gate, not seed-on-first-sight)


def test_regen_hook_is_none_without_any_regen_command(tmp_path: Path) -> None:
    from thalamus.cli import serve as serve_mod
    from thalamus.cli.project import CorpusConfig

    cfg = CorpusConfig(name="py", root=tmp_path, kind="python-ast")
    assert serve_mod.build_regen_hook([cfg]) is None
