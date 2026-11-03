"""An answer of the head that could not be played stays on record (cos20 #533, plan of 03.11.2026).

On the device the head fell back to the rules on 27 % of its moves in one hour, and all that was
kept was the sentence of our own check: 25 of 55 said "not JSON" and nobody could say what the
model had written. This file holds the answers and nothing reads it, so that a model's text does
not travel on towards the look back, the prompt, the status or the bar.
"""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "brain"))

import advisor
import files
import head
import shikigon_brain
from test_fehlersuche_612 import CONF, Tree, World, X, home_of

MARK = "ZEBRA-SEVEN"
GOOD = json.dumps({"action": X, "params": {}, "why": "grow"})


async def one_move(home: Path, answers: list[str]) -> tuple[World, dict[str, Any]]:
    left = iter(answers)

    async def send(talk: list[dict[str, str]], tokens: int) -> str:
        return next(left)

    async def forms() -> head.Forms:  # X has a form, so the head may write it itself
        return {X: []}

    world = World()
    decider = head.HeadDecider(Tree(), CONF, home, send=send, forms=forms)
    last = await shikigon_brain.guarded_tick(world, decider, None, shikigon_brain.Refusals())
    return world, last


def kept(home: Path) -> list[dict[str, Any]]:
    return files.tail(home / head.UNUSABLE_FILE, 100)


async def test_both_answers_stay_with_both_ends_and_how_long_they_were(
    tmp_path: Path, monkeypatch: Any
) -> None:
    home = home_of(tmp_path, monkeypatch)
    first = "A" * 200 + "m" * 600 + "Z" * 200
    _, last = await one_move(home, [first, "no braces at all"])
    assert last["advice"]["fallback"].startswith("the answer was not JSON")
    one, two = kept(home)
    assert one["start"] == "A" * 200 and one["end"] == "Z" * 200 and one["chars"] == 1000
    assert two["start"] == "no braces at all" and two["end"] == "" and two["chars"] == 16
    assert (one["round"], two["round"]) == (1, 2)
    assert one["why"].startswith("the answer was not JSON")
    assert 0 < one["asked"] < two["asked"], "the second question holds the first answer"
    assert one["tick"] == 1 and isinstance(one["at"], int) and one["model"]


async def test_asked_is_the_size_of_the_question_without_the_answer_to_it(
    tmp_path: Path, monkeypatch: Any
) -> None:
    home = home_of(tmp_path, monkeypatch)
    sizes: list[int] = []

    async def send(talk: list[dict[str, str]], tokens: int) -> str:
        sizes.append(sum(len(said["content"]) for said in talk))
        return "prose"

    decider = head.HeadDecider(Tree(), CONF, home, send=send)
    await shikigon_brain.guarded_tick(World(), decider, None, shikigon_brain.Refusals())
    assert [entry["asked"] for entry in kept(home)] == sizes


async def test_a_short_answer_is_not_written_twice(tmp_path: Path, monkeypatch: Any) -> None:
    home = home_of(tmp_path, monkeypatch)
    await one_move(home, ["x" * 250, "y" * 400])
    one, two = kept(home)
    assert len(one["start"]) + len(one["end"]) == 250
    assert len(two["start"]) + len(two["end"]) == 400


@pytest.mark.parametrize(
    ("answer", "why"),
    [
        ("{not json}", "not JSON"),
        (json.dumps({"action": "market_list", "params": {}, "why": "x"}), "among the moves"),
        (json.dumps({"action": "wait", "params": {}, "why": "x"}, indent=9)[:20], "not JSON"),
        (json.dumps({"action": X, "params": {"name": "two words"}, "why": "x"}), "no name or id"),
        (json.dumps({"action": X, "params": {"field_id": ""}, "why": "x"}), "no name or id"),
        (json.dumps({"action": "start_mission", "params": {}, "why": "x"}), "among the moves"),
    ],
)
async def test_every_check_that_turns_an_answer_down_leaves_it_here(
    tmp_path: Path, monkeypatch: Any, answer: str, why: str
) -> None:
    home = home_of(tmp_path, monkeypatch)
    await one_move(home, [answer, answer])
    entries = kept(home)
    assert len(entries) == 2
    assert why in entries[0]["why"], entries[0]
    assert entries[0]["start"] == answer[:200]


