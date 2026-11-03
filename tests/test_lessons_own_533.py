"""The look back judges moves by what they booked, not by the window around them (cos20 #533).

Device 03.11.2026, 11:58Z: 81 of 100 moves placed cells, each booked -25 on average, and total
energy rose by 3.5 million, nearly all of it loot and items. The summary said "place cells:
+348,711" and listed "Best moves: place cells, +663,423". The lesson the model wrote from that:
"Building cells added over four million energy while waiting cost nearly two hundred thousand."
Red on 9f45234.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "brain"))

import advisor
import lessons
import shikigon_brain as brain


def _move(number: int, action: str = "place_cells", total: float = 1_000_000.0,
          **more: Any) -> dict[str, Any]:  # fmt: skip
    effect = {"free": -25.0, "held": 0.0, "items": 0.0}
    return {"tick": number, "action": action, "ok": True, "fields": 2, "energy": 500_000.0,
            "total": total, "effect": effect, **more}  # fmt: skip


def _with_loot() -> list[dict[str, Any]]:
    """Twenty moves; loot of 3 million lands behind the first ten, whatever they were."""
    def total(n: int) -> float:
        return 1_000_000.0 + (3_000_000.0 if n >= 10 else 0.0)

    return [_move(n, "wait" if n % 5 == 0 else "place_cells", total(n)) for n in range(20)]


def test_loot_in_the_window_makes_no_move_the_best() -> None:
    text = lessons.summary(_with_loot())
    assert "Best" not in text and "Worst" not in text
    assert "+3,000,000" not in text.split("Per kind of move")[1]


def test_each_kind_of_move_shows_only_what_it_booked() -> None:
    text = lessons.summary(_with_loot())
    assert "- place cells: 16 played, 0 refused, own effect -25 each, -400 in all" in text


def test_a_move_the_world_booked_nothing_for_says_so() -> None:
    moves = [{**_move(n, "wait"), "effect": None} for n in range(12)]
    assert "- wait: 12 played, 0 refused, nothing booked" in lessons.summary(moves)


def test_a_refused_move_books_nothing() -> None:
    moves = [_move(n, ok=n >= 2) for n in range(12)]
    moves[0]["effect"] = {"free": -999.0}
    moves[1]["effect"] = {"free": -999.0}
    assert "10 played, 2 refused, own effect -25 each, -250 in all" in lessons.summary(moves)


def test_the_change_of_all_causes_stands_apart_and_says_it_is_nobodys() -> None:
    text = lessons.summary(_with_loot())
    head, kinds = text.split("Per kind of move")
    assert "total energy 1,000,000 -> 4,000,000" in head
    assert "all causes" in head and "no move's effect" in head
    assert "total energy" not in kinds


def test_the_same_moves_show_balance_items_and_cells() -> None:
    moves = [_move(n, items=40_000_000.0 + n, cells=700 + n) for n in range(12)]
    moves[-1]["energy"] = 600_000.0
    text = lessons.summary(moves)
    assert "balance 500,000 -> 600,000" in text
    assert "items 40,000,000 -> 40,000,011" in text
    assert "living cells on its fields 700 -> 711" in text


def test_a_journal_before_these_numbers_leaves_them_out() -> None:
    text = lessons.summary([_move(n) for n in range(12)])
    assert "items" not in text.split("Per kind of move")[0]
    assert "living cells" not in text


def test_a_journal_without_the_total_names_the_balance_once() -> None:
    moves = [{k: v for k, v in _move(n).items() if k != "total"} for n in range(12)]
    text = lessons.summary(moves)
    assert "energy 500,000 -> 500,000" in text and "balance" not in text


def test_the_model_is_told_to_name_the_number() -> None:
    assert "names the number" in lessons.LESSON_SYSTEM
    assert "own effect" in lessons.LESSON_SYSTEM


def test_the_journal_keeps_cells_and_items_and_the_model_reads_them() -> None:
    state = SimpleNamespace(
        fields=[SimpleNamespace(active_cell_count=700), SimpleNamespace(active_cell_count=33)],
        ranking=SimpleNamespace(gesamtenergie_teile={"inventar": 47_438_030.4}),
    )
    held = brain.holdings(state)
    assert held == {"cells": 733, "items": 47_438_030.4}
    shown = advisor.move_for_model({"tick": 1, "action": "wait", "ok": True, **held})
    assert shown["cells"] == 733 and shown["items"] == 47_438_030.4


def test_what_it_cannot_read_it_does_not_keep() -> None:
    state = SimpleNamespace(fields=[SimpleNamespace(active_cell_count="a lot"), object()],
                            ranking=SimpleNamespace(gesamtenergie_teile="a lot"))  # fmt: skip
    assert brain.holdings(state) == {}
    assert brain.holdings(SimpleNamespace()) == {}
