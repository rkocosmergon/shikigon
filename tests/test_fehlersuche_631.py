"""Guards for the shikigon findings of the fault search of 05.11.2026 (cos20 #631).

A look back could not read the rules it is sent to: lines over the slice, and a header naming
lines the text did not hold (K-E, K-BR). The head's picture lost the lines it should copy when the
cut came (K-D). The README promised a line for each tournament mission's end, which the world
removes with the tournament (K-J). A key on the edge of a lesson's length stayed half (K-BD).
``forget`` kept the world's way to delete an agent with an account from the player (K-K). The bar
kept a key typed for another address, the talk and journal of a removed agent, and stopped waiting
for an answer the agent may still give (K-BO, K-AC, K-BN).

What only the bar does is read from its source here; Quickshell is not in the test container.
Red on a1d5df2, the positive controls green.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "brain"))

import advisor
import connections
import head
import lessons
import reading
import shikigon_brain as brain
from cosmergon_agent.state import GameState

KEY = "AGENT-test:0123456789abcdef0123456789abcdef"
README = (ROOT / "README.md").read_text(encoding="utf-8")
BAR = (ROOT / "qml" / "BarWidget.qml").read_text(encoding="utf-8")
# One line, as the world sends them; what a lesson points to stands far behind the first slice.
RULES = "word " * 1300 + "income_per_tick: what a field earns " + "word " * 100


@pytest.fixture(autouse=True)
def home_here(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    brain.private_state()
    yield tmp_path


def _folder(tmp_path: Path, lines: list[str]) -> Path:
    (tmp_path / "moves.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return tmp_path


def _read_to_the_end(folder: Path, name: str) -> list[str]:
    """As the model does: on from the line the header names as the last. With an end of its own."""
    pieces, on = [], 1
    for _ in range(50):
        header, _, text = reading.serve(folder, name, on, on + 39).partition("\n")
        found = re.search(r"lines (\d+)-(\d+) of (\d+)", header)
        assert found, header
        first, last, every = map(int, found.groups())
        assert first == on
        pieces.append(text)
        if last >= every:
            return pieces
        assert last >= first, f"no progress: {header}"
        on = last + 1
    raise AssertionError("no end after 50 reads")


def test_the_header_names_only_what_the_text_holds(tmp_path):
    lines = [f"L{i:02d} " + "x" * 315 for i in range(1, 41)]
    header, text = reading.serve(_folder(tmp_path, lines), "moves.txt", 1, 40).split("\n", 1)
    last = max(int(line[1:3]) for line in text.split("\n") if line.startswith("L"))
    assert f"lines 1-{last} of 40" in header, header


def test_reading_on_loses_no_line(tmp_path):
    lines = [f"L{i:02d} " + "x" * 315 for i in range(1, 41)]
    whole = "\n".join(_read_to_the_end(_folder(tmp_path, lines), "moves.txt"))
    assert all(f"L{i:02d} " in whole for i in range(1, 41))


def test_short_lines_read_as_before(tmp_path):
    lines = [f"L{i:02d} short" for i in range(1, 41)]
    assert "L40 short" in reading.serve(_folder(tmp_path, lines), "moves.txt", 1, 40)


def test_the_rules_can_be_read_to_their_end(tmp_path):
    """``lessons.income_line`` sends the model to ``world.txt: income_per_tick``."""
    folder = lessons.review_folder(tmp_path, [], "n", RULES)
    whole = "".join(_read_to_the_end(folder, "world.txt"))
    assert "income_per_tick" in whole
    whole = whole.replace("\n", "").replace("<<<", "").replace(">>>", "")
    kept = advisor.unmarked((folder / "world.txt").read_text().rstrip()).replace("\n", "")
    assert kept in whole, f"{len(kept)} characters, read {len(whole)}"


def test_the_world_stays_between_the_marks(tmp_path):
    folder = lessons.review_folder(tmp_path, [], "n", RULES)
    _, text = reading.serve(folder, "world.txt", 1, 40).split("\n", 1)
    assert text.startswith("<<<") and text.endswith(">>>")


def _state() -> GameState:
    state = {"agent_id": "a", "agent_name": "n", "energy_balance": 5.0, "tick": 1}
    return GameState.from_api(state)


def test_a_short_picture_has_the_lines_to_copy():
    text = head.brief(_state(), {}, [("evolve", {"field_id": "f"})], "", [])
    assert "The rules would play" in text


def test_the_cut_leaves_the_lines_to_copy():
    """The device measured 7,968 of 8,000 characters at most: a little more is enough."""
    line = "evolve, the world answered ok, energy +12.5 " + "y" * 150
    last = [f"tick {i}: {line}" for i in range(50)]
    text = head.brief(_state(), {}, [("evolve", {"field_id": "f"})], "", last)
    assert "[cut:" in text
    assert "The rules would play" in text


def _look_back(home: Path, answer: str) -> dict:
    moves = [{"action": "wait", "energy": 100.0 + i, "tick": i, "at": i} for i in range(60)]
    lines = [json.dumps(move) for move in moves]
    (home / lessons.JOURNAL_FILE).write_text("\n".join(lines) + "\n")

    async def send(_messages: list, _tokens: int) -> str:
        return answer

    conf = {"base_url": "http://127.0.0.1:1", "model": "m"}
    return asyncio.run(lessons.learn(home, conf, "facts", send, force=True, secret=KEY))


def _lesson(text: str) -> str:
    return json.dumps({"lessons": text, "avoid": "a" * 60, "double_down": "b" * 60})


def test_a_whole_key_stays_out():
    done = _look_back(brain.state_dir(), _lesson("w " * 60 + KEY))
    assert done["why"] == "the answer held its key and was dropped"
    assert lessons.kept(brain.state_dir()) == []


def test_half_a_key_stays_out():
    """Text without a space: the cut falls inside the key, and the search for it came after."""
    home = brain.state_dir()
    done = _look_back(home, _lesson("x" * (500 - len(KEY) // 2) + KEY))
    assert KEY[:16] not in json.dumps(lessons.kept(home)), done


def test_a_lesson_without_the_key_is_kept():
    home = brain.state_dir()
    assert _look_back(home, _lesson("w " * 60)).get("ok") is True
    assert len(lessons.kept(home)) == 1


def test_the_readme_promises_only_what_the_brain_can_read():
    """The world sets ``settled`` and removes the tournament's missions in one transaction."""
    line = next(z for z in README.splitlines() if "journal.jsonl" in z and "tournament" in z)
    assert "how each tournament mission ended," not in line
    assert "removes its missions" in line


