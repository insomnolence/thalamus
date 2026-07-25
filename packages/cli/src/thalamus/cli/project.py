"""Per-project configuration — a ``thalamus.toml`` so a project is declarative, not bespoke.

Onboarding a new repo to Thalamus otherwise means hand-writing the same serve/sync invocations
with different paths/ports each time. Instead, a project drops a ``thalamus.toml`` next to its
brain data and the standard commands (`serve`/`sync`/`health`) read it as their defaults — an
explicit CLI flag still wins (CLI > thalamus.toml > built-in default).

The file is flat and declarative; path-valued keys are resolved relative to the file's own
directory (so it is portable). Secrets (the Neo4j password, any HTTP token) are NEVER in the
file — they stay in the environment. Example::

    repo_id    = "sample-app"
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

    [[corpus]]
    name = "field-notes"
    root = "notes"
    kind = "text"                       # generic headingless plain text, chunked
    include = ["*.txt"]
    options.chunk_chars = 400           # per-producer params (forward-compatible)
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from thalamus.core.exceptions import ThalamusError
from thalamus.core.trust import Trust

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
    "secret_redaction": "redact_secrets",
    "dream_tick": "dream_tick",
    "dream_tick_minutes": "dream_tick_minutes",
    "checkpoint": "checkpoint",
    "plan_memory_budget": "plan_memory_budget",
    "plan_memory_chars": "plan_memory_chars",
    "plan_why_chars": "plan_why_chars",
    "plan_node_budget": "plan_node_budget",
    "plan_cochange_commits": "plan_cochange_commits",
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
# may declare an explicit set of corpora — any mix of languages, docs, or arbitrary text — as
# [[corpus]] tables, so Brain 2 is customised per project without bespoke code. The valid ``kind``
# set is the registered producers' (``python-ast``/``scip``/``docs``/``text`` built in); adding a
# kind is a ``register_producer`` call, not an edit here. ``CORPUS_KINDS`` derives from that
# registry (see the module ``__getattr__`` below — it is lazy to keep the import graph cycle-free).


def __getattr__(name: str) -> frozenset[str]:
    """Lazy ``CORPUS_KINDS`` — the registered producer kinds.

    Computed on access (not at module load) because resolving it imports ``producers``, which
    imports ``brain``, which imports *this* module — eager evaluation would be a cycle. PEP 562
    module-level ``__getattr__`` defers it to first use, after the import graph has settled."""
    if name == "CORPUS_KINDS":
        import thalamus.cli.producers  # noqa: F401 — registers the built-in producers
        from thalamus.cli.producer_registry import producer_kinds

        return producer_kinds()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


@dataclass(frozen=True, slots=True)
class CorpusConfig:
    """One Brain-2 corpus declared in a ``thalamus.toml`` ``[[corpus]]`` table.

    ``kind`` selects a registered producer: ``python-ast`` (in-process AST + jedi), ``scip``
    (consume a prebuilt ``.scip`` — any SCIP language), ``docs`` (Markdown), or ``text`` (generic
    headingless plain text). ``root`` is the dir to ingest. ``scip_index`` (for ``scip``) is the
    artifact. ``include`` are filename globs naming the corpus' source files for change detection
    (e.g. ``["*.rs"]``, ``["*.txt"]``); empty falls back to the kind's default walker.
    ``regen_command`` (optional) is the shell command that rebuilds the artifact (e.g.
    ``scip-rust-analyzer …``), run by the re-derive pass when the source changes. ``root_package``
    optionally prefixes module ids. ``options`` are forward-compatible per-producer params (values
    coerced to ``str``) — e.g. ``chunk_chars`` for the text producer. ``trust`` (§17.4) declares the
    corpus' provenance (``operator`` default / ``derived`` / ``third-party``); a non-operator
    corpus' nodes are stamped + fenced on recall so ingested instruction-shaped text can't steer
    the actuator."""

    name: str
    root: Path
    kind: str
    scip_index: Path | None = None
    include: tuple[str, ...] = ()
    regen_command: str | None = None
    root_package: str | None = None
    options: Mapping[str, str] = field(default_factory=dict)
    trust: Trust = Trust.OPERATOR


def parse_corpora(raw: dict[str, Any], base: Path) -> list[CorpusConfig]:
    """Parse the ``[[corpus]]`` array-of-tables into validated configs (empty when absent).

    Paths (``root``/``scip_index``) resolve relative to the config file's directory. Structural
    pre-checks (table shape, ``root`` required, ``regen_command`` needs ``include`` — a serve-layer
    freshness rule) run here; the ``kind`` and any kind-specific config are then validated by the
    registered producer (``get_producer(kind).validate(cfg)``), so onboarding a new kind needs no
    edit here. Raises a clean :class:`ThalamusError` on a malformed entry rather than failing deep
    in the build."""
    # Lazy import: the registry/producers depend (transitively) on this module, so importing them
    # at call time — not module load — keeps the import graph cycle-free. Importing ``producers``
    # registers the built-in kinds so ``get_producer`` can resolve them.
    import thalamus.cli.producers  # noqa: F401 — registers the built-in producers
    from thalamus.cli.producer_registry import get_producer

    corpora: list[CorpusConfig] = []
    for i, entry in enumerate(raw.get("corpus", [])):
        if not isinstance(entry, dict):
            raise ThalamusError(f"[[corpus]] entry {i} must be a table")
        label = str(entry.get("name", i))
        kind = str(entry.get("kind", "")).strip()
        if entry.get("root") is None:
            raise ThalamusError(f"[[corpus]] '{label}': 'root' is required")
        regen = entry.get("regen_command")
        if regen and not entry.get("include"):
            raise ThalamusError(
                f"[[corpus]] '{label}': 'regen_command' needs 'include' globs so the re-derive "
                "pass can tell when the source changed and the artifact must be rebuilt"
            )
        scip = entry.get("scip_index")
        pkg = entry.get("root_package")
        raw_trust = entry.get("trust")
        try:
            trust = Trust.parse(str(raw_trust)) if raw_trust is not None else Trust.OPERATOR
        except ValueError as exc:
            raise ThalamusError(f"[[corpus]] '{label}': {exc}") from None
        cfg = CorpusConfig(
            name=str(entry.get("name") or entry["root"]),
            root=_resolve(base, entry["root"]),
            kind=kind,
            scip_index=_resolve(base, scip) if scip is not None else None,
            include=tuple(str(pat) for pat in entry.get("include", ())),
            regen_command=str(regen) if regen else None,
            root_package=str(pkg) if pkg else None,
            options=_parse_options(label, entry.get("options", {})),
            trust=trust,
        )
        # Producer owns kind validity (unknown kind → known-kinds list) + kind-specific config
        # (e.g. scip needs an index, text's chunk options parse + range-check).
        get_producer(kind).validate(cfg)
        corpora.append(cfg)
    return corpora


def _parse_options(label: str, raw: Any) -> dict[str, str]:
    """Coerce an ``options`` table to ``{str: str}`` (TOML ints/bools → str) for the producer."""
    if not isinstance(raw, dict):
        raise ThalamusError(f"[[corpus]] '{label}': 'options' must be a table")
    return {str(key): str(value) for key, value in raw.items()}
