"""thalamus.cli — composition-root command-line entrypoints.

The one place that wires concrete encoders/stores/observers/graphs together, kept out
of the libraries so they stay decoupled from concretes. ``python -m thalamus.cli`` has
subcommands for derived episode sync, explicit retained memory, evidence capture, and
MCP serving. See ``docs/deep-dives/path-to-real-data.md``.
"""

from thalamus.cli.app import main
from thalamus.cli.brain import build_store, build_two_hemisphere_gateway, close_store
from thalamus.cli.dogfood import SyncConfig, build_ingestor, parse_args, run_sync
from thalamus.cli.remember import RememberConfig, build_retained_record, run_remember
from thalamus.cli.serve import ServeConfig, build_serve_gateway, run_serve, serve_config
from thalamus.cli.test_capture import TestCaptureConfig, run_test_capture, test_config

__all__ = [
    "ServeConfig",
    "SyncConfig",
    "TestCaptureConfig",
    "RememberConfig",
    "build_ingestor",
    "build_retained_record",
    "build_serve_gateway",
    "build_store",
    "build_two_hemisphere_gateway",
    "close_store",
    "main",
    "parse_args",
    "run_serve",
    "run_sync",
    "run_remember",
    "run_test_capture",
    "serve_config",
    "test_config",
]
