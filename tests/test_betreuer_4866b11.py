"""Guards for the Betreuer-Durchgang 4866b11 (cos20 #492, plan 6706958503).

B2 (Pflicht): the README said "the compass that holds carries a ●". The panel knows the compass
of the last move's state and of your clicks; one changed in the terminal shows after the next
move. And a word with no answer from the world stood as "not taken", although the world may
have taken it before its answer was lost. Now the README says what the panel knows, and the
journal says "no answer" (``answered``, a key) instead.

B1: ``note_another_move`` read, changed and wrote ``status.json`` outside any lock; a ``tick`` by
hand ending its move at that moment lost its status until the next move. N1 (found while
building): the panel shows the status's reason only for a failed move, so after a move that worked
the note stood nowhere in the panel (README "the panel says so"). Now the note is a journal line
of its own (``waiting``): only added, never read back, and the panel shows every line.

V1 (variant of B1, found while building): ``advisor try`` and ``advisor advise`` read, changed
and wrote ``advisor.json`` without the lock; two processes of the panel may run at once.

B3: a paste lands only in the line that still has the focus. Closing the panel from the question
line was no change of step, and a paste under way still went in.

Red on 4866b11, the controls green.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
BRAIN = ROOT / "brain"
QML = ROOT / "qml"
sys.path.insert(0, str(BRAIN))

import connections
import shikigon_brain as brain
from test_betreuer_1d81304 import _holder
from test_brain import TABLE
from test_compass_in_the_panel import CompassWorld, _code, _fn


@pytest.fixture(autouse=True)
def _private_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


def _status() -> dict[str, Any]:
    return json.loads((brain.state_dir() / "status.json").read_text())


# ---------- B2 -----------------------------------------------------------------------------------


async def test_b2_a_word_without_an_answer_says_so_not_that_it_was_refused() -> None:
    class Unanswered(CompassWorld):
        async def set_compass(self, preset: str) -> dict[str, Any]:
            raise TimeoutError("the answer did not come")

    with pytest.raises(TimeoutError):  # with_agent answers it, as before
        await brain.instruct(Unanswered(), "attack now", TABLE)
    line = json.loads((brain.state_dir() / "journal.jsonl").read_text().splitlines()[-1])
    assert line["ok"] is False and line["answered"] is False, line
    recent = json.loads((brain.state_dir() / brain.BAR_JOURNAL).read_text())
    assert recent[-1]["ok"] is False and recent[-1]["answered"] is False, recent


async def test_b2_control_a_word_the_world_refused_is_not_taken() -> None:
    from cosmergon_agent.exceptions import RateLimitError

    class TooOften(CompassWorld):
        async def set_compass(self, preset: str) -> dict[str, Any]:
            raise RateLimitError(retry_after=30.0)

    said = await brain.instruct(TooOften(), "attack now", TABLE)
    assert said["ok"] is False
    recent = json.loads((brain.state_dir() / brain.BAR_JOURNAL).read_text())
    assert recent[-1]["ok"] is False and "answered" not in recent[-1], recent


def test_b2_the_readme_says_what_the_panel_knows() -> None:
    readme = re.sub(r"\s+", " ", (ROOT / "README.md").read_text(encoding="utf-8"))
    assert "the compass that holds carries" not in readme
    assert ("The `●` stands on the compass the panel last knows of: the one you clicked, or the "
            "one the world named at its last move; a compass you change in the terminal shows "
            "there after the next move.") in readme
    assert "when no answer comes, the journal says so, and the next move shows the compass" in (
        readme)


def answered_problems(js: str) -> list[str]:
    """``journalLine`` says "no answer" for ``answered: false``, branching on the key."""
    body = _fn(_code(js), "function journalLine(entry)")
    if not re.search(r"entry\.answered === false\)\s*return \{ text: word \+ \"no answer", body):
        return ["a word without an answer is shown as not taken"]
    return []


def test_b2_the_bar_says_no_answer() -> None:
    assert answered_problems((QML / "ShikigonModel.js").read_text(encoding="utf-8")) == []


def test_b2_the_answer_guard_sees_what_it_guards() -> None:
    old = ('function journalLine(entry) {\n  var failed = entry.ok === false\n'
           '  return failed ? { text: word + "not taken" } : {}\n}\n')
    assert answered_problems(old)


# ---------- B1, N1 -------------------------------------------------------------------------------


def test_b1_the_note_leaves_the_status_whole() -> None:
    """Nothing reads, changes and writes the status outside a move: a ``tick`` by hand ending its
    move at that moment keeps its status. On 4866b11 the note wrote the status anew."""
    brain.write_status({"mood": "awake", "last": {"action": "the hand's move", "ok": True}})
    before = (brain.state_dir() / "status.json").read_bytes()
    brain.note_another_move()
    assert (brain.state_dir() / "status.json").read_bytes() == before


def test_n1_the_panel_gets_the_note_after_a_move_that_worked() -> None:
    """The panel shows the status's reason only for a failed move (``BarWidget``, "last move");
    after a move that worked the note stood nowhere it shows. Now a journal line of its own,
    marked ``waiting`` for the bar. On 4866b11 the journal had no such line."""
    brain.write_status({"mood": "awake", "last": {"action": "evolve", "ok": True}})
    brain.note_another_move()
    recent = json.loads((brain.state_dir() / brain.BAR_JOURNAL).read_text())
    assert recent[-1].get("waiting") is True, recent
    assert recent[-1]["why"].startswith(brain.ANOTHER_MOVE + " since "), recent


def test_n1_no_reader_of_the_journal_takes_the_note_for_a_move() -> None:
    """Each reader that tells a model or a lesson of its moves wants an ``action``."""
    import head
    import lessons

    brain.note_another_move()
    home = brain.state_dir()
    assert head.recent_moves(home) == []
    assert brain.recent_moves(3) == []
    assert lessons.moves_since_lesson(lessons.journal_tail(home)) == []


def waiting_problems(js: str) -> list[str]:
    body = _fn(_code(js), "function journalLine(entry)")
    if not re.search(r"if \(entry\.waiting === true\) return \{ text: String\(entry\.why", body):
        return ["the panel does not show the note"]
    return []


def test_n1_the_bar_shows_the_note() -> None:
    assert waiting_problems((QML / "ShikigonModel.js").read_text(encoding="utf-8")) == []


def test_n1_the_note_guard_sees_what_it_guards() -> None:
    old = 'function journalLine(entry) {\n  var failed = entry.ok === false\n  return {}\n}\n'
    assert waiting_problems(old)


# ---------- V1 -----------------------------------------------------------------------------------


def _cfg() -> dict[str, Any]:
    return {"brain": {"decider": "tree", "advisor": {}}, "world": {"base_url": "http://w.test"}}


def test_v1_the_advisors_choice_waits_for_the_folder() -> None:
    home = brain.state_dir()
    home.mkdir(parents=True, exist_ok=True)
    connections.choose(home, {"via": "http", "base_url": "http://model.test/v1", "model": "m"})
    child = _holder("held", 30)
    try:
        with pytest.raises(TimeoutError):
            brain.advisor_command(_cfg(), "advise", ["3"])
    finally:
        child.kill()
        child.wait()
    assert "advise" not in connections.chosen(home), "nothing written past the lock"


def test_v1_control_without_another_process_the_choice_is_written() -> None:
    home = brain.state_dir()
    home.mkdir(parents=True, exist_ok=True)
    connections.choose(home, {"via": "http", "base_url": "http://model.test/v1", "model": "m"})
    assert brain.advisor_command(_cfg(), "advise", ["3"])["ok"]
    assert connections.chosen(home)["every"] == 3


def test_v1_every_read_change_write_of_the_choice_is_held() -> None:
    """Each ``connections.choose`` in the brain stands in a ``files.held`` with the
    ``connections.chosen`` it is made from."""
    source = (BRAIN / "shikigon_brain.py").read_text(encoding="utf-8")
    lines = source.splitlines()
    for i, line in enumerate(lines):
        if "connections.choose(" not in line:
            continue
        before = "\n".join(lines[max(0, i - 6):i])
        assert "with files.held(home):" in before, line


# ---------- B3 -----------------------------------------------------------------------------------


def paste_problems(bar: str) -> list[str]:
    finished = _fn(_code(bar), "onStreamFinished: {")
    if not re.search(r"root\.pasteTarget && root\.pasteTarget\.activeFocus && s !== \"\"\)\s*"
                     r"root\.pasteTarget\.put\(s\)", finished):
        return ["a paste lands in a line that no longer has the focus"]
    return []


def test_b3_a_paste_lands_only_where_the_focus_still_is() -> None:
    assert paste_problems((QML / "BarWidget.qml").read_text(encoding="utf-8")) == []


def test_b3_the_paste_guard_sees_what_it_guards() -> None:
    old = ('      onStreamFinished: {\n        const s = Model.pastedLine(text)\n'
           '        if (root.pasteTarget && s !== "") root.pasteTarget.put(s)\n      }\n')
    assert paste_problems(old)
