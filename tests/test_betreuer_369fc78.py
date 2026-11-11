"""Guards for the Betreuer-Durchgang 369fc78 (cos20 #492, B1).

When the first try of a write went out and its answer got lost, and the last retry was answered
with 400 or more (an empty 502 or 504 of a proxy while the server restarts, the server's own JSON
5xx), cosmergon-agent up to 0.31.9 returned that last answer. ``set_compass`` turned it into
``{"error": …}``, so ``instruct`` wrote "not taken, try again later"; ``act`` turned a JSON 5xx
into a refused move, which ``play`` counted as refused and the budget not at all. Both against the
CHANGELOG of 0.1.15 ("a word or a move whose first try reached the world is never called "not
sent" or "not taken"").

From 0.31.10 on the SDK raises ``ConnectionError`` for it. These tests go through the real pinned
SDK (``CosmergonAgent._request``), with the network replaced by a transport (K40).
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
from test_brain import TABLE

SERVER_ERROR = {"error": {"code": 500, "message": "boom", "type": "http_error"}}
LAST = [(502, b""), (504, b""), (500, SERVER_ERROR)]


@pytest.fixture(autouse=True)
def _private_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(files, "HELD_WAIT_S", 0.3)

    async def at_once(_seconds: float) -> None:
        return None

    monkeypatch.setattr("cosmergon_agent.agent.asyncio.sleep", at_once)


def _agent(answers: list[Any]) -> tuple[CosmergonAgent, list[str]]:
    """The real SDK client; each request takes the next answer: an exception or (status, body)."""
    sent: list[str] = []
    agent = CosmergonAgent(api_key="AGENT-x:y", agent_id="a", base_url="https://world.invalid",
                           max_retries=2)

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(f"{request.method} {request.url.path}")
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        status, body = answer
        if isinstance(body, bytes):
            return httpx.Response(status, content=body)
        return httpx.Response(status, json=body)

    client = agent._create_client()
    client._transport = httpx.MockTransport(handler)
    agent._client = client
    return agent, sent


def _last_line() -> dict[str, Any]:
    return json.loads((brain.state_dir() / "journal.jsonl").read_text().splitlines()[-1])


@pytest.mark.parametrize("last", LAST, ids=lambda a: str(a[0]))
async def test_b1_a_move_whose_last_retry_got_an_error_is_an_unknown_outcome_and_counts(
    last: tuple[int, Any],
) -> None:
    agent, sent = _agent([httpx.ReadTimeout("the answer got lost"), (503, b""), last])
    played = await brain.play(agent, "create_field", {"strict": True, "max_cost": 500.0})
    assert sent == ["POST /api/v1/agents/a/action"] * 3
    assert played["ok"] is False and played.get("unknown") is True, played
    assert not played.get("refused"), played
    advice = {"head": True, "choice": "create_field", "own": True, "limit": 500.0}
    assert brain.spend_budget(advice, played) == 500.0


@pytest.mark.parametrize("last", LAST, ids=lambda a: str(a[0]))
async def test_b1_a_word_whose_last_retry_got_an_error_says_no_answer(
    last: tuple[int, Any],
) -> None:
    agent, sent = _agent([httpx.ReadTimeout("the answer got lost"), (503, b""), last])
    with pytest.raises(CsgConnectionError):
        await brain.instruct(agent, "attack now", TABLE)
    assert sent == ["POST /api/v1/agents/a/compass"] * 3
    line = _last_line()
    assert line["ok"] is False and line["answered"] is False, line


async def test_b1_a_word_whose_every_try_got_a_server_error_says_no_answer() -> None:
    """Each 5xx try went out as well: three in a row are not a word the world refused. The two
    move cases with an empty 502/504 were right before 0.31.10 already (``act`` reads an answer
    without JSON as unknown); the JSON 5xx was not."""
    agent, sent = _agent([(503, b""), (503, b""), (502, b"")])
    with pytest.raises(CsgConnectionError):
        await brain.instruct(agent, "attack now", TABLE)
    assert sent == ["POST /api/v1/agents/a/compass"] * 3
