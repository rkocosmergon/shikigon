"""Tests for "who answers": finding connections, asking an agent, keeping the choice.

No network and no agent is ever started: commands are stubs that record how they were
called. The OpenCode output is a sample taken from the device (OpenCode 1.19.0, 11.10.2026).
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

OPENCODE_SAMPLE = """\
{"type":"step_start","timestamp":1791708796549,"sessionID":"ses_x","part":{"id":"prt_1","messageID":"msg_1","sessionID":"ses_x","type":"step-start"}}
{"type":"reasoning","timestamp":1791708798028,"sessionID":"ses_x","part":{"id":"prt_2","type":"reasoning","text":"The user is asking me to reply with a simple acknowledgment."}}
{"type":"text","timestamp":1791708798140,"sessionID":"ses_x","part":{"id":"prt_3","messageID":"msg_1","sessionID":"ses_x","type":"text","text":"OK"}}
{"type":"step_finish","timestamp":1791708798140,"sessionID":"ses_x","part":{"id":"prt_4","reason":"stop","type":"step-finish","tokens":{"total":7669,"input":7643,"output":26}}}
"""  # the lines as the program printed them

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

    def __call__(self, argv: list[str], **kwargs: Any) -> Any:
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
    monkeypatch.setattr(connections.shutil, "which", lambda cmd: {
        name: str(path), "mise": "/usr/bin/mise"}.get(cmd))
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
        assert [argv for argv, _ in commands.calls] == [["mise", "where", "codex"]]


def test_a_program_of_the_users_own_counts_and_a_missing_one_does_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _on_path(tmp_path, monkeypatch, "opencode", "\x7fELF" + "x" * 5000)
    nothing = Commands()
    assert connections.installed("opencode", run=nothing) is True
    assert connections.installed("codex", run=nothing) is False  # not on the path at all
    assert nothing.calls == []


# --- The list -----------------------------------------------------------------------------------


def test_the_list_names_agents_first_then_what_runs_here_then_the_configs_own() -> None:
    def get(url: str, **_: Any) -> Any:
        if url.startswith("http://localhost:11434"):
            return _answer(200, {"data": [{"id": "qwen3.5:9b"}, {"id": "llama3.2:3b"}]})
        raise OSError("refused")

    conf = {"base_url": "http://model-box.test:11434/v1", "model": "qwen3.5:9b"}
    rows = connections.found(conf, get, is_installed=lambda agent: True)  # Codex is there too
    assert [row["id"] for row in rows] == [
        "agent:opencode", "agent:pi",
        "http:http://localhost:11434/v1", "http:http://model-box.test:11434/v1"]
    assert rows[0]["text"] == "OpenCode — your own keys"
    assert not connections.AGENTS["codex"]["proven"], "offered only once its file probe has run"
    assert rows[1]["text"] == "pi — your own keys" and rows[1]["sign_in"] == ["pi"]
    assert rows[2]["text"] == "Ollama on this machine" and rows[2]["models"][0] == "qwen3.5:9b"
    assert rows[3]["text"] == "qwen3.5:9b at model-box.test"


def test_an_agents_models_name_the_players_own_first() -> None:
    listing = "opencode/big-pickle\nopencode/exo-free\nollama/qwen3.5:9b\nollama/big:latest\n\nnot a model\n"
    commands = Commands(stdout=listing)
    assert connections.agent_models("opencode", run=commands) == [
        "ollama/qwen3.5:9b", "ollama/big:latest", "opencode/big-pickle", "opencode/exo-free"]
    assert commands.calls[0][0] == ["opencode", "models"]
    assert connections.agent_models("codex", run=commands) == [], "Codex has no listing command"


def test_pis_models_come_from_its_table() -> None:
    commands = Commands(stdout=PI_MODELS_SAMPLE)
    assert connections.agent_models("pi", run=commands) == [
        "ollama-macmini/qwen3.5:9b-64k", "anthropic/claude-sonnet-5-5"]
    assert commands.calls[0][0] == ["pi", "--offline", "--list-models"]
    assert connections.table_models("provider  model\n") == [], "a header alone is no model"


def test_nothing_found_is_an_empty_list_not_an_error() -> None:
    def get(url: str, **_: Any) -> Any:
        raise OSError("refused")

    assert connections.found({}, get, is_installed=lambda agent: False) == []


def test_an_address_says_its_models_or_that_it_wants_a_key_or_that_it_is_away() -> None:
    there = connections.models_at(
        "http://x/v1/", lambda url, **_: _answer(200, {"data": [{"id": "a"}, {"id": "b"}]}))
    assert there == {"ok": True, "models": ["a", "b"], "why": ""}
    locked = connections.models_at("http://x/v1", lambda url, **_: _answer(401))
    assert locked["needs_key"] is True and locked["ok"] is False

    def away(url: str, **_: Any) -> Any:
        raise OSError("refused")

    assert "no answer from http://x/v1" in connections.models_at("http://x/v1", away)["why"]
    sent: dict[str, Any] = {}
    connections.models_at("http://x/v1", lambda url, **kw: sent.update(kw) or _answer(200, {}),
                          key="sk-secret")
    assert sent["headers"] == {"Authorization": "Bearer sk-secret"}


# --- Asking an agent: narrow, in an empty folder, its own words on failure ----------------------


def test_each_agent_is_called_in_its_narrowest_setting_with_the_prompt_last() -> None:
    codex = connections.agent_argv({"agent": "codex"}, "the task")
    assert codex[:2] == ["codex", "exec"] and codex[-1] == "the task"
    assert codex[codex.index("--sandbox") + 1] == "read-only"
    assert "--approve-for-me" not in codex and "--full-auto" not in codex
    opencode = connections.agent_argv(
        {"agent": "opencode", "agent_model": "ollama/qwen3.5:9b"}, "the task")
    assert opencode[:2] == ["opencode", "run"] and opencode[-1] == "the task"
    assert "--auto" not in opencode, "auto-approval would let it run commands unasked"
    assert opencode[opencode.index("-m") + 1] == "ollama/qwen3.5:9b"
    # OpenCode allows every operation by default: its call brings an agent that may do nothing
    assert opencode[opencode.index("--agent") + 1] == "shikigon-advisor"
    inline = json.loads(connections.AGENTS["opencode"]["env"]["OPENCODE_CONFIG_CONTENT"])
    assert inline["agent"]["shikigon-advisor"]["permission"] == {"*": "deny"}
    # Without these two a one-word answer took two minutes on the device, with them 4.5 s:
    # the model thought at length, and OpenCode asked a second time for a session title.
    assert inline["agent"]["shikigon-advisor"]["reasoningEffort"] == "none"
    assert inline["agent"]["title"] == {"disable": True}
    pi = connections.agent_argv(
        {"agent": "pi", "agent_model": "ollama-macmini/qwen3.5:9b-64k"}, "the task")
    assert pi[:2] == ["pi", "-p"] and pi[-1] == "the task"
    # pi's tools, session, extensions, MCP, skills, templates and context files: all off
    for switch in ("--no-tools", "--no-session", "--no-extensions", "--no-mcp", "--no-skills",
                   "--no-prompt-templates", "--no-context-files", "--no-approve", "--offline"):
        assert switch in pi, switch
    assert pi[pi.index("--thinking") + 1] == "off"
    assert pi[pi.index("--system-prompt") + 1] == connections.PI_SYSTEM
    assert pi[pi.index("--model") + 1] == "ollama-macmini/qwen3.5:9b-64k"


def test_the_agent_runs_in_an_empty_folder_without_a_terminal_and_is_told_not_to_act() -> None:
    commands = Commands(stdout=OPENCODE_SAMPLE)
    text = connections.prompt_text([
        {"role": "system", "content": "You advise."},
        {"role": "user", "content": "one?"},
        {"role": "assistant", "content": "first"},
        {"role": "user", "content": "two?"},
    ])
    assert text.index("You advise.") < text.index("Do not run commands, do not read or write files.")
    assert text.index("do not read or write files.") < text.index("(Asked before: one?)")
    assert text.index("one?") < text.index("(You answered before: first)") < text.index("two?")
    assert text.endswith("two?"), "the question stays last: its line on language must be the final word"
    assert connections.run_agent({"agent": "opencode"}, text, 30.0, run=commands) == "OK"
    argv, kwargs = commands.calls[0]
    assert argv[-1] == text
    assert kwargs["stdin"] == subprocess.DEVNULL and kwargs["timeout"] == 30.0
    assert "shikigon-advisor" in kwargs["env"]["OPENCODE_CONFIG_CONTENT"]
    assert "PATH" in kwargs["env"], "the user's own environment stays"
    assert "shikigon-ask-" in kwargs["cwd"] and not Path(kwargs["cwd"]).exists()  # gone again


def test_what_opencode_answered_is_its_text_not_its_thinking() -> None:
    assert connections.opencode_text(OPENCODE_SAMPLE) == "OK"
    assert connections.opencode_text("not json\n{}") == ""


def test_an_agent_that_fails_says_so_in_its_own_last_line() -> None:
    refused = Commands(returncode=1, stderr="warming up\nERROR: unexpected status 401 Unauthorized")
    with pytest.raises(connections.AgentError, match=r"^Codex is not signed in\.$"):
        connections.run_agent({"agent": "codex"}, "x", 5.0, run=refused)
    broken = Commands(returncode=1, stderr="warming up\nError: model not found")
    with pytest.raises(connections.AgentError, match="Codex: Error: model not found"):
        connections.run_agent({"agent": "codex"}, "x", 5.0, run=broken)
    silent = Commands(returncode=0, stdout="")
    with pytest.raises(connections.AgentError, match="Codex: exit 0"):
        connections.run_agent({"agent": "codex"}, "x", 5.0, run=silent)
    slow = Commands(fail=subprocess.TimeoutExpired(cmd="codex", timeout=5))
    with pytest.raises(connections.AgentError, match="Codex did not answer within 5 s"):
        connections.run_agent({"agent": "codex"}, "x", 5.0, run=slow)
    with pytest.raises(connections.AgentError, match="could not be started"):
        connections.run_agent({"agent": "codex"}, "x", 5.0, run=Commands(fail=OSError("gone")))


# --- An agent's allowance is the player's --------------------------------------------------------


def test_through_an_agent_there_is_no_advice_on_every_move() -> None:
    assert advisor.every({"via": "agent", "agent": "codex", "every": 1}) == connections.AGENT_MIN_EVERY
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
    await advisor.sender(conf, patience=advisor.ANSWER_PATIENCE)([{"role": "user", "content": "x"}], 10)
    assert waited == [20.0, connections.AGENT_ANSWER_S]


async def test_a_question_an_agent_cannot_answer_says_the_agents_reason() -> None:
    async def send(messages: list[dict[str, str]], max_tokens: int) -> str:
        raise connections.AgentError("OpenCode is not signed in.")

    conf = {"via": "agent", "agent": "opencode", "max_per_day": 0}
    turn = await advisor.answer(conf, brain.state_dir(), "what now?", "ctx", [], send)
    assert turn["ok"] is False and turn["model"] == "OpenCode"
    assert turn["why"] == "no answer (OpenCode is not signed in.)"
    unproven = {"via": "agent", "agent": "codex", "max_per_day": 0}
    assert not advisor.configured(unproven)
    nobody = await advisor.answer({}, brain.state_dir(), "what now?", "ctx", [], send)
    assert nobody["why"] == "nobody is picked to answer yet"


# --- The choice: tried first, then kept; the config file stays for the rest ---------------------


def test_the_panels_choice_goes_over_the_config_file_and_the_file_keeps_the_rest() -> None:
    cfg = {"brain": {"advisor": {"base_url": "http://x/v1", "model": "mine", "max_per_day": 7}}}
    home = brain.state_dir()
    assert advisor.who(advisor.settings(cfg, home)) == "mine"
    connections.choose(home, {"via": "agent", "agent": "opencode"})
    conf = advisor.settings(cfg, home)
    assert advisor.who(conf) == "OpenCode" and advisor.configured(conf)
    assert conf["max_per_day"] == 7 and conf["reasoning_effort"] == "none"
    assert advisor.who(advisor.settings(cfg)) == "mine", "without the state folder: the files only"


async def _says_ok(messages: list[dict[str, str]], max_tokens: int) -> str:
    return "OK"


def test_a_connection_is_taken_only_after_it_answered(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = brain.load_config()
    home = brain.state_dir()
    replies = iter([connections.AgentError("OpenCode: no provider"), "OK"])

    def sender(conf: dict[str, Any], state: Any = None, patience: int = 1) -> Any:
        async def send(messages: list[dict[str, str]], max_tokens: int) -> str:
            reply = next(replies)
            if isinstance(reply, Exception):
                raise reply
            return reply

        return send

    monkeypatch.setattr(advisor, "sender", sender)
    failed = brain.advisor_command(cfg, "try", ["agent:opencode", "ollama/qwen3.5:9b"])
    assert failed == {"ok": False, "why": "OpenCode: no provider"}
    assert connections.chosen(home) == {}, "a connection that did not answer is not kept"
    taken = brain.advisor_command(cfg, "try", ["agent:opencode", "ollama/qwen3.5:9b"])
    assert taken["ok"] is True and taken["said"] == "OK" and taken["label"] == "OpenCode"
    assert connections.chosen(home) == {
        "via": "agent", "agent": "opencode", "agent_model": "ollama/qwen3.5:9b"}
    # another connection replaces it whole (seen on the device: the agent's keys stayed behind)
    connections.choose(home, {**connections.chosen(home), "advise": 15, "every": 15})
    monkeypatch.setattr(advisor, "sender", lambda conf, state=None, patience=1: _says_ok)
    assert brain.advisor_command(cfg, "try", ["http:http://x/v1", "m"])["ok"] is True
    assert connections.chosen(home) == {
        "advise": 15, "every": 15, "via": "http", "base_url": "http://x/v1", "model": "m"}
    assert brain.advisor_command(cfg, "try", ["agent:claude"])["ok"] is False  # not offered
    assert brain.advisor_command(cfg, "try", ["agent:codex"])["ok"] is False  # not proven yet
    assert brain.advisor_command(cfg, "try", ["http:http://x/v1"])["ok"] is False  # no model named


def test_advice_on_moves_is_a_switch_of_its_own_and_forget_drops_the_choice() -> None:
    cfg = brain.load_config()
    home = brain.state_dir()
    connections.choose(home, {"via": "http", "base_url": "http://x/v1", "model": "m"})
    assert brain.advisor_now(cfg)["advise"] == 0, "picking who answers does not switch advice on"
    assert type(brain.make_decider(cfg)).__name__ == "TreeDecider"
    now = brain.advisor_command(cfg, "advise", ["15"])["now"]
    assert now["advise"] == 15 and now["picked"] and now["label"] == "m"
    assert type(brain.make_decider(cfg)).__name__ == "AdvisedDecider"
    assert brain.advisor_command(cfg, "advise", ["off"])["now"]["advise"] == 0
    assert type(brain.make_decider(cfg)).__name__ == "TreeDecider"
    assert connections.chosen(home) == {
        "via": "http", "base_url": "http://x/v1", "model": "m", "advise": "off"}, "no rhythm left"
    connections.keep_key(home, "sk-secret\n")
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
    monkeypatch.setattr(sys, "stdin", io.StringIO("sk-secret\n"))
    assert brain.advisor_command(brain.load_config(), "key", []) == {"ok": True}
    path = brain.state_dir() / "advisor_key"
    assert path.read_text() == "sk-secret"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert connections.kept_key(brain.state_dir()) == "sk-secret"


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
