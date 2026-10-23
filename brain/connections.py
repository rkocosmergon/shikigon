"""Who answers as Shikigon's advisor: finding the connections that are there, and using them.

Three kinds, one list (the panel's "WHO ANSWERS?"):

- a coding agent the player already uses (``pi``; ``codex`` is listed, never offered): Shikigon
  calls the agent's own command without a terminal and takes its answer. It never reads the
  agent's credentials; it runs the player's program as the player. OpenCode is no longer asked
  from here (0.1.13, cos20 #492: it puts the player's own instruction files into every prompt,
  with no switch to turn that off); any agent can still play Cosmergon by itself, without
  Shikigon.
- a model server on this machine (Ollama, LM Studio), found by asking its address;
- an address the player typed (any OpenAI-compatible endpoint).

The choice is kept in ``advisor.json`` in the state folder and goes over ``[brain.advisor]``
in the config file, which stays the place for everything else.

Three rules this file keeps (cos20 #451, concept 10.5d):

1. An agent is called without tools and approvals, in an empty folder; every other program
   started here (its model listing, ``mise``) runs in an empty folder too (``in_empty_folder``).
   The folder lies in the player's own state folder, so none of the folders above it is
   writable by another user: a program may read its configuration from every folder up to ``/``
   (OpenCode did, ``mise`` does).
2. Looking at what is installed never starts a placeholder. On Omarchy the agents' commands
   are stubs that install the agent the first time they run.
3. An agent's allowance is the player's: no advice on every move through an agent.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import ipaddress
import itertools
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Awaitable, Callable, Coroutine
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import files

Send = Callable[[list[dict[str, str]], int], Awaitable[str]]

CHOICE_FILE = "advisor.json"
KEY_FILE = "advisor_key"
AGENT_MIN_EVERY = 15  # through an agent, advice at most on every 15th move (not measured yet)
# A question through an agent waits this long: an agent is a whole program around the model,
# and what it adds by itself can cost minutes (OpenCode took 107 s for a one-word answer on the
# device, 11.10.2026). A move never waits that long: it gets ``timeout_s`` from the config and
# falls back to the rules.
AGENT_ANSWER_S = 240.0
PROBE_QUESTION = "Reply with the single word OK."

# Model servers people run on their own machine, by their default address.
LOCAL_SERVERS = (
    ("Ollama", "http://localhost:11434/v1"),
    ("LM Studio", "http://localhost:1234/v1"),
)

# The agents Shikigon can ask without a terminal. ``argv`` is the agent's own documented
# one-shot command in its narrowest setting; ``{model}`` is filled in. The prompt goes in on
# stdin, never as an argument: arguments stand in /proc/<pid>/cmdline, which every local user
# can read (marketplace review, omacom/omarchy-plugin-marketplace#11550). Both read the prompt
# from stdin when none is given as an argument; pi 1.2.0 tried on the device (15.10.2026), codex
# from its documentation only.
# ``proven`` says the file probe ran on a device (a task that wants to create a file creates
# none); an agent without it is not offered.
#   codex exec: read-only sandbox by default, the final message alone on stdout
#     (https://learn.chatgpt.com/docs/non-interactive-mode.md). Not proven: no account to
#     try it with on 11.10.2026.
#   pi -p: its own switches turn off tools, session, extensions, MCP, skills, templates and
#     context files (pi --help, 1.2.0); the answer alone on stdout, a system prompt of its
#     own instead of the coding one. Probed on the device (14.10.2026, qwen3.5:9b on a
#     model server in the home network): about 4 s an answer; asked to run a command it
#     could not, asked to write a file it wrote none (and said it had: its answer is
#     words, never a deed), ~/.pi unchanged.
#     --thinking off does not reach a model the player added to pi; a local one with a
#     thinking mode needs "samplingParams" in pi's models.json (README).
#     Nothing of the player's goes into its prompt (cos20 #492, seventh review, finding 1): pi
#     1.2 to 1.4 appends ~/.pi/agent/APPEND_SYSTEM.md unless --append-system-prompt is given
#     (core/resource-loader.ts:663-667, 1227-1238), and --system-prompt and --no-context-files
#     do not cover it. The text given is read as a file only where a file of that name exists
#     (:167-182); it runs in an empty folder, and this sentence names none.
# ~~OpenCode~~ (0.1.11 to 0.1.12): no longer asked from here (0.1.13, founder 23.10.2026). It puts
# ~/.config/opencode/AGENTS.md (else ~/.claude/CLAUDE.md) and the ``instructions`` of the
# player's opencode.json into every system prompt (1.19.0 session/instruction.ts:60-63, 135-168,
# session/prompt.ts:1257-1268), with no switch for the first; what it answered could go on to
# another player (speech.py). Playing Cosmergon with OpenCode itself is not touched by this.
PI_SYSTEM = "You answer one question in plain text. You have no tools."
NO_TOOLS = "Answer with text only. Do not run commands, do not read or write files."
AGENTS: dict[str, dict[str, Any]] = {
    "codex": {
        "name": "Codex",
        "whose": "your own login",
        "argv": ["codex", "exec", "--skip-git-repo-check", "--sandbox", "read-only",
                 "--ephemeral"],
        "model_flag": ["-m", "{model}"],
        "sign_in": ["codex", "login"],
        "proven": False,
    },
    "pi": {
        "name": "pi",
        "whose": "your own keys",
        "argv": ["pi", "-p", "--no-tools", "--no-session", "--no-extensions", "--no-mcp",
                 "--no-skills", "--no-prompt-templates", "--no-context-files", "--no-approve",
                 "--offline", "--thinking", "off", "--system-prompt", PI_SYSTEM,
                 "--append-system-prompt", NO_TOOLS],
        # 1.2 to 1.4: the options above read the same in all three (``cli/args.ts``), and
        # ``--version`` answers before any session, extension or network (``main.ts``).
        "version": ["pi", "--offline", "--version"],
        "versions": ("1.2", "1.3", "1.4"),
        "model_flag": ["--model", "{model}"],
        "sign_in": ["pi"],  # its sign-in is "/login" inside pi
        "models": ["pi", "--offline", "--list-models"],
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


def installed(agent: str, run: Callable[..., Any] | None = None) -> bool:
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
    try:  # in a group of its own: a timeout ends mise and what it started (K14)
        done = in_empty_folder(run or run_in_group, ["mise", "where", agent],
                               capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return int(done.returncode) == 0


def in_empty_folder(run: Callable[..., Any], argv: list[str], **kwargs: Any) -> Any:
    """``run(argv, …)`` in an empty folder of its own, for every program Shikigon starts.

    An agent, its model listing and ``mise`` read a project's configuration from their working
    folder, and the brain's is wherever it was started: by hand that can be a folder someone
    else wrote (threat model K36). Until 0.1.12 only the question went to an empty folder, the
    listing and ``mise`` ran in the brain's (cos20 #492 B6).

    The empty folder lies in the state folder (``run/``, 0700), not in ``/tmp``: a program that
    looks for its configuration in the folders above its own (OpenCode, up to ``/``) found
    ``/tmp``, where any local user may put a file. Until 0.1.13 it did (cos20 #492, third
    review).
    """
    # Each level made private by itself: ``mkdir(parents=True)`` gives the folders it adds the
    # default mode, whatever mode it is told.
    parent = files.private_folder(files.private_folder(files.state_dir()) / "run")
    with tempfile.TemporaryDirectory(prefix="run-", dir=parent) as empty:
        return run(argv, cwd=empty, **kwargs)


def agent_env(agent: str) -> dict[str, str]:
    """The environment an agent's command runs in: the player's own, without what would send
    the talk elsewhere (``AGENTS[...]["unset"]``), and what Shikigon sets for this agent
    (``AGENTS[...]["env"]``), for the question and for the model listing alike."""
    spec = AGENTS.get(agent, {})
    own = {k: v for k, v in os.environ.items() if k not in spec.get("unset", ())}
    return {**own, **spec.get("env", {})}


# The versions an agent is asked at (cos20 #492, sixth review, finding 11). ``proven`` says an
# agent was tried on a device, and that was one version of it; Omarchy keeps every agent at
# ``latest`` and updates it every four hours (``omarchy-tools.toml``). A later version may read
# other files, keep other sessions or take options apart differently, and its options are what
# keeps the talk to itself. So before each start of an agent's program for a listing or a
# question its version is read, in the same kind of empty folder and environment, and only a
# version Shikigon was tried with goes on. Read again for each question: it can change between
# two. The session delete that follows a question runs on the version read for it.
VERSION = re.compile(r"v?(\d{1,4})\.(\d{1,4})\.\d{1,6}")
VERSION_S = 10


def agent_version(agent: str, run: Callable[..., Any] | None = None) -> str:
    """What the agent's program says its version is; ``""`` if it says nothing in that form."""
    argv = AGENTS.get(agent, {}).get("version")
    if not argv:
        return ""
    try:
        done = in_empty_folder(run or run_in_group, argv, env=agent_env(agent),
                               capture_output=True, text=True, timeout=VERSION_S, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    said = str(done.stdout or "").strip()
    return said if int(done.returncode) == 0 and VERSION.fullmatch(said) else ""


def untried(agent: str, run: Callable[..., Any] | None = None) -> str:
    """Why the agent is not asked: its version is not one Shikigon was tried with; empty if it
    is. The words carry its version only in the form ``x.y.z``, never other text it printed."""
    spec = AGENTS[agent]
    tried = spec.get("versions", ())
    version = agent_version(agent, run)
    match = VERSION.fullmatch(version)
    if match and f"{int(match[1])}.{int(match[2])}" in tried:
        return ""
    said = f"says {version}" if version else "names no version"
    versions = ", ".join(f"{v}.x" for v in tried) or "no version of it"
    return f"{spec['name']} is not asked: it {said}, Shikigon was tried with {versions}"


# ---------- the choice ----------------------------------------------------------------------


def chosen(home: Path) -> dict[str, Any]:
    """What the player picked in the panel; empty if nothing."""
    path = home / CHOICE_FILE
    if not path.exists():
        return {}
    try:
        data = files.loads(path.read_text(encoding="utf-8"))
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

    A key kept by 0.1.3 or earlier is a bare line without an origin: it goes nowhere, and the
    address asks for it again. Until 0.1.12 it was bound to whatever ``advisor.json`` named at
    the moment of reading, so it went to the next address picked (cos20 #492 B3).
    """
    path = home / KEY_FILE
    if not path.exists() or not origin(url):
        return ""
    try:
        kept = files.loads(path.read_text(encoding="utf-8").strip())
    except ValueError:
        return ""
    if not isinstance(kept, dict):
        return ""
    return str(kept.get("key") or "") if kept.get("origin") == origin(url) else ""


def secret_for(home: Path | None, conf: dict[str, Any], url: str) -> str:
    """The key for ``url``, or ``""``: the one place a key for an advisor's address comes from.

    Each source is bound to the origin it was given for (H3, K9). The key named by
    ``api_key_env`` belongs to the ``base_url`` of the same section of the config file
    (``api_key_env_for``, set by ``advisor.settings``), the key typed in the panel to the address
    it was typed for (``kept_key``). Until 0.1.12 the named key went to any address picked in
    the panel (cos20 #492 B1).
    """
    where = origin(url)
    if not where:
        return ""
    named = str(conf.get("api_key_env") or "")
    if named and conf.get("api_key_env_for") == where and os.environ.get(named):
        return os.environ[named]
    return kept_key(home, url) if home is not None else ""


def credentials_in(url: str) -> bool:
    """True if an address carries a name or a password (``https://me:pw@…``).

    httpx sends them as a Basic key (httpx 0.28.1 ``_client.py:469-471``), so they count as a key
    wherever a key is checked: only over https or to the player's own server on this machine.
    Until 0.1.12 only a key the brain put in a header counted (cos20 #492 B2).
    """
    import httpx

    try:
        return bool(httpx.URL(url).userinfo)
    except httpx.InvalidURL:
        return False  # httpx sends nothing there either


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


KEY_ONLY_SAFE = "a key goes only over https, or straight to this machine (localhost, no proxy)"


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
    if host != "localhost":
        try:
            if not ipaddress.ip_address(host).is_loopback:
                return False
        except ValueError:
            return False
    return not http_proxy_set()


def http_proxy_set() -> bool:
    """True if the environment names a proxy for plain http (``http_proxy``, ``all_proxy``).

    httpx sends through it, also to ``localhost``: a key meant to stay on this machine would
    cross the network in the clear (cos20 #492 S5). ``no_proxy`` is not trusted here, because
    httpx and the standard library read it differently; with a proxy set, no key goes over http.
    The brain's own calls to this machine do not use the environment at all (``trust_env``).
    """
    proxies = urllib.request.getproxies()
    return bool(proxies.get("http") or proxies.get("all"))


# What a model list may bring into the shell (cos20 #492 F1): the list goes through the
# brain's stdout into the bar, inside the shared shell, and a server on this machine is found
# by its port alone, where any local user can listen.
ANSWER_BYTES = 256 * 1024  # read of a model server's list, at most
ANSWER_SECONDS = 8.0  # for the whole list, however slowly it comes
MODELS_KEPT = 200
NAME_CHARS = 120


PLAIN = {"Accept-Encoding": "identity"}  # every answer as it is (K29)


class TooLong(ValueError):
    """An answer past ``ANSWER_BYTES``, or one that came compressed (an odd answer, like bad
    JSON). One past its time ends in ``TimeoutError`` (``exchange``)."""


TALK_ONLY_SAFE = ("plain http goes only to this machine: for a model server elsewhere use "
                  "https, an ssh tunnel (ssh -L 11434:localhost:11434 <host>, then "
                  "http://localhost:11434/v1) or your agent")


def talk_may_go_to(base_url: str) -> bool:
    """Whatever the brain sends travels encrypted (https) or stays on this machine.

    The question, the talk so far and the situation are the player's, and the café argument of
    ``key_may_go_to`` holds for them word for word: at home ``192.168.1.50`` is the player's
    model server, in a café the same address is someone else, and the head, the advice and the
    speaking send there on their own, with no one to read a warning at that moment. Until 0.1.13
    plain http went to any address that got no key (cos20 #492, third review; panel 22.10.2026,
    decision A). Unlike for a key, a proxy in the environment does not count against this
    machine here: the brain's own calls to it never use one (``trust_env``)."""
    parts = urllib.parse.urlsplit(base_url)
    if parts.scheme == "https":
        return True
    return parts.scheme == "http" and on_this_machine(base_url)


def refuse_encoded(headers: Any) -> None:
    """An answer comes as it is, or not at all.

    httpx unpacks gzip while it reads, also stacked (``gzip, gzip, ...``), with no bound on what
    comes out, and before any count of ours: the 256 KiB limit was passed with a few KB on the
    wire (marketplace review #12454, cos20 #492). So every call asks for ``identity``, reads the
    raw bytes, and an answer that comes encoded anyway is refused before a byte is unpacked."""
    coding = str(headers.get("content-encoding", "")).strip().lower()
    if coding not in ("", "identity"):
        raise TooLong("the answer came compressed, though asked for as it is")


async def bounded_body(resp: Any) -> bytes:
    """A streamed answer, read raw only up to ``ANSWER_BYTES``: a server that sends without end
    or compressed cannot fill the brain's memory (the class of marketplace reviews #12064,
    #12454). Its time is the deadline of the whole exchange (``exchange``)."""
    refuse_encoded(resp.headers)
    body = bytearray()
    async for chunk in resp.aiter_raw():
        body += chunk
        if len(body) > ANSWER_BYTES:
            raise TooLong("the answer was too long")
    return bytes(body)


def may_send(url: str, with_key: bool) -> bool:
    """The rules before a byte leaves, raised as the door's own sentence: only over https or to
    this machine (``talk_may_go_to``), a key only over https or to the player's own server
    (``key_may_go_to``), a port on this machine only while it is the player's (``ours``).
    ``True`` when the address leads to this machine. Resolves the name: run it in a thread,
    inside the deadline (``exchange``)."""
    if not talk_may_go_to(url):
        raise AgentError(TALK_ONLY_SAFE)
    if with_key and not key_may_go_to(url):
        raise AgentError(KEY_ONLY_SAFE)
    if not ours(url, with_key=with_key):
        raise NotOurs(NOT_OURS)
    return on_this_machine(url)


async def exchange(method: str, url: str, *, headers: dict[str, str] | None = None,
                   payload: Any = None, timeout: float = 4, seconds: float = ANSWER_SECONDS,
                   transport: Any = None) -> Any:
    """The one door for every HTTP call of the brain: the request and its whole answer within
    ``seconds``, the raw body at most ``ANSWER_BYTES``, nothing compressed.

    httpx bounds each read, not the whole. Until 0.1.13 the limit counted from the body on, and
    httpcore 1.0.9 reads the headers in a loop that takes every ``1xx`` but ``101``
    (``_async/http11.py:170-183``): a server sending ``100 Continue`` every few seconds, or its
    headers a byte at a time, held a move, a question or a model list for ever (cos20 #492,
    third review). The deadline now holds the whole exchange, as in the server's webhook
    delivery (``webhook_dispatcher.py``, #553).

    Before connecting: anything goes only over https or to this machine (``talk_may_go_to``),
    and nothing to a port on this machine that is not the player's right now (H9); once
    connected, the same at the socket (``async_peer_check``). A call that carries a key, in a
    header or as a name and password in the address, goes only over https or to the player's
    own server, never to a system account's (H3, K33). ``transport`` stands in for the network
    in tests.

    The checks resolve the name of the address. They run inside the deadline, in a thread:
    until 0.1.13 they ran before it, in the brain's own loop, and a resolver that did not answer
    held every advised move and question past its time, the lesson beside the moves with it
    (cos20 #492, sixth review, finding 7)."""
    import httpx

    with_key = "Authorization" in (headers or {}) or credentials_in(url)
    sent = {**(headers or {}), **PLAIN}
    async with asyncio.timeout(seconds):
        local = await asyncio.to_thread(may_send, url, with_key)
        trace = {"trace": async_peer_check(local, with_key)}
        async with (
            httpx.AsyncClient(timeout=timeout, transport=transport, trust_env=not local) as client,
            client.stream(method, url, headers=sent, json=payload, extensions=trace) as resp,
        ):
            body = await bounded_body(resp)
    status = resp.status_code

    def raise_for_status() -> None:
        if status >= 400:
            raise httpx.HTTPStatusError(f"HTTP {status}", request=resp.request, response=resp)

    return SimpleNamespace(status_code=status, json=lambda: files.loads(body),
                           raise_for_status=raise_for_status)


def bounded_call(method: str, url: str, *, headers: dict[str, str] | None = None,
                 payload: Any = None, timeout: float = 4,
                 seconds: float = ANSWER_SECONDS) -> Any:
    """``exchange`` for a caller without an event loop: registration, forget, the rules of the
    world, a model list."""
    return alone(exchange(method, url, headers=headers, payload=payload, timeout=timeout,
                          seconds=seconds))


class _Daemons(concurrent.futures.ThreadPoolExecutor):
    """The executor of every event loop of the brain (``run_loop``): each call on a daemon thread
    of its own, and nobody waits for them when the loop closes.

    A loop hands blocking work to its executor: the door's checks (``asyncio.to_thread``), and
    the name resolution of httpx when it connects (``loop.getaddrinfo``). A resolver that does not
    answer keeps such a thread long after the call's deadline. ``asyncio.run`` waits for those
    threads when it closes, up to 300 s (``THREAD_JOIN_TIMEOUT``), and the interpreter waits for
    a ``ThreadPoolExecutor``'s at exit: a model list, a registration, the rules of the world and
    the end of the brain on SIGTERM waited for the resolver (cos20 #492, seventh review, finding 4;
    measured 3.0 s for a deadline of 0.5 s). A daemon thread ends with the process."""

    def submit(  # type: ignore[override]
        self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any
    ) -> concurrent.futures.Future[Any]:
        future: concurrent.futures.Future[Any] = concurrent.futures.Future()

        def work() -> None:
            if not future.set_running_or_notify_cancel():
                return
            try:
                future.set_result(fn(*args, **kwargs))
            except BaseException as exc:  # noqa: BLE001 - handed to the caller as it is
                future.set_exception(exc)

        threading.Thread(target=work, daemon=True).start()
        return future

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        return None  # nothing to wait for: a thread still at work ends with the process


def run_loop(work: Coroutine[Any, Any, Any]) -> Any:
    """``asyncio.run`` for the brain: the same, with ``_Daemons`` as the loop's executor, so a
    loop closes when its work is done, whatever a thread of it is still waiting for."""
    with asyncio.Runner() as runner:
        runner.get_loop().set_default_executor(_Daemons())
        return runner.run(work)


def alone(work: Coroutine[Any, Any, Any]) -> Any:
    """``work`` run to its end from synchronous code, in a loop of its own (``run_loop``) on a
    daemon thread of its own: also where this thread already runs a loop (``asyncio.run`` would
    refuse there). The answer is taken when ``work`` ends."""
    done = threading.Event()
    outcome: dict[str, Any] = {}

    async def watched() -> None:
        try:
            outcome["value"] = await work
        except BaseException as exc:  # noqa: BLE001 - handed to the caller as it is
            outcome["error"] = exc
        finally:
            done.set()

    threading.Thread(target=run_loop, args=(watched(),), daemon=True).start()
    done.wait()
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


def bounded_get(url: str, headers: dict[str, str] | None = None, timeout: float = 4) -> Any:
    """``httpx.get`` with the bounds of ``bounded_call``."""
    return bounded_call("GET", url, headers=headers, timeout=timeout)


def bounded_post(url: str, json: Any = None, timeout: float = 30) -> Any:
    """``httpx.post`` with the bounds of ``bounded_call`` (registration, cos20 #492 S2)."""
    return bounded_call("POST", url, payload=json, timeout=timeout, seconds=timeout)


def bounded_delete(url: str, headers: dict[str, str] | None = None, timeout: float = 30) -> Any:
    """``httpx.delete`` with the bounds of ``bounded_call`` (forget, cos20 #492 S2)."""
    return bounded_call("DELETE", url, headers=headers, timeout=timeout, seconds=timeout)


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
    """The models an OpenAI-compatible address offers. ``needs_key`` if it wants one.

    The rules of the door (https or this machine, a key only where it may go, a port on this
    machine only while it is the player's) are said by the door itself, inside the list's
    deadline: ``get`` is ``bounded_get`` in use, and ``exchange`` raises them as ``AgentError`` or
    ``NotOurs`` before a byte leaves. Until 0.1.13 they were asked here first, and their name
    resolution held the list past its 8 seconds (cos20 #492, seventh review, finding 4)."""
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        resp = get(base_url.rstrip("/") + "/models", headers=headers, timeout=4)
    except TooLong:
        return {"ok": False, "models": [], "why": "its list of models is too long"}
    except (AgentError, NotOurs) as exc:  # the door's own sentence (the rules above, again)
        return {"ok": False, "models": [], "why": str(exc)}
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


def own_server(base_url: str, tables: list[str] | None = None, me: int | None = None, *,
               system_ok: bool = True) -> bool:
    """True if the server at a local port runs as the player or (``system_ok``) as a system
    service.

    Any local user can listen on a free port; a server another person runs there is not
    "on this machine" for the player, and their question would go to that person
    (cos20 #492 F2). Ollama on Arch runs as the system user ``ollama``. A call that carries a
    key passes ``system_ok=False``: a system account is another account (marketplace review
    #12543, cos20 #492 S6), and a key there would leave the player.
    """
    port = port_of(base_url)
    return port is not None and own_port(port, tables, me, system_ok=system_ok)


def own_port(port: int, tables: list[str] | None = None, me: int | None = None, *,
             system_ok: bool = True) -> bool:
    """True if every listener on this port is the player (or a system service, ``system_ok``)."""
    owners = listeners(port, proc_tables() if tables is None else tables)
    me = os.getuid() if me is None else me
    below = system_accounts_below() if system_ok else 0
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


# ---------- the socket that took this very connection (cos20 #492, fifth review) -----------------
# ``own_port`` reads who listens on a port now. A local user who listened on a freed port, took the
# connection and closed his listener before the player's server bound the port again was no longer
# among the listeners, and the request went to him. The socket that took a connection keeps the
# uid of the listener that made it, also before ``accept`` and after the listener is gone.


def _plain(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str:
    """An address as text; an IPv4 address inside IPv6 (``::ffff:127.0.0.1``) as IPv4."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return str(ip.ipv4_mapped)
    return str(ip)


def _endpoint(cell: str) -> tuple[str, int]:
    """``0100007F:1F90`` of ``/proc/net/tcp`` as ``("127.0.0.1", 8080)``. Each 32-bit word of
    the address stands in the machine's byte order."""
    address, port = cell.rsplit(":", 1)
    raw = bytes.fromhex(address)
    if sys.byteorder == "little":
        raw = b"".join(raw[i : i + 4][::-1] for i in range(0, len(raw), 4))
    return _plain(ipaddress.ip_address(raw)), int(port, 16)


def own_sockets() -> set[str]:
    """The inodes of this process's sockets (``/proc/self/fd`` → ``socket:[inode]``)."""
    inodes: set[str] = set()
    with contextlib.suppress(OSError):
        for fd in os.listdir("/proc/self/fd"):
            with contextlib.suppress(OSError):
                link = os.readlink(f"/proc/self/fd/{fd}")
                if link.startswith("socket:["):
                    inodes.add(link[8:-1])
    return inodes


def takers(host: str, port: int, tables: list[str], mine: set[str]) -> list[int]:
    """The owners (uid) of the sockets on this machine that took this process's connections to
    ``host:port``; empty when none stands in the tables."""
    rows = []
    for table in tables:
        for line in table.splitlines()[1:]:
            cells = line.split()
            with contextlib.suppress(ValueError, IndexError):
                rows.append((_endpoint(cells[1]), _endpoint(cells[2]), int(cells[7]), cells[9]))
    server = (_plain(ipaddress.ip_address(host.split("%", 1)[0])), port)
    clients = {local for local, remote, _uid, inode in rows if inode in mine and remote == server}
    return [uid for local, remote, uid, _inode in rows if local == server and remote in clients]


def taken_by_player(host: str, port: int, *, system_ok: bool, tables: list[str] | None = None,
                    mine: set[str] | None = None) -> bool:
    """True if every socket that took a connection of this process to ``host:port`` is the
    player's (or a system service's, ``system_ok``). None found is not the player's: the brain
    looks three times within ten milliseconds, then refuses."""
    found: list[int] = []
    for _ in range(3):
        found = takers(host, port, proc_tables() if tables is None else tables,
                       own_sockets() if mine is None else mine)
        if found or tables is not None:
            break
        time.sleep(0.005)
    below = system_accounts_below() if system_ok else 0
    return bool(found) and all(uid == os.getuid() or uid < below for uid in found)


# ---------- checked when sending, not when found (H9, cos20 #492, review #12356) -----------------

NOT_OURS = ("that address is on this machine, and its port is not yours right now (another "
            "user listens there or took the connection, or nothing listens): nothing was sent")
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


def ours(base_url: str, tables: list[str] | None = None, me: int | None = None, *,
         with_key: bool = False) -> bool:
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
    address. For the world, the SDK asks this right before every request it sends, retries
    included (``world_send_check`` in shikigon_brain.py, cosmergon-agent ``send_check``).
    """
    if urllib.parse.urlsplit(base_url).scheme == "https":
        return True
    return not on_this_machine(base_url) or own_server(base_url, tables, me,
                                                       system_ok=not with_key)


PEER_ELSEWHERE = ("that address is on this machine, but the connection reached another one "
                  "(a proxy?): nothing was sent")


def peer_refusal(host: str, port: int, local: bool, with_key: bool) -> str:
    """Why the connection just made may not carry the request, or ``""``: on this machine, every
    listener on the port and the socket that took this connection must be the player's (or a
    system service's, without a key)."""
    here = _here(host, own_addresses())
    if local and not here:
        return PEER_ELSEWHERE
    if here and not (own_port(port, system_ok=not with_key)
                     and taken_by_player(host, port, system_ok=not with_key)):
        return NOT_OURS
    return ""


def _peer(stream: Any) -> tuple[str, int]:
    addr = stream.get_extra_info("server_addr") or ("", 0)
    return str(addr[0]), int(addr[1])


def async_peer_check(local: bool, with_key: bool) -> Callable[[str, dict[str, Any]],
                                                               Awaitable[None]]:
    """An httpx ``trace`` hook: whom the connection reached, checked before a byte of the
    request leaves.

    ``ours`` reads the address before connecting, and httpx resolves the name again when it
    connects (a name can point elsewhere by then), and a proxy from the environment connects to
    itself. So the last word is the socket's own peer (cos20 #492 S5, §0 rule 5)."""

    async def check(event: str, info: dict[str, Any]) -> None:
        if event != "connection.connect_tcp.complete":
            return
        stream = info["return_value"]
        if why := peer_refusal(*_peer(stream), local, with_key):
            await stream.aclose()
            raise NotOurs(why)

    return check


def found(
    conf: dict[str, Any],
    get: Callable[..., Any],
    is_installed: Callable[[str], bool] = installed,
    is_own: Callable[[str], bool] = own_server,
    why_not: Callable[[str], str] | None = None,
) -> list[dict[str, Any]]:
    """Everything that could answer, most likely first: agents, local servers, the config's own.
    An agent at a version Shikigon was not tried with stands there with the reason (``untried``),
    the same words the question would end with."""
    rows: list[dict[str, Any]] = []
    for agent, spec in AGENTS.items():
        if spec["proven"] and is_installed(agent):
            refused = (why_not or untried)(agent)
            rows.append({"id": f"agent:{agent}", "via": "agent", "agent": agent,
                         "name": spec["name"], "sign_in": spec["sign_in"],
                         "text": refused or f"{spec['name']} — {spec['whose']}"})
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


AGENT_OUTPUT_BYTES = 1024 * 1024  # what an agent may print; an answer is a few KB
_POLL_S = 0.05


class TooMuchOutput(subprocess.SubprocessError):
    """An agent that printed past ``AGENT_OUTPUT_BYTES``."""


# The leader of every agent's group, between the brain and the agent (cos20 #492, fourth review,
# K14). When the brain ends, also by SIGKILL, which reaches no handler of the brain (the
# terminal's deadline, Quickshell tearing down a Process), the kernel sends this leader SIGTERM
# (PR_SET_PDEATHSIG) and it ends the whole group: the agent and everything the agent started.
# The signal is not inherited over fork, so set on the agent itself it would have ended the
# agent alone. It ties to the thread that started it, and ``run_in_group`` waits in that thread
# until the leader is gone. Standard library only, run with ``-I``. argv: the brain's pid, then
# the agent's command.
#
# Before the agent starts, the guard restricts itself and everything it starts with Landlock
# (cos20 #492, sixth review, finding 10; founder 23.10.2026: through an agent only https): a TCP
# connection only to port 443. An agent sends the talk wherever its own configuration names
# for the model, which Shikigon cannot read reliably from outside (pi resolves the address from
# its catalogue, its models.json and at sending from its credentials). The kernel holds the rule
# for every agent and every program it starts: plain http to another machine, a model server in
# the home network or a port on this machine that another user may hold is refused before a
# byte. Name resolution (UDP) stays open. Without Landlock network rules (ABI < 4, Linux before
# 6.7) no agent is started. Syscalls 444-446 are the same on x86_64 and aarch64.
HTTPS_PORT = 443
GUARD = """
import ctypes, os, signal, sys
libc = ctypes.CDLL(None, use_errno=True)
libc.syscall.restype = ctypes.c_long
def end(*_):
    os.killpg(0, signal.SIGKILL)
def only_https(port):
    class Ruleset(ctypes.Structure):
        _fields_ = [("handled_access_fs", ctypes.c_uint64), ("handled_access_net", ctypes.c_uint64)]
    class NetPort(ctypes.Structure):
        _fields_ = [("allowed_access", ctypes.c_uint64), ("port", ctypes.c_uint64)]
    connect_tcp = 1 << 1  # LANDLOCK_ACCESS_NET_CONNECT_TCP
    abi = libc.syscall(444, None, ctypes.c_size_t(0), 1)  # LANDLOCK_CREATE_RULESET_VERSION
    if abi < 4:
        sys.exit("shikigon: no agent is started here: Linux gives no Landlock network rules (ABI "
                 + str(abi) + "), so the agent could reach more than https")
    rules, rule = Ruleset(0, connect_tcp), NetPort(connect_tcp, port)
    fd = libc.syscall(444, ctypes.byref(rules), ctypes.c_size_t(ctypes.sizeof(rules)), 0)
    if (fd < 0
            or libc.syscall(445, fd, 2, ctypes.byref(rule), 0)  # LANDLOCK_RULE_NET_PORT
            or libc.prctl(38, 1, 0, 0, 0)  # PR_SET_NO_NEW_PRIVS
            or libc.syscall(446, fd, 0)):
        sys.exit("shikigon: no agent is started here: its network could not be limited to https")
    os.close(fd)
signal.signal(signal.SIGTERM, end)
if libc.prctl(1, signal.SIGTERM) != 0:  # PR_SET_PDEATHSIG
    sys.exit("shikigon: the agent's guard could not tie itself to the brain")
if os.getppid() != int(sys.argv[1]):  # the brain ended before the tie held
    end()
only_https(int(sys.argv[2]))
child = os.fork()
if child == 0:
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    os.execvp(sys.argv[3], sys.argv[3:])
code = os.waitstatus_to_exitcode(os.waitpid(child, 0)[1])
sys.exit(code if code >= 0 else 128 - code)
"""


def guarded(argv: list[str], env: dict[str, str] | None) -> list[str]:
    """``argv`` behind ``GUARD``. A program that is not there fails here, as ``Popen`` did."""
    path = (env if env is not None else os.environ).get("PATH")
    if shutil.which(argv[0], path=path) is None:
        raise FileNotFoundError(2, "No such file or directory", argv[0])
    return [sys.executable, "-I", "-c", GUARD, str(os.getpid()), str(HTTPS_PORT), *argv]


def run_in_group(argv: list[str], *, cwd: str | None = None, env: dict[str, str] | None = None,
                 input: str | None = None, timeout: float | None = None,
                 capture_output: bool = True, text: bool = True, check: bool = False) -> Any:
    """``subprocess.run`` in a process group of its own, with a bound on what it prints.

    The whole group ends when the agent is done, on a timeout, or once it printed past
    ``AGENT_OUTPUT_BYTES``: an agent is a program around the model, and a child left running
    would go on after its answer was given (class of review #12242; until 0.1.11 only on a
    timeout). What it prints is the model's answer passed on, so it is read like any answer,
    with a limit (K29, cos20 #492 S9; until 0.1.11 ``communicate`` kept all of it). The group
    is ended while its leader is not yet reaped, so its number cannot belong to anyone else.
    A child that opens a session of its own (``setsid``) leaves the group; that stays named
    (threat model K14). While it runs, the group stands in ``GROUPS``: when the brain itself is
    stopped, ``end_groups`` ends it too (``shikigon_brain.end_with_its_agents``). The group's
    leader is ``GUARD``: when the brain ends without a word (SIGKILL), the guard ends the group."""
    del capture_output, check  # always captured, never raised on the exit code
    stdin = subprocess.PIPE if input is not None else subprocess.DEVNULL
    with subprocess.Popen(guarded(argv, env), cwd=cwd, env=env, stdin=stdin,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          start_new_session=True) as proc:
        GROUPS.add(proc.pid)
        try:  # out of GROUPS before the leader is reaped: its number may then be anyone's
            out, err, over = bytearray(), bytearray(), threading.Event()
            threads = [threading.Thread(target=_drain, args=(pipe, into, over), daemon=True)
                       for pipe, into in ((proc.stdout, out), (proc.stderr, err))]
            if input is not None:
                threads.append(threading.Thread(target=_feed, args=(proc.stdin, input),
                                                daemon=True))
            for thread in threads:
                thread.start()
            ended = _wait_unreaped(proc.pid, timeout, over)
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)
        finally:
            GROUPS.discard(proc.pid)
        proc.wait()
        for thread in threads:
            thread.join(timeout=2)  # a child that left the group may still hold a pipe
    if over.is_set():
        raise TooMuchOutput(f"the agent printed more than {AGENT_OUTPUT_BYTES} bytes")
    if not ended:
        raise subprocess.TimeoutExpired(argv, timeout or 0)
    decode = (lambda b: bytes(b).decode("utf-8", "replace")) if text else bytes
    return subprocess.CompletedProcess(argv, proc.returncode, decode(out), decode(err))


GROUPS: set[int] = set()  # process groups of agents still running (``run_in_group``)


def end_groups() -> None:
    """End every agent group still running: the brain is being stopped. Each leader is not
    reaped yet (``_wait_unreaped``), so its number is still that group's."""
    for group in list(GROUPS):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(group, signal.SIGKILL)


def _drain(pipe: Any, into: bytearray, over: threading.Event) -> None:
    """Read a pipe into ``into`` until it ends or the agent has printed too much in all."""
    for chunk in iter(lambda: pipe.read1(64 * 1024), b""):
        into += chunk
        if len(into) > AGENT_OUTPUT_BYTES:
            over.set()
            return


def _feed(pipe: Any, text: str) -> None:
    """Give the agent its prompt on stdin; an agent that stops reading ends the writing."""
    with contextlib.suppress(OSError):
        pipe.write(text.encode("utf-8"))
    with contextlib.suppress(OSError):
        pipe.close()


def _wait_unreaped(pid: int, timeout: float | None, over: threading.Event) -> bool:
    """Wait for the leader to end without reaping it (``WNOWAIT``); False on a timeout or too
    much output. A leader not reaped keeps its number, so the group can be ended safely."""
    deadline = None if timeout is None else time.monotonic() + timeout
    while not over.is_set():
        if os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT | os.WNOHANG) is not None:
            return True
        if deadline is not None and time.monotonic() > deadline:
            return False
        time.sleep(_POLL_S)
    return False


def agent_models(agent: str, run: Callable[..., Any] = run_in_group) -> list[str]:
    """The models an agent can use, by its own listing command; empty if it has none or is at a
    version Shikigon was not tried with (``untried``)."""
    argv = AGENTS.get(agent, {}).get("models")
    if not argv or untried(agent, run):
        return []
    try:
        done = in_empty_folder(run, argv, env=agent_env(agent), capture_output=True, text=True,
                               timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    names = table_models(done.stdout)  # pi's listing, the one agent that has one
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


def last_line(text: str) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return lines[-1][:200] if lines else ""


def run_agent(conf: dict[str, Any], prompt: str, timeout: float,
              run: Callable[..., Any] = run_in_group) -> str:
    """Call the agent once, in an empty folder, and return what it answered.

    Only an agent proven on a device is called, checked here where it is sent and not only
    where it is offered (threat model §0 rule 5): one never probed may keep tools or MCP
    servers (cos20 #492, K28). The same holds for its version, read before each question
    (``untried``, cos20 #492, sixth review, finding 11).
    """
    spec = AGENTS.get(str(conf.get("agent")))
    if spec is None:  # OpenCode, picked under 0.1.12 and no longer asked from here
        raise AgentError(f"{str(conf.get('agent'))[:40]} is not asked from here: pick who answers")
    if not spec.get("proven"):
        raise AgentError(f"{spec['name']} is not offered yet: it was never tried on a device")
    if why := untried(str(conf["agent"]), run):
        raise AgentError(why)
    env = agent_env(str(conf["agent"]))
    argv = agent_argv(conf)
    try:
        done = in_empty_folder(run, argv, env=env, capture_output=True, text=True,
                               timeout=timeout, check=False, input=prompt)
    except subprocess.TimeoutExpired:
        raise AgentError(f"{spec['name']} did not answer within {int(timeout)} s") from None
    except TooMuchOutput:
        raise AgentError(f"{spec['name']} printed more than an answer") from None
    except OSError as exc:
        why = f"{spec['name']} could not be started ({type(exc).__name__})"
        raise AgentError(why) from None
    answer = str(done.stdout or "").strip()
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
