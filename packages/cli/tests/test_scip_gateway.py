"""Integration: a two-hemisphere gateway over a TypeScript (SCIP) code corpus.

Exercises the full composition path — ScipIngestor -> incremental_ingest -> graph/index
-> Gateway — in-memory (no Neo4j, no MCP), over the committed ``ts_sample`` fixture.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from thalamus.cli import build_two_hemisphere_gateway
from thalamus.core.types import RepoId, Scope, StructuralRef, TenantId
from thalamus.gateway import Gateway
from thalamus.routing import DeterministicEncoder
from thalamus.store import InMemoryStore

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("google.protobuf") is None, reason="requires the scip extra"
)

SCOPE = Scope(tenant_id=TenantId("t"), repo_id=RepoId("r"))
FIXTURE = Path(__file__).resolve().parents[2] / "structural" / "tests" / "fixtures" / "ts_sample"


def _build() -> Gateway:
    return build_two_hemisphere_gateway(
        FIXTURE,
        store=InMemoryStore(dim=32),
        encoder=DeterministicEncoder(dim=32),
        scope=SCOPE,
        episodes=[],
        code_language="typescript",
        scip_index=FIXTURE / "index.scip",
        resolve_docs=False,
    )


def test_typescript_nodes_and_calls_land_in_the_graph() -> None:
    gateway = _build()
    graph = gateway.graph
    assert graph is not None
    # Nodes from SCIP reached the shared graph through the real composition path.
    for node_id in (
        "module:src.circle",
        "class:src.circle.Circle",
        "method:src.circle.Circle.area",
        "function:src.geometry.circleArea",
    ):
        assert graph.get(StructuralRef(SCOPE, node_id)) is not None
    # The precise cross-file call edge is traversable: Circle.area -> circleArea.
    area = StructuralRef(SCOPE, "method:src.circle.Circle.area")
    callees = {n.node_id for n in graph.neighbors(area, edge_types=("calls",), direction="out")}
    assert "function:src.geometry.circleArea" in callees


def test_recall_surfaces_typescript_code_directly() -> None:
    gateway = _build()
    payload = gateway.recall(prompt="circleArea computes the area of a circle", scope=SCOPE)
    labels = " ".join(item.label for item in payload.structural)
    assert "circleArea" in labels
