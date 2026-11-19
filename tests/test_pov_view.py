"""The 3D view in the terminal (0.1.19): the view through a marauder's eyes of cosmergon-agent
0.34.0 needs the package ``websockets``, which Shikigon installs only when the player chose it.

``shikigon-wake`` takes ``--pov`` / ``--no-pov``, keeps the choice in ``pov`` in the state folder
and builds the environment from ``requirements.pov.lock`` as well when it is on. Its stamp
(``.lock-sha256``) covers both locks and the choice, so a changed choice builds the environment
anew at the next wake. The panel's question is tested in ``tests/qml/tst_model.qml``.

Red on 0571d1a (0.1.18), the controls green.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WAKE = ROOT / "brain" / "shikigon-wake"
LOCK = ROOT / "requirements.lock"
POV_LOCK = ROOT / "requirements.pov.lock"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Home:
    """A home for ``shikigon-wake``: the system's ``python3`` builds an environment whose Python
    does nothing and writes down what it was asked (``installs``). No network, no package."""

    def __init__(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        self.home = folder / "home"
        self.state = self.home / ".local" / "state" / "shikigon"
        self.venv = self.home / ".local" / "share" / "shikigon" / "venv"
        self.asked = folder / "asked.log"
        self.asked.write_text("")
        self.stub = folder / "stub"
        self.stub.mkdir()
        fake = folder / "fake-python"
        fake.write_text(f'#!/bin/sh\necho "$*" >> "{self.asked}"\nexit 0\n')
        fake.chmod(0o755)
        real = shutil.which("python3")
        assert real
        python3 = self.stub / "python3"
        python3.write_text(
            "#!/bin/sh\n"
            'case "$*" in *"-m venv"*)\n'
            '    for last; do :; done\n'
            f'    mkdir -p "$last/bin" && cp "{fake}" "$last/bin/python"; exit 0 ;;\n'
            "esac\n"
            f'exec {real} "$@"\n'
        )
        python3.chmod(0o755)

    def wake(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = {**os.environ, "HOME": str(self.home), "XDG_STATE_HOME": str(self.state.parent),
               "XDG_CONFIG_HOME": str(self.home / "config"),
               "PATH": f"{self.stub}:{os.environ.get('PATH', '/usr/bin:/bin')}"}  # fmt: skip
        bash = shutil.which("bash")
        assert bash
        return subprocess.run([bash, str(WAKE), "--look", *args], env=env, capture_output=True,
                              text=True, timeout=60, check=False)  # fmt: skip

    def installs(self) -> list[str]:
        """The ``pip install`` lines the environment's Python was asked, in order."""
        return [line for line in self.asked.read_text().splitlines() if "pip install" in line]

    def stamp(self) -> str:
        return (self.venv / ".lock-sha256").read_text().strip()


def test_waking_without_the_3d_view_installs_the_main_lock_only(tmp_path: Path) -> None:
    home = Home(tmp_path)
    done = home.wake("--no-pov")
    assert done.returncode == 0, done.stdout + done.stderr
    (line,) = home.installs()
    assert re.search(r"-r \S+/brain/\.\./requirements\.lock(\s|$)", line), line
    assert "requirements.pov.lock" not in line, "websockets is not installed unless it is chosen"
    assert (home.state / "pov.json").read_text() == '{"pov": false}\n'
    assert home.stamp() == _sha(LOCK.read_bytes()), "off: the stamp is the hash of the main lock"


def test_waking_with_the_3d_view_installs_websockets_from_its_own_lock(tmp_path: Path) -> None:
    home = Home(tmp_path)
    done = home.wake("--pov")
    assert done.returncode == 0, done.stdout + done.stderr
    (line,) = home.installs()
    assert re.search(r"-r \S+/requirements\.lock(\s|$)", line), line
    assert re.search(r"-r \S+/requirements\.pov\.lock(\s|$)", line), line
    for safe in ("--require-hashes", "--only-binary=:all:", "-I"):
        assert safe in line.split(), (safe, line)
    assert (home.state / "pov.json").read_text() == '{"pov": true}\n'
    assert oct((home.state / "pov.json").stat().st_mode & 0o777) == "0o600"
    both = LOCK.read_bytes() + POV_LOCK.read_bytes() + b"pov\n"
    assert home.stamp() == _sha(both), "on: the stamp covers both locks and the choice"


