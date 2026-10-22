"""Answers as they are and only so much of them; the connection that was really made.

The marketplace review of 0.1.11 (#12454): the 256 KiB limit counted after httpx had unpacked
the answer, so a few KB of stacked gzip filled the brain's memory before it was checked. The
search for that class over every way of the brain, and for methods that hold less than they say
(cos20 #492, S1–S14), found the rest of this file. Each test is red against 0.1.11 (``86c9f15``).
"""

from __future__ import annotations

import ast
import asyncio
import gzip
import os
import re
import subprocess
import sys
import time
import tracemalloc
from collections.abc import Callable
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "brain"))

import advisor
import connections
import files
import head
import lessons
import shikigon_brain as brain
from conftest import hostile, sends
from test_sending import ME, MODEL, Kernel, _chat

BRAIN = Path(__file__).resolve().parent.parent / "brain"
UNPACKED = 64 * 1024 * 1024
BOMB = gzip.compress(gzip.compress(b"\0" * UNPACKED, 9), 9)  # about 100 KB on the wire
SYSTEM = 100  # an account below UID_MIN: a system service


@pytest.fixture(autouse=True)
def _private_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    for name in ("http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(name, raising=False)


def _peak(run: Callable[[], object]) -> int:
    tracemalloc.start()
    try:
        run()
    finally:
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
    return peak


# --- K29: an answer as it is, counted raw (S1, S2) --------------------------------------------


def test_a_stacked_gzip_answer_is_refused_before_it_is_unpacked() -> None:
    with hostile(sends(BOMB, "gzip, gzip")) as (address, seen):

        def read() -> None:
            with pytest.raises(connections.TooLong):
                connections.bounded_get(f"{address}/v1/models")

        peak = _peak(read)
    assert peak < 4 * 1024 * 1024, f"{peak} bytes in memory: the answer was unpacked"
    assert seen[0]["accept-encoding"] == "identity"


def test_a_models_answer_is_read_raw_too() -> None:
    with hostile(sends(BOMB, "gzip, gzip")) as (address, seen):
        send = advisor.http_send({**MODEL, "base_url": f"{address}/v1"})

        def ask() -> None:
            with pytest.raises(connections.TooLong):
                asyncio.run(send([{"role": "user", "content": "hi"}], 10))

        peak = _peak(ask)
    assert peak < 4 * 1024 * 1024, f"{peak} bytes in memory: the answer was unpacked"
    assert seen[0]["accept-encoding"] == "identity"


def test_an_answer_as_it_is_is_read() -> None:
    with hostile(sends(_chat("ok"))) as (address, _):
        send = advisor.http_send({**MODEL, "base_url": f"{address}/v1"})
        assert asyncio.run(send([{"role": "user", "content": "hi"}], 10)) == "ok"


def test_registering_and_forgetting_read_a_bounded_answer() -> None:
    huge = b"x" * (connections.ANSWER_BYTES + 1)
    with hostile(sends(huge)) as (address, _):
        with pytest.raises(connections.TooLong):
            connections.bounded_post(f"{address}/api/v1/auth/register/anonymous-agent", json={})
        with pytest.raises(connections.TooLong):
            connections.bounded_delete(f"{address}/api/v1/players/me", headers={})


# --- S5: the connection really made is the one checked ----------------------------------------


def test_the_connection_is_checked_before_a_byte_of_the_request_leaves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(connections, "peer_refusal", lambda *_: "not this one")
    with hostile(sends(_chat("ok"))) as (address, seen):
        with pytest.raises(connections.NotOurs, match="not this one"):
            connections.bounded_get(f"{address}/v1/models")
        send = advisor.http_send({**MODEL, "base_url": f"{address}/v1"})
        with pytest.raises(connections.NotOurs, match="not this one"):
            asyncio.run(send([{"role": "user", "content": "the private question"}], 10))
    assert seen == [], "the server got nothing"


def test_whom_the_connection_reached_decides(monkeypatch: pytest.MonkeyPatch) -> None:
    kernel = Kernel(monkeypatch, 11434, ME)
    assert connections.peer_refusal("127.0.0.1", 11434, True, False) == ""
    # a name on this machine that the connection left (a proxy from the environment)
    assert connections.peer_refusal("203.0.113.7", 3128, True, False) == connections.PEER_ELSEWHERE
    # a name checked as elsewhere that reached a port here (resolved again on connecting)
    kernel.uid = 4242
    assert connections.peer_refusal("127.0.0.1", 11434, False, False) == connections.NOT_OURS
    kernel.uid = SYSTEM
    assert connections.peer_refusal("127.0.0.1", 11434, True, False) == ""
    assert connections.peer_refusal("127.0.0.1", 11434, True, True) == connections.NOT_OURS


# --- S5, S6: where a key may go over plain http -----------------------------------------------


def test_a_key_over_plain_http_never_goes_through_a_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    assert connections.key_may_go_to("http://127.0.0.1:11434/v1")
    monkeypatch.setenv("http_proxy", "http://proxy.test:3128")
    assert not connections.key_may_go_to("http://127.0.0.1:11434/v1")
    assert not connections.key_may_go_to("http://localhost:8080")
    assert connections.key_may_go_to("https://model.test/v1"), "https: the certificate decides"


def test_a_key_never_goes_to_a_system_accounts_server(
    real_ours: Callable[..., bool], monkeypatch: pytest.MonkeyPatch,
) -> None:
    Kernel(monkeypatch, 11434, SYSTEM)
    url = "http://127.0.0.1:11434/v1"
    assert connections.ours(url), "a question may go to the system's Ollama"
    assert not connections.ours(url, with_key=True), "a key may not"
    home = brain.private_state()
    connections.keep_key(home, "sk-paid", url)
    send = advisor.http_send({**MODEL, "base_url": url}, home=home)
    with pytest.raises(connections.NotOurs):
        asyncio.run(send([{"role": "user", "content": "hi"}], 10))
    world = {"world": {"base_url": "http://127.0.0.1:11434"}}
    assert brain.world_refusal(world) == connections.NOT_OURS


# --- S7, S8: a model's answer is read in linear time, and deep nesting is a refusal -----------


def test_brackets_are_a_refusal_not_a_crash() -> None:
    deep = "{" + "[" * 100_000 + "}"
    assert head.parse_move(deep, {"wait"}) == (None, "the answer was not JSON")
    assert advisor.parse(deep, 3) is None
    assert lessons._json_object(deep)[0] is None
    assert connections.opencode_text("[" * 100_000) == ""


def test_finding_the_json_in_an_answer_takes_linear_time() -> None:
    braces = "{" * 64_000
    started = time.monotonic()
    assert advisor.parse(braces, 3) is None
    assert lessons._json_object(braces)[0] is None
    assert time.monotonic() - started < 0.2, "the search ran quadratic (32,000 took 0.3 s)"


def test_json_is_read_in_one_place() -> None:
    def direct(source: str) -> list[int]:
        return [n.lineno for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute) and n.func.attr == "loads"
                and getattr(n.func.value, "id", "") == "json"]

    found = {p.name: direct(p.read_text(encoding="utf-8")) for p in BRAIN.glob("*.py")}
    assert len(found.pop("files.py")) == 1, "files.loads is the one place"
    assert {name: lines for name, lines in found.items() if lines} == {}
    assert direct("import json\njson.loads(x)\n") == [2], "the guard sees a direct read"


