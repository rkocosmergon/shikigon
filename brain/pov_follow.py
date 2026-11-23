"""The follower of the terminal's first-person view.

The Cosmergon dashboard runs inside a ``foot`` window and can start a native renderer whose window
(class ``com.cosmergon.pov``) shall lie exactly over one rectangular area of the dashboard. The
dashboard says where that area is in ``$XDG_RUNTIME_DIR/cosmergon/pov-place.json``; this program
puts the renderer's window there and keeps it there. It works under Hyprland only and talks to it
over its two sockets; anywhere else it ends at once. It is started by ``shikigon-terminal`` and
ends with the process that started it.

The core is three pure functions (``read_place``, ``read_pad``, ``target``) and ``decide``, which
turns what the dashboard and Hyprland say into the requests to send. The loop (``step``,
``main``) is thin. What it touches, as the security core (tested in ``test_pov_follow``): the
terminal window is the client whose process is the PARENT of the dashboard, the renderer window
a client of class ``com.cosmergon.pov`` whose process is a CHILD of the dashboard. A window of any
other process is never touched. No text from the file or from a window title reaches a request:
an address only if it is ``0x`` and hex digits, a workspace name only from a short alphabet, every
number from a validated whole number. A ``foot --server`` with several windows is not served:
all its windows belong to one process, so the terminal's window cannot be told safely and
nothing is done.

Units: Hyprland gives ``at`` and ``size`` of a client in logical pixels (physical pixels divided
by the monitor's scale); the dashboard's pixel numbers are those of the terminal's own surface,
physical. ``target`` converts, rounding inwards so the window never stands beyond the area.

Hyprland sends no event for a move or resize of a window. A resize of the terminal changes the
dashboard's cell grid and so its place file; that file is watched by inotify (where the C library
offers it) and seen within a millisecond or two, else every 50 ms. A move of a floating terminal
has no such sign and is seen when the window is looked at again, once a second, or at once on each
event that can change where it belongs.
"""

from __future__ import annotations

import json
import math
import os
import re
import select
import signal
import socket
import stat
import struct
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

# The brain never writes into the plugin's folder (see shikigon_brain.py), and under ``-I`` Python
# leaves the script's folder off sys.path: its own folder is put there, as the brain does.
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import files

PLACE_BYTES = 4096  # the dashboard's line is about 200 bytes
FOLLOW_BYTES = 4096  # what the dashboard reads of the presence file, at most
ENDING = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)  # after these the file goes, too
FOOT_INI_BYTES = 65536
RENDERER_CLASS = "com.cosmergon.pov"
PARKING = "special:cosmergon-pov"  # where the window waits while it is not to be seen
WAKE = 0.05  # at most this long between two looks at the place file (where there is no inotify)
IDLE_WAKE = 0.25  # with inotify the file needs no look: this long between two checks of the parent
RECHECK = 1.0  # a move or resize makes no event: look again at least this often
SOCKET_TIMEOUT = 0.5
ANSWER_BYTES = 4 * 1024 * 1024  # a long list of clients; more is no answer of Hyprland
EVENT_BUFFER = 65536
# The events after which a window may belong elsewhere.
WATCHED = frozenset({
    "openwindow", "closewindow", "movewindowv2", "workspace", "workspacev2", "fullscreen",
    "changefloatingmode", "activewindowv2", "activespecial", "configreloaded",
    "monitoradded", "monitorremoved",
})
RULE = (
    'eval hl.window_rule({ match = { class = "^com\\\\.cosmergon\\\\.pov$" }, float = true, '
    "no_focus = true, no_initial_focus = true, border_size = 0, no_shadow = true, "
    "no_anim = true })"
)
SCALE_RANGE = (0.5, 4.0)  # a monitor scale outside this is not followed
TRACE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
TRACE_ENV = "COSMERGON_POV_FOLLOW_TRACE"
# inotify: close-after-write, moved-to, created, deleted; the watched folder itself gone or moved
IN_PLACE_MASK = 0x8 | 0x80 | 0x100 | 0x200
IN_FOLDER_GONE = 0x400 | 0x800 | 0x8000  # DELETE_SELF, MOVE_SELF, IGNORED
IN_OVERFLOW = 0x4000
ADDRESS = re.compile(r"0x[0-9a-f]{1,16}")
WORKSPACE = re.compile(r"[A-Za-z0-9:_-]{1,64}")
SIGNATURE = re.compile(r"[0-9a-f]{6,64}(_[0-9]{1,20}){0,2}")
KEYS = frozenset({
    "v", "pid", "visible", "col", "row", "cols", "rows", "cell_w", "cell_h",
    "term_cols", "term_rows", "text_w", "text_h",
})


