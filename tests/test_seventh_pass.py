"""The seventh review (cos20 #492, the betreuer pass at ``91955ba``): each finding closed where the
class has its narrow place, with the variants the class had in this code.

1. pi appended ``~/.pi/agent/APPEND_SYSTEM.md`` to its prompt, OpenCode its global ``AGENTS.md``
   (or ``~/.claude/CLAUDE.md``), and what the model wrote could go on to another player. pi now
   runs with an ``--append-system-prompt`` of its own; OpenCode is no longer asked from here;
2. an ``actions`` of the world's rules in another form ended ``ask`` with a traceback;
3. the learning budget checked only transfers: buying another agent's offer and a contract that
   has this agent pay went past it;
4. the checks before a model list resolved the name outside the list's deadline. Variants of the
   same class: the world's peer check resolved the world's name when the agent was made, and
   every loop of the brain waited, when it closed, for a resolution still at work in one of its
   threads (``asyncio.run``, up to 300 s).

Each test is red against ``91955ba`` (counter-test), except where its docstring says it guards
the form of a fix.
"""

from __future__ import annotations

import ast
import functools
import io
import json
import re
import socket
import sys
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "brain"))

import connections
import head
import shikigon_brain as brain
from conftest import hostile, is_command
from test_advisor import _state
from test_head import _move
from test_sixth_pass import RULES, _agent_on_path, _budget, _head, _offering, _registered

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "state_newborn.json").read_text())
ME = FIXTURE["agent_id"]  # this agent in the fixture


@pytest.fixture(autouse=True)
def _private_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


# --- 1: nothing of the player's own goes into an agent's prompt ---------------------------------


