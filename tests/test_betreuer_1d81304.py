"""Guards for the Betreuer-Durchgang 1d81304 (cos20 #492, plan 6702975051), B1–B4.

B1–B3, one class: a ``tick`` by hand (``shikigon tick``, or the bar's keybind) next to the running
brain. ``files.held`` covered one read, change and write; what a move reads when it chooses and
uses when it goes out spans the whole move. Both moves chose from the same rest of the learning
budget and went out with it as their limit (B1), both read the same spacing of words and the same
unanswered message and both wrote (B2), and a move the other process paused went out once more
(B3). Now one move plays at a time (``files.one_move``); the other plays none and says so.

B4: ``files.held`` waited for its ``flock`` without a deadline, in the thread of the brain's event
loop (K24). Now it waits ``files.HELD_WAIT_S`` at most, then ``TimeoutError``.

Red on 1d81304, the positive controls green.
"""

from __future__ import annotations

import ast
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
BRAIN = ROOT / "brain"
sys.path.insert(0, str(BRAIN))

import files
import head
import shikigon_brain as brain
import speech
from test_advisor import CONF, Model
from test_betreuer_c47b7f1 import BUDGET, TOTAL, _gift_forms
from test_brain import CountingDecider, FakeAgent
from test_brain import _state as _brain_state
from test_effect import Answer, _with_total
from test_head import Replies, StubTree, _move

REST = TOTAL * 5 / 100  # the day's learning budget: 50,000
KEY = "test-key-not-real-0123"
WAIT = getattr(files, "HELD_WAIT_S", 2.0)  # the counter-test on 1d81304 has no constant


@pytest.fixture(autouse=True)
def _private_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


class SlowReplies(Replies):
    """The head's model, answering after a while: the other process moves meanwhile."""

    async def __call__(self, messages: list[dict[str, str]], max_tokens: int) -> str:
        await asyncio.sleep(0.05)
        return await super().__call__(messages, max_tokens)


class SlowModel(Model):
    async def __call__(self, messages: list[dict[str, str]], max_tokens: int) -> str:
        await asyncio.sleep(0.05)
        return await super().__call__(messages, max_tokens)


class BookingWorld(FakeAgent):
    """A world that takes every move and books ``loss`` of free energy for it."""

    def __init__(self, state: Any, loss: float) -> None:
        super().__init__(state)
        self.loss = loss

    async def act(self, action: str, **params: Any) -> Any:  # type: ignore[override]
        self.acted.append((action, params))
        return Answer({"free": -self.loss, "held": 0.0, "items": 0.0})


def _gift_head() -> head.HeadDecider:
    gift = _move("transfer_energy", {"to_player_id": "p2", "amount": REST}, "allies")
    return head.HeadDecider(StubTree(), BUDGET, brain.state_dir(), send=SlowReplies(gift),
                            forms=_gift_forms)


def _spent() -> float:
    return float(json.loads((brain.state_dir() / head.BUDGET_FILE).read_text())["spent"])


# ---------- B1: the learning budget's rest goes out once ----------------------------------------


async def test_b1_a_tick_by_hand_beside_the_brain_spends_the_rest_once() -> None:
    """Both would give the whole rest away: one goes out, the other plays none."""
    state = _with_total(TOTAL)
    running, by_hand = BookingWorld(state, REST), BookingWorld(state, REST)
    first, second = await asyncio.gather(brain.tick(running, _gift_head()),
                                         brain.tick(by_hand, _gift_head()))
    played = [last for last in (first, second) if not last.get("skipped")]
    assert len(played) == 1, "one move at a time"
    assert len(running.acted) + len(by_hand.acted) == 1
    assert [last.get("skipped") for last in (first, second) if last.get("skipped")] == [
        brain.ANOTHER_MOVE]
    assert _spent() <= REST, "no day loses more than its budget"


async def test_b1_control_one_after_another_both_choose_and_the_rules_take_over() -> None:
    state = _with_total(TOTAL)
    world = BookingWorld(state, REST)
    first = await brain.tick(world, _gift_head())
    assert first["action"] == "transfer_energy" and _spent() == REST
    second = await brain.tick(world, _gift_head())
    assert not second.get("skipped") and second["advice"]["fallback"] == (
        "learning budget used up today")
    assert _spent() == REST


