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
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

LINE_BYTES = 300  # a journal line is about 100 bytes, rarely 400; enough to read from the end


def xdg_home(name: str, fallback: Path) -> Path:
    """The folder an XDG variable names, if it names an absolute one; else ``fallback``.

    An empty or relative value counts as unset (XDG Base Directory spec). Until 0.1.14 a relative
    one made the folder relative to wherever a command was started: the config of that folder
    was read (cos20 #613), and the state folder with the talk and the advisor's key stood in a
    folder another user may own (cos20 #492, betreuer pass ead8263, B1). The same rule stands in
    the bar (``Model.stateHome``) and in both scripts (``case``)."""
    given = os.environ.get(name) or ""
    return Path(given) if os.path.isabs(given) else fallback


def state_dir() -> Path:
    """The brain's state folder (``$XDG_STATE_HOME/shikigon``, ``xdg_home``)."""
    return xdg_home("XDG_STATE_HOME", Path.home() / ".local" / "state") / "shikigon"


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
        _lock_within(old.fileno(), HELD_WAIT_S)  # released when the old file closes
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
        try:
            with os.fdopen(fd, "wb") as new:
                shutil.copyfileobj(old, new)
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise


HELD_WAIT_S = 2.0  # how long ``held`` waits for the other process, at most
_HELD_RETRY_S = 0.02
MOVE_LOCK = "move.lock"


def _lock_within(fd: int, wait_s: float) -> None:
    """An exclusive ``flock`` on ``fd`` within ``wait_s``, else ``TimeoutError`` (an ``OSError``).
    Until 1d81304 ``held`` waited without a deadline, in the thread of the brain's event loop: a
    holder stopped inside its few lines stopped every move and question with it (cos20 #492,
    Betreuer-Durchgang 1d81304, B4, K24)."""
    deadline = time.monotonic() + wait_s
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise TimeoutError("the state folder stays locked by another process") from None
            time.sleep(_HELD_RETRY_S)


