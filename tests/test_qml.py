"""The bar's QML, read as text: what the shell must never do on the user's behalf."""

from __future__ import annotations

import re
from pathlib import Path

QML = Path(__file__).resolve().parent.parent / "qml"


def _blocks(source: str, opener: str) -> list[str]:
    """Every `opener {` block of the source, braces matched, as its own text."""
    found: list[str] = []
    for start in (m.start() for m in re.finditer(rf"^\s*{opener}\s*\{{", source, re.MULTILINE)):
        depth = 0
        for end in range(source.index("{", start), len(source)):
            depth += {"{": 1, "}": -1}.get(source[end], 0)
            if depth == 0:
                found.append(source[start:end + 1])
                break
    return found


def test_every_text_line_is_read_only_unless_the_open_panel_shows_it() -> None:
    # Qt fetches the clipboard's text into every editable line on each clipboard change,
    # shown or not, and the panel keeps its lines alive while closed (#11665).
    for qml in sorted(QML.glob("*.qml")):
        source = qml.read_text(encoding="utf-8")
        for kind in ("TextField", "TextInput", "TextArea", "TextEdit"):
            for block in _blocks(source, kind):
                read_only = re.search(r"^\s*readOnly:\s*(.+)$", block, re.MULTILINE)
                assert read_only, f"{qml.name}: a {kind} without readOnly\n{block}"
                assert "root.popupOpen" in read_only.group(1), (
                    f"{qml.name}: a {kind} stays editable while the panel is closed\n{block}")


def test_the_guard_sees_the_three_lines_of_the_bar() -> None:
    source = (QML / "BarWidget.qml").read_text(encoding="utf-8")
    assert len(_blocks(source, "TextField")) == 3
