from __future__ import annotations

import importlib.util
import math
from collections.abc import Sequence

import pytest
from thalamus.routing import FastEmbedEncoder

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("fastembed") is None,
    reason="fastembed not installed (the 'fastembed' extra)",
)

# The production model: BGE-small via ONNX Runtime (no torch). Dim 384.
MODEL = "BAAI/bge-small-en-v1.5"


def _cos(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_dim_and_normalized() -> None:
    encoder = FastEmbedEncoder(MODEL)
    (vec,) = encoder.encode(["hello world"])
    assert encoder.dim == 384
    assert len(vec) == 384
    assert math.isclose(math.sqrt(sum(x * x for x in vec)), 1.0, abs_tol=1e-3)


def test_semantic_beats_lexical() -> None:
    # The query shares no words with the related text yet is semantically close, and shares no
    # words with the distractor either — only a real embedding can rank the related one higher.
    encoder = FastEmbedEncoder(MODEL)
    query = "problems reverting database schema changes"
    related = "rolling back a migration left the table corrupted"
    distractor = "the quick brown fox jumps over the lazy dog"
    q, r, d = encoder.encode([query, related, distractor])
    assert _cos(q, r) > _cos(q, d)


def test_empty_batch_returns_empty() -> None:
    assert FastEmbedEncoder(MODEL).encode([]) == []
