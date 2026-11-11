"""Guards for the Betreuer-Durchgang e859803 (cos20 #492, plan 6790466632), B1–B4.

One family: what a stop does to the work it stops (the deadline of a finite helper, the bar
stopping the brain, Quickshell tearing a helper down).

B1: a move that went out when the brain was stopped (SIGTERM) stood neither in the journal nor
against the learning budget. Now the end books it as a move whose answer was lost (cos20 #614).

B2: a rebuild of the brain's environment that was cut off left the old one in ``$venv.old``, and
the next rebuild removed it first. Now it comes back before anything else.

B3: Quickshell tears a running helper down with SIGKILL to the process it started, which was
``timeout``: the helper's program went on without its deadline. Now ``timeout`` is a child of the
shell line and gets TERM when it dies (``setpriv --pdeathsig``).

B4: a command of the brain that broke off (a traceback, no JSON) ended without a word; a second
notice while one was on its way was dropped. Now "did not finish", and the notices wait.

Red on e859803, the controls green.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
BRAIN = ROOT / "brain"
QML = ROOT / "qml"
sys.path.insert(0, str(BRAIN))

import connections
import shikigon_brain as brain
from test_betreuer_1d81304 import REST, BookingWorld, _gift_head, _spent
from test_betreuer_c01ec84 import _rule
from test_betreuer_c47b7f1 import TOTAL
from test_effect import _with_total
from test_qml import _blocks


@pytest.fixture(autouse=True)
def _private_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


# ---------- B1: a move under way is booked when the brain is stopped ------------------------------


@pytest.fixture
def _ends_as_the_brain_does() -> Iterator[None]:
    """The brain's own handlers for SIGTERM, SIGHUP and SIGINT, as ``__main__`` sets them."""
    kept = {signum: signal.getsignal(signum)
            for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)}  # fmt: skip
    brain.end_with_its_agents()
    try:
        yield
    finally:
        for signum, handler in kept.items():
            signal.signal(signum, handler)


class StoppedWorld(BookingWorld):
    """The move goes out, and while its answer is awaited the brain is stopped: the deadline of
    a tick by hand, or the bar stopping the game loop when "advise on moves" changes."""

    async def act(self, action: str, **params: Any) -> Any:  # type: ignore[override]
        self.acted.append((action, params))
        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.sleep(30)
        raise AssertionError("the brain was stopped before this")


def _journal() -> list[dict[str, Any]]:
    path = brain.state_dir() / "journal.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


@pytest.mark.usefixtures("_ends_as_the_brain_does")
def test_b1_a_move_stopped_before_its_answer_counts_with_its_whole_limit() -> None:
    world = StoppedWorld(_with_total(TOTAL), REST / 5)
    with pytest.raises(SystemExit) as ended:
        connections.run_loop(brain.tick(world, _gift_head()))
    assert ended.value.code == 128 + signal.SIGTERM
    assert world.acted and world.acted[0][0] == "transfer_energy"
    assert _spent() == REST, "not known is given: the whole limit (cos20 #614)"
    last = _journal()[-1]
    assert last["action"] == "transfer_energy" and last["unknown"] is True, last
    assert brain.STOPPED_IN_MOVE in last["why"], last
    assert brain.UNDER_WAY == {}, "nothing stays under way after the move"


@pytest.mark.usefixtures("_ends_as_the_brain_does")
def test_b1_control_a_move_written_down_is_booked_once() -> None:
    """Stopped after the move was written down: the end books and writes nothing more."""
    world = BookingWorld(_with_total(TOTAL), REST / 5)
    connections.run_loop(brain.tick(world, _gift_head()))
    spent, lines = _spent(), len(_journal())
    brain.UNDER_WAY.update({"move": {"action": "transfer_energy", "ok": True}, "written": True})
    try:
        brain.book_the_move_under_way()
    finally:
        brain.UNDER_WAY.clear()
    assert (_spent(), len(_journal())) == (spent, lines) == (REST / 5, lines)


