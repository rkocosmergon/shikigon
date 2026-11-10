"""Guards for the Betreuer-Durchgang 15472e8 (cos20 #492).

B1 (Pflicht): ``pyproject.toml`` asked for cosmergon-agent >= 0.31.8 and the CHANGELOG promised
what 0.31.8 does, while ``requirements.lock`` pinned 0.31.7, and ``shikigon-wake`` installs from
the lock alone. The test of the promise stood as a strict ``xfail`` on the pin, so the gap was
green. Now every lower bound of ``pyproject.toml`` is held against the lock, and every
"needs <package> <version>" of the CHANGELOG too.

B2 (Kür): a compass word whose retry the port check refused said "no answer from the world; its
next move shows the compass", although while the port is not the player's the next move is
refused as well and the Shikigon sleeps. Such a word now carries ``retry_refused`` (a key), and
the bar promises no next move.
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "brain"))

import files
import shikigon_brain as brain
from cosmergon_agent.exceptions import ConnectionError as CsgConnectionError
from cosmergon_agent.exceptions import SendRefusedError
from test_betreuer_5d315a0 import _refused_retry
from test_brain import TABLE
from test_compass_in_the_panel import CompassWorld


@pytest.fixture(autouse=True)
def _private_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(files, "HELD_WAIT_S", 0.3)


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", text)[:3])


def _name(text: str) -> str:
    return re.split(r"[\[<>=!~ ;]", text.strip(), maxsplit=1)[0].lower().replace("_", "-")


def locked(lock: str) -> dict[str, tuple[int, ...]]:
    """``name==version`` of each package in a lock written by pip-compile."""
    return {_name(m[1]): _version(m[2])
            for m in re.finditer(r"^([A-Za-z0-9_.\-]+)==(\S+)", lock, re.MULTILINE)}


def lower_bounds(project: dict[str, Any]) -> dict[str, tuple[int, ...]]:
    """``name>=version`` of each dependency of ``pyproject.toml``."""
    bounds = {}
    for dependency in project["dependencies"]:
        found = re.search(r">=\s*([0-9][0-9.]*)", dependency)
        if found:
            bounds[_name(dependency)] = _version(found[1])
    return bounds


def changelog_needs(text: str) -> dict[str, tuple[int, ...]]:
    """Each "needs <package> <version>" the CHANGELOG says of a change, the highest per package."""
    needs: dict[str, tuple[int, ...]] = {}
    for m in re.finditer(r"needs\s+([a-z][a-z0-9-]+)\s+([0-9]+\.[0-9]+\.[0-9]+)", text):
        needs[m[1]] = max(needs.get(m[1], ()), _version(m[2]))
    return needs


def drift(bounds: dict[str, tuple[int, ...]], pins: dict[str, tuple[int, ...]]) -> list[str]:
    return [f"{name}: asks for {want}, the lock installs {pins.get(name)}"
            for name, want in bounds.items() if pins.get(name, ()) < want]


# ---------- B1 -----------------------------------------------------------------------------------


def test_b1_the_lock_holds_every_lower_bound_of_pyproject() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    pins = locked((ROOT / "requirements.lock").read_text(encoding="utf-8"))
    bounds = lower_bounds(project)
    assert bounds, "pyproject.toml names its lower bounds"
    assert drift(bounds, pins) == []


def test_b1_the_lock_holds_what_the_changelog_says_a_change_needs() -> None:
    pins = locked((ROOT / "requirements.lock").read_text(encoding="utf-8"))
    needs = changelog_needs((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
    assert drift(needs, pins) == []


def test_b1_control_the_guard_sees_the_drift_of_15472e8() -> None:
    lock = "cosmergon-agent==0.31.7 \\\n    --hash=sha256:00\ncosmergon-decider-tree==2.3.13 \\\n"
    project = {"dependencies": ["cosmergon-agent[dashboard]>=0.31.8",
                                "cosmergon-decider-tree>=2.3.13"]}
    assert drift(lower_bounds(project), locked(lock)) == [
        "cosmergon-agent: asks for (0, 31, 8), the lock installs (0, 31, 7)"]
    said = "- **A word** (needs\n  cosmergon-agent 0.31.8). Then (needs cosmergon-agent 0.31.2)."
    assert drift(changelog_needs(said), locked(lock)) == [
        "cosmergon-agent: asks for (0, 31, 8), the lock installs (0, 31, 7)"]


# ---------- B2 -----------------------------------------------------------------------------------


def _last_line() -> dict[str, Any]:
    return json.loads((brain.state_dir() / "journal.jsonl").read_text().splitlines()[-1])


async def test_b2_a_word_whose_retry_the_port_check_refused_promises_no_next_move() -> None:
    class RetryRefused(CompassWorld):
        async def set_compass(self, preset: str) -> dict[str, Any]:
            raise _refused_retry()

    with pytest.raises(CsgConnectionError):
        await brain.instruct(RetryRefused(), "attack now", TABLE)
    line = _last_line()
    assert line["answered"] is False and line["retry_refused"] is True, line
    recent = json.loads((brain.state_dir() / brain.BAR_JOURNAL).read_text())
    assert recent[-1]["retry_refused"] is True, recent


async def test_b2_control_a_word_whose_answer_got_lost_keeps_its_next_move() -> None:
    class AnswerLost(CompassWorld):
        async def set_compass(self, preset: str) -> dict[str, Any]:
            raise CsgConnectionError("Failed after 3 attempts")

    with pytest.raises(CsgConnectionError):
        await brain.instruct(AnswerLost(), "attack now", TABLE)
    line = _last_line()
    assert line["answered"] is False and "retry_refused" not in line, line


async def test_b2_control_a_word_refused_before_its_first_try_stays_not_sent() -> None:
    class NeverLeft(CompassWorld):
        async def set_compass(self, preset: str) -> dict[str, Any]:
            raise SendRefusedError("its port on this machine is not yours right now")

    with pytest.raises(SendRefusedError):
        await brain.instruct(NeverLeft(), "attack now", TABLE)
    line = _last_line()
    assert line["sent"] is False and "retry_refused" not in line, line
