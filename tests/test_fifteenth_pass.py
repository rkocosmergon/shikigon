"""The fifteenth review (cos20 #492, the betreuer pass at ``841cbdb``): the file that is checked is
the file that starts.

1. (B1) ``guarded`` checked the file ``shutil.which`` found for a stub, and GUARD then started
   the name with ``os.execvp``. ``execvp`` goes on to the next folder on the path when the first
   file fails to start, and on the device the next ``pi`` on the path is Omarchy's stub
   (``~/.local/bin/pi``, after ``~/.local/share/mise/shims``).
2. (B2) The version was read from one start of the name and the question went to the next. An
   update between the two (Omarchy updates every agent every four hours) put a version never
   tried in front of the talk. Under mise the name is a shim, which picks its version only as
   it starts.

``connections.program`` is the one place a program is found; the start takes its path, and
GUARD searches nothing (``os.execv``). Each test is red against ``841cbdb`` (counter-test),
except where its docstring says it guards the form of the fix.
"""

from __future__ import annotations

import ast
import contextlib
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "brain"))

import connections
from conftest import fake_mise, version_answer

ANSWERING = """#!/bin/sh
case "$*" in
  *--version*) echo {version} ;;
  *) cat > /dev/null; echo "answered by {version}" ;;
esac
"""


@pytest.fixture(autouse=True)
def _private_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


