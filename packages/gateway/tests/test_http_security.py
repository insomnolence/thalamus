"""Cert-free HTTP security: Origin allow-listing + optional bearer token, as plain ASGI."""

from __future__ import annotations

from typing import Any

from thalamus.gateway.http_security import SecurityMiddleware, origin_allowed


def test_origin_allowed_localhost_any_port() -> None:
    empty: frozenset[str] = frozenset()
    assert origin_allowed("http://localhost:3000", empty)
    assert origin_allowed("http://127.0.0.1:9999", empty)
    assert origin_allowed("http://[::1]:8080", empty)


def test_origin_allowed_explicit_allow_list() -> None:
    allowed = frozenset({"https://app.example.com"})
    assert origin_allowed("https://app.example.com", allowed)
    assert not origin_allowed("https://evil.example.com", allowed)


class _App:
    def __init__(self) -> None:
        self.called = False

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        self.called = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


async def _drive(mw: SecurityMiddleware, headers: list[tuple[bytes, bytes]]) -> int:
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request"}

    async def send(msg: dict[str, Any]) -> None:
        sent.append(msg)

    await mw({"type": "http", "headers": headers}, receive, send)
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


async def test_passes_through_with_no_origin_and_no_token() -> None:
    app = _App()
    mw = SecurityMiddleware(app, allowed_origins=frozenset(), token=None)
    assert await _drive(mw, []) == 200
    assert app.called


async def test_rejects_disallowed_origin() -> None:
    app = _App()
    mw = SecurityMiddleware(app, allowed_origins=frozenset(), token=None)
    status = await _drive(mw, [(b"origin", b"https://evil.example.com")])
    assert status == 403
    assert not app.called


async def test_allows_localhost_origin() -> None:
    app = _App()
    mw = SecurityMiddleware(app, allowed_origins=frozenset(), token=None)
    assert await _drive(mw, [(b"origin", b"http://localhost:5173")]) == 200
    assert app.called


async def test_requires_bearer_token_when_configured() -> None:
    app = _App()
    mw = SecurityMiddleware(app, allowed_origins=frozenset(), token="s3cret")
    assert await _drive(mw, []) == 401  # missing
    assert await _drive(mw, [(b"authorization", b"Bearer wrong")]) == 401  # wrong
    assert not app.called
    assert await _drive(mw, [(b"authorization", b"Bearer s3cret")]) == 200  # right
    assert app.called


async def test_lifespan_scope_passes_through() -> None:
    app = _App()
    mw = SecurityMiddleware(app, allowed_origins=frozenset(), token="s3cret")
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "lifespan.startup"}

    async def send(msg: dict[str, Any]) -> None:
        sent.append(msg)

    await mw({"type": "lifespan"}, receive, send)
    assert app.called  # security gate only applies to http requests
