"""Guards for the shikigon findings of the fault search of 05.11.2026 (cos20 #630).

#5: the README named the wrong key for showing it publicly. #6: ``forget`` took a redirect for a
yes and removed the only key. #7: a move that had gone out was said to be not played when the
disk would not take its line. #8: a tier of the world with thousands of digits ended the move, on
the way the head reads the world's rules. #9: a config that cannot be opened ended in a traceback.
#16: the bar, without a click, could register a new agent when the key file had gone.

The tests go the ways the brain goes: ``main`` and ``guarded_tick``, a world on a real local port,
the real wake script. What only the bar does is held by the last test here and by the panel's own
run in the search's folder (cos20 docs).
"""

from __future__ import annotations

import asyncio
import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "brain"))

import connections
import contracts
import files
import head
import shikigon_brain as brain
from cosmergon_agent.action import ActionResult
from cosmergon_agent.exceptions import AuthenticationError
from cosmergon_agent.state import GameState
from cosmergon_decider_tree import TreeDecider

CONF = {"via": "http", "base_url": "http://127.0.0.1:1", "model": "m"}
NOT_WRITTEN = "could not be written down"  # the words, so a stand without the fix fails on them


@pytest.fixture(autouse=True)
def _a_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    for name in ("http_proxy", "https_proxy", "all_proxy",
                 "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):  # fmt: skip
        monkeypatch.delenv(name, raising=False)
    brain.NOT_PLAYED_COUNT["since_start"] = 0


def _world() -> GameState:
    return GameState.from_api({"tick": 7, "energy": 1000.0, "agent_id": "a", "agent_name": "probe",
                               "fields": [], "available_actions": {}})  # fmt: skip


class _Agent:
    def __init__(self) -> None:
        self.state = _world()
        self.played: list[str] = []

    async def refresh_state(self) -> GameState:
        return self.state

    async def act(self, action: str, **_params: Any) -> ActionResult:
        self.played.append(action)
        return ActionResult.from_response(action, 200, {"success": True, "result": {}}, "k")

    async def missions(self) -> list[Any]:
        return []


class _Places:
    last = None

    async def decide(self, _state: Any) -> tuple[str, dict[str, Any]]:
        return "place_cells", {}


def _move(agent: Any = None, decider: Any = None, refusals: Any = None) -> dict[str, Any]:
    return asyncio.run(brain.guarded_tick(agent or _Agent(), decider or _Places(), None, refusals))


# --- #5: the key the README names ---------------------------------------------------------------


def _action_of(key: str) -> str:
    from cosmergon_agent.dashboard import CosmergonDashboard

    for binding in CosmergonDashboard.BINDINGS:
        if key in str(binding.key).split(","):
            return str(binding.action)
    return ""


def test_5_the_readme_names_the_key_that_shows_it_publicly() -> None:
    said = re.search(r"you\s+change\s+that\s+in\s+the\s+terminal\s+with\s+(\w)\b",
                     (ROOT / "README.md").read_text(encoding="utf-8"))  # fmt: skip
    assert said is not None
    assert _action_of(said.group(1).lower()) == "toggle_showcase"


def test_5_control_s_is_the_shop() -> None:
    assert _action_of("s") not in ("", "toggle_showcase")


# --- #6: forget, when the world did not delete ----------------------------------------------------


class _World(http.server.BaseHTTPRequestHandler):
    answer = 200

    def do_DELETE(self) -> None:
        self.send_response(type(self).answer)
        if 300 <= type(self).answer < 400:
            self.send_header("Location", "https://example.invalid/")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *_args: Any) -> None:
        pass


def _forget(code: int, capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, Any]]:
    _World.answer = code
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _World)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        brain.config_path().parent.mkdir(parents=True)
        brain.config_path().write_text(f'[world]\nbase_url = "{url}"\n', encoding="utf-8")
        brain.private_state()
        files.write_json(brain.credentials_path(), {
            "agent_id": "a1", "api_key": "AGENT-x", "expires_at": "",
            "world": connections.origin(url)})  # fmt: skip
        code_out = brain.main(["forget", "--yes"])
    finally:
        server.shutdown()
    return code_out, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


@pytest.mark.parametrize("code", [301, 302, 303, 304, 307, 308, 400, 403, 404, 500, 503])
def test_6_what_is_no_yes_removes_nothing(code: int, capsys: pytest.CaptureFixture[str]) -> None:
    out, said = _forget(code, capsys)
    assert out == 1 and brain.credentials_path().exists(), said
    assert str(code) in said["why"]


@pytest.mark.parametrize("code", [200, 202, 204])
def test_6_control_a_yes_removes_it(code: int, capsys: pytest.CaptureFixture[str]) -> None:
    out, said = _forget(code, capsys)
    assert out == 0 and said["server"] == "deleted" and not brain.credentials_path().exists()