def test_pi_is_asked_with_an_appended_prompt_of_its_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pi 1.2 to 1.4 appends ``~/.pi/agent/APPEND_SYSTEM.md`` unless ``--append-system-prompt``
    is given (``core/resource-loader.ts:663-667, 1227-1238``); the text is read as a file only
    where a file of that name exists (``:167-182``). The command pi is really started with."""
    asked = _agent_on_path(tmp_path, monkeypatch, "pi", "1.4.0")
    connections.run_agent({"agent": "pi"}, "a question", 30.0)
    started = asked.read_text()
    assert "--append-system-prompt " + connections.NO_TOOLS in started, started
    assert "--system-prompt " + connections.PI_SYSTEM in started
    assert "/" not in connections.NO_TOOLS, "no path: pi would read a file of that name"


def test_opencode_is_not_offered_and_a_choice_of_it_from_before_asks_nobody() -> None:
    rows = connections.found({}, lambda *a, **k: None, is_installed=lambda agent: True,
                             is_own=lambda url: False, why_not=lambda agent: "")  # fmt: skip
    assert [row["id"] for row in rows] == ["agent:pi"]
    # picked under 0.1.12 and left in advisor.json: nobody is picked, nothing is started
    connections.choose(brain.state_dir(), {"via": "agent", "agent": "opencode",
                                           "agent_model": "m", "advise": "head"})  # fmt: skip
    cfg = brain.load_config()
    assert brain.advisor_now(cfg)["picked"] is False
    with pytest.raises(connections.AgentError, match="not asked from here"):
        connections.run_agent({"agent": "opencode"}, "a question", 5.0,
                              lambda *a, **k: pytest.fail("an agent was started"))  # fmt: skip


# --- 2: the rules of the world read by their form ------------------------------------------------

ODD_ACTIONS = [[1], "all of them", 7, True, [{"create_field": {}}], {"create_field": [1, 2]}]


def _info(body: Any) -> Any:
    return SimpleNamespace(json=lambda: body, raise_for_status=lambda: None, status_code=200)


@pytest.mark.parametrize("actions", ODD_ACTIONS)
def test_rules_of_another_form_are_read_as_far_as_they_go(actions: Any) -> None:
    cfg = {"world": {"base_url": "https://world.test"}}
    rules = brain.world_rules(cfg, lambda url, **_: _info({"actions": actions, "economy": "x"}))
    assert json.loads(rules)["economy"] == "x"
    assert brain.world_forms(cfg, lambda url, **_: _info({"actions": actions})) == {}


def _world_of_odd_rules(handler: BaseHTTPRequestHandler) -> None:
    if handler.path.startswith("/api/v1/game/info"):
        body = json.dumps({"actions": ["place_cells", "evolve"]}).encode()
    elif handler.path.endswith("/state"):
        body = json.dumps(FIXTURE).encode()
    else:
        body = b"{}"
    handler.send_response(200)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def test_ask_answers_in_one_sentence_whatever_form_the_rules_come_in(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """The command as the bar starts it (``main``), against a world on a socket."""
    with hostile(_world_of_odd_rules) as (address, _):
        _registered(address)
        config = brain.config_path()
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(f'[world]\nbase_url = "{address}"\n')
        monkeypatch.setattr(sys, "stdin", io.StringIO("What should I do now?\n"))
        assert brain.main(["ask"]) == 0
    turn = json.loads(capsys.readouterr().out)
    assert turn["q"] == "What should I do now?" and turn["why"] == "nobody is picked to answer yet"


# --- 3: what a move gives to another agent is checked before it goes out ------------------------

FORMS = {name: [{"params": fields}] for name, fields in {
    "market_buy": ["listing_id", "item_type", "max_price"],
    "propose_contract": ["to_player_id", "contract_type", "terms", "escrow_amount"],
    "breach_contract": ["contract_id"],
    "future_move": ["to_player_id", "amount"],
    "market_list": ["item_type", "item_data", "price_energy"],
}.items()}  # fmt: skip
OFFERED = [{"contract_id": "c1", "contract_type": "tribute", "from_player_id": "p2",
            "terms": {"amount_per_tick": 100, "payer_id": ME, "duration": 50}},
           {"contract_id": "c2", "contract_type": "non_aggression", "from_player_id": "p3",
            "terms": {}}]  # fmt: skip


def _waiting() -> Any:
    """The world offers the moves of ``FORMS``, and two contracts wait for an answer."""
    return _state(available_actions=_offering(*FORMS).available_actions,
                  contracts={"pending_inbound": OFFERED})  # fmt: skip


@pytest.mark.parametrize("action, params", [
    ("market_buy", {"listing_id": "L-of-another-agent"}),
    ("accept_contract", {"contract_id": "c1"}),  # a tribute this agent would pay
    ("accept_contract", {"contract_id": "c2", "escrow_amount": 100}),  # escrow held
    ("propose_contract", {"to_player_id": "p2", "contract_type": "tribute",
                          "terms": {"amount_per_tick": 5, "payer_id": ME, "duration": 10}}),
    ("propose_contract", {"to_player_id": "p2", "contract_type": "mercenary",
                          "escrow_amount": 40}),
    ("breach_contract", {"contract_id": "c9"}),
    ("future_move", {"to_player_id": "p2", "amount": 1}),  # a move the world adds later
])  # fmt: skip
async def test_a_move_whose_gift_cannot_be_said_plays_the_rules(
    action: str, params: dict[str, Any],
) -> None:
    _budget()
    answer = _move(action, params)
    decider = _head(answer, answer, forms=FORMS)
    move = await decider.decide(_waiting())
    assert move == RULES, json.dumps(decider.last)
    assert decider.last is not None and "learning budget" in decider.last["fallback"]


@pytest.mark.parametrize("action, params", [
    ("accept_contract", {"contract_id": "c2"}),  # a pact without escrow: gives nothing
    ("propose_contract", {"to_player_id": "p3", "contract_type": "non_aggression"}),
    ("market_list", {"item_type": "energy", "item_data": {"amount": 10}, "price_energy": 1}),
])  # fmt: skip
async def test_a_move_that_gives_nothing_goes_out_also_with_a_budget(
    action: str, params: dict[str, Any],
) -> None:
    """Guards the form of the fix: a pact, an offer below its worth (the README's exception)."""
    _budget()
    answer = _move(action, params)
    decider = _head(answer, answer, forms=FORMS)
    move = await decider.decide(_waiting())
    assert move[0] == action, json.dumps(decider.last)


def test_every_move_the_world_names_has_its_answer_and_a_new_one_plays_the_rules() -> None:
    """Guards the form: the list denies by default. The 38 moves cosmergon.com publishes a form
    for (``core/actions/formen.py``, 23.10.2026) each give a number, nothing, or ``None``."""
    state = _waiting()
    published = ["abandon_field", "accept_contract", "activate_voucher", "apply_to_contract",
                 "breach_contract", "burn_plague", "buy_intelligence", "buy_shield",
                 "cancel_mission", "claim_field", "create_cube", "create_field",
                 "decide_application", "evolve", "heal_holes", "list_intelligence", "market_buy",
                 "market_cancel", "market_list", "message", "pause", "pickup_drop", "place_cells",
                 "place_deployable", "propose_contract", "propose_counter",
                 "propose_from_template", "reject_contract", "remit_to_owner", "resume", "say",
                 "set_compass", "set_tournament_compass", "shoot_spore", "start_mission",
                 "terminal_query", "transfer_energy", "transfer_inventory"]  # fmt: skip
    assert len(published) == 38
    gives = {name: head.gives_away(name, {}, state) for name in published}
    assert gives["market_buy"] is None and gives["breach_contract"] is None
    assert gives["place_cells"] == 0.0 and gives["market_list"] == 0.0
    assert head.gives_away("a_move_from_next_year", {"amount": 0}, state) is None
    assert head.gives_away("start_mission", {"params": {"mission_type": "x",
                                                        "reward_energy": 50}}, state) is None


# --- 4: a name resolution holds no call past its deadline ----------------------------------------


def _hanging(seconds: float) -> Any:
    def resolve(*_: Any, **__: Any) -> Any:
        time.sleep(seconds)
        raise socket.gaierror("no answer")

    return resolve


def test_a_model_list_waits_for_a_name_no_longer_than_its_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sync way the bar takes (``advisor models``): the door resolves inside the deadline,
    and the loop the list ran in does not wait for the resolver when it closes."""
    monkeypatch.setattr(socket, "getaddrinfo", _hanging(3))
    get = functools.partial(connections.bounded_call, "GET", seconds=0.5)
    started = time.monotonic()
    said = connections.models_at("http://models.test:11434/v1", get)
    assert time.monotonic() - started < 2, "held past its deadline"
    assert said["ok"] is False


def test_the_name_httpx_resolves_when_it_connects_holds_no_call_either(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The second resolution, httpx's (``loop.getaddrinfo`` in the loop's executor)."""
    monkeypatch.setattr(connections, "on_this_machine", lambda *_, **__: False)
    monkeypatch.setattr(socket, "getaddrinfo", _hanging(3))
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        connections.bounded_call("GET", "https://models.test/v1/models", seconds=0.5)
    assert time.monotonic() - started < 2, "held past its deadline"


def test_the_worlds_peer_check_resolves_no_name_over_https(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[Any] = []
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: asked.append(a) or [])
    check = brain.world_peer_check({"world": {"base_url": "https://cosmergon.com"}})
    assert asked == [], "made without a name resolution"
    assert callable(check)


def test_every_loop_of_the_brain_is_made_by_run_loop() -> None:
    """Guards the form: no ``asyncio.run`` in the brain, so no loop waits for its threads."""
    for path in sorted((ROOT / "brain").glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Attribute) and node.attr == "run" and \
                    isinstance(node.value, ast.Name) and node.value.id == "asyncio":
                pytest.fail(f"{path.name}:{node.lineno} asyncio.run")


# --- claims of the README that had no test (cos20 #492, register of claims, threat model §6) ---


def test_an_agents_list_of_models_takes_its_version_and_its_listing_time_at_most() -> None:
    """README: "from your agent 1 MiB within 40 seconds (its version, then its list)"."""
    seen: list[dict[str, Any]] = []

    def run(argv: list[str], **kwargs: Any) -> Any:
        seen.append({"argv": list(argv), **kwargs})
        said = "1.4.0\n" if is_command(argv, connections.AGENTS["pi"]["version"]) else "p m\nx y\n"
        return SimpleNamespace(returncode=0, stdout=said, stderr="")

    assert connections.agent_models("pi", run=run) == ["x/y"]
    assert [s["timeout"] for s in seen][1:] == [30]
    assert 0 < seen[0]["timeout"] <= connections.VERSION_S  # finding it and its version share it
    assert connections.VERSION_S + 30 <= 40
    assert connections.AGENT_OUTPUT_BYTES == 1024 * 1024


def test_nothing_is_installed_or_sent_before_the_first_click() -> None:
    """Guards the form behind README "Nothing is installed and nothing is sent before that
    click": the wake script starts on the click or the shortcut (``wake``), and otherwise only
    where a Shikigon already lives here (``root.awake``); the brain runs only then."""
    source = (ROOT / "qml" / "Service.qml").read_text(encoding="utf-8")
    lines = source.splitlines()
    starts = [i for i, line in enumerate(lines) if "waker.running = true" in line]
    assert len(starts) == 3, starts
    for i in starts:
        before = "\n".join(lines[max(0, i - 6):i])
        assert "function wake(" in before or "root.awake" in before, lines[max(0, i - 6):i + 1]
    assert re.search(r"wantRunning:\s*awake\b", source)
