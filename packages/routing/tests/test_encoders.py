from __future__ import annotations

import importlib.util
import math
import tempfile
from pathlib import Path

import pytest
from thalamus.core.exceptions import EncoderError
from thalamus.routing import (
    ENCODER_NAMES,
    BgeEncoder,
    DeterministicEncoder,
    FastEmbedEncoder,
    build_encoder,
    default_model_cache_dir,
)


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


def test_every_advertised_encoder_name_is_accepted() -> None:
    assert tuple(type(build_encoder(name)).__name__ for name in ENCODER_NAMES) == (
        "FastEmbedEncoder",
        "BgeEncoder",
        "DeterministicEncoder",
    )


# --- model cache location -------------------------------------------------------------------
# The weights are ~65 MB and are fetched inside the MCP client's startup window, so the cache
# must outlive a reboot. fastembed's own default (system temp dir) does not.


def test_cache_dir_is_never_under_the_system_temp_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    """The regression guard: /tmp is tmpfs on many Linux systems, so a temp-dir cache is erased
    on reboot and every first serve after a boot re-downloads the model and times out."""
    monkeypatch.delenv("FASTEMBED_CACHE_PATH", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    assert Path(tempfile.gettempdir()) not in default_model_cache_dir().parents


def test_cache_dir_defaults_under_home_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FASTEMBED_CACHE_PATH", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    assert default_model_cache_dir() == Path.home() / ".cache" / "thalamus" / "fastembed"


def test_cache_dir_honours_xdg_cache_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("FASTEMBED_CACHE_PATH", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert default_model_cache_dir() == tmp_path / "thalamus" / "fastembed"


def test_fastembed_cache_path_wins_over_xdg(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An operator who already set fastembed's own variable keeps that exact location."""
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(tmp_path / "explicit"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert default_model_cache_dir() == tmp_path / "explicit"


def test_blank_env_values_fall_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", "   ")
    monkeypatch.setenv("XDG_CACHE_HOME", "")
    assert default_model_cache_dir() == Path.home() / ".cache" / "thalamus" / "fastembed"


def test_explicit_cache_dir_overrides_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(tmp_path / "from-env"))
    assert FastEmbedEncoder(cache_dir=tmp_path / "explicit").cache_dir == tmp_path / "explicit"


def test_encoder_cache_dir_tracks_the_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(tmp_path / "cache"))
    assert FastEmbedEncoder().cache_dir == tmp_path / "cache"


def test_cache_dir_expands_user() -> None:
    assert FastEmbedEncoder(cache_dir="~/models").cache_dir == Path.home() / "models"
