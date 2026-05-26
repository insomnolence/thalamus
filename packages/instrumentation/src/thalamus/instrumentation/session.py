"""Session context — publish the active serve session so out-of-band capture can join it.

The proxy↔truth monitor (§13.12) joins a session's Tier-1 recalls to the Tier-2 outcome
of the work they informed, keyed by ``session_id`` (see ``SessionBoundedSegmenter``). The
recall path always knows the session — the serve process mints one — but the capture path
does not: the live pytest plugin runs in a spawned process and the git post-commit hook
runs detached, so neither inherits the actuator's environment. The serve process therefore
*publishes* its session id to a small file that both capture paths read and stamp onto the
events they emit.

This is the only deterministic way to get a session id onto out-of-band commits. Its honest
limits: one active session per repo at a time (fine for single-user dogfooding; concurrent
sessions would need a registry), and a commit landing after a session ends attributes to
that session unless a staleness guard rejects it (prefer *missing* over *wrong*, §13.16).
The store is behind a protocol so it stays swappable/turn-off-able (§14).
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from thalamus.core.types import SessionId


def mint_session_id(*, now: datetime | None = None) -> SessionId:
    """A fresh, human-legible-but-unique session id for one serve process."""
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    return SessionId(f"serve-{stamp}-{uuid.uuid4().hex[:8]}")


@dataclass(frozen=True, slots=True)
class SessionContext:
    """The active serve session, as published for out-of-band capture to read."""

    session_id: SessionId
    started_at: datetime
    last_recall_at: datetime | None = None


def serialize_session_context(ctx: SessionContext) -> dict[str, Any]:
    """Convert a :class:`SessionContext` to a JSON-serializable dict."""
    return {
        "session_id": str(ctx.session_id),
        "started_at": ctx.started_at.isoformat(),
        "last_recall_at": None if ctx.last_recall_at is None else ctx.last_recall_at.isoformat(),
    }


def deserialize_session_context(obj: Mapping[str, Any]) -> SessionContext:
    """Reconstruct a context persisted by :func:`serialize_session_context`."""
    last = obj.get("last_recall_at")
    return SessionContext(
        session_id=SessionId(str(obj["session_id"])),
        started_at=datetime.fromisoformat(str(obj["started_at"])),
        last_recall_at=None if last is None else datetime.fromisoformat(str(last)),
    )


@runtime_checkable
class SessionContextStore(Protocol):
    """Publishes / reads the active session context. Swappable (§14)."""

    def publish(self, ctx: SessionContext) -> None:
        """Make ``ctx`` the active session for readers."""
        ...

    def read(self) -> SessionContext | None:
        """The active session, or ``None`` if none is published (missing data)."""
        ...


def default_session_path(repo: Path) -> Path:
    """The conventional context-file location under a repo's local ``.thalamus`` state."""
    return repo / ".thalamus" / "session" / "current.json"


class FileSessionContextStore:
    """A :class:`SessionContextStore` backed by one JSON file, written atomically.

    ``read`` returns ``None`` for a missing or unreadable/garbage file — a serve process
    that never published, or capture running outside a session, is *missing data*, never
    an error (the same down-weight-don't-fabricate discipline as the segmenters).
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    def publish(self, ctx: SessionContext) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.parent / f"{self._path.name}.{os.getpid()}.tmp"
        tmp.write_text(json.dumps(serialize_session_context(ctx)), encoding="utf-8")
        tmp.replace(self._path)  # atomic rename on POSIX

    def read(self) -> SessionContext | None:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError:
            return None
        try:
            return deserialize_session_context(json.loads(raw))
        except (ValueError, KeyError):
            return None
