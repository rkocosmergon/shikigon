"""Guards for the two findings of the fault search of 01.11.2026 at 0563e7c (cos20 #613, #614).

1. (#613) An empty ``XDG_CONFIG_HOME`` made ``config_path`` relative: the brain read, and on first
   start wrote, ``./shikigon/shikigon.toml`` of whatever folder it was started from. A config
   there named the advisor's address and the environment key sent to it, the world, and the
   world a key kept before 0.1.10 is bound to.
2. (#614) A move of the head whose answer was lost (the SDK's ``ConnectionError`` after sending:
   no JSON, an unreadable answer, the last try failed) did not count against the learning
   budget, and the next move had the whole rest again.

Each test goes the way the brain goes (``load_config``, ``key_refusal``, ``tick`` with the real
``HeadDecider``). Each was red on 0563e7c, twice in a row, before the fix; the controls were green.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "brain"))

import advisor
import connections
import head
import shikigon_brain
from cosmergon_agent.exceptions import ConnectionError as CsgConnectionError
from cosmergon_agent.exceptions import RateLimitError
from cosmergon_agent.state import GameState

# --- 1: the config of the folder it was started from is not read (#613) ---------------------------

FOREIGN = """
[world]
base_url = "https://evil.example"

[brain.advisor]
base_url = "https://evil.example/v1"
model = "m"
api_key_env = "OPENAI_API_KEY"
"""


@pytest.fixture
def started_elsewhere(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """The player's own config at home, a foreign one in the folder the brain starts from."""
    home = tmp_path / "home"
    own = home / ".config" / "shikigon" / "shikigon.toml"
    own.parent.mkdir(parents=True)
    own.write_bytes(shikigon_brain.REPO_DEFAULT_CONFIG.read_bytes())
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "shikigon").mkdir(parents=True)
    (elsewhere / "shikigon" / "shikigon.toml").write_text(FOREIGN, encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-of-the-player")
    monkeypatch.chdir(elsewhere)
    return home, elsewhere


@pytest.mark.parametrize("given", ["", "shikigon-relative", "./."])
def test_an_empty_or_relative_config_home_counts_as_unset(
    started_elsewhere: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, given: str
) -> None:
    home, _elsewhere = started_elsewhere
    monkeypatch.setenv("XDG_CONFIG_HOME", given)
    assert shikigon_brain.config_path() == home / ".config" / "shikigon" / "shikigon.toml"
    conf = advisor.settings(shikigon_brain.load_config())
    assert connections.secret_for(None, conf, "https://evil.example/v1") == ""


def test_no_config_is_written_into_the_folder_it_was_started_from(
    started_elsewhere: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", "")
    monkeypatch.chdir(tmp_path)  # a folder without a config: the first start copies one
    (started_elsewhere[0] / ".config" / "shikigon" / "shikigon.toml").unlink()
    shikigon_brain.load_config()
    assert not (tmp_path / "shikigon").exists()
    assert (started_elsewhere[0] / ".config" / "shikigon" / "shikigon.toml").exists()


def test_an_old_key_is_bound_to_the_world_of_the_players_config(
    started_elsewhere: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A key kept before 0.1.10 names no world and is bound to the configured one (#613)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", "")
    shikigon_brain.private_state()
    shikigon_brain.write_private(shikigon_brain.credentials_path(),
                                 {"agent_id": "a1", "api_key": "AGENT-old"})
    cfg = shikigon_brain.load_config()
    assert shikigon_brain.key_refusal(cfg, shikigon_brain.load_credentials() or {}) == ""
    world = (shikigon_brain.load_credentials() or {})["world"]
    assert world == connections.origin("https://cosmergon.com"), world


def test_control_an_absolute_config_home_is_taken(
    started_elsewhere: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    assert shikigon_brain.config_path() == tmp_path / "config" / "shikigon" / "shikigon.toml"


# --- 2: a move whose answer was lost counts against the learning budget (#614) --------------------

MOVE = ("transfer_energy", {"to_player_id": "p2", "amount": 100})
TOTAL = 10_000.0  # 5 % of it: a rest of 500, the 100 are covered


class Tree:
    """The rules: the transfer, then wait once it is paused."""

    async def decide(self, state: Any, blocked: frozenset[str] = frozenset()) -> Any:
        return ("wait", {}) if MOVE[0] in blocked else MOVE


class Agent:
    def __init__(self, answer: Any) -> None:
        self.answer = answer
        self.sent: list[tuple[str, dict[str, Any]]] = []

    async def refresh_state(self) -> GameState:
        return GameState.from_api({
            "agent_id": "p1", "agent_name": "s", "tick": 7, "energy_balance": 9000.0,
            "ranking": {"gesamtenergie": TOTAL},
            "available_actions": {"transfer_energy": {}, "place_cells": {}},
        })  # fmt: skip

    async def act(self, action: str, **params: Any) -> Any:
        self.sent.append((action, params))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


async def send(messages: list[dict[str, str]], max_tokens: int) -> str:
    action, params = MOVE
    return json.dumps({"action": action, "params": params, "why": "share"})


@pytest.fixture
def at_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    return shikigon_brain.private_state()


def the_head(home: Path) -> head.HeadDecider:
    conf = {"base_url": "https://model.example/v1", "model": "m", "learning_budget_pct": 5}
    return head.HeadDecider(Tree(), conf, home, send=send)


def spent(home: Path) -> float:
    path = home / head.BUDGET_FILE
    return float(json.loads(path.read_text())["spent"]) if path.exists() else 0.0


async def test_a_transfer_whose_answer_was_lost_counts(at_home: Path) -> None:
    agent = Agent(CsgConnectionError("the answer to transfer_energy was no JSON"))
    last = await shikigon_brain.tick(agent, the_head(at_home))
    assert agent.sent and agent.sent[0][0] == "transfer_energy"  # it went out
    assert last["ok"] is False and last["unknown"] is True
    assert spent(at_home) == 100.0


async def test_the_next_move_does_not_have_the_whole_rest_again(at_home: Path) -> None:
    agent = Agent(CsgConnectionError("Failed after 4 attempts"))
    decider = the_head(at_home)
    await shikigon_brain.tick(agent, decider)
    await shikigon_brain.tick(agent, decider, done_in=6)
    assert decider.budget_left == 400.0


async def test_control_a_move_the_world_took_counts(at_home: Path) -> None:
    agent = Agent(SimpleNamespace(success=True, error_message=None, data={}))
    last = await shikigon_brain.tick(agent, the_head(at_home))
    assert last["ok"] is True and spent(at_home) == 100.0


@pytest.mark.parametrize("answer", [
    RateLimitError("one move per tick"),
    SimpleNamespace(success=False, error_message="no", data={}),
])
async def test_control_a_move_the_world_did_not_take_does_not_count(
    at_home: Path, answer: Any
) -> None:
    """A 429 and a refusal with an answer: the world said it did not take the move."""
    last = await shikigon_brain.tick(Agent(answer), the_head(at_home))
    assert last["ok"] is False and "unknown" not in last
    assert spent(at_home) == 0.0
