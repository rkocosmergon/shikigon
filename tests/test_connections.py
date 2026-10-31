"""Tests for "who answers": finding connections, asking an agent, keeping the choice.

No network and no agent is ever started: commands are stubs that record how they were
called. OpenCode is no longer asked from here (0.1.13, cos20 #492, seventh review).
"""

from __future__ import annotations

import io
import json
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "brain"))

import advisor
import connections
import shikigon_brain as brain
from conftest import fake_mise, is_command, version_answer

# `pi --list-models` on the device (pi 1.2.0, 14.10.2026): its first two lines as printed,
# without their trailing blanks; the third is a provider's model in the same columns, as pi
# would list one after /login (none is signed in on the device).
PI_MODELS_SAMPLE = """\
provider        model           context  max-out  thinking  images
ollama-macmini  qwen3.5:9b-64k  128K     16.4K    no        no
anthropic       claude-sonnet-5-5  200K  64K      yes       yes
"""


@pytest.fixture(autouse=True)
def _private_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


class Commands:
    """Stands in for ``subprocess.run``: records every call, answers what it was told."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "",
                 fail: Exception | None = None) -> None:
        self.answer = SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)
        self.fail = fail
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.versions: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> Any:
        if (said := version_answer(argv)) is not None:
            self.versions.append((list(argv), kwargs))
            return said
        self.calls.append((list(argv), kwargs))
        if self.fail is not None:
            raise self.fail
        return self.answer


def _answer(status: int, body: Any = None) -> Any:
    def raise_for_status() -> None:
        if status >= 400:
            raise RuntimeError(f"HTTP {status}")

    return SimpleNamespace(status_code=status, json=lambda: body, raise_for_status=raise_for_status)


# --- What is installed is found without starting it --------------------------------------------


def _on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, content: str) -> Path:
    path = tmp_path / "bin" / name
    path.parent.mkdir(exist_ok=True)
    path.write_text(content)
    mise = fake_mise(tmp_path / "usr")
    monkeypatch.setattr(connections.shutil, "which", lambda cmd, **_: {
        name: str(path), "mise": str(mise)}.get(cmd))
    return path


STUB = '#!/bin/bash\nexport MISE_MINIMUM_RELEASE_AGE=0\nmise use -g --quiet "codex" || exit 1\n'


def test_a_placeholder_is_never_started_only_mise_is_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Seen on the device (11.10.2026): `opencode --version` on the stub installed OpenCode."""
    _on_path(tmp_path, monkeypatch, "codex", STUB)
    not_there = Commands(returncode=1)
    assert connections.installed("codex", run=not_there) is False
    there = Commands(returncode=0)
    assert connections.installed("codex", run=there) is True
    for commands in (not_there, there):
        assert [argv for argv, _ in commands.calls] == [
            [connections.program("mise", None), "where", "codex"]]


def test_a_program_of_the_users_own_counts_and_a_missing_one_does_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _on_path(tmp_path, monkeypatch, "pi", "\x7fELF" + "x" * 5000)
    nothing = Commands()
    assert connections.installed("pi", run=nothing) is True
    assert connections.installed("codex", run=nothing) is False  # not on the path at all
    assert nothing.calls == []


# --- The list -----------------------------------------------------------------------------------


def test_the_list_names_agents_first_then_what_runs_here_then_the_configs_own() -> None:
    def get(url: str, **_: Any) -> Any:
        if url.startswith("http://localhost:11434"):
            return _answer(200, {"data": [{"id": "qwen3.5:9b"}, {"id": "llama3.2:3b"}]})
        raise OSError("refused")

    conf = {"base_url": "http://model-box.test:11434/v1", "model": "qwen3.5:9b"}
    rows = connections.found(conf, get, is_installed=lambda agent: True,
                             is_own=lambda url: True, why_not=lambda agent: "")  # Codex too
    assert [row["id"] for row in rows] == [
        "agent:pi", "http:http://localhost:11434/v1", "http:http://model-box.test:11434/v1"]
    assert not connections.AGENTS["codex"]["proven"], "offered only once its file probe has run"
    assert rows[0]["text"] == "pi — your own keys" and rows[0]["sign_in"] == ["pi"]
    assert rows[1]["text"] == "Ollama on this machine" and rows[1]["models"][0] == "qwen3.5:9b"
    assert rows[2]["text"] == "qwen3.5:9b at model-box.test"


