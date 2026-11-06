"""Guards for the Betreuer-Durchgang b1637b0 (cos20 #492): B1, B2, B3 (Pflicht), A2 (Kür).

B1 and B3 are the proposal ``speech.TalkingDecider`` announced and plays on the next move
from its memo: it went out from the state of the move before, also when the world no longer
offered a contract with that agent, and with no record of the decider that chose it, so the
head's own choice stood in its look back as the rules'. B2 is the note that told the advisor
"every 100 moves" also when the player's agent looks back every five. A2 is the bar's text,
which kept direction marks of another player's name. Each test goes the way the brain goes
(``tick``); the world and the model are stubs. Red on b1637b0, the controls green on both.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "brain"))

import advisor
import contracts
import files
import head
import lessons
import shikigon_brain
import speech
from cosmergon_agent.state import GameState

PARTNER = "22222222-2222-2222-2222-222222222222"
ME = "11111111-1111-1111-1111-111111111111"
TERMS = {"to_player_id": PARTNER, "contract_type": "tribute",
         "terms": {"payer_id": PARTNER, "amount_per_tick": 1}}  # fmt: skip
CONF = {"base_url": "http://localhost:11434/v1", "model": "m", "speak": True,
        "learning_budget_pct": 0, "max_per_day": 0, "head_max_per_day": 0}  # fmt: skip
NOTES = ROOT / "brain" / "advisor_notes.md"


def _state(tick: int, partner: bool) -> GameState:
    targets = [{"player_id": PARTNER, "username": "Neon"}] if partner else []
    return GameState.from_api({
        "agent_id": ME, "tick": tick, "energy": 5000.0,
        "available_actions": {"propose_contract": {"available": True}},
        "world_briefing": {"contract_targets": targets},
    })


class World:
    """Takes every move; ``partner`` says whether it still offers a contract with Neon."""

    def __init__(self) -> None:
        self.tick = 100
        self.partner = True
        self.sent: list[str] = []

    async def refresh_state(self) -> GameState:
        return _state(self.tick, self.partner)

    async def act(self, action: str, **params: Any) -> Any:
        self.sent.append(action)
        return SimpleNamespace(success=True, error_message=None, data={})


class Rules:
    """The rule tree: a contract while the world names a partner, else a wait."""

    async def decide(self, state: Any, blocked: frozenset[str] = frozenset()) -> Any:
        targets = state.world_briefing.contract_targets
        if targets and "propose_contract" not in blocked:
            return "propose_contract", dict(TERMS)
        return "wait", {}

    async def healthcheck(self) -> bool:
        return True


class Model:
    """A word when asked for one; as the head, the proposal."""

    async def __call__(self, messages: list[dict[str, str]], tokens: int) -> str:
        if messages[0]["content"] == speech.WRITE_SYSTEM:
            return "Hello Neon, I would like to propose a tribute; both of us gain from it."
        return json.dumps({"action": "propose_contract", "why": "a tribute", "params": TERMS})


@pytest.fixture
def home(tmp_path: Path, monkeypatch: Any) -> Path:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    path = shikigon_brain.state_dir()
    path.mkdir(parents=True)
    return path


def _journal(home: Path) -> list[dict[str, Any]]:
    return files.tail(home / "journal.jsonl", 10)


async def _two_moves(decider: Any, world: World) -> None:
    await shikigon_brain.tick(world, decider, None, None)
    world.tick = 101
    await shikigon_brain.tick(world, decider, 100, None)


# --- B1: the announced proposal goes out only while the world offers that partner -------------


async def test_control_the_announced_proposal_follows_its_word(home: Path) -> None:
    decider = speech.TalkingDecider(contracts.Careful(Rules()), CONF, home, key="", send=Model())
    world = World()
    await _two_moves(decider, world)
    assert world.sent == ["message", "propose_contract"], world.sent


async def test_b1_no_proposal_to_a_partner_the_world_no_longer_offers(home: Path) -> None:
    decider = speech.TalkingDecider(contracts.Careful(Rules()), CONF, home, key="", send=Model())
    world = World()
    await shikigon_brain.tick(world, decider, None, None)
    world.tick, world.partner = 101, False
    await shikigon_brain.tick(world, decider, 100, None)
    assert world.sent == ["message"], world.sent  # a wait sends nothing
    assert _journal(home)[-1]["action"] == "wait"
    assert speech.load(home)["then"] is None


# --- B3: the head's proposal, played from the memo, stays the head's -------------------------


async def test_b3_the_heads_proposal_from_the_memo_is_its_own(home: Path) -> None:
    model = Model()
    inner = head.HeadDecider(contracts.Careful(Rules()), CONF, home, send=model)
    decider = speech.TalkingDecider(inner, CONF, home, key="", send=model)
    world = World()
    await _two_moves(decider, world)
    assert world.sent == ["message", "propose_contract"], world.sent
    played = _journal(home)[-1]
    assert played["action"] == "propose_contract"
    assert (played.get("advice") or {}).get("choice") == "propose_contract", played
    assert advisor.move_for_model(played)["by"] == "head", played
    assert speech.load(home)["then_last"] is None, "played now or never, its record too"


async def test_control_a_rules_proposal_from_the_memo_stays_the_rules(home: Path) -> None:
    decider = speech.TalkingDecider(contracts.Careful(Rules()), CONF, home, key="", send=Model())
    await _two_moves(decider, World())
    assert advisor.move_for_model(_journal(home)[-1])["by"] == "rules"


# --- B2: the note says both rhythms of the look back --------------------------------------------


def _line(start: str) -> str:
    return next(line for line in NOTES.read_text(encoding="utf-8").splitlines()
                if line.startswith(start))  # fmt: skip


def test_control_the_readme_says_the_heads_rhythm() -> None:
    readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
    assert "every five moves (`review_every`)" in readme
    assert lessons.review_every({}) == 5


def test_b2_the_note_says_how_often_the_head_looks_back() -> None:
    learns = _line("- It learns from its own moves:")
    assert f"every {lessons.LEARN_EVERY} moves" in learns, learns
    assert f"every {lessons.review_every({})} moves" in learns, learns
    assert f"its last {lessons.HEAD_WINDOW}" in learns, learns
    assert "review_every" in learns, learns


def test_b1_the_note_says_its_words_are_moves_of_their_own() -> None:
    model = _line("- A model can advise it on a move")
    assert "only a move the world offers" in model, model
    assert "moves of their own" in model and "world room" in model, model


# --- A2: the bar's texts keep no direction mark ---------------------------------------------------


@pytest.mark.parametrize("mark", ["\u202e", "\u202a", "\u2066", "\u2067", "\u2069", "\u200f",
                                  "\u061c", "\u0007", "\u009b"])
def test_a2_the_bars_text_holds_no_mark(mark: str) -> None:
    assert shikigon_brain.short(f"Neon{mark}-warm", 64) == "Neon-warm"


def test_control_the_bars_text_keeps_its_words_and_lines() -> None:
    assert shikigon_brain.short("größer\nzweite Zeile", 64) == "größer\nzweite Zeile"
    assert shikigon_brain.short("abcdef", 3) == "abc"
    assert shikigon_brain.short(None, 5) == ""
