"""What a Shikigon learns from its own moves, on your machine (cos20 #451, step 2).

Every ``LEARN_EVERY`` moves the brain looks back on them in its journal: per move, the
energy it had then and ten moves later. The numbers go to the advisor you picked, which
writes three short texts: what it learned, what to avoid, what to do more of. The newest
goes along on every later advice and every answer; the journal shows that it learned.

Nothing of this reaches the Cosmergon server: the journal is the history, the advisor is
yours. Measured why (14.10.2026): the server keeps 100 memory events of a free agent and
drops its decisions first, so it holds no rated move of a Shikigon to learn from; and
rating every move of a thousand Shikigons would ask about a hundred times what its hourly
job does. The texts keep the server's lengths (``ReflectionWriteRequest``) so they could be
posted there one day.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

Send = Callable[[list[dict[str, str]], int], Awaitable[str]]

LEARN_EVERY = 100  # moves between two lessons; at one move a tick about two hours
HORIZON = 10  # a move is judged by the energy ten moves later (the server's "short" horizon)
# ``learn`` on demand needs this many: a lesson from two or three judged moves read as a
# rule on the device (14.10.2026: "avoid missions" from two of them)
FORCED_MIN = HORIZON + 20
TAIL = 400  # journal lines read from its end: a hundred moves and what came between them
KEPT = 3  # lessons kept in lessons.json, newest last
SHOWN = 5  # best and worst moves the advisor is told
LESSON_TOKENS = 450  # room for the three texts in JSON
LESSONS_FILE = "lessons.json"
JOURNAL_FILE = "journal.jsonl"
# The server's bounds for a lesson (cos20 backend, ReflectionWriteRequest).
BOUNDS = {"lessons": (100, 500), "avoid": (50, 200), "double_down": (50, 200)}

# The two short parts are asked for in at most 90 characters: the panel shows each in two lines
# (device 14.10.2026: 131 and 173 characters ended in "…"). The bounds above stay the server's.
# Three labelled lines, not JSON: a small local model broke the JSON in two of six lessons on
# the device (14.10.2026, `"avoid","Do not …"`, a comma for the colon). A line needs no quotes.
# An answer in JSON is still read.
LABELS = {"LESSON": "lessons", "LESS": "avoid", "MORE": "double_down"}
LESSON_SYSTEM = (
    "You look back on the last moves of a Cosmergon agent, an economy game built on "
    "Conway's Game of Life, and say what it should learn from them. Use only the numbers "
    "you are given; never invent rules, prices or moves. Its player reads what you write: "
    "name moves in plain words (selling on the market, starting a mission, evolving), never "
    "by their function names. Answer in English with exactly three lines and nothing else:\n"
    "LESSON: <100 to 500 characters: what worked and what did not>\n"
    "LESS: <one short sentence, 50 to 90 characters: what to do less>\n"
    "MORE: <one short sentence, 50 to 90 characters: what to do more>"
)


def journal_tail(home: Path, count: int = TAIL) -> list[dict[str, Any]]:
    """The last ``count`` entries of the journal, oldest first, read from its end."""
    path = home / JOURNAL_FILE
    if not path.exists():
        return []
    with path.open("rb") as handle:
        handle.seek(0, 2)
        end = handle.tell()
        block = min(end, 300 * count)  # a journal line is well under 300 bytes
        handle.seek(end - block)
        lines = handle.read().decode("utf-8", "replace").splitlines()
    if block < end:
        lines = lines[1:]  # the first line may be cut
    entries = []
    for line in lines[-count:]:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def moves_since_lesson(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The moves after the last lesson (or attempt at one) that carry their energy."""
    start = 0
    for number, entry in enumerate(entries):
        if "learned" in entry:
            start = number + 1
    return [entry for entry in entries[start:] if "action" in entry and "energy" in entry]


def due(entries: list[dict[str, Any]]) -> bool:
    return len(moves_since_lesson(entries)) >= LEARN_EVERY