def test_pis_models_come_from_its_table() -> None:
    commands = Commands(stdout=PI_MODELS_SAMPLE)
    assert connections.agent_models("pi", run=commands) == [
        "ollama-macmini/qwen3.5:9b-64k", "anthropic/claude-sonnet-5-5"]
    assert is_command(commands.calls[0][0], ["pi", "--offline", "--list-models"])
    assert connections.table_models("provider  model\n") == [], "a header alone is no model"


def test_nothing_found_is_an_empty_list_not_an_error() -> None:
    def get(url: str, **_: Any) -> Any:
        raise OSError("refused")

    assert connections.found({}, get, is_installed=lambda agent: False) == []


def test_an_address_says_its_models_or_that_it_wants_a_key_or_that_it_is_away() -> None:
    there = connections.models_at(
        "https://x/v1/", lambda url, **_: _answer(200, {"data": [{"id": "a"}, {"id": "b"}]}))
    assert there == {"ok": True, "models": ["a", "b"], "why": ""}
    locked = connections.models_at("https://x/v1", lambda url, **_: _answer(401))
    assert locked["needs_key"] is True and locked["ok"] is False

    def away(url: str, **_: Any) -> Any:
        raise OSError("refused")

    assert "no answer from x " in connections.models_at("https://x/v1", away)["why"]
    sent: dict[str, Any] = {}
    connections.models_at("https://x/v1", lambda url, **kw: sent.update(kw) or _answer(200, {}),
                          key="sk-secret")
    assert sent["headers"] == {"Authorization": "Bearer sk-secret"}
    # 0.1.4: no key over plain http to another machine (cos20 #492, #11798); said by the door
    # itself (0.1.13, seventh review), so the address is a test address and no name is resolved
    refused = connections.models_at("http://192.0.2.1/v1", connections.bounded_get,
                                    key="sk-secret")  # fmt: skip
    assert refused["why"] == connections.TALK_ONLY_SAFE, "the key does not leave in plain text"


@pytest.mark.parametrize(("url", "may"), [
    ("https://openrouter.ai/api/v1", True),
    ("http://localhost:11434/v1", True),
    ("http://127.0.0.1:1234/v1", True),
    ("http://[::1]:1234/v1", True),
    # 0.1.4: no key in plain text to a network that only looks like home (cos20 #492, #11798)
    ("http://macmini.local:11434/v1", False),
    ("http://macmini:11434/v1", False),
    ("http://10.20.30.40:11434/v1", False),
    ("http://192.168.1.50/v1", False),
    ("http://169.254.1.1/v1", False),
    ("http://localhost.evil.com/v1", False),
    ("http://api.example.com/v1", False),
    ("http://8.8.8.8/v1", False),
    ("ftp://localhost.evil.com/v1", False),
])
def test_a_key_goes_only_encrypted_or_to_this_machine(url: str, may: bool) -> None:
    assert connections.key_may_go_to(url) is may


def test_listing_models_with_a_key_over_plain_http_asks_nothing() -> None:
    found = connections.models_at("http://198.51.100.7/v1", connections.bounded_get,
                                  key="sk-paid")  # the door's own rule, before any connection
    assert found["ok"] is False and found["why"] == connections.TALK_ONLY_SAFE
    assert "https" in found["why"]


# --- Asking an agent: narrow, in the neutral folder, its own words on failure --------------------


