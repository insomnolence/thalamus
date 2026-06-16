"""Top-level CLI dispatch: ``python -m thalamus.cli <command>``.

Subcommands compose concrete operational paths: derived episode sync, explicit retained
memory, test evidence capture, and the persistent MCP gateway. Each lives in its own
module; this only wires argument parsing and dispatch.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from thalamus.cli.attribute import add_attribute_arguments, attribute_config, run_attribute
from thalamus.cli.backup import (
    add_backup_arguments,
    add_restore_arguments,
    backup_config,
    restore_config,
    run_backup,
    run_restore,
)
from thalamus.cli.dogfood import add_sync_arguments, run_sync, sync_config
from thalamus.cli.dream import add_dream_arguments, dream_config, run_dream
from thalamus.cli.health import add_health_arguments, health_config, run_health
from thalamus.cli.impact_eval import (
    add_impact_eval_arguments,
    impact_eval_config,
    run_impact_eval,
)
from thalamus.cli.probe_eval import add_probe_eval_arguments, probe_eval_config, run_probe_eval
from thalamus.cli.project import find_project_config, load_project_config
from thalamus.cli.remember import add_remember_arguments, remember_config, run_remember
from thalamus.cli.serve import add_serve_arguments, run_serve, serve_config
from thalamus.cli.test_capture import add_test_arguments, run_test_capture, test_config
from thalamus.cli.verdict import add_verdict_arguments, run_verdict, verdict_config


def _prescan(argv: Sequence[str]) -> tuple[str | None, Path | None]:
    """Find the subcommand + the top-level ``--config`` before the full parse — so a project's
    ``thalamus.toml`` can be loaded and applied as that subparser's defaults."""
    command: str | None = None
    config: Path | None = None
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--config":
            config = Path(argv[i + 1]) if i + 1 < len(argv) else None
            i += 2
            continue
        if tok.startswith("--config="):
            config = Path(tok.split("=", 1)[1])
            i += 1
            continue
        if command is None and not tok.startswith("-"):
            command = tok
        i += 1
    return command, config


def main(argv: Sequence[str] | None = None) -> int:
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    parser = argparse.ArgumentParser(
        prog="python -m thalamus.cli", description="Thalamus brain command line."
    )
    parser.add_argument(
        "--config", type=Path, default=None,
        help="project thalamus.toml (default: ./thalamus.toml if present); its values are "
        "defaults that an explicit flag overrides",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    parsers: dict[str, argparse.ArgumentParser] = {}
    parsers["sync"] = sub.add_parser("sync", help="ingest a repo's commits into Brain 1")
    add_sync_arguments(parsers["sync"])
    parsers["remember"] = sub.add_parser(
        "remember", help="store a retained decision, constraint, or gotcha"
    )
    add_remember_arguments(parsers["remember"])
    parsers["serve"] = sub.add_parser("serve", help="serve the two-hemisphere brain over MCP")
    add_serve_arguments(parsers["serve"])
    parsers["capture-tests"] = sub.add_parser(
        "capture-tests", help="append JUnit evidence to the raw log"
    )
    add_test_arguments(parsers["capture-tests"])
    parsers["verdict"] = sub.add_parser(
        "verdict", help="report the 'does the brain help?' verdict on the real logs"
    )
    add_verdict_arguments(parsers["verdict"])
    parsers["health"] = sub.add_parser(
        "health", help="one-screen health view of a brain (verdict + activity)"
    )
    add_health_arguments(parsers["health"])
    parsers["attribute"] = sub.add_parser(
        "attribute", help="compute the deterministic footprint usage signal"
    )
    add_attribute_arguments(parsers["attribute"])
    parsers["backup"] = sub.add_parser("backup", help="export durable Brain 1 to a snapshot")
    add_backup_arguments(parsers["backup"])
    parsers["restore"] = sub.add_parser("restore", help="restore durable Brain 1 from a snapshot")
    add_restore_arguments(parsers["restore"])
    parsers["probe-eval"] = sub.add_parser(
        "probe-eval", help="L1 verdict: replay transcript questions against the brain"
    )
    add_probe_eval_arguments(parsers["probe-eval"])
    parsers["impact-eval"] = sub.add_parser(
        "impact-eval", help="git-derived blast-radius recall for the plan tool"
    )
    add_impact_eval_arguments(parsers["impact-eval"])
    parsers["dream"] = sub.add_parser(
        "dream", help="run one dreaming cycle (refresh views + audit beliefs)"
    )
    add_dream_arguments(parsers["dream"])

    # Apply a project thalamus.toml (if any) as the chosen subcommand's defaults BEFORE parsing,
    # so precedence is: explicit CLI flag > thalamus.toml > built-in default.
    command, config_arg = _prescan(raw_argv)
    config_path = find_project_config(config_arg)
    if config_path is not None and command in parsers:
        arg_defaults, env_defaults, corpora = load_project_config(config_path)
        for var, val in env_defaults.items():
            os.environ.setdefault(var, val)
        target = parsers[command]
        valid = {action.dest for action in target._actions}
        target.set_defaults(**{k: v for k, v in arg_defaults.items() if k in valid})
        # The declarative [[corpus]] set isn't a CLI flag (it's structured); stash it on the
        # serve namespace directly so serve_config can pick it up. Harmless on other commands.
        if corpora:
            target.set_defaults(corpora=corpora)

    args = parser.parse_args(raw_argv)
    if args.command == "sync":
        run_sync(sync_config(args))
    elif args.command == "remember":
        run_remember(remember_config(args))
    elif args.command == "serve":
        run_serve(serve_config(args))
    elif args.command == "capture-tests":
        run_test_capture(test_config(args))
    elif args.command == "verdict":
        run_verdict(verdict_config(args))
    elif args.command == "health":
        run_health(health_config(args))
    elif args.command == "attribute":
        run_attribute(attribute_config(args))
    elif args.command == "backup":
        run_backup(backup_config(args))
    elif args.command == "restore":
        run_restore(restore_config(args))
    elif args.command == "probe-eval":
        run_probe_eval(probe_eval_config(args))
    elif args.command == "impact-eval":
        run_impact_eval(impact_eval_config(args))
    elif args.command == "dream":
        run_dream(dream_config(args))
    return 0
