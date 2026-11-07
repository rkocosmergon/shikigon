"""Guards for the fault search piece 24 of 06.11.2026 at 520d4fa (cos20 #492, plan 6697678199).

Fund #1: a key pasted for one address went to the next one. The paste is read by its own process,
up to 2 s; when it came after the step had changed, it landed in the key of the next address.
Every change of step now lets a paste under way go (``BarWidget.qml`` ``onStepChanged``).

Fund #2: ``max_per_day`` did not hold for calls at the same moment. A check came first and the
count later, each reading and writing ``advisor_count.json`` on its own; questions from the
terminal and a ``tick`` next to the running brain all went out and were counted as fewer. The
same class held the learning budget's bookings, the refusals, the tournament ends seen, the
lessons and the announced proposal. Every read, change and write of a shared file now runs under
``files.held`` (an exclusive lock on the state folder), and a call is checked and counted in one
step (``advisor.take_call``).

Fund #3: ``[M]`` in the terminal found no advisor for some folder names. The starter quoted with
Bash's ``printf %q``, which writes ``$'...'`` for a control character, and under the C locale
for any letter past ASCII; the dashboard splits with ``shlex.split``, which does not know that
form. The starter now quotes each word as ``shlex`` reads it back.

Red on 520d4fa, the positive controls green.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BRAIN = ROOT / "brain"
QML = ROOT / "qml"
sys.path.insert(0, str(BRAIN))

import head

LIMIT = 3

# A process that waits for a common start, then makes one call against the limit: the real
# ``advisor.answer``, only the sending stood in for (it writes a line instead of asking a model).
ASKER = textwrap.dedent(
    """
    import asyncio, os, sys, time
    sys.path.insert(0, sys.argv[1])
    import advisor
    from pathlib import Path
    home, start = sys.argv[2], float(sys.argv[3])
    conf = {"base_url": "http://127.0.0.1:9/v1", "model": "m", "max_per_day": int(sys.argv[4])}

    async def send(messages, tokens):
        fd = os.open(os.path.join(home, "sent.log"), os.O_WRONLY | os.O_APPEND | os.O_CREAT)
        os.write(fd, b"x\\n")
        os.close(fd)
        return "ok"

    while time.time() < start:
        time.sleep(0.0005)
    turn = asyncio.run(advisor.answer(conf, Path(home), "q", "ctx", [], send))
    print(turn.get("ok"), turn.get("why", ""))
    """
)

# The same for one booking of the learning budget (``head.budget_spend``).
BOOKER = textwrap.dedent(
    """
    import sys, time
    sys.path.insert(0, sys.argv[1])
    import head
    from pathlib import Path
    home, start = Path(sys.argv[2]), float(sys.argv[3])
    while time.time() < start:
        time.sleep(0.0005)
    head.budget_spend(home, 1.0)
    """
)


def _together(script: str, home: Path, n: int) -> list[str]:
    """``n`` processes of ``script`` that start their part at the same moment (each with an end)."""
    start = time.time() + 1.5
    procs = [
        subprocess.Popen(
            [sys.executable, "-I", "-c", script, str(BRAIN), str(home), str(start), str(LIMIT)],
            stdout=subprocess.PIPE, text=True)
        for _ in range(n)
    ]
    return [p.communicate(timeout=60)[0].strip() for p in procs]


def _one_after_another(script: str, home: Path, n: int) -> list[str]:
    return [
        subprocess.run(
            [sys.executable, "-I", "-c", script, str(BRAIN), str(home), "0", str(LIMIT)],
            stdout=subprocess.PIPE, text=True, timeout=60, check=True).stdout.strip()
        for _ in range(n)
    ]


def _sent(home: Path) -> int:
    log = home / "sent.log"
    return log.read_text().count("x") if log.exists() else 0


def _calls(home: Path) -> int:
    return int(json.loads((home / "advisor_count.json").read_text())["calls"])


# ---------- Fund #2: calls a day ----------------------------------------------------------------


def test_f2_control_one_after_another_keeps_the_limit(tmp_path: Path) -> None:
    outs = _one_after_another(ASKER, tmp_path, 8)
    assert _sent(tmp_path) == LIMIT and _calls(tmp_path) == LIMIT, outs
    assert sum("daily limit reached" in o for o in outs) == 8 - LIMIT


def test_f2_calls_at_the_same_moment_keep_the_limit(tmp_path: Path) -> None:
    """Twelve questions at once: no more than the limit go out, and each one is counted."""
    outs = _together(ASKER, tmp_path, 12)
    assert _sent(tmp_path) == LIMIT, f"sent={_sent(tmp_path)} outs={outs}"
    assert _calls(tmp_path) == LIMIT
    assert sum("daily limit reached" in o for o in outs) == 12 - LIMIT


# ---------- Variant: the learning budget's bookings --------------------------------------------


def _budget_home(tmp_path: Path) -> Path:
    conf = {"learning_budget_pct": 10}
    assert head.budget_left(tmp_path, conf, 1000.0) == 100.0  # the day begins: 10 % of 1000
    return tmp_path


def _spent(home: Path) -> float:
    return float(json.loads((home / head.BUDGET_FILE).read_text())["spent"])


def test_f2v_control_bookings_one_after_another(tmp_path: Path) -> None:
    home = _budget_home(tmp_path)
    _one_after_another(BOOKER, home, 6)
    assert _spent(home) == 6.0


def test_f2v_bookings_at_the_same_moment_are_all_counted(tmp_path: Path) -> None:
    """A ``tick`` by hand next to the running brain: no booking of either is lost."""
    home = _budget_home(tmp_path)
    _together(BOOKER, home, 12)
    assert _spent(home) == 12.0


# ---------- The class at its narrow place: every shared read-change-write under the lock --------

# Files written whole, never read, changed and written back, or written after a model was asked
# (a lock across an ``await`` would hold the other process up to 45 s). Each with its reason.
WHOLE_OR_AFTER_A_WAIT = {
    ("shikigon_brain.py", "write_private"): "the setup file, written whole",
    ("shikigon_brain.py", "show_journal"): "the bar's view of the journal, written whole",
    ("shikigon_brain.py", "show_talk"): "the bar's view of the talk, written whole",
    ("shikigon_brain.py", "write_status"): "status.json, written whole from one state",
    ("shikigon_brain.py", "_save"): "Refusals: called only from _note, under the lock in note",
    ("connections.py", "choose"): "the player's choice of advisor, replaced whole",
    ("advisor.py", "_keep"): "the counts: called only from take_call/count_move, under the lock",
    ("speech.py", "decide"): "after the model was asked (the then-branch is under the lock)",
    ("speech.py", "_remember"): "after the model was asked: at worst a word's spacing is lost",
}


def _function_of(tree: ast.AST, node: ast.AST) -> str:
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef) and node in ast.walk(fn):
            inner = [f for f in ast.walk(fn) if f is not fn and isinstance(
                f, ast.FunctionDef | ast.AsyncFunctionDef) and node in ast.walk(f)]
            if not inner:
                return fn.name
    return ""


def _held_blocks(tree: ast.AST) -> list[ast.With]:
    return [w for w in ast.walk(tree) if isinstance(w, ast.With) and any(
        isinstance(i.context_expr, ast.Call) and getattr(i.context_expr.func, "attr", "") == "held"
        for i in w.items)]


def unheld_writes(name: str, source: str) -> list[str]:
    """Every ``write_json`` outside ``with files.held(...)`` that is not on the list, and every
    ``await`` inside one."""
    tree = ast.parse(source)
    held = _held_blocks(tree)
    inside = {id(n) for w in held for n in ast.walk(w)}
    bad = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "write_json"
                and id(node) not in inside):
            where = _function_of(tree, node)
            if (name, where) not in WHOLE_OR_AFTER_A_WAIT:
                bad.append(f"{name}:{node.lineno} {where}")
    for w in held:
        bad += [f"{name}:{a.lineno} await under the lock" for a in ast.walk(w)
                if isinstance(a, ast.Await)]
    return bad


def test_every_shared_file_is_read_changed_and_written_under_the_lock() -> None:
    bad = []
    for path in sorted(BRAIN.glob("*.py")):
        if path.name != "files.py":
            bad += unheld_writes(path.name, path.read_text(encoding="utf-8"))
    assert bad == []


def test_the_lock_guard_sees_what_it_guards() -> None:
    """Counter-test: the guard reports a write outside the lock and an ``await`` inside one."""
    assert unheld_writes("x.py", "def f(h):\n    files.write_json(h, {})\n")
    assert not unheld_writes("x.py", "def f(h):\n    with files.held(h):\n"
                                     "        files.write_json(h, {})\n")
    assert unheld_writes("x.py", "async def f(h):\n    with files.held(h):\n        await g()\n")
    # the old count wrote outside the lock (520d4fa)
    old = "def count_call(home):\n    files.write_json(home / 'advisor_count.json', {})\n"
    assert unheld_writes("advisor.py", old)


# ---------- Fund #1: a paste belongs to the step it was asked in ------------------------------


def paste_problems(source: str) -> list[str]:
    """A paste lands only in the step it was asked in: every ``.put(`` goes to ``pasteTarget``,
    and every change of step lets ``pasteTarget`` go."""
    bad = [m.group(0) for m in re.finditer(r"[\w.]+\.put\(", source)
           if m.group(0) != "root.pasteTarget.put("]
    if ".put(" in source:
        step = re.search(r"onStepChanged:\s*(\{.*?\n  \}|[^\n]*)", source, re.DOTALL)
        if not step or not re.search(r"\bpasteTarget\s*=\s*null\b", step.group(1)):
            bad.append("onStepChanged does not let a paste under way go")
    return bad


def test_f1_a_paste_lands_only_in_the_step_it_was_asked_in() -> None:
    source = (QML / "BarWidget.qml").read_text(encoding="utf-8")
    assert ".put(" in source  # the guard looks at the real paste
    assert paste_problems(source) == []


def test_f1_the_paste_guard_sees_what_it_guards() -> None:
    old = ('  onStepChanged: if (step !== "key") keyField.text = ""\n'
           '        if (root.pasteTarget && s !== "") root.pasteTarget.put(s)\n')
    assert paste_problems(old)  # 520d4fa
    new = ('  onStepChanged: {\n    pasteTarget = null\n'
           '    if (step !== "key") keyField.text = ""\n  }\n'
           '        if (root.pasteTarget && s !== "") root.pasteTarget.put(s)\n')
    assert not paste_problems(new)
    assert paste_problems(new + "  keyField.put(s)\n")


# ---------- K22: one key at a time -------------------------------------------------------------


def test_k22_a_second_key_while_the_first_is_written_stays_in_its_field() -> None:
    service = (QML / "Service.qml").read_text(encoding="utf-8")
    keep = re.search(r"function keepKey\(key\) \{(.*?)\n  \}", service, re.DOTALL)
    assert keep and re.search(r'if \(keyKeeper\.running\) return "busy"', keep.group(1))
    bar = (QML / "BarWidget.qml").read_text(encoding="utf-8")
    submit = re.search(r"function submitKey\(\) \{(.*?)\n  \}", bar, re.DOTALL)
    assert submit and 'service.keepKey(keyField.text) !== "ok") return' in submit.group(1)


# ---------- Fund #3: the terminal's advisor whatever the folder is called ----------------------

STAND_IN = """#!/bin/bash
case " $* " in
  *" -m cosmergon_agent.dashboard "*) printf '%s' "$COSMERGON_ADVISOR_CMD" > "$S24_OUT" ;;
