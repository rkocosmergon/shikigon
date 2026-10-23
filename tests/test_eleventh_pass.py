"""The eleventh review (cos20 #492, the betreuer pass at ``1d320eb``): the learning budget counts
what it checked, and goods have no value it could check.

1. Omarchy 4.0.0 and 4.0.1 read markup in their Button and PanelSectionHeader, and the bar gave
   them model names and the world's tournament number (P1: ``tests/test_qml.py``,
   ``tests/qml/tst_model.qml``);
2. the budget counted a move only by what the world booked (``energy_effect``): a world that
   sends none left the rest whole all day, for every transfer up to it (P2, K45);
3. goods counted at the world's reference price, the cheapest offer of any other agent, while
   the world books them at the house price: one item offered at 1 let the whole stock pass (P3,
   K46).

Each test is red against ``1d320eb`` (counter-test).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "brain"))

import head
import shikigon_brain as brain
from test_advisor import CONF, _state
from test_brain import FakeAgent
from test_head import Replies, StubTree, _move
from test_sixth_pass import RULES, _budget, _head, _offering

BUDGET = {"learning_budget_pct": 5}
TOTAL = 1_000_000.0  # 5 % of it: 50,000 a day


@pytest.fixture(autouse=True)
def _private_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


def _with_total() -> Any:
    return _state(ranking={"wertung": "total_energy", "gesamtenergie": TOTAL})


async def _transfer_forms() -> dict[str, Any]:
    form = {"params": ["to_player_id", "amount"], "required": ["to_player_id", "amount"],
            "description": "send energy to another agent"}
    return head.forms_of({"actions": {"transfer_energy": {"forms": [form]}}})


def _left() -> float | None:
    return head.budget_left(brain.state_dir(), BUDGET, TOTAL)


# --- 2: what the check put on a move counts, also when the world books nothing ---------------


async def test_a_transfer_counts_against_the_budget_also_when_the_world_books_nothing() -> None:
    """The real move: ``brain.tick`` with the head, against a world whose answers carry no
    ``energy_effect`` (``FakeAgent``: no ``data``). The first gift goes out and is counted; the
    second no longer fits the rest, and the rules play."""
    gift = _move("transfer_energy", {"to_player_id": "p2", "amount": 30_000}, "allies")
    decider = head.HeadDecider(StubTree(), {**CONF, **BUDGET}, brain.state_dir(),
                               send=Replies(gift, gift), forms=_transfer_forms)  # fmt: skip
    agent = FakeAgent(_with_total())
    first = await brain.tick(agent, decider)
    assert first["action"] == "transfer_energy" and first["ok"], first
    assert _left() == pytest.approx(20_000.0), "the 30,000 it checked are counted"
    second = await brain.tick(agent, decider)
    assert second["action"] == RULES[0], "the rest no longer covers a second gift"
    assert [action for action, _ in agent.acted] == ["transfer_energy", RULES[0]]


async def test_only_the_move_that_went_out_counts_what_it_gives() -> None:
    """A word written in the place of the head's move gives nothing, a refused move nothing; the
    move itself counts what the check put on it, or the world's booking if that is larger. A move
    the budget refused carries no value at all."""
    _left()  # the day begins
    chose = {"head": True, "own": True, "choice": "transfer_energy", "gives": 30_000.0}
    brain.spend_budget(chose, {"ok": True, "action": "message"})  # a word went out instead
    brain.spend_budget(chose, {"ok": False, "action": "transfer_energy", "refused": True})
    assert _left() == pytest.approx(50_000.0)
    brain.spend_budget(chose, {"ok": True, "action": "transfer_energy"})  # no booking
    assert _left() == pytest.approx(20_000.0)
    booked = {"free": -32_000.0, "held": 0.0, "items": 0.0}  # a fee on top
    brain.spend_budget({**chose, "gives": 10_000.0},
                       {"ok": True, "action": "transfer_energy", "effect": booked})
    assert _left() == pytest.approx(0.0), "the larger of the two: the booking"

    too_much = _move("transfer_energy", {"to_player_id": "p2", "amount": 60_000}, "allies")
    decider = head.HeadDecider(StubTree(), {**CONF, **BUDGET}, brain.state_dir(),
                               send=Replies(too_much), forms=_transfer_forms)  # fmt: skip
    assert await decider.decide(_with_total()) == RULES
    assert decider.last is not None and "gives" not in decider.last


# --- 3: goods have no value the brain can say -------------------------------------------------


async def test_giving_goods_plays_the_rules_whatever_price_the_world_names() -> None:
    """Another agent offers one mega bomb at 1: the world names 1 as its reference price, and
    fifty of them counted 50 against a rest of 50. The world books them at the house price."""
    cheap = {"market_list": {"available": True, "reference_prices": {"mega_bomb": 1}}}
    params = {"to_player_id": "p2", "item_type": "mega_bomb", "count": 50}
    answer = _move("transfer_inventory", params)
    decider = _head(answer, answer, forms={"transfer_inventory": [
        {"params": ["to_player_id", "item_type", "count"]}]})
    _budget()  # a rest of 50 today
    assert await decider.decide(_offering("transfer_inventory", **cheap)) == RULES
    assert decider.last is not None and "cannot be said" in decider.last["fallback"]
    assert head.gives_away("transfer_inventory", params, _offering(**cheap)) is None
    assert head.gives_away("transfer_energy", {"to_player_id": "p2", "amount": 7},
                           _offering()) == 7  # energy keeps its number