@dataclass(frozen=True)
class Place:
    """The area the dashboard names, in cells of its terminal (0-based from the top left)."""

    pid: int
    visible: bool
    col: int
    row: int
    cols: int
    rows: int
    cell_w: int
    cell_h: int
    term_cols: int
    term_rows: int
    text_w: int
    text_h: int


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int


def _whole(value: Any, low: int, high: int) -> bool:
    """``value`` is an ``int`` (not a ``bool``, not a float) from ``low`` to ``high``."""
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def read_place(text: str | bytes) -> Place | None:
    """The dashboard's line as a ``Place``, or ``None`` for anything that is not exactly it:
    more than 4096 bytes, not an object, other keys, ``v`` not 1, a number that is no whole number
    in its bounds, an area that leaves the terminal's cell grid, a grid wider than its pixels."""
    raw = text if isinstance(text, bytes) else text.encode("utf-8", "replace")
    if len(raw) > PLACE_BYTES:
        return None
    try:
        data = files.loads(raw.decode("utf-8"))
    except ValueError:  # includes JSON errors and a depth past the limit
        return None
    if not isinstance(data, dict) or set(data) != KEYS:
        return None
    if not _whole(data["v"], 1, 1) or not isinstance(data["visible"], bool):
        return None
    cells = ("col", "row", "cols", "rows", "term_cols", "term_rows")
    if not (_whole(data["pid"], 2, 4_194_304)
            and all(_whole(data[k], 0, 2000) for k in cells)
            and all(_whole(data[k], 1, 200) for k in ("cell_w", "cell_h"))
            and all(_whole(data[k], 1, 20_000) for k in ("text_w", "text_h"))):  # fmt: skip
        return None
    if (data["col"] + data["cols"] > data["term_cols"]
            or data["row"] + data["rows"] > data["term_rows"]):  # fmt: skip
        return None
    if data["cell_w"] * data["term_cols"] > data["text_w"]:
        return None
    if data["cell_h"] * data["term_rows"] > data["text_h"]:
        return None
    return Place(**{k: data[k] for k in KEYS if k != "v"})


_SECTION = re.compile(r"\[\s*([^\]]*?)\s*\]")
_PAD = re.compile(r"pad\s*=\s*([0-9]{1,4})x([0-9]{1,4})(?:\s+(center))?")


def read_pad(foot_ini_text: str) -> tuple[int, int, bool]:
    """``pad=<x>x<y>[ center]`` of the ``[main]`` section of foot's ini: ``(x, y, center)``.
    The last one wins; comments (``#``), other sections, a value that is no pad or lies outside
    0..500 are skipped. Without one: ``(0, 0, False)``."""
    found = (0, 0, False)
    in_main = False
    for line in foot_ini_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        section = _SECTION.fullmatch(line)
        if section:
            in_main = section.group(1) == "main"
            continue
        match = _PAD.fullmatch(line) if in_main else None
        if match and int(match.group(1)) <= 500 and int(match.group(2)) <= 500:
            found = (int(match.group(1)), int(match.group(2)), match.group(3) == "center")
    return found


def target(place: Place, term_at: tuple[int, int], term_size: tuple[int, int],
           pad: tuple[int, int, bool], scale: Fraction = Fraction(1)) -> Rect | None:
    """Where the area is on the screen, in the logical pixels Hyprland uses: the terminal window's
    corner, the margin to its cell grid (the pad; with ``center`` half of what the window is wider
    or higher than the grid), then ``col`` and ``row`` in cells. The dashboard's numbers (cells,
    text size) and the pad are in the terminal's physical pixels, the window's ``term_at`` and
    ``term_size`` in logical ones (physical divided by ``scale``). The start rounds up and the
    end down, so the rectangle lies inside the area. ``None`` if that is empty or not inside the
    terminal window."""
    pad_x, pad_y, center = pad
    if center:
        pad_x = (round(term_size[0] * scale) - place.text_w) // 2
        pad_y = (round(term_size[1] * scale) - place.text_h) // 2
    else:
        pad_x, pad_y = round(pad_x * scale), round(pad_y * scale)
    x0, y0 = pad_x + place.col * place.cell_w, pad_y + place.row * place.cell_h
    x1, y1 = x0 + place.cols * place.cell_w, y0 + place.rows * place.cell_h
    left, top = term_at[0] + math.ceil(x0 / scale), term_at[1] + math.ceil(y0 / scale)
    rect = Rect(left, top, term_at[0] + math.floor(x1 / scale) - left,
                term_at[1] + math.floor(y1 / scale) - top)
    inside = (rect.x >= term_at[0] and rect.y >= term_at[1]
              and rect.x + rect.w <= term_at[0] + term_size[0]
              and rect.y + rect.h <= term_at[1] + term_size[1])  # fmt: skip
    return rect if rect.w > 0 and rect.h > 0 and inside else None


# --- what Hyprland says, in the forms decide reads ----------------------------------------------


@dataclass(frozen=True)
class Win:
    address: str | None  # ``None``: not of the form the requests allow, never named in one
    cls: str
    pid: int
    at: tuple[int, int]
    size: tuple[int, int]
    ws_id: int
    ws_name: str
    monitor: int
    fullscreen: bool  # real fullscreen (mode 2 or 3); a maximized window (mode 1) is not
    shown: bool  # mapped and not hidden


def _pair(value: Any) -> tuple[int, int] | None:
    if (isinstance(value, list) and len(value) == 2
            and all(_whole(v, -1_000_000, 1_000_000) for v in value)):  # fmt: skip
        return (value[0], value[1])
    return None


def _win(raw: Any) -> Win | None:
    """A client as ``Win``, or ``None`` if a field is missing or of another type."""
    if not isinstance(raw, dict):
        return None
    ws = raw.get("workspace")
    at, size = _pair(raw.get("at")), _pair(raw.get("size"))
    address, cls = raw.get("address"), raw.get("class")
    if (not isinstance(ws, dict) or at is None or size is None or not isinstance(cls, str)
            or not _whole(raw.get("pid"), 0, 2**31) or not _whole(raw.get("monitor"), -1, 10_000)
            or not _whole(ws.get("id"), -2**31, 2**31) or not isinstance(ws.get("name"), str)
            or not isinstance(raw.get("mapped"), bool) or not isinstance(raw.get("hidden"), bool)
            or not isinstance(raw.get("fullscreen"), int)):  # fmt: skip
        return None
    return Win(address if isinstance(address, str) and ADDRESS.fullmatch(address) else None,
               cls, raw["pid"], at, size, ws["id"], ws["name"], raw["monitor"],
               _real_fullscreen(raw["fullscreen"]), raw["mapped"] and not raw["hidden"])


def _real_fullscreen(value: int) -> bool:
    """Hyprland's ``fullscreen`` of a client: 0 none, 1 maximized, 2 fullscreen, 3 both (a bool in
    older versions). Only a mode that hides the rest of the workspace counts; a value that is none
    of these counts as fullscreen (the cautious reading)."""
    if isinstance(value, bool):
        return value
    return value not in (0, 1)


@dataclass(frozen=True)
class Screen:
    active: int  # id of the shown workspace
    special: int  # id of the shown special workspace, 0 if none
    scale: Fraction | None  # the monitor's scale; ``None`` if it is not a plausible number


def _screens(monitors: Any) -> dict[int, Screen]:
    found: dict[int, Screen] = {}
    for raw in monitors if isinstance(monitors, list) else []:
        if not isinstance(raw, dict):
            continue
        active, special = raw.get("activeWorkspace"), raw.get("specialWorkspace")
        scale = raw.get("scale")
        if (_whole(raw.get("id"), -1, 10_000) and isinstance(active, dict)
                and isinstance(special, dict) and _whole(active.get("id"), -2**31, 2**31)
                and _whole(special.get("id"), -2**31, 2**31)):  # fmt: skip
            found[raw["id"]] = Screen(active["id"], special["id"], _scale(scale))
    return found


def _scale(value: Any) -> Fraction | None:
    """The monitor's scale as an exact fraction (Hyprland prints 1.666667 for 5/3), or ``None``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not SCALE_RANGE[0] <= value <= SCALE_RANGE[1]:  # also false for nan
        return None
    return Fraction(value).limit_denominator(1000)


def _num(value: int) -> str:
    """A number for a request: only a whole number goes into one (never a bool, never text)."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError("not a whole number")
    return str(value)


