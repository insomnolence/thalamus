"""MCP transport for the gateway (FastMCP).

A thin adapter exposing :meth:`Gateway.recall` as an MCP tool, so any MCP-capable
actuator (editor/agent) can use the brain immediately. The ``Gateway`` stays pure;
this only translates the protocol. Requires the optional ``mcp`` extra:
``pip install 'thalamus-gateway[mcp]'``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from thalamus.core.exceptions import ThalamusError
from thalamus.core.types import EventId, MemoryId, MemoryRecord, Scope, SessionId
from thalamus.gateway.gateway import Gateway
from thalamus.gateway.payload import ContextPayload

if TYPE_CHECKING:
    from fastmcp import FastMCP

type RememberWriter = Callable[
    [str, str, str | None, Sequence[str], float, str | None, str | None],
    MemoryRecord,
]

# Reconstruct a recall's shown ``(memory_id, content)`` pairs for an ``event_id`` from durable
# state (retrieval-event log + store), or ``None`` if the event isn't found. The record_usage
# fallback when the in-memory payload cache misses (serve restart / another worker).
type ShownResolver = Callable[[EventId], Sequence[tuple[MemoryId, str]] | None]


def build_server(
    gateway: Gateway,
    scope: Scope,
    *,
    name: str = "thalamus",
    remember_writer: RememberWriter | None = None,
    default_session_id: SessionId | None = None,
    resolve_shown: ShownResolver | None = None,
) -> FastMCP:
    """Build a FastMCP server exposing the gateway's ``recall`` tool.

    ``default_session_id`` is stamped on every recall whose caller omits one, so the
    serve process can key its session without any actuator cooperation; an explicit
    caller-supplied ``session_id`` still wins (finer-grained sessions). ``resolve_shown``
    (when provided) lets ``record_usage`` survive a missing in-memory payload by rebuilding
    the shown memories from durable state — the long-running-serve durability fix."""
    try:
        from fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise ThalamusError(
            "MCP support requires the 'mcp' extra: pip install 'thalamus-gateway[mcp]'"
        ) from exc

    server = FastMCP(name)
    pending: dict[EventId, ContextPayload] = {}
    max_pending = 1000

    @server.tool
    async def recall(
        prompt: str,
        focus: str | None = None,
        session_id: str | None = None,
    ) -> str:
        """Recall relevant memory for a prompt; returns an assembled context block."""
        payload = gateway.recall(
            prompt=prompt,
            scope=scope,
            focus=focus,
            session_id=SessionId(session_id) if session_id is not None else default_session_id,
        )
        if payload.event_id is not None:
            pending[payload.event_id] = payload
            if len(pending) > max_pending:
                pending.pop(next(iter(pending)))
        suffix = "" if payload.event_id is None else f"\n# retrieval_event_id: {payload.event_id}\n"
        return payload.render() + suffix

    @server.tool
    async def record_usage(event_id: str, output_text: str) -> str:
        """Record deterministic Tier-1 usage for a prior recall."""
        key = EventId(event_id)
        payload = pending.pop(key, None)
        if payload is not None:  # fast path: the live payload is still cached
            signals = gateway.record_outcome(payload, output_text)
            return f"recorded {len(signals)} usage signal(s)"
        # Fallback: the cached payload is gone (serve restarted, or another worker served the
        # recall). Rebuild the shown memories from durable state so the signal isn't lost.
        if resolve_shown is not None:
            shown = resolve_shown(key)
            if shown is not None:
                signals = gateway.record_outcome_for(key, shown, output_text)
                return f"recorded {len(signals)} usage signal(s)"
        raise ValueError(f"unknown or already-recorded retrieval event: {event_id}")

    if remember_writer is not None:

        @server.tool
        async def remember(
            kind: str,
            text: str,
            why: str | None = None,
            files: list[str] | None = None,
            importance: float = 1.0,
            memory_id: str | None = None,
            supersedes: str | None = None,
        ) -> str:
            """Retain a durable repo decision, constraint, gotcha, investigation, or preference.

            Pass ``supersedes`` with a prior memory's id to mark it replaced (§13.18): the old
            belief is demoted below current truth at recall but kept, surfaced with this fact's
            why/text as the supersession reason. Never deletes the old memory."""
            record = remember_writer(
                kind, text, why, files or (), importance, memory_id, supersedes
            )
            links_note = (
                " Related-file structural links are applied after the MCP server restarts."
                if files
                else ""
            )
            supersedes_note = f" Supersedes {supersedes}." if supersedes else ""
            return f"remembered {record.memory_id} ({record.kind}).{supersedes_note}{links_note}"

    return server
