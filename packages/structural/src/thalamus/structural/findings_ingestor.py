"""Findings ingestor — external analysis results as a re-derivable Brain-2 corpus.

The §vision producer/aggregator principle: Thalamus *ingests the findings* a tool emits, it never
runs the tool's engine. A linter, a security scanner, or a deeper analyzer (code-scalpel) writes a
findings file; this turns each finding into a retrievable Brain-2 node so ``recall`` (and later
``plan``/``research``) can surface "what's already known to be wrong/risky here".

Two input shapes are accepted, detected by structure:
- **SARIF** (the static-analysis interchange standard most scanners emit) — ``runs[].results[]``.
- a **generic findings JSON** — ``{"findings": [...]}`` or a bare list of
  ``{path, line, end_line?, rule?, severity?, message, tool?}`` — for tools/adapters that don't
  speak SARIF.

**Anchoring (deliberate):** a finding node anchors to the *findings file*, not to the source line
it is about. The incremental machinery re-embeds a node when its ``anchor.path`` is in the corpus'
change set, so the anchor must be the file actually read (the findings file) for a findings refresh
to re-embed — see :func:`~thalamus.structural.incremental.incremental_ingest` step 6. The real
source location (``src/foo.ts:42``) is carried in the node's label, embeddable text, and metadata
(so a future pass can link findings to the code symbols they annotate)."""

from __future__ import annotations

import hashlib
import json
import logging
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from thalamus.core.redaction import redact_secrets
from thalamus.core.types import Scope
from thalamus.structural.schema import IngestResult, SourceAnchor, StructuralNode
from thalamus.structural.sources import _rel, glob_files

logger = logging.getLogger(__name__)

_LABEL_CHARS = 120
_MESSAGE_CHARS = 8_192
_PATH_CHARS = 4_096
_RULE_CHARS = 512
_TOOL_CHARS = 256
_SEVERITY_CHARS = 64


def _bounded(value: str, limit: int) -> str:
    """Bound analyzer-controlled fields before redaction, embedding, or storage."""
    return value[:limit]


@dataclass(frozen=True, slots=True)
class Finding:
    """One analysis finding, normalized from whatever input shape produced it."""

    source_path: str  # the code file the finding is ABOUT (as the tool reported it)
    line: int
    end_line: int
    rule: str
    severity: str
    message: str
    tool: str


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _generic_finding(entry: Any) -> Finding | None:
    """A finding from a generic JSON object; ``None`` if it lacks a path or any message/rule."""
    if not isinstance(entry, dict):
        return None
    path = entry.get("path") or entry.get("file") or entry.get("location")
    message = str(entry.get("message", "")).strip()
    rule = str(entry.get("rule") or entry.get("rule_id") or entry.get("ruleId") or "").strip()
    if not path or (not message and not rule):
        return None
    line = _as_int(entry.get("line", entry.get("start_line", 1)), 1)
    return Finding(
        source_path=str(path),
        line=line,
        end_line=_as_int(entry.get("end_line", line), line),
        rule=rule,
        severity=str(entry.get("severity") or entry.get("level") or "info").strip(),
        message=message,
        tool=str(entry.get("tool", "")).strip(),
    )


def _sarif_findings(data: dict[str, Any]) -> list[Finding]:
    """Findings from a SARIF document (defensive against missing/odd fields)."""
    out: list[Finding] = []
    runs = data.get("runs")
    if not isinstance(runs, list):
        return out
    for run in runs:
        if not isinstance(run, dict):
            continue
        driver = run.get("tool", {}).get("driver", {}) if isinstance(run.get("tool"), dict) else {}
        tool = str(driver.get("name", "")).strip() if isinstance(driver, dict) else ""
        for result in run.get("results", []) if isinstance(run.get("results"), list) else []:
            if not isinstance(result, dict):
                continue
            rule = str(result.get("ruleId", "")).strip()
            severity = str(result.get("level", "warning")).strip()
            msg = result.get("message", {})
            message = str(msg.get("text", "")).strip() if isinstance(msg, dict) else str(msg)
            locations = result.get("locations", [])
            for loc in locations if isinstance(locations, list) else []:
                phys = loc.get("physicalLocation", {}) if isinstance(loc, dict) else {}
                art = phys.get("artifactLocation", {}) if isinstance(phys, dict) else {}
                region = phys.get("region", {}) if isinstance(phys, dict) else {}
                uri = str(art.get("uri", "")).strip() if isinstance(art, dict) else ""
                if not uri:
                    continue
                if uri.startswith("file://"):
                    uri = urllib.parse.unquote(uri[7:])
                reg = region if isinstance(region, dict) else {}
                line = _as_int(reg.get("startLine", 1), 1)
                end = _as_int(reg.get("endLine", line), line)
                out.append(Finding(uri, line, end, rule, severity, message, tool))
    return out


