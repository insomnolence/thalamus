from __future__ import annotations

from datetime import UTC, datetime

from thalamus.core.types import (
    Cue,
    Hemisphere,
    MemoryId,
    MemoryRecord,
    RepoId,
    Scope,
    TenantId,
)
from thalamus.retrieval import LexicalRetriever, tokenize
from thalamus.store import InMemoryStore

SCOPE = Scope(tenant_id=TenantId("t1"), repo_id=RepoId("r1"))
NOW = datetime(2026, 6, 14, tzinfo=UTC)


def _record(mid: str, content: str) -> MemoryRecord:
    return MemoryRecord(
        memory_id=MemoryId(mid),
        hemisphere=Hemisphere.EXPERIENTIAL,
        kind="episode",
        content=content,
        scope=SCOPE,
        created_at=NOW,
    )


def _store(*records: MemoryRecord) -> InMemoryStore:
    store = InMemoryStore(dim=8)
    for record in records:
        store.add(record, [0.0] * 8)  # lexical retrieval never reads the vector
    return store


def test_tokenize_keeps_identifiers_drops_stopwords() -> None:
    assert tokenize("the build_corpora helper is in BrainTwo") == [
        "build_corpora",
        "helper",
        "braintwo",
    ]


def test_bm25_ranks_the_exact_term_hit_first() -> None:
    store = _store(
        _record("hit", "the StructuralRederivePass re-derives Brain 2 on the maintenance tick"),
        _record("near", "the dreaming scheduler refreshes the gateway's derived views"),
        _record("off", "completely unrelated note about coffee and weather"),
    )
    result = LexicalRetriever(store).retrieve(
        Cue(text="where is StructuralRederivePass", scope=SCOPE), k=3
    )
    assert result.shown[0].record.memory_id == MemoryId("hit")


def test_no_term_overlap_is_excluded() -> None:
    store = _store(
        _record("a", "alpha beta gamma"),
        _record("b", "delta epsilon zeta"),
    )
    result = LexicalRetriever(store).retrieve(Cue(text="gamma", scope=SCOPE), k=5)
    # only the doc that actually contains a query term is scored/returned
    assert [s.record.memory_id for s in result.candidates] == [MemoryId("a")]


def test_rarer_term_outweighs_a_common_one() -> None:
    # "the" is a stop word and "config" is common across docs; "regen_command" is rare -> the doc
    # that has the rare term should win even though both share the common term.
    store = _store(
        _record("common", "the config the config the config the config the config"),
        _record("rare", "the config mentions regen_command exactly once here"),
    )
    result = LexicalRetriever(store).retrieve(
        Cue(text="config regen_command", scope=SCOPE), k=2
    )
    assert result.shown[0].record.memory_id == MemoryId("rare")
