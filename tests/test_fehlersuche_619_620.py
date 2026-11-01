"""Guards for the two findings of the fault search of 01.11.2026 (cos20 #619, #620).

Both are in ``speech.TalkingDecider``, the wrapper that plays moves of its own: the word, and the
proposal it announced. Each test goes the way the brain goes, through ``tick`` with the deciders
the brain builds; the world and the model are stubs. The first test of each finding was red on
15bea54, twice in a row, before the fix; the tests marked as a control hold on both.
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

import contracts
import head
import shikigon_brain
import speech
from cosmergon_agent.state import GameState

PARTNER = "22222222-2222-2222-2222-222222222222"
ME = "11111111-1111-1111-1111-111111111111"
PROPOSAL = {"action": "propose_contract", "why": "a tribute from them",
            "params": {"to_player_id": PARTNER, "contract_type": "tribute",
                       "terms": {"payer_id": PARTNER, "amount_per_tick": 1}}}  # fmt: skip
CONF = {"base_url": "http://localhost:11434/v1", "model": "m", "speak": True,
        "learning_budget_pct": 0, "max_per_day": 0, "head_max_per_day": 0}  # fmt: skip


def _state(tick: int, inbox: list[dict[str, Any]] | None = None) -> GameState:
    briefing: dict[str, Any] = {"contract_targets": [{"player_id": PARTNER, "username": "Neon"}]}
    if inbox is not None:
        briefing["messages"] = {"inbox": inbox, "rooms": []}
    return GameState.from_api({
        "agent_id": ME, "tick": tick, "energy": 5000.0,
        "available_actions": {"propose_contract": {"available": True}},
        "world_briefing": briefing,
    })


class World:
    """Takes every move but those in ``refuses``."""

    def __init__(self, refuses: frozenset[str] = frozenset(), inbox: list | None = None) -> None:
        self.tick = 100
        self.refuses = refuses
        self.inbox = inbox
        self.sent: list[str] = []

    async def refresh_state(self) -> GameState:
        return _state(self.tick, self.inbox)

    async def act(self, action: str, **params: Any) -> Any:
        self.sent.append(action)
        if action in self.refuses:
            return SimpleNamespace(success=False, error_message=f"no {action} here", data=None)
        return SimpleNamespace(success=True, error_message=None, data={})


class Rules:
    """The rule tree's line: ``move`` while it is not blocked."""

    def __init__(self, move: tuple[str, dict[str, Any]] | None = None) -> None:
        self.move = move or (PROPOSAL["action"], PROPOSAL["params"])

    async def decide(self, state: Any, blocked: frozenset[str] = frozenset()) -> Any:
        if self.move[0] in blocked:
            return "wait", {}
        return self.move[0], dict(self.move[1])

    async def healthcheck(self) -> bool:
        return True


class Model:
    """Writes a word when asked for one, else the head's proposal; counts the words."""

    def __init__(self) -> None:
        self.words = 0

    async def __call__(self, messages: list[dict[str, str]], tokens: int) -> str:
        if messages[0]["content"] == speech.WRITE_SYSTEM:
            self.words += 1
            return "Hello Neon, I would like to propose a tribute; both of us gain from it."
        return json.dumps(PROPOSAL)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: Any) -> Path:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    path = shikigon_brain.state_dir()
    path.mkdir(parents=True)
    return path


def _paused(*actions: str) -> shikigon_brain.Refusals:
    refusals = shikigon_brain.Refusals(shikigon_brain.refusals_path())
    for action in actions:
        for _ in range(shikigon_brain.REFUSALS_BEFORE_PAUSE):
            refusals.note({"action": action, "ok": False, "why": "no", "refused": True})
    assert set(actions) <= refusals.paused()  # the state is reached
    return refusals


# --- cos20 #619: a refused word does not let the proposal go out twice ----------------------


async def test_619_refused_word_then_the_proposal_once(home: Path) -> None:
    model = Model()
    inner = head.HeadDecider(contracts.Careful(Rules()), CONF, home, send=model)
    decider = speech.TalkingDecider(inner, CONF, home, key="", send=model)
    world = World(refuses=frozenset({"message"}))

    await shikigon_brain.tick(world, decider, None, None)
    assert "tries" not in (inner.last or {}), "the head was told of a move it never chose"
    world.tick = 101
    await shikigon_brain.tick(world, decider, 100, None)

    assert world.sent == ["message", "propose_contract"], world.sent


