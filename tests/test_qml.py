"""The bar's QML, read as text: what the shell must never do on the user's behalf.

The bar runs inside the shared Omarchy shell. These guards are rows of the host checklist
H1–H8 (cos20 `docs/referenz/shikigon-bedrohungsmodell.md`): each one has a counter-test that
feeds it a violation, so a guard that sees nothing is noticed.
"""

from __future__ import annotations

import re
from pathlib import Path

QML = Path(__file__).resolve().parent.parent / "qml"


def _blocks(source: str, opener: str) -> list[str]:
    """Every `opener {` block of the source (also `component X: opener {`), braces matched."""
    found: list[str] = []
    pattern = rf"(?:^\s*|:\s*){opener}\s*\{{"
    for match in re.finditer(pattern, source, re.MULTILINE):
        depth = 0
        for end in range(source.index("{", match.start()), len(source)):
            depth += {"{": 1, "}": -1}.get(source[end], 0)
            if depth == 0:
                found.append(source[match.start():end + 1])
                break
    return found


def _sources() -> list[tuple[str, str]]:
    return [(p.name, p.read_text(encoding="utf-8")) for p in sorted(QML.glob("*.qml"))]


def editable_lines(source: str) -> list[str]:
    """H5: input lines that stay editable while the panel is closed (#11665)."""
    bad = []
    for kind in ("TextField", "TextInput", "TextArea", "TextEdit"):
        for block in _blocks(source, kind):
            read_only = re.search(r"^\s*readOnly:\s*(.+)$", block, re.MULTILINE)
            if not read_only or "root.popupOpen" not in read_only.group(1):
                bad.append(block)
    return bad


def rich_texts(source: str) -> list[str]:
    """H4: a `Text` that would read markup (mako and Qt render rich text)."""
    return [b for b in _blocks(source, "Text") if "textFormat: Text.PlainText" not in b]


def blocking_calls(source: str) -> list[str]:
    """H5: loads or writes that stop the shared shell until they finish."""
    patterns = (
        r"blockLoading:\s*true",
        r"blockWrites:\s*true",
        r"blockAllReads:\s*true",
        r"\.open\([^)]*,\s*false\s*\)",  # synchronous XMLHttpRequest
    )
    return [m.group(0) for p in patterns for m in re.finditer(p, source)]


def endless_timers(source: str) -> list[str]:
    """H7: a repeating timer with neither a condition nor anyone who stops it."""
    bad = []
    for block in _blocks(source, "Timer"):
        if not re.search(r"repeat:\s*true", block):
            continue
        running = re.search(r"running:\s*([^\n;}]+)", block)
        if running and running.group(1).strip() != "true":
            continue
        name = re.search(r"id:\s*(\w+)", block)
        if name and re.search(rf"\b{name.group(1)}\.stop\(\)", source):
            continue
        bad.append(block)
    return bad


def test_input_lines_are_read_only_unless_the_open_panel_shows_them() -> None:
    for name, source in _sources():
        assert not editable_lines(source), f"{name}: {editable_lines(source)}"


def test_every_text_is_plain_text() -> None:
    for name, source in _sources():
        assert not rich_texts(source), f"{name}: {rich_texts(source)}"


def test_nothing_blocks_the_shell() -> None:
    for name, source in _sources():
        assert not blocking_calls(source), f"{name}: {blocking_calls(source)}"


def test_no_timer_runs_without_end() -> None:
    for name, source in _sources():
        assert not endless_timers(source), f"{name}: {endless_timers(source)}"


def test_the_guards_see_what_they_guard() -> None:
    """Counter-tests: each guard reports a violation it is fed, and finds the real elements."""
    assert editable_lines("TextField {\n  id: a\n}")
    assert rich_texts("component Line: Text {\n  color: red\n}")
    assert blocking_calls("FileView {\n  blockLoading: true\n}")
    assert blocking_calls("xhr.open('GET', url, false)")
    assert endless_timers("Timer {\n  interval: 5000\n  running: true\n  repeat: true\n}")
    assert not endless_timers("Timer {\n  id: t\n  repeat: true\n}\nfunction f() { t.stop() }")
    bar = (QML / "BarWidget.qml").read_text(encoding="utf-8")
    assert len(_blocks(bar, "TextField")) == 3
    assert len(_blocks(bar, "Text")) >= 6  # four Text blocks and the Caption and Line components
    assert len(_blocks(bar, "Timer")) >= 5
