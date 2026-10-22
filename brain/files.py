"""The files the brain writes and the bar reads: one way to write each kind.

A whole JSON file (status, choice of advisor, lessons) is written to a temporary file and
moved into place, so the bar never reads half of one. A JSON-lines file (journal, talk) gets
one line at a time: the brain's run loop and a short-lived ``instruct`` or ``ask`` may write
it at the same moment, so writers take a lock, a file that grew too long is cut in place (a
writer that has it open keeps a valid handle, which a replaced file would not give it), and a
reader skips a line that broke instead of failing on it.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import tempfile
from pathlib import Path
from typing import Any

LINE_BYTES = 300  # a journal line is about 100 bytes, rarely 400; enough to read from the end


def write_json(path: Path, data: Any) -> None:
    """Write a whole JSON file atomically, readable by the owner only (0600).

    The temporary file is new and the brain's alone (``mkstemp``: created with ``O_EXCL`` and
    mode 0600). A fixed name like ``<name>.tmp`` kept the mode of a file already there and
    followed a link standing in its place (cos20 #492 S12)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def loads(data: str | bytes) -> Any:
    """``json.loads`` for text from outside: deep nesting is a ``ValueError`` like other bad JSON.

    ``json.loads("[" * 100_000)`` raises ``RecursionError``, which no ``except ValueError``
    catches: a model's answer of brackets ended a move with a traceback instead of a refusal
    (cos20 #492 S8). Every JSON the brain reads goes through here (guard in ``test_files``)."""
    try:
        return json.loads(data)
    except RecursionError as exc:
        raise ValueError("the JSON is nested too deep") from exc


def json_in(text: str) -> Any:
    """The JSON between the first ``{`` and the last ``}`` of a model's answer.

    Raises ``ValueError`` if there is none or it is broken. ``find``/``rfind`` are linear;
    ``re.search(r"\\{.*\\}", text, re.DOTALL)`` before was quadratic in the number of ``{``
    (32,000 took 0.3 s, a 256 KiB answer some 20 s; cos20 #492 S7)."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the answer")
    return loads(text[start : end + 1])


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
            entry = loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries
