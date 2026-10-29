"""The twelfth review (cos20 #492, the betreuer pass at ``0d056e8``): what the learning budget
counts, and no start of an agent's program runs Omarchy's placeholder.

1. (B1) The budget counts what a move gives or books when it is made. A mission the head starts
   uses bombs on later ticks, and a field it gives up leaves its total energy without the world
   booking it in that move; the README promised the whole loss of a day. Founder 29.10.2026: the
   promise is narrowed, the head keeps its missions;
2. (B2) only ``installed`` looked for Omarchy's placeholder. Reading the agent's version before
   each question, list and look started it, and the stub runs ``mise use -g``: pi installed
   without a click. Every start passes ``guarded`` now, and a stub there is never started.

(B4, the open energy offers in the README's list of what the model receives, has its test in
``tests/test_advisor.py``.)

Each test is red against ``079e947`` (counter-test), except where its docstring says it guards
the form of a fix.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "brain"))

import connections
import head
import shikigon_brain as brain
from test_advisor import CONF, _state
from test_brain import FakeAgent
from test_head import MISSION, Replies, StubTree

BUDGET = {"learning_budget_pct": 5}
TOTAL = 1_000_000.0  # 5 % of it: 50,000 a day


@pytest.fixture(autouse=True)
def _private_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


# --- 1: the budget counts what a move gives or books when it is made ----------------------------


async def test_a_mission_the_head_starts_counts_what_it_books_when_it_is_made() -> None:
    """Guards the form of the narrowed promise: a mission without a reward goes out while a
    budget is set and counts what the world booked in the move, here nothing. What its bombs
    cost later is not counted, and the README says so."""
    decider = head.HeadDecider(StubTree(), {**CONF, **BUDGET}, brain.state_dir(),
                               send=Replies(MISSION))  # fmt: skip
    agent = FakeAgent(_state(ranking={"wertung": "total_energy", "gesamtenergie": TOTAL}))
    move = await brain.tick(agent, decider)
    assert move["action"] == "start_mission" and move["ok"], move
    assert head.budget_left(brain.state_dir(), BUDGET, TOTAL) == pytest.approx(50_000.0)
    readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
    assert "Nor is what a move costs later" in readme


# --- 2: no start of an agent's program runs Omarchy's placeholder -------------------------------


def _placeholder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Omarchy's stub for pi on the path the brain starts with; it leaves a mark when it runs."""
    folder = tmp_path / "bin"
    folder.mkdir()
    stub = folder / "pi"
    mark = tmp_path / "started"
    stub.write_text(f'#!/bin/sh\ntouch "{mark}"\nmise use -g --quiet "pi" || exit 1\n')
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{folder}:/usr/bin:/bin")
    return mark


def test_no_start_of_an_agents_program_runs_omarchys_placeholder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one place every start passes: its version, its list, a question."""
    mark = _placeholder(tmp_path, monkeypatch)
    for argv in (["pi", "--offline", "--version"], ["pi", "--list-models"], ["pi", "-p"]):
        with pytest.raises(connections.Placeholder):
            connections.guarded(argv, None)
        with pytest.raises(OSError):
            connections.run_in_group(argv, timeout=5)
    assert not mark.exists(), "the placeholder ran"


def test_an_agent_the_placeholder_stands_in_for_is_not_asked_and_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    mark = _placeholder(tmp_path, monkeypatch)
    started: list[list[str]] = []

    def run(argv: list[str], **_: Any) -> Any:
        started.append(argv)
        return SimpleNamespace(returncode=0, stdout="1.4.0", stderr="")

    assert "Omarchy's placeholder" in connections.untried("pi", run)
    assert connections.agent_models("pi", run) == []
    with pytest.raises(connections.AgentError, match="placeholder"):
        connections.run_agent({"agent": "pi"}, "a question", 5.0, run=run)
    assert started == [], "neither its version, its list nor the question"
    assert not mark.exists()


def test_a_command_that_cannot_be_read_counts_as_a_placeholder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What cannot be told apart is not started (until 0.1.14 it counted as the user's own)."""
    command = tmp_path / "pi"
    command.write_text('mise use -g --quiet "pi"\n')

    def denied(*_: Any, **__: Any) -> str:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "read_text", denied)
    assert connections.is_placeholder(command) is True
