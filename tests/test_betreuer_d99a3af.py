"""Guards for the Betreuer-Durchgang d99a3af (cos20 #492).

The server checks its tick limit before the idempotency key, and ``/compass`` has no idempotency
key at all. So when the first try of a write was taken and its answer got lost (a timeout, a 5xx),
the retry got a 429. Up to cosmergon-agent 0.31.8 that was ``RateLimitError``:

B1 (Pflicht): ``play`` said the move was not made ("it already moved in this one"), without
``unknown``, so ``spend_budget`` counted nothing, against the README ("gets lost, the whole limit
counts") and the CHANGELOG of 0.1.15.

B2 (Pflicht): ``instruct`` wrote the word as "not taken, try again later", though the world may
have set the compass, against the README ("a click whose first try did go out, and whose retry was
refused, is said as no answer").

From 0.31.9 on the SDK raises ``ConnectionError`` with the ``RateLimitError`` as its cause. These
tests go through the real pinned SDK (``CosmergonAgent._request``), with the network replaced by a
transport that answers the first try with a timeout and the retry with 429 (K40).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "brain"))

import files
import shikigon_brain as brain
from cosmergon_agent import CosmergonAgent
from cosmergon_agent.exceptions import ConnectionError as CsgConnectionError
from cosmergon_agent.exceptions import RateLimitError
from test_brain import TABLE


@pytest.fixture(autouse=True)
def _private_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(files, "HELD_WAIT_S", 0.3)

    async def at_once(_seconds: float) -> None:
        return None

    monkeypatch.setattr("cosmergon_agent.agent.asyncio.sleep", at_once)


def _agent(answers: list[Any]) -> tuple[CosmergonAgent, list[str]]:
    """The real SDK client; each request takes the next answer: an exception or a status."""
    sent: list[str] = []
    agent = CosmergonAgent(api_key="AGENT-x:y", agent_id="a", base_url="https://world.invalid",
                           max_retries=2)

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(f"{request.method} {request.url.path}")
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return httpx.Response(answer, json={"error": {"message": "one action per tick"}})

    client = agent._create_client()
    client._transport = httpx.MockTransport(handler)
    agent._client = client
    return agent, sent


def _last_line() -> dict[str, Any]:
    return json.loads((brain.state_dir() / "journal.jsonl").read_text().splitlines()[-1])


# ---------- B1 -----------------------------------------------------------------------------------


async def test_b1_a_move_whose_retry_got_429_is_an_unknown_outcome_and_counts() -> None:
    agent, sent = _agent([httpx.ReadTimeout("the answer got lost"), 429])
    last = await brain.play(agent, "create_field", {"strict": True, "max_cost": 500.0})
    assert sent == ["POST /api/v1/agents/a/action"] * 2, "the first try left, the retry got 429"
    assert last["ok"] is False and last.get("unknown") is True, last
    advice = {"head": True, "choice": "create_field", "own": True, "limit": 500.0}
    assert brain.spend_budget(advice, last) == 500.0


async def test_b1_control_a_429_on_the_first_try_is_one_move_per_tick() -> None:
    agent, sent = _agent([429])
    last = await brain.play(agent, "create_field", {})
    assert sent == ["POST /api/v1/agents/a/action"]
    assert last["ok"] is False and "unknown" not in last, last
    assert "one move per tick" in last["why"]


# ---------- B2 -----------------------------------------------------------------------------------


async def test_b2_a_word_whose_retry_got_429_says_no_answer() -> None:
    agent, sent = _agent([httpx.ReadTimeout("the answer got lost"), 429])
    with pytest.raises(CsgConnectionError) as raised:
        await brain.instruct(agent, "attack now", TABLE)
    assert isinstance(raised.value.__cause__, RateLimitError)
    assert sent == ["POST /api/v1/agents/a/compass"] * 2
    line = _last_line()
    assert line["ok"] is False and line["answered"] is False, line
    assert "retry_refused" not in line and "sent" not in line, line


async def test_b2_control_a_429_on_the_first_try_is_not_taken() -> None:
    agent, sent = _agent([429])
    said = await brain.instruct(agent, "attack now", TABLE)
    assert sent == ["POST /api/v1/agents/a/compass"]
    assert said["ok"] is False and "too often" in said["why"], said
    line = _last_line()
    assert line["ok"] is False and "answered" not in line, line
