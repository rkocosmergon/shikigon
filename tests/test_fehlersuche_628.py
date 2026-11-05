"""Guards for the brain's findings of the fault search of 04.11.2026 (cos20 #628).

F1: with speech on, status.json lost what is left of the budget. F2: the run loop kept the model
it started with, so ``forget`` and a new choice did not reach it. F3: a word in ``slots`` broke the
move. F4: a child in its own session held the pipes and the move waited for it. F7: a question
could end without a line in the talk. F8: a form of the world with a number inside broke the move.
F9: a value in the config ended in a traceback, and ``review_every`` restarted the brain every 30
seconds. F10: without python3 the panel got an empty setup.json.

The tests go the ways the brain goes: ``tick`` and ``run`` with a world and a model that answer,
``main`` as a program with the words on stdin. On 54bfdf4 22 of these 39 were red. The controls
hold on both sides, except the shipped config's, which calls a function 54bfdf4 did not have. Six
others hold there too and stand beside what was red: three forms of F8, no ``[world]`` at all, and
a broken address in the panel's two commands.
"""

from __future__ import annotations

import asyncio
import http.server
import json
import os
import shutil
import subprocess
import sys
import threading
import time
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
import shikigon_brain as brain
import speech
from cosmergon_agent.action import ActionResult
from cosmergon_agent.state import GameState
from cosmergon_decider_tree import TreeDecider

CONF = {"base_url": "http://127.0.0.1:1", "model": "m", "learning_budget_pct": 5.0,
        "max_per_day": 0, "timeout_s": 1}  # fmt: skip
ACTIONS = {"propose_from_template": {"available": True}}
RICH = {
    "available_actions": {"create_field": {"available": True}, "set_compass": {"available": True}},
    "universe_cubes": [{"id": "c1", "name": "c"}],
}
GOOD_FORM = {"description": "d", "params": ["preset"], "required": ["preset"]}


@pytest.fixture(autouse=True)
def _a_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    brain.private_state()


def _world(**more: Any) -> GameState:
    data = {"tick": 7, "energy": 1000.0, "agent_id": "a", "agent_name": "probe", "fields": [],
            "ranking": {"rang": 1, "rang_von": 2, "gesamtenergie": 5000.0}}  # fmt: skip
    data.update(more)
    return GameState.from_api(data)


class _Agent:
    def __init__(self, state: GameState) -> None:
        self.state = state
        self.played: list[tuple[str, dict[str, Any]]] = []

    async def refresh_state(self) -> GameState:
        return self.state

    async def act(self, action: str, **params: Any) -> ActionResult:
        self.played.append((action, params))
        return ActionResult.from_response(action, 200, {"success": True, "result": {}}, "k")

    async def missions(self) -> list[Any]:
        return []


async def _no_model(messages: Any, tokens: int) -> str:
    raise OSError("no model")


def _says(text: str) -> Any:
    async def send(messages: Any, tokens: int) -> str:
        return text

    return send


def _head(send: Any = _no_model, conf: dict[str, Any] | None = None) -> head.HeadDecider:
    careful = contracts.Careful(TreeDecider())
    return head.HeadDecider(careful, conf or CONF, brain.state_dir(), send=send)


def _program(*args: str, words: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "brain" / "shikigon_brain.py"), *args],
        check=False, input=words, capture_output=True, text=True, timeout=60,
        env=os.environ.copy(),
    )  # fmt: skip


def _config(text: str) -> None:
    path = brain.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


# --- F1: what is left of the budget stands in status.json, also with speech on ------------------


def _left() -> Any:
    return json.loads((brain.state_dir() / "status.json").read_text()).get("learning_budget_left")


def test_f1_control_head_alone_names_what_is_left() -> None:
    asyncio.run(brain.tick(_Agent(_world()), _head()))
    assert _left() == 250


def test_f1_head_with_speech_names_what_is_left() -> None:
    decider = speech.TalkingDecider(_head(), CONF, brain.state_dir(), "k", send=_no_model)
    asyncio.run(brain.tick(_Agent(_world()), decider))
    assert _left() == 250


