"""Who answers as Shikigon's advisor: finding the connections that are there, and using them.

Three kinds, one list (the panel's "WHO ANSWERS?"):

- a coding agent the player already uses (``codex``, ``opencode``, ``pi``): Shikigon calls the
  agent's own command without a terminal and takes its answer. It never reads the agent's
  credentials; it runs the player's program as the player.
- a model server on this machine (Ollama, LM Studio), found by asking its address;
- an address the player typed (any OpenAI-compatible endpoint).

The choice is kept in ``advisor.json`` in the state folder and goes over ``[brain.advisor]``
in the config file, which stays the place for everything else.

Three rules this file keeps (cos20 #451, concept 10.5d):

1. An agent is called without tools and approvals, in an empty folder.
2. Looking at what is installed never starts a placeholder. On Omarchy the agents' commands
   are stubs that install the agent the first time they run.
3. An agent's allowance is the player's: no advice on every move through an agent.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.parse
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import files

Send = Callable[[list[dict[str, str]], int], Awaitable[str]]

CHOICE_FILE = "advisor.json"
KEY_FILE = "advisor_key"
AGENT_MIN_EVERY = 15  # through an agent, advice at most on every 15th move (not measured yet)
# A question through an agent waits this long: an agent is a whole program around the model,
# and what it adds by itself can cost minutes (see OPENCODE_ADVISOR). A move never waits that
# long: it gets ``timeout_s`` from the config and falls back to the rules.
AGENT_ANSWER_S = 240.0
PROBE_QUESTION = "Reply with the single word OK."

# Model servers people run on their own machine, by their default address.
LOCAL_SERVERS = (
    ("Ollama", "http://localhost:11434/v1"),
    ("LM Studio", "http://localhost:1234/v1"),
)

# OpenCode "allows all operations without requiring explicit approval" by default
# (https://opencode.ai/docs/config/), so Shikigon brings an agent of its own for the one
# call, with every tool denied. It goes in through the environment (inline config, merged
# over the user's), never into the user's files. Measured on the device (OpenCode 1.19.0):
# with OpenCode's own agent the one-word answer cost 7,643 tokens in, with this one 146.
#
# Two more lines make the difference between two minutes and five seconds. Read from the
# model server's own log (Ollama on an M4, qwen3.5:9b, 11.10.2026), for the one-word answer:
#   as OpenCode asks by itself     107 s: the model thought for 1,114 tokens, and OpenCode
#                                  sent a second request for a session title (648 tokens);
#                                  two at once halve the speed (17 instead of 35 tokens/s)
#   + "reasoningEffort": "none"    134 s: the answer took 0.9 s, the title 127 s (2,210 tokens)
#   + the title agent disabled       4.5 s
# Not tried with a provider's model (no account on the device).
OPENCODE_ADVISOR = {
    "agent": {
        "shikigon-advisor": {
            "description": "Answers one question in plain text. No tools.",
            "mode": "primary",
            "prompt": "You answer one question in plain text. You have no tools.",
            "permission": {"*": "deny"},
            "reasoningEffort": "none",
            "temperature": 0.2,  # as on the direct way; the model's own default made it invent
        },
        "title": {"disable": True},
    }
}

# The agents Shikigon can ask without a terminal. ``argv`` is the agent's own documented
# one-shot command in its narrowest setting; ``{model}`` is filled in. The prompt goes in on
# stdin, never as an argument: arguments stand in /proc/<pid>/cmdline, which every local user
# can read (marketplace review, omacom/omarchy-plugin-marketplace#11550). All three read the
# prompt from stdin when none is given as an argument; pi 1.2.0 and opencode 1.19.0 tried on
# the device (15.10.2026), codex from its documentation only.
# ``proven`` says the file probe ran on a device (a task that wants to create a file creates
# none); an agent without it is not offered.
#   codex exec: read-only sandbox by default, the final message alone on stdout
#     (https://learn.chatgpt.com/docs/non-interactive-mode.md). Not proven: no account to
#     try it with on 11.10.2026.
#   opencode run: --format json prints events (https://opencode.ai/docs/cli/). --thinking
#     stays from the first runs, where a call without it did not come back within 200 s;
#     whether it is still needed now that thinking is off was not tried again.
#   pi -p: its own switches turn off tools, session, extensions, MCP, skills, templates and
#     context files (pi --help, 1.2.0); the answer alone on stdout, a system prompt of its
#     own instead of the coding one. Probed on the device (14.10.2026, qwen3.5:9b on a
#     model server in the home network): about 4 s an answer; asked to run a command it
#     could not, asked to write a file it wrote none (and said it had: its answer is
#     words, never a deed), ~/.pi unchanged.
#     --thinking off does not reach a model the player added to pi; a local one with a
#     thinking mode needs "samplingParams" in pi's models.json (README).
PI_SYSTEM = "You answer one question in plain text. You have no tools."
AGENTS: dict[str, dict[str, Any]] = {
    "codex": {
        "name": "Codex",
        "whose": "your own login",
        "argv": ["codex", "exec", "--skip-git-repo-check", "--sandbox", "read-only",
                 "--ephemeral"],
        "model_flag": ["-m", "{model}"],
        "answer": "stdout",
        "sign_in": ["codex", "login"],
        "proven": False,
    },
    "opencode": {
        "name": "OpenCode",
        "whose": "your own keys",
        "argv": ["opencode", "run", "--pure", "--thinking", "--agent", "shikigon-advisor",
                 "--format", "json"],
        "env": {"OPENCODE_CONFIG_CONTENT": json.dumps(OPENCODE_ADVISOR)},
        "model_flag": ["-m", "{model}"],
        "answer": "opencode-json",
        "sign_in": ["opencode", "auth", "login"],
        "models": ["opencode", "models"],
        "proven": True,
    },
    "pi": {
        "name": "pi",
        "whose": "your own keys",
        "argv": ["pi", "-p", "--no-tools", "--no-session", "--no-extensions", "--no-mcp",
                 "--no-skills", "--no-prompt-templates", "--no-context-files", "--no-approve",
                 "--offline", "--thinking", "off", "--system-prompt", PI_SYSTEM],
        "model_flag": ["--model", "{model}"],
        "answer": "stdout",
        "sign_in": ["pi"],  # its sign-in is "/login" inside pi
        "models": ["pi", "--offline", "--list-models"],
        "models_listing": "table",
        "proven": True,
    },
}

NOT_SIGNED_IN = ("401", "unauthorized", "not logged in", "login", "authenticate", "api key")


class AgentError(Exception):
    """The agent's command failed; the message is what it said, in one line."""


