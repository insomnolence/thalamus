from __future__ import annotations

import importlib.util
from datetime import UTC, datetime

import pytest
from thalamus.core.types import Hemisphere, MemoryId, MemoryRecord, RepoId, Scope, TenantId
from thalamus.gateway import Gateway, build_server
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

    server = build_server(_gateway())
    async with Client(server) as client:
        result = await client.call_tool(
            "recall", {"prompt": "why did we move to sqlite", "tenant": "acme", "repo": "widgets"}
        )
        text = result.data if isinstance(result.data, str) else result.content[0].text
        assert "sqlite" in text
        assert "why: json too slow" in text


async def test_recall_tool_lists_in_server() -> None:
    from fastmcp import Client

    server = build_server(_gateway())
    async with Client(server) as client:
        tools = await client.list_tools()
        assert "recall" in {tool.name for tool in tools}