@pytest.mark.usefixtures("_ends_as_the_brain_does")
def test_b1_stopped_after_its_booking_the_move_is_booked_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stopped between the booking in ``write_down`` and its journal line: the end writes the
    line and books nothing a second time (``UNDER_WAY["booked"]``)."""
    real, calls = brain.journal, []

    def stopping(*entries: dict[str, Any]) -> None:
        calls.append(entries)
        if len(calls) == 1:
            os.kill(os.getpid(), signal.SIGTERM)
        real(*entries)

    monkeypatch.setattr(brain, "journal", stopping)
    world = BookingWorld(_with_total(TOTAL), REST / 5)
    with pytest.raises(SystemExit):
        connections.run_loop(brain.tick(world, _gift_head()))
    assert _spent() == REST / 5, "booked once"
    assert _journal()[-1]["action"] == "transfer_energy"


def test_b1_control_nothing_under_way_nothing_written() -> None:
    brain.book_the_move_under_way()
    assert _journal() == []


def test_b1_every_move_goes_out_through_the_door_that_keeps_it() -> None:
    """``agent.act`` only in ``_play``, and ``_play`` only from ``play``: no move leaves past
    ``UNDER_WAY``."""
    source = (BRAIN / "shikigon_brain.py").read_text(encoding="utf-8")
    assert source.count("agent.act(") == 1
    assert re.findall(r"await _play\(", source) == ["await _play("]
    play = source[source.index("async def play("):source.index("async def _play(")]
    assert 'UNDER_WAY["move"]' in play and "await _play(" in play


# ---------- B2: a rebuild cut off leaves the old brain --------------------------------------------


def _venv(folder: Path, lock: str | None, marker: str) -> None:
    (folder / "bin").mkdir(parents=True)
    python = folder / "bin" / "python"
    python.write_text("#!/bin/sh\nexit 0\n")  # loads its packages
    python.chmod(0o755)
    (folder / marker).write_text(marker)
    if lock is not None:
        (folder / ".lock-sha256").write_text(lock + "\n")


def _wake_after_a_cut_off_rebuild(tmp_path: Path) -> tuple[int, str, Path]:
    """The state a deadline leaves in the middle of ``install_brain``: a half-built ``$venv`` with
    no lock hash, the old one in ``$venv.old``. The system's python3 builds no new environment
    (no network here), so a second rebuild fails as on a slow line."""
    home = tmp_path / "home"
    venv = home / ".local" / "share" / "shikigon" / "venv"
    (home / "state").mkdir(parents=True)
    lock = subprocess.run(["sha256sum", str(ROOT / "requirements.lock")], check=True,
                          capture_output=True, text=True).stdout.split()[0]  # fmt: skip
    (venv / "lib").mkdir(parents=True)  # half-built: no python, no hash
    _venv(Path(f"{venv}.old"), lock, "good")
    stub = tmp_path / "stub"
    stub.mkdir()
    real = shutil.which("python3")
    assert real
    python3 = stub / "python3"
    python3.write_text(f'#!/bin/sh\ncase "$*" in *"-m venv"*) exit 1 ;; esac\nexec {real} "$@"\n')
    python3.chmod(0o755)
    env = {**os.environ, "HOME": str(home), "XDG_STATE_HOME": str(home / "state"),
           "XDG_CONFIG_HOME": str(home / "config"),
           "PATH": f"{stub}:{os.environ.get('PATH', '/usr/bin:/bin')}"}  # fmt: skip
    bash = shutil.which("bash")
    assert bash
    done = subprocess.run([bash, str(BRAIN / "shikigon-wake"), "--look"], env=env,
                          capture_output=True, text=True, timeout=60, check=False)  # fmt: skip
    return done.returncode, done.stdout + done.stderr, venv


def test_b2_a_rebuild_cut_off_leaves_the_old_brain(tmp_path: Path) -> None:
    code, said, venv = _wake_after_a_cut_off_rebuild(tmp_path)
    assert (venv / "good").exists(), f"the brain that worked is gone: {said}"
    assert not Path(f"{venv}.old").exists()
    assert code == 0, said  # its hash and its packages hold: nothing to rebuild


def test_b2_control_without_an_old_one_nothing_moves(tmp_path: Path) -> None:
    home = tmp_path / "home"
    venv = home / ".local" / "share" / "shikigon" / "venv"
    (home / "state").mkdir(parents=True)
    lock = subprocess.run(["sha256sum", str(ROOT / "requirements.lock")], check=True,
                          capture_output=True, text=True).stdout.split()[0]  # fmt: skip
    _venv(venv, lock, "current")
    env = {**os.environ, "HOME": str(home), "XDG_STATE_HOME": str(home / "state"),
           "XDG_CONFIG_HOME": str(home / "config")}  # fmt: skip
    done = subprocess.run(["bash", str(BRAIN / "shikigon-wake"), "--look"], env=env,
                          capture_output=True, text=True, timeout=60, check=False)  # fmt: skip
    assert done.returncode == 0 and (venv / "current").exists(), done.stdout + done.stderr


# ---------- B3: a helper torn down by Quickshell ends with its children ---------------------------


def _body(name: str) -> str:
    text = (QML / "ShikigonModel.js").read_text(encoding="utf-8")
    found = re.search(rf"^var {name} = '(.*)'$", text, re.MULTILINE)
    assert found, f"ShikigonModel.js names {name}"
    return found.group(1)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    stat = Path(f"/proc/{pid}/stat")
    return stat.exists() and stat.read_text().split(") ")[-1][:1] != "Z"


def _torn_down(body: str, tmp_path: Path) -> int:
    """The helper's line as Quickshell starts it, its program a shell that keeps a child; then
    SIGKILL to the process Quickshell holds, as ``~Process`` sends it. Returns the child's pid."""
    pidfile = tmp_path / "child"
    program = ["sh", "-c", f'sleep 300 & echo $! > "{pidfile}"; wait']
    argv = ["/bin/sh", "-c", _rule() + body, "sh", "timeout", "-k", "5", "60", *program]
    shell = subprocess.Popen(argv)
    for _ in range(200):
        if pidfile.exists() and pidfile.read_text().strip():
            break
        time.sleep(0.02)
    child = int(pidfile.read_text())
    shell.kill()
    shell.wait()
    return child


