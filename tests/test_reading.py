"""The head reads through the brain, and only what the brain wrote for it (cos20 #492, K28).

Marketplace review 20.10.2026 (#6429, #12388, #12061): text other agents chose reaches a
coding agent; a tool of its own lets an injected instruction read any file of the player. The
boundary is in ``reading``: these tests ask for what lies outside it and prove nothing comes back.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import lessons
import reading

KEY = "test-key-not-real-4711"  # ohne das Muster echter Schluessel (Whitelist-Check)
QUESTION = "Its last moves: … Answer with the three lines."


class Model:
    """Stands in for the head: one answer after the other; remembers every prompt."""

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.prompts: list[list[dict[str, str]]] = []

    async def __call__(self, messages: list[dict[str, str]], max_tokens: int) -> str:
        self.prompts.append([dict(m) for m in messages])
        return self.replies.pop(0) if self.replies else "LESSON: done"


def _home(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "state"
    home.mkdir()
    (home / "credentials.json").write_text(f'{{"api_key": "{KEY}"}}', encoding="utf-8")
    moves = [{"tick": n, "action": "evolve", "ok": True} for n in range(1, 61)]
    folder = lessons.review_folder(home, moves, "evolve 60x, energy +420", '{"rules": "…"}')
    return home, folder


def _ask(model: Model, folder: Path) -> str:
    messages = [{"role": "system", "content": "look back"}, {"role": "user", "content": QUESTION}]
    return asyncio.run(reading.mediated(model, folder)(messages, 100))


def _all_prompts(model: Model) -> str:
    return "\n".join(m["content"] for prompt in model.prompts for m in prompt)


def test_it_reads_the_lines_it_asked_for_and_then_answers(tmp_path: Path) -> None:
    _, folder = _home(tmp_path)
    model = Model("READ moves.txt 2-3", "LESSON: evolving worked")
    assert _ask(model, folder) == "LESSON: evolving worked"
    second = model.prompts[1][-1]["content"]
    assert second.startswith(QUESTION), "the question stays whole and first"
    assert "moves.txt, lines 2-3 of 60" in second
    assert '"tick": 2' in second and '"tick": 3' in second and '"tick": 4' not in second


def test_nothing_outside_its_three_files_comes_back(tmp_path: Path) -> None:
    """The abuse probe: paths, other names, the key file."""
    home, folder = _home(tmp_path)
    model = Model(
        "READ ../credentials.json",
        f"READ {home / 'credentials.json'}",
        "READ credentials.json 1-5",
        "LESSON: done",
    )
    _ask(model, folder)
    prompts = _all_prompts(model)
    assert KEY not in prompts, "the key reached the model"
    last = model.prompts[-1][-1]["content"]  # each refusal stands in every later prompt
    assert last.count("cannot be read. You may read only moves.txt, rated.txt, world.txt") == 3


def test_a_link_in_its_folder_is_not_followed(tmp_path: Path) -> None:
    home, folder = _home(tmp_path)
    (folder / "world.txt").unlink()
    (folder / "world.txt").symlink_to(home / "credentials.json")
    model = Model("READ world.txt", "LESSON: done")
    _ask(model, folder)
    assert KEY not in _all_prompts(model)
    assert "world.txt cannot be read" in model.prompts[1][-1]["content"]


def test_it_may_read_only_so_often_then_it_must_answer(tmp_path: Path) -> None:
    _, folder = _home(tmp_path)
    model = Model(*["READ moves.txt 1-40"] * 10)
    answer = _ask(model, folder)
    assert len(model.prompts) == reading.READS + 1
    assert "No more reading. Answer now" in model.prompts[-1][-1]["content"]
    assert answer == "READ moves.txt 1-40", "a last READ is no lesson; lessons.read_lesson drops it"


def test_a_slice_is_bounded(tmp_path: Path) -> None:
    _, folder = _home(tmp_path)
    text = reading.serve(folder, "moves.txt", 1, 10_000)
    assert text.startswith(f"moves.txt, lines 1-{reading.SLICE_LINES} of 60")
    assert len(text) <= reading.SLICE_CHARS + 100


def test_the_note_names_the_files_and_their_lines(tmp_path: Path) -> None:
    _, folder = _home(tmp_path)
    note = reading.note(folder)
    assert "moves.txt (60 lines" in note and "rated.txt (1 lines" in note
    assert "READ <file> <first>-<last>" in note


def test_a_lesson_is_not_taken_for_a_request() -> None:
    assert reading.asked("LESSON: READ moves.txt 1-2 was useful") is None
    assert reading.asked("`READ world.txt 3-9`") == ("world.txt", 3, 9)
    assert reading.asked("read rated.txt") == ("rated.txt", 1, reading.SLICE_LINES)


async def test_the_brain_lets_a_head_read_through_it(monkeypatch: Any) -> None:
    """The wiring: a head's look back goes through ``reading.mediated``, whatever it is."""
    import shikigon_brain as brain
    from test_advisor import CONF, _state
    from test_lessons import GOOD, _moves, _write

    _write(_moves(40))
    monkeypatch.setattr(brain, "world_rules", lambda cfg, get: '{"rules": "w"}')
    model = Model("READ moves.txt 1-2", GOOD)
    cfg = {"brain": {"advisor": {**CONF, "advise": "head", "review_every": 5}}}
    entry = await brain.learn(cfg, _state(), force=True, send=model)
    assert entry is not None and entry["ok"], entry
    assert "READ <file> <first>-<last>" in model.prompts[0][-1]["content"]
    assert "moves.txt, lines 1-2 of" in model.prompts[1][-1]["content"]