def test_6_control_an_ended_key_removes_it_too(capsys: pytest.CaptureFixture[str]) -> None:
    out, said = _forget(401, capsys)
    assert out == 0 and "Vagant" in said["server"] and not brain.credentials_path().exists()


# --- #7: what fails after the move went out -------------------------------------------------------


def _no_room_for(*names: str) -> None:
    for name in names:
        (brain.state_dir() / name).mkdir(parents=True)


def test_7_control_nothing_in_the_way() -> None:
    agent = _Agent()
    last = _move(agent)
    assert agent.played == ["place_cells"] and last["ok"] is True
    assert NOT_WRITTEN not in str(last.get("why"))
    assert json.loads((brain.state_dir() / "status.json").read_text())["last"]["ok"] is True


def test_7_a_played_move_is_not_called_unplayed() -> None:
    _no_room_for("journal.jsonl")
    agent = _Agent()
    last = _move(agent)
    assert agent.played == ["place_cells"]
    assert last["ok"] is True and NOT_WRITTEN in last["why"], last
    assert brain.NOT_PLAYED not in last["why"]
    assert brain.NOT_PLAYED_COUNT["since_start"] == 0


def test_7_no_room_at_all_ends_the_move_and_nothing_else() -> None:
    _no_room_for("journal.jsonl", "status.json")
    last = _move()
    assert last["ok"] is True and NOT_WRITTEN in last["why"]


def test_7_a_count_without_room_keeps_the_move() -> None:
    _no_room_for("refusals.json")
    agent = _Agent()
    last = _move(agent, refusals=brain.Refusals(brain.state_dir() / "refusals.json"))
    assert agent.played == ["place_cells"] and last["ok"] is True, last


def test_7_falling_asleep_needs_no_room() -> None:
    _no_room_for("status.json")
    agent = _Agent()

    async def nothing() -> None:
        return None

    agent.refresh_state = nothing  # type: ignore[method-assign]
    last = _move(agent)
    assert last["ok"] is False and last["why"] == "no state from the world"


def test_7_a_key_that_ended_still_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """What ``with_agent`` puts to sleep with its own words must not end as a line about writing."""
    monkeypatch.setattr(brain, "tournament_running", lambda _state: True)
    agent = _Agent()

    async def ended() -> list[Any]:
        raise AuthenticationError("ended")

    agent.missions = ended  # type: ignore[method-assign]
    with pytest.raises(AuthenticationError):
        _move(agent)


# --- #8: the world's rules, on the way to the head ------------------------------------------------


def _rules(tier: str) -> dict[str, Any]:
    return {"actions": {"evolve": {"energy_multiplier_per_tier": {tier: 2}}}}


def _head(forms: Any = None, world: Any = None) -> head.HeadDecider:
    async def waits(_talk: Any, _tokens: int) -> str:
        return '{"action": "wait", "params": {}, "why": "x"}'

    async def none_of_forms() -> dict[str, Any]:
        return {}

    async def none_of_world() -> list[str]:
        return []

    return head.HeadDecider(contracts.Careful(TreeDecider()), CONF, brain.private_state(),
                            send=waits, forms=forms or none_of_forms,
                            world=world or none_of_world)  # fmt: skip


def test_8_a_tier_of_thousands_of_digits_costs_the_head_not_the_move() -> None:
    """The world's own rules, on the real way: ``int`` refuses past 4300 digits."""

    async def told() -> list[str]:
        return head.world_lines(_rules("1" * 5000))

    last = _move(decider=_head(world=told))
    assert brain.NOT_PLAYED not in str(last.get("why")), last


def test_8_control_a_tier_is_told() -> None:
    assert any("tier 1:" in line for line in head.world_lines(_rules("1")))


@pytest.mark.parametrize("error", [ValueError, KeyError, TypeError, AttributeError])
@pytest.mark.parametrize("which", ["forms", "world"])
def test_8_rules_that_cannot_be_read_cost_the_head_not_the_move(which: str, error: type) -> None:
    asked = []

    async def odd() -> Any:
        asked.append(1)
        raise error("odd")

    decider = _head(**{which: odd})
    last = _move(decider=decider)
    assert brain.NOT_PLAYED not in str(last.get("why")), last
    assert decider.last is not None and decider.last["head"] is True
    _move(decider=decider)
    assert len(asked) == 2, "the next move asks again"


def test_8_control_rules_that_can_be_read_are_kept() -> None:
    asked = []

    async def told() -> list[str]:
        asked.append(1)
        return ["A tick is about 60 seconds"]

    decider = _head(world=told)
    _move(decider=decider)
    _move(decider=decider)
    assert len(asked) == 1 and decider.world == ["A tick is about 60 seconds"]


# --- #9: a config or a folder it cannot open ------------------------------------------------------


