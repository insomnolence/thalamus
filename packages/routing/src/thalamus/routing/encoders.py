"""Text encoders implementing :class:`thalamus.core.Encoder`.

Two interchangeable implementations behind the one protocol:

- :class:`DeterministicEncoder` — offline, dependency-free, *reproducible*
  hashing-trick embedding. Real and useful for tests/CI and for wiring the
  pipeline without downloading a model. Lexical, not semantically strong.
- :class:`BgeEncoder` — frozen BGE sentence-transformer (the production
  encoder). Requires the optional ``bge`` extra.

(Referenced from an earlier project of ours: the
lazy-load + ``TYPE_CHECKING`` pattern is kept; its torch coupling and
single-string API are dropped in favour of plain float lists + batch encode.)
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from typing import TYPE_CHECKING

from thalamus.core.exceptions import EncoderError
from thalamus.core.types import Vector

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

_TOKEN_RE = re.compile(r"[a-z0-9]+")


class DeterministicEncoder:
    """A stable hashing-trick encoder — offline, dependency-free, reproducible.

    Uses ``blake2b`` (not the per-process-salted builtin ``hash``) so vectors
    are identical across runs and processes. Output vectors are L2-normalized,
    so a dot product equals cosine similarity. Swappable for :class:`BgeEncoder`
    behind ``core.Encoder``; it is a *lexical baseline*, not a production-quality
    semantic encoder.
    """

    def __init__(self, dim: int = 256) -> None:
        if dim <= 0:
            raise ValueError(f"dim must be positive, got {dim}")
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def encode(self, texts: Sequence[str]) -> list[Vector]:
        return [self._encode_one(text) for text in texts]

    def _encode_one(self, text: str) -> list[float]:
        vec = [0.0] * self._dim
        for token in _TOKEN_RE.findall(text.lower()):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % self._dim
            sign = 1.0 if digest[4] & 1 else -1.0
            vec[index] += sign
        norm = math.sqrt(sum(value * value for value in vec))
        if norm > 0.0:
            vec = [value / norm for value in vec]
        return vec


class BgeEncoder:
    """Frozen BGE sentence-transformer encoder, lazily loaded.

    The model loads on first ``encode``/``dim`` call (fast construction,
    testable). Embeddings are L2-normalized and returned as plain ``float``
    lists (no torch in the public surface). Requires the optional ``bge`` extra
    (``sentence-transformers``).
    """

    def __init__(self, model_name: str = "BAAI/bge-base-en-v1.5", *, device: str = "cpu") -> None:
        self._model_name = model_name
        self._device = device
        self._model: SentenceTransformer | None = None
        self._dim: int | None = None

    def _load(self) -> SentenceTransformer:
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:  # pragma: no cover - exercised only without extra
                raise EncoderError(
                    "BgeEncoder requires the 'bge' extra: pip install 'thalamus-routing[bge]'"
                ) from exc
            self._model = SentenceTransformer(self._model_name, device=self._device)
        return self._model

    @property
    def dim(self) -> int:
        if self._dim is None:
            model = self._load()
            # sentence-transformers 5 renamed get_sentence_embedding_dimension; prefer the new name.
            get_dim = getattr(model, "get_embedding_dimension", None)
            if get_dim is not None:
                reported = get_dim()
            else:
                reported = model.get_sentence_embedding_dimension()
            if reported is None:
                raise EncoderError(
                    f"Model '{self._model_name}' does not report an embedding dimension"
                )
            self._dim = int(reported)
        return self._dim

    def encode(self, texts: Sequence[str]) -> list[Vector]:
        model = self._load()
        raw = model.encode(list(texts), normalize_embeddings=True, convert_to_numpy=True)
        return [[float(value) for value in row] for row in raw]
