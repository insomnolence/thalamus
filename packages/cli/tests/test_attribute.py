from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
from pathlib import Path

from thalamus.cli.attribute import AttributeConfig, compute_attribution, run_attribute
from thalamus.cli.verdict import add_verdict_arguments, run_verdict, verdict_config
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
    build_test_run_event,
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


def _event(eid: str, session: str, memory: str, ts: datetime = NOW) -> RetrievalEvent:
    return RetrievalEvent(
        event_id=EventId(eid), timestamp=ts, scope=SCOPE, policy_id="L0", cue_text="q",
        k_requested=1, candidates=[], shown=[ShownItem(MemoryId(memory), rank=0, propensity=1.0)],
        session_id=SessionId(session),
    )


def _commit(eid: str, session: str, files: list[str], ts: datetime = NOW) -> TrajectoryEvent:
    return TrajectoryEvent(
        EventId(eid), ts, SCOPE, TrajectoryEventKind.COMMIT,
        {"sha": eid, "subject": "work", "files": files}, session_id=SessionId(session),
    )


def test_compute_attribution_joins_recalls_to_committed_work_by_time(tmp_path: Path) -> None:
    graph, nodes = _graph(tmp_path)
    attributor = FootprintAttributor(graph, nodes, repo_root=tmp_path)
    footprints = {MemoryId("m_hit"): ("a.py",), MemoryId("m_miss"): ("c.py",)}
    events = [
        _event("e1", "s1", "m_hit"),   # recall at NOW; a commit lands in its window -> used
        _event("e2", "s1", "m_miss"),  # m_miss (footprint c.py) not connected -> not used
        # s2 recalls a day before any commit -> its window has no work -> skipped (missing data)
        _event("e3", "s2", "m_hit", ts=NOW - timedelta(days=1)),
    ]
    trajectory = [_commit("t1", "s1", ["a.py"])]  # at NOW, inside s1's window

    computed = compute_attribution(events, trajectory, footprints, attributor)
    signals = {(s.event_id, s.memory_id): s for s in computed}

    assert signals[(EventId("e1"), MemoryId("m_hit"))].used is True
    assert signals[(EventId("e1"), MemoryId("m_hit"))].kind == "footprint"
    assert signals[(EventId("e2"), MemoryId("m_miss"))].used is False
    assert (EventId("e3"), MemoryId("m_hit")) not in signals  # no commit in s2's time window


def test_compute_attribution_is_session_agnostic_by_time(tmp_path: Path) -> None:
    # The multi-agent case: a recall keyed to the agent's session joins to a commit stamped with
    # a DIFFERENT session (the serve's), because attribution joins by time, not session id.
    graph, nodes = _graph(tmp_path)
    attributor = FootprintAttributor(graph, nodes, repo_root=tmp_path)
    footprints = {MemoryId("m_hit"): ("a.py",)}
    events = [_event("e1", "agent-claude", "m_hit")]
    trajectory = [_commit("t1", "serve-xyz", ["a.py"])]  # different session, same time
    computed = compute_attribution(events, trajectory, footprints, attributor)
    assert any(s.event_id == EventId("e1") and s.used for s in computed)


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


def test_attribute_then_verdict_lifts_utility_where_lexical_read_zero(tmp_path: Path) -> None:
    # The regression gate: a recall whose memory footprint matches the session's committed work
    # reads used=True (utility > 0) — where the lexical citation signal would read 0 (no overlap
    # between the memory's prose and any record_usage paraphrase, which never happened here).
    logs = tmp_path / ".thalamus" / "logs"
    logs.mkdir(parents=True)
    JsonlEventSink(logs / "retrieval.jsonl").emit(_event("e1", "s1", "m_hit"))
    traj = JsonlTrajectorySink(logs / "trajectory.jsonl")
    traj.emit(_commit("t1", "s1", ["a.py"]))  # the work touched a.py (m_hit's footprint)
    traj.emit(  # a terminal PASSED run gives the session a Tier-2 success label
        build_test_run_event(
            event_id=EventId("tr1"), timestamp=NOW, scope=SCOPE, tests=1, failures=0, errors=0,
            skipped=0, failed=[], terminal=True, session_id=SessionId("s1"),
        )
    )

    encoder = DeterministicEncoder(dim=64)
    store = InMemoryStore(dim=64)
    record = MemoryRecord(
        MemoryId("m_hit"), Hemisphere.EXPERIENTIAL, "episode",
        "an episode whose prose shares no tokens with any usage note", SCOPE, NOW,
        metadata={"footprint": ["a.py"]},
    )
    store.add(record, encoder.encode([record.content])[0])
    graph, nodes = _graph(tmp_path)

    run_attribute(_config(tmp_path), store=store, encoder=encoder, graph=graph, nodes=nodes)

    parser = argparse.ArgumentParser()
    add_verdict_arguments(parser)
    report = run_verdict(verdict_config(parser.parse_args(["--repo", str(tmp_path)])))

    assert report.utility.utility_at_k == 1.0  # the surfaced memory was used (footprint matched)
    assert report.n_tier1_sessions == 1
    assert report.n_tier2_sessions == 1
    assert report.monitor.n_units == 1  # the loop now joins, on a deterministic signal
    assert report.monitor.mean_utility_success == 1.0
