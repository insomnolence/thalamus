from __future__ import annotations

import importlib.util
import math
from collections.abc import Sequence

import pytest
from thalamus.routing import BgeEncoder

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("sentence_transformers") is None,
    reason="sentence-transformers not installed (the 'bge' extra)",
)

# Small model keeps the download light; dim 384.
MODEL = "BAAI/bge-small-en-v1.5"


def _cos(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def test_bge_dim_and_normalized() -> None:
    encoder = BgeEncoder(MODEL)
    (vec,) = encoder.encode(["hello world"])
    assert encoder.dim == 384
    assert len(vec) == 384
    assert math.isclose(math.sqrt(sum(x * x for x in vec)), 1.0, abs_tol=1e-3)


def test_bge_semantic_beats_lexical() -> None:
    # The query shares NO words with the related memory, yet is semantically close;
    # it also shares no words with the distractor. A lexical encoder cannot tell them
    # apart; BGE ranks the semantically-related memory higher. This is *why* real
    # embeddings matter — the recall ceiling (deep-dives/outcome-learned-retrieval §13.2).
    encoder = BgeEncoder(MODEL)
    query = "problems reverting database schema changes"
    related = "the migration failed during rollback"
    distractor = "the login button was recolored blue"
    q, r, d = encoder.encode([query, related, distractor])
    assert _cos(q, r) > _cos(q, d)