def rated(moves: list[dict[str, Any]]) -> list[tuple[dict[str, Any], float]]:
    """Each move with the energy change until ``HORIZON`` moves later; the last ones have none."""
    return [
        (move, float(moves[number + HORIZON]["energy"]) - float(move["energy"]))
        for number, move in enumerate(moves[:-HORIZON] if len(moves) > HORIZON else [])
    ]


def _signed(value: float) -> str:
    return f"{value:+,.0f}"


def summary(moves: list[dict[str, Any]]) -> str:
    """The moves in numbers, for the advisor: per action, then the best and worst ones.

    A refused move did nothing, so the energy after it is the world's, not its own: it is
    counted as refused and left out of every average and of the best and worst (seen on
    the device, 14.10.2026: a refused move stood first among the best).
    """
    judged = rated(moves)
    played = [(move, change) for move, change in judged if move.get("ok") is not False]
    if not judged:
        return ""
    overall = _signed(sum(c for _, c in played) / len(played)) if played else "-"
    lines = [
        (f"{len(moves)} moves, energy {moves[0]['energy']:,.0f} -> {moves[-1]['energy']:,.0f}, "
         f"fields {moves[0].get('fields', '?')} -> {moves[-1].get('fields', '?')}."),
        f"Energy change in the {HORIZON} moves after a played move: {overall} on average.",
        "Per kind of move (played, refused, the same average for that kind):",
    ]
    from words import move_words  # the player's words, so the lesson comes back in them

    per_action: dict[str, list[tuple[dict[str, Any], float]]] = {}
    for move, change in judged:
        per_action.setdefault(move_words(move), []).append((move, change))
    for action, rows in sorted(per_action.items(), key=lambda item: -len(item[1])):
        changes = [change for move, change in rows if move.get("ok") is not False]
        mean = _signed(sum(changes) / len(changes)) if changes else "-"
        lines.append(f"- {action}: {len(changes)} played, {len(rows) - len(changes)} refused, {mean}")
    ordered = sorted(played, key=lambda row: row[1])
    shown = min(SHOWN, len(ordered) // 2)  # best and worst never share a move
    for title, rows in (("Best", ordered[::-1][:shown]), ("Worst", ordered[:shown])):
        lines.append(f"{title} moves:")
        lines.extend(
            f"- tick {move.get('tick', '?')}: {move_words(move)}, {_signed(change)}"
            for move, change in rows
        )
    return "\n".join(lines)


def lesson_messages(facts: str, numbers: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": LESSON_SYSTEM},
        {"role": "user", "content": f"The agent now:\n{facts}\n\nIts last moves:\n{numbers}"},
    ]


def _fit(text: str, longest: int) -> str:
    """Cut at the last sentence end that fits; else at the last word, marked with "…".

    The player reads these in the panel (cos20 #451, panel 14.10. I): a lesson that stopped
    mid-sentence ("…an average depletion of two") read like a fault, not like a cut.
    """
    text = " ".join(text.split())
    if len(text) <= longest:
        return text
    window = text[:longest]
    end = max(window.rfind(mark) for mark in (". ", "! ", "? "))
    if end >= longest // 2:
        return window[: end + 1]
    word = text[: longest - 1].rsplit(" ", 1)[0].rstrip(",;:.") or text[: longest - 1]
    return word + "…"


# A label where a line or a sentence begins; the text runs to the next label. On the device one
# answer in three put all three on one line (14.10.2026).
_LABEL = re.compile(r"(?:^|(?<=[\s|.]))[*_#-]*(LESSON|LESS|MORE)[*_]*\s*:[*_]*", re.IGNORECASE)


def _labelled(text: str) -> tuple[dict[str, str] | None, str]:
    """The texts after ``LESSON:``/``LESS:``/``MORE:``; ``None`` if there is no label."""
    text = text or ""
    marks = list(_LABEL.finditer(text))
    if not marks:
        return None, ""
    found = {}
    for here, after in zip(marks, [*marks[1:], None], strict=True):
        value = text[here.end() : after.start() if after else len(text)]
        found[LABELS[here.group(1).upper()]] = value.strip(" \t\n|*_")
    return (found, "") if found else (None, "")


def _json_object(text: str) -> tuple[dict[str, Any] | None, str]:
    """An answer in JSON after all (the earlier form, and some models' habit)."""
    found = re.search(r"\{.*\}", text or "", re.DOTALL)
    if found is None:
        return None, "no LESSON/LESS/MORE lines in the answer"
    try:
        data = json.loads(found.group(0))
    except ValueError:
        return None, "the JSON in the answer is broken"
    if not isinstance(data, dict):
        return None, "the JSON in the answer is not an object"
    return data, ""


def read_lesson(text: str) -> tuple[dict[str, str] | None, str]:
    """The three texts, cut to the server's longest, and ``""``; or ``None`` and what did not
    fit, in a few words for the journal (on the device one answer in four did not fit, and
    "did not fit" alone could not say why, 14.10.2026)."""
    data, why = _labelled(text)
    if data is None:
        data, why = _json_object(text)
    if data is None:
        return None, why
    lesson = {}
    for key, (shortest, longest) in BOUNDS.items():
        value = data.get(key)
        if not isinstance(value, str):
            return None, f"'{key}' is missing"
        lesson[key] = _fit(value, longest)
        if len(lesson[key]) < shortest:
            return None, f"'{key}' has {len(lesson[key])} characters, needs {shortest}"
    return lesson, ""


def parse_lesson(text: str) -> dict[str, str] | None:
    """The three texts, cut to the server's longest, or ``None`` if one is missing or short."""
    return read_lesson(text)[0]


def kept(home: Path) -> list[dict[str, Any]]:
    path = home / LESSONS_FILE
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return []
    return [entry for entry in data if isinstance(entry, dict)] if isinstance(data, list) else []


def keep(home: Path, lesson: dict[str, Any]) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / LESSONS_FILE).write_text(json.dumps((kept(home) + [lesson])[-KEPT:]), encoding="utf-8")