# ---------- what is installed (never by starting it) ---------------------------------------


def is_placeholder(path: Path) -> bool:
    """Omarchy's stub: a small script that runs ``mise use -g`` and then the real program."""
    try:
        if path.is_symlink() or path.stat().st_size > 2048:
            return False
        return any(line.startswith("mise use -g") for line in path.read_text().splitlines())
    except (OSError, UnicodeDecodeError):
        return False


def installed(agent: str, run: Callable[..., Any] = subprocess.run) -> bool:
    """True if the agent's real program is here. Omarchy's own rule (omarchy-default-agent):
    anything at the command's path that is not the stub is the user's install; a stub counts
    only if mise already holds the program. The command itself is never started."""
    found = shutil.which(agent)
    if found is None:
        return False
    if not is_placeholder(Path(found)):
        return True
    if shutil.which("mise") is None:
        return False
    try:
        done = run(["mise", "where", agent], capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return int(done.returncode) == 0


# ---------- the choice ----------------------------------------------------------------------


def chosen(home: Path) -> dict[str, Any]:
    """What the player picked in the panel; empty if nothing."""
    path = home / CHOICE_FILE
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def choose(home: Path, choice: dict[str, Any]) -> None:
    files.write_json(home / CHOICE_FILE, choice)


def keep_key(home: Path, key: str) -> None:
    """A key the endpoint asked for: kept like the agent's own key, readable by the owner only."""
    home.mkdir(parents=True, exist_ok=True)
    fd = os.open(home / KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(key.strip())


def kept_key(home: Path) -> str:
    path = home / KEY_FILE
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def label(conf: dict[str, Any]) -> str:
    """Who answers, as the panel and the journal name it."""
    if conf.get("via") == "agent":
        return str(AGENTS.get(str(conf.get("agent")), {}).get("name") or conf.get("agent") or "")
    return str(conf.get("model") or "")


def usable(conf: dict[str, Any]) -> bool:
    """True once it is clear whom to ask."""
    if conf.get("via") == "agent":
        return bool(AGENTS.get(str(conf.get("agent")), {}).get("proven"))
    return bool(conf.get("base_url")) and bool(conf.get("model"))


# ---------- finding ---------------------------------------------------------------------------


KEY_ONLY_SAFE = "a key goes only over https, or to this machine and your home network"


def key_may_go_to(base_url: str) -> bool:
    """A key travels encrypted (https), or stays on this machine or in the home network.

    Over plain http to the open internet, anyone on the way reads it. A model server at
    home (localhost, a ``.local`` name, a name without a dot such as ``macmini``, which only
    the home network resolves, a private address) usually wants no key at all.
    """
    parts = urllib.parse.urlsplit(base_url)
    if parts.scheme == "https":
        return True
    host = (parts.hostname or "").lower()
    if parts.scheme != "http" or not host:
        return False
    if host == "localhost" or host.endswith(".local") or "." not in host and ":" not in host:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_private or address.is_link_local


def models_at(base_url: str, get: Callable[..., Any], key: str = "") -> dict[str, Any]:
    """The models an OpenAI-compatible address offers. ``needs_key`` if it wants one."""
    if key and not key_may_go_to(base_url):
        return {"ok": False, "models": [], "why": KEY_ONLY_SAFE}
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        resp = get(base_url.rstrip("/") + "/models", headers=headers, timeout=4)
    except Exception as exc:  # noqa: BLE001 — any failure is the same answer: not reachable
        why = f"no answer from {base_url} ({type(exc).__name__})"
        return {"ok": False, "models": [], "why": why}
    if resp.status_code in (401, 403):
        return {"ok": False, "models": [], "needs_key": True, "why": "it asks for a key"}
    try:
        resp.raise_for_status()
        names = [str(m["id"]) for m in resp.json().get("data", []) if m.get("id")]
    except Exception as exc:  # noqa: BLE001 — an odd answer is no list
        return {"ok": False, "models": [], "why": f"no list of models there ({type(exc).__name__})"}
    return {"ok": bool(names), "models": names, "why": "" if names else "it offers no models"}


def found(
    conf: dict[str, Any],
    get: Callable[..., Any],
    is_installed: Callable[[str], bool] = installed,
) -> list[dict[str, Any]]:
    """Everything that could answer, most likely first: agents, local servers, the config's own."""
    rows: list[dict[str, Any]] = []
    for agent, spec in AGENTS.items():
        if spec["proven"] and is_installed(agent):
            rows.append({"id": f"agent:{agent}", "via": "agent", "agent": agent,
                         "name": spec["name"], "sign_in": spec["sign_in"],
                         "text": f"{spec['name']} — {spec['whose']}"})
    for name, base_url in LOCAL_SERVERS:
        there = models_at(base_url, get)
        if there["ok"]:
            rows.append({"id": f"http:{base_url}", "via": "http", "base_url": base_url,
                         "models": there["models"], "text": f"{name} on this machine"})
    if conf.get("base_url") and conf.get("model") and conf.get("via") != "agent":
        own = f"http:{conf['base_url']}"
        if all(row["id"] != own for row in rows):
            rows.append({"id": own, "via": "http", "base_url": conf["base_url"],
                         "model": conf["model"],
                         "text": f"{conf['model']} at {short_host(str(conf['base_url']))}"})
    return rows


def short_host(base_url: str) -> str:
    return base_url.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]


def agent_models(agent: str, run: Callable[..., Any] = subprocess.run) -> list[str]:
    """The models an agent can use, by its own listing command; empty if it has none."""
    argv = AGENTS.get(agent, {}).get("models")
    if not argv:
        return []
    try:
        done = run(argv, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    if AGENTS[agent].get("models_listing") == "table":
        names = table_models(done.stdout)
    else:
        names = [line.strip() for line in done.stdout.splitlines()
                 if "/" in line and " " not in line.strip()]
    # What the player set up themselves first; the agent's own catalogue after it. The panel
    # shows the first eight (on the device eleven catalogue models stood before the player's).
    return sorted(names, key=lambda name: (name.startswith(f"{agent}/"), names.index(name)))


def table_models(listing: str) -> list[str]:
    """``provider/model`` from a table whose first two columns are those, under a header
    line (``pi --list-models``: ``provider  model  context …``)."""
    names: list[str] = []
    for line in listing.splitlines()[1:]:
        cells = line.split()
        if len(cells) >= 2:
            names.append(f"{cells[0]}/{cells[1]}")
    return names


# ---------- asking an agent -------------------------------------------------------------------


def prompt_text(messages: list[dict[str, str]]) -> str:
    """One text for the agent's stdin: what the model is told, then the talk, then the question.

    The question stays last, with its own line on language and length. With a closing
    sentence after it, a question in English was answered in German (seen on the device:
    the earlier exchange was German).
    """
    parts = [m["content"] for m in messages if m["role"] == "system"]
    parts.append("Answer with text only. Do not run commands, do not read or write files.")
    turns = [m for m in messages if m["role"] != "system"]
    for message in turns[:-1]:
        if message["role"] == "user":
            parts.append("(Asked before: " + message["content"] + ")")
        else:
            parts.append("(You answered before: " + message["content"] + ")")
    if turns:
        parts.append(turns[-1]["content"])
    return "\n\n".join(parts)


def agent_argv(conf: dict[str, Any]) -> list[str]:
    """The agent's command; the prompt is not in it (it goes in on stdin)."""
    spec = AGENTS[str(conf["agent"])]
    argv = list(spec["argv"])
    model = str(conf.get("agent_model") or "")
    if model:
        argv += [model if part == "{model}" else part for part in spec["model_flag"]]
    return argv


def opencode_text(output: str) -> str:
    """The answer in ``opencode run --format json``: the text parts of its event lines."""
    texts: list[str] = []
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        part = event.get("part") if isinstance(event, dict) else None
        if isinstance(part, dict) and part.get("type") == "text" and part.get("text"):
            texts.append(str(part["text"]))
    return "\n".join(texts).strip()


def last_line(text: str) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return lines[-1][:200] if lines else ""


def run_agent(conf: dict[str, Any], prompt: str, timeout: float,
              run: Callable[..., Any] = subprocess.run) -> str:
    """Call the agent once, in an empty folder, and return what it answered."""
    spec = AGENTS[str(conf["agent"])]
    env = {**os.environ, **spec.get("env", {})}
    with tempfile.TemporaryDirectory(prefix="shikigon-ask-") as empty:
        try:
            done = run(agent_argv(conf), cwd=empty, env=env, capture_output=True,
                       text=True, timeout=timeout, check=False, input=prompt)
        except subprocess.TimeoutExpired:
            raise AgentError(f"{spec['name']} did not answer within {int(timeout)} s") from None
        except OSError as exc:
            why = f"{spec['name']} could not be started ({type(exc).__name__})"
            raise AgentError(why) from None
    if spec["answer"] == "opencode-json":
        answer = opencode_text(done.stdout)
    else:
        answer = done.stdout.strip()
    if done.returncode != 0 or not answer:
        said = last_line(done.stderr) or last_line(done.stdout) or f"exit {done.returncode}"
        if any(mark in said.lower() for mark in NOT_SIGNED_IN):
            raise AgentError(f"{spec['name']} is not signed in.")
        raise AgentError(f"{spec['name']}: {said}")
    return answer


def agent_send(conf: dict[str, Any], wait: float,
               run: Callable[..., Any] = subprocess.run) -> Send:
    """The same shape as the HTTP sender, through the player's agent; ``wait`` in seconds."""

    async def send(messages: list[dict[str, str]], max_tokens: int) -> str:
        text = prompt_text(messages)
        return await asyncio.to_thread(run_agent, conf, text, wait, run)

    return send


# ---------- trying a connection before it is taken ---------------------------------------------


async def probe(send: Send) -> dict[str, Any]:
    """One real question. ``ok`` with what came back, or the reason in words."""
    started = time.monotonic()
    try:
        text = await send([{"role": "user", "content": PROBE_QUESTION}], 20)
    except Exception as exc:  # noqa: BLE001 — whatever failed, the panel shows one sentence
        why = str(exc) if isinstance(exc, AgentError) else f"no answer ({type(exc).__name__})"
        return {"ok": False, "why": why[:200]}
    ms = int((time.monotonic() - started) * 1000)
    if not text.strip():
        return {"ok": False, "why": "it answered nothing", "ms": ms}
    return {"ok": True, "said": text.strip()[:80], "ms": ms}
