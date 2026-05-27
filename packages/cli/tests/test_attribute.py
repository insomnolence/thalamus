from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from thalamus.cli.attribute import AttributeConfig, compute_attribution, run_attribute
from thalamus.core.types import (
    EventId,
    Hemisphere,
    MemoryId,
    MemoryRecord,
    RepoId,
    Scope,
    SessionId,
    TenantId,
)
from thalamus.instrumentation import (
    JsonlEventSink,
    JsonlTrajectorySink,
    RetrievalEvent,
    ShownItem,
    TrajectoryEvent,
    TrajectoryEventKind,
    read_usage_log,
)
from thalamus.routing import DeterministicEncoder
from thalamus.store import InMemoryStore
from thalamus.structural import FootprintAttributor, InMemoryStructuralGraph
from thalamus.structural.schema import IngestResult, SourceAnchor, StructuralEdge, StructuralNode

SCOPE = Scope(TenantId("local"), RepoId("r"))
NOW = datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC)


def _module(name: str, repo: Path) -> StructuralNode:
    return StructuralNode(
        node_id=f"module:{name}", kind="module", label=name, scope=SCOPE,
        anchor=SourceAnchor(path=str(repo / f"{name}.py"), line_start=1, line_end=1),
    )


def _graph(repo: Path) -> tuple[InMemoryStructuralGraph, list[StructuralNode]]:
    nodes = [_module("a", repo), _module("b", repo), _module("c", repo)]
    edges = [StructuralEdge("module:a", "module:b", "imports")]  # a imports b; c unconnected
    graph = InMemoryStructuralGraph(SCOPE)
    graph.add(IngestResult(nodes=nodes, edges=edges))
    return graph, nodes


def _event(eid: str, session: str, memory: str) -> RetrievalEvent:
    return RetrievalEvent(
        event_id=EventId(eid), timestamp=NOW, scope=SCOPE, policy_id="L0", cue_text="q",
        k_requested=1, candidates=[], shown=[ShownItem(MemoryId(memory), rank=0, propensity=1.0)],
        session_id=SessionId(session),
    )


def _commit(eid: str, session: str, files: list[str]) -> TrajectoryEvent:
    return TrajectoryEvent(
        EventId(eid), NOW, SCOPE, TrajectoryEventKind.COMMIT,
        {"sha": eid, "subject": "work", "files": files}, session_id=SessionId(session),
    )


def test_compute_attribution_joins_recalls_to_session_work(tmp_path: Path) -> None:
    graph, nodes = _graph(tmp_path)
    attributor = FootprintAttributor(graph, nodes, repo_root=tmp_path)
    footprints = {MemoryId("m_hit"): ("a.py",), MemoryId("m_miss"): ("c.py",)}
    events = [
        _event("e1", "s1", "m_hit"),   # s1 committed a.py -> m_hit (footprint a.py) used
        _event("e2", "s1", "m_miss"),  # m_miss (footprint c.py) not connected -> not used
        _event("e3", "s2", "m_hit"),   # s2 has recalls but NO commit -> skipped (missing data)
    ]
    trajectory = [_commit("t1", "s1", ["a.py"])]

    computed = compute_attribution(events, trajectory, footprints, attributor)
    signals = {(s.event_id, s.memory_id): s for s in computed}

    assert signals[(EventId("e1"), MemoryId("m_hit"))].used is True
    assert signals[(EventId("e1"), MemoryId("m_hit"))].kind == "footprint"
    assert signals[(EventId("e2"), MemoryId("m_miss"))].used is False
    assert (EventId("e3"), MemoryId("m_hit")) not in signals  # s2 had no work to attribute against


def _config(repo: Path) -> AttributeConfig:
    logs = repo / ".thalamus" / "logs"
    return AttributeConfig(
        repo=repo, tenant="local", repo_id="r", dim=64, encoder="deterministic", k_hop=1,
        resolve_calls=False,
        retrieval_log=logs / "retrieval.jsonl", trajectory_log=logs / "trajectory.jsonl",
        attributed_log=logs / "usage_attributed.jsonl",
        neo4j_uri=None, neo4j_user="neo4j", neo4j_password=None,
    )


def test_run_attribute_writes_derived_log_and_is_idempotent(tmp_path: Path) -> None:
    logs = tmp_path / ".thalamus" / "logs"
    logs.mkdir(parents=True)
    JsonlEventSink(logs / "retrieval.jsonl").emit(_event("e1", "s1", "m_hit"))
    JsonlTrajectorySink(logs / "trajectory.jsonl").emit(_commit("t1", "s1", ["a.py"]))

    encoder = DeterministicEncoder(dim=64)
    store = InMemoryStore(dim=64)
    record = MemoryRecord(
        MemoryId("m_hit"), Hemisphere.EXPERIENTIAL, "episode", "did a thing", SCOPE, NOW,
        metadata={"footprint": ["a.py"]},
    )
    store.add(record, encoder.encode([record.content])[0])
    graph, nodes = _graph(tmp_path)
    config = _config(tmp_path)

    signals = run_attribute(config, store=store, encoder=encoder, graph=graph, nodes=nodes)
    assert [(s.memory_id, s.used) for s in signals] == [(MemoryId("m_hit"), True)]

    written = list(read_usage_log(config.attributed_log))
    assert [(s.memory_id, s.used) for s in written] == [(MemoryId("m_hit"), True)]

    # derived view: re-running overwrites (no duplication)
    run_attribute(config, store=store, encoder=encoder, graph=graph, nodes=nodes)
    assert len(list(read_usage_log(config.attributed_log))) == 1
