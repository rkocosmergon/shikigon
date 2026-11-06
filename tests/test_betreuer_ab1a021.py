"""Guards for the Betreuer-Durchgang ab1a021 (cos20 #492): B1 to B5, one class (K47).

The direction marks of others reached the panel through every file the bar read but the ones
the brain cut with ``short``, and the terminal through the world's sentence on stderr. The bar
now takes control characters out of everything it reads (``ShikigonModel.readJson``/
``readLine``, tested in ``tests/qml/tst_model.qml``, its doors held by ``test_qml``), and the
brain's stderr keeps printable characters only (``CutLines``). These tests hold the three
definitions to one: the bar's ``CONTROLS`` is the SDK's, and the brain's rule removes at least
those. Red on ab1a021 (the stderr test), the controls green on both.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from cosmergon_agent.action import _control_table

ROOT = Path(__file__).resolve().parent.parent
BACKSLASH = chr(92)
RLO = chr(0x202E)


def bar_controls() -> set[int]:
    """The code points of ``CONTROLS`` in ``ShikigonModel.js``, read from its source."""
    source = (ROOT / "qml" / "ShikigonModel.js").read_text(encoding="utf-8")
    found = re.search(r'^var CONTROLS = "([^"]+)"$', source, re.MULTILINE)
    assert found, "ShikigonModel.js names its control characters in one place"
    body = found.group(1).replace(BACKSLASH, "")
    part = r"u([0-9a-f]{4})(?:-u([0-9a-f]{4}))?"
    assert re.fullmatch(f"(?:{part})+", body), f"every part of CONTROLS is read: {body}"
    points: set[int] = set()
    for first, last in re.findall(part, body):
        points.update(range(int(first, 16), int(last or first, 16) + 1))
    return points


def test_the_bar_removes_what_the_sdk_removes() -> None:
    """One definition at three places: server, SDK and bar (K37, K47)."""
    bar, sdk = bar_controls(), set(_control_table(""))
    assert bar == sdk, (sorted(map(hex, bar - sdk)), sorted(map(hex, sdk - bar)))
    assert {0x1B, 0x9B, 0x202E, 0x2066, 0x061C} <= bar, "control: the set is not empty"
    assert 0x200D not in bar, "the joiner stays"
    assert 0x0A in bar, "a line break is kept by readJson itself, not by the set"


def test_the_brain_removes_at_least_what_the_sdk_removes() -> None:
    """``short`` and ``CutLines`` keep printable characters only (``printable_only``): each
    control character of the shared definition is one the brain takes out too."""
    import shikigon_brain

    every = "".join(map(chr, sorted(_control_table(""))))
    assert shikigon_brain.printable_only(every) == ""
    assert shikigon_brain.printable_only("größer 3" + RLO + "x") == "größer 3x", "letters stay"
    kept = shikigon_brain.short("a" + RLO + "b\nc", 10)
    assert kept == "ab\nc", "a line break stays in the bar's files"


def test_the_worlds_sentence_on_stderr_loses_its_direction_marks(tmp_path: Path) -> None:
    """B1: a world that refuses the registration with a direction mark in its sentence. The
    brain runs as it does from the wake script: stderr through ``CutLines``, the sentence as
    ``SystemExit``, which Python prints through ``sys.stderr``."""
    script = f"""
import sys
sys.path.insert(0, {str(ROOT / "brain")!r})
import shikigon_brain as brain
sys.stderr = brain.CutLines(sys.stderr)

class Refused:
    status_code = 429
    def json(self):
        said = "Max 5 " + chr(0x202E) + "anonymous agents per IP."
        return {{"error": {{"code": 429, "message": said}}}}

world = {{"world": {{"base_url": "https://example.test"}}}}
brain.register(world, lambda url, json, timeout: Refused())
"""
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp_path),
           "XDG_STATE_HOME": str(tmp_path / "state"),
           "XDG_CONFIG_HOME": str(tmp_path / "config")}  # fmt: skip
    run = subprocess.run([sys.executable, "-I", "-c", script], env=env, capture_output=True,
                         text=True, encoding="utf-8", timeout=60, check=False)  # fmt: skip
    assert run.returncode == 1, run.stderr
    assert run.stderr == "Max 5 anonymous agents per IP.\n", repr(run.stderr)
