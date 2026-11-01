"""Guards for the seven findings of the fault search of 24.10.2026 (cos20 #566).

Each test goes the way the brain goes: ``register --new``, ``tick``, ``ask``, ``advise``,
``connections.run_agent``. The world, the model and the agent are stubs at the seams the brain
has for them. Each was red on 0.1.13, twice in a row, before the fix.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import stat
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
BRAIN = ROOT / "brain"
sys.path.insert(0, str(BRAIN))

import advisor
import connections
import files
import head
import lessons
import shikigon_brain
import speech

ADDRESS = {"base_url": "https://modell.example/v1", "model": "m"}
HEAD = {"via": "http", **ADDRESS, "learning_budget_pct": 5}
REFUSAL = "the world says no to this move right now, " + "x" * 150


class Registry:
    """The world at registration: every registration gets a new agent."""

    def __init__(self) -> None:
        self.born = 0

    def post(self, url: str, json: object = None, timeout: float = 30) -> SimpleNamespace:
        self.born += 1
        body = {"agent_id": f"agent-{self.born}", "api_key": f"key-{self.born}",
                "expires_at": "2026-10-25T12:00:00+00:00"}
        return SimpleNamespace(status_code=201, json=lambda: body)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(connections, "bounded_post", Registry().post)
    return shikigon_brain.private_state()


def state(agent_id: str = "agent-1", tick: int = 500, energy: float = 900_000.0,
          total: float = 1_000_000.0,
          actions: tuple[str, ...] = ("transfer_energy", "create_field")) -> SimpleNamespace:
    return SimpleNamespace(
        agent_id=agent_id, tick=tick, energy=energy, fields=[], agent_name="Kopf",
        persona_type="trader", compass_preset=None, world_briefing=None, next_step=None,
        key_expiry=None, tournament=None, showcase=None,
        ranking=SimpleNamespace(gesamtenergie=total, ziel="", rang=0, rang_von=0,
                                champion_rang=0),
        available_actions={name: {} for name in actions},
        pending_contracts=(), active_contracts=(),
    )


def no_state() -> object:
    return SimpleNamespace(refresh_state=lambda: asyncio.sleep(0, result=None))


def agent_id(home: Path) -> str:
    return str(json.loads((home / "credentials.json").read_text())["agent_id"])


def ask_config() -> dict[str, Any]:
    cfg = shikigon_brain.load_config()
    cfg.setdefault("brain", {})["advisor"] = dict(ADDRESS)
    return cfg


def answering(text: str, told: list[list[dict[str, str]]] | None = None) -> Any:
    async def send(messages: list[dict[str, str]], max_tokens: int) -> str:
        if told is not None:
            told.append(messages)
        return text
    return send


async def forms() -> dict[str, list[dict[str, Any]]]:
    return {"transfer_energy": [{"params": ["to_player_id", "amount"],
                                 "required": ["to_player_id", "amount"]}]}


class Waiting:
    async def decide(self, state: Any, blocked: frozenset[str] = frozenset()) -> Any:
        return "wait", {}


class FieldFirst:
    async def decide(self, state: Any, blocked: frozenset[str] = frozenset()) -> Any:
        return ("wait", {}) if "create_field" in blocked else ("create_field", {})


class World:
    """Refuses every move, or takes ``create_field`` and books ``field_cost`` for it."""

    def __init__(self, state: Any, field_cost: float | None = None) -> None:
        self.state = state
        self.field_cost = field_cost
        self.played: list[str] = []

    async def refresh_state(self) -> Any:
        return self.state

    async def act(self, action: str, **params: Any) -> Any:
        self.played.append(action)
        if action == "create_field" and self.field_cost is not None:
            return SimpleNamespace(success=True, error_message="", data={
                "energy_effect": {"free": -self.field_cost, "held": 0.0, "items": 0.0}})
        return SimpleNamespace(success=False, error_message=REFUSAL, data={})


# F1: wake a new one


OLD = "OLD-SHIKIGON: sending energy paid off, missions did not."


def test_f1_a_new_shikigon_is_not_told_the_old_ones_lessons(home: Path) -> None:
    assert shikigon_brain.main(["register"]) == 0
    old = agent_id(home)
    lessons.keep(home, {"at": 1, "tick": 100, "model": "m", "from_moves": 100,
                        "lessons": OLD, "avoid": OLD, "double_down": OLD})
    assert shikigon_brain.main(["register", "--new"]) == 0
    assert agent_id(home) != old
    told: list[list[dict[str, str]]] = []
    turn = asyncio.run(shikigon_brain.ask(no_state(), ask_config(), "What did you learn?",
                                          rules="", send=answering("Answer.", told)))
    assert turn["ok"], turn
    assert "OLD-SHIKIGON" not in "\n".join(m["content"] for m in told[0])


def test_f1_a_new_shikigon_measures_its_budget_against_its_own_total(home: Path) -> None:
    assert shikigon_brain.main(["register"]) == 0
    assert head.budget_left(home, HEAD, 1_000_000.0) == 50_000.0  # the old one's day
    assert shikigon_brain.main(["register", "--new"]) == 0
    move = json.dumps({"action": "transfer_energy",
                       "params": {"to_player_id": "fremd", "amount": 40_000}, "why": "x"})
    decider = head.HeadDecider(Waiting(), HEAD, home, send=answering(move), forms=forms)
    new = state(agent_id(home), energy=100_000.0, total=100_000.0, actions=("transfer_energy",))
    action, params = asyncio.run(decider.decide(new))
    assert decider.budget_left == 5_000.0, decider.budget_left
    assert action == "wait", (action, params, decider.last)


def test_f1_the_new_heads_last_moves_are_its_own(home: Path) -> None:
    assert shikigon_brain.main(["register"]) == 0
    for tick in range(100, 106):
        shikigon_brain.journal({"at": 1, "tick": tick, "energy": 5e6, "fields": 9,
                                "action": "transfer_energy", "ok": True})
    assert shikigon_brain.main(["register", "--new"]) == 0
    told: list[list[dict[str, str]]] = []
    wait = json.dumps({"action": "wait", "params": {}, "why": "x"})
    decider = head.HeadDecider(Waiting(), {"via": "http", **ADDRESS}, home,
                               send=answering(wait, told))
    asyncio.run(decider.decide(state(agent_id(home), tick=900, actions=("create_field",))))
    assert "tick 105" not in "\n".join(m["content"] for m in told[0])  # the head appends


def test_f1_the_new_one_does_not_play_the_old_ones_announced_proposal(home: Path) -> None:
    assert shikigon_brain.main(["register"]) == 0
    files.write_json(speech.memo_path(home), {
        "moves": 40, "talked": 40, "posted": 0, "answered": [],
        "then": ["propose_contract", {"to_player_id": "partner-of-the-old-one",
                                      "contract_type": "alliance_public"}],
        "then_at": time.time()})
    assert shikigon_brain.main(["register", "--new"]) == 0
    talking = speech.TalkingDecider(Waiting(), {"via": "http", **ADDRESS}, home, key="key-2")
    action, params = asyncio.run(talking.decide(
        state(agent_id(home), tick=900, actions=("create_field",))))
    assert action != "propose_contract", (action, params)


AGENT_FILES = {"journal.jsonl", "journal_recent.json", "lessons.json", "review", "budget.json",
               "speech.json", "tournament_seen.json", "status.json", "refusals.json"}
PLAYER_FILES = {"credentials.json", "advisor.json", "advisor_key", "advisor_count.json",
                "chat.jsonl", "talk.json"}
NOT_IN_STATE = {"moves.txt", "rated.txt", "world.txt",  # inside review/
                "advisor_notes.md",  # beside the brain
                "coach.md"}  # removed since 0.1.x
NAME = re.compile(r'"([A-Za-z_.-]+\.(?:json|jsonl|txt|md)|advisor_key|review)"')


def test_f1_every_file_of_the_brain_is_the_agents_or_the_players(home: Path) -> None:
    """A file the brain starts to write must be sorted: the agent's goes with ``register --new``,
    the player's stays. Unsorted, it would go on to the next Shikigon unseen (F1)."""
    named = {name for path in BRAIN.glob("*.py") for name in NAME.findall(path.read_text())}
    assert named == AGENT_FILES | PLAYER_FILES | NOT_IN_STATE
    assert {path.name for path in shikigon_brain.agent_paths(home)} == AGENT_FILES


