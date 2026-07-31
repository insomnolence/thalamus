from __future__ import annotations

import importlib.util
import logging
from collections.abc import Sequence
from datetime import UTC, datetime

import pytest
from thalamus.core.exceptions import StoreError, UserFacingError
from thalamus.core.types import (
    Cue,
    EventId,
    Hemisphere,
    MemoryId,
    MemoryRecord,
    RepoId,
    RetrievalResult,
    Scope,
    SessionId,
    TenantId,
)
from thalamus.gateway import Gateway, build_server
from thalamus.instrumentation import InMemoryEventSink, InMemoryUsageSink, LoggingRetriever
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


def test_health_route_is_registered_for_liveness_probes() -> None:
    # The HTTP serve mounts only /mcp, so any other path 404s; a GET /health → 200 liveness route
    # keeps health probes / scanners from generating that 404 noise.
    server = build_server(_gateway(), SCOPE)
    paths = {getattr(route, "path", None) for route in server._additional_http_routes}
    assert "/health" in paths


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


def _logging_gateway(
    usage_sink: InMemoryUsageSink | None = None,
) -> tuple[Gateway, InMemoryEventSink]:
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
    return Gateway(retriever, k=3, usage_sink=usage_sink), sink


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


async def test_record_usage_preserves_explicit_empty_declaration() -> None:
    from fastmcp import Client

    usage_sink = InMemoryUsageSink()
    gateway, event_sink = _logging_gateway(usage_sink)
    server = build_server(gateway, SCOPE)
    async with Client(server) as client:
        await client.call_tool("recall", {"prompt": "why did we move to sqlite"})
        event_id = str(event_sink.events[-1].event_id)
        await client.call_tool(
            "record_usage",
            {
                "event_id": event_id,
                "output_text": "The recalled context did not shape this answer.",
                "used_memory_ids": [],
            },
        )

    declared = [signal for signal in usage_sink.signals if signal.kind == "declared"]
    assert declared
    assert not any(signal.used for signal in declared)


async def test_per_connection_sessions_key_by_connection_not_process_default() -> None:
    from fastmcp import Client

    gateway, sink = _logging_gateway()
    # HTTP-style: many clients, one process. The recall must be keyed by the caller's MCP
    # connection session, NOT the single process default (which would collapse all clients).
    server = build_server(
        gateway, SCOPE, default_session_id=SessionId("proc-default"), per_connection_sessions=True
    )
    async with Client(server) as client:
        await client.call_tool("recall", {"prompt": "why did we move to sqlite"})
    keyed = sink.events[-1].session_id
    assert keyed is not None
    assert keyed != SessionId("proc-default")  # used the per-connection id, not the process one
    # an explicit caller id still wins even under per-connection keying
    async with Client(server) as client:
        await client.call_tool(
            "recall", {"prompt": "why did we move to sqlite", "session_id": "caller-7"}
        )
    assert sink.events[-1].session_id == SessionId("caller-7")


async def test_recall_tool_lists_in_server() -> None:
    from fastmcp import Client

    server = build_server(_gateway(), SCOPE)
    async with Client(server) as client:
        tools = await client.list_tools()
        assert "recall" in {tool.name for tool in tools}


async def test_read_only_server_exposes_recall_but_not_writes() -> None:
    from fastmcp import Client

    def write(*args: object) -> MemoryRecord:  # a writer that must never be exposed read-only
        raise AssertionError("read-only server must not register a write tool")

    server = build_server(_gateway(), SCOPE, remember_writer=write, read_only=True)
    async with Client(server) as client:
        tools = {tool.name for tool in await client.list_tools()}
    assert "recall" in tools  # reads stay
    assert "record_usage" not in tools  # the Tier-1 write is suppressed
    assert "remember" not in tools  # no writer is wired in read-only


async def test_recent_tool_lists_newest_first() -> None:
    from fastmcp import Client
    from thalamus.retrieval import render_recent, select_recent

    older = MemoryRecord(MemoryId("old"), Hemisphere.EXPERIENTIAL, "decision", "older note",
                         SCOPE, datetime(2026, 5, 1, tzinfo=UTC))
    newer = MemoryRecord(MemoryId("new"), Hemisphere.EXPERIENTIAL, "decision", "newer note",
                         SCOPE, datetime(2026, 5, 28, tzinfo=UTC))
    records = [older, newer]

    def reader(limit: int, kind: str | None) -> str:
        return render_recent(select_recent(records, limit=limit, kinds=(kind,) if kind else None))

    server = build_server(_gateway(), SCOPE, recent_reader=reader)
    async with Client(server) as client:
        assert "recent" in {t.name for t in await client.list_tools()}
        result = await client.call_tool("recent", {"limit": 5})
        text = result.data if isinstance(result.data, str) else result.content[0].text
        assert text.index("new") < text.index("old")  # newest first


async def test_plan_tool_lists_and_calls_its_reader() -> None:
    from fastmcp import Client

    seen: list[tuple[str, int]] = []

    def reader(target: str, hops: int) -> str:
        seen.append((target, hops))
        return f"# Plan brief: {target}"

    server = build_server(_gateway(), SCOPE, plan_reader=reader)
    async with Client(server) as client:
        assert "plan" in {t.name for t in await client.list_tools()}
        result = await client.call_tool("plan", {"target": "frobnicate", "hops": 3})
        text = result.data if isinstance(result.data, str) else result.content[0].text
    assert "frobnicate" in text
    assert seen == [("frobnicate", 3)]


async def test_plan_tool_absent_without_a_reader() -> None:
    from fastmcp import Client

    server = build_server(_gateway(), SCOPE)
    async with Client(server) as client:
        assert "plan" not in {t.name for t in await client.list_tools()}


async def test_plan_tool_stays_in_read_only_mode() -> None:
    """Plan is read-only against the brain (no Tier-1 signal), so investigate mode keeps it."""
    from fastmcp import Client

    server = build_server(_gateway(), SCOPE, plan_reader=lambda t, h: "brief", read_only=True)
    async with Client(server) as client:
        assert "plan" in {t.name for t in await client.list_tools()}


async def test_remember_tool_is_exposed_only_with_writer_and_calls_it() -> None:
    from fastmcp import Client

    calls: list[tuple[str, str, str | None, Sequence[str], float, str | None, str | None]] = []

    def write(
        kind: str,
        text: str,
        why: str | None,
        files: Sequence[str],
        importance: float,
        memory_id: str | None,
        supersedes: str | None,
    ) -> MemoryRecord:
        calls.append((kind, text, why, files, importance, memory_id, supersedes))
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
                "supersedes": "retained:old",
            },
        )
    assert calls == [
        (
            "constraint", "ids stay scoped", "isolation", ["pkg/store.py"],
            1.0, "scope", "retained:old",
        )
    ]
    assert "remembered retained:scope" in str(result.data)
    assert "Supersedes retained:old" in str(result.data)
    assert "after the MCP server restarts" in str(result.data)


async def test_remember_tool_accepts_a_synonym_kind_but_rejects_an_unknown_one() -> None:
    from fastmcp import Client
    from fastmcp.exceptions import ToolError

    calls: list[str] = []

    def write(
        kind: str,
        text: str,
        why: str | None,
        files: Sequence[str],
        importance: float,
        memory_id: str | None,
        supersedes: str | None,
    ) -> MemoryRecord:
        calls.append(kind)
        return MemoryRecord(
            MemoryId("retained:scope"), Hemisphere.EXPERIENTIAL, kind, text, SCOPE, NOW, {}
        )

    server = build_server(_gateway(), SCOPE, remember_writer=write)
    async with Client(server) as client:
        # An accepted synonym passes schema validation and reaches the writer (normalized later).
        await client.call_tool("remember", {"kind": "project", "text": "x"})
        # A genuinely unknown kind is rejected at the schema boundary — a clean tool error, never
        # an uncaught traceback, and the writer is never invoked.
        with pytest.raises(ToolError):
            await client.call_tool("remember", {"kind": "conversation", "text": "x"})
    assert calls == ["project"]


async def test_unexpected_tool_errors_do_not_expose_backend_details(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from fastmcp import Client
    from fastmcp.exceptions import ToolError

    secret = "bolt://neo4j:password@internal:7687"

    def fail() -> None:
        raise StoreError(
            f"Neo4j operation failed: {secret} timed out on MATCH (m:M_experiential)"
        )

    class FailingRetriever:
        def retrieve(self, cue: Cue, k: int) -> RetrievalResult:
            del cue, k
            fail()
            raise AssertionError("unreachable")

    def write(
        kind: str,
        text: str,
        why: str | None,
        files: Sequence[str],
        importance: float,
        memory_id: str | None,
        supersedes: str | None,
    ) -> MemoryRecord:
        del kind, text, why, files, importance, memory_id, supersedes
        fail()
        raise AssertionError("unreachable")

    def recent_reader(limit: int, kind: str | None) -> str:
        del limit, kind
        fail()
        raise AssertionError("unreachable")

    def plan_reader(target: str, hops: int) -> str:
        del target, hops
        fail()
        raise AssertionError("unreachable")

    def resolve_shown(event_id: EventId) -> Sequence[tuple[MemoryId, str]] | None:
        del event_id
        fail()
        raise AssertionError("unreachable")

    server = build_server(
        Gateway(FailingRetriever()),
        SCOPE,
        remember_writer=write,
        resolve_shown=resolve_shown,
        recent_reader=recent_reader,
        plan_reader=plan_reader,
    )
    calls = (
        ("recall", {"prompt": "x"}),
        ("record_usage", {"event_id": "missing", "output_text": "unused"}),
        ("recent", {}),
        ("plan", {"target": "x"}),
        ("remember", {"kind": "decision", "text": "x"}),
    )
    caplog.set_level(logging.ERROR, logger="thalamus.gateway.server")
    async with Client(server) as client:
        for tool_name, arguments in calls:
            with pytest.raises(ToolError) as caught:
                await client.call_tool(tool_name, arguments)
            message = str(caught.value)
            assert f"{tool_name} failed unexpectedly" in message
            assert secret not in message
            assert "M_experiential" not in message
            assert "Traceback" not in message
    logged = [
        record
        for record in caplog.records
        if record.message.startswith("unexpected failure in MCP tool")
    ]
    assert len(logged) == len(calls)
    assert all(record.exc_info is not None for record in logged)


async def test_expected_domain_error_remains_actionable() -> None:
    from fastmcp import Client
    from fastmcp.exceptions import ToolError

    def reader(limit: int, kind: str | None) -> str:
        del limit, kind
        raise UserFacingError("recent limit must be positive")

    server = build_server(_gateway(), SCOPE, recent_reader=reader)
    async with Client(server) as client:
        with pytest.raises(ToolError, match="recent limit must be positive"):
            await client.call_tool("recent", {})


async def test_unknown_record_usage_event_remains_actionable() -> None:
    from fastmcp import Client
    from fastmcp.exceptions import ToolError

    server = build_server(_gateway(), SCOPE)
    async with Client(server) as client:
        with pytest.raises(ToolError, match="unknown or already-recorded retrieval event: missing"):
            await client.call_tool(
                "record_usage",
                {"event_id": "missing", "output_text": "unused"},
            )
