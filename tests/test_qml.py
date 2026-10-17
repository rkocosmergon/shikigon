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
    """H5: an input line that can become editable (#11665, both reviews).

    Qt fetches the clipboard's text into an editable line on every clipboard change and when
    it turns editable, inside the shared shell. So no line may ever be editable: every input
    type is `readOnly: true`, and nothing anywhere sets `readOnly` to anything else.
    """
    bad = []
    for kind in ("TextField", "TextInput", "TextArea", "TextEdit"):
        for block in _blocks(source, kind):
            if not re.search(r"^\s*readOnly:\s*true\s*$", block, re.MULTILINE):
                bad.append(block)
    for m in re.finditer(r"readOnly:(.*)$", source, re.MULTILINE):
        if m.group(1).strip() != "true":
            bad.append(m.group(0))
    return bad


def clipboard_reads(source: str) -> list[str]:
    """H5/H6: the clipboard is read only by its own process, with a deadline and a limit."""
    bad = [m.group(0) for m in re.finditer(r"clipboardText|\.paste\(\)", source)]
    for line in source.splitlines():
        if "wl-paste" in line and not ("timeout " in line and "head -c " in line):
            bad.append(line.strip())
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


# Shown only in the open panel; the README says so ("On your desktop").
PANEL_FILES = ("journal_recent.json", "talk.json", "lessons.json")

# H5 (cos20 #492 F5): the only files the bar reads. Each is written by the brain, which bounds
# its size (status: server blocks to 8 KiB; journal_recent: 12 lines; talk: 3 exchanges, cut;
# lessons: 3, in the server's lengths; setup: one cut sentence; tips, advisor: its own small
# facts). journal.jsonl and chat.jsonl grow and are never read here.
BAR_FILES = PANEL_FILES + ("status.json", "setup.json", "tips.json", "advisor.json")


def unbounded_reads(source: str) -> list[str]:
    """H5: a FileView on a file that is not one the brain keeps small for the bar."""
    bad = []
    for block in _blocks(source, "FileView"):
        path = re.search(r"^\s*path:\s*(.+)$", block, re.MULTILINE)
        named = re.findall(r'"/([^"/]+)"', path.group(1)) if path else []
        if not named or any(name not in BAR_FILES for name in named):
            bad.append(block)
    return bad


def panel_files_read_while_closed(source: str) -> list[str]:
    """H7: a panel file with a path while the panel is closed.

    A FileView loads its file as soon as it has a path (`preload` is on by default,
    Quickshell `fileview.hpp`), so only a path that is empty while closed keeps it unread.
    """
    bad = []
    for block in _blocks(source, "FileView"):
        path = re.search(r"^\s*path:\s*(.+)$", block, re.MULTILINE)
        if not path or not any(f in path.group(1) for f in PANEL_FILES):
            continue
        if "root.popupOpen" not in path.group(1) or ': ""' not in path.group(1):
            bad.append(block)
    return bad


def test_no_input_line_is_ever_editable() -> None:
    for name, source in _sources():
        assert not editable_lines(source), f"{name}: {editable_lines(source)}"


def test_the_clipboard_is_read_only_by_its_own_bounded_process() -> None:
    for name, source in _sources():
        assert not clipboard_reads(source), f"{name}: {clipboard_reads(source)}"


def test_every_text_is_plain_text() -> None:
    for name, source in _sources():
        assert not rich_texts(source), f"{name}: {rich_texts(source)}"


def test_nothing_blocks_the_shell() -> None:
    for name, source in _sources():
        assert not blocking_calls(source), f"{name}: {blocking_calls(source)}"


def test_no_timer_runs_without_end() -> None:
    for name, source in _sources():
        assert not endless_timers(source), f"{name}: {endless_timers(source)}"


def test_the_bar_reads_only_files_the_brain_keeps_small() -> None:
    for name, source in _sources():
        assert not unbounded_reads(source), f"{name}: {unbounded_reads(source)}"


def test_panel_files_are_read_only_while_the_panel_is_open() -> None:
    for name, source in _sources():
        assert not panel_files_read_while_closed(source), (
            f"{name}: {panel_files_read_while_closed(source)}"
        )


def test_the_guards_see_what_they_guard() -> None:
    """Counter-tests: each guard reports a violation it is fed, and finds the real elements."""
    assert editable_lines("TextField {\n  id: a\n}")
    assert editable_lines("TextField {\n  readOnly: !(root.popupOpen && root.step === \"key\")\n}")
    quiet = "component Q: TextField {\n  readOnly: true\n}\nQ {\n  %s\n}"
    assert editable_lines(quiet % "readOnly: false")
    assert not editable_lines(quiet % "id: q")
    assert clipboard_reads("text: Quickshell.clipboardText")
    assert clipboard_reads("onClicked: field.paste()")
    assert clipboard_reads('command: ["sh", "-c", "wl-paste -n"]')
    assert clipboard_reads('command: ["sh", "-c", "timeout 2 wl-paste -n"]')
    assert not clipboard_reads('command: ["sh", "-c", "timeout 2 wl-paste -n | head -c 4096"]')
    assert rich_texts("component Line: Text {\n  color: red\n}")
    assert blocking_calls("FileView {\n  blockLoading: true\n}")
    assert blocking_calls("xhr.open('GET', url, false)")
    assert endless_timers("Timer {\n  interval: 5000\n  running: true\n  repeat: true\n}")
    assert not endless_timers("Timer {\n  id: t\n  repeat: true\n}\nfunction f() { t.stop() }")
    fixed = 'FileView {\n  path: root.stateDir + "/%s"\n}'
    assert unbounded_reads(fixed % "chat.jsonl")
    assert unbounded_reads(fixed % "journal.jsonl")
    assert not unbounded_reads(fixed % "status.json")
    assert panel_files_read_while_closed(fixed % "journal_recent.json")
    assert not panel_files_read_while_closed(fixed % "status.json")
    bar = (QML / "BarWidget.qml").read_text(encoding="utf-8")
    assert len(_blocks(bar, "TextField")) == 1  # the QuietLine component
    assert len(_blocks(bar, "QuietLine")) == 3  # address, key, question
    assert "wl-paste" in bar
    assert len(_blocks(bar, "Text")) >= 6  # four Text blocks and the Caption and Line components
    assert len(_blocks(bar, "Timer")) >= 5
    shown = [b for b in _blocks(bar, "FileView") if any(f in b for f in PANEL_FILES)]
    assert len(shown) == 3