def parse_findings(text: str) -> list[Finding]:
    """Parse a findings file's text into normalized findings (SARIF or generic JSON)."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("skipping findings file: invalid JSON (%s)", exc)
        return []
    if isinstance(data, dict) and "runs" in data:
        return _sarif_findings(data)
    raw = data.get("findings") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        return []
    return [f for f in (_generic_finding(entry) for entry in raw) if f is not None]


class FindingsIngestor:
    """Ingests findings files into one ``finding`` node per analysis result."""

    def __init__(
        self,
        *,
        files: Callable[[Path], list[Path]],
        id_namespace: str | None = None,
        redact: bool = True,
    ) -> None:
        # ``files`` MUST enumerate exactly what this ingestor reads (the findings files) — they are
        # also the nodes' anchor.path, so incremental re-embed fires when a findings file changes.
        self._files = files
        self._id_prefix = f"{id_namespace}:" if id_namespace else ""
        self._redact = redact

    def ingest_path(self, root: Path, scope: Scope) -> IngestResult:
        nodes: list[StructuralNode] = []
        for path in self._files(root):
            self._ingest_file(path, root, scope, nodes)
        return IngestResult(nodes=nodes, edges=[])

    def _ingest_file(
        self, path: Path, root: Path, scope: Scope, nodes: list[StructuralNode]
    ) -> None:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("skipping %s: %s", path, exc)
            return
        rel_file = _rel(path, root)
        seen: set[str] = set()
        for finding in parse_findings(text):
            node = self._node(finding, path, rel_file, scope)
            if node.node_id not in seen:  # de-dup identical findings within a file
                seen.add(node.node_id)
                nodes.append(node)

    def _node(
        self, f: Finding, findings_path: Path, rel_file: str, scope: Scope
    ) -> StructuralNode:
        del rel_file  # reserved for a future source-file namespace; the anchor carries the report
        # Analyzer messages are attacker-controlled and SARIF does not bound them. Bound every
        # displayed/stored field before running the scrubber. Structural source paths remain
        # unredacted so finding→code linking and stable identities keep using the tool's coordinate.
        source_path = _bounded(f.source_path, _PATH_CHARS)
        rule = _bounded(f.rule or "finding", _RULE_CHARS)
        msg = _bounded(f.message, _MESSAGE_CHARS)
        tool = _bounded(f.tool, _TOOL_CHARS)
        severity = _bounded(f.severity, _SEVERITY_CHARS)
        if self._redact:
            msg = redact_secrets(msg).text
            rule = redact_secrets(rule).text
            if tool:
                tool = redact_secrets(tool).text
            if severity:
                severity = redact_secrets(severity).text
        # Hash the original normalized finding, not its redacted presentation. Two findings that
        # differ only in a removed secret must not collapse into one node.
        digest = hashlib.sha1(  # noqa: S324 - id stability, not security
            f"{f.source_path}:{f.line}:{f.rule}:{f.message}".encode()
        ).hexdigest()[:8]
        node_id = f"finding:{self._id_prefix}{source_path}:{f.line}:{digest}"
        where = f"{source_path}:{f.line}"
        base = f"{Path(source_path).name}:{f.line}"
        label = f"{rule} ({severity}) {base} — {msg}".strip()[:_LABEL_CHARS]
        text = " ".join(
            part for part in (severity, tool, rule, "at", where + ":", msg) if part
        )
        return StructuralNode(
            node_id=node_id,
            kind="finding",
            label=label,
            scope=scope,
            # Anchor to the FINDINGS file (for incremental re-embed); source loc is in metadata.
            anchor=SourceAnchor(path=str(findings_path), line_start=1, line_end=1),
            metadata={
                "text": text,
                "rule": rule,
                "severity": severity,
                "tool": tool,
                "source_path": source_path,
                "source_line": f.line,
                "source_end_line": f.end_line,
            },
        )


def findings_files(*include: str) -> Callable[[Path], list[Path]]:
    """File enumerator for a findings corpus — the configured ``include`` globs (no default)."""
    return glob_files(*include)


__all__ = ["Finding", "FindingsIngestor", "findings_files", "parse_findings"]
