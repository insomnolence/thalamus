"""StructuralRederivePass re-derives Brain 2 from current source into the live graph/index/manifest
the gateway queries — hash-gated (skips when nothing changed), and runs an optional regen hook
before the ingest. The point: new/changed code becomes recallable without a serve restart."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from thalamus.core.types import RepoId, Scope, TenantId
from thalamus.dreaming import PassContext, PassStatus, StructuralRederivePass
from thalamus.routing import DeterministicEncoder
from thalamus.structural import (
    CorpusSpec,
    InMemoryFileManifest,
    InMemoryStructuralGraph,
    InMemoryStructuralIndex,
    PythonAstIngestor,
    python_files,
)

SCOPE = Scope(TenantId("t"), RepoId("r"))


def _ctx(repo: Path) -> PassContext:
    return PassContext(scope=SCOPE, now=datetime(2026, 6, 13, tzinfo=UTC), repo_root=str(repo))


def _func_ids(graph: InMemoryStructuralGraph) -> set[str]:
    return {node.node_id for node in graph.nodes_of_kind(SCOPE, "function")}


def _corpora(index: InMemoryStructuralIndex) -> list[CorpusSpec]:
    return [CorpusSpec(PythonAstIngestor(), index, python_files, "code")]


def test_rederive_picks_up_new_code_then_skips_when_unchanged(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    graph = InMemoryStructuralGraph(SCOPE)
    index = InMemoryStructuralIndex(dim=32)
    manifest = InMemoryFileManifest()
    rederive = StructuralRederivePass(
        _corpora(index), graph, manifest, DeterministicEncoder(dim=32)
    )

    first = rederive.run(_ctx(repo))
    assert first.status is PassStatus.OK
    assert first.details["changed"] == 1
    before = _func_ids(graph)
    assert len(before) == 1  # the function node for f is now in the live graph

    second = rederive.run(_ctx(repo))  # nothing changed on disk
    assert second.status is PassStatus.SKIPPED
    assert _func_ids(graph) == before

    # Edit the source while "serving": the pass re-derives live — f drops, g appears, no restart.
    (repo / "mod.py").write_text("def g():\n    return 2\n", encoding="utf-8")
    third = rederive.run(_ctx(repo))
    assert third.status is PassStatus.OK
    after = _func_ids(graph)
    assert len(after) == 1
    assert after != before  # the live graph now reflects the edited source


def test_skips_without_a_repo_root_handle() -> None:
    graph = InMemoryStructuralGraph(SCOPE)
    index = InMemoryStructuralIndex(dim=32)
    rederive = StructuralRederivePass(
        _corpora(index), graph, InMemoryFileManifest(), DeterministicEncoder(dim=32)
    )
    outcome = rederive.run(PassContext(scope=SCOPE, now=datetime(2026, 6, 13, tzinfo=UTC)))
    assert outcome.status is PassStatus.SKIPPED


def test_regen_hook_runs_before_ingest(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    graph = InMemoryStructuralGraph(SCOPE)
    index = InMemoryStructuralIndex(dim=32)
    seen: list[int] = []

    def regen(specs: Sequence[CorpusSpec]) -> None:
        seen.append(len(list(specs)))

    rederive = StructuralRederivePass(
        _corpora(index), graph, InMemoryFileManifest(), DeterministicEncoder(dim=32), regen=regen
    )
    rederive.run(_ctx(repo))
    assert seen == [1]  # the regen hook ran once, handed this corpus to (re)build its artifact
