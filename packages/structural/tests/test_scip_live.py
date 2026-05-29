"""Live drift guard: regenerate the fixture `.scip` and compare to the committed one.

Skipped unless ``scip-typescript`` is on PATH. Catches indexer drift (a scip-typescript
upgrade that changes symbols/ranges) so the committed binary fixture can't go silently
stale relative to the indexer the ingestor is built against.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest
from thalamus.core.types import RepoId, Scope, TenantId
from thalamus.structural.scip_ingestor import ScipIngestor

_HAS_PROTOBUF = importlib.util.find_spec("google.protobuf") is not None
_HAS_INDEXER = shutil.which("scip-typescript") is not None

pytestmark = pytest.mark.skipif(
    not (_HAS_PROTOBUF and _HAS_INDEXER),
    reason="requires the scip extra and scip-typescript on PATH",
)

SCOPE = Scope(TenantId("t"), RepoId("r"))
FIXTURE = Path(__file__).parent / "fixtures" / "ts_sample"


def _node_edge_sets(result):  # noqa: ANN001, ANN202 - test helper
    nodes = {(n.node_id, n.kind) for n in result.nodes}
    edges = {(e.source_id, e.target_id, e.type) for e in result.edges}
    return nodes, edges


def test_regenerated_index_matches_committed_fixture(tmp_path: Path) -> None:
    # Copy the sample sources (not the committed index) to a temp dir and re-index.
    project = tmp_path / "ts_sample"
    shutil.copytree(FIXTURE, project, ignore=shutil.ignore_patterns("index.scip"))
    subprocess.run(
        ["scip-typescript", "index", "--output", "index.scip"],
        cwd=project,
        check=True,
        capture_output=True,
    )

    fresh = ScipIngestor(project / "index.scip").ingest_path(project, SCOPE)
    committed = ScipIngestor(FIXTURE / "index.scip").ingest_path(FIXTURE, SCOPE)

    fresh_nodes, fresh_edges = _node_edge_sets(fresh)
    committed_nodes, committed_edges = _node_edge_sets(committed)
    assert fresh_nodes == committed_nodes
    assert fresh_edges == committed_edges