async def test_a_good_answer_leaves_nothing(tmp_path: Path, monkeypatch: Any) -> None:
    home = home_of(tmp_path, monkeypatch)
    world, _ = await one_move(home, [GOOD])
    assert world.sent == [(1, X)]
    assert not (home / head.UNUSABLE_FILE).exists()


async def test_a_second_answer_that_is_good_still_plays_and_only_the_first_stays(
    tmp_path: Path, monkeypatch: Any
) -> None:
    home = home_of(tmp_path, monkeypatch)
    world, last = await one_move(home, ["prose", GOOD])
    assert world.sent == [(1, X)]
    assert "fallback" not in last["advice"]
    assert [e["start"] for e in kept(home)] == ["prose"]


async def test_no_answer_leaves_nothing(tmp_path: Path, monkeypatch: Any) -> None:
    home = home_of(tmp_path, monkeypatch)

    async def send(talk: list[dict[str, str]], tokens: int) -> str:
        raise advisor.call_errors()[0]("gone")

    decider = head.HeadDecider(Tree(), CONF, home, send=send)
    await shikigon_brain.guarded_tick(World(), decider, None, shikigon_brain.Refusals())
    assert not (home / head.UNUSABLE_FILE).exists()


async def test_the_answer_stands_in_no_other_file(tmp_path: Path, monkeypatch: Any) -> None:
    """Threat model: the model is not trusted. Its text goes nowhere from here."""
    home = home_of(tmp_path, monkeypatch)
    await one_move(home, [f"prose with {MARK} in it", f"again {MARK}"])
    holding = [p.name for p in home.rglob("*") if p.is_file()
               and MARK in p.read_text(encoding="utf-8", errors="replace")]
    assert holding == [head.UNUSABLE_FILE]


async def test_it_does_not_reach_the_next_question(tmp_path: Path, monkeypatch: Any) -> None:
    home = home_of(tmp_path, monkeypatch)
    asked: list[str] = []
    answers = iter([f"{MARK} one", f"{MARK} two", GOOD])

    async def send(talk: list[dict[str, str]], tokens: int) -> str:
        asked.append("\n".join(m["content"] for m in talk))
        return next(answers)

    decider = head.HeadDecider(Tree(), CONF, home, send=send)
    world = World()
    refusals = shikigon_brain.Refusals()
    await shikigon_brain.guarded_tick(world, decider, None, refusals)
    await shikigon_brain.guarded_tick(world, decider, None, refusals)
    assert MARK not in asked[2]


async def test_a_file_that_cannot_be_written_costs_no_move(
    tmp_path: Path, monkeypatch: Any
) -> None:
    home = home_of(tmp_path, monkeypatch)
    (home / head.UNUSABLE_FILE).mkdir()
    world, last = await one_move(home, ["prose", GOOD])
    assert world.sent == [(1, X)]
    assert last["ok"]


async def test_a_terminal_reading_it_gets_no_control_characters(
    tmp_path: Path, monkeypatch: Any
) -> None:
    home = home_of(tmp_path, monkeypatch)
    await one_move(home, ["\x1b[31mred\x07\n", "\x1b]0;title\x07"])
    raw = (home / head.UNUSABLE_FILE).read_bytes()
    assert b"\x1b" not in raw and b"\x07" not in raw
    assert raw.count(b"\n") == 2, "one line per answer"


async def test_only_the_player_can_read_it(tmp_path: Path, monkeypatch: Any) -> None:
    home = home_of(tmp_path, monkeypatch)
    await one_move(home, ["prose", "prose"])
    assert stat.S_IMODE((home / head.UNUSABLE_FILE).stat().st_mode) == 0o600


async def test_it_stays_within_its_bound(tmp_path: Path, monkeypatch: Any) -> None:
    home = home_of(tmp_path, monkeypatch)
    monkeypatch.setattr(head, "UNUSABLE_KEEP_BYTES", 4096)
    for _ in range(40):
        await one_move(home, ["ä" * 500, "ä" * 500])
    size = (home / head.UNUSABLE_FILE).stat().st_size
    assert 0 < size <= 2 * 4096
    assert kept(home), "the newest lines stay"


def test_a_new_shikigon_does_not_get_it(tmp_path: Path) -> None:
    assert tmp_path / head.UNUSABLE_FILE in shikigon_brain.agent_paths(tmp_path)