# F2, F6, F7: the head


def test_f2_the_rules_move_is_not_counted_against_the_heads_budget(home: Path) -> None:
    answers = iter([json.dumps({"action": "transfer_energy",
                                "params": {"to_player_id": "ally", "amount": 1_000},
                                "why": "x"}),
                    "I do not know", "I do not know"])

    async def model(messages: list[dict[str, str]], max_tokens: int) -> str:
        return next(answers)

    world = World(state(), field_cost=40_000.0)
    decider = head.HeadDecider(FieldFirst(), HEAD, home, send=model, forms=forms)
    last = asyncio.run(shikigon_brain.tick(world, decider, refusals=shikigon_brain.Refusals()))
    assert world.played == ["transfer_energy", "create_field"], world.played
    assert last["action"] == "create_field" and last["ok"], last
    assert json.loads((home / head.BUDGET_FILE).read_text())["spent"] == 0.0


def test_f6_the_refused_rules_move_does_not_go_out_twice(home: Path) -> None:
    answers = iter([json.dumps({"action": "create_field", "params": {}, "why": "the rules"}),
                    json.dumps({"action": "transfer_energy",  # beyond the budget of 50,000
                                "params": {"to_player_id": "ally", "amount": 900_000},
                                "why": "x"})])

    async def model(messages: list[dict[str, str]], max_tokens: int) -> str:
        return next(answers)

    world = World(state())
    decider = head.HeadDecider(FieldFirst(), HEAD, home, send=model, forms=forms)
    last = asyncio.run(shikigon_brain.tick(world, decider, refusals=shikigon_brain.Refusals()))
    assert world.played.count("create_field") == 1, (world.played, last.get("advice"))


