"""The eighth review (cos20 #492, the betreuer pass at ``6935995``): each finding closed where the
class has its narrow place, with the variants the class had in this code.

1. pi puts its working folder into every system prompt (``core/system-prompt.ts:183``), and that
   folder lay in the player's home (``/home/<login>/.local/state/shikigon/run/…``): a message of
   another player could ask for it, and the answer goes back to that player. Every program the
   brain starts now runs in ``/``;
2. a move the head wrote with no parameters went out without ``strict``, and the world filled in
   its defaults (``market_list``: the whole surplus at the median price);
3. with no total energy from the world the learning budget checked nothing;
4. any patch release of a pi version was asked, read or not;
5. a look back counted one call and made up to four.

Each test is red against ``6935995`` (counter-test), except where its docstring says it guards
the form of a fix.
"""

from __future__ import annotations

import ast
import functools
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "brain"))

import advisor
import connections
import files
import head
import lessons
import reading
import shikigon_brain as brain
from conftest import fake_mise, is_command, version_answer
from test_advisor import CONF, _state
from test_effect import _transfer_forms
from test_head import LESSON, Replies, StubTree, _forms, _move, _played, _sell_state

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _private_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


# --- 1: no program tells its model where the player lives ----------------------------------------


def _record(seen: list[dict[str, Any]]) -> Any:
    def run(argv: list[str], **kw: Any) -> Any:
        seen.append({"argv": list(argv), **kw})
        if (said := version_answer(argv)) is not None:
            return said
        listing = "provider  model\nollama  m\n" if "--list-models" in argv else "OK"
        return SimpleNamespace(returncode=0, stdout=listing, stderr="")

    return run


def test_no_program_tells_its_model_where_the_player_lives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = tmp_path / "bin" / "pi"
    stub.parent.mkdir()
    stub.write_text('#!/bin/bash\nmise use -g --quiet "pi" || exit 1\n')  # Omarchy's placeholder
    mise = fake_mise(tmp_path / "usr")
    monkeypatch.setattr(connections.shutil, "which",
                        lambda cmd, **_: {"pi": str(stub), "mise": str(mise)}.get(cmd))
    seen: list[dict[str, Any]] = []
    run = _record(seen)
    connections.installed("pi", run=run)  # mise
    stub.write_text("\x7fELF" + "x" * 5000)  # mise installed it: no stub is started
    connections.agent_models("pi", run=run)  # its version, its list
    connections.run_agent({"agent": "pi"}, "question", 5.0, run=run)  # its version, the question
    assert [s["argv"][0] for s in seen] == [str(mise), *[str(stub.resolve())] * 4]
    assert {s["cwd"] for s in seen} == {"/"}, "pi names its working folder to its model"
    home = str(files.state_dir().parent.parent.parent)
    assert not any(home in s["cwd"] for s in seen)


class _RootForEveryone:
    """``os`` as ``connections`` sees it, with a ``/`` that everyone may write."""

    def __getattr__(self, name: str) -> Any:
        return getattr(os, name)

    @staticmethod
    def stat(path: Any, *args: Any, **kwargs: Any) -> Any:
        if str(path) == "/":
            return SimpleNamespace(st_uid=0, st_mode=0o40777)
        return os.stat(path, *args, **kwargs)


