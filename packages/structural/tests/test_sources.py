"""The code corpus walker excludes tests; the general python walker does not."""

from __future__ import annotations

from pathlib import Path

from thalamus.structural import code_files, python_files


def _write(repo: Path, rel: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x = 1\n", encoding="utf-8")


def test_code_files_excludes_tests(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/impl.py")
    _write(tmp_path, "pkg/test_impl.py")  # test_ prefix
    _write(tmp_path, "pkg/impl_test.py")  # _test suffix
    _write(tmp_path, "conftest.py")
    _write(tmp_path, "tests/test_thing.py")  # under a tests/ dir
    _write(tmp_path, "tests/helper.py")  # non-test file but inside tests/

    got = {p.relative_to(tmp_path).as_posix() for p in code_files(tmp_path)}
    assert got == {"pkg/impl.py"}  # only real source survives


def test_python_files_still_returns_everything(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/impl.py")
    _write(tmp_path, "pkg/test_impl.py")
    _write(tmp_path, "tests/test_thing.py")

    got = {p.relative_to(tmp_path).as_posix() for p in python_files(tmp_path)}
    assert got == {"pkg/impl.py", "pkg/test_impl.py", "tests/test_thing.py"}


def test_code_files_single_file(tmp_path: Path) -> None:
    impl = tmp_path / "impl.py"
    impl.write_text("x = 1\n", encoding="utf-8")
    test = tmp_path / "test_impl.py"
    test.write_text("x = 1\n", encoding="utf-8")
    assert code_files(impl) == [impl]
    assert code_files(test) == []  # a single test file is excluded too
