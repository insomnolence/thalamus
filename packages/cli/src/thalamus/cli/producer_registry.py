"""Producer registry — the seam that turns "add a Brain-2 corpus kind" into a registration.

A :class:`Producer` owns everything needed to turn a declarative ``[[corpus]]`` config into a
buildable corpus ingredient: which :class:`~thalamus.structural.Ingestor` parses it, which files
drive change detection, and how to validate the config. Built-in producers (``python-ast`` /
``scip`` / ``docs`` / ``text``) register on import of ``producers.py``; a *new* kind (external
findings, a tree-sitter language, …) is a single :func:`register_producer` call — not an edit to
the build dispatch. Entry-point / external-plugin auto-discovery is deliberately deferred; this
in-process registry is the v1 seam.

**Leaf module:** imports nothing from the CLI composition (``project.py`` / ``brain.py``), so the
import graph stays cycle-free — concretes in ``producers.py`` and the callers in ``project.py`` /
``brain.py`` depend on this, never the reverse. ``CorpusConfig`` is referenced only in annotations
(``TYPE_CHECKING``), so the leaf never imports ``project`` at runtime.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from thalamus.core.exceptions import ThalamusError
from thalamus.structural import Ingestor

if TYPE_CHECKING:
    from thalamus.cli.project import CorpusConfig


@dataclass(frozen=True, slots=True)
class ProducerContext:
    """Cross-cutting build inputs shared by every producer.

    Today ``resolve_calls`` (jedi call resolution for the Python AST corpus) and ``redact`` (the
    §17.4 secret scrubber, on by default — passed to the doc/text ingestors that embed free text).
    The corpus' vector index is *not* here — it stays the caller's concern (the index_factory in
    ``build_corpora_from_configs`` wires it), keeping the interface narrow."""

    resolve_calls: bool
    redact: bool = True


@dataclass(frozen=True, slots=True)
class ProducerBuild:
    """What a producer yields for one corpus: the ingestor + its change-detection enumerator.

    ``files`` MUST enumerate exactly the paths ``ingestor`` reads (its nodes' ``anchor.path``),
    so incremental re-embed never drifts from what was parsed — the producer builds one
    enumerator and hands the same one to both the ingestor and the corpus."""

    ingestor: Ingestor
    files: Callable[[Path], list[Path]]


class Producer(Protocol):
    """Turns a ``CorpusConfig`` of one ``kind`` into a buildable ingredient + validates it."""

    kind: str

    def validate(self, cfg: CorpusConfig) -> None:
        """Raise :class:`ThalamusError` if ``cfg`` is malformed for this kind (config-load time)."""
        ...

    def build(self, cfg: CorpusConfig, *, ctx: ProducerContext) -> ProducerBuild:
        """Build the ingestor + change-detection enumerator for ``cfg``."""
        ...


_PRODUCERS: dict[str, Producer] = {}


def register_producer(producer: Producer) -> None:
    """Register ``producer`` under its ``kind`` (raises on a duplicate kind)."""
    if producer.kind in _PRODUCERS:
        raise ThalamusError(f"producer kind {producer.kind!r} is already registered")
    _PRODUCERS[producer.kind] = producer


def get_producer(kind: str) -> Producer:
    """The producer for ``kind``, or a :class:`ThalamusError` listing the known kinds."""
    try:
        return _PRODUCERS[kind]
    except KeyError:
        known = ", ".join(sorted(_PRODUCERS)) or "(none registered)"
        raise ThalamusError(f"unknown corpus kind {kind!r}; known kinds: {known}") from None


def producer_kinds() -> frozenset[str]:
    """The set of registered producer kinds — the single source of truth for ``CORPUS_KINDS``."""
    return frozenset(_PRODUCERS)
