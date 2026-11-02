"""The betreuer pass at ``c01ec84`` (cos20 #492).

F1: both scripts and the bar looked a program up in every entry of PATH, an empty or relative one
too, which is a folder of wherever they were started; only the brain's ``on_path`` left those out,
against "A program is looked up only in the absolute folders of your PATH" (CHANGELOG 0.1.14).
One rule now sets PATH before any lookup, in the brain, both scripts and the bar, and what they
start inherits it. F2: a head's move of some 490 nested lists, within its 1024 bytes, ended the
tick in ``head.own_words`` with ``RecursionError``: neither the head's move nor the rules' went
out, against "Without a usable answer the rule tree plays" (README). ``files.loads`` now refuses
JSON deeper than ``MAX_DEPTH``. F3: with ``PYTHONSAFEPATH`` set, the brain did not find its own
modules and no start of it ran. Red on c01ec84.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "brain"))

import connections
import files
import head
from test_betreuer_f2c7212 import _plays

BRAIN = ROOT / "brain" / "shikigon_brain.py"
SCRIPTS = ("shikigon-wake", "shikigon-terminal")
MODEL = ROOT / "qml" / "ShikigonModel.js"
# empty, relative, absolute, and a folder of the current one
GIVEN = ".::bin:/usr/bin:./x:/bin:"
KEPT = "/usr/bin:/bin"


@pytest.fixture(autouse=True)
def _private_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


# --- F1: PATH from its absolute folders only, at every start --------------------------------------

def _rule() -> str:
    """``Model.PATH_RULE`` as the bar has it: its string pieces, joined."""
    text = MODEL.read_text(encoding="utf-8")
    found = re.search(r"^var PATH_RULE = (.*?)\n\n", text, re.MULTILINE | re.DOTALL)
    assert found, "ShikigonModel.js names PATH_RULE"
    return "".join(re.findall(r"'([^']*)'", found.group(1)))


def _planted(folder: Path) -> None:
    for name in ("python3", "dirname", "sh", "test"):
        program = folder / name
        program.write_text("#!/bin/sh\necho PLANTED\n")
        program.chmod(0o755)


def _ruled(shell: str, env: dict[str, str], cwd: Path) -> str:
    done = subprocess.run([shell, "-c", _rule() + 'printf %s "$PATH"'], env=env, cwd=cwd,
                          capture_output=True, text=True, check=True)
    return done.stdout


@pytest.mark.parametrize("shell", ["/bin/sh", "/bin/bash"])
@pytest.mark.parametrize(("given", "want"), [(GIVEN, KEPT), (".:bin", "/bin:/usr/bin"),
                                             ("", "/bin:/usr/bin")])
def test_the_rule_keeps_the_absolute_folders_only(tmp_path: Path, shell: str, given: str,
                                                  want: str) -> None:
    assert _ruled(shell, {"HOME": str(tmp_path), "PATH": given}, tmp_path) == want
    assert connections.absolute_path(given) == want


@pytest.mark.parametrize("shell", ["/bin/sh", "/bin/bash"])
def test_without_path_the_shells_own_is_ruled_too(tmp_path: Path, shell: str) -> None:
    """Without PATH a shell sets its own, and bash's may end in ``.`` (Debian 13, measured)."""
    folders = _ruled(shell, {"HOME": str(tmp_path)}, tmp_path).split(":")
    assert folders and all(folder.startswith("/") for folder in folders), folders


def test_the_brain_and_the_bar_have_one_rule() -> None:
    assert connections.absolute_path(GIVEN) == KEPT
    assert connections.absolute_path("") == os.defpath == "/bin:/usr/bin"


@pytest.mark.parametrize("script", SCRIPTS)
def test_each_script_sets_the_rule_before_it_runs_any_program(script: str) -> None:
    lines = (ROOT / "brain" / script).read_text(encoding="utf-8").splitlines()
    rule = _rule().rstrip()
    assert rule in lines, f"{script} carries the bar's line as it is"
    before = [line for line in lines[1:lines.index(rule)]
              if line.strip() and not line.lstrip().startswith("#")]
    assert all(line.split()[0] in ("set", "umask") for line in before), before