def _program(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(ROOT / "brain" / "shikigon_brain.py"), *args],
                          check=False, capture_output=True, text=True, timeout=60,
                          env=os.environ.copy())  # fmt: skip


def test_9_a_config_it_cannot_open_is_one_sentence() -> None:
    brain.config_path().mkdir(parents=True)
    done = _program("status")
    assert done.returncode == 1 and "Traceback" not in done.stderr, done.stderr
    assert "config" in json.loads(done.stdout.strip().splitlines()[-1])["why"]


def test_9_control_a_broken_config_is_one_sentence() -> None:
    brain.config_path().parent.mkdir(parents=True)
    brain.config_path().write_text("[broken")
    done = _program("status")
    assert done.returncode == 1 and "config" in done.stdout


# --- #16: no new agent without a click ----------------------------------------------------------


def _wake(tmp_path: Path, *args: str, key: bool, vanishes: bool = False) -> tuple[int, str, str]:
    """The real script and the real lock; a stand-in for the Python environment writes its calls.
    The stand-in is never left out, and the world is no world: run against a stand without the fix,
    a version of this test without them registered two real agents on cosmergon.com (cos20 #630)."""
    home = tmp_path / "home"
    called = home / "called"
    env_dir = home / ".local" / "share" / "shikigon" / "venv"
    state = home / "state" / "shikigon"
    state.mkdir(parents=True)
    (env_dir / "bin").mkdir(parents=True)
    config = home / "config" / "shikigon"
    config.mkdir(parents=True)
    (config / "shikigon.toml").write_text('[world]\nbase_url = "http://127.0.0.1:1"\n')
    lock = subprocess.run(["sha256sum", str(ROOT / "requirements.lock")], check=True,
                          capture_output=True, text=True).stdout.split()[0]  # fmt: skip
    (env_dir / ".lock-sha256").write_text(lock + "\n")
    python = env_dir / "bin" / "python"
    gone = f'rm -f "{state}/credentials.json"\n' if vanishes else ""
    python.write_text(f'#!/bin/sh\necho "$*" >> "{called}"\n{gone}exit 0\n')
    python.chmod(0o755)
    if key:
        (state / "credentials.json").write_text("{}")
    env = {**os.environ, "HOME": str(home), "XDG_STATE_HOME": str(home / "state"),
           "XDG_CONFIG_HOME": str(home / "config")}  # fmt: skip
    bash = shutil.which("bash")
    assert bash
    done = subprocess.run([bash, str(ROOT / "brain" / "shikigon-wake"), *args], env=env,
                          capture_output=True, text=True, timeout=30, check=False)  # fmt: skip
    said = json.loads((state / "setup.json").read_text())["stage"]
    return done.returncode, called.read_text() if called.exists() else "", said


def test_16_a_look_registers_nothing_without_a_key(tmp_path: Path) -> None:
    code, called, said = _wake(tmp_path, "--look", key=False)
    assert code == 0 and said == "done" and " register" not in called, called


def test_16_a_look_registers_nothing_with_a_key(tmp_path: Path) -> None:
    code, called, _said = _wake(tmp_path, "--look", key=True)
    assert code == 0 and " register" not in called, called


def test_16_a_key_that_goes_during_the_look_brings_no_new_one(tmp_path: Path) -> None:
    code, called, _said = _wake(tmp_path, "--look", key=True, vanishes=True)
    assert code == 0 and "import cosmergon_agent" in called and " register" not in called, called


def test_16_control_a_look_still_checks_the_brain(tmp_path: Path) -> None:
    _code, called, _said = _wake(tmp_path, "--look", key=True)
    assert "import cosmergon_agent" in called, "after an update of Python it rebuilds (14.10.)"


def test_16_control_the_click_registers(tmp_path: Path) -> None:
    code, called, _said = _wake(tmp_path, key=False)
    assert code == 0 and " register" in called, called


def test_16_control_new_replaces_the_one_here(tmp_path: Path) -> None:
    code, called, _said = _wake(tmp_path, "--new", key=True)
    assert code == 0 and " register --new" in called, called


def test_every_start_of_the_waker_says_what_it_is() -> None:
    """Every place that starts the waker sets ``look`` right before it: the click clears it,
    whatever starts it on its own sets it. A new start without either is the old finding."""
    text = (ROOT / "qml" / "Service.qml").read_text(encoding="utf-8")
    starts = re.findall(r"(waker\.look = (?:true|false)\n\s*)?waker\.running = true", text)
    assert len(starts) == 3 and all(starts), starts
    wake = text[text.index("function wake(fresh)"):text.index("function recheck()")]
    assert "waker.look = false" in wake and "waker.look = true" not in wake
    own = text[text.index("id: restartLater"):text.index("IpcHandler {")]
    assert own.count("waker.look = true") == 2 and "waker.look = false" not in own