def _window_call(call: str, args: str, address: str) -> str:
    if not ADDRESS.fullmatch(address):
        raise ValueError("not an address")
    return f'dispatch hl.dsp.window.{call}({{ {args}window = "address:{address}" }})'


def _commands(win: Win, rect: Rect | None, ws_name: str | None) -> list[str]:
    """The requests that bring ``win`` to ``rect`` on ``ws_name``, or to the parking place if
    ``rect`` is ``None``. Resize before move: a resize keeps the window's centre."""
    assert win.address is not None
    if rect is None:
        if win.ws_name == PARKING:
            return []
        return [_window_call("move", f'workspace = "{PARKING}", follow = false, ', win.address)]
    commands: list[str] = []
    moved = ws_name is not None and win.ws_name != ws_name
    if moved:
        assert ws_name is not None and WORKSPACE.fullmatch(ws_name)
        commands.append(_window_call(
            "move", f'workspace = "{ws_name}", follow = false, ', win.address))
    if moved or win.size != (rect.w, rect.h):
        commands.append(_window_call(
            "resize", f"x = {_num(rect.w)}, y = {_num(rect.h)}, ", win.address))
    if moved or win.at != (rect.x, rect.y):
        commands.append(_window_call(
            "move", f"x = {_num(rect.x)}, y = {_num(rect.y)}, ", win.address))
    return commands


def _terminal(wins: list[Win], terminal_pid: int) -> Win | None:
    """The one window of the process that is the dashboard's parent; with none or several (a
    ``foot --server``) ``None``, never a guess."""
    candidates = [w for w in wins if w.pid == terminal_pid]
    return candidates[0] if len(candidates) == 1 else None


def decide(place: Place, clients: Any, monitors: Any, pad: tuple[int, int, bool],
           ppid_of: Callable[[int], int | None]) -> list[str]:
    """The requests to send (none if nothing is to do).

    The terminal is the client whose process is the parent of the dashboard (``place.pid``; with
    several windows of it: nothing), the renderer a client of class ``com.cosmergon.pov``
    whose parent process is the dashboard (``ppid_of`` reads the parent of a process). The
    renderer is shown where the area is while the dashboard wants it, its terminal is on a
    workspace the monitor shows, the monitor's scale is plausible and no other window on that
    workspace is in real fullscreen; else it waits on a special workspace. A window of any other
    process is never named in a request."""
    terminal_pid = ppid_of(place.pid)
    if terminal_pid is None or terminal_pid < 2 or not isinstance(clients, list):
        return []
    wins = [w for w in map(_win, clients) if w is not None]
    term = _terminal(wins, terminal_pid)
    if term is None:
        return []
    renderers = [w for w in wins if w.cls == RENDERER_CLASS and w.shown and w.address is not None
                 and ppid_of(w.pid) == place.pid]  # fmt: skip
    screen = _screens(monitors).get(term.monitor)
    if not renderers or screen is None or screen.scale is None:
        return []  # no monitor to read the scale from: nothing is tried
    rect: Rect | None = None
    if (place.visible and term.shown and term.ws_id in (screen.active, screen.special)
            and not any(w.fullscreen and w.shown and w.ws_id == term.ws_id
                        and w is not term and w not in renderers for w in wins)):  # fmt: skip
        rect = target(place, term.at, term.size, pad, screen.scale)
    ws_name = term.ws_name if WORKSPACE.fullmatch(term.ws_name) else None
    commands: list[str] = []
    for win in renderers:
        if rect is not None and ws_name is None and win.ws_id != term.ws_id:
            continue  # on another workspace than the terminal and no name to bring it by
        commands += _commands(win, rect, ws_name if win.ws_id != term.ws_id else None)
    if len(commands) > 1:
        return ["[[BATCH]]" + ";".join(commands)]
    return commands


def proc_ppid(pid: int) -> int | None:
    """The parent of process ``pid`` from field 4 of ``/proc/<pid>/stat``, or ``None``. The name
    (field 2) stands in brackets and may hold spaces and brackets: split after the LAST ``)``."""
    try:
        with open(f"/proc/{int(pid)}/stat", "rb") as stat_file:
            text = stat_file.read(4096).decode("utf-8", "replace")
        return int(text[text.rindex(")") + 1:].split()[1])
    except (OSError, ValueError, IndexError):
        return None


