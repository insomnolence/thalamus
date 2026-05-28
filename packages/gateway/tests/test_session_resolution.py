"""Session-id precedence for recall: explicit > per-connection > process default."""

from __future__ import annotations

from thalamus.core.types import SessionId
from thalamus.gateway.server import _connection_session_id, resolve_session_id


def test_explicit_caller_id_wins() -> None:
    assert resolve_session_id("explicit", "conn", SessionId("proc")) == SessionId("explicit")


def test_connection_used_when_no_explicit() -> None:
    assert resolve_session_id(None, "conn", SessionId("proc")) == SessionId("conn")


def test_falls_back_to_process_default() -> None:
    assert resolve_session_id(None, None, SessionId("proc")) == SessionId("proc")


def test_none_when_nothing_available() -> None:
    assert resolve_session_id(None, None, None) is None


class _Ctx:
    def __init__(self, value: object) -> None:
        self._value = value

    @property
    def session_id(self) -> object:
        if isinstance(self._value, Exception):
            raise self._value
        return self._value


def test_connection_session_id_reads_value() -> None:
    assert _connection_session_id(_Ctx("sess-abc")) == "sess-abc"


def test_connection_session_id_none_on_empty_or_error() -> None:
    assert _connection_session_id(_Ctx("")) is None
    assert _connection_session_id(_Ctx(RuntimeError("no session"))) is None
    assert _connection_session_id(object()) is None  # no session_id attribute at all
