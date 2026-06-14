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

For Brain 2 beyond a single code language, declare an explicit set of corpora as ``[[corpus]]``
tables (any mix of languages, or docs only) — so the brain is customised per project, not bespoke
to Python/TS. Any language with a SCIP indexer is ``kind = "scip"``; ``regen_command`` (optional)
lets the live re-derive pass rebuild that corpus' index when its source changes::

    [[corpus]]
    name = "rust-core"
    root = "crates"
    kind = "scip"                       # any SCIP language: Rust, C++, Go, TS, …
    scip_index    = "rust-core.scip"
    include       = ["*.rs"]            # source globs (change detection + regen gating)
    regen_command = "rust-analyzer scip . --output rust-core.scip"

    [[corpus]]
    name = "design-docs"
    root = "docs"
    kind = "docs"                       # Markdown; 'python-ast' for in-process Python parsing
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from thalamus.core.exceptions import ThalamusError

# Friendly TOML key -> argparse dest (serve/sync/health share these dests).
_KEY_TO_DEST: dict[str, str] = {
    "repo_id": "repo_id",
    "tenant": "tenant",
    "code_root": "repo",
    "data_dir": "data_dir",
    "language": "code_language",
    "scip_index": "scip_index",
    "doc_roots": "doc_roots",
    "transport": "transport",
    "http_port": "port",
    "http_host": "host",
    "encoder": "encoder",
    "dim": "dim",
    "k": "k",
    "k_hop": "k_hop",
    "resolve_calls": "resolve_calls",
    "structural_min_relevance": "structural_min_relevance",
    "hybrid_retrieval": "hybrid_retrieval",
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


def load_project_config(
    path: Path,
) -> tuple[dict[str, Any], dict[str, str], list[CorpusConfig]]:
    """Parse a ``thalamus.toml`` into (argparse-default overrides, environment overrides, corpora).

    Path-valued keys are resolved relative to ``path``'s directory. Unknown keys are ignored
    (forward-compatible). Returns dests keyed for ``parser.set_defaults``, env vars to
    ``setdefault`` (so an explicit env var still wins), and the declarative ``[[corpus]]`` set
    (empty when absent — the flat ``code_root``/``language``/``doc_roots`` keys then drive it)."""
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
    return arg_defaults, env_defaults, parse_corpora(raw, base)


def _resolve(base: Path, value: Any) -> Path:
    """Resolve a path value relative to the config file's directory (absolute kept as-is)."""
    p = Path(str(value))
    return p if p.is_absolute() else (base / p).resolve()


# ── Declarative Brain-2 corpora ──────────────────────────────────────────────────────────────
# Beyond the flat code_root/language/scip_index/doc_roots keys (one code corpus + docs), a project
# may declare an explicit set of corpora — any mix of languages or docs — as [[corpus]] tables, so
# Brain 2 is customised per project without bespoke code. Any language with a SCIP indexer
# (TS/Rust/C++/Go/…) is a 'scip' corpus; Python is 'python-ast'; Markdown is 'docs'.

CORPUS_KINDS = frozenset({"python-ast", "scip", "docs"})


@dataclass(frozen=True, slots=True)
class CorpusConfig:
    """One Brain-2 corpus declared in a ``thalamus.toml`` ``[[corpus]]`` table.

    ``kind`` is ``python-ast`` (in-process AST + jedi), ``scip`` (consume a prebuilt ``.scip`` —
    any SCIP language), or ``docs`` (Markdown). ``root`` is the dir to ingest. ``scip_index`` (for
    ``scip``) is the artifact. ``include`` are filename globs naming the corpus' source files for
    change detection (e.g. ``["*.rs"]``); empty falls back to the kind's default walker.
    ``regen_command`` (optional) is the shell command that rebuilds the artifact (e.g.
    ``scip-rust-analyzer …``), run by the re-derive pass when the source changes. ``root_package``
    optionally prefixes module ids."""

    name: str
    root: Path
    kind: str
    scip_index: Path | None = None
    include: tuple[str, ...] = ()
    regen_command: str | None = None
    root_package: str | None = None


def parse_corpora(raw: dict[str, Any], base: Path) -> list[CorpusConfig]:
    """Parse the ``[[corpus]]`` array-of-tables into validated configs (empty when absent).

    Paths (``root``/``scip_index``) resolve relative to the config file's directory. Raises a
    clean :class:`ThalamusError` on a malformed entry (unknown kind, missing root, scip without
    an index) rather than failing deep in the build."""
    corpora: list[CorpusConfig] = []
    for i, entry in enumerate(raw.get("corpus", [])):
        if not isinstance(entry, dict):
            raise ThalamusError(f"[[corpus]] entry {i} must be a table")
        label = str(entry.get("name", i))
        kind = str(entry.get("kind", "")).strip()
        if kind not in CORPUS_KINDS:
            raise ThalamusError(
                f"[[corpus]] '{label}': kind must be one of {sorted(CORPUS_KINDS)}, got {kind!r}"
            )
        if entry.get("root") is None:
            raise ThalamusError(f"[[corpus]] '{label}': 'root' is required")
        scip = entry.get("scip_index")
        if kind == "scip" and scip is None:
            raise ThalamusError(f"[[corpus]] '{label}': kind='scip' requires 'scip_index'")
        regen = entry.get("regen_command")
        if regen and not entry.get("include"):
            raise ThalamusError(
                f"[[corpus]] '{label}': 'regen_command' needs 'include' globs so the re-derive "
                "pass can tell when the source changed and the artifact must be rebuilt"
            )
        pkg = entry.get("root_package")
        corpora.append(
            CorpusConfig(
                name=str(entry.get("name") or entry["root"]),
                root=_resolve(base, entry["root"]),
                kind=kind,
                scip_index=_resolve(base, scip) if scip is not None else None,
                include=tuple(str(pat) for pat in entry.get("include", ())),
                regen_command=str(regen) if regen else None,
                root_package=str(pkg) if pkg else None,
            )
        )
    return corpora
