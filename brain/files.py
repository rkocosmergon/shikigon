"""The files the brain writes and the bar reads: one way to write each kind.

A whole JSON file (status, choice of advisor, lessons) is written to a temporary file and
moved into place, so the bar never reads half of one. A JSON-lines file (journal, talk) gets
one line at a time: the brain's run loop and a short-lived ``instruct`` or ``ask`` may write
it at the same moment, so writers take a lock, a file that grew too long is cut in place (a
writer that has it open keeps a valid handle, which a replaced file would not give it), and a
reader skips a line that broke instead of failing on it.
"""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
from typing import Any

LINE_BYTES = 300  # a journal line is about 100 bytes, rarely 400; enough to read from the end


def write_json(path: Path, data: Any) -> None:
    """Write a whole JSON file atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def append(path: Path, entry: dict[str, Any], keep_bytes: int | None = None) -> None:
    """Add one line. With ``keep_bytes``, a file past twice that keeps only its last whole
    lines within ``keep_bytes``; without it, the file keeps everything."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = (json.dumps(entry) + "\n").encode("utf-8")
    with path.open("ab") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)  # released when the file closes
        handle.write(line)
        handle.flush()
        if keep_bytes is not None and os.fstat(handle.fileno()).st_size > 2 * keep_bytes:
            _keep_last(path, keep_bytes)


def _keep_last(path: Path, keep_bytes: int) -> None:
    """Cut the file in place to its last whole lines within ``keep_bytes`` (lock held)."""
    with path.open("r+b") as handle:
        handle.seek(0, os.SEEK_END)
        end = handle.tell()
        handle.seek(max(0, end - keep_bytes))
        kept = handle.read()
        if end > keep_bytes:
            kept = kept[kept.find(b"\n") + 1 :]  # the first line may be cut
        handle.seek(0)
        handle.write(kept)
        handle.truncate()


def tail(path: Path, count: int, line_bytes: int = LINE_BYTES) -> list[dict[str, Any]]:
    """The last ``count`` lines that hold a JSON object, oldest first, read from the file's end.

    A line that is not one (cut by a crash, or read while the file was being cut) is skipped.
    """
    if not path.exists():
        return []
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        end = handle.tell()
        block = min(end, line_bytes * count)
        handle.seek(end - block)
        lines = handle.read().decode("utf-8", "replace").splitlines()
    if block < end:
        lines = lines[1:]  # the first line may be cut
    entries: list[dict[str, Any]] = []
    for line in lines[-count:]:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries
