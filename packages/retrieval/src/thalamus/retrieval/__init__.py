"""thalamus.retrieval — the swappable Retriever seam.

Provides the L0 baseline now; gated rungs (bandit reweighting, bent geometry)
land as later implementations behind the same ``core.Retriever`` protocol.
"""

from thalamus.retrieval.hybrid import HybridRetriever
from thalamus.retrieval.l0 import L0Retriever
from thalamus.retrieval.lexical import LexicalRetriever, bm25_scores, tokenize
from thalamus.retrieval.recent import render_recent, select_recent
from thalamus.retrieval.usage_weighted import UsageWeightedRetriever

__all__ = [
    "HybridRetriever",
    "L0Retriever",
    "LexicalRetriever",
    "UsageWeightedRetriever",
    "bm25_scores",
    "render_recent",
    "select_recent",
    "tokenize",
]
