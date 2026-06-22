from __future__ import annotations

import importlib.util
import math

import pytest
from thalamus.core.exceptions import EncoderError
from thalamus.routing import BgeEncoder, DeterministicEncoder, FastEmbedEncoder, build_encoder


def test_dim() -> None:
    assert DeterministicEncoder(dim=128).dim == 128


def test_reproducible_across_instances() -> None:
    first = DeterministicEncoder(dim=64).encode(["switch to sqlite"])[0]
    second = DeterministicEncoder(dim=64).encode(["switch to sqlite"])[0]
    assert list(first) == list(second)


def test_normalized() -> None:
    (vec,) = DeterministicEncoder(dim=64).encode(["async teardown is flaky"])
    assert math.sqrt(sum(x * x for x in vec)) == pytest.approx(1.0)


def test_empty_text_is_zero_vector() -> None:
    (vec,) = DeterministicEncoder(dim=32).encode([""])
    assert len(vec) == 32
    assert all(x == 0.0 for x in vec)


def test_distinct_texts_differ() -> None:
    first, second = DeterministicEncoder(dim=128).encode(["use postgres", "use sqlite"])
    assert list(first) != list(second)


def test_invalid_dim() -> None:
    with pytest.raises(ValueError):
        DeterministicEncoder(dim=0)


@pytest.mark.skipif(
    importlib.util.find_spec("sentence_transformers") is not None,
    reason="sentence-transformers installed; skip the missing-extra error path",
)
def test_bge_missing_extra_raises() -> None:
    with pytest.raises(EncoderError):
        BgeEncoder().encode(["hello"])


def test_build_encoder_deterministic() -> None:
    enc = build_encoder("deterministic", dim=64)
    assert isinstance(enc, DeterministicEncoder)
    assert enc.dim == 64


def test_build_encoder_unknown_name_raises() -> None:
    with pytest.raises(EncoderError):
        build_encoder("nope")


def test_build_encoder_bge_small_is_fastembed() -> None:
    # Construction is lazy (no model download), so this maps the name without the extra installed.
    assert isinstance(build_encoder("bge-small"), FastEmbedEncoder)


def test_build_encoder_bge_small_st_is_sentence_transformers() -> None:
    assert isinstance(build_encoder("bge-small-st"), BgeEncoder)
