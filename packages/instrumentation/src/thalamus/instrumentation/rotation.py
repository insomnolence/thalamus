"""Size-based rotation + segment-aware reading for the append-only logs (Track I).

The event/usage/trajectory logs are append-only and otherwise unbounded — left alone they grow
without limit. Rotation bounds the *live* file: when it exceeds ``max_bytes`` it is renamed to a
numbered archive (``<name>.1``; older segments carry higher indices) and a fresh live file starts.
Readers concatenate the segments **oldest-first** (:func:`jsonl_segments`), so the learning
consumers — ``verdict``, the attribution time-join, the L-6 training log — still see the full
*retained* history transparently; rotation is invisible to them.

``keep`` is the retention window: the maximum number of archive segments kept, older ones dropped.
For the **usage** signal this is now safe-to-drop, *not* a stopgap: Architecture B (Track I)
consolidates usage into the brain each maintenance tick, reading the **full retained history**
(:func:`jsonl_segments`) — so every retained segment is re-folded into the durable behavioral store
before it can age out, making a dropped usage segment redundant, not lost signal. For the
**retrieval-event and trajectory** logs — not yet brain-consolidated — ``keep`` stays a real
retention bound (the offline ``verdict`` / attribution read their history from these files).

Rotation is concurrency-safe with :func:`append_jsonl`'s reopen-per-append pattern: a ``rename``
leaves any in-flight append on the now-archived inode (preserved) and the next append, which reopens
by path, simply recreates a fresh live file. So rotation can run on the maintenance thread while the
request threads append, with no lock.
"""

from __future__ import annotations

from pathlib import Path


def _archive(path: Path, index: int) -> Path:
    return path.parent / f"{path.name}.{index}"


def _archive_indices(path: Path) -> list[int]:
    """The numeric suffixes of ``path``'s existing archive segments, ascending."""
    prefix = path.name + "."
    indices: list[int] = []
    for sibling in path.parent.glob(prefix + "*"):
        tail = sibling.name[len(prefix) :]
        if tail.isdigit():
            indices.append(int(tail))
    return sorted(indices)


def jsonl_segments(path: Path) -> list[Path]:
    """Existing segments of ``path`` oldest-first: highest-index archive … ``.1`` … live ``path``.

    A missing live file is omitted (a log may exist only as archives, or not at all)."""
    archives = [_archive(path, index) for index in sorted(_archive_indices(path), reverse=True)]
    return [*archives, path] if path.exists() else archives


def rotate_log(path: Path, *, max_bytes: int, keep: int) -> bool:
    """Rotate ``path`` → ``path.1`` past ``max_bytes``; shift archives, drop beyond ``keep``.

    ``max_bytes <= 0`` disables rotation (returns ``False``). Returns whether a rotation happened.
    Shifts from the oldest archive down so an index is never clobbered before it moves."""
    if max_bytes <= 0 or not path.exists() or path.stat().st_size <= max_bytes:
        return False
    for index in sorted(_archive_indices(path), reverse=True):
        if index >= keep:
            _archive(path, index).unlink()  # would shift past the retention window → drop oldest
        else:
            _archive(path, index).rename(_archive(path, index + 1))
    path.rename(_archive(path, 1))
    return True