def latest_text(home: Path) -> str:
    """The newest lesson in words for a prompt; empty before the first."""
    lessons = kept(home)
    if not lessons:
        return ""
    newest = lessons[-1]
    return (f"{newest['lessons']}\nAvoid: {newest['avoid']}\n"
            f"Do more of: {newest['double_down']}")


async def learn(
    home: Path, conf: dict[str, Any], facts: str, send: Send, force: bool = False
) -> dict[str, Any] | None:
    """Look back and keep a lesson. ``None`` without an advisor, or when it is not time
    (forced: fewer than ``FORCED_MIN`` moves).

    The returned entry goes into the journal either way, so a failed attempt waits another
    ``LEARN_EVERY`` moves instead of asking on every move. A failure is said, never raised.
    """
    import advisor

    moves = moves_since_lesson(journal_tail(home))
    enough = len(moves) >= LEARN_EVERY or (force and len(moves) >= FORCED_MIN)
    if not enough or not advisor.configured(conf):
        return None
    entry: dict[str, Any] = {"at": int(time.time()), "tick": moves[-1].get("tick", 0),
                             "learned": "", "ok": False, "from_moves": len(moves),
                             "model": advisor.who(conf)}
    if advisor.limit_reached(conf, home):
        entry["why"] = "daily limit reached"
        return entry
    advisor.count_call(home)
    try:
        text = await send(lesson_messages(facts, summary(moves)), LESSON_TOKENS)
    except advisor.call_errors() as exc:
        entry["why"] = f"no answer ({type(exc).__name__})"
        return entry
    lesson, unfit = read_lesson(text)
    if lesson is None:
        entry["why"] = f"the answer did not fit: {unfit}"
        return entry
    keep(home, {"at": entry["at"], "tick": entry["tick"], "model": entry["model"],
                "from_moves": len(moves), **lesson})
    entry.update(learned=lesson["lessons"], ok=True)
    return entry
