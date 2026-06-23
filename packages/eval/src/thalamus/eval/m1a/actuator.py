"""Pluggable LLM *actuators* for the M-1a harness.

An actuator is the thing being *measured*, not the brain: it consumes an assembled
prompt (task + a context block) and emits an action. The brain has no LLM in its
path; M-1a asks whether feeding the brain's context to a fresh actuator changes
what that actuator does. So the verdict on the output is a deterministic oracle
(see :mod:`thalamus.eval.m1a.cases`), never another model — the actuator is the
subject, never the judge (the §14 firewall).

Backends are dependency-free (stdlib ``urllib`` + ``json``) so the eval stays
contained, and each takes an injectable ``transport`` so the request shape is unit
-testable without a live service. Supported: Ollama (local; the default for tests),
Anthropic (Claude), OpenAI-compatible (covers Codex and any ``OPENAI_BASE_URL``
endpoint), and Google Gemini. A :class:`FixtureActuator` drives the harness's own
tests offline.
"""

from __future__ import annotations

import json
import os
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any, Protocol, runtime_checkable

DEFAULT_TIMEOUT = 120.0

# A transport performs one POST and returns the parsed JSON response. Injectable so
# the backends are testable without network access.
Transport = Callable[[str, Mapping[str, str], dict[str, Any], float], dict[str, Any]]


class ActuatorError(RuntimeError):
    """Raised when an actuator cannot produce an action (transport or parse failure)."""


def http_post(
    url: str, headers: Mapping[str, str], body: dict[str, Any], timeout: float
) -> dict[str, Any]:
    """The default transport: a single JSON POST via stdlib ``urllib``."""
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")  # noqa: S310 (trusted URL)
    request.add_header("Content-Type", "application/json")
    for key, value in headers.items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            payload = response.read().decode("utf-8")
    except OSError as exc:  # URLError, TimeoutError, ConnectionReset — all subclass OSError
        raise ActuatorError(f"actuator request to {url} failed: {exc}") from exc
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ActuatorError(f"actuator response was not JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ActuatorError("actuator response was not a JSON object")
    return parsed


@runtime_checkable
class Actuator(Protocol):
    """Runs one assembled prompt and returns the action text."""

    @property
    def name(self) -> str: ...

    def act(self, prompt: str, *, temperature: float = 0.7, seed: int | None = None) -> str: ...


def _require(value: str | None, what: str) -> str:
    if not value:
        raise ActuatorError(f"missing {what}")
    return value


class OllamaActuator:
    """Local Ollama backend (the default for testing). Honors ``seed`` for reproducibility."""

    def __init__(
        self,
        model: str,
        *,
        base_url: str = "http://localhost:11434",
        timeout: float = DEFAULT_TIMEOUT,
        transport: Transport = http_post,
    ) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._transport = transport

    @property
    def name(self) -> str:
        return f"ollama:{self._model}"

    def act(self, prompt: str, *, temperature: float = 0.7, seed: int | None = None) -> str:
        options: dict[str, Any] = {"temperature": temperature}
        if seed is not None:
            options["seed"] = seed
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": options,
        }
        data = self._transport(f"{self._base_url}/api/chat", {}, body, self._timeout)
        try:
            return str(data["message"]["content"])
        except (KeyError, TypeError) as exc:
            raise ActuatorError(f"unexpected Ollama response shape: {exc}") from exc