def test_changing_the_choice_builds_the_environment_anew(tmp_path: Path) -> None:
    home = Home(tmp_path)
    assert home.wake("--no-pov").returncode == 0 and len(home.installs()) == 1
    assert home.wake("--no-pov").returncode == 0 and len(home.installs()) == 1, "same: current"
    assert home.wake("--pov").returncode == 0
    assert len(home.installs()) == 2, "a changed choice is not current"
    assert "requirements.pov.lock" in home.installs()[1]
    assert home.wake().returncode == 0 and len(home.installs()) == 2, "the kept choice stands"
    assert home.wake("--no-pov").returncode == 0
    assert len(home.installs()) == 3 and "requirements.pov.lock" not in home.installs()[2]
    assert home.stamp() == _sha(LOCK.read_bytes())


def test_a_wake_without_a_choice_follows_the_kept_one_and_is_off_by_default(tmp_path: Path) -> None:
    fresh = Home(tmp_path / "a")
    assert fresh.wake().returncode == 0
    assert "requirements.pov.lock" not in fresh.installs()[0]
    assert not (fresh.state / "pov.json").exists(), "nothing is kept before the player chooses"
    kept = Home(tmp_path / "b")
    kept.state.mkdir(parents=True)
    (kept.state / "pov.json").write_text('{"pov": true}\n')
    assert kept.wake().returncode == 0
    assert "requirements.pov.lock" in kept.installs()[0]


def test_control_a_word_other_than_yes_is_off(tmp_path: Path) -> None:
    home = Home(tmp_path)
    home.state.mkdir(parents=True)
    (home.state / "pov.json").write_text('{"pov": 1}\n')
    assert home.wake().returncode == 0
    assert "requirements.pov.lock" not in home.installs()[0]


def test_the_pov_lock_holds_websockets_only_pinned_with_a_hash() -> None:
    text = POV_LOCK.read_text(encoding="utf-8")
    pins = re.findall(r"^([A-Za-z0-9_.-]+)==(\S+) \\$", text, re.MULTILINE)
    assert pins == [("websockets", "17.4")]
    assert re.findall(r"--hash=sha256:[0-9a-f]{64}", text)


def test_the_lock_holds_the_sdk_with_the_view_and_the_versions_agree() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert "cosmergon-agent[dashboard]>=0.34.0" in project["dependencies"]
    locked = LOCK.read_text(encoding="utf-8")
    assert re.search(r"^cosmergon-agent==0\.34\.0 ", locked, re.MULTILINE)
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == project["version"] == "0.1.19"


def _helper(source: str, name: str) -> str:
    from test_qml import _blocks

    return next(b for b in _blocks(source, "Helper") if f"id: {name}" in b)


def test_the_bar_gives_the_choice_to_a_click_and_not_to_its_own_look() -> None:
    """The look at the start of the shell may run before ``pov.json`` is read: a flag from it
    would overwrite the kept choice with "no"."""
    service = (ROOT / "qml" / "Service.qml").read_text(encoding="utf-8")
    bar = (ROOT / "qml" / "BarWidget.qml").read_text(encoding="utf-8")
    waker = _helper(service, "waker")
    assert "Model.povFlag(root.pov)" in waker and "look ? [] :" in waker
    assert "Model.isPovAction(action)" in bar and "service.setPov(!service.pov)" in bar
    assert "pov: service ? service.pov : false" in bar


def test_the_bars_writer_of_the_choice_makes_no_folder_and_takes_no_typed_text() -> None:
    """``forget`` removes the state folder; a writer that made it anew would bring it back
    (Betreuer-Durchgang c6f552f). The one argument besides the folder is fixed text."""
    service = (ROOT / "qml" / "Service.qml").read_text(encoding="utf-8")
    writer = _helper(service, "povWriter")
    assert "mkdir" not in writer and 'test -d "$1"' in writer
    assert "umask 077" in writer
    assert "[root.stateDir, Model.povFileText(value)]" in writer
