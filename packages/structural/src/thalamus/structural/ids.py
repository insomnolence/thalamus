"""Canonical structural node-id scheme — the single source of truth.

Both the AST structure ingestor and the jedi call resolver must name the *same*
code symbol with the *same* node id, or resolved call edges won't connect to their
targets. The id derivation lives here so the two ingestors cannot drift.

Ids are ``"<kind>:<dotted-module>.<symbol>"`` over a stable dotted module derived
from the *corpus-relative file path* (independent of the import name, so it is
robust to src-layouts where they differ). Kinds: module / class / function / method.
"""

from __future__ import annotations

from pathlib import Path


def module_dotted(path: Path, root: Path, root_package: str | None = None) -> str:
    """Stable dotted module identity from a corpus-relative path."""
    relative = path.name if root.is_file() else str(path.relative_to(root))
    parts = list(Path(relative).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    if root_package is not None:
        pkg = root_package.split(".")
        if parts[: len(pkg)] != pkg:
            parts = [*pkg, *parts]
    if not parts:
        parts = root_package.split(".") if root_package is not None else [path.parent.name]
    return ".".join(parts)


def module_id(dotted: str) -> str:
    return f"module:{dotted}"


def class_id(dotted: str, class_name: str) -> str:
    return f"class:{dotted}.{class_name}"


def function_id(dotted: str, func_name: str) -> str:
    return f"function:{dotted}.{func_name}"


def method_id(dotted: str, class_name: str, method_name: str) -> str:
    return f"method:{dotted}.{class_name}.{method_name}"
