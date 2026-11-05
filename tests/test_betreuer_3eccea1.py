"""Guards for the betreuer pass on 3eccea1 (cos20 #492).

B2: a look back kept sending to a model the player had forgotten or replaced. B3, B4: what the
world sent could break the head's situation, and then not even the rules played. B5: a config
without ``[world]`` ended in a traceback, and a value of the wrong kind in no sentence that names
it. B6: ``status`` did not count an unreadable ``status.json`` as none. B7: one lone surrogate in
foreign text let a question to an agent wait for its whole time, and ended a look back.

B1 is the SDK's: ``sdk-tests/test_steuerzeichen_492.py`` in cos20.

On 3eccea1 34 of these 41 were red. The seven that hold there are the controls; the address's own
control is red there only because it calls a function 3eccea1 did not have. A surrogate in the
world's rules holds on both sides: ``json.dumps`` writes it as an escape, so of the review folder
only the words that do not pass through it could end a look back.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "brain"))

import advisor
import connections
import contracts
import files
import head
import lessons
import shikigon_brain as brain
from cosmergon_agent.action import ActionResult
from cosmergon_agent.state import GameState
from cosmergon_decider_tree import TreeDecider
from test_head import Replies
from test_lessons import GOOD, _moves, _write

CONF = {"base_url": "https://model.test/v1", "model": "first", "learning_budget_pct": 0,
        "reasoning_effort": "none"}  # fmt: skip
RICH = {
    "available_actions": {"create_field": {"available": True}, "set_compass": {"available": True}},
    "universe_cubes": [{"id": "c1", "name": "c"}],
}
FORM = {"description": "d", "params": ["preset"], "required": ["preset"]}
HOME_WORLD = "https://cosmergon.com"
SURROGATE = "a\ud83db"


@pytest.fixture(autouse=True)
def _a_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    brain.private_state()


def _world(**more: Any) -> GameState:
    data = {"tick": 7, "energy": 1000.0, "agent_id": "a", "agent_name": "probe", "fields": [],
            "ranking": {"rang": 1, "rang_von": 2, "gesamtenergie": 5000.0}, **RICH}  # fmt: skip
    data.update(more)
    return GameState.from_api(data)


class _Agent:
    def __init__(self, state: GameState) -> None:
        self.state = state
        self.played: list[str] = []

    async def refresh_state(self) -> GameState:
        return self.state

    async def act(self, action: str, **params: Any) -> ActionResult:
        self.played.append(action)
        return ActionResult.from_response(action, 200, {"success": True, "result": {}}, "k")

    async def missions(self) -> list[Any]:
        return []


async def _no_model(messages: Any, tokens: int) -> str:
    raise OSError("no model")


def _head(forms: dict[str, Any] | None = None) -> head.HeadDecider:
    careful = contracts.Careful(TreeDecider())
    decider = head.HeadDecider(careful, CONF, brain.state_dir(), send=_no_model)
    if forms:
        decider.forms = head.forms_of({"actions": {"set_compass": {"forms": [forms]}}})
    return decider


def _config(text: str) -> None:
    path = brain.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _program(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "brain" / "shikigon_brain.py"), *args],
        check=False, capture_output=True, text=True, timeout=60, env=os.environ.copy(),
    )  # fmt: skip


# --- B3, B4: what the world sends may cost the head its move, never the move --------------------


def test_b3_control_a_good_form_goes_to_the_head() -> None:
    decider = _head(FORM)
    assert asyncio.run(decider.decide(_world()))
    assert "chars" in decider.last, "control: the situation was written"


@pytest.mark.parametrize("params", [[[1]], [{"a": 1}]], ids=["list", "object"])
def test_b3_a_form_with_something_unhashable_in_params_lets_the_rules_play(params: Any) -> None:
    decider = _head({**FORM, "params": params})
    agent = _Agent(_world())
    asyncio.run(brain.tick(agent, decider))
    assert decider.last["fallback"].startswith("its situation could not be written")
    assert decider.last["rules"] in agent.played or decider.last["rules"] == "wait"


def test_b4_a_contract_type_that_is_a_list_lets_the_rules_play() -> None:
    decider = _head()
    agent = _Agent(_world(contracts={"active": [{"id": "c", "contract_type": ["x"]}]}))
    asyncio.run(brain.tick(agent, decider))
    assert decider.last["fallback"].startswith("its situation could not be written")


def test_b4_control_a_contract_with_a_word_goes_to_the_head() -> None:
    decider = _head()
    active = [{"id": "c", "contract_type": "custom"}]
    assert asyncio.run(decider.decide(_world(contracts={"active": active})))
    assert "chars" in decider.last


# --- B2: a look back holds to the model the player picked --------------------------------------


def _looking_back(changes: Any = None, *replies: str) -> tuple[dict[str, Any], Any]:
    """A head that has moves to look back on, and a model that changes the choice while it
    answers the first time."""
    cfg = {"world": {"base_url": "https://world.test"},
           "brain": {"advisor": {**CONF, "advise": "head", "every": 1}}}  # fmt: skip
    _write(_moves(100))
    model = Replies(*(replies or ("READ moves.txt 1-1", GOOD)))

    async def send(messages: list[dict[str, str]], tokens: int) -> str:
        said = await model(messages, tokens)
        if changes is not None:
            changes()
        return said

    send.asked = model.asked  # type: ignore[attr-defined]
    return cfg, send


def _info(url: str, **_: Any) -> Any:
    body = {"actions": {"place_cells": {"description": "d"}}}
    return SimpleNamespace(json=lambda: body, raise_for_status=lambda: None, status_code=200)


def _state() -> GameState:
    return _world()


async def test_b2_control_it_reads_and_learns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(connections, "bounded_get", _info)
    cfg, send = _looking_back()
    entry = await brain.learn(cfg, _state(), force=True, send=send)
    assert entry is not None and entry["ok"] and len(send.asked) == 2, entry


async def test_b2_after_a_new_choice_no_further_round_goes_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(connections, "bounded_get", _info)
    home = brain.state_dir()
    cfg, send = _looking_back(lambda: connections.choose(home, {"model": "second"}))
    entry = await brain.learn(cfg, _state(), force=True, send=send)
    assert len(send.asked) == 1, "the first round went out before the choice, nothing after it"
    assert entry is not None and not entry["ok"] and entry["why"]


async def test_b2_after_forget_no_further_round_goes_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(connections, "bounded_get", _info)
    home = brain.state_dir()
    connections.choose(home, {"model": "picked"})
    cfg, send = _looking_back(lambda: brain.advisor_command(cfg, "forget", []))
    await brain.learn(cfg, _state(), force=True, send=send)
    assert len(send.asked) == 1


async def test_b2_a_choice_made_while_it_gathers_stops_the_first_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = brain.state_dir()

    def gathers(url: str, **_: Any) -> Any:
        connections.choose(home, {"model": "second"})
        return _info(url)

    monkeypatch.setattr(connections, "bounded_get", gathers)
    cfg, send = _looking_back()
    await brain.learn(cfg, _state(), force=True, send=send)
    assert send.asked == []


# --- B5: one way to read the world's address, and each value of its kind ------------------------


NO_WORLD = [{}, {"world": {}}, {"world": {"base_url": ""}}]
IDS = ["no_section", "no_address", "empty_address"]


@pytest.mark.parametrize("cfg", NO_WORLD, ids=IDS)
def test_b5_no_address_means_the_home_world(cfg: dict[str, Any]) -> None:
    assert brain.world_url(cfg) == HOME_WORLD


def test_b5_control_the_address_given_holds() -> None:
    assert brain.world_url({"world": {"base_url": "https://world.test"}}) == "https://world.test"


@pytest.mark.parametrize("cfg", NO_WORLD, ids=IDS)
def test_b5_the_client_starts_without_an_address(cfg: dict[str, Any]) -> None:
    """With a key: without one ``make_agent`` ends earlier, and the guard would hold on the old
    code (``no_world`` in #628 F9 did)."""
    files.write_json(brain.credentials_path(), {"api_key": "k", "agent_id": "a"})
    assert brain.make_agent(cfg) is not None


@pytest.mark.parametrize("cfg", NO_WORLD, ids=IDS)
def test_b5_register_forget_and_the_rules_ask_the_home_world(cfg: dict[str, Any]) -> None:
    asked: list[str] = []

    def call(url: str, **_: Any) -> Any:
        asked.append(url)
        raise OSError("no world here")

    with contextlib.suppress(SystemExit):
        brain.register(cfg, call)
    brain.world_rules(cfg, call)
    files.write_json(brain.credentials_path(), {"api_key": "k", "agent_id": "a"})
    brain.forget(cfg, call)
    assert len(asked) == 3 and all(url.startswith(f"{HOME_WORLD}/") for url in asked), asked


def test_b5_the_address_is_read_in_one_place() -> None:
    source = (ROOT / "brain" / "shikigon_brain.py").read_text(encoding="utf-8")
    assert 'cfg["world"]' not in source
    assert source.count('cfg.get("world"') == 1


@pytest.mark.parametrize(("text", "name"), [
    ('[world]\nbase_url = 5\n', "world.base_url"),
    ('[world]\nbase_url = ["https://cosmergon.com"]\n', "world.base_url"),
    ('[world]\nbase_url = 2026-11-05\n', "world.base_url"),
    ('[character]\nname = 5\n', "character.name"),
    ('[brain]\ndecider = ["tree"]\n', "brain.decider"),
    ('[brain.advisor]\nmodel = true\n', "brain.advisor.model"),
    ('[brain.advisor]\nspeak = "yes"\n', "brain.advisor.speak"),
    ('[brain.advisor]\nspeak = 1\n', "brain.advisor.speak"),
    ('[instructions]\nattack = 5\n', "instructions.attack"),
    ('[instructions]\nattack = "attack"\n', "instructions.attack"),
])  # fmt: skip
def test_b5_a_value_of_another_kind_is_a_sentence_that_names_it(text: str, name: str) -> None:
    _config(text)
    done = _program("status")
    assert name in done.stdout and "Traceback" not in done.stderr, (done.stdout, done.stderr[-300:])
    assert done.returncode == 1


def test_b5_control_values_of_their_kind_are_followed() -> None:
    cfg = {"world": {"base_url": "https://world.test"}, "character": {"name": "x"},
           "brain": {"decider": "tree", "interval_s": 30.5,
                     "advisor": {"model": "m", "speak": False, "every": 3}},
           "instructions": {"attack": ["a"]}}  # fmt: skip
    assert brain.config_refusal(cfg) == ""


# --- B6: a status.json it cannot read counts as none ---------------------------------------------


@pytest.mark.parametrize("data", [b"\xff\xfe", b"", b"{broken", b"[1]"],
                         ids=["no_utf8", "empty", "broken", "a_list"])  # fmt: skip
def test_b6_an_unreadable_status_counts_as_none(data: bytes) -> None:
    (brain.state_dir() / "status.json").write_bytes(data)
    done = _program("status")
    assert "Traceback" not in done.stderr, done.stderr[-300:]
    assert json.loads(done.stdout) == {"mood": brain.ASLEEP}


def test_b6_control_a_good_status_is_told() -> None:
    (brain.state_dir() / "status.json").write_text('{"mood": "awake", "tick": 7}')
    assert json.loads(_program("status").stdout) == {"mood": "awake", "tick": 7}


# --- B7: a lone surrogate in foreign text is no reason to wait or to fail ------------------------


CAT = os.path.realpath(shutil.which("cat") or "/bin/cat")


def test_b7_control_a_question_reaches_the_agent() -> None:
    done = connections.run_in_group([CAT], input="plain", timeout=10)
    assert done.stdout == "plain"


def test_b7_a_question_with_a_surrogate_does_not_wait_for_the_whole_time() -> None:
    done = connections.run_in_group([CAT], input=SURROGATE, timeout=5)
    assert done.stdout.startswith("a") and done.stdout.endswith("b")


def test_b7_foreign_text_leaves_the_brain_writable() -> None:
    for said in (advisor.unmarked(SURROGATE), advisor.quoted(SURROGATE, 40),
                 advisor.word(SURROGATE)):  # fmt: skip
        said.encode("utf-8")


def test_b7_the_review_folder_takes_a_surrogate_in_every_file() -> None:
    folder = lessons.review_folder(brain.state_dir(), [], SURROGATE, SURROGATE, SURROGATE)
    for name in ("rated.txt", "world.txt", "ledger.txt"):
        assert (folder / name).read_text(encoding="utf-8").startswith("a")


async def test_b7_control_a_look_back_lives_through_a_surrogate_in_the_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def surrogate(url: str, **_: Any) -> Any:
        body = {"actions": {"place_cells": {"description": SURROGATE}}}
        return SimpleNamespace(json=lambda: body, raise_for_status=lambda: None, status_code=200)

    monkeypatch.setattr(connections, "bounded_get", surrogate)
    cfg, send = _looking_back()
    entry = await brain.learn(cfg, _state(), force=True, send=send)
    assert entry is not None and entry["ok"], entry
