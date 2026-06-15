"""Tests for the producer registry + built-in producers (thalamus.cli.producers)."""

from __future__ import annotations

from pathlib import Path

import pytest
from thalamus.cli import producers  # noqa: F401 — registers the built-in producers on import
from thalamus.cli.producer_registry import (
    Producer,
    ProducerBuild,
    ProducerContext,
    get_producer,
    producer_kinds,
    register_producer,
)
from thalamus.cli.project import CorpusConfig
from thalamus.core.exceptions import ThalamusError
from thalamus.core.types import RepoId, Scope, TenantId
from thalamus.structural import DocIngestor, Ingestor, ScipIngestor, TextIngestor

CTX = ProducerContext(resolve_calls=False)
_SCOPE = Scope(TenantId("t"), RepoId("r"))


def _cfg(**kwargs: object) -> CorpusConfig:
    base: dict[str, object] = {"name": "c", "root": Path("."), "kind": "text"}
    base.update(kwargs)
    return CorpusConfig(**base)  # type: ignore[arg-type]


def test_builtin_kinds_are_registered() -> None:
    assert producer_kinds() == frozenset({"python-ast", "scip", "docs", "text"})


def test_corpus_kinds_derive_from_the_registry() -> None:
    from thalamus.cli import project

    assert producer_kinds() == project.CORPUS_KINDS


def test_get_producer_unknown_kind_lists_the_known_kinds() -> None:
    with pytest.raises(ThalamusError, match="unknown corpus kind 'nope'; known kinds:"):
        get_producer("nope")


def test_duplicate_registration_raises() -> None:
    with pytest.raises(ThalamusError, match="already registered"):
        register_producer(get_producer("text"))


def test_each_builtin_builds_the_expected_ingestor_and_enumerator() -> None:
    py = get_producer("python-ast").build(_cfg(kind="python-ast"), ctx=CTX)
    assert isinstance(py, ProducerBuild) and isinstance(py.ingestor, Ingestor)

    docs = get_producer("docs").build(_cfg(kind="docs"), ctx=CTX)
    assert isinstance(docs.ingestor, DocIngestor)

    text = get_producer("text").build(_cfg(kind="text"), ctx=CTX)
    assert isinstance(text.ingestor, TextIngestor)

    scip = get_producer("scip").build(
        _cfg(kind="scip", scip_index=Path("x.scip"), include=("*.rs",)), ctx=CTX
    )
    assert isinstance(scip.ingestor, ScipIngestor)


def test_text_producer_hands_one_enumerator_to_ingestor_and_corpus(tmp_path: Path) -> None:
    # The no-drift invariant: the ingestor walks exactly the files change detection hashes, so
    # they can't disagree about what's in the corpus. We assert both see the same .txt set.
    (tmp_path / "keep.txt").write_text("x\n", encoding="utf-8")
    (tmp_path / "skip.md").write_text("y\n", encoding="utf-8")
    built = get_producer("text").build(_cfg(kind="text", include=("*.txt",)), ctx=CTX)
    enumerated = {p.name for p in built.files(tmp_path)}
    nodes = built.ingestor.ingest_path(tmp_path, _SCOPE).nodes
    ingested = {Path(n.anchor.path).name for n in nodes if n.anchor}
    assert enumerated == {"keep.txt"}
    assert ingested == {"keep.txt"}


def test_scip_validate_requires_an_index() -> None:
    with pytest.raises(ThalamusError, match="requires 'scip_index'"):
        get_producer("scip").validate(_cfg(kind="scip"))


def test_text_validate_rejects_bad_options() -> None:
    with pytest.raises(ThalamusError, match="option 'chunk_chars' must be an integer"):
        get_producer("text").validate(_cfg(kind="text", options={"chunk_chars": "huge"}))
    with pytest.raises(ThalamusError, match="overlap_chars .* must be < chunk_chars"):
        get_producer("text").validate(
            _cfg(kind="text", options={"chunk_chars": "100", "overlap_chars": "100"})
        )


def test_producer_protocol_is_satisfied_by_builtins() -> None:
    for kind in producer_kinds():
        producer: Producer = get_producer(kind)
        assert producer.kind == kind
