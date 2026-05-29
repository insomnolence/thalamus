"""Per-project thalamus.toml: path resolution, key mapping, and the CLI precedence chain."""

from __future__ import annotations

import argparse
from pathlib import Path

from thalamus.cli.project import find_project_config, load_project_config
from thalamus.cli.serve import add_serve_arguments, serve_config

_TOML = """
repo_id    = "dollhouse"
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
    arg_defaults, env_defaults = load_project_config(_write_toml(tmp_path))
    # friendly keys -> argparse dests
    assert arg_defaults["repo_id"] == "dollhouse"
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
    arg_defaults, _ = load_project_config(path)
    assert arg_defaults == {"repo_id": "x"}  # forward-compatible: unknown key dropped


def test_find_prefers_explicit_then_cwd(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    explicit = tmp_path / "custom.toml"
    explicit.write_text("repo_id='x'\n", encoding="utf-8")
    assert find_project_config(explicit) == explicit
    monkeypatch.chdir(tmp_path)
    assert find_project_config(None) is None  # no ./thalamus.toml here
    (tmp_path / "thalamus.toml").write_text("repo_id='y'\n", encoding="utf-8")
    assert find_project_config(None) == tmp_path / "thalamus.toml"


def test_toml_defaults_flow_into_serve_config_and_cli_overrides(tmp_path: Path) -> None:
    arg_defaults, _ = load_project_config(_write_toml(tmp_path))
    parser = argparse.ArgumentParser()
    add_serve_arguments(parser)
    valid = {a.dest for a in parser._actions}
    parser.set_defaults(**{k: v for k, v in arg_defaults.items() if k in valid})

    # No CLI flags: the toml supplies the values.
    cfg = serve_config(parser.parse_args([]))
    assert cfg.repo_id == "dollhouse"
    assert cfg.code_language == "typescript"
    assert cfg.port == 8788
    assert cfg.repo == (tmp_path / "mcp-server").resolve()
    assert cfg.data_dir == tmp_path.resolve()
    assert cfg.scip_index == (tmp_path / "mcp-server.scip").resolve()

    # An explicit flag overrides the toml default (CLI > thalamus.toml).
    cfg2 = serve_config(parser.parse_args(["--port", "9999"]))
    assert cfg2.port == 9999
    assert cfg2.repo_id == "dollhouse"  # untouched keys still from the toml
