"""Guards for the Betreuer-Durchgang c611ee5 (cos20 #492), B1 and B2.

B1: ``HeadDecider.decide`` measured the learning budget's rest before it asked the model and sent
it as the move's limit; ``budget_spend`` booked the loss into the budget of the day it was written
down. A move chosen before midnight UTC and written down after it went out with the rest of the
day before and counted on neither day: ``budget.json`` still held the old day, and its count was
dropped. Now ``decide`` names the day once, the move carries it (``last["day"]``, also through the
memo of an announced proposal), and the loss counts on that day. Red on c611ee5.

B2: what ``status.json`` names after midnight is what was left after the last move, and after a
restart the number comes with the first move the agent chooses. The README says so now (the text,
not the code, changed; red on c611ee5).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "brain"))

import files
import head
import shikigon_brain as brain
import speech
from test_betreuer_c47b7f1 import BUDGET, TOTAL, _gift_forms, _status_left
from test_effect import _with_total
from test_head import Replies, StubTree, _move

ROOT = Path(__file__).resolve().parent.parent
BEFORE, AFTER = "2026-11-06", "2026-11-07"  # two UTC days, midnight between them


@pytest.fixture(autouse=True)
def _private_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The UTC day ``head`` reads; a test moves it past midnight by setting ``clock[0]``."""
    today = [BEFORE]
    monkeypatch.setattr(head, "_today", lambda: today[0])
    return today


def _budget() -> dict[str, Any]:
    return json.loads((brain.state_dir() / head.BUDGET_FILE).read_text())


def _gift(amount: float) -> dict[str, Any]:
    """The world's answer to a gift of ``amount``: it booked the energy that went."""
    return {"action": "transfer_energy", "ok": True,
            "effect": {"free": -float(amount), "held": 0.0, "items": 0.0}}


def _chosen_gift(amount: float) -> tuple[head.HeadDecider, Any, tuple[str, dict[str, Any]]]:
    gift = _move("transfer_energy", {"to_player_id": "p2", "amount": amount}, "allies")
    decider = head.HeadDecider(StubTree(), BUDGET, brain.state_dir(), send=Replies(gift),
                               forms=_gift_forms)
    state = _with_total(TOTAL)
    played = asyncio.run(decider.decide(state))
    return decider, state, played


def test_b1_a_move_chosen_before_midnight_counts_on_that_day(clock: list[str]) -> None:
    decider, state, played = _chosen_gift(1_000)
    assert played[0] == "transfer_energy"
    assert played[1]["max_cost"] == pytest.approx(50_000.0), "the limit is the rest of its day"

    clock[0] = AFTER  # midnight passes while the move goes out
    asyncio.run(brain.write_down(None, decider, state, _gift(1_000)))

    assert _budget() == {"day": BEFORE, "start": TOTAL, "spent": 1_000.0}, (
        "the loss counts on the day the move was chosen")
    assert _status_left() == 49_000, "what was left after its last move"


def test_b1_the_new_day_starts_whole_at_its_first_move(clock: list[str]) -> None:
    decider, state, _ = _chosen_gift(1_000)
    clock[0] = AFTER
    asyncio.run(brain.write_down(None, decider, state, _gift(1_000)))

    after = TOTAL - 1_000
    assert head.budget_left(brain.state_dir(), BUDGET, after) == pytest.approx(after * 5 / 100)
    assert _budget()["day"] == AFTER and _budget()["spent"] == 0.0


def test_b1_the_announced_proposal_counts_on_the_day_it_was_chosen(clock: list[str]) -> None:
    """The proposal it announced goes out on the next move, up to a quarter of an hour later,
    with what the head did as its ``last``, kept in the memo file (``speech.py``, ``then``)."""
    decider, _, _ = _chosen_gift(700)
    files.write_json(speech.memo_path(brain.state_dir()), {"then_last": decider.last})
    chose = speech.load(brain.state_dir())["then_last"]
    assert chose is not None and chose.get("day") == BEFORE

    clock[0] = AFTER
    assert brain.spend_budget(chose, _gift(700)) == 700.0

    assert _budget() == {"day": BEFORE, "start": TOTAL, "spent": 700.0}


def test_b1_control_the_same_day_counts_as_before(clock: list[str]) -> None:
    decider, state, _ = _chosen_gift(1_000)
    asyncio.run(brain.write_down(None, decider, state, _gift(1_000)))

    assert _budget() == {"day": BEFORE, "start": TOTAL, "spent": 1_000.0}
    assert head.budget_left(brain.state_dir(), BUDGET, TOTAL) == pytest.approx(49_000.0)


def test_b1_where_the_new_day_began_first_it_stays_whole(clock: list[str]) -> None:
    """The rest the plan names: another process began the new day before the old move was
    written down. Nothing is counted then, and the new day keeps its whole budget. Until c611ee5
    the old move counted on the new day there."""
    decider, state, _ = _chosen_gift(1_000)
    clock[0] = AFTER
    head.budget_left(brain.state_dir(), BUDGET, TOTAL)  # a `tick` by hand, after midnight
    asyncio.run(brain.write_down(None, decider, state, _gift(1_000)))

    assert _budget() == {"day": AFTER, "start": TOTAL, "spent": 0.0}


def test_b1_a_day_of_another_form_in_the_memo_counts_today(clock: list[str]) -> None:
    """The memo is a file: a ``day`` that is no text is not followed."""
    head.budget_left(brain.state_dir(), BUDGET, TOTAL)
    chose = {"head": True, "choice": "transfer_energy", "own": True, "day": ["x"]}

    assert brain.spend_budget(chose, _gift(300)) == 300.0
    assert _budget() == {"day": BEFORE, "start": TOTAL, "spent": 300.0}


def test_b2_after_a_restart_the_number_comes_with_the_first_move_it_chooses(
    clock: list[str],
) -> None:
    """After a restart the announced proposal goes out from the memo: the head of this run has
    not chosen yet, so ``status.json`` names no rest; the move still counts. The next move the
    head chooses brings the number back (README, B2)."""
    first, state, _ = _chosen_gift(700)
    chose = json.loads(json.dumps(first.last))  # what the memo file keeps of it
    restarted = speech.TalkingDecider(
        head.HeadDecider(StubTree(), BUDGET, brain.state_dir(), send=Replies("no move"),
                         forms=_gift_forms),
        BUDGET, brain.state_dir(), "k")
    restarted.last = chose  # the ``then`` branch of ``TalkingDecider.decide``
    asyncio.run(brain.write_down(None, restarted, state, _gift(700)))

    assert _status_left() is None
    assert _budget()["spent"] == 700.0, "the move counts all the same"

    asyncio.run(restarted.inner.decide(state))
    asyncio.run(brain.write_down(None, restarted.inner, state, {"action": "create_field",
                                                                 "ok": True}))
    assert _status_left() == 49_300


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_b1_b2_the_readme_says_on_which_day_a_move_counts_and_what_the_status_names() -> None:
    readme = _flat((ROOT / "README.md").read_text(encoding="utf-8"))

    assert ("A move counts on the UTC day it was chosen, with that day's rest as its limit, "
            "also when it goes out after midnight.") in readme
    assert ("What was left after its last move stands in `status.json` "
            "(`learning_budget_left`); after a restart it comes with the first move your "
            "agent chooses.") in readme
    assert "What is left today stands in `status.json`" not in readme
