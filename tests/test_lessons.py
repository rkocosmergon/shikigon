"""Tests for what a Shikigon learns from its own moves (cos20 #451, step 2).

No network and no model: the journal is written here, the advisor is a stub that answers
what each test says.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "brain"))

import advisor
import lessons
import shikigon_brain as brain
from test_advisor import CONF, Model
from test_brain import FakeAgent, FakeDecider, _state

GOOD = json.dumps({
    "lessons": "Missions brought energy back within ten moves; listing on the market while "
               "nothing sold only cost moves. Its field held steady the whole time.",
    "avoid": "Listing on the market again and again when nothing of it sells.",
    "double_down": "Starting missions while the marauder is free; they paid back each time.",
})


@pytest.fixture(autouse=True)
def _private_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


def _write(entries: list[dict[str, Any]]) -> Path:
    home = brain.state_dir()
    home.mkdir(parents=True, exist_ok=True)
    with (home / "journal.jsonl").open("a", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry) + "\n")
    return home


def _moves(count: int, start_tick: int = 1000) -> list[dict[str, Any]]:
    """Missions win 50 energy, market listings lose 10, every third one refused."""
    moves, energy = [], 5000.0
    for number in range(count):
        action = "start_mission" if number % 2 == 0 else "market_list"
        moves.append({"at": number, "tick": start_tick + number, "energy": energy, "fields": 1,
                      "action": action, "ok": number % 3 != 2, "why": ""})
        energy += 50 if action == "start_mission" else -10
    return moves


# --- The journal carries what it learns from --------------------------------------------------


async def test_a_move_in_the_journal_carries_its_energy_and_fields() -> None:
    await brain.tick(FakeAgent(_state()), FakeDecider(("place_cells", {"preset": "blinker"})))
    line = json.loads((brain.state_dir() / "journal.jsonl").read_text().splitlines()[-1])
    assert line["energy"] == 1234.6 and line["fields"] == 1 and line["action"] == "place_cells"


async def test_a_move_in_the_journal_carries_the_goals_measure_when_the_world_gives_it() -> None:
    """cos20 #527: the total energy (energy plus offers, fields, items) is what it plays for."""
    from types import SimpleNamespace

    state = _state()
    await brain.tick(FakeAgent(state), FakeDecider(("place_cells", {"preset": "blinker"})))
    line = json.loads((brain.state_dir() / "journal.jsonl").read_text().splitlines()[-1])
    assert "total" not in line, "an older world gives no total: nothing is made up"
    state.ranking = SimpleNamespace(champion_rang=7, gesamtenergie=3_302_108.44)
    await brain.tick(FakeAgent(state), FakeDecider(("place_cells", {"preset": "blinker"})))
    line = json.loads((brain.state_dir() / "journal.jsonl").read_text().splitlines()[-1])
    assert line["total"] == 3_302_108.4 and line["energy"] == 1234.6


def test_an_energy_offer_is_judged_by_the_total_not_by_the_balance() -> None:
    """The device, 19.10.2026: an offer took 70,000 out of the balance and gave it back when it
    expired. Judged by the balance, the moves after the offer lost and the moves at its end
    won; the lesson praised selling and blamed contracts. The total does not move."""
    moves = []
    for number in range(30):
        held = 70_000.0 if 1 <= number < 16 else 0.0  # the offer, from move 1 until it expires
        moves.append({"tick": number, "energy": 100_000.0 - held, "total": 100_000.0,
                      "fields": 0, "action": "propose_contract", "ok": True, "why": ""})
    assert lessons.measure(moves) == "total"
    assert {change for _, change in lessons.rated(moves)} == {0.0}
    assert "total energy 100,000 -> 100,000" in lessons.summary(moves)
    by_balance = [{k: v for k, v in move.items() if k != "total"} for move in moves]
    assert lessons.measure(by_balance) == "energy", "without the total: the balance, as before"
    assert max(change for _, change in lessons.rated(by_balance)) == 70_000.0
    mixed = by_balance[:1] + moves[1:]
    assert lessons.measure(mixed) == "energy", "never two measures in one look back"


