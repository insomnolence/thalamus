from __future__ import annotations


def test_package_imports() -> None:
    import thalamus.cli  # noqa: F401
    from thalamus.cli import main, run_sync  # noqa: F401
