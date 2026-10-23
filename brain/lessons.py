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

import files
import reading

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
    return files.tail(home / JOURNAL_FILE, count)


def moves_since_lesson(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The moves after the last lesson (or attempt at one) that carry their energy."""
    start = 0
    for number, entry in enumerate(entries):
        if "learned" in entry:
            start = number + 1
    return [entry for entry in entries[start:] if "action" in entry and "energy" in entry]


def due(entries: list[dict[str, Any]], every: int = LEARN_EVERY) -> bool:
    return len(moves_since_lesson(entries)) >= every


HEAD_WINDOW = 100  # moves the head's look back is rated on, whatever came since its last one
REVIEW_FOLDER = "review"  # what the head may read while it looks back (cos20 #527)
LINE_CHARS = 200  # one journal line in that folder


def window(entries: list[dict[str, Any]], size: int = HEAD_WINDOW) -> list[dict[str, Any]]:
    """The last ``size`` moves that carry their energy: the head looks back every few moves,
    so the moves since its last lesson alone would be too few to rate (cos20 #527)."""
    return [entry for entry in entries if "action" in entry and "energy" in entry][-size:]


def review_folder(home: Path, moves: list[dict[str, Any]], numbers: str, world: str) -> Path:
    """Write what the head may read while it looks back; the folder is inside the state
    folder (0700). Its moves one per line, as a model may read them (``advisor.move_for_model``:
    no names of other agents, the world's reason quoted), the numbers, the rules of the world."""
    import advisor

    folder = home / REVIEW_FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(advisor.move_for_model(move), ensure_ascii=False)[:LINE_CHARS]
             for move in moves]  # fmt: skip
    (folder / "moves.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (folder / "rated.txt").write_text(numbers + "\n", encoding="utf-8")
    (folder / "world.txt").write_text(world[:8000] + "\n", encoding="utf-8")
    return folder


def measure(moves: list[dict[str, Any]]) -> str:
    """What a move is judged by: ``total`` (the goal's measure, cos20 #504) when every move
    carries it, else ``energy``.

    Measured on the device (19.10.2026, cos20 #527): judged by the balance, an energy offer
    read as a loss for the moves after it and its return as a gain for whatever was played
    when it expired; the lesson then praised selling and blamed contracts.
    """
    return "total" if moves and all("total" in move for move in moves) else "energy"


def measured(moves: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The moves judged together: once the newest carries the total, only the moves since the
    total came. Without this the switch would wait a whole window (100 moves, four hours) and
    keep judging by the balance meanwhile; with it a look back waits for ``HORIZON`` moves."""
    if not moves or "total" not in moves[-1]:
        return moves
    start = len(moves)
    while start > 0 and "total" in moves[start - 1]:
        start -= 1
    return moves[start:]


def rated(moves: list[dict[str, Any]]) -> list[tuple[dict[str, Any], float]]:
    """Each move with the change of its measure until ``HORIZON`` moves later; the last ones
    have none."""
    key = measure(moves)
    return [
        (move, float(moves[number + HORIZON][key]) - float(move[key]))
        for number, move in enumerate(moves[:-HORIZON] if len(moves) > HORIZON else [])
    ]


def _signed(value: float) -> str:
    return f"{value:+,.0f}"


def own_effect(move: dict[str, Any]) -> float | None:
    """What the move itself booked: free + held + items (cos20 #533); ``None`` without that word.

    Exact and its own, unlike the change after it, which mixes in income, decay and the moves
    of others. Buying from the house or listing an offer comes out at 0.
    """
    effect = move.get("effect")
    if not isinstance(effect, dict):
        return None
    return sum(float(effect.get(k) or 0.0) for k in ("free", "held", "items"))


def _kind_line(rows: list[tuple[dict[str, Any], float]]) -> str:
    """``3 played, 1 refused, +1,200, own effect -500`` for one kind of move."""
    changes = [change for move, change in rows if move.get("ok") is not False]
    mean = _signed(sum(changes) / len(changes)) if changes else "-"
    refused = len(rows) - len(changes)
    line = f"{len(changes)} played, {refused} refused, {mean}"
    own = [e for e in (own_effect(move) for move, _ in rows) if e is not None]
    return line + (f", own effect {_signed(sum(own) / len(own))}" if own else "")


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
    key = measure(moves)
    said = "total energy" if key == "total" else "energy"
    lines = [
        (f"{len(moves)} moves, {said} {moves[0][key]:,.0f} -> {moves[-1][key]:,.0f}, "
         f"fields {moves[0].get('fields', '?')} -> {moves[-1].get('fields', '?')}."),
        (f"Change of {said} in the {HORIZON} moves after a played move, all causes: "
         f"{overall} on average."),
        ("Per kind of move (played, refused, that change for the kind; and own effect: what "
         "the move itself booked, as the world said it):"),
    ]
    from words import move_words  # the player's words, so the lesson comes back in them

    per_action: dict[str, list[tuple[dict[str, Any], float]]] = {}
    for move, change in judged:
        per_action.setdefault(move_words(move), []).append((move, change))
    for action, rows in sorted(per_action.items(), key=lambda item: -len(item[1])):
        lines.append(f"- {action}: {_kind_line(rows)}")
    ordered = sorted(played, key=lambda row: row[1])
    shown = min(SHOWN, len(ordered) // 2)  # best and worst never share a move
    for title, rows in (("Best", ordered[::-1][:shown]), ("Worst", ordered[:shown])):
        lines.append(f"{title} moves:")
        lines.extend(
            f"- tick {move.get('tick', '?')}: {move_words(move)}, {_signed(change)}"
            for move, change in rows
        )
    return "\n".join(lines)


def lesson_messages(facts: str, numbers: str, before: str = "",
                    files_note: str = "") -> list[dict[str, str]]:  # fmt: skip
    """``before``: the lesson it wrote last time. The head updates it instead of starting
    over, so a rule changes only when the numbers say so (cos20 #527)."""
    text = f"The agent now:\n{facts}\n\nIts last moves:\n{numbers}"
    if before:
        text += ("\n\nWhat you wrote last time. Keep every rule unless the numbers contradict "
                 "it; a rule about a move that is not in these numbers stays "
                 f"(cos20 #533):\n{before}")
    if files_note:
        text += f"\n\n{files_note}"
    return [{"role": "system", "content": LESSON_SYSTEM}, {"role": "user", "content": text}]


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
    if "{" not in (text or ""):
        return None, "no LESSON/LESS/MORE lines in the answer"
    try:
        data = files.json_in(text or "")
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
        data = files.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return []
    return [entry for entry in data if isinstance(entry, dict)] if isinstance(data, list) else []


def keep(home: Path, lesson: dict[str, Any]) -> None:
    home.mkdir(parents=True, exist_ok=True)
    files.write_json(home / LESSONS_FILE, (kept(home) + [lesson])[-KEPT:])


def latest_text(home: Path) -> str:
    """The newest lesson in words for a prompt; empty before the first."""
    lessons = kept(home)
    if not lessons:
        return ""
    newest = lessons[-1]
    return (f"{newest['lessons']}\nAvoid: {newest['avoid']}\n"
            f"Do more of: {newest['double_down']}")


async def learn(
    home: Path, conf: dict[str, Any], facts: str, send: Send, force: bool = False,
    head: bool = False, world: str = "", reader: Any = None, secret: str = "",
) -> dict[str, Any] | None:
    """Look back and keep a lesson. ``None`` without an advisor, or when it is not time
    (forced: fewer than ``FORCED_MIN`` moves).

    The returned entry goes into the journal either way, so a failed attempt waits another
    ``LEARN_EVERY`` moves instead of asking on every move. A failure is said, never raised.

    ``head``: the player's agent has taken over (cos20 #527). It looks back every
    ``review_every`` moves on the last ``HEAD_WINDOW`` and updates its last lesson. ``reader``
    makes a sender that lets it read, through the brain, the folder with its moves, the
    numbers and ``world`` (``reading.mediated``); without one, the numbers go in the prompt.
    """
    import advisor

    entries = journal_tail(home)
    since = moves_since_lesson(entries)
    every = max(1, int(conf.get("review_every", 5) or 5)) if head else LEARN_EVERY
    enough = len(since) >= every or (force and len(since) >= FORCED_MIN)
    moves = measured(window(entries) if head else since)
    if not enough or not advisor.configured(conf) or len(moves) <= HORIZON:
        return None
    entry: dict[str, Any] = {"at": int(time.time()), "tick": moves[-1].get("tick", 0),
                             "learned": "", "ok": False, "from_moves": len(moves),
                             "model": advisor.who(conf)}
    if advisor.limit_reached(conf, home):
        entry["why"] = "daily limit reached"
        return entry
    advisor.count_call(home)
    # the numbers as a model may read them (``advisor.move_for_model``): a mission type the world
    # named went into them raw until 0.1.13, also into ``rated.txt`` (sixth review, finding 4)
    numbers = summary([m for m in map(advisor.move_for_model, moves) if "energy" in m])
    before = latest_text(home) if head else ""
    note = ""
    if head and reader is not None:
        folder = review_folder(home, moves, numbers, world)
        send, note = reader(folder), reading.note(folder)
    started = time.monotonic()
    try:
        text = await send(lesson_messages(facts, numbers, before, note), LESSON_TOKENS)
    except advisor.call_errors() as exc:
        entry["why"] = f"no answer ({type(exc).__name__})"
        return entry
    finally:  # how long a look back takes: the head's must fit between its moves (#527)
        entry["ms"] = int((time.monotonic() - started) * 1000)
    lesson, unfit = read_lesson(text)
    if lesson is None:
        entry["why"] = f"the answer did not fit: {unfit}"
        return entry
    if secret and any(secret in value for value in lesson.values()):
        # no agent can read the key file any more (cos20 #492, K28); a lesson with the key is
        # still never kept: it would stand in the bar and go back to the model (G8)
        entry["why"] = "the answer held its key and was dropped"
        return entry
    keep(home, {"at": entry["at"], "tick": entry["tick"], "model": entry["model"],
                "from_moves": len(moves), **lesson})
    entry.update(learned=lesson["lessons"], ok=True)
    return entry
