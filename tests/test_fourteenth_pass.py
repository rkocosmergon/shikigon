"""The fourteenth review (cos20 #492, the betreuer pass at ``6d2f246``): one promise the code did
not keep, and two things beyond it.

1. (Pflicht 1) the README said chat.jsonl keeps the questions and answers "all of them", next to
   talk.json "each answer cut at 2,000 characters"; since 0.1.14 the brain cuts both when it
   writes chat.jsonl. Every exchange is still kept (cos20 #468), each one cut, and the README
   says so;
2. (Kür 2) journal_recent.json, which the bar reads in the shared shell, had no bound of its own:
   a move the world named stood uncut in several fields of its line. The bar now gets only the
   fields it shows, each cut; and the head is offered no move by a name longer than any the
   world has (found while building: a line past 128 KiB emptied the whole journal at its cut);
3. (Kür 3) ``is_placeholder`` took any link for the user's install, also a link to Omarchy's
   stub. A link is read where it points.

Each test is red against ``6d2f246`` (counter-test), except where its docstring says it guards
the form of a fix.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "brain"))

import connections
import head
import shikigon_brain as brain


@pytest.fixture(autouse=True)
def _private_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


# --- 1: the README says how long each exchange is kept -------------------------------------------


def _row(name: str) -> str:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    start = f"| `~/.local/state/shikigon/{name}`"
    return next(line for line in readme.splitlines() if line.startswith(start))


def test_the_readme_says_how_long_each_question_and_answer_is_kept() -> None:
    chat, talk = _row("chat.jsonl"), _row("talk.json")
    assert "all of them" in chat, "every exchange is kept (cos20 #468)"
    assert f"each question kept to {brain.QUESTION_CHARS} characters" in chat, chat
    assert f"each answer to {brain.ANSWER_CHARS:,}" in chat, chat
    assert f"each answer cut at {brain.ANSWER_CHARS:,} characters" in talk, talk


# --- 2: the bar gets the journal's texts cut, and only the ones it shows -------------------------


def test_a_world_offers_no_move_by_a_name_longer_than_any_it_has() -> None:
    """The source: a name past ``MOVE_NAME_CHARS`` is never a move the head may choose, so it
    reaches neither the journal nor the bar (the longest real one has 22 characters)."""
    state = SimpleNamespace(available_actions={"x" * 33: {}, "set_tournament_compass": {}})
    moves = head.offered(state, frozenset())
    assert "set_tournament_compass" in moves
    assert not [action for action in moves if len(action) > head.MOVE_NAME_CHARS]


def test_a_name_the_world_gives_a_move_reaches_the_bar_only_cut() -> None:
    """The bar's own bound, also for a field the journal gets later (``later``). 20,000
    characters in five fields stay below the journal's cut, which would empty it."""
    name = "x" * 20_000
    brain.journal({"at": 1, "tick": 2, "action": name, "words": name, "ok": False, "why": "no",
                   "advice": {"head": True, "choice": name, "tries": [{"action": name}]},
                   "later": name})
    raw = (brain.state_dir() / brain.BAR_JOURNAL).read_text()
    shown = json.loads(raw)
    assert len(shown) == 1
    line = shown[0]
    assert set(line) == {"at", "action", "words", "ok", "why"}, sorted(line)
    assert all(len(line[k]) <= brain.SENTENCE_CHARS for k in ("action", "words", "why"))
    assert line["ok"] is False
    assert len(raw) < 2 * brain.SENTENCE_CHARS + 200


def test_the_bar_still_tells_each_kind_of_line_apart() -> None:
    """Guards the form: what ``journalLine`` branches on (an empty lesson, a word without a
    preset, a move without ``ok``) comes through as the journal has it."""
    brain.journal({"at": 1, "instruction": "farm", "preset": None, "ok": False},
                  {"at": 2, "learned": "", "ok": False, "why": "the answer did not fit"},
                  {"at": 3, "action": "wait"})
    word, lesson, move = json.loads((brain.state_dir() / brain.BAR_JOURNAL).read_text())
    assert word == {"at": 1, "instruction": "farm", "preset": None, "ok": False}
    assert lesson == {"at": 2, "learned": "", "ok": False, "why": "the answer did not fit"}
    assert move == {"at": 3, "action": "wait"}


def test_the_bar_gets_every_field_its_journal_line_reads() -> None:
    """Guards the form: each ``entry.<field>`` that ``journalLine`` and ``moveWords`` read is
    one the brain writes for the bar; a field read there and not listed would be empty."""
    source = (ROOT / "qml" / "ShikigonModel.js").read_text(encoding="utf-8")
    read: set[str] = set()
    for function in ("journalLine", "moveWords"):
        body = re.search(rf"^function {function}\(.*?^}}", source, re.MULTILINE | re.DOTALL)
        assert body, function
        read |= set(re.findall(r"\bentry\.(\w+)", body.group(0)))
    assert read, "the functions were not found"
    written = set(brain.BAR_JOURNAL_TEXTS) | {"ok"}
    assert read <= written, sorted(read - written)


# --- 3: a link is read where it points ---------------------------------------------------------


def _stub(folder: Path) -> Path:
    stub = folder / "pi-stub"
    stub.write_text('#!/bin/sh\nmise use -g --quiet "pi" || exit 1\nexec pi "$@"\n')
    stub.chmod(0o755)
    return stub


def test_a_link_to_omarchys_stub_is_the_placeholder(tmp_path: Path) -> None:
    link = tmp_path / "pi"
    link.symlink_to(_stub(tmp_path))
    assert connections.is_placeholder(link)


def test_a_dead_link_or_a_loop_counts_as_the_placeholder(tmp_path: Path) -> None:
    dead, loop = tmp_path / "dead", tmp_path / "loop"
    dead.symlink_to(tmp_path / "gone")
    loop.symlink_to(loop)
    assert connections.is_placeholder(dead)
    assert connections.is_placeholder(loop)


def test_a_link_to_a_real_program_is_not_the_placeholder(tmp_path: Path) -> None:
    """Guards the form: mise's shims are links to its binary, far past the stub's size."""
    program = tmp_path / "mise"
    program.write_bytes(b"\x7fELF" + b"\0" * 4096)
    link = tmp_path / "pi"
    link.symlink_to(program)
    assert not connections.is_placeholder(link)