# ---------- B2: one word, one answer -------------------------------------------------------------


def _with_message() -> Any:
    state = _brain_state()
    msg = SimpleNamespace(id="m1", from_player_id="p-agni", from_name="Agni-swift",
                          text="Pact for 50 ticks?", at="")
    state.world_briefing = SimpleNamespace(
        situation=state.world_briefing.situation, contract_targets=(),
        messages=SimpleNamespace(inbox=(msg,), rooms=()))
    return state


def _talking() -> speech.TalkingDecider:
    return speech.TalkingDecider(CountingDecider(("evolve", {})), CONF, brain.state_dir(), KEY,
                                 send=SlowModel("Yes, a pact for 50 ticks suits me."))


async def test_b2_a_message_is_answered_once_also_beside_a_tick_by_hand() -> None:
    state = _with_message()
    running, by_hand = FakeAgent(state), FakeAgent(state)
    await asyncio.gather(brain.tick(running, _talking()), brain.tick(by_hand, _talking()))
    words = [a for a, _ in running.acted + by_hand.acted if a == "message"]
    assert len(words) == 1, "one answer, and ten moves before the next word"
    memo = speech.load(brain.state_dir())
    assert memo["answered"] == ["m1"] and memo["moves"] == 1


async def test_b2_control_one_after_another_the_second_does_not_answer_again() -> None:
    state = _with_message()
    agent = FakeAgent(state)
    await brain.tick(agent, _talking())
    await brain.tick(agent, _talking())
    assert [a for a, _ in agent.acted] == ["message", "evolve"]


# ---------- Two processes: a move under way leaves the other one out ------------------------------

HOLDER = """
import sys, time
sys.path.insert(0, sys.argv[1])
import files
from pathlib import Path
with getattr(files, sys.argv[3])(Path(sys.argv[2])) as mine:
    print("held", mine, flush=True)
    time.sleep(float(sys.argv[4]))
"""


def _holder(what: str, seconds: float) -> subprocess.Popen[str]:
    child = subprocess.Popen(
        [sys.executable, "-I", "-c", HOLDER, str(BRAIN), str(brain.state_dir()), what,
         str(seconds)], stdout=subprocess.PIPE, text=True)
    assert child.stdout is not None
    line = child.stdout.readline()
    assert line.startswith("held"), line
    return child


async def test_a_move_of_another_process_leaves_this_one_out() -> None:
    child = _holder("one_move", 30)
    try:
        agent, decider = FakeAgent(_brain_state()), CountingDecider(("evolve", {}))
        last = await brain.tick(agent, decider)
        assert last["skipped"] == brain.ANOTHER_MOVE
        assert decider.asked == 0 and agent.acted == []
        assert not (brain.state_dir() / "journal.jsonl").exists(), "nothing written"
    finally:
        child.kill()
        child.wait()
    played = await brain.tick(agent, decider)  # the other move is over: this one plays
    assert played["action"] == "evolve" and decider.asked == 1