@contextlib.contextmanager
def one_move(folder: Path) -> Iterator[bool]:
    """``True`` while this process plays a move, ``False`` when another process is in one.

    One move at a time: the running brain and a ``tick`` by hand never play at once. What a move
    reads when it chooses (the learning budget's rest, the words' spacing, the paused moves) is
    what holds when it goes out and is written down; ``held`` covers one read, change and write
    only. It never waits: a move that finds another under way is left out. Its own file, not the
    folder ``held`` locks (cos20 #492, Betreuer-Durchgang 1d81304, B1–B3)."""
    folder.mkdir(parents=True, exist_ok=True)
    fd = os.open(folder / MOVE_LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    mine = False
    try:
        with contextlib.suppress(BlockingIOError):  # another process is in a move
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            mine = True
        if mine:
            _MOVING.add(os.path.abspath(folder))
        yield mine
    finally:
        if mine:
            _MOVING.discard(os.path.abspath(folder))
        os.close(fd)  # closing it lets the lock go


# The state folders this process is in a move of (``one_move``, ``in_move_or_held``).
_MOVING: set[str] = set()


@contextlib.contextmanager
def in_move_or_held(folder: Path) -> Iterator[None]:
    """For a file that only a move reads, changes and writes (the learning budget, the words'
    memo, the tournament ends seen, the refusals): inside this process's move, ``one_move``
    already keeps every other writer out, and nothing waits; outside one, ``held``. Until
    af58853 the booking of a move that had gone out waited for ``held`` and, past its deadline,
    was lost: the next move had the whole rest again (cos20 #492, Betreuer-Durchgang af58853,
    B1, K45)."""
    if os.path.abspath(folder) in _MOVING:
        yield
    else:
        with held(folder):
            yield


# This process's hold on a state folder: its descriptor and how deep it is held (``held``).
_HELD: dict[str, list[int]] = {}
_HELD_HERE = threading.RLock()


@contextlib.contextmanager
def held(folder: Path) -> Iterator[None]:
    """The folder to this process alone, for one read, change and write of a file in it.

    Two processes keep the same files: the running brain, and next to it a ``tick`` by hand,
    a question from the terminal, a look back. ``write_json`` is atomic, but reading, changing
    and writing is not: two at the same moment read the same state, and the second write undid
    the first. Calls a day went past ``max_per_day``, a booking of the learning budget was lost
    (Fehlersuche Stück 24, Fund #2). An exclusive ``flock`` on the folder itself, no file of its
    own; held again in this process it passes through. Never across an ``await``: the other
    process waits for it (a guard in the tests), and at most ``HELD_WAIT_S``."""
    key = os.path.abspath(folder)
    with _HELD_HERE:
        entry = _HELD.get(key)
        if entry is None:
            folder.mkdir(parents=True, exist_ok=True)
            fd = os.open(key, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            try:
                _lock_within(fd, HELD_WAIT_S)
            except BaseException:
                os.close(fd)
                raise
            entry = _HELD[key] = [fd, 0]
        entry[1] += 1
        try:
            yield
        finally:
            entry[1] -= 1
            if entry[1] == 0:
                del _HELD[key]
                os.close(entry[0])  # closing it lets the lock go


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


_FROM_A_MODEL = json.JSONDecoder(parse_constant=_no_constant, parse_float=_finite_float)


# How deep a JSON the brain reads may nest. The deepest it is given is 8 (the world's
# ``/api/v1/game/info`` and its state, measured 02.11.2026); a walk over 64 levels stays far
# below Python's recursion limit of 1000, also at two frames a level (``head.own_words``).
MAX_DEPTH = 64


def loads(data: str | bytes) -> Any:
    """``json.loads`` for text from outside: nesting deeper than ``MAX_DEPTH`` is a ``ValueError``
    like other bad JSON, and so is a number that is not finite.

    ``json.loads("[" * 100_000)`` raises ``RecursionError``, which no ``except ValueError``
    catches: a model's answer of brackets ended a move with a traceback instead of a refusal
    (cos20 #492 S8). What the parser still took, a walk after it could not: a head's move of
    some 490 nested lists, in its 1024 bytes, ended the tick in ``head.own_words`` with neither
    the head's move nor the rules' (cos20 #492, betreuer pass c01ec84, F2). ``NaN``,
    ``Infinity`` and ``1e999`` are no JSON numbers; Python took them, and a head's move with one
    in it ended the brain where httpx refuses to send it (``allow_nan=False``; sixth review,
    finding 2). Every JSON the brain reads goes through here (guard in ``test_files``)."""
    try:
        value = json.loads(data, parse_constant=_no_constant, parse_float=_finite_float)
    except RecursionError as exc:
        raise ValueError("the JSON is nested too deep") from exc
    return _not_too_deep(value)


def _not_too_deep(value: Any) -> Any:
    if _depth(value) > MAX_DEPTH:
        raise ValueError("the JSON is nested too deep")
    return value


def _depth(value: Any) -> int:
    """How deep ``value`` nests (a scalar 0, ``[]`` 1), walked without recursion."""
    deepest, open_ = 0, [(value, 1)]
    while open_:
        item, level = open_.pop()
        if isinstance(item, dict):
            item = list(item.values())
        if isinstance(item, list):
            deepest = max(deepest, level)
            open_.extend((inner, level + 1) for inner in item)
    return deepest


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


def _kept(path: Path) -> Any:
    """What a file the brain keeps holds, or ``None``: missing, unreadable, empty, broken."""
    try:
        return loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # a byte that is no UTF-8 is a ValueError too
        return None


def read_object(path: Path) -> dict[str, Any]:
    """A JSON object the brain keeps, or ``{}``: the one way to read one (guard in test_files).

    A file that is missing, empty (a crash between writing and the disk), broken, or of another
    form counts as none; its values are then read by their type (``finite``, ``isinstance``).
    Until 0.1.14 each reader did this itself, and four did not: a ``speech.json``, a
    ``lessons.json``, a ``budget.json`` or an ``advisor.json`` of another form stopped every move
    (cos20 #492, Betreuer-Durchgang b40f78b; the class of #612 F3/F4)."""
    data = _kept(path)
    return data if isinstance(data, dict) else {}


def read_list(path: Path) -> list[Any]:
    """A JSON list the brain keeps, or ``[]`` (see ``read_object``)."""
    data = _kept(path)
    return data if isinstance(data, list) else []


def count(value: Any, default: int = 0) -> int:
    """``value`` as a whole number, or ``default``: a counter read from a file the brain keeps."""
    number = finite(value)
    return int(number) if number is not None else default


def json_in(text: str) -> Any:
    """The JSON object that begins at the first ``{`` of a model's answer, read as ``loads`` reads:
    what comes after it counts for nothing. A ``{`` before it in the text (``use {field_id}``)
    makes the answer unusable, and the rules play: the brain does not search on for an object that
    may be an example rather than the move (cos20 #492, Betreuer-Durchgang c47b7f1, B2).

    Raises ``ValueError`` if there is none or it is broken. Until 0.1.14 it took everything from
    the first ``{`` to the last ``}``: a brace closed too early, or a reason hanging behind the
    move, lost a whole move (cos20 #533, device 04.11.2026: 32 of 52 fall-backs of the head). One
    decode from the first ``{`` stays linear; ``re.search(r"\\{.*\\}", text, re.DOTALL)`` before was
    quadratic in the number of ``{`` (32,000 took 0.3 s, a 256 KiB answer some 20 s; cos20 #492
    S7)."""
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in the answer")
    try:
        value, _ = _FROM_A_MODEL.raw_decode(text, start)
    except RecursionError as exc:
        raise ValueError("the JSON is nested too deep") from exc
    return _not_too_deep(value)


def append(path: Path, entry: dict[str, Any], keep_bytes: int | None = None) -> None:
    """Add one line. With ``keep_bytes``, a file past twice that keeps only its last whole
    lines within ``keep_bytes``; without it, the file keeps everything. Waits for another writer
    ``HELD_WAIT_S`` at most, then ``TimeoutError`` (an ``OSError``)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = (json.dumps(entry) + "\n").encode("utf-8")
    while True:
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "ab") as handle:
            # released when the file closes; at most ``HELD_WAIT_S``: until af58853 a writer
            # stopped here held the running brain in its move (cos20 #492, Betreuer-Durchgang
            # af58853, B2, K24)
            _lock_within(handle.fileno(), HELD_WAIT_S)
            if not _still_there(path, handle.fileno()):
                continue  # ``keep_private`` replaced it while we waited: into the new one
            handle.write(line)
            handle.flush()
            if keep_bytes is not None and os.fstat(handle.fileno()).st_size > 2 * keep_bytes:
                _keep_last(path, keep_bytes)
            return


@contextlib.contextmanager
def lines_held(path: Path) -> Iterator[bool]:
    """The lock ``append`` takes on a JSON-lines file, held for a read of it and a write of what
    is shown of it (``True``); ``False``, and no lock, while the file is not there yet.

    The bar's view of the journal and the talk was read and written outside any lock: a view
    read before another process added its line could be written after that process's view, and
    the bar lacked the line until the next one (cos20 #492, Betreuer-Durchgang b964644, B3).
    Under this lock the view written last has read every line added before it. At most
    ``HELD_WAIT_S``, then ``TimeoutError`` (an ``OSError``), as ``append``."""
    while True:
        try:
            fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
        except FileNotFoundError:
            yield False
            return
        try:
            _lock_within(fd, HELD_WAIT_S)  # released when the file closes
            if _still_there(path, fd):  # else ``keep_private`` replaced it: the new one
                yield True
                return
        finally:
            os.close(fd)


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
