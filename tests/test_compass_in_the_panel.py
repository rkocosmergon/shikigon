"""Guards for the compass buttons in the panel (cos20 #492, found by the founder on the device,
07.11.2026; plan 6704311138).

A click on a compass button reached the world (four ``compass_change`` events), and the panel's
journal said so at once. But the panel's compass and its marked button read ``status.compass``,
which a move takes from the state it asks for **at its start** and writes down 80–100 s later:
a move that had looked before the click wrote the old compass after it, and the button did
nothing to see for up to two moves.

B1: the status says when its state was seen (``seen_at``); the panel shows the newest compass
the world took from a word of yours when it is not older than that (``Model.currentCompass``).
B2: a click while the last word is still on its way is kept, not dropped.
B3: a word that could not reach the world says so in the journal, which the panel reads.

Red on bb56320, the controls green.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
BRAIN = ROOT / "brain"
QML = ROOT / "qml"
sys.path.insert(0, str(BRAIN))

import shikigon_brain as brain
from test_brain import TABLE, CountingDecider
from test_brain import _state as _brain_state


@pytest.fixture(autouse=True)
def _private_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """A wall clock one second further at every look: which came first shows in whole seconds."""
    now = [1_794_038_000.0]

    def tick_tock() -> float:
        now[0] += 1.0
        return now[0]

    monkeypatch.setattr(brain.time, "time", tick_tock)
    return now


# The rule of ``Model.currentCompass`` (qml/ShikigonModel.js), for the files the brain really
# writes; tst_model.qml holds the same cases against the JavaScript itself.
PRESETS = {"grow", "defend", "attack", "trade", "cooperate", "explore", "autonomous"}


def current_compass(status: dict[str, Any], newest_first: list[dict[str, Any]]) -> str:
    shown = str(status.get("compass") or "")
    seen = status.get("seen_at")
    if not isinstance(seen, int | float):
        return shown  # a brain from before: the status, as before
    for e in newest_first:
        if "instruction" not in e or e.get("ok") is not True or not isinstance(e.get("at"), int):
            continue
        if str(e.get("preset") or "") not in PRESETS:
            continue
        return str(e["preset"]) if e["at"] >= seen else shown
    return shown


def _panel() -> str:
    """What the panel shows as compass: status.json and the journal's view, as the bar reads
    them (BarWidget keeps the view newest first)."""
    status = json.loads((brain.state_dir() / "status.json").read_text())
    recent = json.loads((brain.state_dir() / brain.BAR_JOURNAL).read_text())
    return current_compass(status, list(reversed(recent)))


class CompassWorld:
    """A world with a compass: a state is what it was when it was asked for."""

    def __init__(self) -> None:
        self.compass = "autonomous"
        self.looked_at: list[float] = []

    async def refresh_state(self) -> Any:
        self.looked_at.append(time.time())
        state = _brain_state()
        state.compass_preset = self.compass
        return state

    async def set_compass(self, preset: str) -> dict[str, Any]:
        self.compass = preset
        return {"preset": preset}


class ClickWhileThinking(CountingDecider):
    """The head thinks, and meanwhile the player clicks a compass button in the panel."""

    def __init__(self, world: CompassWorld, word: str) -> None:
        super().__init__(("wait", {}))
        self.world, self.word = world, word

    async def decide(self, state: Any) -> tuple[str, dict[str, Any]]:
        if self.word:
            await brain.instruct(self.world, self.word, TABLE)
        return await super().decide(state)


# ---------- B1 -----------------------------------------------------------------------------------


async def test_b1_the_click_shows_although_the_move_looked_before_it(clock: list[float]) -> None:
    """The case on the device: the move looked, the player clicked attack, the move wrote down
    the state it had seen (autonomous). The panel shows attack."""
    world = CompassWorld()
    await brain.tick(world, ClickWhileThinking(world, "attack now"))
    status = json.loads((brain.state_dir() / "status.json").read_text())
    assert status["compass"] == "autonomous", "the move's own state is from before the click"
    assert _panel() == "attack"


async def test_b1_a_change_in_the_terminal_afterwards_wins(clock: list[float]) -> None:
    """After the click the terminal sets trade: the next move's state is newer than the click."""
    world = CompassWorld()
    await brain.tick(world, ClickWhileThinking(world, "attack now"))
    world.compass = "trade"  # the terminal client, straight to the world
    await brain.tick(world, ClickWhileThinking(world, ""))
    assert _panel() == "trade"