def _forget(code: int, body: dict) -> dict:
    brain.write_private(
        brain.credentials_path(),
        {"agent_id": "a", "api_key": KEY, "world": "https://cosmergon.com:443"},
    )

    def delete(*_a: object, **_k: object) -> SimpleNamespace:
        return SimpleNamespace(status_code=code, json=lambda: body)

    return brain.forget({"world": {"base_url": "https://cosmergon.com"}}, delete)


def test_forget_deletes_on_a_yes():
    assert _forget(200, {})["ok"] is True


def test_forget_passes_on_the_way_the_world_names():
    says = "This agent belongs to an account. The account holder deletes it with the master key."
    done = _forget(403, {"error": {"code": 403, "message": says, "type": "http_error"}})
    assert done["ok"] is False and brain.credentials_path().exists()
    assert "master key" in done["why"] and "403" in done["why"]


def test_forget_without_a_sentence_still_says_the_number():
    done = _forget(500, {})
    assert done["why"] == "the server answered 500; nothing was removed"


def _file_view(name: str) -> str:
    start = BAR.index(f"id: {name}\n")
    return BAR[start : BAR.index("\n  }\n", start)]


def test_a_key_sent_leaves_the_field():
    body = BAR[BAR.index("function submitKey()") :]
    assert 'keyField.text = ""' in body[: body.index("\n  }\n")]


def test_going_back_leaves_no_typed_key_in_the_field():
    """Esc, another address that wants a key, Enter: the key for A went to B."""
    assert len(re.findall(r'keyField\.text\s*=\s*""', BAR)) >= 2


@pytest.mark.parametrize("name", ["statusFile", "lessonsFile", "chatFile", "journalFile"])
def test_every_file_of_the_bar_knows_it_can_be_gone(name):
    assert "onLoadFailed" in _file_view(name)


def test_the_bar_waits_as_long_as_the_brain():
    limit = int(re.search(r"root\.pending\.at > (\d+)", BAR).group(1))
    assert advisor.ANSWER_PATIENCE * 20 <= limit  # timeout_s from config/shikigon.toml
    assert connections.AGENT_ANSWER_S <= limit
