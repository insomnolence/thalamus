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
from thalamus.core.taxonomy import RememberKindInput
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

# Render the most recent memories (newest first), optionally filtered by kind — the temporal
# query behind the ``recent`` tool, distinct from relevance-ranked ``recall``.
type RecentReader = Callable[[int, str | None], str]

# Render a blast-radius brief for a target (symbol/description), depth-bounded — the backend
# behind the ``plan`` tool. Read-only against the brain (no Tier-1 signal), so it stays enabled
# even in investigate mode.
type PlanReader = Callable[[str, int], str]


def resolve_session_id(
    explicit: str | None, connection_session: str | None, default: SessionId | None
) -> SessionId | None:
    """Pick the session id a recall is keyed by: an explicit caller id wins; else the
    per-connection MCP session (HTTP, many clients); else the process default (stdio, one client).
    Pure, so the precedence is unit-testable away from the transport."""
    if explicit is not None:
        return SessionId(explicit)
    if connection_session is not None:
        return SessionId(connection_session)
    return default


def _connection_session_id(ctx: object) -> str | None:
    """The MCP per-connection session id, or None if unavailable.

    ``ctx.session_id`` is the real Streamable-HTTP ``Mcp-Session-Id`` over HTTP but a *generated*
    id over stdio (and may raise), so callers read it only when per-connection keying is enabled
    (HTTP) — keying stdio by it would break the single-process out-of-band Tier-2 join."""
    try:
        sid = ctx.session_id  # type: ignore[attr-defined]
    except (RuntimeError, AttributeError):
        return None
    return str(sid) if sid else None


def build_server(
    gateway: Gateway,
    scope: Scope,
    *,
    name: str = "thalamus",
    remember_writer: RememberWriter | None = None,
    default_session_id: SessionId | None = None,
    resolve_shown: ShownResolver | None = None,
    per_connection_sessions: bool = False,
    recent_reader: RecentReader | None = None,
    plan_reader: PlanReader | None = None,
    read_only: bool = False,
) -> FastMCP:
    """Build a FastMCP server exposing the gateway's ``recall`` tool.

    ``default_session_id`` is stamped on every recall whose caller omits one, so the
    serve process can key its session without any actuator cooperation; an explicit
    caller-supplied ``session_id`` still wins (finer-grained sessions). ``resolve_shown``
    (when provided) lets ``record_usage`` survive a missing in-memory payload by rebuilding
    the shown memories from durable state — the long-running-serve durability fix.
    ``per_connection_sessions`` (HTTP, many clients) keys each recall by the caller's MCP
    connection session instead of the single process id, so concurrent agents don't collapse
    into one session; leave off for stdio (one client = the process session)."""
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
        """Search project memory BEFORE substantive work (decisions, architecture questions,
        starting a task, reorienting); returns prior decisions + why + related code/docs.
        Recall even when unsure — finding nothing still tells you there's a gap. The result ends
        with a `# retrieval_event_id:` line; pass it to record_usage if the context shaped your
        work. `focus` optionally narrows to a file/subsystem."""
        connection_session: str | None = None
        if per_connection_sessions:
            from fastmcp.server.dependencies import get_context  # active per-request context

            try:
                connection_session = _connection_session_id(get_context())
            except RuntimeError:  # no active context (shouldn't happen inside a tool call)
                connection_session = None
        payload = gateway.recall(
            prompt=prompt,
            scope=scope,
            focus=focus,
            session_id=resolve_session_id(session_id, connection_session, default_session_id),
        )
        if payload.event_id is not None:
            pending[payload.event_id] = payload
            if len(pending) > max_pending:
                pending.pop(next(iter(pending)))
        suffix = "" if payload.event_id is None else f"\n# retrieval_event_id: {payload.event_id}\n"
        return payload.render() + suffix

    # ``record_usage`` writes a Tier-1 signal, so it is suppressed in a read-only/investigate
    # serve — a connection used only to inspect a brain must not contaminate its measurement.
    if not read_only:

        @server.tool
        async def record_usage(event_id: str, output_text: str) -> str:
            """Report that a recall shaped your output — how the brain learns which memories
            help. Call after using recalled context. `event_id`: from the recall's
            `# retrieval_event_id:` line. `output_text`: what you produced (a summary is fine)."""
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

    if recent_reader is not None:

        @server.tool
        async def recent(limit: int = 10, kind: str | None = None) -> str:
            """List the most recently recorded memories, newest first (optionally one ``kind``).

            Answers "what's the latest / what did we just do" — a time-ordered view, distinct
            from the relevance-ranked ``recall`` (use ``recall`` to find what's *relevant* to a
            topic; use this to see what's *recent*)."""
            return recent_reader(limit, kind)

    if plan_reader is not None:

        @server.tool
        async def plan(target: str, hops: int = 2) -> str:
            """Blast-radius brief BEFORE changing shared/central code. Resolve TARGET (a symbol
            name or short description) to the code it names, compute what depends on it ("what
            breaks"), and gather the decisions/constraints/gotchas the brain has recorded about
            everything in scope — one fused brief that flags where its coverage is blind. Use it
            to see the cross-cutting impact a local edit view misses; `hops` bounds the radius."""
            return plan_reader(target, hops)

    if remember_writer is not None and not read_only:

        @server.tool
        async def remember(
            kind: RememberKindInput,
            text: str,
            why: str | None = None,
            files: list[str] | None = None,
            importance: float = 1.0,
            memory_id: str | None = None,
            supersedes: str | None = None,
        ) -> str:
            """Save a durable fact so future sessions don't rediscover it: after a decision,
            gotcha, correction, or finished chunk of work. `kind`:
            decision|constraint|gotcha|investigation|preference (project→decision,
            user/feedback→preference, reference→investigation are also accepted and normalized).
            `why`: the reasoning (makes it useful later). `files`: paths it's about.
            `importance`: 1 normal, 2 load-bearing, 3 project-defining. `supersedes`: id of a
            memory this replaces (kept but demoted, never dropped)."""
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
