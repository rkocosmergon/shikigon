"""Betreuer pass 6080b8b (cos20 #492): an answer of the world that is not in its form shows no
partner figures (B1), and the README's list of what a picked model receives names where the total
energy lies (B2).

B1 runs through the pinned SDK itself, its HTTP door included (``CosmergonAgent`` over
``httpx.MockTransport``): the SDK turns an answer of 200 that is no JSON object, or holds a number
that is not finite, into ``{}``, which no stand-in for ``agent.partner`` showed (threat model K40).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import advisor
import httpx
import pytest
import shikigon_brain as brain
from cosmergon_agent import CosmergonAgent
from cosmergon_agent.state import GameState

README = Path(__file__).resolve().parent.parent / "README.md"
GOOD = (b'{"umsatz": {"direkt_gesamt_cents": 12000}, "provision": {"wartezeit_cents": 3960, '
        b'"auszahlbar_cents": 0, "ausgezahlt_cents": 0, "ohne_auszahlung_cents": 0}, '
        b'"kanaele": []}')
RATES = b'{"base": "EUR", "date": "2026-11-13", "rates": {"USD": 1.1214}}'


@pytest.fixture(autouse=True)
def _home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from cosmergon_agent import config

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    async def at_once(_seconds: float) -> None:
        return None

    monkeypatch.setattr("cosmergon_agent.agent.asyncio.sleep", at_once)


def _world(partner: bytes) -> CosmergonAgent:
    """The real SDK client against a world whose partner overview is ``partner``, status 200."""
    agent = CosmergonAgent(api_key="AGENT-x:y", agent_id="a", base_url="https://world.invalid")

    def handler(request: httpx.Request) -> httpx.Response:
        body = partner if request.url.path.endswith("/partner") else RATES
        return httpx.Response(200, content=body, headers={"content-type": "application/json"})

    client = agent._create_client()
    client._transport = httpx.MockTransport(handler)
    agent._client = client
    return agent


async def _asked(partner: bytes) -> Any:
    await brain.earn_refresh(_world(partner))
    return brain.earn_kept()


def _a_quarter_later() -> None:
    path = brain.state_dir() / brain.EARN_FILE
    kept = brain.files.read_object(path)
    brain.files.write_json(path, {**kept, "at": kept["at"] - brain.EARN_EVERY_S})


async def test_an_answer_in_its_form_shows_the_figures() -> None:
    """The control: the same way, with the answer the world really gives."""
    assert await _asked(GOOD) == {"revenue": 120.0, "commission": 39.6, "currency": "EUR"}


NOT_IN_FORM = {
    "a number that is not finite": GOOD.replace(b"12000", b"1e999"),
    "NaN": GOOD.replace(b"12000", b"NaN"),
    "a page, no JSON": b"<html><body>welcome</body></html>",
    "a list": b"[1, 2, 3]",
    "an empty object": b"{}",
    "a word for the revenue": GOOD.replace(b"12000", b'"viel"'),
    "digits as text": GOOD.replace(b"12000", b'"12000"'),
    "a yes for the revenue": GOOD.replace(b"12000", b"true"),
    "a word in the commission": GOOD.replace(b"3960", b'"viel"'),
    "a list for the commission": GOOD.replace(b'{"wartezeit_cents"', b'[{"wartezeit_cents"')
                                     .replace(b'0}, "kanaele"', b'0}], "kanaele"'),
    "a word for a channel's energy": GOOD.replace(b'"kanaele": []',
                                                  b'"kanaele": [{"energie": "viel"}]'),
}  # fmt: skip


@pytest.mark.parametrize("partner", NOT_IN_FORM.values(), ids=NOT_IN_FORM.keys())
async def test_an_answer_not_in_its_form_shows_no_figures(partner: bytes) -> None:
    """Neither 0 and 0 nor the figures kept before: the two cells stay away (CHANGELOG)."""
    assert await _asked(GOOD) is not None
    _a_quarter_later()
    assert await _asked(partner) is None


@pytest.mark.parametrize("partner", NOT_IN_FORM.values(), ids=NOT_IN_FORM.keys())
async def test_an_answer_not_in_its_form_shows_none_from_the_start(partner: bytes) -> None:
    assert await _asked(partner) is None


def test_the_readme_names_where_the_total_energy_lies_for_every_model() -> None:
    """``facts`` goes into every prompt and says where the total energy lies; the README's first
    list (what a picked model receives, before the part on an agent that has taken over) names
    it."""
    state = GameState.from_api({
        "agent_id": "a", "energy": 10.0, "tick": 1,
        "ranking": {"gesamtenergie": 100.0,
                    "gesamtenergie_teile": {"konto": 10.0, "felder": 90.0}},
    })  # fmt: skip
    assert "Of that: on account 10, its fields 90" in advisor.facts(state)
    text = " ".join(README.read_text(encoding="utf-8").split())
    sends = text[text.index("## What it sends"):]
    every_model = sends[: sends.index("When your agent has taken over")]
    assert "its own total energy and where that lies" in every_model
