"""The files the brain writes and the bar reads: one way to write each kind.

A whole JSON file (status, choice of advisor, lessons) is written to a temporary file and
moved into place, so the bar never reads half of one. A JSON-lines file (journal, talk) gets
one line at a time: the brain's run loop and a short-lived ``instruct`` or ``ask`` may write
it at the same moment, so writers take a lock, a file that grew too long is cut in place (a
writer that has it open keeps a valid handle, which a replaced file would not give it), and a
reader skips a line that broke instead of failing on it.

Everything here is the player's alone: the folder 0700, every file 0600 (cos20 #492).
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import math
import os
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Any

LINE_BYTES = 300  # a journal line is about 100 bytes, rarely 400; enough to read from the end


def state_dir() -> Path:
    """The brain's state folder (``$XDG_STATE_HOME/shikigon``). An empty ``XDG_STATE_HOME``
    counts as unset, as in the wake script (``${XDG_STATE_HOME:-…}``) and the bar (``||``)."""
    base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return base / "shikigon"


def private_folder(path: Path) -> Path:
    """``path`` as a folder for the owner only (0700), also if it was made with another mode."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def keep_private(folder: Path) -> None:
    """Every file in ``folder`` and its folders readable by the owner only, as a new file.

    0.1.0 made the state folder with the default mode, and its files were 0644 until 0.1.13. A
    folder made private later keeps out who opens a file from now on, not who opened one
    then: an open descriptor goes on reading the same file, and the journal and the talk were
    only ever appended to (cos20 #492, third review). So such a file is copied into a new one,
    0600, and the new one takes its name; an old descriptor is left with the old file, which
    gets nothing more. Under the file's lock, so a line written meanwhile goes into the new
    file (``append``). A link is never followed, to a folder or a file: what lies outside the
    state folder stays as it is."""
    for where, _folders, names in os.walk(folder):  # does not follow links to folders
        for name in sorted(names):
            path = Path(where) / name
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(info.st_mode) or not info.st_mode & 0o077:
                continue
            with contextlib.suppress(OSError):  # a file it cannot read is no reason to stop
                _renew(path)


def _renew(path: Path) -> None:
    """``path`` copied into a new file of mode 0600 that takes its name."""
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as old:
        fcntl.flock(old, fcntl.LOCK_EX)  # released when the old file closes
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
        try:
            with os.fdopen(fd, "wb") as new:
                shutil.copyfileobj(old, new)
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise


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


def _no_constant(name: str) -> Any:
    raise ValueError(f"{name} is no JSON number")


def _finite_float(text: str) -> float:
    number = float(text)
    if not math.isfinite(number):
        raise ValueError(f"{text[:20]} is no finite number")
    return number


def loads(data: str | bytes) -> Any:
    """``json.loads`` for text from outside: deep nesting is a ``ValueError`` like other bad JSON,
    and so is a number that is not finite.

    ``json.loads("[" * 100_000)`` raises ``RecursionError``, which no ``except ValueError``
    catches: a model's answer of brackets ended a move with a traceback instead of a refusal
    (cos20 #492 S8). ``NaN``, ``Infinity`` and ``1e999`` are no JSON numbers; Python took them,
    and a head's move with one in it ended the brain where httpx refuses to send it
    (``allow_nan=False``; sixth review, finding 2). Every JSON the brain reads goes through here
    (guard in ``test_files``)."""
    try:
        return json.loads(data, parse_constant=_no_constant, parse_float=_finite_float)
    except RecursionError as exc:
        raise ValueError("the JSON is nested too deep") from exc


def finite(value: Any) -> float | None:
    """``value`` as a finite number, or ``None``: how the brain reads a number from outside.

    A word, a list, ``True`` or an endless number is no amount (cos20 #492, fifth and sixth
    review: a bare ``float()`` on such a value ended the brain)."""
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        number = float(value)
    except (ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


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
    while True:
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "ab") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)  # released when the file closes
            if not _still_there(path, handle.fileno()):
                continue  # ``keep_private`` replaced it while we waited: into the new one
            handle.write(line)
            handle.flush()
            if keep_bytes is not None and os.fstat(handle.fileno()).st_size > 2 * keep_bytes:
                _keep_last(path, keep_bytes)
            return


def _still_there(path: Path, fd: int) -> bool:
    """True if ``fd`` is the file that stands at ``path`` now."""
    try:
        return os.stat(path).st_ino == os.fstat(fd).st_ino
    except FileNotFoundError:
        return False


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


# What ``tail`` reads at most. A line of the talk holds an answer whole (cos20 #468): from an agent
# up to 1 MiB, written as JSON escapes up to six times that.
TAIL_BYTES = 16 * 1024 * 1024


def tail(path: Path, count: int, line_bytes: int = LINE_BYTES) -> list[dict[str, Any]]:
    """The last ``count`` lines that hold a JSON object, oldest first, read from the file's end.

    ``line_bytes`` is a first guess at a line's length: the window grows until it holds ``count``
    whole lines, the file's start or ``TAIL_BYTES``. Until 0.1.14 it stayed at the guess, and one
    long line pushed the lines before it out: an answer of 50,000 characters emptied the talk the
    bar shows, one of 20,000 the talk the next question is given, long refusals the head's last
    moves (cos20 #566, F5, F7). A line that is not one (cut by a crash, or read while the file was
    being cut) is skipped.
    """
    if not path.exists():
        return []
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        end = handle.tell()
        block = min(end, line_bytes * count, TAIL_BYTES)
        while True:
            handle.seek(end - block)
            lines = handle.read(block).decode("utf-8", "replace").splitlines()
            if block < end:
                lines = lines[1:]  # the first line may be cut
            if len(lines) >= count or block >= min(end, TAIL_BYTES):
                break
            block = min(end, 2 * block, TAIL_BYTES)
    entries: list[dict[str, Any]] = []
    for line in lines[-count:]:
        try:
            entry = loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries
