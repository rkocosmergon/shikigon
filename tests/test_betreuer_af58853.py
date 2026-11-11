"""Guards for the Betreuer-Durchgang af58853 (cos20 #492, plan 6703734966), B1–B4.

One family: a process of the player's own holds a lock and is stopped (Ctrl+Z, SIGSTOP).

B1: the booking of a move that had gone out waited for ``files.held``; past its deadline it was
lost, and the head's next move went out with the whole rest again (K45). Now a file only moves
write (the learning budget) needs no second lock inside a move: ``files.in_move_or_held``.

B2: ``files.append`` and ``files._renew`` waited for their ``flock`` without a deadline (K24).
Now every ``flock`` goes through ``files._lock_within``.

B3: a ``tick`` by hand that does not end held every move of the running brain, and the panel
showed the last move as if nothing waited. Now its last sentence says so.

B4: open terminal went through Omarchy's launch-or-focus starter, which brings any window whose
class **or title** matches to the front, a web page's too. Now ``omarchy-launch-tui``: no focus by
title, and the path reaches the terminal as one argument, whatever the folder is called.

Red on af58853, the positive controls green.
"""

from __future__ import annotations

import ast
import json
import re
import shlex
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
BRAIN = ROOT / "brain"
QML = ROOT / "qml"
sys.path.insert(0, str(BRAIN))

import files
import head
import shikigon_brain as brain
from test_betreuer_1d81304 import REST, WAIT, BookingWorld, _gift_head, _holder, _spent
from test_betreuer_c47b7f1 import BUDGET, TOTAL
from test_brain import CountingDecider, FakeAgent
from test_brain import _state as _brain_state
from test_effect import _with_total


@pytest.fixture(autouse=True)
def _private_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


# ---------- B1: a move that went out keeps its booking -------------------------------------------

LOSS = REST / 5  # what the world books for the head's transfer


class StallingWorld(BookingWorld):
    """Books the move, and meanwhile another process of the player's takes the folder's lock
    and is stopped there: from now on, every wait for it ends at its deadline."""

    holder: subprocess.Popen[str] | None = None

    async def act(self, action: str, **params: Any) -> Any:  # type: ignore[override]
        self.holder = _holder("held", WAIT * 4 + 2)
        return await super().act(action, **params)


async def test_b1_a_move_out_is_booked_also_while_another_process_holds_the_folder() -> None:
    """The move went out: its booking, its journal line and its status stand, and the next move
    sees the smaller rest. On af58853 the booking ran into the deadline and was lost."""
    world = StallingWorld(_with_total(TOTAL), LOSS)
    try:
        last = await brain.tick(world, _gift_head())
    finally:
        if world.holder is not None:
            world.holder.kill()
            world.holder.wait()
    assert last["action"] == "transfer_energy" and world.acted, last
    assert brain.NOT_WRITTEN not in str(last.get("why")), last
    assert _spent() == LOSS
    assert head.budget_left(brain.state_dir(), BUDGET, TOTAL, last["advice"]["day"]) == (
        REST - LOSS)
    journal = (brain.state_dir() / "journal.jsonl").read_text().splitlines()
    assert json.loads(journal[-1])["action"] == "transfer_energy"


async def test_b1_control_without_another_process_the_same_booking() -> None:
    world = BookingWorld(_with_total(TOTAL), LOSS)
    last = await brain.tick(world, _gift_head())
    assert last["action"] == "transfer_energy" and _spent() == LOSS


def test_b1_outside_a_move_the_budget_still_waits_for_the_folder() -> None:
    """``in_move_or_held`` skips the folder's lock only inside this process's own move."""
    brain.state_dir().mkdir(parents=True, exist_ok=True)
    child = _holder("held", WAIT * 4 + 2)
    try:
        with pytest.raises(TimeoutError), files.in_move_or_held(brain.state_dir()):
            pass
        with files.one_move(brain.state_dir()) as mine:
            assert mine
            began = time.monotonic()
            with files.in_move_or_held(brain.state_dir()):
                pass
            assert time.monotonic() - began < 0.5, "in its own move nothing waits"
        with pytest.raises(TimeoutError), files.in_move_or_held(brain.state_dir()):
            pass  # the move is over
    finally:
        child.kill()
        child.wait()


# Where the folder's lock (``files.held``) may still be waited for: files that are also written
# outside a move. Each with its reason; everything a move alone writes is ``in_move_or_held``.
HELD_PLACES = {
    ("advisor.py", "take_call"): "the day's calls: questions from the terminal and look backs too",
    ("advisor.py", "count_move"): "beside the day's calls, in the same file",
    ("lessons.py", "keep"): "the lessons: a look back runs beside the moves",
    ("shikigon_brain.py", "change_choice"): (
        "the advisor's choice: the panel's try and advise run as processes of their own, never in "
        "a move (Betreuer-Durchgang 4866b11, V1)"),
    ("shikigon_brain.py", "advisor_command"): (
        "forget and a key typed in the panel: the advisor's files, beside change_choice, never in "
        "a move (Betreuer-Durchgang b964644, B2)"),
}


