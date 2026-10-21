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
import contextlib
import ipaddress
import itertools
import json
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import urllib.parse
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
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
            # a second switch beside the denied permissions (cos20 #492, K28): by OpenCode's
            # docs both cover MCP tools too (named `<server>_<tool>`), which `--pure` does not
            # drop (it drops plugins only, opencode 1.19.0). `tools` is deprecated upstream.
            "tools": {"*": False},
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


def origin(url: str) -> str:
    """``scheme://host:port`` of an address, the port always named: what a key is bound to."""
    parts = urllib.parse.urlsplit(url.strip())
    scheme = parts.scheme.lower()
    try:
        port = parts.port or {"http": 80, "https": 443}.get(scheme, 0)
    except ValueError:
        return ""
    host = (parts.hostname or "").lower()
    return f"{scheme}://{host}:{port}" if scheme and host else ""


def keep_key(home: Path, key: str, where: str) -> None:
    """A key the endpoint at ``where`` asked for: kept like the agent's own key, readable by the
    owner only, and bound to that address's origin. It never goes to another address
    (cos20 #492 F4: a key typed for one endpoint went to every address asked after it)."""
    home.mkdir(parents=True, exist_ok=True)
    fd = os.open(home / KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump({"origin": origin(where), "key": key.strip()}, handle)


def kept_key(home: Path, url: str) -> str:
    """The kept key if it was given for ``url``'s origin; empty for any other address.

    A key kept by 0.1.3 or earlier is a bare line without an origin: it belongs to the address
    picked then (``advisor.json``), the only one it was ever sent to.
    """
    path = home / KEY_FILE
    if not path.exists() or not origin(url):
        return ""
    text = path.read_text(encoding="utf-8").strip()
    try:
        kept = json.loads(text)
    except ValueError:
        kept = None
    if not isinstance(kept, dict):
        kept = {"origin": origin(str(chosen(home).get("base_url") or "")), "key": text}
    return str(kept.get("key") or "") if kept.get("origin") == origin(url) else ""


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


KEY_ONLY_SAFE = "a key goes only over https, or to this machine (localhost)"


def key_may_go_to(base_url: str) -> bool:
    """A key travels encrypted (https), or stays on this machine (loopback).

    Over plain http anyone on the way reads it — also on a network that only looks like home:
    in a café ``192.168.1.50`` is someone else's machine, anyone on the link answers for a
    ``.local`` name, and the network's DNS for a name without a dot (cos20 #492; the class of
    marketplace review #11798). A model server at home (Ollama, LM Studio) usually wants no
    key; one that does is reached over https. Up to 0.1.3 the home network counted as safe
    (``.local``, names without a dot, private addresses).
    """
    parts = urllib.parse.urlsplit(base_url)
    if parts.scheme == "https":
        return True
    host = (parts.hostname or "").lower()
    if parts.scheme != "http" or not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


# What a model list may bring into the shell (cos20 #492 F1): the list goes through the
# brain's stdout into the bar, inside the shared shell, and a server on this machine is found
# by its port alone, where any local user can listen.
ANSWER_BYTES = 256 * 1024  # read of a model server's list, at most
ANSWER_SECONDS = 8.0  # for the whole list, however slowly it comes
MODELS_KEPT = 200
NAME_CHARS = 120


class TooLong(ValueError):
    """An answer past ``ANSWER_BYTES`` or ``ANSWER_SECONDS`` (an odd answer, like bad JSON)."""


async def bounded_body(resp: Any, seconds: float) -> bytes:
    """A streamed answer, read only up to ``ANSWER_BYTES`` and ``seconds`` in all: a server
    that sends without end, or a byte at a time, cannot fill the brain's memory or hold it
    (the class of marketplace review #12064; a model server can be plain http in the LAN)."""
    started = time.monotonic()
    body = bytearray()
    async for chunk in resp.aiter_bytes():
        body += chunk
        if len(body) > ANSWER_BYTES or time.monotonic() - started > seconds:
            raise TooLong("the answer was too long")
    return bytes(body)


def bounded_get(url: str, headers: dict[str, str] | None = None, timeout: float = 4) -> Any:
    """``httpx.get`` with a limit on the body and on the whole time, not only on each read.

    Nothing goes to a port on this machine that is not the player's right now (H9)."""
    import httpx

    if not ours(url):
        raise NotOurs(NOT_OURS)
    started = time.monotonic()
    body = bytearray()
    with httpx.stream("GET", url, headers=headers or {}, timeout=timeout) as resp:
        for chunk in resp.iter_bytes():
            body += chunk
            if len(body) > ANSWER_BYTES or time.monotonic() - started > ANSWER_SECONDS:
                raise TooLong(url)
    status = resp.status_code

    def raise_for_status() -> None:
        if status >= 400:
            raise httpx.HTTPStatusError(f"HTTP {status}", request=resp.request, response=resp)

    return SimpleNamespace(status_code=status, json=lambda: json.loads(bytes(body)),
                           raise_for_status=raise_for_status)


def clean_names(names: list[Any]) -> list[str]:
    """Model names as the bar may show them: printable, short, and not too many."""
    kept: list[str] = []
    for name in names:
        text = "".join(ch for ch in str(name) if ch.isprintable()).strip()
        if text and len(text) <= NAME_CHARS:
            kept.append(text)
        if len(kept) == MODELS_KEPT:
            break
    return kept


def models_at(base_url: str, get: Callable[..., Any], key: str = "") -> dict[str, Any]:
    """The models an OpenAI-compatible address offers. ``needs_key`` if it wants one."""
    if key and not key_may_go_to(base_url):
        return {"ok": False, "models": [], "why": KEY_ONLY_SAFE}
    if not ours(base_url):  # also an address typed by hand, not only one found (H9)
        return {"ok": False, "models": [], "why": NOT_OURS}
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        resp = get(base_url.rstrip("/") + "/models", headers=headers, timeout=4)
    except TooLong:
        return {"ok": False, "models": [], "why": "its list of models is too long"}
    except Exception as exc:  # noqa: BLE001 — any failure is the same answer: not reachable
        # the host alone: an address can carry a name and a password (https://me:pw@…)
        why = f"no answer from {short_host(base_url)} ({type(exc).__name__})"
        return {"ok": False, "models": [], "why": why}
    if resp.status_code in (401, 403):
        return {"ok": False, "models": [], "needs_key": True, "why": "it asks for a key"}
    try:
        resp.raise_for_status()
        listed = resp.json().get("data", [])[: MODELS_KEPT * 2]
        names = clean_names([m["id"] for m in listed if m.get("id")])
    except Exception as exc:  # noqa: BLE001 — an odd answer is no list
        return {"ok": False, "models": [], "why": f"no list of models there ({type(exc).__name__})"}
    return {"ok": bool(names), "models": names, "why": "" if names else "it offers no models"}


# ---------- a server "on this machine" is the player's own -------------------------------------

LISTEN = "0A"  # TCP_LISTEN in /proc/net/tcp


def listeners(port: int, tables: list[str]) -> list[int]:
    """The owners (uid) of the sockets listening on ``port`` in ``/proc/net/tcp``-style tables."""
    owners: list[int] = []
    for table in tables:
        for line in table.splitlines()[1:]:
            cells = line.split()
            if len(cells) < 8 or cells[3] != LISTEN:
                continue
            if int(cells[1].rsplit(":", 1)[1], 16) == port:
                owners.append(int(cells[7]))
    return owners


def system_accounts_below() -> int:
    """``UID_MIN`` from ``/etc/login.defs``: accounts below it are the system's, not people."""
    try:
        for line in Path("/etc/login.defs").read_text(encoding="utf-8").splitlines():
            cells = line.split()
            if len(cells) == 2 and cells[0] == "UID_MIN":
                return int(cells[1])
    except (OSError, ValueError):
        pass
    return 1000


def own_server(base_url: str, tables: list[str] | None = None, me: int | None = None) -> bool:
    """True if the server at a local port runs as the player or as a system service.

    Any local user can listen on a free port; a server another person runs there is not
    "on this machine" for the player, and their question would go to that person
    (cos20 #492 F2). Ollama on Arch runs as the system user ``ollama``.
    """
    port = port_of(base_url)
    if port is None:
        return False
    owners = listeners(port, proc_tables() if tables is None else tables)
    me = os.getuid() if me is None else me
    below = system_accounts_below()
    return bool(owners) and all(uid == me or uid < below for uid in owners)


def port_of(base_url: str) -> int | None:
    """The port an address goes to, also when it names none (``http`` 80, ``https`` 443)."""
    parts = urllib.parse.urlsplit(base_url)
    try:
        return parts.port or {"http": 80, "https": 443}.get(parts.scheme)
    except ValueError:  # a port that is no number
        return None


def proc_tables() -> list[str]:
    """The kernel's lists of TCP sockets, IPv4 and IPv6."""
    tables = []
    for name in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            tables.append(Path(name).read_text(encoding="utf-8"))
        except OSError:
            continue
    return tables


# ---------- checked when sending, not when found (H9, cos20 #492, review #12356) -----------------

NOT_OURS = ("that address is on this machine, and its port is not yours right now (another "
            "user listens there, or nothing does): nothing was sent")
NOT_OURS_SHORT = "its port on this machine is not yours right now"  # the panel's move line


def failed_why(exc: BaseException) -> str:
    """What a failed call is called on the panel: an agent's own words, the port that is not
    the player's in words, anything else by its kind."""
    if isinstance(exc, AgentError):
        return str(exc)
    return NOT_OURS_SHORT if isinstance(exc, NotOurs) else type(exc).__name__


class NotOurs(OSError):
    """An address on this machine whose port the player does not hold right now."""


def own_addresses() -> set[str]:
    """This machine's own addresses (``/proc/net/fib_trie`` LOCAL, ``/proc/net/if_inet6``)."""
    found_here: set[str] = set()
    try:
        lines = Path("/proc/net/fib_trie").read_text(encoding="utf-8").splitlines()
        for before, line in itertools.pairwise(lines):
            if "/32 host LOCAL" in line:
                found_here.add(before.split()[-1])
    except (OSError, IndexError):
        pass
    try:
        for line in Path("/proc/net/if_inet6").read_text(encoding="utf-8").splitlines():
            found_here.add(str(ipaddress.ip_address(bytes.fromhex(line.split()[0]))))
    except (OSError, ValueError, IndexError):
        pass
    return found_here


def _here(address: str, own: set[str]) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback or ip.is_unspecified or str(ip) in own


def on_this_machine(
    base_url: str,
    resolve: Callable[..., Any] | None = None,
    own: Callable[[], set[str]] | None = None,
) -> bool:
    """True if the address leads to this machine: ``localhost``, a loopback address,
    ``0.0.0.0``, one of the machine's own addresses, or a name that resolves to one of
    these (the machine's own name is ``127.0.1.1`` on many systems)."""
    host = (urllib.parse.urlsplit(base_url).hostname or "").lower()
    if not host:
        return False
    if host == "localhost" or host.endswith(".localhost"):
        return True
    mine = (own or own_addresses)()
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
        return _here(host, mine)
    except ValueError:
        pass
    try:
        answers = (resolve or socket.getaddrinfo)(host, None)
    except (OSError, UnicodeError):
        return False
    return any(_here(str(answer[4][0]), mine) for answer in answers)


def ours(base_url: str, tables: list[str] | None = None, me: int | None = None) -> bool:
    """May anything be sent to this address now?

    An address that leads to this machine is used only while every listener on its port runs
    as the player or as a system service — read right before each sending, not once when it
    was found: the server found can stop, and another local user can take the freed port
    (review of 0.1.9, #12356). A port nobody listens on is not the player's either: someone
    could take it between this check and the connection. Any other address is not ours to
    judge here (H3 guards the key on its way).

    Over https the certificate already does it: whoever listens on the port cannot prove the
    name, the handshake fails before a byte of the request leaves, and nothing here turns
    verification off. So only plain http is looked at, and no name is resolved for an https
    address (the running brain checks its world before every move).
    """
    if urllib.parse.urlsplit(base_url).scheme == "https":
        return True
    return not on_this_machine(base_url) or own_server(base_url, tables, me)


def found(
    conf: dict[str, Any],
    get: Callable[..., Any],
    is_installed: Callable[[str], bool] = installed,
    is_own: Callable[[str], bool] = own_server,
) -> list[dict[str, Any]]:
    """Everything that could answer, most likely first: agents, local servers, the config's own."""
    rows: list[dict[str, Any]] = []
    for agent, spec in AGENTS.items():
        if spec["proven"] and is_installed(agent):
            rows.append({"id": f"agent:{agent}", "via": "agent", "agent": agent,
                         "name": spec["name"], "sign_in": spec["sign_in"],
                         "text": f"{spec['name']} — {spec['whose']}"})
    for name, base_url in LOCAL_SERVERS:
        if not is_own(base_url):
            continue
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
    """The host alone, never a name or password an address may carry (``https://me:pw@…``)."""
    return urllib.parse.urlsplit(base_url).hostname or ""


def run_in_group(argv: list[str], *, cwd: str | None = None, env: dict[str, str] | None = None,
                 input: str | None = None, timeout: float | None = None,
                 capture_output: bool = True, text: bool = True, check: bool = False) -> Any:
    """``subprocess.run`` in a process group of its own. On a timeout the whole group ends,
    also what the agent started itself: an agent is a program around the model, and a
    child left running would go on after its answer was given up (class of review #12242)."""
    del capture_output, check  # always captured, never raised on the exit code
    stdin = subprocess.PIPE if input is not None else subprocess.DEVNULL
    with subprocess.Popen(argv, cwd=cwd, env=env, text=text, stdin=stdin, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, start_new_session=True) as proc:
        try:
            out, err = proc.communicate(input, timeout=timeout)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.communicate(timeout=2)  # a child that left the group keeps no pipe open
            raise
    return subprocess.CompletedProcess(argv, proc.returncode, out, err)


def agent_models(agent: str, run: Callable[..., Any] = run_in_group) -> list[str]:
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
    names = clean_names(names)
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


NO_TOOLS = "Answer with text only. Do not run commands, do not read or write files."


def prompt_text(messages: list[dict[str, str]]) -> str:
    """One text for the agent's stdin: what the model is told, then the talk, then the question.

    The question stays last, with its own line on language and length. With a closing
    sentence after it, a question in English was answered in German (seen on the device:
    the earlier exchange was German). No agent is ever told it may read: the head reads
    through the brain (``reading``, cos20 #492).
    """
    parts = [m["content"] for m in messages if m["role"] == "system"]
    parts.append(NO_TOOLS)
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
    if model.startswith("-"):  # a name, never an option of the agent's command
        raise AgentError(f"{spec['name']}: a model name cannot start with '-'")
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
              run: Callable[..., Any] = run_in_group) -> str:
    """Call the agent once, in an empty folder, and return what it answered.

    Only an agent proven on a device is called, checked here where it is sent and not only
    where it is offered (threat model §0 rule 5): one never probed may keep tools or MCP
    servers (cos20 #492, K28).
    """
    spec = AGENTS[str(conf["agent"])]
    if not spec.get("proven"):
        raise AgentError(f"{spec['name']} is not offered yet: it was never tried on a device")
    env = {**os.environ, **spec.get("env", {})}
    argv = agent_argv(conf)
    with tempfile.TemporaryDirectory(prefix="shikigon-ask-") as empty:
        try:
            done = run(argv, cwd=empty, env=env, capture_output=True,
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
               run: Callable[..., Any] = run_in_group) -> Send:
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
        said = isinstance(exc, AgentError | NotOurs)
        why = str(exc) if said else f"no answer ({type(exc).__name__})"
        return {"ok": False, "why": why[:200]}
    ms = int((time.monotonic() - started) * 1000)
    if not text.strip():
        return {"ok": False, "why": "it answered nothing", "ms": ms}
    return {"ok": True, "said": text.strip()[:80], "ms": ms}