def _program(folder: Path, name: str, text: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(text)
    path.chmod(0o755)
    return path


def _stub(folder: Path, mark: Path) -> Path:
    """Omarchy's stub for pi; it leaves ``mark`` when it runs."""
    stub = f'#!/bin/sh\ntouch "{mark}"\nmise use -g --quiet "pi" || exit 1\n'
    return _program(folder, "pi", stub)


# --- 1: a checked file that fails to start lets no other file start ----------------------------


def test_a_checked_program_that_fails_to_start_lets_no_other_one_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The checked pi cannot start (its interpreter is gone, as in the middle of an update), and
    the stub stands later on the path, as on the device. Through the real start and GUARD."""
    first = _program(tmp_path / "mise-shims", "pi", "#!/nonexistent/interpreter\n")
    mark = tmp_path / "stub-ran"
    _stub(tmp_path / "local-bin", mark)
    monkeypatch.setenv("PATH", f"{first.parent}:{tmp_path / 'local-bin'}:/usr/bin:/bin")
    why = connections.untried("pi")
    assert why.startswith("pi is not asked"), why
    with pytest.raises(connections.AgentError):
        connections.run_agent({"agent": "pi"}, "a question", 10.0)
    assert not mark.exists(), "the stub ran in place of the program that was checked"


# --- 2: the version read is the version that answers --------------------------------------------


def test_the_question_goes_to_the_file_whose_version_was_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Between reading the version and the question, an update puts another pi first on the
    path. The question still goes to the file that said 1.4.0. Through the real start."""
    tried = _program(tmp_path / "a", "pi", ANSWERING.format(version="1.4.0"))
    _program(tmp_path / "b", "pi", ANSWERING.format(version="9.9.9"))
    monkeypatch.setenv("PATH", f"{tried.parent}:/usr/bin:/bin")

    def run(argv: list[str], **kwargs: Any) -> Any:
        done = connections.run_in_group(argv, **kwargs)
        if "--version" in argv:  # the update, right after the version was read
            monkeypatch.setenv("PATH", f"{tmp_path / 'b'}:{tried.parent}:/usr/bin:/bin")
        return done

    assert connections.run_agent({"agent": "pi"}, "a question", 10.0, run=run) == (
        "answered by 1.4.0")


def test_a_shim_of_mise_is_never_started_and_its_version_folder_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """As on the device: ``shims/pi`` → ``mise``, and ``mise which pi`` names
    ``installs/pi/latest/pi/pi`` with ``latest`` → ``1.4.0``. The file started lies in the folder
    of its version; the shim, which picks a version only as it starts and may install one, never
    starts."""
    answer = ""
    real = _program(tmp_path / "installs" / "pi" / "1.4.0" / "pi", "pi",
                    ANSWERING.format(version="1.4.0"))
    (tmp_path / "installs" / "pi" / "latest").symlink_to("1.4.0")
    shim_ran = tmp_path / "shim-ran"
    mise = _program(tmp_path / "usr-bin", "mise", f"""#!/bin/sh
if [ "$1" = which ] && [ "$2" = pi ]; then echo "{tmp_path}/installs/pi/latest/pi/pi"; exit 0; fi
touch "{shim_ran}"
exit 1
""")
    shims = tmp_path / "shims"
    shims.mkdir()
    (shims / "pi").symlink_to(mise)
    monkeypatch.setenv("PATH", f"{shims}:{mise.parent}:/usr/bin:/bin")
    with contextlib.suppress(connections.AgentError):
        answer = connections.run_agent({"agent": "pi"}, "a question", 10.0)
    assert not shim_ran.exists(), "the shim started"
    assert answer == "answered by 1.4.0"
    assert connections.untried("pi") == ""
    assert connections.program("pi", None) == str(real.resolve())


def test_a_shim_whose_mise_names_nothing_is_not_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guards the form: no program behind the shim, no start; and never mise itself."""
    for said in ("", "relative/pi", "{mise}"):
        mise = _program(tmp_path / "usr-bin", "mise", "#!/bin/sh\n[ \"$1\" = which ] && echo "
                        f"\"{said.format(mise=tmp_path / 'usr-bin' / 'mise')}\"\nexit 0\n")
        shims = tmp_path / "shims"
        shims.mkdir(exist_ok=True)
        if not (shims / "pi").is_symlink():
            (shims / "pi").symlink_to(mise)
        monkeypatch.setenv("PATH", f"{shims}:{mise.parent}:/usr/bin:/bin")
        with pytest.raises(FileNotFoundError):
            connections.program("pi", None)


@pytest.mark.parametrize(("mise_takes", "asked"), [(0.5, True), (None, False)])
def test_finding_pi_behind_a_shim_and_its_version_share_one_deadline(
    mise_takes: float | None, asked: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """README: "from your agent 1 MiB within 40 seconds (its version, then its list)". Asking
    mise which file stands behind the shim takes from the version's time, not on top of it: a mise
    that answers only at its deadline (``None``) leaves none, and pi is not asked."""
    real = _program(tmp_path / "installs" / "pi" / "1.4.0" / "pi", "pi", "#!/bin/sh\nexit 0\n")
    shims = tmp_path / "shims"
    shims.mkdir()
    (shims / "pi").symlink_to(fake_mise(tmp_path / "usr-bin"))
    monkeypatch.setenv("PATH", f"{shims}:/usr/bin:/bin")
    clock = [1000.0]
    monkeypatch.setattr(connections, "time", SimpleNamespace(
        monotonic=lambda: clock[0], sleep=time.sleep))
    waited: list[float] = []

    def run(argv: list[str], **kwargs: Any) -> Any:
        waited.append(kwargs["timeout"])
        if argv[1:] == ["which", "pi"]:
            clock[0] += kwargs["timeout"] if mise_takes is None else mise_takes
            return SimpleNamespace(returncode=0, stdout=f"{real}\n", stderr="")
        return version_answer(argv) or SimpleNamespace(
            returncode=0, stdout="provider  model\nollama  m\n", stderr="")

    said = connections.agent_listing("pi", run)
    assert said["ok"] is asked, said
    # at worst every later start waits its whole time: what mise took plus those times
    assert (clock[0] - 1000.0) + sum(waited[1:]) <= connections.VERSION_S + 30, waited
    if not asked:
        assert "names no version" in said["why"], said


# --- the guards at the sink ---------------------------------------------------------------------


def test_the_guard_searches_nothing() -> None:
    """Guards the form: GUARD starts the path it is given, with no search along ``PATH``."""
    calls = {node.func.attr for node in ast.walk(ast.parse(connections.GUARD))
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
    assert "execv" in calls
    assert not calls & {"execvp", "execvpe", "execlp", "execlpe", "spawnvp", "spawnlp"}, calls


def test_only_a_file_program_found_is_started(tmp_path: Path) -> None:
    """Guards the form: a name and a link are refused at the start, whatever the caller."""
    target = _program(tmp_path, "pi", "#!/bin/sh\nexit 0\n")
    link = tmp_path / "link-to-pi"
    link.symlink_to(target)
    for first in ("pi", "./pi", str(link)):
        with pytest.raises(connections.Unchecked):
            connections.guarded([first, "--offline", "--version"], None)
        with pytest.raises(connections.Unchecked):
            connections.run_in_group([first, "--offline", "--version"], timeout=5)
    assert connections.guarded([str(target.resolve())], None)[-1] == str(target.resolve())
