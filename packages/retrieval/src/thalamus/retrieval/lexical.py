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

import heapq
import math
import re
from collections import Counter, OrderedDict
from collections.abc import Sequence
from threading import RLock

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


def bm25_index_scores(
    index: _ScopeIndex,
    query: Sequence[str],
    *,
    k1: float = 1.5,
    b: float = 0.75,
) -> dict[MemoryId, float]:
    """Compute BM25 scores for a query using a precomputed _ScopeIndex."""
    n_docs = index.num_documents
    if n_docs == 0 or index.avgdl == 0:
        return {}
    terms = {t for t in query if t in index.inverted_index}
    if not terms:
        return {}
    idf = {
        t: math.log(1.0 + (n_docs - index.doc_freq[t] + 0.5) / (index.doc_freq[t] + 0.5))
        for t in terms
    }
    scores: dict[MemoryId, float] = {}
    for term in terms:
        term_idf = idf[term]
        for doc_id, freq, length in index.inverted_index[term]:
            score = term_idf * (freq * (k1 + 1.0)) / (
                freq + k1 * (1.0 - b + b * length / index.avgdl)
            )
            scores[doc_id] = scores.get(doc_id, 0.0) + score
    return {doc_id: score for doc_id, score in scores.items() if score > 0.0}


def bm25_scores(
    query: Sequence[str],
    documents: Sequence[tuple[MemoryId, Sequence[str]]],
    *,
    k1: float = 1.5,
    b: float = 0.75,
) -> dict[MemoryId, float]:
    """Compute BM25 scores across pre-tokenized documents.

    This public reference helper intentionally keeps its original ``(query, documents)`` contract;
    the live retriever uses :func:`bm25_index_scores` with precomputed frequencies.
    """
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
_MAX_COLD_BUILD_RETRIES = 3


class _ScopeIndex:
    """Pre-tokenized document index for a single scope with precomputed BM25 corpus statistics."""

    def __init__(self, records: Sequence[MemoryRecord]) -> None:
        self.records_by_id = {r.memory_id: r for r in records}
        self.lengths: list[int] = []
        self.doc_freq: dict[str, int] = {}
        self.inverted_index: dict[str, list[tuple[MemoryId, int, int]]] = {}
        for r in records:
            tokens = tokenize(r.content)
            self.lengths.append(len(tokens))
            if not tokens:
                continue
            tok_tuple = tuple(tokens)
            doc_len = len(tok_tuple)
            counts = Counter(tokens)
            for term, count in counts.items():
                self.doc_freq[term] = self.doc_freq.get(term, 0) + 1
                self.inverted_index.setdefault(term, []).append((r.memory_id, count, doc_len))
        # Empty-token documents are still corpus documents: they affect BM25 IDF and average
        # document length even though they never appear in an inverted posting list.
        self.num_documents = len(records)
        if self.num_documents > 0:
            self.avgdl: float = sum(self.lengths) / self.num_documents
        else:
            self.avgdl = 0.0


class LexicalRetriever:
    """BM25 keyword retrieval over a scope's records — the lexical leg behind ``core.Retriever``."""

    def __init__(
        self, store: Store, *, k_candidates: int = 50, k1: float = 1.5, b: float = 0.75
    ) -> None:
        self._store = store
        self._k_candidates = k_candidates
        self._k1 = k1
        self._b = b
        self._lock = RLock()
        self._index_cache: OrderedDict[Scope, _ScopeIndex] = OrderedDict()
        self._generation = 0
        if hasattr(store, "add_listener"):
            store.add_listener(self.invalidate)

    def invalidate(self, scope: Scope | None = None) -> None:
        """Invalidate cached BM25 index when writes occur."""
        with self._lock:
            self._generation += 1
            if scope is None:
                self._index_cache.clear()
            else:
                self._index_cache.pop(scope, None)

    def retrieve(self, cue: Cue, k: int) -> RetrievalResult:
        # Build cold indexes outside the retriever-wide lock so independent scopes can scan in
        # parallel. The generation check closes the lost-invalidation race: a write that overlaps
        # the scan forces a retry, while a concurrent builder for this scope wins and is reused.
        for _attempt in range(_MAX_COLD_BUILD_RETRIES):
            with self._lock:
                index = self._index_cache.get(cue.scope)
                if index is not None:
                    self._index_cache.move_to_end(cue.scope)
                    break
                generation = self._generation

            built = _ScopeIndex(self._store.scan(cue.scope))

            with self._lock:
                index = self._index_cache.get(cue.scope)
                if index is not None:
                    self._index_cache.move_to_end(cue.scope)
                    break
                if generation != self._generation:
                    continue
                index = built
                self._index_cache[cue.scope] = index
                if len(self._index_cache) > _MAX_SCOPE_INDEXES:
                    self._index_cache.popitem(last=False)
                break
        else:
            # Sustained writes can invalidate every optimistic build. Return the last coherent
            # store snapshot without caching it: this bounds query latency without installing a
            # stale index, and the next query will retry from the current store state.
            index = built

        query = tokenize(cue.text)
        scores = bm25_index_scores(index, query, k1=self._k1, b=self._b)
        candidates = [
            ScoredMemory(
                record=index.records_by_id[mid], score=score, features={"lexical": score}
            )
            for mid, score in scores.items()
        ]
        pool = heapq.nlargest(self._k_candidates, candidates, key=lambda scored: scored.score)
        return RetrievalResult(cue=cue, candidates=pool, shown=pool[: max(k, 0)])
