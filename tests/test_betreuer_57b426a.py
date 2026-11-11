"""Guards for the Betreuer-Durchgang 57b426a (cos20 #492, plan 6794798715): B1, B2, V1, K1.

B1: ``timeout`` was tied to the shell line, but nothing tied the line to the shell. Quickshell ends
a running Process only in ``~Process``; a shell that ended by a signal, a crash or the OOM killer
left a helper running to its deadline. Now every process the bar holds starts behind
``setpriv --pdeathsig TERM`` (``Model.tied``): the kernel ends it with the shell's thread.

V1: the game loop had no tie at all, and the restarted shell started a second one beside it.

B2: a rebuild that worked and was cut off while removing ``$venv.old`` left half an old brain, and
the next start put it in the place of the new one. Now a folder goes by a new name before it goes,
and ``.old`` comes back only when the new one is not whole.

K1: a brain command could start while the wake script rebuilt the environment it runs from.

K2 (a notice dropped while another is on its way) is not built: the panel of 11.11.2026
(cos20 ed3769b04, founder's decision) took that queue back, ``notify-send`` ends in milliseconds.

Red on 57b426a, the controls green.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BRAIN = ROOT / "brain"
QML = ROOT / "qml"
SERVICE = (QML / "Service.qml").read_text(encoding="utf-8")
MODEL = (QML / "ShikigonModel.js").read_text(encoding="utf-8")

from test_betreuer_c01ec84 import _rule
from test_betreuer_e859803 import _alive, _body, _venv
from test_qml import _blocks

SETPRIV = "/usr/bin/setpriv"


# ---------- B1, V1: what the bar holds ends with the shell ----------------------------------------


def _tie() -> list[str]:
    found = re.search(r"^var TIE = (\[.*\])$", MODEL, re.MULTILINE)
    assert found, "ShikigonModel.js names TIE"
    return re.findall(r'"([^"]*)"', found.group(1))


SHELL = """
import subprocess, sys, time
subprocess.Popen(sys.argv[1:])
time.sleep(600)
"""


def _shell_dies(tie: list[str], tmp_path: Path) -> int:
    """A stand-in for Quickshell starts the helper's line as the bar builds it and is then ended
    with SIGKILL, so no destructor of it runs and nothing is forwarded. Returns the pid of the
    helper's child."""
    pidfile = tmp_path / "child"
    program = ["sh", "-c", f'sleep 300 & echo $! > "{pidfile}"; wait']
    line = [*tie, "/bin/sh", "-c", _rule() + _body("HELPER_BODY"), "sh",
            "timeout", "-k", "5", "60", *program]  # fmt: skip
    shell = subprocess.Popen([sys.executable, "-c", SHELL, *line])
    for _ in range(250):
        if pidfile.exists() and pidfile.read_text().strip():
            break
        time.sleep(0.02)
    child = int(pidfile.read_text())
    shell.kill()
    shell.wait()
    return child


@pytest.mark.skipif(not Path(SETPRIV).exists(), reason="util-linux setpriv at its Arch path")
def test_b1_a_helper_ends_when_the_shell_dies_without_tearing_it_down(tmp_path: Path) -> None:
    child = _shell_dies(_tie(), tmp_path)
    for _ in range(250):
        if not _alive(child):
            break
        time.sleep(0.02)
    assert not _alive(child), "the helper went on after the shell was gone"


def test_b1_control_without_the_tie_the_helper_outlives_the_shell(tmp_path: Path) -> None:
    """The probe measures the threat: the line of 57b426a (no tie) lives on."""
    child = _shell_dies([], tmp_path)
    try:
        time.sleep(0.5)
        assert _alive(child)
    finally:
        os.killpg(os.getpgid(child), signal.SIGKILL)


def test_b1_v1_every_process_the_bar_holds_is_tied() -> None:
    """``Model.shell`` (and so ``bounded``) and the game loop go through ``tied``; what is meant
    to go on (``execDetached``) through ``outside`` alone, untied."""
    assert _tie() == [SETPRIV, "--pdeathsig", "TERM", "--"]
    assert 'return tied(["/bin/sh", "-c", PATH_RULE + body, "sh"].concat(args || []))' in MODEL
    assert 'return ["/bin/sh", "-c", PATH_RULE + \'exec "$@"\', "sh"].concat(argv)' in MODEL
    commands = []
    for qml in sorted(QML.glob("*.qml")):
        commands += re.findall(r"\bcommand:\s*(.*)", qml.read_text(encoding="utf-8"))
    held = re.compile(r"^(Model\.shell\(|Model\.bounded\(|Model\.tied\()")
    assert commands and all(held.match(c) for c in commands), commands
    runner = next(b for b in _blocks(SERVICE, "Process") if "id: runner" in b)
    assert 'command: Model.tied(root.brainCommand(["run"]))' in runner
    detached = re.findall(r"execDetached\((.*)", SERVICE)
    assert detached and all(d.startswith("Model.outside(") for d in detached), detached


# ---------- B2: a cut-off cleanup never costs the new brain ---------------------------------------


def _lock_hash() -> str:
    done = subprocess.run(["sha256sum", str(ROOT / "requirements.lock")], check=True,
                          capture_output=True, text=True)  # fmt: skip
    return done.stdout.split()[0]


