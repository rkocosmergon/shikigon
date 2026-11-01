"""Guards for the four findings of the fault search of 31.10.2026 (cos20 #612).

Each test goes the way the brain goes: ``tick`` or ``guarded_tick`` as ``run`` calls them, with
the deciders the brain builds. The world, the model and the rule tree are stubs at the seams the
brain has for them. Each was red on fd1ef72, twice in a row, before the fix.
"""

from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
BRAIN = ROOT / "brain"
sys.path.insert(0, str(BRAIN))

import advisor
import head
import shikigon_brain
import speech

X = "create_field"
P = ("propose_contract", {"to_player_id": "p2", "contract_type": "trade"})
CONF = {"base_url": "http://127.0.0.1:9", "model": "m"}


class Tree:
    """The rules: X unless it is paused."""

    async def decide(self, state: Any, blocked: frozenset[str] = frozenset()) -> tuple[str, dict]:
        return ("wait", {}) if X in blocked else (X, {})


class World:
    """A world that offers X each tick and refuses the moves in ``refuse``."""

    def __init__(self, refuse: frozenset[str] = frozenset(), tournament: Any = None) -> None:
        self.tick = 0
        self.sent: list[tuple[int, str]] = []
        self.refuse = refuse
        self.tournament = tournament

    def actions(self) -> dict[str, Any]:
        return {X: {}}

    async def refresh_state(self) -> Any:
        self.tick += 1
        return SimpleNamespace(
            tick=self.tick, energy=100.0, fields=[], available_actions=self.actions(),
            ranking=None, agent_name="a", persona_type="farmer", compass_preset=None,
            tournament=self.tournament, next_step=None, key_expiry=None, showcase=None,
            pending_contracts=None,
        )

    async def act(self, action: str, **params: Any) -> Any:
        self.sent.append((self.tick, action))
        refused = action in self.refuse
        return SimpleNamespace(success=not refused, error_message="no" if refused else None)

    async def missions(self) -> list[dict[str, Any]]:
        return []


def home_of(tmp_path: Path, monkeypatch: Any) -> Path:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    home = tmp_path / "state" / "shikigon"
    home.mkdir(parents=True)
    return home


# --- F1: a move the world refused three times in a row goes out a fourth time ---------------


def scripted(script: list[list[str]], world: World) -> Any:
    """The head's answers per tick, in order, one per call."""
    pos: dict[int, int] = {}

    async def send(talk: list[dict[str, str]], tokens: int) -> str:
        n = pos.get(world.tick, 0)
        pos[world.tick] = n + 1
        return json.dumps({"action": script[world.tick - 1][n], "params": {}, "why": "go"})

    return send


async def run_head(
    tmp_path: Path, monkeypatch: Any, script: list[list[str]]
) -> tuple[World, Any, list[dict]]:
    home = home_of(tmp_path, monkeypatch)
    world = World(refuse=frozenset({X}))
    decider = head.HeadDecider(Tree(), CONF, home, send=scripted(script, world))
    refusals = shikigon_brain.Refusals()
    lasts = [await shikigon_brain.tick(world, decider, None, refusals) for _ in script]
    return world, refusals, lasts


async def test_no_fourth_send_after_three_refusals(tmp_path: Path, monkeypatch: Any) -> None:
    # Ticks 1 and 2: the head picks X, the world refuses, the head then waits.
    # Tick 3: the third refusal in a row pauses X; the head still wants X twice more.
    script = [[X, "wait"], [X, "wait"], [X, X, X], ["wait"]]
    world, refusals, _ = await run_head(tmp_path, monkeypatch, script)
    sends = [t for t, a in world.sent if a == X]
    assert X in refusals.paused()
    assert 4 not in sends
    # README: "left alone for the next 30 moves".
    assert len(sends) == 3, f"X went out {len(sends)} times, in ticks {sends}"


async def test_the_pause_is_said_in_the_move(tmp_path: Path, monkeypatch: Any) -> None:
    script = [[X, "wait"], [X, "wait"], [X, X, X]]
    _, _, lasts = await run_head(tmp_path, monkeypatch, script)
    assert "paused for" in lasts[2]["why"], lasts[2]["why"]


class Stubborn:
    """A decider from another package whose ``again`` ignores ``blocked``; it gives up after
    five tries, since fd1ef72 had no limit of its own there."""

    def __init__(self) -> None:
        self.tries = 0

    async def decide(self, state: Any) -> tuple[str, dict]:
        return X, {}

    async def again(self, state: Any, refused: dict, blocked: frozenset[str] = frozenset()) -> Any:
        self.tries += 1
        return (X, {}) if self.tries <= 5 else None


async def test_a_paused_move_is_held_back_from_any_decider(
    tmp_path: Path, monkeypatch: Any
) -> None:
    home_of(tmp_path, monkeypatch)
    world = World(refuse=frozenset({X}))
    refusals = shikigon_brain.Refusals()
    last = await shikigon_brain.tick(world, Stubborn(), None, refusals)
    assert X in refusals.paused()
    assert world.sent == [(1, X)] * 3, world.sent
    assert "paused for" in last["why"], last["why"]


# --- F2: after an announced proposal the head retries from the tick before ------------------


