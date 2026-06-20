"""Multi-root doc ingestion: docs from directories OUTSIDE the code root, each as its own
labeled corpus — the sample-project case (a design-docs dir beside the code package's own docs).

In-memory (no Neo4j/MCP). Asserts: no node-id collision across roots that share a filename,
and each root surfaces as its own ``docs (<label>)`` corpus.
"""

from __future__ import annotations

from pathlib import Path

from thalamus.cli import build_two_hemisphere_gateway
from thalamus.core.types import RepoId, Scope, StructuralRef, TenantId
from thalamus.routing import DeterministicEncoder
from thalamus.store import InMemoryStore

SCOPE = Scope(tenant_id=TenantId("t"), repo_id=RepoId("r"))


def _doc(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _build(repo: Path, doc_roots: list[Path]):  # noqa: ANN202 - test helper
    return build_two_hemisphere_gateway(
        repo,
        store=InMemoryStore(dim=32),
        encoder=DeterministicEncoder(dim=32),
        scope=SCOPE,
        episodes=[],
        doc_roots=doc_roots,
    )


def test_two_doc_roots_no_collision_and_labeled(tmp_path: Path) -> None:
    code = tmp_path / "pkg"  # empty-ish code root (the code corpus is irrelevant here)
    code.mkdir()
    # Two doc roots that BOTH contain a README.md — would collide on node id without namespacing.
    _doc(tmp_path / "design" / "docs" / "README.md", "# Design\n\nThe alpha widget rationale.\n")
    _doc(tmp_path / "pkg" / "docs" / "README.md", "# Project\n\nThe beta gadget guide.\n")

    gateway = _build(code, [tmp_path / "design" / "docs", tmp_path / "pkg" / "docs"])
    graph = gateway.graph
    assert graph is not None
    # Distinct, namespaced ids => both documents coexist (no collision in the shared graph).
    assert graph.get(StructuralRef(SCOPE, "document:design:README.md")) is not None
    assert graph.get(StructuralRef(SCOPE, "document:pkg:README.md")) is not None


def test_recall_tags_each_doc_root_as_its_own_corpus(tmp_path: Path) -> None:
    code = tmp_path / "pkg"
    code.mkdir()
    _doc(tmp_path / "design" / "docs" / "intro.md", "# Design\n\nThe alpha widget rationale.\n")
    _doc(tmp_path / "pkg" / "docs" / "intro.md", "# Project\n\nThe beta gadget guide.\n")

    gateway = _build(code, [tmp_path / "design" / "docs", tmp_path / "pkg" / "docs"])

    alpha = gateway.recall(prompt="alpha widget rationale", scope=SCOPE)
    corpora = {item.corpus for item in alpha.structural}
    # The design-docs root surfaces under its own labeled corpus (parent dir = "design").
    assert "docs (design)" in corpora
    assert any(c.startswith("docs (") for c in corpora)
