"""Guards for the Betreuer-Durchgang e859803 (cos20 #492, plan 6790466632), B1–B4.

One family: what a stop does to the work it stops (the deadline of a finite helper, the bar
stopping the brain, Quickshell tearing a helper down).

B1: a move that went out when the brain was stopped (SIGTERM, or a KILL) did not count against the
learning budget. Now ``play`` books what it may cost before it goes out and gives back what it did
not lose: a stop in between leaves it counted as a move whose answer was lost (cos20 #614).

B2: a rebuild of the brain's environment that was cut off left the old one in ``$venv.old``, and
the next rebuild removed it first. Now it comes back before anything else.

B3: Quickshell tears a running helper down with SIGKILL to the process it started, which was
``timeout``: the helper's program went on without its deadline. Now ``timeout`` is a child of the
shell line and gets TERM when it dies (``setpriv --pdeathsig``).

B4: a command of the brain that broke off (a traceback, no JSON) ended without a word. Now "did
not finish".

Red on e859803, the controls green.
"""

from __future__ import annotations

import asyncio
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
from test_brain import FakeResult
from test_effect import _with_total
from test_qml import _blocks


@pytest.fixture(autouse=True)
def _private_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


# ---------- B1: what a move may cost is booked before it goes out --------------------------------


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


@pytest.mark.usefixtures("_ends_as_the_brain_does")
def test_b1_a_move_stopped_before_its_answer_counts_with_its_whole_limit() -> None:
    world = StoppedWorld(_with_total(TOTAL), REST / 5)
    with pytest.raises(SystemExit) as ended:
        connections.run_loop(brain.tick(world, _gift_head()))
    assert ended.value.code == 128 + signal.SIGTERM
    assert world.acted and world.acted[0][0] == "transfer_energy"
    assert _spent() == REST, "not known is given: the whole limit (cos20 #614)"


KILLED_IN_MOVE = """
import os, signal, sys
sys.path[:0] = [sys.argv[1], sys.argv[2]]
import connections, shikigon_brain as brain
from test_betreuer_1d81304 import REST, BookingWorld, _gift_head
from test_betreuer_c47b7f1 import TOTAL
from test_effect import _with_total

class Killed(BookingWorld):
    async def act(self, action, **params):
        os.kill(os.getpid(), signal.SIGKILL)

connections.run_loop(brain.tick(Killed(_with_total(TOTAL), REST / 5), _gift_head()))
"""


def test_b1_a_move_killed_on_its_way_counts_with_its_whole_limit() -> None:
    """KILL, which no handler sees: Quickshell tearing the game loop down, ``timeout -k``. The
    booking stands from before the move went out."""
    done = subprocess.run([sys.executable, "-c", KILLED_IN_MOVE, str(ROOT / "tests"), str(BRAIN)],
                          capture_output=True, text=True, timeout=60, check=False)
    assert done.returncode == -signal.SIGKILL, done.stderr
    assert _spent() == REST, "not known is given: the whole limit (cos20 #614)"


class RefusedFirst(BookingWorld):
    """Refuses the first move, books ``loss`` for every later one; notes what stood booked."""

    def __init__(self, state: Any, loss: float) -> None:
        super().__init__(state, loss)
        self.booked_while_out: list[float] = []

    async def act(self, action: str, **params: Any) -> Any:  # type: ignore[override]
        self.booked_while_out.append(_spent())
        if not self.acted:
            self.acted.append((action, params))
            return FakeResult(False, "Not enough energy")
        return await super().act(action, **params)


def test_b1_a_refused_move_gives_back_what_it_held() -> None:
    """The head's gift is refused, the rules build a field: nothing counts (cos20 #566, F2), and
    the gift's limit stood booked only while it was out."""
    world = RefusedFirst(_with_total(TOTAL), REST / 5)
    last = connections.run_loop(brain.tick(world, _gift_head(), refusals=brain.Refusals()))
    assert [a for a, _ in world.acted] == ["transfer_energy", "create_field"], world.acted
    assert world.acted[0][1].get("max_cost") == REST
    assert world.booked_while_out == [REST, 0.0], "held while out, back before the next one"
    assert last["action"] == "create_field" and _spent() == 0.0


class KeyEnded(BookingWorld):
    async def act(self, action: str, **params: Any) -> Any:  # type: ignore[override]
        from cosmergon_agent.exceptions import AuthenticationError

        self.acted.append((action, params))
        raise AuthenticationError("ended")


def test_b1_a_move_the_world_did_not_take_gives_back_what_it_held() -> None:
    """A key that ended: the move was not taken, and the brain goes to sleep with its budget."""
    from cosmergon_agent.exceptions import AuthenticationError

    world = KeyEnded(_with_total(TOTAL), REST / 5)
    with pytest.raises(AuthenticationError):
        connections.run_loop(brain.tick(world, _gift_head()))
    assert world.acted and _spent() == 0.0


def test_b1_control_a_move_taken_counts_what_it_lost_once() -> None:
    world = BookingWorld(_with_total(TOTAL), REST / 5)
    last = connections.run_loop(brain.tick(world, _gift_head()))
    assert last["ok"] and last["held"] == REST
    assert _spent() == REST / 5, "the loss, not the limit and not both"


def test_b1_every_move_goes_out_with_the_day_it_was_chosen() -> None:
    """``agent.act`` only in ``_play``, ``_play`` only from ``play``, and every ``play`` is told
    the day of the choice: the booking before and after it fall on one day (c611ee5)."""
    source = (BRAIN / "shikigon_brain.py").read_text(encoding="utf-8")
    assert source.count("agent.act(") == 1
    assert re.findall(r"await _play\(", source) == ["await _play("]
    calls = re.findall(r"await play\([^)]*\)[^\n]*", source)
    assert calls and all("chosen_day(decider)" in call for call in calls), calls


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


# ---------- B4: a brain that broke off says so ----------------------------------------------------


def test_b4_every_command_of_the_brain_is_read_as_one_that_answers() -> None:
    service = (QML / "Service.qml").read_text(encoding="utf-8")
    helpers = [b for b in _blocks(service, "Helper") if "signal settled(" not in b]
    brainy = [b for b in helpers if "root.brainCommand(" in b]
    assert len(brainy) == 8
    assert all("answers: true" in b for b in brainy), [b.splitlines()[1] for b in brainy
                                                         if "answers: true" not in b]
    assert not any("answers: true" in b for b in helpers if b not in brainy)
    assert "Model.helperWhy(code, answers ? said !== null : undefined)" in service
