"""cos20 #492, Betreuer-Durchgang 3b6fff1: what the pass found, each as a test that was red there.

B1: the quarter of an hour between two partner requests stood in each process's memory; a
    ``tick`` by hand and every start of the game loop asked at once.
B2: the README's list of what the bar touches named two kinds of notifications; the bar sends
    a third (a step that ended without its result).
K3: the request ran inside the move, before the status was written.
K5: a number that is not finite was shown as 0.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import shikigon_brain as brain

ROOT = Path(__file__).resolve().parent.parent
PARTNER = {"umsatz": {"direkt_gesamt_cents": 12_000}, "provision": {"wartezeit_cents": 3_960}}


class _Agent:
    def __init__(self, partner: Any = PARTNER, hangs: bool = False) -> None:
        self.partner_answer, self.hangs, self.asked = partner, hangs, 0

    async def partner(self) -> Any:
        self.asked += 1
        if self.hangs:
            await asyncio.sleep(3600)
        return self.partner_answer

    async def exchange_rates(self) -> None:
        return None

    async def missions(self) -> list[Any]:
        return []


@pytest.fixture(autouse=True)
def _own_folders(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from cosmergon_agent import config

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


ASKS_IN_A_NEW_PROCESS = """
import asyncio, sys
sys.path.insert(0, sys.argv[1])
import shikigon_brain as brain
class Agent:
    asked = 0
    async def partner(self):
        Agent.asked += 1
        return {}
    async def exchange_rates(self):
        return None
asyncio.run(brain.earn_refresh(Agent()))
print(Agent.asked)
"""


def test_b1_a_new_process_does_not_ask_again() -> None:
    """The time of the last request is the state folder's, not a process's: the game loop started
    anew (after "advise on moves", a stop, a wake) finds it there."""
    agent = _Agent()
    asyncio.run(brain.earn_refresh(agent))
    assert agent.asked == 1
    done = subprocess.run([sys.executable, "-c", ASKS_IN_A_NEW_PROCESS, str(ROOT / "brain")],
                          capture_output=True, text=True, timeout=60, check=True)  # fmt: skip
    assert done.stdout.strip() == "0", done.stdout + done.stderr


async def test_b1_k3_a_move_asks_nobody(monkeypatch: pytest.MonkeyPatch) -> None:
    """What follows a move (``write_down``, also of a ``tick`` by hand) reads the figures kept and
    sends nothing for them: with none kept and a world that would answer, the status has none."""
    written: list[dict[str, Any]] = []
    monkeypatch.setattr(brain, "write_status", written.append)
    agent = _Agent()
    state = type("State", (), {"tick": 7, "energy": 1.0, "fields": []})()
    await brain.write_down(agent, object(), state, {"action": "wait", "ok": True, "why": ""})
    assert agent.asked == 0 and "earn" not in written[0]
    await brain.earn_refresh(agent)  # the game loop, beside the move
    await brain.write_down(agent, object(), state, {"action": "wait", "ok": True, "why": ""})
    assert agent.asked == 1 and written[1]["earn"]["revenue"] == 120.0


def test_b1_only_the_game_loop_asks() -> None:
    """One caller of ``earn_refresh``, in ``run``; nothing else reaches the partner endpoint."""
    code = (ROOT / "brain" / "shikigon_brain.py").read_text(encoding="utf-8")
    assert len(re.findall(r"earn_refresh\(", code)) == 2  # its definition and ``run``
    run = code[code.index("async def run("):code.index("async def with_agent(")]
    assert "await earn_refresh(agent)" in run
    assert len(re.findall(r"\.partner\(\)|\.exchange_rates\(\)", code)) == 2  # in earn_refresh


async def test_k3_a_world_that_does_not_answer_is_left_within_its_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(brain, "EARN_WAIT_S", 0.05)
    agent = _Agent(hangs=True)
    await asyncio.wait_for(brain.earn_refresh(agent), 5)
    assert agent.asked == 1 and brain.earn_kept() is None
    await brain.earn_refresh(agent)  # and not asked again before its quarter of an hour
    assert agent.asked == 1


async def test_b1_a_clock_set_back_does_not_ask_at_once() -> None:
    """A time of the last request that lies ahead counts from now: a quarter of an hour on."""
    path = brain.state_dir() / brain.EARN_FILE
    brain.files.write_json(path, {"at": brain.time.time() + 86_400, "figures": None})
    agent = _Agent()
    await brain.earn_refresh(agent)
    assert agent.asked == 0
    assert abs(brain.files.read_object(path)["at"] - brain.time.time()) < 60
    await brain.earn_refresh(agent)
    assert agent.asked == 0  # still within the quarter of an hour that began just now


@pytest.mark.parametrize("cents", [float("inf"), "1e999", 10**400])
async def test_k5_a_number_that_is_not_finite_shows_nothing(cents: Any) -> None:
    agent = _Agent({"umsatz": {"direkt_gesamt_cents": cents}, "provision": {}})
    await brain.earn_refresh(agent)
    assert agent.asked == 1 and brain.earn_kept() is None


async def test_a_new_shikigon_starts_without_the_old_ones_figures() -> None:
    await brain.earn_refresh(_Agent())
    assert brain.earn_kept() is not None
    brain.leave_the_old_one(brain.state_dir())
    assert brain.earn_kept() is None


def test_a_file_of_another_form_is_no_figures() -> None:
    path = brain.state_dir() / brain.EARN_FILE
    for kept in ([1], {"figures": "x"}, {"figures": {"revenue": "a", "commission": 1}},
                 {"figures": {"revenue": True, "commission": 1}}):  # fmt: skip
        brain.files.write_json(path, kept)
        assert brain.earn_kept() is None
    brain.files.write_json(path, {"at": "x", "figures": {"revenue": 1, "commission": 2,
                                                         "currency": "<b>"}})  # fmt: skip
    assert brain.earn_kept() == {"revenue": 1.0, "commission": 2.0, "currency": "EUR"}


def test_b2_the_readme_names_every_notification_the_bar_sends() -> None:
    """Three places in the bar send a desktop notification, and the README's list of what the bar
    touches names each. A fourth place turns this red until the README names it too."""
    qml = "".join(path.read_text(encoding="utf-8") for path in (ROOT / "qml").glob("*.qml"))
    senders = re.findall(r"notify-send -a Shikigon", qml)
    assert len(senders) == 3, "a new notification: name it in the README (On your desktop)"
    readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
    start = readme.index("- **Notifications:**")
    said = readme[start:readme.index("- **Programs:**", start)]
    for part in ("once when it moves in", "a hint the first time something new happens",
                 "ended without its result"):  # fmt: skip
        assert part in said, part
