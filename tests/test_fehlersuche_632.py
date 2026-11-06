"""Guards for the shikigon finding of the fault search of 06.11.2026 (cos20 #632).

The notes every question to the advisor starts with (``advisor_notes.md``) told it how the
Shikigon learns and who chooses its moves, in words the code had left: the look back as "its
energy before and after each move" (replaced in cos20 #533 by what each move itself booked, and
the change from all causes apart), and its moves as "chosen by a rule tree" also when the
player's agent takes over. The advisor then explains the Shikigon wrongly to its player. The
second time after b4ba36a (the key's place); that guard bound one sentence, these bind the notes
to what the look back and the head do.

Red on ebf251a, the positive controls green.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "brain"))

import advisor
import files
import lessons
import shikigon_brain

NOTES = ROOT / "brain" / "advisor_notes.md"
README = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())


def _line(start: str) -> str:
    return next(line for line in NOTES.read_text(encoding="utf-8").splitlines()
                if line.startswith(start))  # fmt: skip


class _Agent:
    async def refresh_state(self) -> None:
        return None


async def _context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """What ``ask`` gives the advisor; the advisor itself is replaced."""
    seen: dict[str, str] = {}

    async def answer(conf: Any, folder: Any, question: str, context: str, *rest: Any) -> dict:
        seen["context"] = context
        return {"q": question, "a": "ok", "ok": True}

    async def now(state: Any) -> str:
        return "(state)"

    monkeypatch.setattr(files, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(advisor, "answer", answer)
    monkeypatch.setattr(shikigon_brain, "right_now", now)
    await shikigon_brain.ask(_Agent(), {}, "How do you learn?", rules="")
    return seen["context"]


def _move(number: int, action: str) -> dict[str, Any]:
    effect = {"free": -25.0, "held": 0.0, "items": 0.0}
    return {"tick": number, "action": action, "ok": True, "fields": 2, "energy": 500_000.0,
            "total": 1_000_000.0 + number, "effect": effect}  # fmt: skip


# --- the look back --------------------------------------------------------------------------------


def test_positive_control_the_look_back_says_what_the_notes_must_say() -> None:
    """The look back itself, as the advisor gets it: own effect per kind, the rest apart."""
    told = lessons.summary([_move(n, "wait" if n % 5 else "place_cells") for n in range(20)])
    assert "what the move itself booked" in told
    assert "all causes together" in told
    assert "never credit or blame a move with it" in told
    assert "what the move itself booked" in README
    assert "from all causes together" in README


def test_the_notes_tell_the_advisor_the_look_back_the_code_makes() -> None:
    learns = _line("- It learns from its own moves:")
    assert f"every {lessons.LEARN_EVERY} moves" in learns, learns
    assert "what the move itself booked" in learns, learns
    assert "from all causes together" in learns, learns
    for left in ("before and after each move", "ten moves later", "after each move"):
        assert left not in learns, learns


async def test_the_advisor_gets_that_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = await _context(tmp_path, monkeypatch)
    assert context.startswith("Notes:\n")
    assert _line("- It learns from its own moves:") in context
    assert "before and after each move" not in context


def test_the_lessons_docstring_says_the_same() -> None:
    head = (lessons.__doc__ or "").split("\n\n")[1]
    flat = " ".join(head.split())
    assert "what the move itself booked" in flat, flat
    assert "ten moves later" not in flat, flat


# --- who chooses its moves ------------------------------------------------------------------------


def test_positive_control_the_readme_lets_the_agent_choose_every_move() -> None:
    assert "### Your agent takes over" in (ROOT / "README.md").read_text(encoding="utf-8")
    assert "and it chooses one move itself" in README


def test_the_notes_say_the_players_agent_can_choose_every_move() -> None:
    first = _line("- A Shikigon is one Cosmergon agent")
    model = _line("- A model can advise it on a move")
    assert "takes over" in first or "take over" in first, first
    assert "chooses every move" in first or "choose every move" in model, (first, model)
    assert "only a move the world offers" in model, model


# --- the ways in ----------------------------------------------------------------------------------


def test_positive_control_the_readme_names_the_ways_in() -> None:
    assert "to a Solo plan" in README
    assert "today that runs over the API, not from Shikigon" in README


def test_the_notes_name_the_ways_in_as_the_readme_does() -> None:
    games = _line("- Two games, both count the same:")
    assert "link to a Solo plan" in games, games
    assert "not from Shikigon" in games, games