def test_the_wake_script_runs_no_program_of_the_folder_it_was_started_in(tmp_path: Path) -> None:
    """A copy without the lock beside it: it says ``failed`` at once, with ``python3``."""
    copy = tmp_path / "plugin" / "brain" / "shikigon-wake"
    copy.parent.mkdir(parents=True)
    copy.write_text((ROOT / "brain" / "shikigon-wake").read_text(encoding="utf-8"))
    started_in = tmp_path / "here"
    started_in.mkdir()
    _planted(started_in)
    state = tmp_path / "state"
    env = {"HOME": str(tmp_path / "home"), "XDG_STATE_HOME": str(state),
           "PATH": f".::{os.environ['PATH']}"}
    done = subprocess.run(["bash", str(copy)], env=env, cwd=started_in,
                          capture_output=True, text=True, timeout=60, check=False)
    said = (state / "shikigon" / "setup.json").read_text(encoding="utf-8")
    assert done.returncode == 1 and "PLANTED" not in said, (said, done.stderr)
    assert files.loads(said) == {"stage": "failed",
                                 "said": "The plugin's requirements.lock is missing."}


def test_the_bar_starts_programs_only_through_the_rule() -> None:
    """Every ``command:`` and ``execDetached(`` of the bar is the brain, the wake script, or
    ``Model.outside``/``Model.shell``: no program by its bare name, which QProcess and sh look up
    in every entry of PATH."""
    ours = re.compile(r"^(root\.)?(brainCommand\(|wakeScript\b|\[root\.wakeScript\]|"
                      r"Model\.outside\(|Model\.shell\()")
    bad = []
    for qml in sorted((ROOT / "qml").glob("*.qml")):
        text = qml.read_text(encoding="utf-8")
        for m in re.finditer(r"(?:\bcommand:\s*|execDetached\()\s*(.*)", text):
            if not ours.match(m.group(1).strip()):
                bad.append(f"{qml.name}: {m.group(0).strip()}")
    assert not bad, bad
    assert ours.match('Model.outside(["omarchy-menu"])')
    assert not ours.match('["sh", "-c", "x"]') and not ours.match('["omarchy-menu"]')


def test_the_bars_ways_put_the_rule_first() -> None:
    text = MODEL.read_text(encoding="utf-8")
    assert '["/bin/sh", "-c", PATH_RULE + \'exec "$@"\', "sh"].concat(argv)' in text
    assert '["/bin/sh", "-c", PATH_RULE + body, "sh"].concat(args || [])' in text


def test_what_the_brain_starts_inherits_the_rule(tmp_path: Path) -> None:
    code = ("import os, runpy, sys\nsys.argv = [sys.argv[1], '--help']\n"
            "sys.path.insert(0, os.path.dirname(sys.argv[0]))\n"
            "try:\n    runpy.run_path(sys.argv[0], run_name='__main__')\n"
            "except SystemExit:\n    pass\nprint(os.environ['PATH'], file=sys.stderr)\n")
    env = {"HOME": str(tmp_path), "PATH": GIVEN}
    done = subprocess.run([sys.executable, "-c", code, str(BRAIN)], env=env, cwd=tmp_path,
                          capture_output=True, text=True, timeout=60, check=False)
    assert done.stderr.strip().splitlines()[-1] == KEPT, done.stderr


# --- F2: no nesting deeper than the brain reads ---------------------------------------------------

# 490 nested lists in the params of a move with a world form: 995 bytes, within PARAMS_BYTES
DEEP = ('{"action": "create_cube", "params": {"cube_name": ' + "[" * 490 + "]" * 490
        + '}, "why": "x"}')


def test_json_deeper_than_max_depth_is_bad_json() -> None:
    assert files.loads("[" * files.MAX_DEPTH + "]" * files.MAX_DEPTH)
    for deep in ("[" * (files.MAX_DEPTH + 1) + "]" * (files.MAX_DEPTH + 1),
                 '{"a":' * (files.MAX_DEPTH + 1) + "1" + "}" * (files.MAX_DEPTH + 1)):
        with pytest.raises(ValueError, match="nested too deep"):
            files.loads(deep)


def test_a_deeply_nested_move_is_no_usable_answer() -> None:
    assert len(DEEP) < head.PARAMS_BYTES + 60
    move, why = head.parse_move(DEEP, {"create_cube"})
    assert move is None and "nested too deep" in why


@pytest.mark.parametrize("budget", [5, 0])
async def test_after_a_deeply_nested_move_the_rules_play(budget: float) -> None:
    action, _ = await _plays(DEEP, budget)
    assert action == "create_field", "the rules play"


# --- F3: the brain finds its own modules ------------------------------------------------------

def test_the_brain_starts_under_pythonsafepath(tmp_path: Path) -> None:
    env = {"HOME": str(tmp_path), "PATH": os.environ["PATH"], "PYTHONSAFEPATH": "1"}
    done = subprocess.run([sys.executable, str(BRAIN), "--help"], env=env, cwd=tmp_path,
                          capture_output=True, text=True, timeout=60, check=False)
    assert done.returncode == 0, done.stderr
    assert "ModuleNotFoundError" not in done.stderr