esac
exit 0
"""


def _advisor_cmd(home: Path, locale: str) -> tuple[str, Path]:
    """What the real starter exports, with a stand-in Python in the venv that starts nothing."""
    python = home / ".local/share/shikigon/venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text(STAND_IN)
    python.chmod(0o755)
    state = home / ".local/state/shikigon"
    state.mkdir(parents=True)
    (state / "credentials.json").write_text('{"api_key": "x"}')
    out = home.parent / "cmd.txt"
    env = {"HOME": str(home), "PATH": "/usr/local/bin:/usr/bin:/bin", "LC_ALL": locale,
           "S24_OUT": str(out)}
    subprocess.run(["/bin/bash", "-p", str(BRAIN / "shikigon-terminal")], env=env, check=True,
                   timeout=30, stdin=subprocess.DEVNULL)
    return out.read_text(), python


@pytest.mark.parametrize(
    ("folder", "locale"),
    [
        ("plain", "C.UTF-8"),  # control
        ("a b", "C"),  # control
        ("jörg", "C.UTF-8"),  # control
        ("jörg", "C"),  # %q: $'j\303\266rg'
        ("a\tb", "C.UTF-8"),  # %q: $'a\tb'
        ("nl\nx", "C.UTF-8"),
        ("it's", "C.UTF-8"),
        ("$x", "C.UTF-8"),
        ("a\\b", "C.UTF-8"),
    ],
)
def test_f3_the_dashboard_finds_the_terminals_advisor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, folder: str, locale: str
) -> None:
    from cosmergon_agent import dashboard

    cmd, python = _advisor_cmd(tmp_path / "h" / folder, locale)
    monkeypatch.setenv("COSMERGON_ADVISOR_CMD", cmd)
    argv = dashboard._advisor_argv()
    assert argv == [str(python), "-I", str(BRAIN / "shikigon_brain.py"), "ask"], cmd


# --- variant of fund #3: the path Omarchy starts through eval ------------------------------------
# Read at the device on 07.11.2026: omarchy-launch-or-focus-tui writes `"omarchy-launch-tui $@"`
# into one string, and omarchy-launch-or-focus runs it as `eval exec setsid $LAUNCH_COMMAND`.

OMARCHY_CHAIN = textwrap.dedent("""\
    set -- --app-id=org.omarchy.cosmergon "$1"
    LAUNCH_COMMAND="omarchy-launch-tui $@"
    eval exec setsid $LAUNCH_COMMAND
