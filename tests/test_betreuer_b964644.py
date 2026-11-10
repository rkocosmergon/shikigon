"""Guards for the Betreuer-Durchgang b964644 (cos20 #492).

B1 (Pflicht): a compass word refused before it left this machine (``SendRefusedError``: the
world's port is not the player's, or its key may not go there) stood in the journal as "no
answer from the world; its next move shows the compass". Nothing was sent, and no next move came:
``with_agent`` puts the Shikigon to sleep. The CHANGELOG of 0.1.14 promises that a word that could
not reach the world says so. Now ``sent: False``, and the bar says "not sent".

B2 (Kür): ``advisor forget`` removed the choice and the key outside ``files.held``, so a ``try`` or
``advise`` that read the choice before wrote it back after (variant of V1, 4866b11). ``keep_key``
truncated the key file and wrote it in place, outside the hold as well.

B3 (Kür): the bar's view of the journal and of the talk was read and written outside any lock;
a view read before another process added its line could be written after that process's view.
Now under the lock ``files.append`` takes on the same file.

Red on b964644, the controls green.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
BRAIN = ROOT / "brain"
QML = ROOT / "qml"
sys.path.insert(0, str(BRAIN))

import connections
import files
import shikigon_brain as brain
from test_betreuer_1d81304 import _holder
from test_brain import TABLE
from test_compass_in_the_panel import CompassWorld, _code, _fn


@pytest.fixture(autouse=True)
def _private_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(files, "HELD_WAIT_S", 0.3)  # a lock held by another process: soon out


# ---------- B1 -----------------------------------------------------------------------------------


async def test_b1_a_word_never_sent_says_so() -> None:
    from cosmergon_agent.exceptions import SendRefusedError

    class NotOurs(CompassWorld):
        async def set_compass(self, preset: str) -> dict[str, Any]:
            raise SendRefusedError(connections.NOT_OURS)

    with pytest.raises(SendRefusedError):  # with_agent puts it to sleep, as before
        await brain.instruct(NotOurs(), "attack now", TABLE)
    line = json.loads((brain.state_dir() / "journal.jsonl").read_text().splitlines()[-1])
    assert line["ok"] is False and line["sent"] is False and "answered" not in line, line
    recent = json.loads((brain.state_dir() / brain.BAR_JOURNAL).read_text())
    assert recent[-1]["sent"] is False and "answered" not in recent[-1], recent


async def test_b1_control_a_word_without_an_answer_stays_no_answer() -> None:
    class Unanswered(CompassWorld):
        async def set_compass(self, preset: str) -> dict[str, Any]:
            raise TimeoutError("the answer did not come")

    with pytest.raises(TimeoutError):
        await brain.instruct(Unanswered(), "attack now", TABLE)
    recent = json.loads((brain.state_dir() / brain.BAR_JOURNAL).read_text())
    assert recent[-1]["answered"] is False and "sent" not in recent[-1], recent


def sent_problems(js: str) -> list[str]:
    """``journalLine`` says "not sent" for ``sent: false``, branching on the key."""
    body = _fn(_code(js), "function journalLine(entry)")
    if not re.search(r"entry\.sent === false\)\s*return \{ text: word \+ \"not sent", body):
        return ["a word never sent is shown as no answer or not taken"]
    return []


def test_b1_the_bar_says_not_sent() -> None:
    assert sent_problems((QML / "ShikigonModel.js").read_text(encoding="utf-8")) == []


def test_b1_the_sent_guard_sees_what_it_guards() -> None:
    old = ('function journalLine(entry) {\n  var failed = entry.ok === false\n'
           '  if (failed && entry.answered === false) return { text: word + "no answer" }\n}\n')
    assert sent_problems(old)


def test_b1_the_readme_says_not_sent() -> None:
    readme = re.sub(r"\s+", " ", (ROOT / "README.md").read_text(encoding="utf-8"))
    assert ("A click that could not even be sent (the world's port on this machine is not yours, "
            "or its key may not go there) is said as not sent in the journal, and the panel says "
            "why the Shikigon sleeps.") in readme


# ---------- B2 -----------------------------------------------------------------------------------


def _cfg() -> dict[str, Any]:
    return {"brain": {"decider": "tree", "advisor": {}}, "world": {"base_url": "http://w.test"}}


def _chosen_and_key() -> Path:
    home = brain.state_dir()
    home.mkdir(parents=True, exist_ok=True)
    connections.choose(home, {"via": "http", "base_url": "https://model.test/v1", "model": "m"})
    connections.keep_key(home, "sk-old", "https://model.test/v1")
    return home


def test_b2_forget_waits_for_the_folder() -> None:
    home = _chosen_and_key()
    child = _holder("held", 30)
    try:
        with pytest.raises(TimeoutError):
            brain.advisor_command(_cfg(), "forget", [])
    finally:
        child.kill()
        child.wait()
    assert (home / connections.CHOICE_FILE).exists(), "nothing removed past the lock"
    assert (home / connections.KEY_FILE).exists()


def test_b2_control_forget_without_another_process() -> None:
    home = _chosen_and_key()
    assert brain.advisor_command(_cfg(), "forget", [])["ok"]
    assert not (home / connections.CHOICE_FILE).exists()
    assert not (home / connections.KEY_FILE).exists()


def test_b2_a_key_waits_for_the_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    home = _chosen_and_key()
    given = json.dumps({"key": "sk-new", "where": "https://model.test/v1"})
    monkeypatch.setattr(brain, "json_on_stdin", lambda: json.loads(given))
    child = _holder("held", 30)
    try:
        with pytest.raises(TimeoutError):
            brain.advisor_command(_cfg(), "key", [])
    finally:
        child.kill()
        child.wait()
    assert connections.kept_key(home, "https://model.test/v1") == "sk-old"
    assert brain.advisor_command(_cfg(), "key", [])["ok"]  # control: without the other process
    assert connections.kept_key(home, "https://model.test/v1") == "sk-new"


def test_b2_a_key_is_written_whole() -> None:
    """A new file takes the name, as every whole file the brain keeps (``files.write_json``):
    a reader never finds it truncated. On b964644 the same file was truncated and rewritten."""
    home = _chosen_and_key()
    before = (home / connections.KEY_FILE).stat().st_ino
    connections.keep_key(home, "sk-new", "https://model.test/v1")
    after = (home / connections.KEY_FILE).stat()
    assert after.st_ino != before
    assert after.st_mode & 0o777 == 0o600


# ---------- B3 -----------------------------------------------------------------------------------


FILE_HOLDER = """
import fcntl, os, sys, time
fd = os.open(sys.argv[1], os.O_RDONLY)
fcntl.flock(fd, fcntl.LOCK_EX)
print("held", flush=True)
time.sleep(float(sys.argv[2]))
"""


def _file_holder(path: Path, seconds: float) -> subprocess.Popen[str]:
    """Another process in ``files.append`` on ``path``: it holds the file's lock."""
    child = subprocess.Popen([sys.executable, "-I", "-c", FILE_HOLDER, str(path), str(seconds)],
                             stdout=subprocess.PIPE, text=True)
    assert child.stdout is not None
    assert child.stdout.readline().startswith("held")
    return child


