from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from thalamus.eval.m1a import (
    ActuatorError,
    AnthropicActuator,
    FixtureActuator,
    GeminiActuator,
    OllamaActuator,
    OpenAIActuator,
    build_actuator,
)


def _capturing(response: dict[str, Any]) -> tuple[Any, list[dict[str, Any]]]:
    calls: list[dict[str, Any]] = []

    def transport(
        url: str, headers: Mapping[str, str], body: dict[str, Any], timeout: float
    ) -> dict[str, Any]:
        calls.append({"url": url, "headers": dict(headers), "body": body})
        return response

    return transport, calls


def test_fixture_actuator_echoes() -> None:
    actuator = FixtureActuator(lambda prompt: f"echo:{prompt}")
    assert actuator.act("hello") == "echo:hello"
    assert actuator.name == "fixture"


def test_build_actuator_unknown_backend_raises() -> None:
    with pytest.raises(ActuatorError):
        build_actuator("nope", "m")


def test_build_actuator_codex_aliases_openai() -> None:
    assert isinstance(build_actuator("codex", "gpt-x", api_key="k"), OpenAIActuator)


def test_ollama_request_shape_and_parse() -> None:
    transport, calls = _capturing({"message": {"content": "answer"}})
    actuator = OllamaActuator("qwen", base_url="http://h:11434/", transport=transport)
    assert actuator.act("do it", temperature=0.3, seed=2) == "answer"
    assert calls[0]["url"] == "http://h:11434/api/chat"
    assert calls[0]["body"]["model"] == "qwen"
    assert calls[0]["body"]["options"] == {"temperature": 0.3, "seed": 2}
    assert calls[0]["body"]["messages"][0]["content"] == "do it"


def test_anthropic_request_shape_and_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    transport, calls = _capturing({"content": [{"text": "claude-out"}]})
    actuator = AnthropicActuator("claude-x", api_key="sk", transport=transport)
    assert actuator.act("task") == "claude-out"
    assert calls[0]["url"].endswith("/v1/messages")
    assert calls[0]["headers"]["x-api-key"] == "sk"
    assert "anthropic-version" in calls[0]["headers"]


def test_anthropic_missing_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    actuator = AnthropicActuator("claude-x", api_key=None, transport=lambda *_: {})
    with pytest.raises(ActuatorError):
        actuator.act("task")


def test_openai_request_shape_and_parse() -> None:
    transport, calls = _capturing({"choices": [{"message": {"content": "oai"}}]})
    actuator = OpenAIActuator("gpt", api_key="k", base_url="https://api.x/v1", transport=transport)
    assert actuator.act("t", seed=5) == "oai"
    assert calls[0]["url"] == "https://api.x/v1/chat/completions"
    assert calls[0]["headers"]["Authorization"] == "Bearer k"
    assert calls[0]["body"]["seed"] == 5


def test_gemini_request_shape_and_parse() -> None:
    transport, calls = _capturing({"candidates": [{"content": {"parts": [{"text": "gem"}]}}]})
    actuator = GeminiActuator("gemini-x", api_key="gk", transport=transport)
    assert actuator.act("t") == "gem"
    assert ":generateContent?key=gk" in calls[0]["url"]


def test_unexpected_response_shape_raises() -> None:
    transport, _ = _capturing({"wrong": "shape"})
    actuator = OllamaActuator("m", transport=transport)
    with pytest.raises(ActuatorError):
        actuator.act("x")


def test_http_post_wraps_socket_timeout_as_actuator_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # A socket read timeout raises a bare TimeoutError (not URLError); it must still surface as an
    # ActuatorError so the harness's retry/conservative-miss path can catch it.
    import urllib.request

    from thalamus.eval.m1a.actuator import http_post

    def boom(*_a: object, **_k: object) -> object:
        raise TimeoutError("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(ActuatorError):
        http_post("http://x", {}, {"a": 1}, 1.0)
