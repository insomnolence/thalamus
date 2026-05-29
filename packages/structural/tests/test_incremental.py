"""The §14.1 correctness oracle: incremental ingestion == a from-scratch full rebuild.

Runs on a tiny temp corpus (no large repo needed): build, then edit/add/delete files and
rebuild incrementally (reusing held graph/index/manifest, simulating persistence), and assert
the result is identical to a full rebuild of the same final corpus. Plus the work-avoided
invariant: a no-change rebuild re-embeds nothing (the structural perf claim, proven by
counting encode calls rather than timing).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from thalamus.core.types import RepoId, Scope, StructuralRef, TenantId, Vector
from thalamus.routing import DeterministicEncoder
from thalamus.structural import (
    CorpusSpec,
    DocIngestor,
    InMemoryFileManifest,
    InMemoryStructuralGraph,
    InMemoryStructuralIndex,
    PythonAstIngestor,
    incremental_ingest,
    markdown_files,
    python_files,
)

SCOPE = Scope(TenantId("t"), RepoId("r"))


class _CountingEncoder:
    """Wraps an encoder to count how many texts it embeds (the work-avoided probe)."""

    def __init__(self) -> None:
        self._inner = DeterministicEncoder(dim=32)
        self.encoded = 0

    @property
    def dim(self) -> int:
        return self._inner.dim

    def encode(self, texts: Sequence[str]) -> list[Vector]:
        self.encoded += len(texts)
        return self._inner.encode(texts)


class _CountingIngestor:
    """Counts how many times it actually parses — to prove a no-change build never parses."""

    def __init__(self) -> None:
        self._inner = PythonAstIngestor()
        self.parses = 0

    def ingest_path(self, root: Path, scope: object) -> object:
        self.parses += 1
        return self._inner.ingest_path(root, scope)  # type: ignore[arg-type]


def test_no_change_build_skips_parsing_entirely(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write(repo, "a.py", "def a():\n    return 1\n")
    graph = InMemoryStructuralGraph(SCOPE)
    index = InMemoryStructuralIndex(dim=32)
    manifest = InMemoryFileManifest()
    ingestor = _CountingIngestor()
    enc = _CountingEncoder()

    def build() -> object:
        return incremental_ingest(
            repo, SCOPE,
            corpora=[CorpusSpec(ingestor, index, python_files, "code")],  # type: ignore[arg-type]
            graph=graph, manifest=manifest, encoder=enc,
        )

    first = build()
    assert first.rebuilt is True and ingestor.parses == 1  # cold build parses (+ jedi at scale)

    second = build()  # nothing changed
    assert second.rebuilt is False  # skipped the whole re-derive
    assert ingestor.parses == 1  # NOT re-parsed — the O(repo) work (incl jedi) is avoided
    assert enc.encoded == first.stats.embedded  # and nothing re-embedded


def _write(repo: Path, rel: str, src: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(src, encoding="utf-8")


def _fresh() -> tuple[InMemoryStructuralGraph, InMemoryStructuralIndex, InMemoryFileManifest]:
    return InMemoryStructuralGraph(SCOPE), InMemoryStructuralIndex(dim=32), InMemoryFileManifest()


def _build(repo: Path, graph, index, manifest, encoder) -> object:
    return incremental_ingest(
        repo, SCOPE,
        corpora=[CorpusSpec(PythonAstIngestor(), index, python_files, "code")],
        graph=graph, manifest=manifest, encoder=encoder,
    )


def _snapshot(
    graph: InMemoryStructuralGraph, index: InMemoryStructuralIndex
) -> tuple[set[str], set[tuple[str, str, str]], dict[str, tuple[float, ...]]]:
    nodes = set(graph._nodes)  # noqa: SLF001 (white-box equivalence check)
    edges = {
        (e.source_id, e.target_id, e.type)
        for adjacency in graph._out.values()  # noqa: SLF001
        for e in adjacency
    }
    embeddings = {ref.node_id: vec for ref, vec in index._embeddings.items()}  # noqa: SLF001
    return nodes, edges, embeddings


def test_incremental_equals_full_rebuild_across_edit_add_delete(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write(repo, "a.py", "import b\n\ndef a_one():\n    return 1\n")
    _write(repo, "b.py", "def b_one():\n    return 1\n")

    # incremental: build v1, then edit a, add c, delete b -> v2, rebuild on the SAME held state
    g, i, m = _fresh()
    enc = DeterministicEncoder(dim=32)
    _build(repo, g, i, m, enc)
    _write(repo, "a.py", "import b\n\ndef a_one():\n    return 2\n")  # edit
    _write(repo, "c.py", "def c_one():\n    return 3\n")  # add
    (repo / "b.py").unlink()  # delete
    _build(repo, g, i, m, enc)

    # oracle: a fresh full build of the v2 corpus
    g2, i2, m2 = _fresh()
    _build(repo, g2, i2, m2, DeterministicEncoder(dim=32))

    assert _snapshot(g, i) == _snapshot(g2, i2)  # incremental result == full rebuild


def test_no_change_rebuild_re_embeds_nothing(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write(repo, "a.py", "def a_one():\n    return 1\n")
    _write(repo, "b.py", "def b_one():\n    return 1\n")
    g, i, m = _fresh()
    enc = _CountingEncoder()

    _build(repo, g, i, m, enc)
    first = enc.encoded
    assert first > 0  # the initial build embeds every node

    _build(repo, g, i, m, enc)  # nothing changed
    assert enc.encoded == first  # zero further embedding — the work-avoided invariant


def test_only_changed_files_are_re_embedded(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write(repo, "a.py", "def a_one():\n    return 1\n")
    _write(repo, "b.py", "def b_one():\n    return 1\n\ndef b_two():\n    return 2\n")
    g, i, m = _fresh()
    enc = _CountingEncoder()
    _build(repo, g, i, m, enc)
    baseline = enc.encoded

    _write(repo, "a.py", "def a_one():\n    return 99\n")  # edit only a.py (1 node)
    _build(repo, g, i, m, enc)
    # only a.py's node(s) re-embedded, not b.py's — far fewer than a full re-embed
    assert 0 < enc.encoded - baseline < baseline


def test_corpus_root_override_ingests_from_outside_the_repo(tmp_path: Path) -> None:
    # A docs corpus can target its own root (outside the code repo) and is change-detected there.
    repo = tmp_path / "repo"
    _write(repo, "a.py", "def a():\n    return 1\n")
    docs = tmp_path / "elsewhere" / "docs"
    docs.mkdir(parents=True)
    (docs / "guide.md").write_text("# Guide\n\nhello\n", encoding="utf-8")

    graph, index, manifest = _fresh()
    doc_index = InMemoryStructuralIndex(dim=32)
    enc = _CountingEncoder()
    corpora = [
        CorpusSpec(PythonAstIngestor(), index, python_files, "code"),
        CorpusSpec(DocIngestor(), doc_index, markdown_files, "docs", root=docs),
    ]

    def build() -> object:
        return incremental_ingest(
            repo, SCOPE, corpora=corpora, graph=graph, manifest=manifest, encoder=enc
        )

    first = build()
    assert first.rebuilt is True
    # the doc from the override root was ingested into the shared graph
    assert graph.get(StructuralRef(SCOPE, "document:guide.md")) is not None

    assert build().rebuilt is False  # no change anywhere -> skip

    (docs / "guide.md").write_text("# Guide\n\nhello there\n", encoding="utf-8")  # edit the doc
    assert build().rebuilt is True  # change in the override root is detected