# --- the loop -----------------------------------------------------------------------------------


@dataclass
class Follower:
    request_path: str
    events_path: str
    place_path: str
    foot_path: str
    ppid: int  # the parent when this started: when it changes, this ends
    ppid_of: Callable[[int], int | None] = proc_ppid
    place_sig: tuple[int, int] | None = None
    foot_sig: tuple[int, int] | None = None
    pad: tuple[int, int, bool] = (0, 0, False)
    checked: float = float("-inf")
    rule_due: bool = True
    rule_next: float = 0.0  # a rule Hyprland did not take is tried again after this time
    events: socket.socket | None = None
    events_retry: float = 0.0
    buffer: bytes = b""
    reported: set[str] = field(default_factory=set)
    follow_path: str = ""  # the presence file the dashboard looks for; empty: none is written
    stop: bool = False  # set by a signal: the loop ends and the file goes
    trace_path: str = ""  # where to write the timing of each re-placement; empty: nowhere
    watch: int | None = None  # inotify descriptor on the place file's folder
    watch_retry: float = 0.0
    woke: float = 0.0  # monotonic time the last event (socket or folder) was seen; 0: none


def report(state: Follower, kind: str, why: object) -> None:
    """One line on stderr for each kind of failure, not for each pass of the loop."""
    if kind in state.reported:
        return
    state.reported.add(kind)
    try:
        sys.stderr.write(f"pov_follow: {kind}: {why}\n")
        sys.stderr.flush()
    except OSError:
        pass


def ask(path: str, request: str) -> str:
    """One request over Hyprland's socket: send, read to the end. The whole answer within
    ``SOCKET_TIMEOUT``, at most ``ANSWER_BYTES``; ``OSError`` otherwise."""
    deadline = time.monotonic() + SOCKET_TIMEOUT
    chunks: list[bytes] = []
    total = 0
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(SOCKET_TIMEOUT)
        sock.connect(path)
        sock.sendall(request.encode("utf-8"))
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError("no answer in time")
            sock.settimeout(left)
            data = sock.recv(65536)
            if not data:
                break
            total += len(data)
            if total > ANSWER_BYTES:
                raise OSError("the answer is too long")
            chunks.append(data)
    return b"".join(chunks).decode("utf-8", "replace")


def _signature(path: str) -> tuple[int, int] | None:
    try:
        info = os.lstat(path)
    except OSError:
        return None
    return (info.st_mtime_ns, info.st_ino)


