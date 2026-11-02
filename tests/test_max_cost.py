"""The learning budget goes out with the move as ``max_cost`` (cos20 #527 C-a).

The world measures what a move costs and refuses one that costs more than ``max_cost``, with
nothing booked (cos20 ``v1.65.748``). The brain no longer guesses what a move gives before it goes
out, except for a contract, whose costs come later than the move (``contracts.costs_me``). A world
that does not know ``max_cost`` refuses it under ``strict`` as an unknown field, so the move is
not booked there either; a world without forms gets no move of the head's own under a budget.
"""

from __future__ import annotations

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


@pytest.fixture(autouse=True)
def _private_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


def _forms(action: str, params: list[str]) -> Any:
    async def load() -> dict[str, Any]:
        form = {"params": params, "required": params, "description": action}
        return head.forms_of({"actions": {action: {"forms": [form]}}})

    return load


async def _choose(answer: str, forms: Any = None) -> tuple[tuple[str, dict[str, Any]], Any]:
    decider = head.HeadDecider(StubTree(), BUDGET, brain.state_dir(),
                               send=Replies(answer, answer), forms=forms)
    return await decider.decide(_with_total(1_000_000.0)), decider


async def test_a_move_beyond_the_rest_goes_out_with_the_rest_as_its_limit() -> None:
    """The world says whether it costs too much, and the head learns from its answer."""
    gift = _move("transfer_energy", {"to_player_id": "p2", "amount": 60_000}, "allies")

    played, decider = await _choose(gift, _forms("transfer_energy", ["to_player_id", "amount"]))

    assert played[0] == "transfer_energy" and played[1]["amount"] == 60_000
    assert played[1]["strict"] is True and played[1]["max_cost"] == 50_000.0
    assert decider.last is not None and "fallback" not in decider.last
    assert "gives" not in decider.last, "counted from what the world books, not a guess"


async def test_buying_another_agents_offer_goes_out_with_its_limit() -> None:
    """Until now ``cannot be said``: 34 of 39 such fallbacks on the device were ``market_buy``."""
    buy = _move("market_buy", {"listing_id": "L-1"}, "cheap bombs")

    played, _ = await _choose(buy, _forms("market_buy", ["listing_id"]))

    assert played[0] == "market_buy" and played[1]["listing_id"] == "L-1"
    assert played[1]["strict"] is True and played[1]["max_cost"] == 50_000.0


async def test_without_a_form_no_limit_can_go_with_the_move_so_the_rules_play() -> None:
    """A guard, not new: ``form_problem`` lets no move of its own out without the world's form,
    and without one no ``strict`` and no ``max_cost`` could go with it."""
    gift = _move("transfer_energy", {"to_player_id": "p2", "amount": 100}, "small gift")

    played, decider = await _choose(gift)

    assert played == ("create_field", {"size": 32}), "the rules' line"
    assert decider.last is not None and "states no form" in decider.last["fallback"]


async def test_without_a_budget_no_limit_goes_out() -> None:
    gift = _move("transfer_energy", {"to_player_id": "p2", "amount": 60_000}, "allies")
    decider = head.HeadDecider(StubTree(), CONF, brain.state_dir(), send=Replies(gift),
                               forms=_forms("transfer_energy", ["to_player_id", "amount"]))

    played = await decider.decide(_with_total(1_000_000.0))

    assert "max_cost" not in played[1]


def test_a_limited_move_counts_what_the_world_booked_else_its_limit() -> None:
    """A move with ``max_cost`` carries no ``gives``. Where its answer names no effect (a lost
    answer, cos20 #614; a world that books nothing, K45), the limit counts: the most the world
    let it cost."""
    def left() -> float | None:
        return head.budget_left(brain.state_dir(), BUDGET, 1_000_000.0)

    left()  # the day begins: 50,000
    chose = {"head": True, "own": True, "choice": "market_buy", "limit": 20_000.0}
    brain.spend_budget(chose, {"ok": True, "action": "market_buy",
                               "effect": {"free": -800.0, "held": 0.0, "items": 100.0}})
    assert left() == pytest.approx(49_300.0), "what the world booked"
    brain.spend_budget(chose, {"ok": True, "action": "market_buy"})
    assert left() == pytest.approx(29_300.0), "no effect named: the limit"
    brain.spend_budget(chose, {"unknown": True, "action": "market_buy"})
    assert left() == pytest.approx(9_300.0), "a lost answer: the limit"
    brain.spend_budget(chose, {"ok": False, "action": "market_buy", "refused": True})
    assert left() == pytest.approx(9_300.0), "refused: nothing"
