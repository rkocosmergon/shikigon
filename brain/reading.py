"""The head reads what the brain wrote for its look back, through the brain (cos20 #492, K28).

No agent ever gets a tool from Shikigon. In its look back the head may still read the three
files the brain wrote for it: instead of its lesson it answers with one line
``READ <file> <first>-<last>``, and the brain hands back those lines, from exactly these files in
exactly that folder. No path, no other name, no link, a bounded slice, a bounded number of
rounds; then it has to answer.

Why: names other agents chose reach the model (threat model G7). With a read tool of its own an
injected instruction could bring any file of the player into its context, and with a model at a
provider out of the machine. The marketplace review asks for exactly this boundary (20.10.2026,
#6429 #12388 #12061): "disable tools for this path or enforce a filesystem boundary that exposes
only the supplied input". Here the boundary is in the brain, where a test can prove it.
"""

from __future__ import annotations

import os
import re
from collections.abc import Awaitable, Callable
from pathlib import Path

Send = Callable[[list[dict[str, str]], int], Awaitable[str]]

READABLE = ("moves.txt", "rated.txt", "world.txt")  # what lessons.review_folder writes
READS = 3  # times it may read before it must answer
SLICE_LINES = 40
SLICE_CHARS = 6000
FILE_BYTES = 256 * 1024  # the brain writes far less; a bound all the same (K6)
_ASKED = re.compile(r"READ\s+(\S+)(?:\s+(\d+)\s*-\s*(\d+))?", re.IGNORECASE)
WHAT = {
    "moves.txt": "its last moves, one per line, with what the world answered",
    "rated.txt": "the numbers above",
    "world.txt": "the rules as the world states them",
}


def lines_of(folder: Path, name: str) -> list[str] | None:
    """The lines of one readable file, or ``None``. Never a link, never another name."""
    if name not in READABLE:
        return None
    try:
        fd = os.open(folder / name, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    with os.fdopen(fd, "rb") as handle:
        data = handle.read(FILE_BYTES)
    return data.decode("utf-8", errors="replace").splitlines()


def note(folder: Path) -> str:
    """What the head may read and how, with the number of lines in each file."""
    listed = ", ".join(
        f"{name} ({len(lines_of(folder, name) or [])} lines: {WHAT[name]})" for name in READABLE
    )
    return (
        f"Three files hold the details: {listed}. To read lines before you answer, answer with "
        f"one line and nothing else: READ <file> <first>-<last> (at most {SLICE_LINES} lines). "
        f"You get them back. You may read {READS} times; then answer with the three lines."
    )


def asked(answer: str) -> tuple[str, int, int] | None:
    """``READ <file> <first>-<last>`` as the first line of an answer, else ``None``."""
    first = next((line.strip().strip("`") for line in answer.splitlines() if line.strip()), "")
    hit = _ASKED.fullmatch(first.strip())
    if hit is None:
        return None
    start = int(hit.group(2) or 1)
    return hit.group(1), start, int(hit.group(3) or start + SLICE_LINES - 1)


def serve(folder: Path, name: str, first: int, last: int) -> str:
    """The lines asked for, bounded; a sentence for anything that may not be read."""
    lines = lines_of(folder, name)
    if lines is None:
        return f"{name[:60]} cannot be read. You may read only {', '.join(READABLE)}."
    first = max(1, first)
    last = min(len(lines), max(first, last), first + SLICE_LINES - 1)
    text = "\n".join(lines[first - 1 : last])[:SLICE_CHARS]
    return f"{name}, lines {first}-{last} of {len(lines)}:\n{text}"


def mediated(send: Send, folder: Path, more: Callable[[], bool] | None = None) -> Send:
    """A sender that lets the model read through the brain before it answers.

    The first call is the caller's, counted where it starts the look back. Each further round
    is a call of its own: ``more`` counts it and says ``False`` past the day's limit, and then
    the answer so far stands. Until 0.1.13 a look back counted one call and made up to four
    (cos20 #492, eighth review, finding 5)."""

    async def ask(messages: list[dict[str, str]], max_tokens: int) -> str:
        *before, question = messages
        read: list[str] = []
        while True:
            left = READS - len(read)
            talk = [*before, {**question, "content": _with(question["content"], read, left)}]
            answer = await send(talk, max_tokens)
            wanted = asked(answer)
            if wanted is None or left == 0 or (more is not None and not more()):
                return answer
            read.append(serve(folder, *wanted))

    return ask


def _with(question: str, read: list[str], left: int) -> str:
    """The question stays whole and last; what it read follows it."""
    if not read:
        return question
    told = "\n\n".join(f"You read {text}" for text in read)
    rest = (f"You may read {left} more times, or answer now with the three lines." if left
            else "No more reading. Answer now with the three lines.")  # fmt: skip
    return f"{question}\n\n{told}\n\n{rest}"
