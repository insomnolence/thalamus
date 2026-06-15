"""Built-in Brain-2 producers — the concrete ``python-ast`` / ``scip`` / ``docs`` / ``text`` kinds.

Each :class:`~thalamus.cli.producer_registry.Producer` reuses the existing ingestor + file-walker
helpers (no logic duplication) and registers itself on import. Importing this module is the one
place built-ins are wired into the registry; ``brain.build_corpora_from_configs`` imports it for
that side effect, and ``project.parse_corpora`` imports it lazily for kind validation. Adding a
kind is a new ``Producer`` here (or in any module) plus a ``register_producer(...)`` call.

Imports the ingestor factories from ``brain.py`` (kept there because the flat-config path needs
them); this module is imported *lazily* by ``brain``/``project`` at call time, so ``brain`` is
fully initialised before this runs — no import cycle despite the ``from thalamus.cli.brain`` line.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from thalamus.cli.brain import _default_ingestor, _scip_ingestor
from thalamus.cli.producer_registry import (
    Producer,
    ProducerBuild,
    ProducerContext,
    register_producer,
)
from thalamus.cli.project import CorpusConfig
from thalamus.core.exceptions import ThalamusError
from thalamus.structural import (
    DEFAULT_CHUNK_CHARS,
    DEFAULT_OVERLAP_CHARS,
    DocIngestor,
    TextIngestor,
    code_files,
    glob_files,
    markdown_files,
    text_files,
)


def _scip_change_files(scip_index: Path, include: tuple[str, ...]) -> Callable[[Path], list[Path]]:
    """Change-detection enumerator for a SCIP corpus: its source files (the ``include`` globs) PLUS
    the ``.scip`` artifact itself — so a live re-derive fires on a code edit OR an externally
    rebuilt index. (The ingestor reads the ``.scip``; this only drives change detection.)"""
    source = glob_files(*include)

    def files(root: Path) -> list[Path]:
        out = list(source(root))
        if scip_index.exists():
            out.append(scip_index)
        return out

    return files


class PythonAstProducer:
    """``python-ast`` — in-process Python AST (+ jedi calls when ``resolve_calls``)."""

    kind = "python-ast"

    def validate(self, cfg: CorpusConfig) -> None:
        return None  # no kind-specific config; ``root`` is enforced by parse_corpora

    def build(self, cfg: CorpusConfig, *, ctx: ProducerContext) -> ProducerBuild:
        files = glob_files(*cfg.include) if cfg.include else code_files
        return ProducerBuild(ingestor=_default_ingestor(ctx.resolve_calls), files=files)


class ScipProducer:
    """``scip`` — consume a prebuilt ``.scip`` index (any SCIP language: TS/Rust/C++/Go/…)."""

    kind = "scip"

    def validate(self, cfg: CorpusConfig) -> None:
        if cfg.scip_index is None:
            raise ThalamusError(f"corpus {cfg.name!r}: kind='scip' requires 'scip_index'")

    def build(self, cfg: CorpusConfig, *, ctx: ProducerContext) -> ProducerBuild:
        if cfg.scip_index is None:  # defensive — validate() already enforces this
            raise ThalamusError(f"corpus {cfg.name!r}: kind='scip' requires a scip_index")
        return ProducerBuild(
            ingestor=_scip_ingestor(cfg.scip_index, root_package=cfg.root_package),
            files=_scip_change_files(cfg.scip_index, cfg.include),
        )


class DocsProducer:
    """``docs`` — Markdown by its heading structure."""

    kind = "docs"

    def validate(self, cfg: CorpusConfig) -> None:
        return None

    def build(self, cfg: CorpusConfig, *, ctx: ProducerContext) -> ProducerBuild:
        files = glob_files(*cfg.include) if cfg.include else markdown_files
        return ProducerBuild(ingestor=DocIngestor(id_namespace=cfg.name), files=files)


class TextProducer:
    """``text`` — generic headingless plain text, chunked into ``document``/``chunk`` nodes.

    ``options.chunk_chars`` / ``options.overlap_chars`` tune the chunker; both are parsed and
    range-checked in :meth:`validate` so a bad value surfaces at config-load, not deep in a build.
    The *same* file enumerator is handed to the ingestor and the corpus' change detection, so they
    never drift (the no-drift invariant)."""

    kind = "text"

    def validate(self, cfg: CorpusConfig) -> None:
        _text_chunking(cfg)

    def build(self, cfg: CorpusConfig, *, ctx: ProducerContext) -> ProducerBuild:
        chunk_chars, overlap_chars = _text_chunking(cfg)
        files = glob_files(*cfg.include) if cfg.include else text_files
        ingestor = TextIngestor(
            files=files,
            id_namespace=cfg.name,
            chunk_chars=chunk_chars,
            overlap_chars=overlap_chars,
        )
        return ProducerBuild(ingestor=ingestor, files=files)


def _text_chunking(cfg: CorpusConfig) -> tuple[int, int]:
    """Parse + range-check the text chunker options, returning ``(chunk_chars, overlap_chars)``."""
    chunk_chars = _int_option(cfg, "chunk_chars", DEFAULT_CHUNK_CHARS, minimum=50)
    overlap_chars = _int_option(cfg, "overlap_chars", DEFAULT_OVERLAP_CHARS, minimum=0)
    if overlap_chars >= chunk_chars:
        raise ThalamusError(
            f"corpus {cfg.name!r}: overlap_chars ({overlap_chars}) must be < "
            f"chunk_chars ({chunk_chars})"
        )
    return chunk_chars, overlap_chars


def _int_option(cfg: CorpusConfig, key: str, default: int, *, minimum: int) -> int:
    """Read ``cfg.options[key]`` as an int ≥ ``minimum``, defaulting when absent."""
    raw = cfg.options.get(key)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ThalamusError(
            f"corpus {cfg.name!r}: option {key!r} must be an integer, got {raw!r}"
        ) from None
    if value < minimum:
        raise ThalamusError(
            f"corpus {cfg.name!r}: option {key!r} must be >= {minimum}, got {value}"
        )
    return value


def register_builtins() -> None:
    """Register the built-in producers (runs once, on this module's single import)."""
    builtins: tuple[Producer, ...] = (
        PythonAstProducer(),
        ScipProducer(),
        DocsProducer(),
        TextProducer(),
    )
    for producer in builtins:
        register_producer(producer)


register_builtins()
