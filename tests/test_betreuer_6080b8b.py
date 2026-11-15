"""Betreuer pass 6080b8b (cos20 #492): an answer of the world that is not in its form shows no
partner figures (B1), and the README's list of what a picked model receives names where the total
energy lies (B2). Kür: the new key before the old files go (K1), ``forget`` and the game loop take
turns (K2), the README row of ``status.json`` (K3), the currency only read (K5); K4 in
``tests/qml/tst_model.qml``.

B1 runs through the pinned SDK itself, its HTTP door included (``CosmergonAgent`` over
``httpx.MockTransport``): the SDK turns an answer of 200 that is no JSON object, or holds a number
that is not finite, into ``{}``, which no stand-in for ``agent.partner`` showed (threat model K40).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import advisor
import httpx
import pytest
import shikigon_brain as brain
from cosmergon_agent import CosmergonAgent, config
from cosmergon_agent.state import GameState
from test_betreuer_1d81304 import _holder

README = Path(__file__).resolve().parent.parent / "README.md"
GOOD = (b'{"umsatz": {"direkt_gesamt_cents": 12000}, "provision": {"wartezeit_cents": 3960, '
        b'"auszahlbar_cents": 0, "ausgezahlt_cents": 0, "ohne_auszahlung_cents": 0}, '
        b'"kanaele": []}')
RATES = b'{"base": "EUR", "date": "2026-11-13", "rates": {"USD": 1.1214}}'


@pytest.fixture(autouse=True)
def _home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
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


# ---------- Kür K1–K5 of the same pass ----------------------------------------------------------

CFG = {"world": {"base_url": "https://cosmergon.com"}}
OLD = {"agent_id": "old", "api_key": "AGENT-old:k", "world": "https://cosmergon.com:443"}


class _Born:
    status_code = 200

    def json(self) -> dict[str, str]:
        return {"agent_id": "new", "api_key": "AGENT-new:k"}


def _old_agent_lives_here() -> Path:
    home = brain.state_dir()
    home.mkdir(parents=True, exist_ok=True)
    brain.write_private(brain.credentials_path(), OLD)
    (home / "journal.jsonl").write_text('{"at": 1, "action": "wait"}\n')
    (home / "lessons.json").write_text("[]")
    return home


def test_k1_a_key_that_cannot_be_written_leaves_the_old_agents_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``register --new`` writes the new key before it clears what the old agent left."""
    home = _old_agent_lives_here()

    def full_disk(path: Path, data: Any) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(brain, "write_private", full_disk)
    with pytest.raises(OSError):
        brain.register(CFG, lambda *a, **k: _Born(), replace=True)
    assert (home / "journal.jsonl").exists() and (home / "lessons.json").exists()


def test_k1_control_a_new_agent_starts_without_the_old_ones_files() -> None:
    home = _old_agent_lives_here()
    brain.register(CFG, lambda *a, **k: _Born(), replace=True)
    assert not (home / "journal.jsonl").exists() and not (home / "lessons.json").exists()
    assert brain.load_credentials() == {**OLD, "agent_id": "new", "api_key": "AGENT-new:k",
                                        "expires_at": ""}


def test_k2_forget_sends_nothing_while_a_move_is_under_way() -> None:
    home = _old_agent_lives_here()
    sent: list[str] = []
    child = _holder("one_move", 30)
    try:
        said = brain.forget(CFG, lambda url, **k: sent.append(url))
    finally:
        child.kill()
        child.wait()
    assert said["ok"] is False and brain.ANOTHER_MOVE in said["why"], said
    assert sent == [] and brain.credentials_path().exists() and (home / "journal.jsonl").exists()


def test_k2_control_forget_without_a_move_removes_everything() -> None:
    class Deleted:
        status_code = 204

    _old_agent_lives_here()
    assert brain.forget(CFG, lambda url, **k: Deleted())["ok"] is True
    assert not brain.state_dir().exists()


async def test_k2_the_game_loop_ends_once_the_key_file_is_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``forget`` removes the folder while the game loop plays: the loop ends and makes nothing
    anew (until 0.1.17 it asked for the partner figures and wrote them into a new folder)."""
    _old_agent_lives_here()
    ticks: list[int] = []

    async def one_tick(*args: Any, **kwargs: Any) -> dict[str, Any]:
        ticks.append(1)
        if len(ticks) > 1:
            raise RuntimeError("the loop went on after the key file was gone")
        shutil.rmtree(brain.state_dir())  # forget, between the move and what follows it
        return {"action": "evolve", "ok": True, "why": ""}

    monkeypatch.setattr(brain, "guarded_tick", one_tick)
    monkeypatch.setattr(brain, "pause", lambda *a, **k: 0.0)
    await brain.run(CFG, None, object())
    assert ticks == [1] and not brain.state_dir().exists()


@pytest.mark.no_state_folder  # forget has run: no folder (c6f552f: ``one_move`` makes none)
async def test_k2_a_move_of_the_loop_after_forget_makes_no_folder() -> None:
    last = await brain.tick(None, None, until_gone=True)
    assert last["gone"] is True and not brain.state_dir().exists()


@pytest.mark.no_state_folder  # the lock finds no folder (``files.StateGone``) and makes none
async def test_k2_forget_between_the_look_and_the_lock_leaves_no_folder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    looks = iter([False, True])  # there before the lock, gone once the lock is held
    monkeypatch.setattr(brain, "gone", lambda: next(looks))
    last = await brain.tick(None, None, until_gone=True)
    assert last["gone"] is True and not brain.state_dir().exists()


def test_k3_the_readme_says_status_json_holds_the_last_move_and_the_partner_figures() -> None:
    text = " ".join(README.read_text(encoding="utf-8").split())
    row = next(line for line in text.split("| `~/.local/state/shikigon/") if
               line.startswith("status.json"))
    assert "(`last`" in row and "(`earn`" in row


def _config(text: str) -> Path:
    path = Path(config.CONFIG_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.chmod(path, 0o644)
    return path


async def test_k5_the_currency_is_read_and_the_sdk_config_is_left_as_it_is() -> None:
    path = _config('[display]\ncurrency = "USD"\n')
    figures = await _asked(GOOD)
    assert figures is not None and figures["currency"] == "USD"
    assert os.stat(path).st_mode & 0o777 == 0o644, "not changed by Shikigon"


async def test_k5_a_display_that_is_no_table_means_euros() -> None:
    _config('display = "USD"\n')
    figures = await _asked(GOOD)
    assert figures == {"revenue": 120.0, "commission": 39.6, "currency": "EUR"}


@pytest.mark.parametrize("given", ['[display]\ncurrency = "ΑΒΓ"\n', '[display]\ncurrency = 5\n',
                                   "not toml [", '[display]\ncurrency = "usdx"\n'])
async def test_k5_a_currency_in_another_form_means_euros(given: str) -> None:
    _config(given)
    figures = await _asked(GOOD)
    assert figures is not None and figures["currency"] == "EUR"