def _calls(sources: dict[str, str], wanted: set[str]) -> list[tuple[str, str, str, int]]:
    """(file, function, callee, line) of every call of a name in ``wanted``."""
    found = []
    for name, source in sources.items():
        tree = ast.parse(source)
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            nested = [f for f in ast.walk(fn) if f is not fn and isinstance(
                f, ast.FunctionDef | ast.AsyncFunctionDef)]
            for node in ast.walk(fn):
                if not isinstance(node, ast.Call) or any(node in ast.walk(f) for f in nested):
                    continue
                func = node.func
                callee = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
                if isinstance(func, ast.Attribute) and f"{ast.unparse(func)}" in wanted:
                    callee = ast.unparse(func)
                if callee in wanted:
                    found.append((name, fn.name, callee, node.lineno))
    return found


def held_outside(sources: dict[str, str]) -> list[str]:
    """Every ``files.held(`` outside ``HELD_PLACES`` (``make_decider`` has a ``held`` of its own,
    a wrapper of the advisor's send, called by name)."""
    return [f"{name}:{line} {fn}" for name, fn, callee, line in _calls(sources, {"files.held"})
            if (name, fn) not in HELD_PLACES]


# Who may book or measure the learning budget: steps of a move only (under ``files.one_move``).
BUDGET_CALLS = {
    "budget_left": {("head.py", "decide")},
    "budget_spend": {("shikigon_brain.py", "spend_budget")},
    # and the end on SIGTERM, for the move under way: only inside this process's move (``UNDER_WAY``
    # is filled under ``tick``'s lock), where nothing waits (Betreuer-Durchgang e859803, B1)
    "spend_budget": {("shikigon_brain.py", "write_down"),
                     ("shikigon_brain.py", "book_the_move_under_way")},
}


def budget_calls_outside(sources: dict[str, str]) -> list[str]:
    return [f"{name}:{line} {callee} in {fn}"
            for name, fn, callee, line in _calls(sources, set(BUDGET_CALLS))
            if (name, fn) not in BUDGET_CALLS[callee]]


def _brain_sources() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(BRAIN.glob("*.py"))}


def test_b1_a_move_waits_for_the_folder_only_where_others_write_too() -> None:
    assert held_outside(_brain_sources()) == []


def test_b1_the_budget_is_measured_and_booked_in_a_move_only() -> None:
    assert budget_calls_outside(_brain_sources()) == []


def test_b1_the_guards_see_what_they_guard() -> None:
    # the booking of af58853
    old = "def budget_spend(home, loss):\n    with files.held(home):\n        pass\n"
    assert held_outside({"head.py": old})
    assert not held_outside({"lessons.py": "def keep(home):\n    with files.held(home):\n"
                                           "        pass\n"})
    assert budget_calls_outside({"x.py": "def ask():\n    head.budget_spend(h, 1)\n"})
    assert budget_calls_outside({"shikigon_brain.py": "def run():\n    spend_budget(a, b)\n"})
    assert not budget_calls_outside(
        {"shikigon_brain.py": "def write_down():\n    spend_budget(a, b)\n"})


# ---------- B2: every flock has a deadline --------------------------------------------------------


def blocking_flocks(sources: dict[str, str]) -> list[str]:
    """Every ``fcntl.flock`` that may wait: one without ``LOCK_NB`` in its flags."""
    bad = []
    for name, source in sources.items():
        for node in ast.walk(ast.parse(source)):
            if (isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "flock"
                    and "LOCK_NB" not in ast.unparse(node)):
                bad.append(f"{name}:{node.lineno}")
    return bad


def test_b2_no_flock_waits_without_a_deadline() -> None:
    assert blocking_flocks(_brain_sources()) == []


def test_b2_the_flock_guard_sees_what_it_guards() -> None:
    # the append of af58853
    assert blocking_flocks({"files.py": "def append(h):\n    fcntl.flock(h, fcntl.LOCK_EX)\n"})
    assert not blocking_flocks({"f.py": "fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"})


FILE_HOLDER = textwrap.dedent("""\
    import fcntl, sys, time
    with open(sys.argv[1], "ab") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        print("held", flush=True)
        time.sleep(float(sys.argv[2]))
""")


def _file_holder(path: Path, seconds: float) -> subprocess.Popen[str]:
    child = subprocess.Popen([sys.executable, "-I", "-c", FILE_HOLDER, str(path), str(seconds)],
                             stdout=subprocess.PIPE, text=True)
    assert child.stdout is not None
    assert child.stdout.readline().startswith("held")
    return child


