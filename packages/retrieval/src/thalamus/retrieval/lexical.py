"""Lexical (BM25) retrieval — the keyword leg of hybrid recall.

Semantic (vector) recall finds memories by *meaning* but can miss an exact token a query
names verbatim — a rare identifier, an error string, a symbol. BM25 over the raw text is the
classic complement: it ranks by literal term overlap, weighted by term rarity and document
length. Fused with the L0 vector retriever (``HybridRetriever``) it recovers those exact-term
hits without giving up semantic recall.

A boring, dependency-free baseline behind the ``core.Retriever`` seam: it scores by scanning the
scope's records and computing BM25 in-process. Correct and current (no stale index) at the brain
sizes we run; a persistent inverted index can swap in behind the same seam when scan-per-query
stops being cheap. The scan is the same ``Store.scan`` the dreaming/audit passes use.
"""

from __future__ import annotations

import math
import re
from collections import Counter, OrderedDict
from collections.abc import Sequence

from thalamus.core.protocols import Store
from thalamus.core.types import (
    Cue,
    MemoryId,
    MemoryRecord,
    RetrievalResult,
    Scope,
    ScoredMemory,
)

_WORD = re.compile(r"\w+")
# A small, deliberately conservative stop list — common function words carry no retrieval signal
# and only inflate BM25 length normalization. Identifiers/error strings are never stopped.
_STOP = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "if", "in", "into",
    "is", "it", "no", "not", "of", "on", "or", "such", "that", "the", "their", "then",
    "there", "these", "they", "this", "to", "was", "will", "with", "we", "you", "our", "your",
})


def tokenize(text: str) -> list[str]:
    """Lowercase ``\\w+`` tokens minus stop words — keeps identifiers (``build_corpora``) whole."""
    return [tok for tok in (m.lower() for m in _WORD.findall(text)) if tok not in _STOP]


def bm25_scores(
    query: list[str], documents: list[tuple[MemoryId, list[str]]], *, k1: float, b: float
) -> dict[MemoryId, float]:
    """Okapi BM25 score per document for the (distinct) query terms; only docs that hit appear.

    IDF/avgdl are computed over ``documents`` (the scanned scope) — i.e. BM25 over the current
    corpus, recomputed per query so it never goes stale."""
    n_docs = len(documents)
    if n_docs == 0:
        return {}
    lengths = [len(tokens) for _id, tokens in documents]
    avgdl = sum(lengths) / n_docs
    if avgdl == 0:
        return {}
    doc_freq: dict[str, int] = {}
    for _id, tokens in documents:
        for term in set(tokens):
            doc_freq[term] = doc_freq.get(term, 0) + 1
    terms = {t for t in query if t in doc_freq}
    idf = {t: math.log(1.0 + (n_docs - doc_freq[t] + 0.5) / (doc_freq[t] + 0.5)) for t in terms}
    scores: dict[MemoryId, float] = {}
    for (doc_id, tokens), length in zip(documents, lengths, strict=True):
        freqs = Counter(tokens)
        score = 0.0
        for term in terms:
            freq = freqs.get(term, 0)
            if freq == 0:
                continue
            score += idf[term] * (freq * (k1 + 1.0)) / (freq + k1 * (1.0 - b + b * length / avgdl))
        if score > 0.0:
            scores[doc_id] = score
    return scores


_MAX_SCOPE_INDEXES = 32


class _ScopeIndex:
    """Pre-tokenized document index for a single scope."""

    def __init__(self, records: Sequence[MemoryRecord]) -> None:
        self.records_by_id = {r.memory_id: r for r in records}
        self.documents = [(r.memory_id, tokenize(r.content)) for r in records]


class LexicalRetriever:
    """BM25 keyword retrieval over a scope's records — the lexical leg behind ``core.Retriever``."""

    def __init__(
        self, store: Store, *, k_candidates: int = 50, k1: float = 1.5, b: float = 0.75
    ) -> None:
        self._store = store
        self._k_candidates = k_candidates
        self._k1 = k1
        self._b = b
        self._index_cache: OrderedDict[Scope, _ScopeIndex] = OrderedDict()
        if hasattr(store, "add_listener"):
            store.add_listener(self.invalidate)

    def invalidate(self, scope: Scope | None = None) -> None:
        """Invalidate cached BM25 index when writes occur."""
        if scope is None:
            self._index_cache.clear()
        else:
            self._index_cache.pop(scope, None)

    def retrieve(self, cue: Cue, k: int) -> RetrievalResult:
        index = self._index_cache.get(cue.scope)
        if index is None:
            records = self._store.scan(cue.scope)
            index = _ScopeIndex(records)
            self._index_cache[cue.scope] = index
            if len(self._index_cache) > _MAX_SCOPE_INDEXES:
                self._index_cache.popitem(last=False)
        else:
            self._index_cache.move_to_end(cue.scope)

        query = tokenize(cue.text)
        scores = bm25_scores(query, index.documents, k1=self._k1, b=self._b)
        ranked = sorted(
            (
                ScoredMemory(
                    record=index.records_by_id[mid], score=score, features={"lexical": score}
                )
                for mid, score in scores.items()
            ),
            key=lambda scored: scored.score,
            reverse=True,
        )
        pool = ranked[: self._k_candidates]
        return RetrievalResult(cue=cue, candidates=pool, shown=pool[: max(k, 0)])
