"""MCP transport for the gateway (FastMCP).

A thin adapter exposing :meth:`Gateway.recall` as an MCP tool, so any MCP-capable
actuator (editor/agent) can use the brain immediately. The ``Gateway`` stays pure;
this only translates the protocol. Requires the optional ``mcp`` extra:
``pip install 'thalamus-gateway[mcp]'``.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import threading
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

async def _run_sync[T](call: Callable[[], T]) -> T:
    """Run blocking gateway work without tying it to an executor's process lifetime.

    AnyIO and asyncio worker pools leave a waiting non-daemon worker behind on supported Python
    3.14 runtimes, which makes an otherwise completed in-process MCP request hang indefinitely at
    teardown. A request-scoped worker exits as soon as its one call completes, preserves the
    caller's context variables, and still keeps blocking store/encoder work off the event loop.
    """
    completed: concurrent.futures.Future[T] = concurrent.futures.Future()
    context = contextvars.copy_context()

    def worker() -> None:
        try:
            completed.set_result(context.run(call))
        except BaseException as error:
            completed.set_exception(error)

    threading.Thread(target=worker, name="thalamus-mcp-worker", daemon=True).start()
    # Poll a thread-safe future instead of relying on call_soon_threadsafe's selector wakeup.
    # Python 3.14 can lose that wakeup after append-only log I/O, leaving a completed request
    # asleep forever; the short timer also gives cancellation a regular checkpoint.
    while not completed.done():
        await asyncio.sleep(0.01)
    return completed.result()


def resolve_session_id(
    explicit: str | None, connection_session: str | None, default: SessionId | None
) -> SessionId | None:
    """Pick the session id a recall is keyed by: an explicit caller id wins; else the
    per-connection MCP session (HTTP, many clients); else the process default (stdio, one client).
    Pure, so the precedence is unit-testable away from the transport."""
    if explicit is not None and explicit.strip():
        return SessionId(explicit)
    if connection_session is not None and connection_session.strip():
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

    # A tiny liveness route. The HTTP serve registers only the MCP endpoint (/mcp), so every other
    # path 404s — a health probe or LAN scanner hitting GET /health is the most common source of
    # that 404 log noise. Return 200 with no brain state and no auth (pure liveness), so the common
    # legitimate probe stops 404ing. Only mounted for the HTTP transport; harmless under stdio.
    from starlette.requests import Request
    from starlette.responses import JSONResponse, Response

    @server.custom_route("/health", methods=["GET"])
    async def health(_request: Request) -> Response:
        return JSONResponse({"status": "ok"})

    pending: dict[EventId, ContextPayload] = {}
    pending_lock = threading.Lock()
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
        target_session = resolve_session_id(session_id, connection_session, default_session_id)
        payload = await _run_sync(
            lambda: gateway.recall(
                prompt=prompt,
                scope=scope,
                focus=focus,
                session_id=target_session,
            )
        )
        if payload.event_id is not None:
            with pending_lock:
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
            with pending_lock:
                payload = pending.pop(key, None)
            if payload is not None:  # fast path: the live payload is still cached
                signals = await _run_sync(
                    lambda: gateway.record_outcome(payload, output_text)
                )
                return f"recorded {len(signals)} usage signal(s)"
            # Fallback: the cached payload is gone (serve restarted, or another worker served the
            # recall). Rebuild the shown memories from durable state so the signal isn't lost.
            if resolve_shown is not None:
                shown = await _run_sync(lambda: resolve_shown(key))
                if shown is not None:
                    signals = await _run_sync(
                        lambda: gateway.record_outcome_for(key, shown, output_text)
                    )
                    return f"recorded {len(signals)} usage signal(s)"
            raise ValueError(f"unknown or already-recorded retrieval event: {event_id}")

    if recent_reader is not None:

        @server.tool
        async def recent(limit: int = 10, kind: str | None = None) -> str:
            """List the most recently recorded memories, newest first (optionally one ``kind``).

            Answers "what's the latest / what did we just do" — a time-ordered view, distinct
            from the relevance-ranked ``recall`` (use ``recall`` to find what's *relevant* to a
            topic; use this to see what's *recent*)."""
            return await _run_sync(lambda: recent_reader(limit, kind))

    if plan_reader is not None:

        @server.tool
        async def plan(target: str, hops: int = 2) -> str:
            """Blast-radius brief BEFORE changing shared/central code. Resolve TARGET (a symbol
            name or short description) to the code it names, compute what depends on it ("what
            breaks"), and gather the decisions/constraints/gotchas the brain has recorded about
            everything in scope — one fused brief that flags where its coverage is blind. Use it
            to see the cross-cutting impact a local edit view misses; `hops` bounds the radius."""
            return await _run_sync(lambda: plan_reader(target, hops))

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
            record = await _run_sync(
                lambda: remember_writer(
                    kind, text, why, files or (), importance, memory_id, supersedes
                )
            )
            links_note = (
                " Related-file structural links are applied after the MCP server restarts."
                if files
                else ""
            )
            supersedes_note = f" Supersedes {supersedes}." if supersedes else ""
            return f"remembered {record.memory_id} ({record.kind}).{supersedes_note}{links_note}"

    return server