def test_f7_the_head_is_told_its_last_five_moves(home: Path) -> None:
    move = json.dumps({"action": "transfer_energy",
                       "params": {"to_player_id": "ally", "amount": 1}, "why": "x"})
    for tick in range(100, 110):  # ten moves, each with three refused tries
        decider = head.HeadDecider(FieldFirst(), HEAD, home, send=answering(move), forms=forms)
        asyncio.run(shikigon_brain.tick(World(state(tick=tick)), decider,
                                        refusals=shikigon_brain.Refusals()))
    assert len(head.recent_moves(home)) == head.RECENT


# F4: the limit while its agent has taken over


def test_f4_the_brain_reads_the_advisor_settings_in_one_place() -> None:
    """``advisor_settings`` sets the limit of the head mode; a caller of ``advisor.settings``
    beside it would cut a call at ``max_per_day`` again (F4)."""
    calls = [path.name for path in BRAIN.glob("*.py")
             for _ in re.finditer(r"\badvisor\.settings\(", path.read_text())]
    assert calls == ["shikigon_brain.py"], calls


def test_f4_a_question_in_head_mode_follows_the_head_limit(home: Path) -> None:
    connections.choose(home, {"via": "http", **ADDRESS, "advise": "head"})
    cfg = shikigon_brain.load_config()  # shipped: max_per_day 500, head_max_per_day 0
    for _ in range(500):
        advisor.count_call(home)
    now = shikigon_brain.advisor_now(cfg)
    assert now["advise"] == "head" and now["limit"] == 0
    turn = asyncio.run(shikigon_brain.ask(no_state(), cfg, "How is it going?", rules="",
                                          send=answering("Answer.")))
    assert turn["ok"], turn


