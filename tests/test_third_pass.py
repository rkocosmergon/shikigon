"""The third review (cos20 #492, the betreuer pass at ``b0ac2f7``): each finding closed where the
class has its narrow place, not where it was reported.

1. a program started by the brain read configuration from ``/tmp``, above its empty folder;
2. no deadline while the head of an answer came (``100 Continue`` again and again, or a byte at
   a time): a move, a question or a model list was held for ever;
3. the talk went over plain http to another machine whenever no key went with it;
5. a world's sentence reached a terminal with its control characters;
7. an agent went on when the brain was stopped;
8. a descriptor opened while an earlier version left the files readable went on reading;
9. the echo check compared a whole answer before cutting it.

Each test is red against ``b0ac2f7`` (counter-test), except where its docstring says it guards
the form of a fix.
"""

from __future__ import annotations

import ast
import asyncio
import difflib
import fcntl
import io
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "brain"))

import advisor
import connections
import files
import shikigon_brain as brain
import speech
from conftest import hostile, sends
from test_advisor import _state, _tree
from test_sending import ASK, _chat

ROOT = Path(__file__).resolve().parent.parent
BRAIN = ROOT / "brain"
CONTROL = re.compile(r"[\x00-\x09\x0b-\x1f\x7f-\x9f]")  # what a terminal reads as a command