# --- F2: the run loop follows the player's choice -----------------------------------------------


class _Model:
    """An address that counts what reaches it and answers with a choice."""

    def __init__(self) -> None:
        model = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                model.asked += 1
                self.rfile.read(int(self.headers.get("content-length", 0)))
                answer = {"message": {"content": '{"choice":1,"why":"w"}'}}
                body = json.dumps({"choices": [answer]})
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body.encode())

            def log_message(self, *_: Any) -> None:
                pass

        self.asked = 0
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.address = f"http://127.0.0.1:{server.server_address[1]}/v1"

    def chosen(self) -> None:
        choice = {"via": "http", "base_url": self.address, "model": "m", "advise": 1, "every": 1}
        connections.choose(brain.state_dir(), choice)


class _TwoProposals:
    """Stands in for the rule tree: here only where the question goes counts."""

    async def decide(
        self, state: Any, blocked: frozenset[str] = frozenset()
    ) -> tuple[str, dict[str, Any]]:
        for move in ("create_field", "set_compass"):
            if move not in blocked:
                return move, {}
        return "wait", {}


class _End(Exception):
    pass


def _advised(model: _Model) -> tuple[dict[str, Any], Any]:
    model.chosen()
    cfg = {"world": {"base_url": "https://cosmergon.com"},
           "brain": {"decider": "advised", "advisor": {"speak": False}}}  # fmt: skip
    decider = brain.make_decider(cfg)
    decider.tree = _TwoProposals()
    return cfg, decider