def test_nothing_is_started_where_someone_else_may_write(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(connections, "os", _RootForEveryone())
    started: list[Any] = []

    def run(argv: list[str], **kw: Any) -> Any:
        started.append(argv)
        return version_answer(argv) or SimpleNamespace(returncode=0, stdout="OK", stderr="")

    with pytest.raises(connections.AgentError):
        connections.run_agent({"agent": "pi"}, "a private question", 5.0, run=run)
    assert started == [], "neither its version nor the question"


# --- 2: what the head writes itself goes out with ``strict``, also with no parameters ------------


async def test_a_move_the_head_writes_without_parameters_goes_out_strict() -> None:
    """The world names the fields it needs instead of filling in its defaults (cos20
    ``markt.py``: ``market_list`` with none offers the whole surplus at the median price)."""
    model = Replies(json.dumps({"action": "market_list", "why": "sell what is left"}))
    decider = head.HeadDecider(StubTree(), CONF, brain.state_dir(), send=model, forms=_forms)
    action, params = await decider.decide(_sell_state())
    assert action == "market_list" and params.get("strict") is True


class EmptyLineTree:
    """The rules: a move without parameters."""

    async def decide(self, state: Any, blocked: frozenset[str] = frozenset()) -> Any:
        return ("wait", {}) if "cancel_mission" in blocked else ("cancel_mission", {})


async def test_a_rules_line_without_parameters_still_goes_out_as_it_is() -> None:
    """Guards the form of the fix: only what the head writes itself gets ``strict``."""
    model = Replies(_move("cancel_mission"))
    decider = head.HeadDecider(EmptyLineTree(), CONF, brain.state_dir(), send=model)
    assert await decider.decide(_sell_state()) == ("cancel_mission", {})


# --- 3: no total energy from the world, nothing given away --------------------------------------


async def test_without_a_total_from_the_world_nothing_is_given_away() -> None:
    gift = _move("transfer_energy", {"to_player_id": "p2", "amount": 10}, "allies")
    decider = head.HeadDecider(StubTree(), {**CONF, "learning_budget_pct": 5}, brain.state_dir(),
                               send=Replies(gift), forms=_transfer_forms)
    action, _ = await decider.decide(_state())  # the newborn's ranking names no total energy
    assert action == "create_field", "the rules' line"
    assert decider.last is not None and decider.last["fallback"] == head.NO_TOTAL
    assert head.budget_left(brain.state_dir(), {"learning_budget_pct": 5}, None) == 0.0


async def test_without_a_total_a_move_that_gives_nothing_still_goes_out() -> None:
    """Guards the form of the fix: the head still plays; only a gift waits for a total."""
    sell = {"item_type": "mega_bomb", "item_data": {"count": 5}, "price_energy": 900}
    decider = head.HeadDecider(StubTree(), {**CONF, "learning_budget_pct": 5}, brain.state_dir(),
                               send=Replies(_move("market_list", sell)), forms=_forms)
    action, params = await decider.decide(_sell_state())
    assert action == "market_list" and params["strict"] is True


# --- 4: only the versions read -------------------------------------------------------------------


@pytest.mark.parametrize("says", ["1.4.1", "1.2.9", "v1.3.12", "1.4.10"])
def test_a_release_never_read_is_not_asked(says: str) -> None:
    def run(argv: list[str], **kw: Any) -> Any:
        assert is_command(argv, connections.AGENTS["pi"]["version"]), "nothing but its version"
        return SimpleNamespace(returncode=0, stdout=says + "\n", stderr="")

    why = connections.untried("pi", run)
    assert why.startswith("pi is not asked") and "1.2.0, 1.3.0, 1.4.0" in why, why


# --- 5: every call of a model is counted ----------------------------------------------------------


def _look_back(home: Path, model: Replies, conf: dict[str, Any]) -> Any:
    _played(home, 40)
    reader = functools.partial(reading.mediated, model)
    return lessons.learn(home, conf, "facts", model, head=True, world="{}", reader=reader)


async def test_each_round_of_reading_is_a_call_of_its_own() -> None:
    home = brain.state_dir()
    model = Replies("READ moves.txt 1-5", "READ rated.txt 1-2", "READ world.txt 1-1", LESSON)
    entry = await _look_back(home, model, {**CONF, "review_every": 5})
    assert entry is not None and entry["ok"], entry
    assert len(model.asked) == 4 and advisor.calls_today(home) == 4


async def test_the_days_limit_ends_its_reading() -> None:
    home = brain.state_dir()
    model = Replies(*["READ moves.txt 1-5"] * 3, LESSON)
    entry = await _look_back(home, model, {**CONF, "review_every": 5, "max_per_day": 2})
    assert len(model.asked) == 2 and advisor.calls_today(home) == 2, "not past the limit"
    assert entry is not None and entry["why"] == "daily limit reached"


def test_every_call_of_a_model_is_counted() -> None:
    """The class at its narrow place: each function of the brain that awaits a sender counts
    that call (``count_call``) or asks ``more()`` before it. ``probe`` is the one question on
    the player's click (README)."""
    uncounted = []
    for path in sorted((ROOT / "brain").glob("*.py")):
        for function in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            calls = [n.func for n in ast.walk(function) if isinstance(n, ast.Call)]
            names = {f.id if isinstance(f, ast.Name) else getattr(f, "attr", "") for f in calls}
            sends = [n for n in ast.walk(function) if isinstance(n, ast.Await)
                     and isinstance(n.value, ast.Call)
                     and (getattr(n.value.func, "id", "") == "send"
                          or getattr(n.value.func, "attr", "") == "send")]
            if sends and function.name != "probe" and not names & {"count_call", "more"}:
                uncounted.append(f"{path.name}:{function.lineno} {function.name}")
    assert uncounted == []