@pytest.mark.parametrize("show", ["show_journal", "show_talk"])
def test_b3_the_view_waits_for_a_writer_of_its_file(show: str) -> None:
    path = brain.state_dir() / ("journal.jsonl" if show == "show_journal" else "chat.jsonl")
    files.append(path, {"at": 1, "action": "evolve", "ok": True, "q": "q", "a": "a"})
    view = brain.state_dir() / (brain.BAR_JOURNAL if show == "show_journal" else brain.BAR_TALK)
    view.unlink(missing_ok=True)
    child = _file_holder(path, 30)
    try:
        with pytest.raises(TimeoutError):
            getattr(brain, show)()
    finally:
        child.kill()
        child.wait()
    assert not view.exists(), "no view written past the lock"
    getattr(brain, show)()  # control: without the other process
    assert json.loads(view.read_text())


def test_b3_control_no_journal_yet_an_empty_view() -> None:
    brain.state_dir().mkdir(parents=True, exist_ok=True)
    brain.show_journal()
    brain.show_talk()
    assert json.loads((brain.state_dir() / brain.BAR_JOURNAL).read_text()) == []
    assert json.loads((brain.state_dir() / brain.BAR_TALK).read_text()) == []
    # Since 5d315a0 K1 the view opens the file as ``append`` does, so it holds its lock even
    # before the first line: an empty journal, the player's alone.
    journal = brain.state_dir() / "journal.jsonl"
    assert journal.read_bytes() == b"" and (journal.stat().st_mode & 0o777) == 0o600


def test_b3_a_view_follows_a_replaced_file() -> None:
    """``keep_private`` replaces a file others could read with a new one: the view reads that."""
    path = brain.state_dir() / "journal.jsonl"
    files.append(path, {"at": 1, "action": "evolve", "ok": True})
    os.chmod(path, 0o644)
    files.keep_private(brain.state_dir())
    files.append(path, {"at": 2, "action": "trade", "ok": True})
    brain.show_journal()
    shown = json.loads((brain.state_dir() / brain.BAR_JOURNAL).read_text())
    assert [line["action"] for line in shown] == ["evolve", "trade"]