def read_small(path: str, limit: int) -> bytes | None:
    """The first ``limit`` + 1 bytes of a regular file of this user that no one else may write
    (never through a link), else ``None``."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
            return None
        return os.read(fd, limit + 1)
    except OSError:
        return None
    finally:
        os.close(fd)


def connect_events(state: Follower, now: float) -> None:
    """The event socket, if it is not open and the pause since the last try is over."""
    if state.events is not None or now < state.events_retry:
        return
    state.events_retry = now + 1.0
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(SOCKET_TIMEOUT)
        sock.connect(state.events_path)
        sock.setblocking(False)
    except OSError as exc:
        sock.close()
        report(state, "events", exc)
        return
    state.events = sock
    state.buffer = b""
    state.checked = float("-inf")  # events may have passed while it was shut: look at once


def open_watch(folder: str) -> int | None:
    """A non-blocking inotify descriptor watching ``folder`` for files made, replaced or removed,
    or ``None`` where the C library has none (the place file is then looked at every ``WAKE``).
    ``ctypes`` is of the standard library."""
    try:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        fd = int(libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC))
        if fd < 0:
            return None
        if int(libc.inotify_add_watch(fd, os.fsencode(folder), IN_PLACE_MASK | IN_FOLDER_GONE)) < 0:
            os.close(fd)
            return None
        return fd
    except (OSError, AttributeError, ImportError, ValueError):
        return None


def connect_watch(state: Follower, now: float) -> None:
    """The watch on the place file's folder, if there is none and the pause since the last try is
    over (the folder may not exist yet, or may have been replaced)."""
    if state.watch is not None or now < state.watch_retry:
        return
    state.watch_retry = now + 1.0
    state.watch = open_watch(os.path.dirname(state.place_path))
    if state.watch is not None:
        state.checked = float("-inf")  # the file may have changed before the watch: look at once


def _drain_watch(state: Follower) -> bool:
    """Read what inotify says; ``True`` if the place file was touched (or events were lost). The
    watch is closed when its folder is gone or moved (``connect_watch`` makes a new one)."""
    assert state.watch is not None
    try:
        data = os.read(state.watch, 65536)
    except BlockingIOError:
        return False
    except OSError:
        data = b""
    name_wanted = os.path.basename(state.place_path).encode()
    hit, offset = False, 0
    while offset + 16 <= len(data):
        _, mask, _, length = struct.unpack_from("iIII", data, offset)
        name = data[offset + 16:offset + 16 + length].split(b"\0", 1)[0]
        hit = hit or name == name_wanted or bool(mask & IN_OVERFLOW)
        if mask & IN_FOLDER_GONE:
            hit = True
            data = b""
        offset += 16 + length
    if not data:
        os.close(state.watch)
        state.watch = None
        hit = True
    return hit


def _read_events(state: Follower) -> set[str]:
    assert state.events is not None
    try:
        data = state.events.recv(65536)
    except BlockingIOError:
        return set()
    except OSError:
        data = b""
    if not data:
        state.events.close()
        state.events = None
        return set()
    state.buffer += data
    *lines, state.buffer = state.buffer.split(b"\n")
    if len(state.buffer) > EVENT_BUFFER:
        state.buffer = b""
    names = {line.split(b">>", 1)[0].decode("ascii", "replace") for line in lines}
    return names & WATCHED


def wait(state: Follower) -> set[str]:
    """Wait for an event of Hyprland or a change in the place file's folder; the names of the
    watched events that came (``place`` for the file). Up to ``WAKE`` (``IDLE_WAKE`` where inotify
    serves, which then needs no look at the file). A broken event socket is closed
    (``connect_events`` opens it again after a pause)."""
    now = time.monotonic()
    connect_events(state, now)
    connect_watch(state, now)
    poller = select.poll()
    for source in (state.events, state.watch):
        if source is not None:
            poller.register(source, select.POLLIN)
    if state.events is None and state.watch is None:
        time.sleep(WAKE)
        return set()
    ready = {fd for fd, _ in poller.poll((WAKE if state.watch is None else IDLE_WAKE) * 1000)}
    if not ready:
        return set()
    state.woke = time.monotonic()
    names: set[str] = set()
    if state.watch is not None and state.watch in ready and _drain_watch(state):
        names.add("place")
    if state.events is not None and state.events.fileno() in ready:
        names |= _read_events(state)
    return names


def _list(state: Follower, request: str) -> Any:
    return files.loads(ask(state.request_path, request))


def _pad(state: Follower) -> tuple[int, int, bool]:
    sig = _signature(state.foot_path)
    if sig != state.foot_sig:
        state.foot_sig = sig
        raw = read_small(state.foot_path, FOOT_INI_BYTES) if sig else None
        state.pad = read_pad(raw.decode("utf-8", "replace")) if raw is not None else (0, 0, False)
    return state.pad


def _rule(state: Follower, names: set[str], now: float) -> None:
    """Send the window rule if it is due (it must exist before the renderer's window opens)."""
    try:
        if "configreloaded" in names:
            state.rule_due, state.rule_next = True, 0.0  # the reload dropped it: at once
        if state.rule_due and now >= state.rule_next:
            state.rule_next = now + RECHECK
            if ask(state.request_path, RULE).strip() != "ok":
                raise OSError("Hyprland did not take the window rule")
            state.rule_due = False
    except Exception as exc:  # noqa: BLE001 - the loop must go on
        report(state, "rule", exc)


def _cause(state: Follower, sig: tuple[int, int] | None, changed: bool,
           names: set[str]) -> tuple[str, float]:
    """What made this pass look, and when that happened (monotonic seconds): the place file's own
    modification time, else the wake-up on an event, else now."""
    mono, wall = time.monotonic(), time.time_ns()
    if changed and sig is not None:
        return "place", mono - max(0.0, (wall - sig[0]) / 1e9)
    if names:
        return min(names), state.woke if 0.0 < state.woke <= mono else mono
    return "recheck", mono


def trace(state: Follower, kind: str, began: float, done: float) -> None:
    """One line in the trace file: what caused the re-placement and the monotonic times of the
    cause and of Hyprland's last answer. Only when ``COSMERGON_POV_FOLLOW_TRACE`` named a file at
    the start. Never raises."""
    if not state.trace_path:
        return
    line = json.dumps({"kind": kind, "t0": round(began, 6), "t1": round(done, 6)}) + "\n"
    try:
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
        fd = os.open(state.trace_path, flags, 0o600)
        try:
            info = os.fstat(fd)
            ours = stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
            if ours and not info.st_mode & 0o077:
                os.write(fd, line.encode())
        finally:
            os.close(fd)
    except OSError as exc:
        report(state, "trace", exc)


def _place(state: Follower, names: set[str], now: float) -> list[str]:
    """If an event came, the place file changed or a second has passed: read the file and, if
    there is a view, ask Hyprland and send what ``decide`` says. The requests sent."""
    sig = _signature(state.place_path)
    changed = sig != state.place_sig
    if not (names or changed or now - state.checked >= RECHECK):
        return []  # the idle wake: nothing was asked, nothing is read
    kind, began = _cause(state, sig, changed, names)
    state.place_sig, state.checked = sig, now
    raw = read_small(state.place_path, PLACE_BYTES) if sig else None
    if raw is None:
        return []
    place = read_place(raw)
    if place is None:
        report(state, "place", "the dashboard's file is not the one expected")
        return []
    sent: list[str] = []
    requests = decide(place, _list(state, "j/clients"), _list(state, "j/monitors"),
                      _pad(state), state.ppid_of)
    for request in requests:
        sent.append(request)
        answer = ask(state.request_path, request)
        if any(part.strip() != "ok" for part in answer.split("\n\n\n")):
            report(state, "refused", answer[:80].replace("\n", " "))
    if sent:
        trace(state, kind, began, time.monotonic())
    return sent


def step(state: Follower, names: Iterable[str], now: float) -> list[str]:
    """One wake: send the window rule if it is due; if an event came, the place file changed or
    a second has passed, read the place file and, if there is one, ask Hyprland and send what
    ``decide`` says. The requests sent. Never raises: a failure is said once per kind."""
    names = set(names)
    _rule(state, names, now)
    sent: list[str] = []
    try:
        sent = _place(state, names, now)
    except Exception as exc:  # noqa: BLE001 - the loop must go on
        report(state, type(exc).__name__, exc)
    state.woke = 0.0
    return sent


def start() -> Follower | None:
    """The follower for this environment, or ``None`` (not Hyprland, or not ours to follow)."""
    runtime = os.environ.get("XDG_RUNTIME_DIR", "")
    signature = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "")
    if not os.path.isabs(runtime) or not SIGNATURE.fullmatch(signature) or os.getppid() <= 1:
        return None
    try:
        folder = os.stat(runtime)
        request = os.path.join(runtime, "hypr", signature, ".socket.sock")
        sock = os.stat(request)
    except OSError:
        return None
    if not stat.S_ISDIR(folder.st_mode) or folder.st_uid != os.getuid():
        return None
    if not stat.S_ISSOCK(sock.st_mode) or sock.st_uid != os.getuid():
        return None
    foot = files.xdg_home("XDG_CONFIG_HOME", Path.home() / ".config") / "foot" / "foot.ini"
    return Follower(request, os.path.join(runtime, "hypr", signature, ".socket2.sock"),
                    os.path.join(runtime, "cosmergon", "pov-place.json"), str(foot),
                    os.getppid(), follow_path=os.path.join(runtime, "cosmergon", "pov-follow.json"),
                    trace_path=_trace_path(runtime))