@pytest.fixture(autouse=True)
def _private_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    for name in ("http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(name, raising=False)


def _state_folder() -> Path:
    """The state folder (0700) as the wake script makes it, without the brain's own helpers, so
    the counter-test measures the old code at its behaviour, not at a missing name."""
    home = Path(os.environ["XDG_STATE_HOME"]) / "shikigon"
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    home.chmod(0o700)
    return home


def _within(seconds: float, work: Callable[[], Any]) -> tuple[bool, BaseException | None, float]:
    """``work`` in a thread of its own: did it end within ``seconds``, with what, how fast. A call
    held for ever (the counter-test) fails the test instead of holding the run."""
    box: dict[str, Any] = {"took": float("inf")}

    def go() -> None:
        started = time.monotonic()
        try:
            box["value"] = work()
        except BaseException as exc:  # noqa: BLE001 — what it ended with is the measurement
            box["error"] = exc
        box["took"] = time.monotonic() - started

    thread = threading.Thread(target=go, daemon=True)
    thread.start()
    thread.join(seconds)
    return not thread.is_alive(), box.get("error"), box["took"]


# --- 1: no configuration from a folder another user can write -----------------------------------


def _record(seen: list[dict[str, Any]], stdout: str = "") -> Callable[..., Any]:
    def run(argv: list[str], **kw: Any) -> Any:
        seen.append({"argv": argv, **kw})
        return SimpleNamespace(stdout=stdout, stderr="", returncode=0)

    return run


def test_every_program_runs_in_a_folder_of_the_players_own(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    shared = tmp_path / "shared"  # where /tmp would be: anyone may write there
    shared.mkdir()
    shared.chmod(0o1777)
    monkeypatch.setattr(tempfile, "tempdir", str(shared))
    seen: list[dict[str, Any]] = []
    connections.in_empty_folder(_record(seen), ["mise", "where", "pi"])
    cwd = Path(seen[0]["cwd"])
    assert shared not in cwd.parents, "not below the shared temporary folder"
    assert cwd.parent == files.state_dir() / "run"
    for folder in (cwd.parent, files.state_dir()):  # what Shikigon makes for it
        assert stat.S_IMODE(folder.stat().st_mode) == 0o700, folder


def test_opencode_reads_no_project_configuration_neither_asked_nor_listing() -> None:
    seen: list[dict[str, Any]] = []
    answer = '{"part": {"type": "text", "text": "OK"}}\n'
    assert connections.run_agent({"agent": "opencode"}, "q", 5.0, run=_record(seen, answer)) == "OK"
    connections.agent_models("opencode", run=_record(seen))
    assert len(seen) == 2
    assert all((s.get("env") or {}).get("OPENCODE_DISABLE_PROJECT_CONFIG") == "1" for s in seen)


# --- 2: one deadline for the whole exchange, also before the head of the answer ----------------


def continues(handler: BaseHTTPRequestHandler) -> None:
    """``100 Continue`` again and again, never an answer (httpcore takes every 1xx but 101)."""
    while True:
        handler.wfile.write(b"HTTP/1.1 100 Continue\r\n\r\n")
        time.sleep(0.1)


def dribbles(handler: BaseHTTPRequestHandler) -> None:
    """The head of an answer, a byte every tenth of a second."""
    handler.wfile.write(b"HTTP/1.1 200 OK\r\n")
    while True:
        handler.wfile.write(b"X")
        time.sleep(0.1)


SLOW = pytest.mark.parametrize("respond", [continues, dribbles],
                               ids=["100 Continue", "a byte at a time"])


@SLOW
def test_a_model_list_ends_within_its_time_whatever_comes_first(
    respond: Callable[[BaseHTTPRequestHandler], None],
) -> None:
    with hostile(respond) as (address, _):
        ended, error, took = _within(
            8, lambda: connections.bounded_call("GET", address + "/v1/models", seconds=1))
    assert ended, "the call was held past its time"
    assert isinstance(error, TimeoutError) and took < 3, (error, took)


@SLOW
def test_a_question_ends_within_its_time_whatever_comes_first(
    respond: Callable[[BaseHTTPRequestHandler], None],
) -> None:
    with hostile(respond) as (address, _):
        send = advisor.http_send({"base_url": address + "/v1", "model": "m", "timeout_s": 1})
        ended, error, took = _within(8, lambda: asyncio.run(send(ASK, 10)))
    assert ended, "the question was held past its time"
    assert isinstance(error, TimeoutError) and took < 3, (error, took)


def test_a_move_falls_back_to_the_rules_within_its_time() -> None:
    state = _state()
    with hostile(continues) as (address, _):
        conf = {"base_url": address + "/v1", "model": "m", "timeout_s": 1, "every": 1,
                "max_per_day": 0}
        decider = advisor.AdvisedDecider(_tree(), conf, brain.state_dir())
        ended, error, took = _within(8, lambda: asyncio.run(decider.decide(state)))
    assert ended and error is None and took < 3, (error, took)
    assert decider.last is not None and decider.last["fallback"] == "TimeoutError"


# --- 3: the talk goes encrypted or stays on this machine (decision A) --------------------------


@pytest.mark.parametrize(("url", "may"), [
    ("https://openrouter.ai/api/v1", True),
    ("http://localhost:11434/v1", True),
    ("http://127.0.0.1:1234/v1", True),
    ("http://[::1]:1234/v1", True),
    ("http://192.168.1.50:11434/v1", False),
    ("http://10.20.30.40:11434/v1", False),
    ("http://169.254.1.1/v1", False),
    ("http://localhost.evil.com/v1", False),
    ("ftp://localhost/v1", False),
])
def test_the_talk_goes_only_encrypted_or_to_this_machine(url: str, may: bool) -> None:
    assert connections.talk_may_go_to(url) is may


def test_a_model_server_in_the_home_network_gets_nothing_over_plain_http() -> None:
    asked: list[str] = []

    def get(url: str, **_: Any) -> Any:
        asked.append(url)
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None,
                               json=lambda: {"data": [{"id": "m"}]})

    found = connections.models_at("http://192.168.1.50:11434/v1", get)
    assert asked == [] and found["ok"] is False
    assert found["why"] == connections.TALK_ONLY_SAFE and "ssh -L" in found["why"]


def test_a_proxy_in_the_environment_does_not_cut_off_the_players_own_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guards the form of decision A (green at ``b0ac2f7`` too): the rule for the talk is not the
    one for keys, which refuses plain http to this machine once a proxy is set, although the
    brain's own calls to this machine never use one."""
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    with hostile(sends(_chat("OK"))) as (address, seen):
        send = advisor.http_send({"base_url": address + "/v1", "model": "m", "timeout_s": 5})
        assert asyncio.run(send(ASK, 10)) == "OK"
    assert len(seen) == 1


def test_the_brain_opens_a_connection_in_one_place_only() -> None:
    """Every rule of sending is checked in ``connections.exchange``; no other function opens an
    httpx client, so no way of sending goes past them (K11, the class of #12584)."""
    senders = {"AsyncClient", "Client", "stream", "request", "get", "post", "delete", "put"}
    outside: list[str] = []
    for path in sorted(BRAIN.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for node in ast.walk(fn):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and getattr(node.func.value, "id", "") == "httpx"
                        and node.func.attr in senders):
                    outside.append(f"{path.name}:{fn.name}")
    assert set(outside) == {"connections.py:exchange"}, outside


# --- 5: nothing on stderr a terminal reads as a command ----------------------------------------


def test_the_brain_says_nothing_on_stderr_a_terminal_reads_as_a_command() -> None:
    out = io.StringIO()
    cut = brain.CutLines(out)
    cut.write("refused\x1b]52;c;QUJD\x07 \x9b31mred\x1b[0m\x7f end\nnext\x0bline\n")
    text = out.getvalue()
    assert not CONTROL.search(text), repr(text)
    assert text.startswith("refused") and "\nnext" in text, "the words and the lines stay"


# --- 7: the agents end with the brain -----------------------------------------------------------

AGENT = """
import sys, threading, time
sys.path.insert(0, {brain!r})
import connections, shikigon_brain as brain
getattr(brain, "end_with_its_agents", lambda: None)()
threading.Thread(target=lambda: connections.run_in_group(["sleep", "61.25"], timeout=120),
                 daemon=True).start()
time.sleep(30)
"""


def _sleeper(parent: int) -> int | None:
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes()
            ppid = int((entry / "stat").read_text().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            continue
        if b"61.25" in cmdline and ppid == parent:
            return int(entry.name)
    return None


def _alive(pid: int) -> bool:
    try:
        return (Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]) != "Z"
    except OSError:
        return False


def test_an_agent_ends_when_the_brain_is_stopped(tmp_path: Path) -> None:
    script = tmp_path / "brain_with_an_agent.py"
    script.write_text(AGENT.format(brain=str(BRAIN)), encoding="utf-8")
    proc = subprocess.Popen([sys.executable, str(script)], env={**os.environ})
    try:
        deadline = time.monotonic() + 10
        pid = None
        while pid is None and time.monotonic() < deadline:
            pid = _sleeper(proc.pid)
            time.sleep(0.05)
        assert pid is not None, "the agent started"
        proc.send_signal(signal.SIGTERM)  # what the bar does when it stops the brain
        proc.wait(timeout=10)
        deadline = time.monotonic() + 5
        while _alive(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _alive(pid), "the agent ended with the brain"
    finally:
        proc.kill()
        if pid is not None and _alive(pid):
            os.kill(pid, signal.SIGKILL)


# --- 8: a descriptor from an earlier version reads nothing more ----------------------------------


def test_a_file_left_readable_is_renewed_and_an_old_reader_gets_nothing_more() -> None:
    home = _state_folder()
    journal = home / "journal.jsonl"
    journal.write_text('{"old": 1}\n', encoding="utf-8")
    journal.chmod(0o644)  # as 0.1.0 to 0.1.12 made it
    with journal.open("rb") as reader:  # opened while 0.1.0 left the folder readable
        brain.private_state()
        files.append(journal, {"new": 2})
        assert reader.read() == b'{"old": 1}\n', "the old descriptor sees nothing new"
    assert stat.S_IMODE(journal.stat().st_mode) == 0o600
    assert journal.read_text(encoding="utf-8").splitlines() == ['{"old": 1}', '{"new": 2}']


def test_a_line_file_is_made_for_the_player_alone() -> None:
    before = os.umask(0o022)  # the usual default
    try:
        files.append(_state_folder() / "chat.jsonl", {"q": "mine"})
    finally:
        os.umask(before)
    assert stat.S_IMODE((_state_folder() / "chat.jsonl").stat().st_mode) == 0o600


def test_a_line_written_while_the_file_is_renewed_goes_into_the_new_file() -> None:
    path = _state_folder() / "chat.jsonl"
    path.write_text("", encoding="utf-8")
    with path.open("rb") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        writer = threading.Thread(target=files.append, args=(path, {"q": "late"}))
        writer.start()
        time.sleep(0.3)  # it waits for the lock of the old file
        renewed = path.with_name(".renewed")
        renewed.write_text("", encoding="utf-8")
        os.replace(renewed, path)
    writer.join(5)
    assert '"late"' in path.read_text(encoding="utf-8"), "the line is in the file of that name"


def test_every_writer_makes_its_files_for_the_player_alone() -> None:
    """Guards the form (red at ``b0ac2f7``): the running brain, the wake script and the bar's two
    shell commands each set the mode of what they make."""
    source = (BRAIN / "shikigon_brain.py").read_text(encoding="utf-8")
    main = source[source.index('if __name__ == "__main__":'):]
    assert main.index("os.umask(0o077)") < main.index("sys.exit(main())")
    wake = (BRAIN / "shikigon-wake").read_text(encoding="utf-8")
    assert wake.index("\numask 077\n") < wake.index('mkdir -p "$state"')
    service = (ROOT / "qml" / "Service.qml").read_text(encoding="utf-8")
    shells = re.findall(r'command: \["sh", "-c",\s*\n\s*\'([^\']*)', service)
    assert len(shells) == 2 and all(s.startswith("umask 077;") for s in shells), shells


# --- 9: the echo check compares no more than a message -------------------------------------------


def test_the_echo_check_compares_no_more_than_a_message(monkeypatch: pytest.MonkeyPatch) -> None:
    lengths: list[int] = []
    real = difflib.SequenceMatcher

    class Spy(real):  # type: ignore[misc, valid-type]
        def __init__(self, isjunk: Any = None, a: str = "", b: str = "",
                     autojunk: bool = True) -> None:
            lengths.append(len(a))
            super().__init__(isjunk, a, b, autojunk)

    monkeypatch.setattr(speech.difflib, "SequenceMatcher", Spy)
    speech.usable("word " * 50_000, "a short message from another agent", "")
    assert lengths and max(lengths) <= speech.TEXT_CHARS, lengths


# --- 6: what the README says about the look back -----------------------------------------------


def test_the_readme_gives_pi_no_tool_where_it_lists_what_runs() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "with its read-only tool" not in text, "pi looks back through the brain (0.1.11)"
