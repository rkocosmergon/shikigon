"""Guards for the Betreuer-Durchgang 1d7ff4d (cos20 #492, plan 6795541259): B1, B2.

B1: the setup waited only for the steps the bar starts; the terminal, its ``M`` and a sign-in
start the brain's Python on their own. CHANGELOG and README promised more ("never run at once",
"keeps the version it started with"). The promise is now what the code holds: the bar's steps
wait, the terminal is closed before an update.

B2: "wake a new one" started the wake script while the game loop was only told to stop. The loop
is counted busy now, so the wake script starts once it has ended: before, an old brain could still
book into the files ``register --new`` had just cleared, and the loop that ended after the wake
script was never started again.

Red on 1d7ff4d.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVICE = (ROOT / "qml" / "Service.qml").read_text(encoding="utf-8")
CHANGELOG = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_b2_the_wake_script_waits_for_the_game_loop_to_end() -> None:
    busy = SERVICE[SERVICE.index("readonly property bool brainBusy:"):]
    busy = busy[:busy.index("\n  //")]  # the expression alone: `stuck` below names runner too
    assert "runner.running" in busy, busy


def test_b1_no_promise_that_nothing_runs_beside_the_setup() -> None:
    assert "never run at once" not in _flat(CHANGELOG)


def test_b1_no_promise_that_an_open_terminal_keeps_its_version() -> None:
    assert "keeps the version it started with" not in _flat(README)


def test_b1_the_readme_says_to_close_the_terminal_before_an_update() -> None:
    assert "close it before an update" in _flat(README)
