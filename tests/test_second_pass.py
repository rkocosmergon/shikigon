"""What the second pass on 0.1.13's candidate found (cos20 #492, 22.10.2026, B1–B8).

A fresh session read the whole plugin at ``22802ba`` the way the marketplace reviewer does, the
pinned wheels included. Each test here is red against ``22802ba``: keys bound to no origin
(B1, B3), a name and password in an address sent as a key where no key may go (B2), programs
started in the brain's working folder (B6), the terminal's key in its environment (B7), a
version without its changelog heading (B8). Building B7 showed the terminal did not open at
all at ``22802ba``: its world check ran the brain with ``python -I``, which leaves the brain's
own folder off ``sys.path``. The tests take the real way: a server on a socket, the brain's own
commands, the terminal script itself.
"""

from __future__ import annotations

import ast
import asyncio
import io
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "brain"))

import advisor
import connections
import shikigon_brain as brain
from conftest import hostile, sends, version_answer
from test_sending import ASK, ME, Kernel, _chat

ROOT = Path(__file__).resolve().parent.parent
SYSTEM = 100  # an account below UID_MIN: a system service
ELSEWHERE = "203.0.113.7"  # TEST-NET-3: never this machine, never reached


@pytest.fixture(autouse=True)
def _private_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    for name in ("http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(name, raising=False)


# --- B1: the key named by api_key_env goes only to the address of its section -------------------


def test_the_key_named_in_the_config_goes_only_to_the_address_of_its_section(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROVIDER_KEY", "sk-provider")
    own = {"base_url": "https://provider.example/v1", "model": "m", "api_key_env": "PROVIDER_KEY"}
    cfg = {"brain": {"advisor": own}}
    home = brain.state_dir()
    with hostile(sends(_chat("OK"))) as (address, seen):
        # the player tries another address in the panel, then asks it something
        line = json.dumps({"connection": f"http:{address}/v1", "model": "m"}) + "\n"
        monkeypatch.setattr(sys, "stdin", io.StringIO(line))
        assert brain.advisor_command(cfg, "try", [])["ok"] is True
        conf = advisor.settings(cfg, home)
        assert asyncio.run(advisor.sender(conf, home)(ASK, 10)) == "OK"
    assert len(seen) == 2, "the probe and the question reached the picked address"
    assert all("authorization" not in headers for headers in seen), "without the provider's key"
    # the address of its own section still gets it
    provider = "https://provider.example/v1/chat/completions"
    assert connections.secret_for(home, conf, provider) == "sk-provider"


# --- B3: a key kept by 0.1.3 or earlier follows no pick -----------------------------------------


def test_a_key_kept_by_0_1_3_goes_to_no_address_the_player_picks_later() -> None:
    home = brain.private_state()
    (home / "advisor_key").write_text("sk-old")
    with hostile(sends(_chat("OK"))) as (address, seen):
        connections.choose(home, {"via": "http", "base_url": f"{address}/v1", "model": "m"})
        conf = advisor.settings({}, home)
        assert asyncio.run(advisor.sender(conf, home)(ASK, 10)) == "OK"
    assert len(seen) == 1 and "authorization" not in seen[0]


# --- B2: a name and a password in an address are a key ------------------------------------------


def test_a_name_and_password_in_an_address_count_as_a_key() -> None:
    assert connections.credentials_in("https://me:pw@llm.example/v1")
    assert connections.credentials_in("http://me@llm.example/v1")
    assert not connections.credentials_in("http://llm.example/v1")


def test_a_password_in_an_address_never_goes_over_plain_http_to_another_machine() -> None:
    url = f"http://me:pw@{ELSEWHERE}:11434/v1"
    started = time.monotonic()
    # Since 0.1.13 nothing goes over plain http to another machine (decision A), so the rule
    # for the talk says no before the one for keys has to.
    assert connections.models_at(url, connections.bounded_get)["why"] == connections.TALK_ONLY_SAFE
    with pytest.raises(connections.AgentError, match="goes only to this machine"):
        connections.bounded_get(url + "/models")  # the same rule where it is sent
    send = advisor.http_send({"base_url": url, "model": "m", "timeout_s": 1})
    with pytest.raises(connections.AgentError, match="goes only to this machine"):
        asyncio.run(send(ASK, 10))
    assert time.monotonic() - started < 1, "refused before any connection was tried"


def test_a_password_in_an_address_never_goes_to_a_system_accounts_server(
    real_ours: Callable[..., bool], monkeypatch: pytest.MonkeyPatch,
) -> None:
    with hostile(sends(_chat("OK"))) as (address, seen):
        port = int(address.rsplit(":", 1)[1])
        url = address.replace("http://", "http://me:pw@") + "/v1"
        Kernel(monkeypatch, port, SYSTEM)
        assert connections.models_at(url, connections.bounded_get)["why"] == connections.NOT_OURS
        send = advisor.http_send({"base_url": url, "model": "m", "timeout_s": 5})
        with pytest.raises(connections.NotOurs):
            asyncio.run(send(ASK, 10))
        assert seen == [], "the system account's server got nothing"
        Kernel(monkeypatch, port, ME)  # the player's own server gets it, as before
        assert asyncio.run(send(ASK, 10)) == "OK"
    assert len(seen) == 1 and seen[0]["authorization"].startswith("Basic ")


# --- B6: every program the brain starts runs in an empty folder ---------------------------------


def test_every_program_the_brain_starts_runs_in_an_empty_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = tmp_path / "bin" / "pi"
    stub.parent.mkdir()
    stub.write_text('#!/bin/bash\nmise use -g --quiet "pi" || exit 1\n')  # Omarchy's placeholder
    monkeypatch.setattr(connections.shutil, "which",
                        lambda cmd: {"pi": str(stub), "mise": "/usr/bin/mise"}.get(cmd))
    monkeypatch.chdir(tmp_path)  # a working folder with something in it
    started: list[tuple[str, bool]] = []

    def run(argv: list[str], **kwargs: Any) -> Any:
        folder = Path(kwargs.get("cwd") or ".").resolve()
        empty = folder.is_dir() and not any(folder.iterdir()) and folder != tmp_path
        started.append((argv[0], empty))
        listing = SimpleNamespace(returncode=0, stdout="provider  model\nollama  m\n", stderr="")
        return version_answer(argv) or listing

    connections.installed("pi", run=run)
    connections.agent_models("pi", run=run)
    connections.run_agent({"agent": "pi"}, "question", 5.0, run=run)
    # its version before the listing and before the question (cos20 #492, finding 11)
    assert started == [("mise", True), ("pi", True), ("pi", True), ("pi", True), ("pi", True)]


def test_no_program_is_started_but_through_the_empty_folder() -> None:
    """The guard against a new start beside it: in the brain, an injected runner (a parameter
    named ``run``) is called only by ``in_empty_folder``, and a process is created only by
    ``run_in_group``, which ``in_empty_folder`` is given."""
    allowed = {"in_empty_folder": {"run"}, "run_in_group": {"subprocess.Popen"}}
    found = []
    for path in sorted((ROOT / "brain").glob("*.py")):
        for function in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(function, ast.FunctionDef):
                found += [f"{path.name}:{line} {function.name} → {what}"
                          for line, what in _starts(function)
                          if what not in allowed.get(function.name, set())]
    assert found == []


def _starts(function: ast.FunctionDef) -> list[tuple[int, str]]:
    """Where ``function`` starts a program: its injected runner, ``subprocess``, ``os``."""
    params = {arg.arg for arg in function.args.args + function.args.kwonlyargs}
    spawning = {"run", "Popen", "call", "check_call", "check_output", "system", "popen"}
    starts = []
    for call in ast.walk(function):
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        if isinstance(func, ast.Name) and func.id == "run" and "run" in params:
            starts.append((call.lineno, "run"))
        elif isinstance(func, ast.Attribute) and func.attr.startswith("create_subprocess"):
            starts.append((call.lineno, func.attr))
        elif isinstance(func, ast.Attribute) and func.attr in spawning:
            module = getattr(func.value, "id", "")
            if module in ("subprocess", "os"):
                starts.append((call.lineno, f"{module}.{func.attr}"))
    return starts


# --- B7: the terminal gets the key over a pipe, never in its environment -------------------------

KEY = "AGENT-term0001:geheim000000000000000000"

# Stands in for cosmergon-dashboard: writes down what it got, then ends.
DASHBOARD = """#!{python}
import json, os, pathlib, sys
fd = sys.argv[sys.argv.index("--api-key-fd") + 1] if "--api-key-fd" in sys.argv else ""
seen = {{"argv": sys.argv, "fd": os.read(int(fd), 4096).decode().strip() if fd else "",
        "environ": pathlib.Path("/proc/self/environ").read_bytes().decode(errors="replace"),
        "advisor": os.environ.get("COSMERGON_ADVISOR_CMD", "")}}
pathlib.Path(os.environ["SEEN"]).write_text(json.dumps(seen))
"""


def _terminal(tmp_path: Path, world: str) -> tuple[int, dict[str, Any] | None]:
    """Run ``brain/shikigon-terminal`` as the bar does, with a stand-in dashboard."""
    home = tmp_path / "home"
    bin_ = home / ".local" / "share" / "shikigon" / "venv" / "bin"
    bin_.mkdir(parents=True)
    (bin_ / "python").symlink_to(sys.executable)
    (bin_ / "cosmergon-dashboard").write_text(DASHBOARD.format(python=sys.executable))
    (bin_ / "cosmergon-dashboard").chmod(0o755)
    state = tmp_path / "state" / "shikigon"
    state.mkdir(parents=True)
    creds = {"agent_id": "a-1", "api_key": KEY, "world": world}
    (state / "credentials.json").write_text(json.dumps(creds))
    seen = tmp_path / "seen.json"
    env = {**os.environ, "HOME": str(home), "XDG_STATE_HOME": str(tmp_path / "state"),
           "XDG_CONFIG_HOME": str(tmp_path / "config"), "SEEN": str(seen)}
    done = subprocess.run([str(ROOT / "brain" / "shikigon-terminal")], env=env, input="\n",
                          capture_output=True, text=True, timeout=60, check=False)
    return done.returncode, json.loads(seen.read_text()) if seen.exists() else None


def test_the_terminal_opens_and_gets_the_key_over_a_pipe_only(tmp_path: Path) -> None:
    code, seen = _terminal(tmp_path, "https://cosmergon.com:443")
    assert code == 0 and seen is not None, "the terminal opened (at 22802ba the check broke)"
    assert KEY not in seen["environ"], "not in /proc/<pid>/environ"
    assert KEY not in " ".join(seen["argv"])
    assert seen["fd"] == KEY and "--api-key-fd" in seen["argv"]
    assert seen["advisor"].endswith(" ask"), "the advisor command is there, without a key"


def test_the_terminal_stays_closed_for_a_shikigon_of_another_world(tmp_path: Path) -> None:
    code, seen = _terminal(tmp_path, "https://other.example:443")
    assert code == 1 and seen is None, "no dashboard, and no key handed anywhere"


# --- B8: the version the manifest ships has its changelog heading -------------------------------


def test_the_changelog_names_the_version_the_manifest_ships() -> None:
    version = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))["version"]
    headings = [line[3:].split(" ")[0] for line in
                (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").splitlines()
                if line.startswith("## ")]
    released = [heading for heading in headings if heading != "Unreleased"]
    assert released and released[0] == version, (
        f"manifest.json says {version}, the newest version in CHANGELOG.md is "
        f"{released[0] if released else 'none'}")