async def test_b1_control_without_a_click_the_status_compass(clock: list[float]) -> None:
    world = CompassWorld()
    world.compass = "defend"
    await brain.tick(world, ClickWhileThinking(world, ""))
    assert _panel() == "defend"


async def test_b1_seen_at_is_from_before_the_state(clock: list[float]) -> None:
    world = CompassWorld()
    await brain.tick(world, ClickWhileThinking(world, ""))
    status = json.loads((brain.state_dir() / "status.json").read_text())
    assert status["seen_at"] < world.looked_at[0], (status["seen_at"], world.looked_at)


async def test_b1_a_word_the_world_refused_does_not_show(clock: list[float]) -> None:
    from cosmergon_agent.exceptions import RateLimitError

    class TooOften(CompassWorld):
        async def set_compass(self, preset: str) -> dict[str, Any]:
            raise RateLimitError(retry_after=30.0)

    world = TooOften()
    await brain.tick(world, ClickWhileThinking(world, "attack now"))
    assert _panel() == "autonomous"


# ---------- B3 -----------------------------------------------------------------------------------


async def test_b3_a_word_that_did_not_reach_the_world_says_so_in_the_journal() -> None:
    class Unreachable(CompassWorld):
        async def set_compass(self, preset: str) -> dict[str, Any]:
            raise ConnectionError("no route")

    with pytest.raises(ConnectionError):  # with_agent answers it, as before
        await brain.instruct(Unreachable(), "attack now", TABLE)
    recent = json.loads((brain.state_dir() / brain.BAR_JOURNAL).read_text())
    assert recent[-1]["instruction"] == "attack now" and recent[-1]["ok"] is False, recent


async def test_b3_control_a_word_taken_says_so_too() -> None:
    await brain.instruct(CompassWorld(), "attack now", TABLE)
    recent = json.loads((brain.state_dir() / brain.BAR_JOURNAL).read_text())
    assert recent[-1]["preset"] == "attack" and recent[-1]["ok"] is True


# ---------- B2 -----------------------------------------------------------------------------------


def _code(text: str) -> str:
    return re.sub(r"//[^\n]*", "", text)


def click_problems(service: str) -> list[str]:
    """A click while ``instructor`` runs must be kept and sent when it is done."""
    code = _code(service)
    found = []
    body = re.search(r"function instruct\(text\) \{\n(.*?)\n  \}\n", code, re.DOTALL)
    if not body or re.search(r'instructor\.running\) return "busy"', body[1]):
        found.append("a click while the last word is on its way is dropped")
    elif "instructor.next = said" not in body[1]:
        found.append("a click while the last word is on its way is not kept")
    proc = re.search(r"id: instructor\n(.*?)\n  \}\n", code, re.DOTALL)
    if not proc or not re.search(r"onExited:.*?said = next.*?running = true", proc[1], re.DOTALL):
        found.append("a kept click is never sent")
    return found


def test_b2_a_click_while_the_last_word_is_on_its_way_is_kept() -> None:
    assert click_problems((QML / "Service.qml").read_text(encoding="utf-8")) == []


def test_b2_the_guard_sees_what_it_guards() -> None:
    old = ('  function instruct(text) {\n    if (instructor.running) return "busy"\n  }\n'
           "  Process {\n    id: instructor\n    command: x\n  }\n")
    assert click_problems(old)
    kept_never_sent = ('  function instruct(text) {\n    instructor.next = said\n  }\n'
                       "  Process {\n    id: instructor\n    command: x\n  }\n")
    assert click_problems(kept_never_sent) == ["a kept click is never sent"]


def test_b1_the_bar_takes_its_compass_from_the_rule() -> None:
    bar = _code((QML / "BarWidget.qml").read_text(encoding="utf-8"))
    assert re.search(r"property string compass: Model\.currentCompass\(status, journal\)", bar)


def test_b1_the_rule_here_is_the_rule_there() -> None:
    """The Python mirror above and the JavaScript name the same presets."""
    js = (QML / "ShikigonModel.js").read_text(encoding="utf-8")
    words = set(re.search(r"var COMPASS_WORDS = \[(.*?)\]", js)[1].replace('"', "").split(", "))
    assert PRESETS == (words - {"free"}) | {"autonomous"}
