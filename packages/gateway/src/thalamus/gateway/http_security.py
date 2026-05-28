"""HTTP security for the Streamable-HTTP transport — cert-free.

The standalone ``fastmcp`` builds the streamable-HTTP app *without* MCP's transport
security, so DNS-rebinding/Origin protection is off by default — this adds it. Two
layers, no TLS (encryption, if wanted, is an ops concern: a reverse proxy or a mesh
VPN like Tailscale in front of this plain-HTTP server):

1. **Origin validation** (the MCP MUST): reject requests whose ``Origin`` header is not
   allow-listed. Browsers always send ``Origin``; non-browser MCP clients (CLI agents)
   omit it, so a missing Origin is allowed — the token/bind layers govern those. localhost
   origins are always allowed (the dev's own browser).
2. **Optional bearer token**: when configured, require ``Authorization: Bearer <token>``.
   Sniffable without TLS, so it stops casual/accidental LAN access, not a network attacker.

The decision logic is pure (``origin_allowed``) and the middleware is plain ASGI, so both
are testable without sockets; ``build_security_middleware`` lazily wraps it as a Starlette
``Middleware`` (the only part that needs the optional HTTP stack).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def origin_allowed(origin: str, allowed: frozenset[str]) -> bool:
    """True if ``origin`` may talk to the server: explicitly allow-listed, or any localhost
    origin (the dev's own browser, any port)."""
    if origin in allowed:
        return True
    return urlsplit(origin).hostname in _LOCAL_HOSTS


class SecurityMiddleware:
    """Plain-ASGI gate: validate Origin (always) and bearer token (when configured)."""

    def __init__(
        self,
        app: Callable[..., Awaitable[None]],
        *,
        allowed_origins: frozenset[str],
        token: str | None,
    ) -> None:
        self._app = app
        self._allowed_origins = allowed_origins
        self._token = token

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":  # lifespan/websocket pass straight through
            await self._app(scope, receive, send)
            return
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        origin = headers.get(b"origin")
        if origin is not None and not origin_allowed(
            origin.decode("latin-1"), self._allowed_origins
        ):
            await _reject(send, 403, b"origin not allowed")
            return
        if self._token is not None:
            auth = headers.get(b"authorization", b"").decode("latin-1")
            if auth != f"Bearer {self._token}":
                await _reject(send, 401, b"unauthorized")
                return
        await self._app(scope, receive, send)


async def _reject(send: Any, status: int, body: bytes) -> None:
    await send(
        {"type": "http.response.start", "status": status,
         "headers": [(b"content-type", b"text/plain")]}
    )
    await send({"type": "http.response.body", "body": body})


def build_security_middleware(*, allowed_origins: frozenset[str], token: str | None) -> Any:
    """Wrap :class:`SecurityMiddleware` as a Starlette ``Middleware`` for FastMCP's http app."""
    from starlette.middleware import Middleware  # lazy: only the HTTP transport needs it

    return Middleware(SecurityMiddleware, allowed_origins=allowed_origins, token=token)
