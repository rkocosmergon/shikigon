"""cos20 #757: revenue and commission of the partner program in the bar.

The game loop asks at most every quarter of an hour (``earn_refresh``), keeps numbers only (and
the currency code of the player's own config) in a file a move reads (``earn_kept``), stays quiet
on any refusal, and an SDK without ``earn`` leaves the cells out. The bar shows them beside the
other figures; ``ShikigonModel.money`` formats them.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
import shikigon_brain as brain

QML = Path(__file__).resolve().parent.parent / "qml"
PARTNER = {"umsatz": {"direkt_gesamt_cents": 12_000}, "provision": {"wartezeit_cents": 3_960}}


class _Agent:
    def __init__(self, partner: Any = PARTNER, fails: bool = False) -> None:
        self.partner_answer, self.fails, self.asked = partner, fails, 0

    async def partner(self) -> Any:
        self.asked += 1
        if self.fails:
            raise ConnectionError("down")
        return self.partner_answer

    async def exchange_rates(self) -> dict[str, Any]:
        return {"base": "EUR", "date": "2026-11-13", "rates": {"USD": 1.1214}}


@pytest.fixture
def sdk(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    """The real ``cosmergon_agent.earn`` and ``config`` of the pinned SDK (0.32.1), with the
    currency set the way the player sets it (``[display] currency`` in a config.toml of its own).

    Stand-ins stood here while the lock pinned 0.31.10, which had neither; a test on a stand-in
    does not follow the real dependency (threat model K40).
    """
    from cosmergon_agent import config

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.toml")
    assert config.set_display_currency("USD") is True
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    return config


async def asked(agent: Any) -> Any:
    """What the bar gets after the game loop asked ``agent`` once."""
    await brain.earn_refresh(agent)
    return brain.earn_kept()


def a_quarter_later() -> None:
    """The file says the last question is a quarter of an hour old."""
    path = brain.state_dir() / brain.EARN_FILE
    kept = brain.files.read_object(path)
    brain.files.write_json(path, {**kept, "at": kept["at"] - brain.EARN_EVERY_S})


async def test_numbers_only_in_the_chosen_currency(sdk: Any) -> None:
    figures = await asked(_Agent())
    assert figures == {"revenue": pytest.approx(134.57), "commission": pytest.approx(44.41),
                       "currency": "USD"}


async def test_asked_at_most_every_quarter_hour(sdk: Any) -> None:
    agent = _Agent()
    await brain.earn_refresh(agent)
    await brain.earn_refresh(agent)
    assert agent.asked == 1
    a_quarter_later()
    await brain.earn_refresh(agent)
    assert agent.asked == 2


async def test_a_refusal_keeps_what_was_there(sdk: Any) -> None:
    first = await asked(_Agent())
    a_quarter_later()
    down = _Agent(fails=True)
    assert await asked(down) == first and first is not None and down.asked == 1


async def test_program_off_shows_nothing(sdk: Any) -> None:
    assert await asked(_Agent()) is not None
    a_quarter_later()
    assert await asked(_Agent(partner=None)) is None


@pytest.mark.parametrize("bad", ["<b>", "ΑΒΓ", "us"])
async def test_a_currency_that_is_no_code_falls_back(
    sdk: Any, monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    """Only three Latin capitals pass, the bar's own rule (``^[A-Z]{3}$``): Greek letters are
    letters to ``isalpha`` but no code the bar shows (threat model K46, one reading)."""
    from cosmergon_agent import earn

    real = earn.figures
    monkeypatch.setattr(earn, "figures", lambda p, c, r: {**real(p, "EUR", r), "currency": bad})
    figures = await asked(_Agent())
    assert figures is not None and figures["currency"] == "EUR"


async def test_an_sdk_without_earn_leaves_the_cells_out(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setitem(sys.modules, "cosmergon_agent.earn", None)  # import fails
    agent = _Agent()
    assert await asked(agent) is None and agent.asked == 0


def test_the_bar_has_the_two_cells_and_the_format() -> None:
    bar = (QML / "BarWidget.qml").read_text(encoding="utf-8")
    model = (QML / "ShikigonModel.js").read_text(encoding="utf-8")
    assert 'label: "revenue"' in bar and 'label: "commission"' in bar
    assert "root.status.earn" in bar and "Model.money(" in bar
    assert "function money(number, currency)" in model