# --- S9: an agent's output is bounded, and what it left running ends with it ------------------


def test_an_agent_that_prints_past_its_limit_is_stopped() -> None:
    eight_mb = [sys.executable, "-c", "import sys; sys.stdout.write('x' * 8_000_000)"]
    with pytest.raises(connections.TooMuchOutput):
        connections.run_in_group(eight_mb, timeout=30)


def test_what_an_agent_left_running_ends_with_its_answer() -> None:
    done = connections.run_in_group(["sh", "-c", "sleep 60 & echo $!"], timeout=10)
    pid = int(done.stdout.strip())
    for _ in range(40):
        stat = Path(f"/proc/{pid}/stat")
        if not stat.exists() or stat.read_text().split(") ")[1].startswith("Z"):
            break
        time.sleep(0.05)
    else:
        os.kill(pid, 9)
        pytest.fail("the agent's child outlived its answer")


def test_an_agent_that_runs_too_long_still_times_out() -> None:
    with pytest.raises(subprocess.TimeoutExpired):
        connections.run_in_group(["sleep", "10"], timeout=0.3)


# --- S10: no Python of the scripts reads modules from the folder it runs in -------------------


SCRIPTS = ("shikigon-wake", "shikigon-terminal")
_CALL = re.compile(r"""(?:\bpython3?|/python)"?((?:\s+-[A-Za-z]+)*)\s+-[cm]\b""")


def unisolated(text: str) -> list[str]:
    """Each ``python -c``/``python -m`` without ``-I``: it puts the folder it runs in before the
    standard library, and a ``json.py`` planted there runs (marketplace review #12552)."""
    return [m.group(0) for m in _CALL.finditer(text) if "-I" not in m.group(1).split()]


def test_every_python_call_of_the_scripts_is_isolated() -> None:
    found = {name: unisolated((BRAIN / name).read_text(encoding="utf-8")) for name in SCRIPTS}
    assert {name: calls for name, calls in found.items() if calls} == {}
    assert sum(len(_CALL.findall((BRAIN / n).read_text())) for n in SCRIPTS) >= 4
    assert unisolated('"$venv/bin/python" -c \'import json\'') != [], "the guard sees one"


# --- S12, S13: what the brain writes ----------------------------------------------------------


def test_a_private_file_is_written_through_a_temporary_file_of_its_own(tmp_path: Path) -> None:
    target = tmp_path / "elsewhere"
    target.write_text("untouched")
    path = brain.credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    (path.parent / "credentials.tmp").symlink_to(target)  # the name used until 0.1.11
    brain.write_private(path, {"api_key": "AGENT-a:secret"})
    assert target.read_text() == "untouched", "the key went through a link"
    assert path.stat().st_mode & 0o777 == 0o600


def test_status_never_passes_its_bound() -> None:
    brain.write_status({"mood": "calm", "last": {"action": "wait", "ok": True},
                        "later_field": "x" * (brain.STATUS_BYTES + 1)})
    written = brain.state_dir() / "status.json"
    assert written.stat().st_size <= brain.STATUS_BYTES
    assert files.loads(written.read_text())["cut"] is True
