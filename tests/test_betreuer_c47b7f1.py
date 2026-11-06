"""Guards for the Betreuer-Durchgang c47b7f1 (cos20 #492), B1 and the reading P13.

B1: ``status.json`` named the learning budget as it stood before the move: ``write_down`` took
``decider.budget_left``, measured when the head chose, and counted the move's loss only after.
After the move that used the day's budget up, the file still showed a positive rest. Now it shows
what ``budget.json`` holds after the move; the count and the stop are the same as before.

P13: the head is shown a mission as ``{"mission_type": …, "params": {…}}`` (``head.shaped``). A
reason written into that inner ``params`` was not its reason: it stayed in the move, ``own_words``
refused the sentence, and the rules played instead. Now it is the reason, as one written into the
outer ``params``. Both red on c47b7f1.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "brain"))

import head
import shikigon_brain as brain
from test_advisor import CONF
from test_effect import _with_total
from test_head import Replies, StubTree, _move

BUDGET = {**CONF, "learning_budget_pct": 5}  # of 1,000,000: 50,000 a day
TOTAL = 1_000_000.0


@pytest.fixture(autouse=True)
def _private_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


async def _gift_forms() -> dict[str, Any]:
    form = {"params": ["to_player_id", "amount"], "required": ["to_player_id", "amount"],
            "description": "send energy to another agent"}
    return head.forms_of({"actions": {"transfer_energy": {"forms": [form]}}})


def _status_left() -> Any:
    status = json.loads((brain.state_dir() / "status.json").read_text())
    return status.get("learning_budget_left")


def _after_a_gift(amount: float) -> head.HeadDecider:
    """The head gives ``amount`` away, the world books it, and the brain writes the move down."""
    gift = _move("transfer_energy", {"to_player_id": "p2", "amount": amount}, "allies")
    decider = head.HeadDecider(StubTree(), BUDGET, brain.state_dir(), send=Replies(gift),
                               forms=_gift_forms)
    state = _with_total(TOTAL)
    played = asyncio.run(decider.decide(state))
    assert played[0] == "transfer_energy" and decider.budget_left == pytest.approx(50_000.0)
    last = {"action": "transfer_energy", "ok": True,
            "effect": {"free": -float(amount), "held": 0.0, "items": 0.0}}
    asyncio.run(brain.write_down(None, decider, state, last))
    return decider


def test_b1_the_status_names_what_is_left_after_the_move() -> None:
    _after_a_gift(1_000)

    assert _status_left() == 49_000
    assert head.budget_left(brain.state_dir(), BUDGET, TOTAL) == pytest.approx(49_000.0), (
        "what the stop reads: the count is the same as before")


def test_b1_after_the_move_that_uses_it_up_the_status_says_0() -> None:
    _after_a_gift(50_000)

    assert _status_left() == 0
    assert head.budget_left(brain.state_dir(), BUDGET, TOTAL) == 0.0


def test_b1_control_a_rules_move_leaves_the_status_whole() -> None:
    """Control: no answer of the head, the rules play, nothing is counted."""
    decider = head.HeadDecider(StubTree(), BUDGET, brain.state_dir(), send=Replies("no move"),
                               forms=_gift_forms)
    state = _with_total(TOTAL)
    played = asyncio.run(decider.decide(state))
    assert played == ("create_field", {"size": 32})
    last = {"action": "create_field", "ok": True,
            "effect": {"free": -5_000.0, "held": 0.0, "items": 0.0}}
    asyncio.run(brain.write_down(None, decider, state, last))

    assert _status_left() == 50_000


MISSION = {"mission_type": "gather_spores", "params": {"field_id": "f1"}}


def test_p13_a_reason_in_the_inner_params_is_its_reason() -> None:
    inner = {**MISSION["params"], "why": "spores are scarce here"}
    answer = json.dumps({"action": "start_mission", "params": {**MISSION, "params": inner}})

    move, why_not = head.parse_move(answer, {"start_mission"})

    assert why_not == "" and move is not None
    assert move == ("start_mission", MISSION, "spores are scarce here")
    assert head.own_words(move[1]) == "", "nothing of the reason stays in the move"


def test_p13_the_outer_reason_comes_first() -> None:
    inner = {**MISSION["params"], "why": "inner"}
    answer = json.dumps({"action": "start_mission", "why": "outer",
                         "params": {**MISSION, "params": inner}})

    assert head.parse_move(answer, {"start_mission"}) == (("start_mission", MISSION, "outer"), "")


def test_p13_control_a_sentence_elsewhere_in_the_move_stays_refused() -> None:
    """Only ``why`` is taken out; any other sentence is judged as before."""
    inner = {**MISSION["params"], "note": "spores are scarce here"}
    answer = json.dumps({"action": "start_mission", "params": {**MISSION, "params": inner}})

    move, _ = head.parse_move(answer, {"start_mission"})

    assert move is not None and head.own_words(move[1]) != ""