def test_each_agent_is_called_in_its_narrowest_setting() -> None:
    codex = connections.agent_argv({"agent": "codex"})
    assert codex[:2] == ["codex", "exec"]
    assert codex[codex.index("--sandbox") + 1] == "read-only"
    assert "--approve-for-me" not in codex and "--full-auto" not in codex
    pi = connections.agent_argv({"agent": "pi", "agent_model": "ollama-macmini/qwen3.5:9b-64k"})
    assert pi[:2] == ["pi", "-p"]
    # pi's tools, session, extensions, MCP, skills, templates and context files: all off
    for switch in ("--no-tools", "--no-session", "--no-extensions", "--no-mcp", "--no-skills",
                   "--no-prompt-templates", "--no-context-files", "--no-approve", "--offline"):
        assert switch in pi, switch
    assert pi[pi.index("--thinking") + 1] == "off"
    assert pi[pi.index("--system-prompt") + 1] == connections.PI_SYSTEM
    assert pi[pi.index("--append-system-prompt") + 1] == connections.NO_TOOLS
    assert pi[pi.index("--model") + 1] == "ollama-macmini/qwen3.5:9b-64k"


def test_the_agent_runs_in_the_neutral_folder_without_a_terminal_and_is_told_not_to_act() -> None:
    commands = Commands(stdout="OK")
    text = connections.prompt_text([
        {"role": "system", "content": "You advise."},
        {"role": "user", "content": "one?"},
        {"role": "assistant", "content": "first"},
        {"role": "user", "content": "two?"},
    ])
    rule = "Do not run commands, do not read or write files."
    assert text.index("You advise.") < text.index(rule)
    assert text.index("do not read or write files.") < text.index("(Asked before: one?)")
    assert text.index("one?") < text.index("(You answered before: first)") < text.index("two?")
    assert text.endswith("two?"), "the question stays last: its language line is the final word"
    assert connections.run_agent({"agent": "pi"}, text, 30.0, run=commands) == "OK"
    argv, kwargs = commands.calls[0]
    assert kwargs["input"] == text and kwargs["timeout"] == 30.0
    assert text not in argv
    assert "PATH" in kwargs["env"], "the user's own environment stays"
    assert kwargs["cwd"] == "/", "only the system writes there, and it names nobody"


@pytest.mark.parametrize(  # an agent never tried on a device is not even started (#492, K28)
    "agent", sorted(n for n, spec in connections.AGENTS.items() if spec.get("proven"))
)
def test_the_players_words_never_stand_in_the_agents_arguments(agent: str) -> None:
    """Arguments stand in /proc/<pid>/cmdline for every local user (#11550): stdin only."""
    commands = Commands(stdout="OK")
    secret = "my-private-question-7f3a"
    prompt = connections.prompt_text([{"role": "system", "content": "You advise."},
                                      {"role": "user", "content": secret}])
    connections.run_agent({"agent": agent, "agent_model": "m/x"}, prompt, 5.0, run=commands)
    argv, kwargs = commands.calls[0]
    assert not any(secret in part for part in argv), argv
    assert secret in kwargs["input"]


def test_an_agent_that_fails_says_so_in_its_own_last_line() -> None:
    refused = Commands(returncode=1, stderr="warming up\nERROR: unexpected status 401 Unauthorized")
    with pytest.raises(connections.AgentError, match=r"^pi is not signed in\.$"):
        connections.run_agent({"agent": "pi"}, "x", 5.0, run=refused)
    broken = Commands(returncode=1, stderr="warming up\nError: model not found")
    with pytest.raises(connections.AgentError, match="pi: Error: model not found"):
        connections.run_agent({"agent": "pi"}, "x", 5.0, run=broken)
    silent = Commands(returncode=0, stdout="")
    with pytest.raises(connections.AgentError, match="pi: exit 0"):
        connections.run_agent({"agent": "pi"}, "x", 5.0, run=silent)
    slow = Commands(fail=subprocess.TimeoutExpired(cmd="pi", timeout=5))
    with pytest.raises(connections.AgentError, match="pi did not answer within 5 s"):
        connections.run_agent({"agent": "pi"}, "x", 5.0, run=slow)
    with pytest.raises(connections.AgentError, match="could not be started"):
        connections.run_agent({"agent": "pi"}, "x", 5.0, run=Commands(fail=OSError("gone")))


# --- An agent's allowance is the player's --------------------------------------------------------


def test_through_an_agent_there_is_no_advice_on_every_move() -> None:
    codex = {"via": "agent", "agent": "codex", "every": 1}
    assert advisor.every(codex) == connections.AGENT_MIN_EVERY
    assert advisor.every({"via": "agent", "agent": "codex", "every": 40}) == 40
    assert advisor.every({"base_url": "http://x/v1", "model": "m", "every": 1}) == 1


