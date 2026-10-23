"""The fifth review (cos20 #492, the betreuer pass at ``9ce01c0``): each finding closed where the
class has its narrow place.

1. the terminal drew the world's mission as markup: in the SDK (``cosmergon-agent`` 0.27.33,
   cos20 ``sdk-tests/test_fuenfter_durchgang_492.py``);
2. a world's count of ended missions cost two files each, as many as it listed;
3. a hint carried a sentence of the server into a notification (``tests/qml/tst_model.qml``);
4. "its key ended" was never reached with the real SDK: the test stood on a stub;
5. a word where the head's answer should hold a number ended the brain;
6. opening the panel started ``mise`` and asked two ports of this machine.

Finding 7 (the socket check reads the listeners, not the peer of the connection) is not built:
a residual named in the threat model (K11), like ``setsid`` at K14.

Each test is red against ``9ce01c0`` (counter-test), except where its docstring says it guards
the form of a fix.
"""

from __future__ import annotations

import json
import re
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "brain"))

import connections
import files
import head
import shikigon_brain as brain
from conftest import hostile
from test_advisor import CONF, _state
from test_brain import FakeDecider
from test_head import Replies, StubTree, _move

ROOT = Path(__file__).resolve().parent.parent
KEY = "test-key-not-real-0123"  # without the pattern of real keys (whitelist check)


@pytest.fixture(autouse=True)
def _private_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


# --- 2: a world's count of missions drives no more than fifty lines and one view ----------------


class Lists:
    """Stands in for the SDK: lists the missions it was given, as many as they are."""

    def __init__(self, missions: list[Any]) -> None:
        self.listed = missions

    async def missions(self) -> list[Any]:
        return list(self.listed)


async def test_ten_thousand_ended_missions_give_fifty_lines_and_one_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    files.write_json(brain.seen_path(), {"ids": ["looked before"]})
    many = [{"mission_id": f"m{i}" + "x" * 500, "mission_type": "gather_spores" * 50,
             "status": "completed", "body": "tournament", "outcome": "y" * 5000}
            for i in range(10_000)]  # fmt: skip
    views: list[int] = []
    shown = brain.show_journal

    def counted() -> None:
        views.append(1)
        shown()

    monkeypatch.setattr(brain, "show_journal", counted)
    state = SimpleNamespace(tournament={"status": "running"}, tick=7)
    written = await brain.tournament_journal(Lists(many), state)
    lines = files.tail(brain.state_dir() / "journal.jsonl", 100_000, line_bytes=4096)
    assert written == 50, f"{written} ended missions journaled in one look"
    assert len(lines) == 50, "one line per mission, fifty at most"
    assert views == [1], "the bar's view is written once for the whole look"
    assert all(len(line["mission"]) <= 80 and len(line["text"]) <= 200 for line in lines)
    ids = json.loads(brain.seen_path().read_text(encoding="utf-8"))["ids"]
    assert all(len(i) <= 64 for i in ids)


# --- 4: a key that ended says so, with the real SDK on a socket ---------------------------------


def _refuses(handler: BaseHTTPRequestHandler) -> None:
    body = b'{"error": {"code": 401, "message": "invalid or expired api key"}}'
    handler.send_response(401)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


async def test_a_key_that_ended_says_so_with_the_real_sdk() -> None:
    """Until 0.1.13 the test of this stood on a stub that raised in ``__aenter__``; the real SDK
    returned ``None`` on 401, and the bar said "the world did not answer" for ever."""
    with hostile(_refuses) as (address, seen):
        files.write_json(brain.credentials_path(), {"agent_id": "a1", "api_key": KEY,
                                                    "world": connections.origin(address)})
        cfg = {"world": {"base_url": address}, "brain": {}}
        last = await brain.with_agent(cfg, lambda a: brain.tick(a, FakeDecider(("wait", {}))))
    status = json.loads((brain.state_dir() / "status.json").read_text(encoding="utf-8"))
    assert seen, "the world was asked"
    assert status["reason"] == "key_ended", status
    assert "register a new Shikigon" in last["why"]


# --- 5: no value in the head's answer ends a move ----------------------------------------------

ODD: list[Any] = [None, True, 0, 10, -1, "half", "10", "1e999", "nan", "", [], [1, 2], {},
                  {"a": 1}, 10**30]  # fmt: skip


async def test_no_value_in_the_heads_answer_ends_a_move() -> None:
    files.write_json(brain.state_dir() / head.BUDGET_FILE,
                     {"day": head._today(), "start": 1000.0, "spent": 0.0})  # fmt: skip
    wire = json.loads((ROOT / "tests" / "fixtures" / "state_newborn.json").read_text())
    offers = {**wire["available_actions"], "transfer_energy": {"available": True}}
    state = _state(available_actions=offers, contracts={"pending_inbound": [{"contract_id": "c1"}]})

    async def forms() -> dict[str, Any]:
        return {"transfer_energy": [{"params": ["to", "amount"], "required": ["to", "amount"]}]}

    for value in ODD:
        for answer in (
            _move("transfer_energy", {"to": "p2", "amount": value}),
            _move("start_mission", {"mission_type": "gather_spores",
                                    "params": {"field_id": "f1"}, "amount": value}),
            _move("accept_contract", {"params": value, "contract_id": "c1"}),
        ):  # fmt: skip
            decider = head.HeadDecider(StubTree(), {**CONF, "learning_budget_pct": 5},
                                       brain.state_dir(), send=Replies(answer, answer),
                                       forms=forms)  # fmt: skip
            move = await decider.decide(state)
            assert isinstance(move, tuple) and isinstance(move[1], dict), (value, answer)


async def test_an_amount_that_is_no_number_lets_the_rules_play() -> None:
    files.write_json(brain.state_dir() / head.BUDGET_FILE,
                     {"day": head._today(), "start": 1000.0, "spent": 0.0})  # fmt: skip
    wire = json.loads((ROOT / "tests" / "fixtures" / "state_newborn.json").read_text())
    state = _state(available_actions={**wire["available_actions"],
                                      "transfer_energy": {"available": True}})  # fmt: skip

    async def forms() -> dict[str, Any]:
        return {"transfer_energy": [{"params": ["to", "amount"]}]}

    for amount in ("half", "nan", [5]):
        answer = _move("transfer_energy", {"to": "p2", "amount": amount})
        decider = head.HeadDecider(StubTree(), {**CONF, "learning_budget_pct": 5},
                                   brain.state_dir(), send=Replies(answer), forms=forms)
        assert await decider.decide(state) == ("create_field", {"size": 32}), amount
        assert decider.last is not None and "learning budget" in decider.last["fallback"]


# --- 6: opening the panel looks for nobody --------------------------------------------------------


def _function(qml: str, name: str) -> str:
    found = re.search(rf"function {name}\(\) \{{(.*?)\n  \}}", qml, re.DOTALL)
    assert found, name
    return found.group(1)


def test_opening_the_panel_starts_no_look_for_who_answers() -> None:
    """Guards the form: ``open()`` starts no ``advisor list`` (``mise``, two ports); ``ask``
    does, and its README names both."""
    qml = (ROOT / "qml" / "BarWidget.qml").read_text(encoding="utf-8")
    assert "refreshAdvisors" not in _function(qml, "open")
    assert "refreshAdvisors" in _function(qml, "startTalking")
    readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
    assert "When you press **ask**, the brain looks for who could answer" in readme
    assert "`mise`" in readme and "11434 and 1234" in readme
