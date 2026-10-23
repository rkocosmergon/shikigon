"""The ninth review (cos20 #492, the betreuer pass at ``398a512``): what a contract costs this
Shikigon is read in one place (``contracts.costs_me``), for its head and for its rules.

1. the rule tree accepted an offer by its kind alone: a tribute it pays, an alliance with costs
   per tick (P1); under a budget its move was also what the head's refused move fell back to;
2. the budget let an alliance with costs per tick pass, offered or accepted (P2);
3. the budget let a mercenary pass that it hires without escrow; once he accepts, the world gives
   him its mega bombs (P3);
4. the budget compared the payer of a tribute as text: its own id in upper case went past (P4);
5. the expiry of its key, in a form that is no time, went uncut into every prompt (P5).

Each test is red against ``398a512`` (counter-test), except where its docstring says it guards
the form of a fix.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "brain"))

import advisor
import contracts
import head
import shikigon_brain as brain
from test_advisor import _state
from test_head import _move
from test_seventh_pass import FORMS
from test_sixth_pass import RULES, _budget, _head

ME = "3f0c9a52-7d1e-4b6a-9c2e-5a8b1d4e6f70"  # this agent, as the world names it
OTHER = "9b1e2c3d-4f5a-4b6c-8d7e-0f1a2b3c4d5e"
SPELLINGS = [ME, ME.upper(), ME.replace("-", ""), "{" + ME + "}", "urn:uuid:" + ME]


@pytest.fixture(autouse=True)
def _private_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


def _offer(contract_id: str, kind: str, **terms: Any) -> dict[str, Any]:
    return {"contract_id": contract_id, "contract_type": kind, "from_player_id": OTHER,
            "terms": {"duration": 100, **terms}}  # fmt: skip


def _offered(*offers: dict[str, Any], **changes: Any) -> Any:
    """This agent, a diplomat with energy (its tree accepts tribute and alliance by kind), with
    contracts offered to it."""
    return _state(agent_id=ME, persona_type="diplomat", energy_balance=5000.0,
                  contracts={"pending_inbound": list(offers)}, **changes)  # fmt: skip


def _tribute(payer: str) -> dict[str, Any]:
    return {"to_player_id": OTHER, "contract_type": "tribute", "escrow_amount": 0,
            "terms": {"amount_per_tick": 100, "payer_id": payer, "duration": 100}}  # fmt: skip


# --- the one place -------------------------------------------------------------------------------


@pytest.mark.parametrize("action, params, costs", [
    *[("propose_contract", _tribute(spelling), True) for spelling in SPELLINGS],
    ("propose_contract", _tribute(OTHER), False),  # the other pays it
    ("propose_contract", {**_tribute(OTHER), "terms": {"amount_per_tick": 1}}, True),  # no payer
    ("propose_contract", {"contract_type": "alliance", "terms": {"cost_per_tick": 1000}}, True),
    ("propose_contract", {"contract_type": "alliance", "terms": {"cost_per_tick": "lots"}}, True),
    ("propose_contract", {"contract_type": "alliance", "terms": {"duration": 100}}, False),
    ("propose_contract", {"contract_type": "mercenary", "escrow_amount": 0,
                          "terms": {"target_field_id": "f9", "reward": 1}}, True),
    ("propose_contract", {"contract_type": "non_aggression", "terms": {}}, False),
    ("propose_contract", {"contract_type": "non_aggression", "escrow_amount": 5}, True),
    ("propose_contract", {"contract_type": "a_kind_added_later"}, True),
    # the rule tree's own template proposal (2.3.10), and one it never sends
    ("propose_from_template", {"params": {"template_id": "T08_NON_AGGRESSION"},
                               "escrow_amount": 0}, False),
    ("propose_from_template", {"params": {"template_id": "T06_TRIBUTE"}}, True),
    ("propose_counter", {"contract_id": "c1", "terms": {}}, True),
    ("accept_contract", {"contract_id": "t-me"}, True),  # a tribute it pays
    ("accept_contract", {"contract_id": "T-ME"}, True),  # the same offer in another spelling
    ("accept_contract", {"contract_id": "t-other"}, False),  # a tribute paid to it
    ("accept_contract", {"contract_id": "a-cost"}, True),  # an alliance with costs per tick
    ("accept_contract", {"contract_id": "m-hired"}, False),  # it is the mercenary: it gets
    ("accept_contract", {"contract_id": "missing"}, True),
    ("create_field", {"size": 32}, False),  # no contract move
])  # fmt: skip
def test_what_a_contract_costs_this_agent(
    action: str, params: dict[str, Any], costs: bool,
) -> None:
    state = _offered(_offer("t-me", "tribute", payer_id=ME, amount_per_tick=50),
                     _offer("t-other", "tribute", payer_id=OTHER, amount_per_tick=50),
                     _offer("a-cost", "alliance", cost_per_tick=500),
                     _offer("m-hired", "mercenary", target_field_id="f9", reward=10))  # fmt: skip
    assert contracts.costs_me(action, params, state) is costs


# --- P2-P4: the head's own contract under a budget -----------------------------------------------


@pytest.mark.parametrize("action, params", [
    ("accept_contract", {"contract_id": "a-cost"}),  # P2: offered alliance with costs per tick
    ("propose_contract", {"to_player_id": OTHER, "contract_type": "alliance",
                          "terms": {"duration": 100, "cost_per_tick": 1000}}),  # P2
    ("propose_contract", {"to_player_id": OTHER, "contract_type": "mercenary", "escrow_amount": 0,
                          "terms": {"target_field_id": "f9", "reward": 1, "duration": 100}}),  # P3
    ("propose_contract", _tribute(ME.upper())),  # P4: its own id in upper case
])  # fmt: skip
async def test_a_contract_that_costs_this_agent_plays_the_rules(
    action: str, params: dict[str, Any],
) -> None:
    _budget()
    answer = _move(action, params)
    decider = _head(answer, answer, forms=FORMS)
    move = await decider.decide(_offered(_offer("a-cost", "alliance", cost_per_tick=500),
                                         available_actions={a: {"available": True}
                                                            for a in FORMS}))  # fmt: skip
    assert move == RULES, json.dumps(decider.last)
    assert decider.last is not None and "learning budget" in decider.last["fallback"]


async def test_a_tribute_paid_to_it_still_goes_out_under_a_budget() -> None:
    """Guards the form of the fix: a contract that costs nothing is still the head's to take."""
    _budget()
    answer = _move("accept_contract", {"contract_id": "t-other"})
    decider = _head(answer, answer, forms=FORMS)
    move = await decider.decide(_offered(_offer("t-other", "tribute", payer_id=OTHER,
                                                amount_per_tick=50)))  # fmt: skip
    assert move[0] == "accept_contract", json.dumps(decider.last)


