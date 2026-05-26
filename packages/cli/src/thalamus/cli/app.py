"""Top-level CLI dispatch: ``python -m thalamus.cli <command>``.

Subcommands compose concrete operational paths: derived episode sync, explicit retained
memory, test evidence capture, and the persistent MCP gateway. Each lives in its own
module; this only wires argument parsing and dispatch.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from thalamus.cli.dogfood import add_sync_arguments, run_sync, sync_config
from thalamus.cli.remember import add_remember_arguments, remember_config, run_remember
from thalamus.cli.serve import add_serve_arguments, run_serve, serve_config
from thalamus.cli.test_capture import add_test_arguments, run_test_capture, test_config
from thalamus.cli.verdict import add_verdict_arguments, run_verdict, verdict_config


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m thalamus.cli", description="Thalamus brain command line."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    add_sync_arguments(sub.add_parser("sync", help="ingest a repo's commits into Brain 1"))
    add_remember_arguments(
        sub.add_parser("remember", help="store a retained decision, constraint, or gotcha")
    )
    add_serve_arguments(sub.add_parser("serve", help="serve the two-hemisphere brain over MCP"))
    add_test_arguments(sub.add_parser("capture-tests", help="append JUnit evidence to the raw log"))
    add_verdict_arguments(
        sub.add_parser("verdict", help="report the 'does the brain help?' verdict on the real logs")
    )

    args = parser.parse_args(list(argv) if argv is not None else None)
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
    return 0
