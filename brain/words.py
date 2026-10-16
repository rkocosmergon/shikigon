"""Its moves in a player's words: one place for the journal, the panel and its advisor.

The world names a move by its function (``market_list``, ``place_cells``). Shown to a player
that reads like a log; handed to the model that writes its lessons, it came back the same way
(device 14.10.2026: "start_mission too often", although the model was asked for plain words:
it had been given the function names). Missions already had their own words (cos20 #451,
panel H); this does the same for every move.

The words themselves come from the SDK (``cosmergon_agent.action.action_word``, since 0.27.12,
cos20 #486), the same the terminal's log uses, so the bar and the terminal never say a move
two ways. A move the SDK does not know is said with its name, underscores as spaces.
"""

from __future__ import annotations

from typing import Any

from cosmergon_agent.action import action_word


def action_words(action: str) -> str:
    """A move by its name in words: ``market_list`` → ``sell on the market``."""
    return action_word(action)


def move_words(entry: dict[str, Any]) -> str:
    """A move of its journal in words: ``mission: gather spores``, ``sell on the market``."""
    kind = str(entry.get("kind") or "")
    if kind:
        return "mission: " + kind.replace("_", " ")
    return action_words(str(entry.get("action") or ""))
