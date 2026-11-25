"""The 3D picture of the terminal's view (0.1.20, cos20 #853).

The view through a marauder's eyes is drawn in characters by the terminal itself (its package
``websockets`` is in the main lock). The choice in the panel, ``--pov`` / ``--no-pov`` of
``shikigon-wake``, is about the 3D picture: with yes the wake script fetches the program
``cosmergon-pov`` with the terminal's own command (``cosmergon_agent.view_install --if-missing``,
every download checked against a SHA-256 in that locked package). The environment is the same
either way, and a failed fetch is a sentence, not a failed wake. The panel's question is tested
in ``tests/qml/tst_model.qml``.

Red on 612b6d7 (0.1.19 with the follower), the controls green.
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

import pytest

ROOT = Path(__file__).resolve().parent.parent
WAKE = ROOT / "brain" / "shikigon-wake"
LOCK = ROOT / "requirements.lock"
TERMINAL = ROOT / "brain" / "shikigon-terminal"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Home:
    """A home for ``shikigon-wake``: the system's ``python3`` builds an environment whose Python
    does nothing and writes down what it was asked (``installs``). No network, no package."""

    def __init__(self, folder: Path, view_code: int = 0) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        self.home = folder / "home"
        self.state = self.home / ".local" / "state" / "shikigon"
        self.venv = self.home / ".local" / "share" / "shikigon" / "venv"
        self.asked = folder / "asked.log"
        self.asked.write_text("")
        self.stub = folder / "stub"
        self.stub.mkdir()
        fake = folder / "fake-python"
        fake.write_text(
            f'#!/bin/sh\necho "$*" >> "{self.asked}"\n'
            f'case "$*" in *view_install*) exit {view_code} ;; esac\nexit 0\n'
        )
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

    def views(self) -> list[str]:
        """The calls of the terminal's command that fetches the 3D picture."""
        return [line for line in self.asked.read_text().splitlines() if "view_install" in line]

    def said(self) -> dict[str, str]:
        return json.loads((self.state / "setup.json").read_text())

    def stamp(self) -> str:
        return (self.venv / ".lock-sha256").read_text().strip()


def test_waking_without_the_3d_picture_fetches_nothing(tmp_path: Path) -> None:
    home = Home(tmp_path)
    done = home.wake("--no-pov")
    assert done.returncode == 0, done.stdout + done.stderr
    (line,) = home.installs()
    assert re.search(r"-r \S+/brain/\.\./requirements\.lock(\s|$)", line), line
    assert home.views() == [], "no picture unless it is chosen"
    assert (home.state / "pov.json").read_text() == '{"pov": false}\n'
    assert home.stamp() == _sha(LOCK.read_bytes())


def test_waking_with_the_3d_picture_fetches_it_with_the_terminals_command(tmp_path: Path) -> None:
    home = Home(tmp_path)
    done = home.wake("--pov")
    assert done.returncode == 0, done.stdout + done.stderr
    (line,) = home.installs()
    for safe in ("--require-hashes", "--only-binary=:all:", "-I"):
        assert safe in line.split(), (safe, line)
    (view,) = home.views()
    assert view.split() == ["-I", "-m", "cosmergon_agent.view_install", "--if-missing"], view
    assert (home.state / "pov.json").read_text() == '{"pov": true}\n'
    assert oct((home.state / "pov.json").stat().st_mode & 0o777) == "0o600"
    assert home.stamp() == _sha(LOCK.read_bytes()), "the choice no longer changes the environment"


def test_changing_the_choice_keeps_the_environment(tmp_path: Path) -> None:
    home = Home(tmp_path)
    assert home.wake("--no-pov").returncode == 0 and len(home.installs()) == 1
    assert home.wake("--pov").returncode == 0
    assert len(home.installs()) == 1 and len(home.views()) == 1
    assert home.wake().returncode == 0 and len(home.views()) == 2, "the kept choice stands"
    assert home.wake("--no-pov").returncode == 0 and len(home.views()) == 2
    assert len(home.installs()) == 1


def test_a_wake_without_a_choice_follows_the_kept_one_and_is_no_by_default(tmp_path: Path) -> None:
    fresh = Home(tmp_path / "a")
    assert fresh.wake().returncode == 0 and fresh.views() == []
    assert not (fresh.state / "pov.json").exists(), "nothing is kept before the player chooses"
    kept = Home(tmp_path / "b")
    kept.state.mkdir(parents=True)
    (kept.state / "pov.json").write_text('{"pov": true}\n')
    assert kept.wake().returncode == 0 and len(kept.views()) == 1


def test_control_a_word_other_than_yes_is_no(tmp_path: Path) -> None:
    home = Home(tmp_path)
    home.state.mkdir(parents=True)
    (home.state / "pov.json").write_text('{"pov": 1}\n')
    assert home.wake().returncode == 0 and home.views() == []


def test_a_picture_that_could_not_be_fetched_is_a_sentence_not_a_failed_wake(
    tmp_path: Path,
) -> None:
    home = Home(tmp_path, view_code=1)
    done = home.wake("--pov")
    assert done.returncode == 0, done.stdout + done.stderr
    said = home.said()
    assert said["stage"] == "done"
    assert said["said"].startswith("The 3D picture could not be fetched; the view is drawn")


def test_the_lock_holds_the_sdk_with_the_view_and_the_versions_agree() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert "cosmergon-agent[dashboard]>=0.37.0" in project["dependencies"]
    locked = LOCK.read_text(encoding="utf-8")
    assert re.search(r"^cosmergon-agent==0\.37\.0 ", locked, re.MULTILINE)
    # The characters of the view need websockets: it is in the main lock, the second lock is gone.
    assert re.search(r"^websockets==", locked, re.MULTILINE)
    assert not (ROOT / "requirements.pov.lock").exists()
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == project["version"] == "0.1.20"


def test_the_terminal_lets_the_picture_open_only_over_the_view() -> None:
    """Without the variable the terminal (0.37.0) opens the picture beside its window while the
    follower is still starting; Shikigon has the follower and asks for it."""
    text = TERMINAL.read_text(encoding="utf-8")
    assert re.search(r"^export COSMERGON_POV_WINDOW=follow$", text, re.MULTILINE)
    assert text.index("COSMERGON_POV_WINDOW=follow") < text.index("exec ")

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


def test_the_terminals_command_checks_every_download_before_it_unpacks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The locked package's own command: bytes that are not the written ones are refused before
    anything is unpacked or placed, and only https is asked (CHANGELOG 0.1.20)."""
    import io

    from cosmergon_agent import view_install

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(view_install.platform, "system", lambda: "Linux")
    monkeypatch.setattr(view_install.platform, "machine", lambda: "x86_64")
    asked: list[str] = []

    def opener(url: str) -> io.BytesIO:
        asked.append(url)
        return io.BytesIO(b"not the program")

    monkeypatch.setattr(view_install, "_open", opener)
    assert view_install.main([]) == 1
    assert not (tmp_path / "data" / "cosmergon-pov").exists()
    assert asked and all(url.startswith("https://") for url in asked)
    with pytest.raises(view_install.InstallError):
        view_install.fetch("http://example.invalid/x", "0" * 64, 10)
