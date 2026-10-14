"""Its moves in a player's words: one place for the journal, the panel and its advisor.

The world names a move by its function (``market_list``, ``place_cells``). Shown to a player
that reads like a log; handed to the model that writes its lessons, it came back the same way
(device 14.10.2026: "start_mission too often", although the model was asked for plain words:
it had been given the function names). Missions already had their own words (cos20 #451,
panel H); this does the same for every move.
"""

from __future__ import annotations

from typing import Any

# The moves the world offers (``GET /api/v1/game/info`` ``actions``, 14.10.2026). A move that is
# not here is said with its name, underscores as spaces.
ACTION_WORDS = {
    "abandon_field": "give up a field",
    "accept_contract": "accept a contract",
    "apply_to_contract": "apply for a contract",
    "buy_shield": "buy a shield",
    "burn_plague": "burn a plague",
    "cancel_mission": "call off a mission",
    "claim_field": "claim a field",
    "create_field": "create a field",
    "evolve": "evolve a field",
    "heal_holes": "heal holes in a field",
    "market_buy": "buy on the market",
    "market_cancel": "take back an offer",
    "market_list": "sell on the market",
    "pickup_drop": "pick up loot",
    "place_cells": "place cells",
    "propose_contract": "offer a contract",
    "propose_from_template": "offer a contract",
    "reject_contract": "turn down a contract",
    "set_compass": "set its compass",
    "shoot_spore": "shoot a spore",
    "start_mission": "start a mission",
    "transfer_energy": "send energy",
    "wait": "wait",
}


def move_words(entry: dict[str, Any]) -> str:
    """A move of its journal in words: ``mission: gather spores``, ``sell on the market``."""
    kind = str(entry.get("kind") or "")
    if kind:
        return "mission: " + kind.replace("_", " ")
    action = str(entry.get("action") or "")
    return ACTION_WORDS.get(action, action.replace("_", " "))
