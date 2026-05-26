from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from thalamus.core.types import SessionId
from thalamus.instrumentation import (
    FileSessionContextStore,
    SessionContext,
    default_session_path,
    deserialize_session_context,
    mint_session_id,
    serialize_session_context,
)


def test_publish_then_read_round_trips(tmp_path: Path) -> None:
    store = FileSessionContextStore(tmp_path / "current.json")
    ctx = SessionContext(SessionId("serve-x"), datetime(2026, 5, 26, 12, 0, tzinfo=UTC))
    store.publish(ctx)
    assert store.read() == ctx


def test_publish_overwrites_previous_session(tmp_path: Path) -> None:
    store = FileSessionContextStore(tmp_path / "current.json")
    store.publish(SessionContext(SessionId("old"), datetime(2026, 5, 26, tzinfo=UTC)))
    store.publish(SessionContext(SessionId("new"), datetime(2026, 5, 26, 13, tzinfo=UTC)))
    read = store.read()
    assert read is not None and read.session_id == SessionId("new")


def test_read_missing_file_is_none(tmp_path: Path) -> None:
    assert FileSessionContextStore(tmp_path / "nope.json").read() is None


def test_read_garbage_is_none(tmp_path: Path) -> None:
    path = tmp_path / "current.json"
    path.write_text("{ not valid json", encoding="utf-8")
    assert FileSessionContextStore(path).read() is None


def test_serialize_round_trips_with_last_recall() -> None:
    ctx = SessionContext(
        SessionId("s"),
        datetime(2026, 5, 26, tzinfo=UTC),
        last_recall_at=datetime(2026, 5, 26, 12, tzinfo=UTC),
    )
    assert deserialize_session_context(serialize_session_context(ctx)) == ctx


def test_mint_session_id_is_unique() -> None:
    assert mint_session_id() != mint_session_id()


def test_default_session_path_is_under_repo_thalamus(tmp_path: Path) -> None:
    assert default_session_path(tmp_path) == tmp_path / ".thalamus" / "session" / "current.json"
