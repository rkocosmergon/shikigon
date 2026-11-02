"""Guards for the two findings of the fault search of 02.11.2026 (cos20 #621).

F1: the run loop's pause after a move had no upper bound; the SDK waits for the same word of the
world at most ``_MAX_POLL_S``. F2: a word of the compass table counted anywhere in your words, also
inside another word ("ally" in "really"). The tests go the ways the brain goes: ``run`` with a
world that announces its next tick, and ``main(["instruct"])`` with the words on stdin. The tests
marked as a control hold on both sides; the others were red on 1524d09.
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
import time
import tomllib
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Self

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "brain"))

import shikigon_brain as brain
from cosmergon_agent.agent import _MAX_POLL_S

# --- F1: the pause waits for the announced tick, at most as long as the SDK does ---------------


class _Stop(Exception):
    pass


class _World:
    """A world whose next tick comes ``ahead`` seconds from now."""

    def __init__(self, ahead: float) -> None:
        self.ahead = ahead
        self.state: Any = None

    async def refresh_state(self) -> Any:
        self.state = SimpleNamespace(tick=7, energy=100.0, fields=[],
                                     next_tick_at=time.time() + self.ahead)
        return self.state

    async def missions(self) -> list[Any]:
        return []


class _Waits:
    name, version, last = "wait", "0", None

    async def decide(self, state: Any) -> tuple[str, dict[str, Any]]:
        return "wait", {}


def _pause_after_one_move(ahead: float, interval: float, tmp_path: Path,
                          monkeypatch: pytest.MonkeyPatch) -> float:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        raise _Stop

    monkeypatch.setattr(brain.asyncio, "sleep", sleep)
    cfg = brain.load_config()
    cfg.setdefault("brain", {})["interval_s"] = interval
    with pytest.raises(_Stop):
        asyncio.run(brain.run(cfg, _World(ahead), _Waits()))
    assert len(slept) == 1
    return slept[0]


def test_control_the_pause_follows_the_announced_tick(tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    assert 100 < _pause_after_one_move(100.0, 60.0, tmp_path, monkeypatch) < 110


def test_a_tick_an_hour_ahead_is_waited_for_no_longer_than_the_sdk_waits(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _pause_after_one_move(3600.0, 60.0, tmp_path, monkeypatch) <= _MAX_POLL_S


def test_the_bound_is_the_sdks_own() -> None:
    """One rule, two places: the brain's bound and the SDK's stay equal."""
    assert getattr(brain, "MAX_PAUSE_S", None) == _MAX_POLL_S


def test_an_interval_of_the_players_beyond_the_bound_still_stands() -> None:
    state = SimpleNamespace(next_tick_at=1000.0 + 3600.0)
    assert brain.pause(600.0, state, now=1000.0) == 600.0


# --- F2: a word of the table counts only where one of your words begins -----------------------


TABLE: dict[str, list[str]] = tomllib.loads(
    (ROOT / "config" / "shikigon.toml").read_text(encoding="utf-8"))["instructions"]


class _Compass:
    def __init__(self) -> None:
        self.set: list[str] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def set_compass(self, preset: str) -> dict[str, Any]:
        self.set.append(preset)
        return {"ok": True}


def _instruct(words: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
              capsys: pytest.CaptureFixture[str]) -> tuple[dict[str, Any], list[str]]:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    home = tmp_path / "state" / "shikigon"
    home.mkdir(parents=True)
    (home / "credentials.json").write_text(json.dumps(
        {"agent_id": "a1", "api_key": "k1", "world": "https://cosmergon.com:443"}))
    compass = _Compass()
    monkeypatch.setattr(brain, "make_agent", lambda cfg: compass)  # the network stays out
    monkeypatch.setattr(sys, "stdin", io.StringIO(words + "\n"))
    assert brain.main(["instruct"]) == 0
    return json.loads(capsys.readouterr().out), compass.set


@pytest.mark.parametrize(("words", "preset"), [
    ("be careful", "defend"), ("trade more", "trade"), ("expand", "grow"),  # the README's
    ("explore", "explore"), ("actually grow", "grow"), ("carefully", "defend"),
    ("verteidigen", "defend"), ("Kaufen!", "trade"),
])
def test_control_your_words_set_the_compass(words: str, preset: str, tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch,
                                            capsys: pytest.CaptureFixture[str]) -> None:
    said, sent = _instruct(words, tmp_path, monkeypatch, capsys)
    assert said["preset"] == preset and sent == [preset]


@pytest.mark.parametrize(("words", "preset"), [
    ("really explore", "explore"), ("actually explore", "explore"),
    ("finally explore", "explore"), ("totally free", "autonomous"),
])
def test_a_word_inside_another_word_does_not_set_the_compass(
        words: str, preset: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    said, sent = _instruct(words, tmp_path, monkeypatch, capsys)
    assert said["preset"] == preset and sent == [preset]


def test_no_word_of_the_table_is_heard_inside_another_word() -> None:
    """The class, for every word the plugin ships: inside a longer word it sets nothing."""
    heard = {word: brain.preset_for("xq" + word, TABLE)
             for words in TABLE.values() for word in words}
    assert {word: preset for word, preset in heard.items() if preset is not None} == {}


def test_control_every_word_of_the_table_is_heard_where_a_word_begins() -> None:
    """And where a word begins, with an ending, it is heard: by its own group or one before it."""
    order = list(TABLE)
    for own, words in TABLE.items():
        for word in words:
            preset = brain.preset_for(f"please {word}en now", TABLE)
            assert preset is not None and order.index(preset) <= order.index(own), word


# --- K17: a sleep for its config comes back; the bar starts it again only after an exit != 0 ---


class _Opened:
    """An agent whose connection fails as it opens, with ``error``."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    async def __aenter__(self) -> Self:
        raise self.error

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _run_ends(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, refusals: list[str],
              agent: Any = None) -> tuple[int, dict[str, Any]]:
    """``main(["run"])`` with the world's port refused as ``refusals`` says, call by call."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    home = tmp_path / "state" / "shikigon"
    home.mkdir(parents=True)
    (home / "credentials.json").write_text(json.dumps(
        {"agent_id": "a1", "api_key": "k1", "world": "https://cosmergon.com:443"}))
    said = iter(refusals)
    monkeypatch.setattr(brain, "world_refusal", lambda cfg: next(said, ""))
    monkeypatch.setattr(brain, "key_refusal", lambda cfg, creds: "")
    monkeypatch.setattr(brain, "make_agent", lambda cfg: agent or _Compass())
    code = brain.main(["run"])
    return code, json.loads((home / "status.json").read_text())


@pytest.mark.parametrize("refusals", [["its port is not yours"], ["", "its port is not yours"]],
                         ids=["at the start", "while it runs"])
def test_a_port_that_is_not_yours_ends_the_run_so_that_the_bar_starts_it_again(
        refusals: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, status = _run_ends(tmp_path, monkeypatch, refusals)
    assert status["reason"] == brain.CONFIG and code != 0


def test_a_send_refused_at_the_port_ends_the_run_so_that_the_bar_starts_it_again(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from cosmergon_agent.exceptions import SendRefusedError

    code, status = _run_ends(tmp_path, monkeypatch, [], _Opened(SendRefusedError("not yours")))
    assert status["reason"] == brain.CONFIG and code != 0


def test_control_a_key_that_ended_ends_the_run_with_0_a_restart_would_not_help(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from cosmergon_agent.exceptions import AuthenticationError

    code, status = _run_ends(tmp_path, monkeypatch, [], _Opened(AuthenticationError("401")))
    assert status["reason"] == brain.KEY_ENDED and code == 0


# --- K27: the world allows a few compass changes an hour; the brain promises no minute ---------


class _TooOften(_Compass):
    async def set_compass(self, preset: str) -> dict[str, Any]:
        from cosmergon_agent.exceptions import RateLimitError

        raise RateLimitError(retry_after=30.0)  # what the SDK says, capped (_MAX_BACKOFF)


def test_a_compass_changed_too_often_promises_no_wait_the_world_does_not_keep(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))  # the journal stays here
    said = asyncio.run(brain.instruct(_TooOften(), "explore", TABLE))
    assert said["ok"] is False and said["preset"] == "explore"
    assert "minute" not in said["why"] and "later" in said["why"]