""")
STARTED = ["omarchy-launch-tui", "--app-id=org.omarchy.cosmergon"]


def _launchable() -> re.Pattern[str]:
    """``LAUNCHABLE`` of ``ShikigonModel.js``, as Python reads it (``$`` only at the very end)."""
    literal = re.search(r"^var LAUNCHABLE = /(.*)/$", (QML / "ShikigonModel.js").read_text(
        encoding="utf-8"), re.MULTILINE)
    assert literal, "LAUNCHABLE is gone from ShikigonModel.js"
    source = literal.group(1)
    assert source.endswith("$")
    return re.compile(source[:-1] + r"\Z")


def _through_omarchy(path: str, tmp_path: Path) -> list[str] | None:
    """What Omarchy's chain starts for ``path``: a stand-in ``setsid`` writes its argv."""
    stub = tmp_path / "bin"
    stub.mkdir(exist_ok=True)
    out = tmp_path / "argv"
    out.unlink(missing_ok=True)
    (stub / "setsid").write_text(f"#!/bin/sh\nprintf '%s\\0' \"$@\" > '{out}'\n")
    (stub / "setsid").chmod(0o755)
    subprocess.run(["/bin/bash", "-c", OMARCHY_CHAIN, "chain", path], cwd=tmp_path, timeout=10,
                   env={"PATH": f"{stub}:/usr/bin:/bin", "x": "SET", "LC_ALL": "C.UTF-8"},
                   capture_output=True, check=False)
    return out.read_bytes().decode().split("\0")[:-1] if out.exists() else None