def _two_moves(
    cfg: dict[str, Any], decider: Any, between: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real ``run``: two moves, the player's word between them. Each move asks the model."""
    moves: list[int] = []

    async def move(agent: Any, chosen: Any, done_in: Any, refusals: Any) -> dict[str, Any]:
        if hasattr(chosen, "tree"):
            chosen.tree = _TwoProposals()
        await chosen.decide(_world(**RICH))
        moves.append(1)
        return {"action": "wait", "ok": True}

    async def pause(_s: float) -> None:
        if len(moves) >= 2:
            raise _End
        between()

    monkeypatch.setattr(brain, "guarded_tick", move)
    monkeypatch.setattr(brain.asyncio, "sleep", pause)
    monkeypatch.setattr(brain, "world_refusal", lambda _cfg: "")
    with pytest.raises(_End):
        asyncio.run(brain.run(cfg, SimpleNamespace(state=_world(**RICH)), decider))


def test_f2_control_the_chosen_address_gets_the_move() -> None:
    model = _Model()
    _, decider = _advised(model)
    asyncio.run(decider.decide(_world(**RICH)))
    assert model.asked >= 1, decider.last


def test_f2_after_forget_the_old_address_gets_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    model = _Model()
    cfg, decider = _advised(model)
    before: list[int] = []

    def forget() -> None:
        before.append(model.asked)
        brain.advisor_command(cfg, "forget", [])

    _two_moves(cfg, decider, forget, monkeypatch)
    assert before and before[0] >= 1, "control: the first move asked the model"
    assert model.asked == before[0]


def test_f2_after_a_new_choice_only_the_new_address_gets_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old, new = _Model(), _Model()
    cfg, decider = _advised(old)
    before: list[int] = []

    def choose() -> None:
        before.append(old.asked)
        new.chosen()

    _two_moves(cfg, decider, choose, monkeypatch)
    assert old.asked == before[0] and new.asked >= 1, (old.asked, before, new.asked)


# --- F3: a word in slots is no broken move ------------------------------------------------------


def _speaking_head(answer: str) -> speech.TalkingDecider:
    conf = {**CONF, "learning_budget_pct": 0}
    inner = _head(_says(answer), conf)
    form = {"description": "d", "params": ["template_id", "slots"], "required": ["template_id"]}
    inner.forms = head.forms_of({"actions": {"propose_from_template": {"forms": [form]}}})
    return speech.TalkingDecider(inner, conf, brain.state_dir(), "k", send=_no_model)


def _offer(slots: Any) -> str:
    return json.dumps({"action": "propose_from_template", "why": "w",
                       "params": {"slots": slots, "template_id": "T01"}})  # fmt: skip


def test_f3_control_slots_as_an_object_play() -> None:
    agent = _Agent(_world(available_actions=ACTIONS))
    asyncio.run(brain.tick(agent, _speaking_head(_offer({"partner_id": "x"}))))
    assert agent.played and agent.played[0][0] == "propose_from_template", agent.played


def test_f3_slots_as_a_word_do_not_break_the_move() -> None:
    asyncio.run(brain.tick(_Agent(_world(available_actions=ACTIONS)), _speaking_head(_offer("x"))))


# --- F4: a move does not wait for a child in its own session ------------------------------------


def _run_program(tmp_path: Path, body: str, monkeypatch: pytest.MonkeyPatch) -> tuple[float, Any]:
    guard = subprocess.run(
        [sys.executable, "-I", "-c", connections.GUARD, str(os.getpid()), "443", "/bin/true"],
        check=False, capture_output=True, text=True,
    )  # fmt: skip
    if "Landlock" in guard.stderr:  # a kernel without Landlock ABI 4 cannot start the guard
        monkeypatch.setattr(connections, "guarded", lambda argv, env: argv)
    program = tmp_path / "agent"
    program.write_text("#!/bin/sh\n" + body + "\n")
    program.chmod(0o755)
    start = time.monotonic()
    done = connections.run_in_group([str(program.resolve())], input="words", timeout=5.0)
    return time.monotonic() - start, done


def test_f4_control_a_plain_program_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    took, done = _run_program(tmp_path, "echo hello", monkeypatch)
    assert done.stdout.strip() == "hello"
    assert took < 5


def test_f4_a_child_in_its_own_session_does_not_hold_the_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    took, done = _run_program(tmp_path, "setsid sleep 25 &\necho hello", monkeypatch)
    assert done.stdout.strip() == "hello"
    assert took < 10, f"{took:.1f} s"


# --- F7: a question that cannot be answered leaves a line ---------------------------------------


def test_f7_control_an_answer_stands_in_the_talk() -> None:
    conf = {"world": {"base_url": "https://cosmergon.com"}, "brain": {"advisor": CONF}}
    asyncio.run(brain.ask(_Agent(_world()), conf, "question", rules="", send=_says("answer")))
    assert brain.chat_tail(1)


def test_f7_a_silent_world_leaves_a_line() -> None:
    files.write_json(brain.state_dir() / "credentials.json", {"api_key": "k", "agent_id": "a"})
    _config('[world]\nbase_url = "http://127.0.0.1:1"\n'
            '[brain.advisor]\nbase_url = "http://127.0.0.1:1"\nmodel = "m"\n')  # fmt: skip
    done = _program("ask", words="question\n")
    assert files.tail(brain.chat_path(), 1), done.stdout[:200]


# --- F8: a form of the world that is none lets the rules play -----------------------------------


def _with_forms(form: dict[str, Any]) -> head.HeadDecider:
    decider = _head(conf={**CONF, "learning_budget_pct": 0})
    decider.forms = head.forms_of({"actions": {"set_compass": {"forms": [form]}}})
    return decider


def test_f8_control_good_forms() -> None:
    decider = _with_forms(GOOD_FORM)
    assert decider.forms
    assert asyncio.run(decider.decide(_world(**RICH)))


@pytest.mark.parametrize("bad", [{"required": 5}, {"params": 5}, {"types": 5},
                                 {"description": {"x": 1}}, {"required": [{"a": 1}]}])  # fmt: skip
def test_f8_forms_that_are_none_let_the_rules_play(bad: dict[str, Any]) -> None:
    assert asyncio.run(_with_forms({**GOOD_FORM, **bad}).decide(_world(**RICH)))


# --- F9: a config it cannot follow is a sentence that names the value ---------------------------


@pytest.mark.parametrize(("text", "name"), [
    ('[brain]\ninterval_s = "x"\n', "brain.interval_s"),
    ('[brain.advisor]\nevery = "x"\n', "brain.advisor.every"),
    ('[brain.advisor]\ntimeout_s = "x"\n', "brain.advisor.timeout_s"),
    ('[brain.advisor]\nmax_per_day = "x"\n', "brain.advisor.max_per_day"),
    ('[brain.advisor]\nhead_max_per_day = "x"\n', "brain.advisor.head_max_per_day"),
    ('[brain.advisor]\nreview_every = "often"\n', "brain.advisor.review_every"),
    ('[brain.advisor]\nlearning_budget_pct = "x"\n', "brain.advisor.learning_budget_pct"),
    ('[brain.advisor]\nreview_every = inf\n', "brain.advisor.review_every"),
    ("brain = 5\n", "brain"),
])  # fmt: skip
def test_f9_the_sentence_names_the_value(text: str, name: str) -> None:
    _config(text)
    done = _program("status")
    assert name in done.stdout and "Traceback" not in done.stderr, (done.stdout, done.stderr[-300:])
    assert done.returncode == brain.CONFIG_EXIT


@pytest.mark.parametrize("text", ["[decider]\n", "[world\n", '[world]\nbase_url = "http://["\n'],
                         ids=["no_world", "broken_toml", "broken_address"])  # fmt: skip
def test_f9_a_broken_config_is_no_traceback(text: str) -> None:
    _config(text)
    done = _program("tick")
    assert "Traceback" not in done.stderr, done.stderr[-800:]


def test_f9_control_the_shipped_config_is_followed() -> None:
    _config(brain.REPO_DEFAULT_CONFIG.read_text())
    assert brain.config_refusal(brain.load_config()) == ""


def test_f9_control_a_plain_number_is_a_choice() -> None:
    assert json.loads(_program("advisor", "advise", "3").stdout)["ok"] is True


def test_f9_a_superscript_two_is_no_number() -> None:
    done = _program("advisor", "advise", "²")
    assert "Traceback" not in done.stderr, done.stderr[-800:]


@pytest.mark.parametrize("command", ["models", "try"])
def test_f9_a_broken_address_answers_for_the_panel(command: str) -> None:
    done = _program("advisor", command, words="http://[\n")
    assert done.stdout.strip(), done.stderr[-500:]
    json.loads(done.stdout)


def test_f9_control_a_good_address_answers_for_the_panel() -> None:
    done = _program("advisor", "models", words="http://127.0.0.1:1\n")
    assert json.loads(done.stdout), done.stderr[-500:]


# --- F10: without python3 the panel still gets a sentence ---------------------------------------


def test_f10_without_python3_setup_json_holds_a_sentence(tmp_path: Path) -> None:
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("dirname", "readlink", "mkdir", "chmod", "mv", "sha256sum", "cut", "cat", "rm"):
        found = shutil.which(name)
        assert found, name
        (tools / name).symlink_to(found)
    env = {**os.environ, "PATH": str(tools)}
    bash = shutil.which("bash")
    assert bash
    subprocess.run([bash, str(ROOT / "brain" / "shikigon-wake")], env=env, capture_output=True,
                   text=True, timeout=30, check=False)  # fmt: skip
    said = json.loads((brain.state_dir() / "setup.json").read_text())
    assert said["stage"] == "failed" and "python3" in said["said"], said


# --- A look at what the panel reads: facts of an odd world do not break ``ask`` -----------------


@pytest.mark.parametrize("more", [
    {"next_step": {"x": 1}},
    {"key_expiry": {"at": 5}},
    {"key_expiry": {"at": "yesterday", "renews_on_use": "x"}},
])  # fmt: skip
def test_control_facts_of_an_odd_world(more: dict[str, Any]) -> None:
    assert advisor.facts(_world(**more))