def test_the_journal_is_read_from_its_end_and_odd_lines_are_skipped() -> None:
    home = _write(_moves(600))
    with (home / "journal.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("not json\n")
    tail = lessons.journal_tail(home, 400)
    assert len(tail) == 399 and tail[-1]["tick"] == 1599, "the odd last line is skipped"
    assert lessons.journal_tail(brain.state_dir() / "nowhere") == []


# --- When it is time ---------------------------------------------------------------------------


def test_a_lesson_is_due_after_a_hundred_moves_since_the_last_one() -> None:
    entries = _moves(99)
    assert not lessons.due(entries)
    assert lessons.due(entries + _moves(1, 2000))
    after = entries + _moves(1) + [{"learned": "", "ok": False}] + _moves(5)
    assert len(lessons.moves_since_lesson(after)) == 5, "an attempt counts like a lesson"
    old = [{"tick": 1, "action": "wait", "ok": True}] * 200  # written before moves had energy
    assert not lessons.due(old)


def test_the_numbers_say_what_paid_and_what_did_not() -> None:
    moves = _moves(30)
    judged = lessons.rated(moves)
    assert len(judged) == 20, "the last ten moves have no ten moves after them yet"
    text = lessons.summary(moves)
    assert "30 moves, energy 5,000 -> 5,610, fields 1 -> 1." in text
    assert "- start a mission: 7 played, 3 refused, +" in text
    assert "- sell on the market: 7 played, 3 refused, +" in text
    best = text.split("Best moves:\n")[1].split("Worst moves:")[0].splitlines()
    worst = text.split("Worst moves:\n")[1].splitlines()
    assert len(best) == len(worst) == 5 and not set(best) & set(worst)
    refused = {f"tick {m['tick']}:" for m in moves if m["ok"] is False}
    assert not any(tick in line for tick in refused for line in best + worst), (
        "a refused move did nothing")
    few = lessons.summary(_moves(13))  # three judged moves, two of them played
    assert few.count("- tick") == 2, "one best, one worst: never the same move twice"
    assert lessons.summary(_moves(5)) == "", "too few moves to judge"


# --- What the model answers -------------------------------------------------------------------


def test_a_lesson_is_kept_in_the_servers_lengths_or_not_at_all() -> None:
    lesson = lessons.parse_lesson("Here it is: " + GOOD)
    assert lesson is not None and set(lesson) == {"lessons", "avoid", "double_down"}
    long = json.loads(GOOD)
    long["avoid"] = "Do not list on the market " * 20
    cut = lessons.parse_lesson(json.dumps(long))
    assert cut is not None and len(cut["avoid"]) <= 200 and not cut["avoid"].endswith(" ")
    # A cut is said, not hidden: no sentence end in reach, so the word cut is marked.
    assert cut["avoid"].endswith("…"), cut["avoid"]
    sentences = {**json.loads(GOOD), "avoid": "Sell less on the market when prices fall. " * 8}
    cut = lessons.parse_lesson(json.dumps(sentences))
    assert cut is not None and len(cut["avoid"]) <= 200 and cut["avoid"].endswith("fall."), cut
    short = {**json.loads(GOOD), "lessons": "Too short."}
    assert lessons.parse_lesson(json.dumps(short)) is None
    assert lessons.parse_lesson(json.dumps({**json.loads(GOOD), "avoid": ["a", "b"]})) is None
    assert lessons.parse_lesson("no json at all") is None


# --- Learning --------------------------------------------------------------------------------


async def test_it_learns_keeps_three_lessons_and_tells_its_journal() -> None:
    home = _write(_moves(100))
    stub = Model(GOOD)
    entry = await lessons.learn(home, CONF, "Energy: 5000", stub)
    assert entry is not None and entry["ok"] is True and entry["from_moves"] == 100
    assert entry["learned"].startswith("Missions brought energy back")
    assert "start a mission" in stub.asked[0][1]["content"], "the model is told the numbers"
    assert "start_mission" not in stub.asked[0][1]["content"], "in a player's words, not names"
    assert advisor.calls_today(home) == 1
    for _ in range(3):
        lessons.keep(home, {**json.loads(GOOD), "lessons": "x" * 100})
    assert len(lessons.kept(home)) == 3
    assert lessons.latest_text(home).startswith("x" * 100)


async def test_without_an_advisor_or_before_its_time_nothing_is_asked() -> None:
    home = _write(_moves(50))
    stub = Model(GOOD)
    assert await lessons.learn(home, CONF, "", stub) is None, "fifty moves: not yet"
    _write(_moves(50, 2000))
    assert await lessons.learn(home, {}, "", stub) is None, "a hundred, but nobody to ask"
    assert stub.asked == []


async def test_a_failing_or_unfit_answer_is_said_and_waits_for_the_next_hundred() -> None:
    home = _write(_moves(100))
    entry = await lessons.learn(home, CONF, "", Model(OSError("refused")))
    assert entry is not None and entry["ok"] is False and entry["why"] == "no answer (OSError)"
    entry = await lessons.learn(home, CONF, "", Model("I think it went fine."))
    assert entry is not None and entry["why"] == (
        "the answer did not fit: no LESSON/LESS/MORE lines in the answer")
    assert lessons.kept(home) == []


def test_an_unfit_answer_says_what_did_not_fit() -> None:
    short = {**json.loads(GOOD), "avoid": "Sell less."}
    assert lessons.read_lesson(json.dumps(short)) == (
        None, "'avoid' has 10 characters, needs 50")
    missing = {key: value for key, value in json.loads(GOOD).items() if key != "double_down"}
    assert lessons.read_lesson(json.dumps(missing))[1] == "'double_down' is missing"
    assert lessons.read_lesson("{not json}")[1] == "the JSON in the answer is broken"
    lesson, why = lessons.read_lesson(GOOD)
    assert lesson is not None and why == ""


LINES = (
    "LESSON: Missions brought energy back within ten moves; listing on the market while "
    "nothing sold only cost moves.\n"
    "**LESS:** Listing on the market again and again when nothing of it sells.\n"
    "More: Starting missions while the marauder is free; they paid back each time.\n"
)


def test_three_labelled_lines_are_a_lesson_and_need_no_quotes() -> None:
    """A small local model broke the JSON in two of six lessons on the device (14.10.2026);
    the labelled lines need no quotes, markdown around a label is tolerated."""
    lesson, why = lessons.read_lesson("Here you go:\n" + LINES)
    assert why == "" and lesson is not None
    assert lesson["avoid"] == "Listing on the market again and again when nothing of it sells."
    assert lesson["double_down"].startswith("Starting missions while the marauder is free")
    assert lesson["lessons"].startswith("Missions brought energy back")
    two = "\n".join(LINES.splitlines()[:2])
    assert lessons.read_lesson(two)[1] == "'double_down' is missing"
    # all three on one line, as the device's model wrote it once
    one_line, why = lessons.read_lesson(LINES.replace("\n", " "))
    assert why == "" and one_line == lesson
    # the answer the device's model gave (a comma for the colon): still said, not guessed
    broken = '{"lessons":"Agents can sell.","avoid","Do not waste energy evolving."}'
    assert lessons.read_lesson(broken)[1] == "the JSON in the answer is broken"
    assert "LESSON:" in lessons.LESSON_SYSTEM and "JSON" not in lessons.LESSON_SYSTEM


async def test_the_daily_limit_holds_for_lessons_too() -> None:
    home = _write(_moves(100))
    advisor.count_call(home)
    stub = Model(GOOD)
    entry = await lessons.learn(home, {**CONF, "max_per_day": 1}, "", stub)
    assert entry is not None and entry["why"] == "daily limit reached" and stub.asked == []


async def test_learn_now_needs_thirty_moves_and_writes_the_journal() -> None:
    _write(_moves(29))
    cfg0 = {"brain": {"advisor": CONF}}
    assert await brain.learn(cfg0, _state(), force=True, send=Model(GOOD)) is None, (
        "29 moves: too few to draw a lesson from")
    _write(_moves(1, 2000))
    cfg = {"brain": {"advisor": CONF}}
    entry = await brain.learn(cfg, _state(), force=True, send=Model(GOOD))
    assert entry is not None and entry["ok"] is True
    last = json.loads((brain.state_dir() / "journal.jsonl").read_text().splitlines()[-1])
    assert last["learned"] == entry["learned"]
    assert await brain.learn(cfg, _state(), force=True, send=Model(GOOD)) is None, (
        "no new moves since the lesson")


async def test_the_running_brain_starts_a_lesson_beside_its_moves_only_when_due() -> None:
    cfg = {"brain": {"advisor": CONF}}
    assert brain.learning_next(cfg, _state(), None) is None
    _write(_moves(100))
    running = brain.learning_next(cfg, _state(), None)
    assert isinstance(running, asyncio.Task)
    assert brain.learning_next(cfg, _state(), running) is running, "one lesson at a time"
    running.cancel()


# --- Where the lesson goes --------------------------------------------------------------------


async def test_the_newest_lesson_goes_into_move_advice_and_answers() -> None:
    home = brain.state_dir()
    lessons.keep(home, {**json.loads(GOOD)})
    messages = advisor.move_messages(_state(), [("wait", {}), ("start_mission", {})],
                                     lessons.latest_text(home))
    assert "What it learned from its own last moves:" in messages[1]["content"]
    assert "Do more of: Starting missions" in messages[1]["content"]
    assert "learned" not in advisor.move_messages(_state(), [("wait", {})])[1]["content"]
    stub = Model("An answer.")
    await brain.ask(FakeAgent(None), {"brain": {"advisor": CONF}}, "what did you learn?", "", stub)
    assert "Avoid: Listing on the market" in stub.asked[0][0]["content"]