async def test_a_move_waits_the_configs_seconds_a_question_may_take_longer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    waited: list[float] = []

    def run_agent(conf: dict[str, Any], prompt: str, timeout: float, run: Any) -> str:
        waited.append(timeout)
        return "OK"

    monkeypatch.setattr(connections, "run_agent", run_agent)
    conf = {"via": "agent", "agent": "codex", "timeout_s": 20}
    await advisor.sender(conf)([{"role": "user", "content": "x"}], 10)
    send = advisor.sender(conf, patience=advisor.ANSWER_PATIENCE)
    await send([{"role": "user", "content": "x"}], 10)
    assert waited == [20.0, connections.AGENT_ANSWER_S]


async def test_a_question_an_agent_cannot_answer_says_the_agents_reason() -> None:
    async def send(messages: list[dict[str, str]], max_tokens: int) -> str:
        raise connections.AgentError("pi is not signed in.")

    conf = {"via": "agent", "agent": "pi", "max_per_day": 0}
    turn = await advisor.answer(conf, brain.state_dir(), "what now?", "ctx", [], send)
    assert turn["ok"] is False and turn["model"] == "pi"
    assert turn["why"] == "no answer (pi is not signed in.)"
    unproven = {"via": "agent", "agent": "codex", "max_per_day": 0}
    assert not advisor.configured(unproven)
    nobody = await advisor.answer({}, brain.state_dir(), "what now?", "ctx", [], send)
    assert nobody["why"] == "nobody is picked to answer yet"


# --- The choice: tried first, then kept; the config file stays for the rest ---------------------


def test_the_panels_choice_goes_over_the_config_file_and_the_file_keeps_the_rest() -> None:
    cfg = {"brain": {"advisor": {"base_url": "http://x/v1", "model": "mine", "max_per_day": 7}}}
    home = brain.state_dir()
    assert advisor.who(advisor.settings(cfg, home)) == "mine"
    connections.choose(home, {"via": "agent", "agent": "pi"})
    conf = advisor.settings(cfg, home)
    assert advisor.who(conf) == "pi" and advisor.configured(conf)
    assert conf["max_per_day"] == 7 and conf["reasoning_effort"] == "none"
    assert advisor.who(advisor.settings(cfg)) == "mine", "without the state folder: the files only"


async def _says_ok(messages: list[dict[str, str]], max_tokens: int) -> str:
    return "OK"