class AnthropicActuator:
    """Anthropic (Claude) Messages API. ``ANTHROPIC_API_KEY`` or an explicit key."""

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        base_url: str = "https://api.anthropic.com",
        max_tokens: int = 2048,
        timeout: float = DEFAULT_TIMEOUT,
        transport: Transport = http_post,
    ) -> None:
        self._model = model
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._base_url = base_url.rstrip("/")
        self._max_tokens = max_tokens
        self._timeout = timeout
        self._transport = transport

    @property
    def name(self) -> str:
        return f"claude:{self._model}"

    def act(self, prompt: str, *, temperature: float = 0.7, seed: int | None = None) -> str:
        headers = {
            "x-api-key": _require(self._api_key, "ANTHROPIC_API_KEY"),
            "anthropic-version": "2023-06-01",
        }
        body: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        data = self._transport(f"{self._base_url}/v1/messages", headers, body, self._timeout)
        try:
            return str(data["content"][0]["text"])
        except (KeyError, IndexError, TypeError) as exc:
            raise ActuatorError(f"unexpected Anthropic response shape: {exc}") from exc


class OpenAIActuator:
    """OpenAI-compatible Chat Completions — covers OpenAI, Codex, and any compatible
    endpoint via ``OPENAI_BASE_URL`` (e.g. a local server). ``OPENAI_API_KEY`` or a key."""

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        transport: Transport = http_post,
    ) -> None:
        self._model = model
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self._base_url = (base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        self._timeout = timeout
        self._transport = transport

    @property
    def name(self) -> str:
        return f"openai:{self._model}"

    def act(self, prompt: str, *, temperature: float = 0.7, seed: int | None = None) -> str:
        headers = {"Authorization": f"Bearer {_require(self._api_key, 'OPENAI_API_KEY')}"}
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
        }
        if seed is not None:
            body["seed"] = seed
        data = self._transport(f"{self._base_url}/chat/completions", headers, body, self._timeout)
        try:
            return str(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise ActuatorError(f"unexpected OpenAI response shape: {exc}") from exc


class GeminiActuator:
    """Google Gemini (Generative Language API). ``GEMINI_API_KEY`` or an explicit key."""

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        timeout: float = DEFAULT_TIMEOUT,
        transport: Transport = http_post,
    ) -> None:
        self._model = model
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY")
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._transport = transport

    @property
    def name(self) -> str:
        return f"gemini:{self._model}"

    def act(self, prompt: str, *, temperature: float = 0.7, seed: int | None = None) -> str:
        key = _require(self._api_key, "GEMINI_API_KEY")
        url = f"{self._base_url}/models/{self._model}:generateContent?key={key}"
        body: dict[str, Any] = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature},
        }
        data = self._transport(url, {}, body, self._timeout)
        try:
            return str(data["candidates"][0]["content"]["parts"][0]["text"])
        except (KeyError, IndexError, TypeError) as exc:
            raise ActuatorError(f"unexpected Gemini response shape: {exc}") from exc


class FixtureActuator:
    """A deterministic, offline actuator for tests: ``responder(prompt) -> output``."""

    def __init__(self, responder: Callable[[str], str], *, name: str = "fixture") -> None:
        self._responder = responder
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def act(self, prompt: str, *, temperature: float = 0.7, seed: int | None = None) -> str:
        return self._responder(prompt)


# name -> (class, extra notes) for the CLI/factory. "codex" aliases the OpenAI backend.
_BACKENDS: dict[str, Callable[..., Actuator]] = {
    "ollama": OllamaActuator,
    "claude": AnthropicActuator,
    "anthropic": AnthropicActuator,
    "openai": OpenAIActuator,
    "codex": OpenAIActuator,
    "gemini": GeminiActuator,
}

BACKEND_NAMES = tuple(_BACKENDS)


def build_actuator(backend: str, model: str, **options: Any) -> Actuator:
    """Construct an actuator by backend name — the single seam the CLI uses.

    ``backend`` ∈ {ollama, claude/anthropic, openai/codex, gemini}. ``options`` are
    forwarded to the backend (``base_url``, ``api_key``, ``timeout``, …).
    """
    try:
        factory = _BACKENDS[backend]
    except KeyError:
        raise ActuatorError(
            f"unknown actuator backend {backend!r} (choices: {', '.join(BACKEND_NAMES)})"
        ) from None
    return factory(model, **options)
