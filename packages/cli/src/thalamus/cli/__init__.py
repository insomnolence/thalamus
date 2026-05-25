"""thalamus.cli — composition-root command-line entrypoints.

The one place that wires concrete encoders/stores/observers together, kept out of
the libraries so they stay decoupled from concretes. ``python -m thalamus.cli`` runs
the dogfood sync (a repo's commits → Brain 1). See ``docs/deep-dives/path-to-real-data.md``.
"""

from thalamus.cli.brain import build_two_hemisphere_gateway
from thalamus.cli.dogfood import SyncConfig, build_ingestor, main, parse_args, run_sync

__all__ = [
    "SyncConfig",
    "build_ingestor",
    "build_two_hemisphere_gateway",
    "main",
    "parse_args",
    "run_sync",
]