def test_b3_a_helper_torn_down_ends_with_its_children(tmp_path: Path) -> None:
    child = _torn_down(_body("HELPER_BODY"), tmp_path)
    for _ in range(150):
        if not _alive(child):
            break
        time.sleep(0.02)
    assert not _alive(child), "the helper's child went on without its deadline"


def test_b3_control_the_form_before_leaves_the_child_running(tmp_path: Path) -> None:
    """The probe measures the threat: with `timeout` in the line's place (`exec`), its child lives
    on after the SIGKILL."""
    child = _torn_down('exec "$@"', tmp_path)
    try:
        time.sleep(0.5)
        assert _alive(child)
    finally:
        os.killpg(os.getpgid(child), signal.SIGKILL)


def test_b3_every_helper_goes_through_the_body() -> None:
    model = (QML / "ShikigonModel.js").read_text(encoding="utf-8")
    assert "return shell(HELPER_BODY," in model
    service = (QML / "Service.qml").read_text(encoding="utf-8")
    assert "command: Model.bounded(seconds, argv)" in service


# ---------- B4: a brain that broke off says so; no notice is dropped ------------------------------


def test_b4_every_command_of_the_brain_is_read_as_one_that_answers() -> None:
    service = (QML / "Service.qml").read_text(encoding="utf-8")
    helpers = [b for b in _blocks(service, "Helper") if "signal settled(" not in b]
    brainy = [b for b in helpers if "root.brainCommand(" in b]
    assert len(brainy) == 8
    assert all("answers: true" in b for b in brainy), [b.splitlines()[1] for b in brainy
                                                         if "answers: true" not in b]
    assert not any("answers: true" in b for b in helpers if b not in brainy)
    assert "Model.helperWhy(code, answers ? said !== null : undefined)" in service


def test_b4_a_notice_while_one_is_on_its_way_waits() -> None:
    service = (QML / "Service.qml").read_text(encoding="utf-8")
    failed = service[service.index("function failed("):service.index("Helper {\n    id: notifier")]
    assert 'why === "" || notifier.running' not in failed
    assert "Model.noticesAfter(notifier.waiting, body)" in failed