def test_the_readme_names_the_move_lock() -> None:
    """H6: a file the brain writes stands in the README's table."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"| `~/.local/state/shikigon/{files.MOVE_LOCK}` | empty; locked while a move plays" in (
        readme)


def test_the_move_lock_waits_for_nobody() -> None:
    child = _holder("one_move", 30)
    try:
        began = time.monotonic()
        with files.one_move(brain.state_dir()) as mine:
            assert mine is False
        assert time.monotonic() - began < 0.5
    finally:
        child.kill()
        child.wait()
    with files.one_move(brain.state_dir()) as mine:
        assert mine is True


# ---------- B4: the state folder's lock has a deadline -------------------------------------------


def test_b4_held_gives_up_after_its_deadline() -> None:
    brain.state_dir().mkdir(parents=True, exist_ok=True)
    child = _holder("held", WAIT * 2 + 2)
    try:
        began = time.monotonic()
        with pytest.raises(TimeoutError), files.held(brain.state_dir()):
            pass
        waited = time.monotonic() - began
        assert WAIT <= waited < WAIT * 2
    finally:
        child.kill()
        child.wait()


def test_b4_control_held_passes_once_the_other_lets_go() -> None:
    brain.state_dir().mkdir(parents=True, exist_ok=True)
    child = _holder("held", 0.3)
    try:
        with files.held(brain.state_dir()):
            pass
    finally:
        child.wait()


async def test_b4_a_count_that_waits_too_long_says_so_and_the_move_stays_played() -> None:
    """``Refusals.note`` under ``held``: past the deadline it counts in memory for this tick."""
    refusals = brain.Refusals(brain.refusals_path())
    refusals.path.parent.mkdir(parents=True, exist_ok=True)
    child = _holder("held", WAIT * 2 + 2)
    try:
        assert refusals.note({"action": "evolve", "ok": True}) is False
    finally:
        child.kill()
        child.wait()


# ---------- The class at its narrow place: every step of a move under ``files.one_move`` ---------

# Where a step of a move may be called, and why there. The brain's own steps by name, a decider's
# ``decide`` as a method (``connections.choose`` is another function of that name).
NAME_STEPS = {"choose", "play", "try_again", "write_down", "_tick", "again"}
METHOD_STEPS = {"decide"}
STEP_PLACES = {
    "tick": {"_tick"},  # under ``files.one_move``, checked below
    "_tick": {"choose", "play", "try_again", "write_down"},
    "try_again": {"play", "again"},
    "choose": {"decide"},
    "advise": {"decide"},  # a dry run: plays nothing, says nothing, books no budget
}


def _callee(call: ast.Call) -> str:
    func = call.func
    return func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")


def _step(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Name) and func.id in NAME_STEPS:
        return func.id
    if isinstance(func, ast.Attribute) and func.attr in METHOD_STEPS:
        return func.attr
    return ""


def move_steps_outside(source: str) -> list[str]:
    """Every step of a move called from a place not listed, and a ``tick`` whose ``_tick`` is not
    under ``with files.one_move(...)``."""
    tree = ast.parse(source)
    bad = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        allowed = STEP_PLACES.get(fn.name, set())
        inner = {id(n) for f in ast.walk(fn) if f is not fn and isinstance(
            f, ast.FunctionDef | ast.AsyncFunctionDef) for n in ast.walk(f)}
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and id(node) not in inner:
                name = _step(node)
                if name and name not in allowed:
                    bad.append(f"{fn.name}:{node.lineno} {name}")
        if fn.name == "tick":
            locked = [w for w in ast.walk(fn) if isinstance(w, ast.With) and any(
                isinstance(i.context_expr, ast.Call)
                and _callee(i.context_expr) == "one_move" for i in w.items)]
            under = {id(n) for w in locked for n in ast.walk(w)}
            calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and _callee(n) == "_tick"]
            bad += [f"tick:{n.lineno} _tick outside files.one_move" for n in calls
                    if id(n) not in under]
            if not locked:
                bad.append("tick: no files.one_move")
    return bad


def test_every_step_of_a_move_runs_under_the_move_lock() -> None:
    source = (BRAIN / "shikigon_brain.py").read_text(encoding="utf-8")
    assert move_steps_outside(source) == []


def test_the_move_guard_sees_what_it_guards() -> None:
    """Counter-test: a step from another place, and a ``tick`` without the lock, are reported."""
    assert move_steps_outside("async def other(a):\n    await play(a, 'x', {})\n")
    assert move_steps_outside("async def other(d):\n    await d.decide(s)\n")
    assert not move_steps_outside("def other(h):\n    connections.choose(h, {})\n")
    assert move_steps_outside("async def tick(a):\n    return await _tick(a)\n")
    assert not move_steps_outside(
        "async def tick(a):\n    with files.one_move(d) as mine:\n        return await _tick(a)\n")
    # 1d81304: the move itself was ``tick``, with no lock around it
    old = "async def tick(a, d):\n    action, params = await choose(d, s, p)\n"
    assert move_steps_outside(old)
