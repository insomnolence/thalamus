"""The dreaming framework imports and exposes its public surface."""

from __future__ import annotations

import thalamus.dreaming as dreaming


def test_public_surface_is_importable() -> None:
    for name in dreaming.__all__:
        assert hasattr(dreaming, name), name
