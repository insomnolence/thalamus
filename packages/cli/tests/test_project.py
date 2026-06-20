"""Per-project thalamus.toml: path resolution, key mapping, and the CLI precedence chain."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest
from thalamus.cli.project import find_project_config, load_project_config
from thalamus.cli.serve import add_serve_arguments, serve_config

_TOML = """
repo_id    = "sample-app"
code_root  = "mcp-server"
data_dir   = "."
language   = "typescript"
scip_index = "mcp-server.scip"
doc_roots  = ["docs", "mcp-server/docs"]
http_port  = 8788
neo4j_uri  = "bolt://localhost:7688"
neo4j_user = "neo4j"
"""


def _write_toml(tmp_path: Path) -> Path:
    path = tmp_path / "thalamus.toml"
    path.write_text(_TOML, encoding="utf-8")
    return path


def test_load_maps_keys_and_resolves_paths(tmp_path: Path) -> None:
    arg_defaults, env_defaults, _ = load_project_config(_write_toml(tmp_path))
    # friendly keys -> argparse dests
    assert arg_defaults["repo_id"] == "sample-app"
    assert arg_defaults["code_language"] == "typescript"
    assert arg_defaults["port"] == 8788
    # path keys resolved relative to the toml's dir (existence not required)
    assert arg_defaults["repo"] == (tmp_path / "mcp-server").resolve()
    assert arg_defaults["data_dir"] == tmp_path.resolve()
    assert arg_defaults["scip_index"] == (tmp_path / "mcp-server.scip").resolve()
    assert arg_defaults["doc_roots"] == [
        (tmp_path / "docs").resolve(),
        (tmp_path / "mcp-server" / "docs").resolve(),
    ]
    # neo4j (non-secret) bridges to env; password is never in the file
    assert env_defaults == {
        "THALAMUS_NEO4J_URI": "bolt://localhost:7688",
        "THALAMUS_NEO4J_USER": "neo4j",
    }


def test_unknown_keys_ignored(tmp_path: Path) -> None:
    path = tmp_path / "thalamus.toml"
    path.write_text('repo_id = "x"\nfuture_knob = 42\n', encoding="utf-8")
    arg_defaults, _, _ = load_project_config(path)
    assert arg_defaults == {"repo_id": "x"}  # forward-compatible: unknown key dropped


def test_find_prefers_explicit_then_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    explicit = tmp_path / "custom.toml"
    explicit.write_text("repo_id='x'\n", encoding="utf-8")
    assert find_project_config(explicit) == explicit
    monkeypatch.chdir(tmp_path)
    assert find_project_config(None) is None  # no ./thalamus.toml here
    (tmp_path / "thalamus.toml").write_text("repo_id='y'\n", encoding="utf-8")
    assert find_project_config(None) == tmp_path / "thalamus.toml"


def test_toml_defaults_flow_into_serve_config_and_cli_overrides(tmp_path: Path) -> None:
    arg_defaults, _, _ = load_project_config(_write_toml(tmp_path))
    parser = argparse.ArgumentParser()
    add_serve_arguments(parser)
    valid = {a.dest for a in parser._actions}
    parser.set_defaults(**{k: v for k, v in arg_defaults.items() if k in valid})

    # No CLI flags: the toml supplies the values.
    cfg = serve_config(parser.parse_args([]))
    assert cfg.repo_id == "sample-app"
    assert cfg.code_language == "typescript"
    assert cfg.port == 8788
    assert cfg.repo == (tmp_path / "mcp-server").resolve()
    assert cfg.data_dir == tmp_path.resolve()
    assert cfg.scip_index == (tmp_path / "mcp-server.scip").resolve()

    # An explicit flag overrides the toml default (CLI > thalamus.toml).
    cfg2 = serve_config(parser.parse_args(["--port", "9999"]))
    assert cfg2.port == 9999
    assert cfg2.repo_id == "sample-app"  # untouched keys still from the toml


# ── declarative [[corpus]] config ────────────────────────────────────────────────────────────

_CORPUS_TOML = """
repo_id = "poly"

[[corpus]]
name = "rust-core"
root = "crates"
kind = "scip"
scip_index = "rust.scip"
include = ["*.rs"]
regen_command = "rust-analyzer scip . --output rust.scip"

[[corpus]]
name = "py-tools"
root = "tools"
kind = "python-ast"

[[corpus]]
name = "design-docs"
root = "docs"
kind = "docs"
"""


def test_parse_corpora_reads_mixed_languages_and_resolves_paths(tmp_path: Path) -> None:
    path = tmp_path / "thalamus.toml"
    path.write_text(_CORPUS_TOML, encoding="utf-8")
    _, _, corpora = load_project_config(path)
    assert [c.name for c in corpora] == ["rust-core", "py-tools", "design-docs"]
    rust = corpora[0]
    assert rust.kind == "scip"
    assert rust.root == (tmp_path / "crates").resolve()
    assert rust.scip_index == (tmp_path / "rust.scip").resolve()
    assert rust.include == ("*.rs",)
    assert rust.regen_command == "rust-analyzer scip . --output rust.scip"
    assert corpora[1].kind == "python-ast"
    assert corpora[2].kind == "docs"


def test_flat_config_has_no_explicit_corpora(tmp_path: Path) -> None:
    _, _, corpora = load_project_config(_write_toml(tmp_path))
    assert corpora == []  # flat keys drive the build; [[corpus]] is opt-in


def _parse(tmp_path: Path, body: str) -> None:
    path = tmp_path / "thalamus.toml"
    path.write_text(body, encoding="utf-8")
    load_project_config(path)


def test_corpus_validation_errors(tmp_path: Path) -> None:
    from thalamus.core.exceptions import ThalamusError

    with pytest.raises(ThalamusError, match="unknown corpus kind 'rust'; known kinds:"):
        _parse(tmp_path, '[[corpus]]\nname="x"\nroot="."\nkind="rust"\n')
    with pytest.raises(ThalamusError, match="'root' is required"):
        _parse(tmp_path, '[[corpus]]\nname="x"\nkind="docs"\n')
    with pytest.raises(ThalamusError, match="requires 'scip_index'"):
        _parse(tmp_path, '[[corpus]]\nname="x"\nroot="."\nkind="scip"\n')
    with pytest.raises(ThalamusError, match="needs 'include'"):
        _parse(
            tmp_path,
            '[[corpus]]\nname="x"\nroot="."\nkind="scip"\nscip_index="x.scip"\n'
            'regen_command="build"\n',
        )
    # text producer parses + range-checks its options at config-load (not deep in a build).
    with pytest.raises(ThalamusError, match="option 'chunk_chars' must be an integer"):
        _parse(
            tmp_path,
            '[[corpus]]\nname="x"\nroot="."\nkind="text"\n'
            '[corpus.options]\nchunk_chars="big"\n',
        )


def test_parse_text_corpus_with_options(tmp_path: Path) -> None:
    path = tmp_path / "thalamus.toml"
    path.write_text(
        '[[corpus]]\nname="notes"\nroot="notes"\nkind="text"\ninclude=["*.txt"]\n'
        "[corpus.options]\nchunk_chars=400\noverlap_chars=50\n",
        encoding="utf-8",
    )
    _, _, corpora = load_project_config(path)
    assert len(corpora) == 1
    notes = corpora[0]
    assert notes.kind == "text"
    assert notes.root == (tmp_path / "notes").resolve()
    assert notes.include == ("*.txt",)
    # TOML ints are coerced to str for the producer
    assert notes.options == {"chunk_chars": "400", "overlap_chars": "50"}
