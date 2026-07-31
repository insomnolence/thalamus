"""Text encoders implementing :class:`thalamus.core.Encoder`.

Three interchangeable implementations behind the one protocol, selected via
:func:`build_encoder` (the single encoder-choice seam):

- :class:`FastEmbedEncoder` — the **production** semantic encoder: BGE-small
  (``BAAI/bge-small-en-v1.5``) run through ``fastembed`` (ONNX Runtime, no
  torch). Requires the optional ``fastembed`` extra.
- :class:`BgeEncoder` — the *same model* via ``sentence-transformers`` (torch).
  Kept for parity/ablation; needs ``sentence-transformers`` installed. Its
  embeddings match :class:`FastEmbedEncoder`'s (verified cosine ~1.0), so the
  two are interchangeable against the same index.
- :class:`DeterministicEncoder` — offline, dependency-free, *reproducible*
  hashing-trick embedding. For tests/CI and air-gapped use. Lexical, not
  semantically strong.

(Referenced from an earlier project of ours: the
lazy-load + ``TYPE_CHECKING`` pattern is kept; its torch coupling and
single-string API are dropped in favour of plain float lists + batch encode.)
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from thalamus.core.exceptions import EncoderError
from thalamus.core.types import Vector

if TYPE_CHECKING:
    from fastembed import TextEmbedding
    from sentence_transformers import SentenceTransformer
    from thalamus.core import Encoder

_TOKEN_RE = re.compile(r"[a-z0-9]+")

#: Honoured by ``fastembed`` itself, so an operator who has already set it keeps that location.
_CACHE_ENV_VAR = "FASTEMBED_CACHE_PATH"


def default_model_cache_dir() -> Path:
    """Where downloaded encoder weights live — a **persistent** directory, chosen deliberately.

    ``fastembed`` defaults to ``fastembed_cache`` under the *system temp directory*. On Linux
    ``/tmp`` is commonly ``tmpfs`` (RAM-backed), so that default is erased on every reboot. The
    weights are ~65 MB, and over stdio the serve is a subprocess of the agent, so the re-download
    lands inside the MCP client's startup handshake window — the client then reports nothing more
    useful than a closed connection. Inheriting an ephemeral cache for a large artifact on a
    startup-timeout-sensitive path is not a decision worth leaving to a dependency's default, so
    Thalamus picks the location itself.

    Precedence: ``FASTEMBED_CACHE_PATH`` (an explicit operator choice) > ``XDG_CACHE_HOME`` >
    ``~/.cache``. The last two resolve to ``<cache>/thalamus/fastembed``.
    """
    override = os.environ.get(_CACHE_ENV_VAR, "").strip()
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CACHE_HOME", "").strip()
    base = Path(xdg).expanduser() if xdg else Path.home() / ".cache"
    return base / "thalamus" / "fastembed"


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
    lists (no torch in the public surface). The torch path is intentionally not
    a project extra (it would bloat the default install); install it manually
    with ``pip install sentence-transformers``. :class:`FastEmbedEncoder` is the
    default and produces matching embeddings without torch.
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
                    "BgeEncoder requires sentence-transformers: pip install sentence-transformers "
                    "(or use the default FastEmbedEncoder via --encoder bge-small)"
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


class FastEmbedEncoder:
    """Frozen BGE encoder via ``fastembed`` (ONNX Runtime, no torch), lazily loaded.

    Runs ``BAAI/bge-small-en-v1.5`` and returns L2-normalized plain ``float``
    lists. Embeddings are interchangeable with :class:`BgeEncoder` (verified
    cosine ~1.0 on the same model). The model loads on first ``encode``/``dim``
    call (fast construction, testable). Requires the optional ``fastembed`` extra.

    Note that ``fastembed`` resolves ``BAAI/bge-small-en-v1.5`` to Qdrant's quantized ONNX
    conversion (``qdrant/bge-small-en-v1.5-onnx-q``, Apache-2.0) of that MIT-licensed model —
    that conversion, not the BAAI repository, is what is downloaded.

    ``cache_dir`` overrides where those weights are stored; it defaults to
    :func:`default_model_cache_dir`, which is persistent across reboots by design.
    """

    def __init__(
        self,
        model_name: str = "BAAI/bge-small-en-v1.5",
        *,
        cache_dir: Path | str | None = None,
    ) -> None:
        self._model_name = model_name
        self._cache_dir = Path(cache_dir).expanduser() if cache_dir is not None else None
        self._model: TextEmbedding | None = None
        self._dim: int | None = None

    @property
    def cache_dir(self) -> Path:
        """The directory the weights are cached in (resolved lazily, like the model itself)."""
        return self._cache_dir if self._cache_dir is not None else default_model_cache_dir()

    def _load(self) -> TextEmbedding:
        if self._model is None:
            try:
                from fastembed import TextEmbedding
            except ImportError as exc:  # pragma: no cover - exercised only without extra
                raise EncoderError(
                    "FastEmbedEncoder requires the 'fastembed' extra: "
                    "pip install 'thalamus-routing[fastembed]'"
                ) from exc
            self._model = TextEmbedding(model_name=self._model_name, cache_dir=str(self.cache_dir))
        return self._model

    @property
    def dim(self) -> int:
        if self._dim is None:
            (probe,) = self.encode(["probe"])
            self._dim = len(probe)
        return self._dim

    def encode(self, texts: Sequence[str]) -> list[Vector]:
        model = self._load()
        # fastembed.embed yields one numpy float32 row per text (already L2-normalized for
        # BGE); normalize defensively so the output matches BgeEncoder exactly.
        out: list[Vector] = []
        for row in model.embed(list(texts)):
            values = [float(value) for value in row]
            norm = math.sqrt(sum(value * value for value in values))
            if norm > 0.0:
                values = [value / norm for value in values]
            out.append(values)
        return out


_BGE_SMALL = "BAAI/bge-small-en-v1.5"
ENCODER_NAMES = ("bge-small", "bge-small-st", "deterministic")


def build_encoder(name: str, *, dim: int = 256) -> Encoder:
    """Construct the encoder named ``name`` — the single seam for encoder choice.

    - ``"bge-small"``: production semantic encoder — BGE-small via fastembed
      (ONNX Runtime, no torch); needs the ``fastembed`` extra.
    - ``"bge-small-st"``: the *same model* via sentence-transformers (torch);
      kept for parity/ablation (needs ``sentence-transformers`` installed).
    - ``"deterministic"``: offline, dependency-free lexical baseline of width ``dim``.
    """
    if name == "bge-small":
        return FastEmbedEncoder(_BGE_SMALL)
    if name == "bge-small-st":
        return BgeEncoder(_BGE_SMALL)
    if name == "deterministic":
        return DeterministicEncoder(dim=dim)
    raise EncoderError(f"unknown encoder {name!r} (choices: {', '.join(ENCODER_NAMES)})")