def _trace_path(runtime: str) -> str:
    """The trace file, if ``COSMERGON_POV_FOLLOW_TRACE`` names one: a plain file name (no folder,
    not one of the two files of the view) that is put in the dashboard's runtime folder, which
    must be ours and closed to others. Else empty."""
    name = os.environ.get(TRACE_ENV, "")
    if not TRACE_NAME.fullmatch(name) or name in ("pov-place.json", "pov-follow.json"):
        return ""
    folder = Path(runtime) / "cosmergon"
    try:
        return str(folder / name) if _own_folder(folder) else ""
    except OSError:
        return ""


def announce(state: Follower) -> None:
    """Say in ``pov-follow.json`` that this follower runs (the dashboard starts the renderer only
    then): ``{"version": 1, "pid": <own>}``, 0600, put in place by a rename (``files.write_json``:
    a new file of its own, so a link at the path is replaced, never followed). The folder is made
    0700 if it is missing; one that is a link, not ours or open to others gets nothing written.
    Never raises: a failure is said once."""
    if not state.follow_path:
        return
    path = Path(state.follow_path)
    try:
        if _own_folder(path.parent):
            files.write_json(path, {"version": 1, "pid": os.getpid()})
    except Exception as exc:  # noqa: BLE001 - the follower goes on without it
        report(state, "announce", exc)


