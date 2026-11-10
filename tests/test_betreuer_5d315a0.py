"""Guards for the Betreuer-Durchgang 5d315a0 (cos20 #492).

B1 (Pflicht): the SDK checks ``send_check`` before every try, retries included. When the first
try of a compass word left (a timeout after sending, a 5xx) and the retry was refused, the SDK
raised ``SendRefusedError``, and the journal said "not sent" of a word the world may have set.
B2 (Pflicht): the same refusal of a move's retry passed ``play`` as "nothing sent": the move the
world may have booked stood neither in the journal nor against the learning budget.
The class is closed where it starts, in the SDK (cosmergon-agent >= 0.31.8): a refused retry of a
write is a ``ConnectionError``, outcome unknown, with the refusal as its cause. The brain already
reads that as "no answer" (``instruct``) and as ``unknown`` (``play``, #614).

K1 (Kür): ``files.lines_held`` took no lock while the file was not there yet; an empty view of
the journal could land after the view of its first line. It now opens the file as ``append`` does.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import stat
import sys
import tomllib
from pathlib import Path
from typing import Any

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "brain"))

import connections
import cosmergon_agent
import files
import shikigon_brain as brain
from cosmergon_agent.exceptions import ConnectionError as CsgConnectionError
from cosmergon_agent.exceptions import SendRefusedError
from test_brain import TABLE
from test_compass_in_the_panel import CompassWorld
from test_fehlersuche_613_614 import Agent, spent, the_head

PINNED = (0, 31, 8)


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", text)[:3])


SDK_BEFORE_THE_FIX = _version(cosmergon_agent.__version__) < PINNED


def _refused_retry() -> CsgConnectionError:
    """What cosmergon-agent >= 0.31.8 raises when a retry of a write is refused."""
    refusal = SendRefusedError(connections.NOT_OURS)
    error = CsgConnectionError(f"{refusal.message} (refused before a retry; an earlier try may "
                               "have reached the server)")
    error.__cause__ = refusal
    return error


@pytest.fixture(autouse=True)
def _private_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(files, "HELD_WAIT_S", 0.3)


def _last_line() -> dict[str, Any]:
    return json.loads((brain.state_dir() / "journal.jsonl").read_text().splitlines()[-1])


# ---------- B1 -----------------------------------------------------------------------------------


async def test_b1_a_word_whose_retry_was_refused_says_no_answer_not_not_sent() -> None:
    class FirstTryLeft(CompassWorld):
        async def set_compass(self, preset: str) -> dict[str, Any]:
            raise _refused_retry()

    with pytest.raises(CsgConnectionError):
        await brain.instruct(FirstTryLeft(), "attack now", TABLE)
    line = _last_line()
    assert line["answered"] is False and "sent" not in line, line
    recent = json.loads((brain.state_dir() / brain.BAR_JOURNAL).read_text())
    assert recent[-1]["answered"] is False and "sent" not in recent[-1], recent


async def test_b1_control_a_word_refused_before_its_first_try_stays_not_sent() -> None:
    class NeverLeft(CompassWorld):
        async def set_compass(self, preset: str) -> dict[str, Any]:
            raise SendRefusedError("its port on this machine is not yours right now")

    with pytest.raises(SendRefusedError):
        await brain.instruct(NeverLeft(), "attack now", TABLE)
    assert _last_line()["sent"] is False


def _sdk_agent(verdicts: list[str], answers: list[Any]) -> Any:
    """The real SDK client, its network a list of answers, ``send_check`` a list of verdicts."""
    from cosmergon_agent import CosmergonAgent

    agent = CosmergonAgent(api_key="AGENT-x:y", agent_id="a", base_url="https://world.invalid",
                           max_retries=2, send_check=lambda url: verdicts.pop(0))

    def handler(request: httpx.Request) -> httpx.Response:
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return httpx.Response(answer, json={"ok": True})

    client = agent._create_client()
    client._transport = httpx.MockTransport(handler)
    agent._client = client
    return agent


@pytest.mark.xfail(SDK_BEFORE_THE_FIX, strict=True,
                   reason="the fix is in cosmergon-agent >= 0.31.8; the lock still pins 0.31.7")
async def test_b1_the_sdk_says_a_refused_retry_of_a_word_has_no_known_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def at_once(_seconds: float) -> None:
        return None

    monkeypatch.setattr("cosmergon_agent.agent.asyncio.sleep", at_once)
    agent = _sdk_agent(["", "its port on this machine is not yours right now"],
                       [httpx.ReadTimeout("the answer did not come"), 200])
    with pytest.raises(CsgConnectionError):
        await brain.instruct(agent, "attack now", TABLE)
    await agent._client.aclose()
    assert _last_line()["answered"] is False and "sent" not in _last_line()


def test_b1_the_brain_asks_for_the_sdk_that_says_so() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    pin = next(d for d in project["dependencies"] if d.startswith("cosmergon-agent"))
    assert _version(pin.split(">=", 1)[1]) >= PINNED, pin


# ---------- B2 -----------------------------------------------------------------------------------


async def test_b2_a_move_whose_retry_was_refused_counts_and_is_written_down() -> None:
    home = brain.private_state()
    agent = Agent(_refused_retry())
    last = await brain.tick(agent, the_head(home))
    assert agent.sent and agent.sent[0][0] == "transfer_energy"
    assert last["ok"] is False and last["unknown"] is True, last
    assert spent(home) == 500.0, "not known is given: the whole limit counts"
    assert _last_line().get("action") == "transfer_energy", "the move stands in the journal"


async def test_b2_control_a_move_refused_before_its_first_try_ends_the_work() -> None:
    home = brain.private_state()
    agent = Agent(SendRefusedError("its port on this machine is not yours right now"))
    with pytest.raises(SendRefusedError):  # with_agent puts it to sleep with the reason
        await brain.tick(agent, the_head(home))
    assert spent(home) == 0.0


# ---------- K1 -----------------------------------------------------------------------------------


def test_k1_a_view_of_a_file_not_there_yet_is_under_its_lock() -> None:
    path = brain.state_dir() / "journal.jsonl"
    assert not path.exists()
    with files.lines_held(path):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        other = os.open(path, os.O_RDONLY)
        try:
            with pytest.raises(BlockingIOError):  # another open of it cannot take the lock
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(other)


def test_k1_control_the_bar_still_gets_an_empty_view_at_the_start() -> None:
    brain.show_journal()
    brain.show_talk()
    assert json.loads((brain.state_dir() / brain.BAR_JOURNAL).read_text()) == []
    assert json.loads((brain.state_dir() / brain.BAR_TALK).read_text()) == []