async def test_619_refused_word_announced_nothing(home: Path) -> None:
    model = Model()
    inner = head.HeadDecider(contracts.Careful(Rules()), CONF, home, send=model)
    decider = speech.TalkingDecider(inner, CONF, home, key="", send=model)

    await shikigon_brain.tick(World(refuses=frozenset({"message"})), decider, None, None)

    assert speech.load(home)["then"] is None


async def test_619_control_rule_tree_alone(home: Path) -> None:
    """Control: a tree has no ``again``; the proposal goes out once, on the next move."""
    model = Model()
    decider = speech.TalkingDecider(contracts.Careful(Rules()), CONF, home, key="", send=model)
    world = World(refuses=frozenset({"message"}))

    await shikigon_brain.tick(world, decider, None, None)
    world.tick = 101
    await shikigon_brain.tick(world, decider, 100, None)

    assert world.sent == ["message", "propose_contract"], world.sent


async def test_619_control_word_taken_proposal_follows(home: Path) -> None:
    """Control: the promise holds; the word goes out, the proposal on the next move."""
    model = Model()
    inner = head.HeadDecider(contracts.Careful(Rules()), CONF, home, send=model)
    decider = speech.TalkingDecider(inner, CONF, home, key="", send=model)
    world = World()

    await shikigon_brain.tick(world, decider, None, None)
    assert speech.load(home)["then"] is not None
    world.tick = 101
    await shikigon_brain.tick(world, decider, 100, None)

    assert world.sent == ["message", "propose_contract"], world.sent
    assert model.words == 1


async def test_619_control_head_asked_again_after_a_later_refusal(home: Path) -> None:
    """Control: only the move of the word skips the head's ``again``, not the moves after it."""
    model = Model()
    inner = head.HeadDecider(contracts.Careful(Rules()), CONF, home, send=model)
    decider = speech.TalkingDecider(inner, CONF, home, key="", send=model)
    world = World(refuses=frozenset({"propose_contract"}))

    for done_in, now in ((None, 100), (100, 101), (101, 102)):  # word, announced, the head's
        world.tick = now
        await shikigon_brain.tick(world, decider, done_in, None)

    assert world.sent[:3] == ["message", "propose_contract", "propose_contract"], world.sent
    assert (inner.last or {}).get("tries"), "the head was not asked again after its own move"


# --- cos20 #620: no word while the move that carries it is paused ---------------------------


async def test_620_no_word_while_message_is_paused(home: Path) -> None:
    model = Model()
    decider = speech.TalkingDecider(
        head.HeadDecider(contracts.Careful(Rules()), CONF, home, send=model),
        CONF, home, key="", send=model)  # fmt: skip
    world = World()

    await shikigon_brain.tick(world, decider, None, _paused("message"))

    assert model.words == 0, "a word was written (and counted) that cannot go out"
    assert world.sent == ["propose_contract"], world.sent
    assert speech.load(home)["then"] is None


async def test_620_no_post_while_say_is_paused(home: Path) -> None:
    model = Model()
    decider = speech.TalkingDecider(contracts.Careful(Rules(("create_field", {}))), CONF, home,
                                    key="", send=model)  # fmt: skip
    world = World()

    await shikigon_brain.tick(world, decider, None, _paused("say"))

    assert model.words == 0
    assert world.sent == ["create_field"], world.sent


async def test_620_no_answer_while_message_is_paused(home: Path) -> None:
    model = Model()
    decider = speech.TalkingDecider(contracts.Careful(Rules(("create_field", {}))), CONF, home,
                                    key="", send=model)  # fmt: skip
    inbox = [{"id": "m1", "from_player_id": PARTNER, "from_name": "Neon",
              "text": "Pact for 50 ticks?", "at": ""}]  # fmt: skip
    world = World(inbox=inbox)
    assert speech.inbox(await world.refresh_state())  # the message reaches the decider

    await shikigon_brain.tick(world, decider, None, _paused("message"))

    assert model.words == 0
    assert world.sent == ["create_field"], world.sent
    assert speech.load(home)["answered"] == [], "a message it could not answer counts as answered"


async def test_620_control_say_paused_message_carries_the_word(home: Path) -> None:
    """Control: only the move that carries the word counts; an announcement goes by message."""
    model = Model()
    decider = speech.TalkingDecider(contracts.Careful(Rules()), CONF, home, key="", send=model)
    world = World()

    await shikigon_brain.tick(world, decider, None, _paused("say"))

    assert model.words == 1
    assert world.sent == ["message"], world.sent