# --- P1: the rules say no to a contract that would cost it ---------------------------------------


@pytest.mark.parametrize("offer", [
    _offer("c1", "tribute", payer_id=ME, amount_per_tick=50),
    _offer("c1", "tribute", payer_id=ME.upper(), amount_per_tick=50),
    _offer("c1", "alliance", cost_per_tick=500),
])  # fmt: skip
async def test_the_rules_reject_an_offer_that_would_cost_it(offer: dict[str, Any]) -> None:
    """The real rule tree (2.3.10) accepts a diplomat's tribute and alliance by their kind; the
    tree the brain builds for its deciders says no to one it would pay for."""
    decider = brain.make_decider({"brain": {"decider": "tree"}})
    assert await decider.decide(_offered(offer)) == ("reject_contract", {"contract_id": "c1"})


async def test_the_rules_still_accept_an_offer_that_costs_nothing() -> None:
    """Guards the form of the fix: a tribute paid to it is accepted as the tree decides."""
    decider = brain.make_decider({"brain": {"decider": "tree"}})
    move = await decider.decide(_offered(_offer("c1", "tribute", payer_id=OTHER,
                                                amount_per_tick=50)))  # fmt: skip
    assert move[0] == "accept_contract"


async def test_what_the_budget_refuses_the_rules_do_not_play_instead() -> None:
    """P1 under a budget: the head's own acceptance of a tribute it pays is refused, and what
    the rules play instead is a no, not the same acceptance."""
    from cosmergon_decider_tree import TreeDecider
    from test_head import Replies
    from test_sixth_pass import CONF

    _budget()
    answer = _move("accept_contract", {"contract_id": "c1"})
    decider = head.HeadDecider(contracts.Careful(TreeDecider()),
                               {**CONF, "learning_budget_pct": 5}, brain.state_dir(),
                               send=Replies(answer, answer))  # fmt: skip
    move = await decider.decide(_offered(_offer("c1", "tribute", payer_id=ME,
                                                amount_per_tick=50)))  # fmt: skip
    assert move == ("reject_contract", {"contract_id": "c1"}), json.dumps(decider.last)


# --- P5: what the world sends fills a prompt only so far -----------------------------------------


def test_the_expiry_of_its_key_is_cut_where_it_enters_a_prompt() -> None:
    told = advisor.facts(_state(key_expiry={"at": "x" * 1_000_000, "renews_on_use": True}))
    assert len(told) < 4000
