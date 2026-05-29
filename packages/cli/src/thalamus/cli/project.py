"""Per-project configuration — a ``thalamus.toml`` so a project is declarative, not bespoke.

Onboarding a new repo to Thalamus otherwise means hand-writing the same serve/sync invocations
with different paths/ports each time. Instead, a project drops a ``thalamus.toml`` next to its
brain data and the standard commands (`serve`/`sync`/`health`) read it as their defaults — an
explicit CLI flag still wins (CLI > thalamus.toml > built-in default).

The file is flat and declarative; path-valued keys are resolved relative to the file's own
directory (so it is portable). Secrets (the Neo4j password, any HTTP token) are NEVER in the
file — they stay in the environment. Example::

    repo_id    = "dollhouse"
    code_root  = "mcp-server"              # the code corpus root (serve --repo)
    data_dir   = "."                       # where the brain's .thalamus data lives
    language   = "typescript"
    scip_index = "mcp-server.scip"
    doc_roots  = ["docs", "mcp-server/docs"]
    http_port  = 8788
    neo4j_uri  = "bolt://localhost:7688"   # password via THALAMUS_NEO4J_PASSWORD (env)
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

# Friendly TOML key -> argparse dest (serve/sync/health share these dests).
_KEY_TO_DEST: dict[str, str] = {
    "repo_id": "repo_id",
    "tenant": "tenant",
    "code_root": "repo",
    "data_dir": "data_dir",
    "language": "code_language",
    "scip_index": "scip_index",
    "doc_roots": "doc_roots",
    "http_port": "port",
    "http_host": "host",
    "encoder": "encoder",
    "dim": "dim",
    "k": "k",
    "k_hop": "k_hop",
    "resolve_calls": "resolve_calls",
    "structural_min_relevance": "structural_min_relevance",
    "dream_tick": "dream_tick",
    "dream_tick_minutes": "dream_tick_minutes",
    "checkpoint": "checkpoint",
}
# Dests whose values are paths, resolved relative to the config file's directory.
_PATH_DESTS = frozenset({"repo", "data_dir", "scip_index", "checkpoint"})
_PATH_LIST_DESTS = frozenset({"doc_roots"})
# Non-secret keys bridged to the environment (serve/sync read Neo4j from env; password stays env).
_ENV_KEYS: dict[str, str] = {
    "neo4j_uri": "THALAMUS_NEO4J_URI",
    "neo4j_user": "THALAMUS_NEO4J_USER",
}


def find_project_config(explicit: Path | None) -> Path | None:
    """The config to use: the explicit ``--config`` path, else ``./thalamus.toml`` if present."""
    if explicit is not None:
        return explicit
    candidate = Path.cwd() / "thalamus.toml"
    return candidate if candidate.is_file() else None


def load_project_config(path: Path) -> tuple[dict[str, Any], dict[str, str]]:
    """Parse a ``thalamus.toml`` into (argparse-default overrides, environment overrides).

    Path-valued keys are resolved relative to ``path``'s directory. Unknown keys are ignored
    (forward-compatible). Returns dests keyed for ``parser.set_defaults`` plus env vars to
    ``setdefault`` (so an explicit env var still wins)."""
    base = path.resolve().parent
    with path.open("rb") as fh:
        raw = tomllib.load(fh)

    arg_defaults: dict[str, Any] = {}
    env_defaults: dict[str, str] = {}
    for key, value in raw.items():
        if key in _ENV_KEYS:
            env_defaults[_ENV_KEYS[key]] = str(value)
            continue
        dest = _KEY_TO_DEST.get(key)
        if dest is None:
            continue  # unknown / not a CLI-backed key — ignore
        if dest in _PATH_DESTS:
            arg_defaults[dest] = _resolve(base, value)
        elif dest in _PATH_LIST_DESTS:
            arg_defaults[dest] = [_resolve(base, item) for item in value]
        else:
            arg_defaults[dest] = value
    return arg_defaults, env_defaults


def _resolve(base: Path, value: Any) -> Path:
    """Resolve a path value relative to the config file's directory (absolute kept as-is)."""
    p = Path(str(value))
    return p if p.is_absolute() else (base / p).resolve()