class ContractTree:
    """Proposes only what the world offers and lets it afford."""

    async def decide(self, state: Any, blocked: frozenset[str] = frozenset()) -> tuple[str, dict]:
        acts = state.available_actions
        if P[0] not in blocked and P[0] in acts:
            return P[0], dict(P[1])
        if X not in blocked and acts.get(X, {}).get("can_afford") is not False:
            return X, {}
        return "wait", {}


class ContractWorld(World):
    """Offers a partner; from tick 2 on X is no longer affordable."""

    def actions(self) -> dict[str, Any]:
        return {P[0]: {}, X: {"can_afford": False} if self.tick >= 2 else {}}

    async def refresh_state(self) -> Any:
        state = await super().refresh_state()
        state.persona_type = "trader"
        state.world_briefing = SimpleNamespace(
            situation=None, messages=SimpleNamespace(inbox=()),
            contract_targets=(SimpleNamespace(player_id="p2", username="Neon"),),
        )
        return state


async def test_a_retry_after_an_announced_move_uses_this_ticks_offer(
    tmp_path: Path, monkeypatch: Any
) -> None:
    home = home_of(tmp_path, monkeypatch)
    world = ContractWorld(refuse=frozenset({P[0]}))
    answers = iter([
        json.dumps({"action": P[0], "params": P[1], "why": "partner"}),
        json.dumps({"action": X, "params": {}, "why": "build instead"}),
    ])

    async def head_send(talk: list[dict[str, str]], tokens: int) -> str:
        return next(answers)

    async def word_send(talk: list[dict[str, str]], tokens: int) -> str:
        return "Hello Neon, a trade would help us both."

    inner = head.HeadDecider(ContractTree(), CONF, home, send=head_send)
    decider = speech.TalkingDecider(inner, CONF, home, "k-secret", send=word_send)
    refusals = shikigon_brain.Refusals()
    first = await shikigon_brain.tick(world, decider, None, refusals)
    # Tick 1: the word goes out and announces the proposal; X is allowed in this tick.
    assert world.sent == [(1, "message")], world.sent
    assert first["ok"]
    assert X in inner._allowed
    await shikigon_brain.tick(world, decider, None, refusals)
    assert (2, P[0]) in world.sent
    # README: "using only moves the world says are possible right now".
    assert (2, X) not in world.sent, f"sent: {world.sent}"


# --- F3: an empty advisor_count.json stops every move of the head ----------------------------


async def one_head_move(tmp_path: Path, monkeypatch: Any, damaged: str) -> tuple[World, dict]:
    home = home_of(tmp_path, monkeypatch)
    if damaged:
        (home / damaged).write_text("", encoding="utf-8")

    async def send(talk: list[dict[str, str]], tokens: int) -> str:
        return '{"action": "create_field", "params": {}, "why": "grow"}'

    world = World()
    decider = head.HeadDecider(Tree(), CONF, home, send=send)
    last = await shikigon_brain.guarded_tick(world, decider, None, shikigon_brain.Refusals())
    return world, last


@pytest.mark.parametrize("damaged", ["", "budget.json", "advisor_count.json"])
async def test_an_empty_state_file_does_not_stop_the_head(
    tmp_path: Path, monkeypatch: Any, damaged: str
) -> None:
    world, last = await one_head_move(tmp_path, monkeypatch, damaged)
    assert world.sent == [(1, X)], last.get("why")


TODAY = datetime.datetime.now(datetime.UTC).date().isoformat()


@pytest.mark.parametrize("text", ["5", json.dumps({"day": TODAY, "calls": "x", "moves": "y"})])
def test_the_daily_count_survives_a_file_of_another_form(tmp_path: Path, text: str) -> None:
    (tmp_path / "advisor_count.json").write_text(text, encoding="utf-8")
    assert advisor.calls_today(tmp_path) == 0
    advisor.count_call(tmp_path)
    assert advisor.calls_today(tmp_path) == 1
    assert advisor.count_move(tmp_path) == 1


# --- F4: an empty tournament_seen.json stops every move in a tournament ----------------------


@pytest.mark.parametrize("seen", [None, '{"ids": []}', ""])
async def test_an_empty_seen_file_does_not_stop_a_tournament(
    tmp_path: Path, monkeypatch: Any, seen: str | None
) -> None:
    home = home_of(tmp_path, monkeypatch)
    if seen is not None:
        (home / "tournament_seen.json").write_text(seen, encoding="utf-8")
    world = World(tournament={"status": "running"})
    lasts = [
        await shikigon_brain.guarded_tick(world, Tree(), None, shikigon_brain.Refusals())
        for _ in range(2)
    ]
    assert all(last.get("ok") for last in lasts), [last.get("why") for last in lasts]


@pytest.mark.parametrize("text", ["[]", '{"ids": 5}'])
def test_a_seen_file_of_another_form_is_a_first_look(
    tmp_path: Path, monkeypatch: Any, text: str
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    shikigon_brain.seen_path().parent.mkdir(parents=True)
    shikigon_brain.seen_path().write_text(text, encoding="utf-8")
    assert shikigon_brain.load_seen() is None


def test_a_seen_file_keeps_its_ids(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    shikigon_brain.seen_path().parent.mkdir(parents=True)
    shikigon_brain.seen_path().write_text('{"ids": ["a", 1]}', encoding="utf-8")
    assert shikigon_brain.load_seen() == ["a", "1"]