def _wake(tmp_path: Path, script: Path) -> tuple[int, str, Path]:
    """Runs ``script --look`` with an empty home whose environment the caller set up; the system's
    python3 builds no new environment (no package index here)."""
    home = tmp_path / "home"
    venv = home / ".local" / "share" / "shikigon" / "venv"
    stub = tmp_path / "stub"
    stub.mkdir(exist_ok=True)
    real = shutil.which("python3")
    assert real
    python3 = stub / "python3"
    python3.write_text(f'#!/bin/sh\ncase "$*" in *"-m venv"*) exit 1 ;; esac\nexec {real} "$@"\n')
    python3.chmod(0o755)
    (home / "state").mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "HOME": str(home), "XDG_STATE_HOME": str(home / "state"),
           "XDG_CONFIG_HOME": str(home / "config"),
           "PATH": f"{stub}:{os.environ.get('PATH', '/usr/bin:/bin')}"}  # fmt: skip
    done = subprocess.run(["bash", str(script), "--look"], env=env, capture_output=True,
                          text=True, timeout=60, check=False)  # fmt: skip
    return done.returncode, done.stdout + done.stderr, venv


def _cut_in_cleanup(tmp_path: Path) -> Path:
    """What a cut in ``rm -rf "$venv.old"`` after a rebuild that worked leaves: the new brain whole
    (its packages load, the lock's hash), half of the old one beside it."""
    venv = tmp_path / "home" / ".local" / "share" / "shikigon" / "venv"
    _venv(venv, _lock_hash(), "new")
    half = Path(f"{venv}.old")
    (half / "lib").mkdir(parents=True)  # its python already removed
    (half / "half").write_text("half")
    return venv


def test_b2_a_cleanup_cut_off_keeps_the_new_brain(tmp_path: Path) -> None:
    _cut_in_cleanup(tmp_path)
    code, said, venv = _wake(tmp_path, BRAIN / "shikigon-wake")
    assert (venv / "new").exists(), f"the new brain was thrown away: {said}"
    assert not Path(f"{venv}.old").exists() and not Path(f"{venv}.old.gone").exists()
    assert code == 0, said


def test_b2_a_discard_cut_off_leaves_nothing_that_comes_back(tmp_path: Path) -> None:
    """A cut in ``discard`` leaves ``<folder>.gone``: never brought back, removed on the next
    start; the old brain that worked comes back where the new one is not whole."""
    venv = tmp_path / "home" / ".local" / "share" / "shikigon" / "venv"
    _venv(Path(f"{venv}.old"), _lock_hash(), "good")
    (Path(f"{venv}.gone") / "lib").mkdir(parents=True)  # the half-built new one, half removed
    code, said, venv = _wake(tmp_path, BRAIN / "shikigon-wake")
    assert (venv / "good").exists() and code == 0, said
    assert not Path(f"{venv}.gone").exists()


def test_b2_folders_go_only_through_discard() -> None:
    script = (BRAIN / "shikigon-wake").read_text(encoding="utf-8")
    removes = [line.strip() for line in script.splitlines()
               if "rm -rf" in line and not line.lstrip().startswith("#")]  # fmt: skip
    assert removes == ['rm -rf "$1.gone"', 'mv "$1" "$1.gone" && rm -rf "$1.gone"',
                       ('rm -rf "$venv.gone" "$venv.old.gone"'
                        '  # what a cut-off discard left behind')], removes  # fmt: skip
    assert "if current; then" in script


# ---------- K1: the brain's commands and the wake script never run at once ------------------------


BRAIN_HELPERS = ("ticker", "advisorLister", "modelLister", "prober", "asker", "instructor",
                 "keyKeeper", "adviser")  # fmt: skip


def test_k1_the_brain_helpers_are_the_ones_counted_busy() -> None:
    brainy = {re.search(r"id: (\w+)", b).group(1)  # type: ignore[union-attr]
              for b in _blocks(SERVICE, "Helper") if "root.brainCommand(" in b}
    assert brainy == set(BRAIN_HELPERS)
    busy = SERVICE[SERVICE.index("readonly property bool brainBusy:"):]
    busy = busy[:busy.index("\n\n")]
    assert all(f"{name}.running" in busy for name in BRAIN_HELPERS), busy


def test_k1_every_way_into_a_brain_helper_waits_for_the_setup() -> None:
    """Each function that starts a brain helper says "busy" (or nothing) while it is set up; the
    only other starts are the helpers' own next run from `settled`, while the wake waits."""
    starts = re.finditer(r"^  function (\w+)\([^)]*\) \{\n.*?\n  \}", SERVICE,
                         re.MULTILINE | re.DOTALL)
    found = []
    for function in starts:
        body = function.group(0)
        if any(f"{name}.running = true" in body for name in BRAIN_HELPERS):
            found.append(function.group(1))
            assert "settingUp" in body, function.group(1)
    assert sorted(found) == ["ask", "instruct", "keepKey", "listModels", "refreshAdvisors",
                             "setAdvise", "tickNow", "tryConnection"]  # fmt: skip


def test_k1_the_wake_script_waits_for_the_brain_helpers() -> None:
    start = SERVICE[SERVICE.index("function startWaker("):SERVICE.index("onBrainBusyChanged:")]
    assert "if (brainBusy) { wakeWaiting = true; return }" in start
    deferred = SERVICE[SERVICE.index("onBrainBusyChanged:"):]
    deferred = deferred[:deferred.index("\n  }")]
    assert deferred.index("waker.running = true") < deferred.index("wakeWaiting = false")
    assert "readonly property bool settingUp: waker.running || wakeWaiting" in SERVICE
