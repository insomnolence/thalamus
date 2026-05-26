from __future__ import annotations

import importlib.util
from collections.abc import Sequence
from datetime import UTC, datetime

import pytest
from thalamus.core.types import (
    Hemisphere,
    MemoryId,
    MemoryRecord,
    RepoId,
    Scope,
    SessionId,
    TenantId,
)
from thalamus.gateway import Gateway, build_server
from thalamus.instrumentation import InMemoryEventSink, LoggingRetriever
from thalamus.retrieval import L0Retriever
from thalamus.routing import DeterministicEncoder
from thalamus.store import InMemoryStore

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("fastmcp") is None,
    reason="fastmcp not installed (the 'mcp' extra)",
)

SCOPE = Scope(tenant_id=TenantId("acme"), repo_id=RepoId("widgets"))
NOW = datetime(2026, 5, 25, tzinfo=UTC)


def _gateway() -> Gateway:
    encoder = DeterministicEncoder(dim=64)
    store = InMemoryStore(dim=64)
    content = "switched to sqlite; json too slow"
    store.add(
        MemoryRecord(
            MemoryId("sqlite"),
            Hemisphere.EXPERIENTIAL,
            "episode",
            content,
            SCOPE,
            NOW,
            {"why": "json too slow"},
        ),
        encoder.encode([content])[0],
    )
    return Gateway(L0Retriever(encoder, store, now=lambda: NOW), k=3)


async def test_recall_tool_returns_context_over_mcp() -> None:
    from fastmcp import Client

    server = build_server(_gateway(), SCOPE)
    async with Client(server) as client:
        result = await client.call_tool(
            "recall", {"prompt": "why did we move to sqlite"}
        )
        text = result.data if isinstance(result.data, str) else result.content[0].text
        assert "sqlite" in text
        assert "why: json too slow" in text


def _logging_gateway() -> tuple[Gateway, InMemoryEventSink]:
    encoder = DeterministicEncoder(dim=64)
    store = InMemoryStore(dim=64)
    content = "switched to sqlite; json too slow"
    record = MemoryRecord(
        MemoryId("sqlite"), Hemisphere.EXPERIENTIAL, "episode", content, SCOPE, NOW, {}
    )
    store.add(record, encoder.encode([content])[0])
    sink = InMemoryEventSink()
    retriever = LoggingRetriever(
        L0Retriever(encoder, store, now=lambda: NOW), sink, policy_id="L0"
    )
    return Gateway(retriever, k=3), sink


async def test_recall_defaults_to_server_session_id_when_caller_omits_it() -> None:
    from fastmcp import Client

    gateway, sink = _logging_gateway()
    server = build_server(gateway, SCOPE, default_session_id=SessionId("serve-test-1"))
    async with Client(server) as client:
        await client.call_tool("recall", {"prompt": "why did we move to sqlite"})
    assert sink.events[-1].session_id == SessionId("serve-test-1")


async def test_caller_session_id_overrides_server_default() -> None:
    from fastmcp import Client

    gateway, sink = _logging_gateway()
    server = build_server(gateway, SCOPE, default_session_id=SessionId("serve-test-1"))
    async with Client(server) as client:
        await client.call_tool(
            "recall", {"prompt": "why did we move to sqlite", "session_id": "caller-99"}
        )
    assert sink.events[-1].session_id == SessionId("caller-99")


async def test_recall_tool_lists_in_server() -> None:
    from fastmcp import Client

    server = build_server(_gateway(), SCOPE)
    async with Client(server) as client:
        tools = await client.list_tools()
        assert "recall" in {tool.name for tool in tools}


async def test_remember_tool_is_exposed_only_with_writer_and_calls_it() -> None:
    from fastmcp import Client

    calls: list[tuple[str, str, str | None, Sequence[str], float, str | None]] = []

    def write(
        kind: str,
        text: str,
        why: str | None,
        files: Sequence[str],
        importance: float,
        memory_id: str | None,
    ) -> MemoryRecord:
        calls.append((kind, text, why, files, importance, memory_id))
        return MemoryRecord(
            MemoryId("retained:scope"),
            Hemisphere.EXPERIENTIAL,
            kind,
            text,
            SCOPE,
            NOW,
            {"source": "curated"},
        )

    server = build_server(_gateway(), SCOPE, remember_writer=write)
    async with Client(server) as client:
        tools = {tool.name for tool in await client.list_tools()}
        assert "remember" in tools
        result = await client.call_tool(
            "remember",
            {
                "kind": "constraint",
                "text": "ids stay scoped",
                "why": "isolation",
                "files": ["pkg/store.py"],
                "importance": 1.0,
                "memory_id": "scope",
            },
        )
    assert calls == [("constraint", "ids stay scoped", "isolation", ["pkg/store.py"], 1.0, "scope")]
    assert "remembered retained:scope" in str(result.data)
    assert "after the MCP server restarts" in str(result.data)
