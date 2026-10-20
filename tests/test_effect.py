"""Tests for what a move did, as the world says it (cos20 #533), and the learning budget.

The world answers each move with ``energy_effect``: what the move itself booked, not income,
decay or later effects. Shikigon keeps it in the journal, shows it to the head next to its own
last moves, sets it beside the window number when the head looks back, and counts the losses of
the head's own moves against a daily budget. Each test fails on the code before (``5191235``):
none of it existed, and the head was simply never offered a transfer.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "brain"))

import head
import lessons
import shikigon_brain as brain
from test_advisor import CONF, _state
from test_head import Replies, StubTree, _move


@pytest.fixture(autouse=True)
def _private_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


GIFT = {"free": -214612.0, "held": 0.0, "items": 0.0, "by_type": {"transfer_out": -214612.0}}
OFFER = {"free": -100403.0, "held": 100403.0, "items": 0.0}


class Answer:
    """What the SDK hands back: success and the server's body in ``data``."""

    def __init__(self, effect: dict[str, Any] | None) -> None:
        self.success = True
        self.error_message = ""
        self.data = {"success": True, "result": {}, "energy_effect": effect} if effect else {}


class World:
    def __init__(self, effect: dict[str, Any] | None) -> None:
        self.effect = effect

    async def act(self, action: str, **params: Any) -> Answer:
        return Answer(self.effect)


async def test_the_journal_keeps_what_the_move_booked() -> None:
    done = await brain.play(World(GIFT), "transfer_energy", {"to_player_id": "p2", "amount": 5})
    assert done["effect"] == {"free": -214612.0, "held": 0.0, "items": 0.0}
    assert "effect" not in await brain.play(World(None), "wait", {}), "an older world: nothing"


def test_the_head_sees_what_its_last_moves_did() -> None:
    home = brain.state_dir()
    home.mkdir(parents=True, exist_ok=True)
    rows = [{"tick": 7, "action": "transfer_energy", "ok": True, "effect": GIFT},
            {"tick": 8, "action": "market_list", "ok": True, "effect": OFFER}]
    (home / "journal.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    lines = head.recent_moves(home)
    assert lines[0] == "- tick 7: transfer_energy — taken, this move: -214,612"
    assert lines[1] == (
        "- tick 8: market_list — taken, this move: -100,403, held +100,403 in your offers or "
        "contracts"
    )


def test_a_cancelled_offer_reads_as_held_coming_back() -> None:
    """cos20 #533 review: unsigned, a cancel read "-1,000 held in an offer", like a stock."""
    said = head._effect({"effect": {"free": 1000.0, "held": -1000.0, "items": 0.0}})
    assert said == ", this move: +1,000, held -1,000 in your offers or contracts"


def test_the_look_back_sets_the_own_effect_beside_the_window() -> None:
    moves = [{"tick": t, "action": "transfer_energy", "ok": True, "total": 1_000_000.0 + t,
              "effect": GIFT} for t in range(lessons.HORIZON + 2)]
    text = lessons.summary(moves)
    assert "all causes" in text
    assert "own effect -214,612" in text
    assert lessons.own_effect({"effect": OFFER}) == 0.0, "an offer keeps the energy: no loss"
    asked = lessons.lesson_messages("facts", "numbers", before="Sell bombs.")[1]["content"]
    assert "Keep every rule unless the numbers contradict it" in asked


def _with_total(total: float) -> Any:
    return _state(ranking={"wertung": "total_energy", "gesamtenergie": total})


async def _transfer_forms() -> dict[str, Any]:
    form = {"params": ["to_player_id", "amount"], "required": ["to_player_id", "amount"],
            "description": "send energy to another agent"}
    return head.forms_of({"actions": {"transfer_energy": {"forms": [form]}}})


async def test_a_transfer_beyond_the_learning_budget_is_played_by_the_rules() -> None:
    gift = _move("transfer_energy", {"to_player_id": "p2", "amount": 60_000}, "allies")
    decider = head.HeadDecider(StubTree(), {**CONF, "learning_budget_pct": 5}, brain.state_dir(),
                               send=Replies(gift), forms=_transfer_forms)
    action, _ = await decider.decide(_with_total(1_000_000.0))  # 5 % = 50,000 left today
    assert action == "create_field", "the rules' line"
    assert decider.last is not None and "learning budget" in decider.last["fallback"]
    assert decider.budget_left == pytest.approx(50_000.0)


async def test_a_used_up_budget_lets_the_rules_play_without_asking() -> None:
    model = Replies(_move("wait"))
    decider = head.HeadDecider(StubTree(), {**CONF, "learning_budget_pct": 5}, brain.state_dir(),
                               send=model)
    await decider.decide(_with_total(1_000_000.0))
    head.budget_spend(brain.state_dir(), 50_000.0)
    asked = len(model.asked)
    assert await decider.decide(_with_total(1_000_000.0)) == ("create_field", {"size": 32})
    assert len(model.asked) == asked, "no call once the budget is used up"


def test_only_the_heads_own_losses_count() -> None:
    home = brain.state_dir()
    home.mkdir(parents=True, exist_ok=True)
    head.budget_left(home, {"learning_budget_pct": 5}, 1_000_000.0)
    brain.spend_budget({"head": True, "own": True}, {"ok": True, "effect": GIFT})
    brain.spend_budget({"head": True, "own": False}, {"ok": True, "effect": GIFT})  # rules' line
    brain.spend_budget({"head": True, "own": True}, {"ok": True, "effect": OFFER})  # no loss
    left = head.budget_left(home, {"learning_budget_pct": 5}, 1_000_000.0)
    assert left == pytest.approx(0.0), "50,000 set aside, one gift of 214,612 counted"
    assert head.budget_left(home, {"learning_budget_pct": 0}, 1_000_000.0) is None
