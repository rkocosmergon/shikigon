"""Guards for the six findings of the second pass at b40f78b (cos20 #492, all Kür).

Findings 1 to 5 are one class: a file the brain keeps, empty or of another form, ended a move
or the brain. They are read through ``files.read_object``/``read_list`` now, and the last test
holds that no reader of such a file goes past them. Finding 6: the pauses of the running brain
hold for a ``tick`` by hand too. Each test goes the way the brain goes, with the stubs of
``test_fehlersuche_612``.
"""

from __future__ import annotations

import ast
import asyncio
import datetime
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
BRAIN = ROOT / "brain"
sys.path.insert(0, str(BRAIN))

import advisor
import connections
import head
import lessons
import shikigon_brain
import speech
from test_fehlersuche_612 import CONF, Tree, World, X, home_of

TODAY = datetime.datetime.now(datetime.UTC).date().isoformat()
OTHER_FORMS = ["", "[]", "null", "5", '"x"']


async def head_move(home: Path, conf: dict[str, Any]) -> tuple[World, dict[str, Any]]:
    async def send(talk: list[dict[str, str]], tokens: int) -> str:
        return json.dumps({"action": X, "params": {}, "why": "grow"})

    world = World()
    decider = head.HeadDecider(Tree(), conf, home, send=send)
    last = await shikigon_brain.guarded_tick(world, decider, None, shikigon_brain.Refusals())
    return world, last


# --- 1: speech.json -----------------------------------------------------------------------


@pytest.mark.parametrize("text", [*OTHER_FORMS, '{"moves": "x"}', '{"answered": 5}',
                                  '{"talked": [1], "then_at": "soon"}'])
async def test_1_a_speech_file_of_another_form_stops_no_move(
    tmp_path: Path, monkeypatch: Any, text: str
) -> None:
    home = home_of(tmp_path, monkeypatch)
    speech.memo_path(home).write_text(text, encoding="utf-8")

    async def model(messages: list[dict[str, str]], tokens: int) -> str:
        return "SKIP"

    world = World()
    talking = speech.TalkingDecider(Tree(), CONF, home, key="k", send=model)
    last = await shikigon_brain.guarded_tick(world, talking, None, shikigon_brain.Refusals())
    assert world.sent == [(1, X)], last.get("why")
    assert speech.load(home)["moves"] == 1  # written anew


# --- 2: lessons.json ----------------------------------------------------------------------


NO_TEXT = json.dumps([{"lessons": 5, "avoid": "a", "double_down": "b"}])


@pytest.mark.parametrize("text", [*OTHER_FORMS, "[{}]", NO_TEXT])
async def test_2_a_lessons_file_of_another_form_stops_no_move_of_the_head(
    tmp_path: Path, monkeypatch: Any, text: str
) -> None:
    home = home_of(tmp_path, monkeypatch)
    (home / lessons.LESSONS_FILE).write_text(text, encoding="utf-8")
    world, last = await head_move(home, CONF)
    assert world.sent == [(1, X)], last.get("why")
    assert lessons.latest_text(home) == ""


def test_2_a_lesson_that_carries_its_texts_is_kept(tmp_path: Path) -> None:
    good = {"lessons": "l" * 100, "avoid": "a" * 50, "double_down": "d" * 50}
    (tmp_path / lessons.LESSONS_FILE).write_text(json.dumps([{}, good]), encoding="utf-8")
    assert lessons.kept(tmp_path) == [good]
    assert lessons.latest_text(tmp_path).startswith("l" * 100)


# --- 3: budget.json -----------------------------------------------------------------------


@pytest.mark.parametrize("data", [{"day": TODAY}, {"day": TODAY, "start": "x"},
                                  {"day": TODAY, "start": 100.0, "spent": "x"},
                                  {"day": TODAY, "start": [1], "spent": 0}])
async def test_3_a_budget_of_today_of_another_form_stops_no_move_of_the_head(
    tmp_path: Path, monkeypatch: Any, data: dict[str, Any]
) -> None:
    home = home_of(tmp_path, monkeypatch)
    (home / head.BUDGET_FILE).write_text(json.dumps(data), encoding="utf-8")
    world, last = await head_move(home, {**CONF, "learning_budget_pct": 5})
    assert world.sent == [(1, X)], last.get("why")


def test_3_a_budget_without_a_start_is_measured_anew(tmp_path: Path) -> None:
    (tmp_path / head.BUDGET_FILE).write_text(json.dumps({"day": TODAY}), encoding="utf-8")
    assert head.budget_left(tmp_path, {"learning_budget_pct": 5}, 1_000.0) == 50.0
    head.budget_spend(tmp_path, 20.0)
    assert head.budget_left(tmp_path, {"learning_budget_pct": 5}, 1_000.0) == 30.0


def test_3_a_spent_of_another_form_is_counted_from_zero(tmp_path: Path) -> None:
    data = {"day": TODAY, "start": 1_000.0, "spent": "x"}
    (tmp_path / head.BUDGET_FILE).write_text(json.dumps(data), encoding="utf-8")
    head.budget_spend(tmp_path, 10.0)
    assert head.budget_left(tmp_path, {"learning_budget_pct": 5}, 1_000.0) == 40.0


# --- 4: advisor.json ----------------------------------------------------------------------


@pytest.mark.parametrize("text", [*OTHER_FORMS, '{"every": "x"}', '{"timeout_s": "x"}',
                                  '{"max_per_day": "x", "advise": "sometimes"}'])
def test_4_a_choice_of_another_form_starts_the_brain(tmp_path: Path, text: str) -> None:
    (tmp_path / connections.CHOICE_FILE).write_text(text, encoding="utf-8")
    conf = advisor.settings({}, tmp_path)
    assert advisor.every(conf) == 1
    assert advisor.limit_reached(conf, tmp_path) is False
    head.HeadDecider(Tree(), conf, tmp_path)  # timeout_s read when it is made
    assert shikigon_brain.decider_name({}, conf) == "tree"


def test_4_the_choice_the_brain_writes_is_read_whole(tmp_path: Path) -> None:
    choice = {"via": "http", "base_url": "https://a.example/v1", "model": "m",
              "advise": 15, "every": 15}
    connections.choose(tmp_path, choice)
    assert connections.chosen(tmp_path) == choice
    connections.choose(tmp_path, {"via": "agent", "agent": "pi", "agent_model": "x/y",
                                  "advise": "head"})
    assert connections.chosen(tmp_path)["advise"] == "head"


# --- 5: credentials.json ------------------------------------------------------------------

WORLD = {"world": {"base_url": "https://cosmergon.com"}}


@pytest.fixture
def lost(tmp_path: Path, monkeypatch: Any) -> Path:
    """A key file that is there and empty (a crash between writing and the disk)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    home = home_of(tmp_path, monkeypatch)
    shikigon_brain.credentials_path().write_text("", encoding="utf-8")
    return home


def test_5_a_key_file_it_cannot_read_puts_it_to_sleep(lost: Path) -> None:
    async def work(agent: Any) -> Any:
        raise AssertionError("no agent without a key")

    said = asyncio.run(shikigon_brain.with_agent(WORLD, work, once=False))
    assert said["why"] == shikigon_brain.KEY_LOST_WHY
    status = json.loads((lost / "status.json").read_text())
    assert status["reason"] == "key_lost" and status["mood"] == "asleep"


def test_5_a_key_file_it_cannot_read_is_no_place_for_a_new_one_without_new(lost: Path) -> None:
    def post(*_: Any, **__: Any) -> Any:
        raise AssertionError("nothing is sent")

    with pytest.raises(SystemExit, match="already lives here"):
        shikigon_brain.register(WORLD, post)


def test_5_forget_removes_the_files_here_and_sends_nothing(lost: Path) -> None:
    def delete(*_: Any, **__: Any) -> Any:
        raise AssertionError("nothing is sent")

    said = shikigon_brain.forget(WORLD, delete)
    assert said["ok"] and not lost.exists()


def test_5_the_terminal_says_why(lost: Path, capsys: Any) -> None:
    assert shikigon_brain.main(["terminal"]) == 1
    assert capsys.readouterr().out.strip() == shikigon_brain.KEY_LOST_WHY


def test_5_a_key_file_of_another_form_is_lost_too(lost: Path) -> None:
    shikigon_brain.credentials_path().write_text('{"agent_id": 5, "api_key": "k"}')
    assert shikigon_brain.key_lost(shikigon_brain.load_credentials())
    shikigon_brain.credentials_path().write_text('{"agent_id": "a", "api_key": "k"}')
    assert not shikigon_brain.key_lost(shikigon_brain.load_credentials())


# --- 6: a tick by hand holds back what the running brain paused ---------------------------


def test_6_a_tick_by_hand_holds_back_a_paused_move(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    home_of(tmp_path, monkeypatch)
    world = World(refuse=frozenset({X}))
    monkeypatch.setattr(shikigon_brain, "make_decider", lambda cfg, advised=False: Tree())

    async def with_agent(cfg: Any, work: Any, **_: Any) -> Any:
        return await work(world)

    monkeypatch.setattr(shikigon_brain, "with_agent", with_agent)
    running = shikigon_brain.Refusals(shikigon_brain.refusals_path())  # the brain's loop
    for _ in range(shikigon_brain.REFUSALS_BEFORE_PAUSE):
        asyncio.run(shikigon_brain.guarded_tick(world, Tree(), None, running))
    assert X in running.paused()
    assert shikigon_brain.main(["tick"]) == 0  # the tick by hand, a process of its own
    assert world.sent == [(n, X) for n in range(1, shikigon_brain.REFUSALS_BEFORE_PAUSE + 1)]


def test_6_a_refusals_file_of_another_form_pauses_nothing(tmp_path: Path) -> None:
    path = tmp_path / shikigon_brain.REFUSALS_FILE
    for text in [*OTHER_FORMS, '{"moves": "x", "paused_until": {"a": "b"}, "in_a_row": 3}']:
        path.write_text(text, encoding="utf-8")
        assert shikigon_brain.Refusals(path).paused() == frozenset()


# --- the class: every file the brain keeps is read through files.read_object/read_list ------

# Reads of a file that is not one the brain keeps: what stands in a program's place, the
# system's tables, the plugin's own notes, the snapshot printed whole by ``status``.
NOT_KEPT = {("connections", "is_placeholder"), ("connections", "system_accounts_below"),
            ("connections", "proc_tables"), ("connections", "own_addresses"),
            ("shikigon_brain", "ask"), ("shikigon_brain", "_local")}


def test_no_file_the_brain_keeps_is_read_past_files() -> None:
    """``read_text`` outside ``files.py`` only where no file the brain keeps is read: such a
    file goes through ``files.read_object``/``read_list``. A new reader fails here until it is
    one of those, or named above with why it is none."""
    found: set[tuple[str, str]] = set()
    for path in BRAIN.glob("*.py"):
        if path.name == "files.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for function in ast.walk(tree):
            if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for node in ast.walk(function):
                if isinstance(node, ast.Attribute) and node.attr == "read_text":
                    found.add((path.stem, function.name))
    assert found == NOT_KEPT
