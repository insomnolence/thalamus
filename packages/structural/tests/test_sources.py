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


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("export const x = 1;\n", encoding="utf-8")


def test_typescript_files_selects_ts_tsx_excludes_decls_and_tests(tmp_path: Path) -> None:
    from thalamus.structural.sources import typescript_files

    for rel in (
        "src/a.ts",
        "src/b.tsx",
        "src/types.d.ts",  # declaration file
        "src/a.test.ts",  # test
        "src/a.spec.tsx",  # spec
        "tests/c.ts",  # under tests/
        "node_modules/dep/d.ts",  # dependency
    ):
        _touch(tmp_path / rel)

    found = {p.relative_to(tmp_path).as_posix() for p in typescript_files(tmp_path)}
    assert found == {"src/a.ts", "src/b.tsx"}


def test_typescript_files_single_file(tmp_path: Path) -> None:
    from thalamus.structural.sources import typescript_files

    src = tmp_path / "x.ts"
    _touch(src)
    assert typescript_files(src) == [src]
    decl = tmp_path / "x.d.ts"
    _touch(decl)
    assert typescript_files(decl) == []