def _within(work: Any, seconds: float) -> tuple[bool, BaseException | None]:
    """Run ``work`` in a thread; ``(True, error)`` if it ended within ``seconds``."""
    caught: list[BaseException] = []

    def run() -> None:
        try:
            work()
        except BaseException as exc:  # noqa: BLE001 - handed to the test
            caught.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(seconds)
    return not thread.is_alive(), (caught[0] if caught else None)


def test_b2_a_line_waits_for_another_writer_its_deadline_at_most(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    path.touch(mode=0o600)
    child = _file_holder(path, WAIT * 3 + 2)
    try:
        began = time.monotonic()
        ended, error = _within(lambda: files.append(path, {"at": 1}), WAIT * 2)
        assert ended, "append waited past its deadline"
        assert isinstance(error, TimeoutError) and time.monotonic() - began >= WAIT
    finally:
        child.kill()
        child.wait()


def test_b2_control_a_line_is_written_once_the_other_lets_go(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    path.touch(mode=0o600)
    child = _file_holder(path, 0.3)
    try:
        files.append(path, {"at": 1})
    finally:
        child.wait()
    assert json.loads(path.read_text()) == {"at": 1}


def test_b2_making_a_file_private_waits_its_deadline_at_most(tmp_path: Path) -> None:
    folder = tmp_path / "state"
    folder.mkdir()
    path = folder / "journal.jsonl"
    path.write_text("{}\n")
    path.chmod(0o644)
    child = _file_holder(path, WAIT * 3 + 2)
    try:
        ended, error = _within(lambda: files.keep_private(folder), WAIT * 2)
        assert ended and error is None, "keep_private waited past its deadline"
    finally:
        child.kill()
        child.wait()


# ---------- B3: the panel says that another move holds this one up -------------------------------
# Since 4866b11 (N1, B1) in a journal line, which the panel always shows; the status's reason it
# shows only for a move that failed, and the status was read, changed and written without a lock.


async def test_b3_the_journal_says_another_move_is_under_way_once(
        monkeypatch: pytest.MonkeyPatch) -> None:
    brain.write_status({"mood": "busy", "last": {"action": "evolve", "ok": True, "why": "grew"}})
    before = (brain.state_dir() / "status.json").read_bytes()
    ticks = iter([{"skipped": brain.ANOTHER_MOVE}, {"skipped": brain.ANOTHER_MOVE}])

    class Over(Exception):
        pass

    async def tick(*_: Any) -> dict[str, Any]:
        try:
            return {"action": "", "ok": True, "why": "", **next(ticks)}
        except StopIteration:
            raise Over from None

    async def no_wait(_: float) -> None:
        return None

    monkeypatch.setattr(brain, "guarded_tick", tick)
    monkeypatch.setattr(brain, "world_refusal", lambda cfg: None)
    monkeypatch.setattr(brain, "decider_for_now", lambda cfg, d, c: (d, c))
    monkeypatch.setattr(brain.asyncio, "sleep", no_wait)
    with pytest.raises(Over):
        await brain.run({}, FakeAgent(_brain_state()), CountingDecider(("evolve", {})))
    recent = json.loads((brain.state_dir() / brain.BAR_JOURNAL).read_text())
    waiting = [line for line in recent if line.get("waiting")]
    assert len(waiting) == 1, "said once, not every few seconds"
    assert waiting[0]["why"].startswith(brain.ANOTHER_MOVE + " since "), waiting
    assert (brain.state_dir() / "status.json").read_bytes() == before, "the status stays whole"


async def test_b3_control_the_next_move_follows_it_in_the_journal() -> None:
    brain.note_another_move()
    await brain.tick(FakeAgent(_brain_state()), CountingDecider(("evolve", {})))
    recent = json.loads((brain.state_dir() / brain.BAR_JOURNAL).read_text())
    assert recent[-2].get("waiting") is True and recent[-1]["action"] == "evolve", recent
    status = json.loads((brain.state_dir() / "status.json").read_text())
    assert brain.ANOTHER_MOVE not in str(status["last"].get("why"))


# ---------- B4: open terminal: no focus by title, the path whole ---------------------------------


def _code(text: str) -> str:
    """QML without its ``//`` comments."""
    return re.sub(r"//[^\n]*", "", text)


def terminal_problems(service: str, others: str) -> list[str]:
    found = []
    if "omarchy-launch-or-focus" in _code(service) + _code(others):
        found.append("a starter that brings a window to the front by its title")
    body = re.search(r"function openTerminal\(\) \{\n(.*?)\n  \}\n", service, re.DOTALL)
    if not body or '"omarchy-launch-tui"' not in body[1] or "terminalScript" not in body[1]:
        found.append("openTerminal does not start the terminal through omarchy-launch-tui")
    return found


def _others() -> str:
    return "".join(p.read_text(encoding="utf-8") for p in sorted(QML.glob("*.qml"))
                   if p.name != "Service.qml") + (QML / "ShikigonModel.js").read_text(
        encoding="utf-8")


def test_b4_open_terminal_chooses_no_window_by_its_title() -> None:
    assert terminal_problems((QML / "Service.qml").read_text(encoding="utf-8"), _others()) == []


def test_b4_the_terminal_guard_sees_what_it_guards() -> None:
    service = (QML / "Service.qml").read_text(encoding="utf-8")
    old = service.replace('"omarchy-launch-tui",\n', '"omarchy-launch-or-focus-tui",\n', 1)
    assert old != service and terminal_problems(old, "")
    assert not terminal_problems(service, "// omarchy-launch-or-focus in a comment\n")


# Read at the device on 07.11.2026 (Omarchy 4.5.0-3, uwsm 0.27.1, xdg-terminal-exec 0.14.3):
# omarchy-launch-tui:13 `exec setsid uwsm-app -- xdg-terminal-exec --app-id=$APP_ID -e "$1"
# "${@:2}"`; uwsm-app hands its arguments to uwsm's daemon, which answers `exec` and
# `shlex.join` of them (main.py), and uwsm-app runs that answer with `eval "$CMDLINE"`.
# xdg-terminal-exec's own `eval` reads its entry id, not the command.
LAUNCH_TUI = textwrap.dedent("""\
    set -- --app-id=org.omarchy.cosmergon "$1"
    APP_ID="${1#--app-id=}"
    shift
    CMDLINE=$("$PYTHON" -I -c 'import shlex, sys; print("exec " + shlex.join(sys.argv[1:]))' \\
        xdg-terminal-exec --app-id=$APP_ID -e "$1" "${@:2}")
    eval "$CMDLINE"
""")
# The chain until af58853: omarchy-launch-or-focus-tui writes `"omarchy-launch-tui $@"` into one
# string, and omarchy-launch-or-focus runs `eval exec setsid $LAUNCH_COMMAND`.
LAUNCH_OR_FOCUS = textwrap.dedent("""\
    set -- --app-id=org.omarchy.cosmergon "$1"
    LAUNCH_COMMAND="omarchy-launch-tui $@"
    eval exec setsid $LAUNCH_COMMAND
""")


def _started(chain: str, path: str, tmp_path: Path, stand_in: str) -> list[str] | None:
    """What ``chain`` starts for ``path``: a stand-in for the last program writes its argv."""
    stub = tmp_path / "bin"
    stub.mkdir(exist_ok=True)
    out = tmp_path / "argv"
    out.unlink(missing_ok=True)
    (stub / stand_in).write_text(f"#!/bin/sh\nprintf '%s\\0' \"$@\" > '{out}'\n")
    (stub / stand_in).chmod(0o755)
    subprocess.run(["/bin/bash", "-c", chain, "chain", path], cwd=tmp_path, timeout=10,
                   env={"PATH": f"{stub}:/usr/bin:/bin", "x": "SET", "LC_ALL": "C.UTF-8",
                        "PYTHON": sys.executable}, capture_output=True, check=False)
    return out.read_bytes().decode().split("\0")[:-1] if out.exists() else None


PATHS = ["/h/plain/x", "/h/jörg/x", "/h/a-b_c.d@e%f+g=h:i,j/x", "/h/a b/x", "/h/a\tb/x",
         "/h/a\nb/x", "/h/a'b/x", '/h/a"b/x', "/h/$x/x", "/h/`true`/x", "/h/a;b/x", "/h/a*b/x",
         "/h/{a,b}/x", "/h/a\\b/x", "/h/a|b/x", "/h/a&b/x", "/h/~x/x", "/h/a(b/x", "/h/a#b/x",
         "/h/a[b/x"]


def test_b4_every_path_reaches_the_terminal_whole(tmp_path: Path) -> None:
    for path in PATHS:
        started = _started(LAUNCH_TUI, path, tmp_path, "xdg-terminal-exec")
        assert started == ["--app-id=org.omarchy.cosmergon", "-e", path], repr(path)


def test_b4_the_old_chain_broke_paths(tmp_path: Path) -> None:
    """Counter-test: the stand-in chain until af58853 breaks these, so the test above measures."""
    for path in ["/h/a b/x", "/h/a'b/x", "/h/$x/x", "/h/a;b/x"]:
        started = _started(LAUNCH_OR_FOCUS, path, tmp_path, "setsid")
        assert started != ["omarchy-launch-tui", "--app-id=org.omarchy.cosmergon", path], path
    assert shlex.split(shlex.join(["a b"])) == ["a b"]