def test_f4_a_dry_run_in_head_mode_follows_the_head_limit(home: Path) -> None:
    connections.choose(home, {"via": "http", **ADDRESS, "advise": "head"})
    cfg = shikigon_brain.load_config()
    for _ in range(500):
        advisor.count_call(home)
    decider = shikigon_brain.make_decider(cfg, advised=True)
    decider.send = lambda messages, max_tokens: asyncio.sleep(0, result='{"choice": 1}')

    class TwoMoves:
        async def decide(self, state: Any, blocked: frozenset[str] = frozenset()) -> Any:
            for move in ("create_field", "evolve"):
                if move not in blocked:
                    return move, {}
            return "wait", {}

    decider.tree = TwoMoves()
    asyncio.run(decider.decide(state()))
    assert (decider.last or {}).get("fallback") != "daily limit reached", decider.last


def test_f4_the_ordinary_limit_still_holds_when_its_agent_only_advises(home: Path) -> None:
    """Control: outside the head mode, ``max_per_day`` still ends the day's calls."""
    connections.choose(home, {"via": "http", **ADDRESS})
    cfg = ask_config()
    for _ in range(int(cfg["brain"].get("max_per_day", 500))):
        advisor.count_call(home)
    turn = asyncio.run(shikigon_brain.ask(no_state(), cfg, "How is it going?", rules="",
                                          send=answering("Answer.")))
    assert not turn["ok"], turn


# F5: a long answer


@pytest.mark.parametrize("length", [10_000, 60_000])
def test_f5_a_long_answer_still_reaches_the_bar(home: Path, length: int) -> None:
    cfg = ask_config()
    assert asyncio.run(shikigon_brain.ask(no_state(), cfg, "First?", rules="",
                                          send=answering("Short.")))["ok"]
    assert asyncio.run(shikigon_brain.ask(no_state(), cfg, "Second?", rules="",
                                          send=answering("y" * length)))["ok"]
    shown = json.loads((home / shikigon_brain.BAR_TALK).read_text())
    assert [turn.get("q") for turn in shown] == ["First?", "Second?"]
    assert len(shown[-1]["a"]) == shikigon_brain.ANSWER_CHARS


def test_f5_a_long_answer_keeps_the_talk_for_the_next_question(home: Path) -> None:
    cfg = ask_config()
    told: list[list[dict[str, str]]] = []
    for question, answer in (("First?", "One."), ("Second?", "y" * 20_000), ("Third?", "3.")):
        asyncio.run(shikigon_brain.ask(no_state(), cfg, question, rules="",
                                       send=answering(answer, told)))
    earlier = [m["content"] for m in told[-1] if m["role"] == "user"][:-1]
    assert earlier == ["First?", "Second?"], earlier


# F3: what the guard keeps out, as the README says it


FAKE_PI = """#!{python}
import socket, sys
if "--version" in sys.argv:
    print("1.4.0")
    sys.exit(0)
sys.stdin.read()
socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b"name?", ("127.0.0.1", {udp_port}))
try:
    socket.create_connection(("127.0.0.1", {tcp_port}), timeout=2).close()
    print("tcp-through")
except OSError:
    print("tcp-refused")
"""


def test_f3_the_guard_refuses_tcp_but_443_and_leaves_udp_open_as_the_readme_says(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
    assert "UDP stays open" in readme  # the README promises no more than the guard keeps

    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.bind(("127.0.0.1", 0))
    udp.settimeout(3)
    tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp.bind(("127.0.0.1", 0))
    tcp.listen()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    pi = bin_dir / "pi"
    pi.write_text(FAKE_PI.format(python=sys.executable, udp_port=udp.getsockname()[1],
                                 tcp_port=tcp.getsockname()[1]))
    pi.chmod(pi.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    try:
        answer = connections.run_agent({"agent": "pi", "agent_model": ""}, "Question", 20)
    except connections.AgentError as exc:
        if "Landlock" in str(exc):
            pytest.skip(str(exc))
        raise
    assert "tcp-refused" in answer, answer
    assert udp.recvfrom(1024)[0] == b"name?"  # if this turns, the README can say more