PATHS = ["/h/plain/x", "/h/jörg/x", "/h/a-b_c.d@e%f+g=h:i,j/x", "/h/a b/x", "/h/a\tb/x",
         "/h/a\nb/x", "/h/a'b/x", '/h/a"b/x', "/h/$x/x", "/h/`true`/x", "/h/a;b/x", "/h/a*b/x",
         "/h/{a,b}/x", "/h/a\\b/x", "/h/a|b/x", "/h/a&b/x", "/h/~x/x", "/h/a(b/x", "/h/a#b/x",
         "/h/a[b/x"]


def test_v3_a_path_the_bar_hands_to_omarchy_arrives_whole(tmp_path: Path) -> None:
    """Every path ``launchablePath`` lets through reaches the terminal as exactly itself; the
    others the bar refuses (it says so and starts nothing)."""
    let_through = [path for path in PATHS if _launchable().match(path)]
    assert let_through == ["/h/plain/x", "/h/jörg/x", "/h/a-b_c.d@e%f+g=h:i,j/x"]
    for path in let_through:
        assert _through_omarchy(path, tmp_path) == [*STARTED, path], path


def test_v3_the_chain_breaks_paths_that_are_refused(tmp_path: Path) -> None:
    """Counter-test: the stand-in chain really breaks these paths, so the test above measures."""
    broken = ["/h/a b/x", "/h/a\tb/x", "/h/a'b/x", "/h/$x/x", "/h/`true`/x", "/h/a;b/x",
              "/h/{a,b}/x", "/h/a\\b/x"]
    for path in broken:
        assert not _launchable().match(path), path
        assert _through_omarchy(path, tmp_path) != [*STARTED, path], path
    assert _through_omarchy("/h/plain/x", tmp_path) == [*STARTED, "/h/plain/x"]


def launch_problems(service: str) -> list[str]:
    """``omarchy-launch-or-focus`` only in ``openTerminal``, after ``launchablePath`` returned."""
    found = []
    body = re.search(r"function openTerminal\(\) \{\n(.*?)\n  \}\n", service, re.DOTALL)
    if not body:
        return ["no openTerminal"]
    check = re.search(r"if \(!Model\.launchablePath\(terminalScript\)\) \{(.*?)\n    \}", body[1],
                      re.DOTALL)
    start = body[1].find("omarchy-launch-or-focus")
    if not check or "return" not in check[1] or start < check.end():
        found.append("openTerminal starts before launchablePath said yes")
    outside = service[:body.start()] + service[body.end():]
    if "omarchy-launch-or-focus" in outside:
        found.append("omarchy-launch-or-focus outside openTerminal")
    return found


def test_v3_the_bar_starts_omarchys_terminal_only_for_a_whole_path() -> None:
    texts = "".join(p.read_text(encoding="utf-8") for p in sorted(QML.glob("*.qml"))
                    if p.name != "Service.qml")
    assert "omarchy-launch-or-focus" not in texts
    assert launch_problems((QML / "Service.qml").read_text(encoding="utf-8")) == []


def test_v3_the_launch_guard_sees_what_it_guards() -> None:
    """Counter-test: without the check, or with the start before it, the guard reports."""
    service = (QML / "Service.qml").read_text(encoding="utf-8")
    without = re.sub(r"if \(!Model\.launchablePath\(terminalScript\)\) \{.*?\n    \}\n", "",
                     service, flags=re.DOTALL)
    assert without != service
    assert launch_problems(without)
    assert launch_problems(service + '\nexecDetached(["omarchy-launch-or-focus", "x"])\n')