def _own_folder(folder: Path) -> bool:
    """``folder`` is a real folder of this user that only this user may enter; made (0700) if it
    is missing. ``lstat``: a link in its place is not one."""
    try:
        info = os.lstat(folder)
    except FileNotFoundError:
        files.private_folder(folder)  # the one place that makes a folder (0700)
        info = os.lstat(folder)
    return stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077


def withdraw(state: Follower) -> None:
    """Remove the presence file, but only while it still carries this process's pid: a newer
    follower keeps its own. A file that is not ours to read, or is gone, is left. Never raises."""
    if not state.follow_path:
        return
    try:
        raw = read_small(state.follow_path, FOLLOW_BYTES)
        data = files.loads(raw.decode("utf-8")) if raw is not None else None
        if isinstance(data, dict) and data.get("pid") == os.getpid():
            os.unlink(state.follow_path)
    except Exception as exc:  # noqa: BLE001 - the end must not fail on it
        report(state, "withdraw", exc)


def _signals(state: Follower) -> dict[int, Any]:
    """Make SIGTERM, SIGINT and SIGHUP end the loop (``state.stop``) instead of the process, so
    the file is removed. The handlers that stood before, for ``_restore``. Where signals cannot be
    set (not the main thread) nothing is changed."""

    def handler(signum: int, frame: Any) -> None:
        state.stop = True

    before: dict[int, Any] = {}
    for sig in ENDING:
        try:
            before[sig] = signal.signal(sig, handler)
        except (ValueError, OSError):
            pass
    return before


def _restore(before: dict[int, Any]) -> None:
    for sig, old in before.items():
        try:
            signal.signal(sig, old)
        except (ValueError, OSError, TypeError):
            pass


def run(state: Follower) -> int:
    """Until the process that started this is gone (its parent changes, or is init) or a signal
    says to end. The presence file is there meanwhile and removed at the end."""
    before = _signals(state)
    try:
        announce(state)
        while os.getppid() == state.ppid and not state.stop:
            try:
                names = wait(state)
                step(state, names, time.monotonic())
            except Exception as exc:  # noqa: BLE001 - never out of the loop
                report(state, "loop:" + type(exc).__name__, exc)
                time.sleep(WAKE)
    finally:
        withdraw(state)
        _restore(before)
    return 0


def summarize(text: str) -> str | None:
    """From a trace file's lines: ``n=<count> median=<ms> p90=<ms> max=<ms>`` of the time from
    the cause to Hyprland's answer; lines that are not ours are skipped; ``None`` without one."""
    spans: list[float] = []
    for line in text.splitlines():
        try:
            data = files.loads(line)
            spans.append((float(data["t1"]) - float(data["t0"])) * 1000.0)
        except (ValueError, KeyError, TypeError):
            continue
    if not spans:
        return None
    spans.sort()
    p90 = spans[min(len(spans) - 1, math.ceil(0.9 * len(spans)) - 1)]
    middle = (spans[(len(spans) - 1) // 2] + spans[len(spans) // 2]) / 2
    return f"n={len(spans)} median={middle:.1f}ms p90={p90:.1f}ms max={spans[-1]:.1f}ms"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ["--trace-summary"]:
        try:
            with open(args[1], encoding="utf-8", errors="replace") as handle:
                result = summarize(handle.read())
        except (IndexError, OSError) as exc:
            print(f"pov_follow: {exc}", file=sys.stderr)
            return 2
        print(result or "no lines")
        return 0 if result else 1
    state = start()
    return 0 if state is None else run(state)


if __name__ == "__main__":
    sys.exit(main())
