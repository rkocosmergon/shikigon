"""cos20 #757: revenue and commission of the partner program in the bar.

The brain asks at most every quarter of an hour, writes numbers only (and the currency code of
the player's own config), stays quiet on any refusal, and an SDK without ``earn`` leaves the
cells out. The bar shows them beside the other figures; ``ShikigonModel.money`` formats them.
"""

from __future__ import annotations

import sys
import types
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
def sdk(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The SDK's ``earn`` and ``display_currency`` as small stand-ins (the lock pins 0.31.10)."""
    chosen = {"currency": "USD"}
    earn = types.ModuleType("cosmergon_agent.earn")

    def figures(partner: dict, currency: str, rates: dict | None) -> dict[str, Any]:
        cents = partner["umsatz"]["direkt_gesamt_cents"]
        rate = (rates or {}).get("rates", {}).get(currency, 1.0) if currency != "EUR" else 1.0
        return {"revenue": cents / 100 * rate, "commission": 39.6 * rate, "currency": currency}

    earn.figures = figures  # type: ignore[attr-defined]
    config = types.ModuleType("cosmergon_agent.config")
    config.display_currency = lambda: chosen["currency"]  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "cosmergon_agent.earn", earn)
    monkeypatch.setitem(sys.modules, "cosmergon_agent.config", config)
    import cosmergon_agent

    monkeypatch.setattr(cosmergon_agent, "earn", earn, raising=False)
    monkeypatch.setattr(brain, "EARN", {"at": 0.0, "figures": None})
    return chosen


async def test_numbers_only_in_the_chosen_currency(sdk: dict[str, Any]) -> None:
    figures = await brain.earn_now(_Agent())
    assert figures == {"revenue": pytest.approx(134.57), "commission": pytest.approx(44.41),
                       "currency": "USD"}


async def test_asked_at_most_every_quarter_hour(sdk: dict[str, Any]) -> None:
    agent = _Agent()
    await brain.earn_now(agent)
    await brain.earn_now(agent)
    assert agent.asked == 1


async def test_a_refusal_keeps_what_was_there(sdk: dict[str, Any]) -> None:
    first = await brain.earn_now(_Agent())
    brain.EARN["at"] = 0.0
    assert await brain.earn_now(_Agent(fails=True)) == first


async def test_program_off_shows_nothing(sdk: dict[str, Any]) -> None:
    assert await brain.earn_now(_Agent(partner=None)) is None


async def test_a_currency_that_is_no_code_falls_back(sdk: dict[str, Any]) -> None:
    sdk["currency"] = "<b>"
    figures = await brain.earn_now(_Agent())
    assert figures is not None and figures["currency"] == "EUR"


async def test_an_sdk_without_earn_leaves_the_cells_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "cosmergon_agent.earn", None)  # import fails
    monkeypatch.setattr(brain, "EARN", {"at": 0.0, "figures": None})
    assert await brain.earn_now(_Agent()) is None


def test_the_bar_has_the_two_cells_and_the_format() -> None:
    bar = (QML / "BarWidget.qml").read_text(encoding="utf-8")
    model = (QML / "ShikigonModel.js").read_text(encoding="utf-8")
    assert 'label: "revenue"' in bar and 'label: "commission"' in bar
    assert "root.status.earn" in bar and "Model.money(" in bar
    assert "function money(number, currency)" in model