def test_a_connection_is_taken_only_after_it_answered(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = brain.load_config()
    home = brain.state_dir()
    replies = iter([connections.AgentError("pi: no provider"), "OK"])

    def sender(conf: dict[str, Any], state: Any = None, patience: int = 1) -> Any:
        async def send(messages: list[dict[str, str]], max_tokens: int) -> str:
            reply = next(replies)
            if isinstance(reply, Exception):
                raise reply
            return reply

        return send

    monkeypatch.setattr(advisor, "sender", sender)
    failed = brain.advisor_command(cfg, "try", ["agent:pi", "ollama/qwen3.5:9b"])
    assert failed == {"ok": False, "why": "pi: no provider"}
    assert connections.chosen(home) == {}, "a connection that did not answer is not kept"
    taken = brain.advisor_command(cfg, "try", ["agent:pi", "ollama/qwen3.5:9b"])
    assert taken["ok"] is True and taken["said"] == "OK" and taken["label"] == "pi"
    assert connections.chosen(home) == {
        "via": "agent", "agent": "pi", "agent_model": "ollama/qwen3.5:9b"}
    # another connection replaces it whole (seen on the device: the agent's keys stayed behind)
    connections.choose(home, {**connections.chosen(home), "advise": 15, "every": 15})
    monkeypatch.setattr(advisor, "sender", lambda conf, state=None, patience=1: _says_ok)
    # an address comes on stdin, as the bar sends it (0.1.4: never as an argument)
    monkeypatch.setattr(brain, "json_on_stdin", lambda: {"connection": "http:http://x/v1",
                                                          "model": "m"})
    assert brain.advisor_command(cfg, "try", [])["ok"] is True
    assert connections.chosen(home) == {
        "advise": 15, "every": 15, "via": "http", "base_url": "http://x/v1", "model": "m"}
    assert brain.advisor_command(cfg, "try", ["agent:claude"])["ok"] is False  # not offered
    assert brain.advisor_command(cfg, "try", ["agent:codex"])["ok"] is False  # not proven yet
    monkeypatch.setattr(brain, "json_on_stdin", lambda: {"connection": "http:http://x/v1"})
    assert brain.advisor_command(cfg, "try", [])["ok"] is False  # no model named


def _plays(decider: Any) -> str:
    """Who chooses the move: the decider itself, or the one inside the talking wrapper; the rule
    tree inside the brain's careful shell (``contracts.Careful``) counts as the tree."""
    plays = getattr(decider, "inner", decider)
    return type(plays.tree if type(plays).__name__ == "Careful" else plays).__name__


def test_advice_on_moves_is_a_switch_of_its_own_and_forget_drops_the_choice() -> None:
    cfg = brain.load_config()
    home = brain.state_dir()
    connections.choose(home, {"via": "http", "base_url": "http://x/v1", "model": "m"})
    assert brain.advisor_now(cfg)["advise"] == 0, "picking who answers does not switch advice on"
    # The move is the tree's; with an advisor set up it also talks (cos20 #526): the wrapper.
    assert _plays(brain.make_decider(cfg)) == "TreeDecider"
    now = brain.advisor_command(cfg, "advise", ["15"])["now"]
    assert now["advise"] == 15 and now["picked"] and now["label"] == "m"
    assert _plays(brain.make_decider(cfg)) == "AdvisedDecider"
    assert brain.advisor_command(cfg, "advise", ["off"])["now"]["advise"] == 0
    assert _plays(brain.make_decider(cfg)) == "TreeDecider"
    assert connections.chosen(home) == {
        "via": "http", "base_url": "http://x/v1", "model": "m", "advise": "off"}, "no rhythm left"
    connections.keep_key(home, "sk-secret\n", "http://x/v1")
    assert brain.advisor_command(cfg, "forget", [])["now"]["picked"] is False
    assert not (home / "advisor.json").exists() and not (home / "advisor_key").exists()


def test_the_panels_switch_leaves_a_decider_someone_installed_alone() -> None:
    cfg = {"brain": {"decider": "oracle"}}
    assert brain.decider_name(cfg, {"advise": 15}) == "oracle"
    assert brain.decider_name({"brain": {"decider": "advised"}}, {"advise": "off"}) == "tree"
    assert brain.decider_name({"brain": {"decider": "advised"}}, {}) == "advised"


def test_a_key_comes_in_on_stdin_and_is_kept_for_the_owner_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    line = '{"key": "sk-secret\\n", "where": "http://x/v1"}\n'
    monkeypatch.setattr(sys, "stdin", io.StringIO(line))
    assert brain.advisor_command(brain.load_config(), "key", []) == {"ok": True}
    path = brain.state_dir() / "advisor_key"
    assert json.loads(path.read_text()) == {"origin": "http://x:80", "key": "sk-secret"}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert connections.kept_key(brain.state_dir(), "http://x/v1") == "sk-secret"


def test_what_an_agent_is_sent_holds_no_text_another_player_wrote() -> None:
    """The task for an agent may only hold our notes, the server's words and numbers: an
    agent can act, and text from other players would be a way to steer it."""
    from test_advisor import _state

    state = _state()
    told = json.dumps(advisor.move_messages(state, [("place_cells", {"preset": "blinker"})]))
    others = {str(n) for n in _names_of_others(state)}
    assert others, "the fixture knows other agents, or this test proves nothing"
    assert not [name for name in others if name in told]


def _names_of_others(state: Any) -> list[str]:
    raw = json.loads(
        (Path(__file__).parent / "fixtures" / "state_newborn.json").read_text(encoding="utf-8"))
    own = str(raw.get("agent_name") or getattr(state, "agent_name", ""))
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("agent_name", "owner_name", "username") and isinstance(value, str):
                    found.append(value)
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(raw)
    return [name for name in found if name and name != own and len(name) > 3]
