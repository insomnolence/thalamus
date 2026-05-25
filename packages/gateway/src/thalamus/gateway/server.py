"""MCP transport for the gateway (FastMCP).

A thin adapter exposing :meth:`Gateway.recall` as an MCP tool, so any MCP-capable
actuator (editor/agent) can use the brain immediately. The ``Gateway`` stays pure;
this only translates the protocol. Requires the optional ``mcp`` extra:
``pip install 'thalamus-gateway[mcp]'``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thalamus.core.exceptions import ThalamusError
from thalamus.core.types import RepoId, Scope, SessionId, TenantId
from thalamus.gateway.gateway import Gateway

if TYPE_CHECKING:
    from fastmcp import FastMCP


def build_server(gateway: Gateway, *, name: str = "thalamus") -> FastMCP:
    """Build a FastMCP server exposing the gateway's ``recall`` tool."""
    try:
        from fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise ThalamusError(
            "MCP support requires the 'mcp' extra: pip install 'thalamus-gateway[mcp]'"
        ) from exc

    server = FastMCP(name)

    @server.tool
    def recall(
        prompt: str,
        tenant: str,
        repo: str,
        focus: str | None = None,
        session_id: str | None = None,
    ) -> str:
        """Recall relevant memory for a prompt; returns an assembled context block."""
        payload = gateway.recall(
            prompt=prompt,
            scope=Scope(tenant_id=TenantId(tenant), repo_id=RepoId(repo)),
            focus=focus,
            session_id=SessionId(session_id) if session_id is not None else None,
        )
        return payload.render()

    return server
