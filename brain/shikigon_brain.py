"""Shikigon's brain: one Cosmergon agent with a will of its own.

The brain is thin on purpose. The game rules live on the server, the move
choice lives in the rule tree (``cosmergon-decider-tree``), and the HTTP side
lives in the SDK (``cosmergon-agent``). What stays here is what makes it yours:
its own identity, its character, your words, and what the bar shows.

Commands (called by the Omarchy plugin, or by you)::

    shikigon_brain.py register      # once: a new agent of its own
    shikigon_brain.py tick          # one move
    shikigon_brain.py run           # a move every ``interval_s``
    shikigon_brain.py instruct TEXT # a word from you sets its compass
    shikigon_brain.py status        # the snapshot the bar reads
    shikigon_brain.py advise        # dry run: what the advisor would pick, nothing is played
    shikigon_brain.py ask TEXT      # a question to the advisor
    shikigon_brain.py learn         # a lesson from its last moves now (else every 100 moves)
    shikigon_brain.py advisor list  # who could answer; also: try ID [MODEL] | advise off|N | forget
    shikigon_brain.py forget --yes  # delete it: its agent on the server, its files here

Nothing here ever prints or logs the API key. No text from outside (the world, other agents, a
model) goes to a program that has tools: the ``coach`` command did that until 0.1.13 and is
gone (cos20 #492, sixth review, finding 9).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import functools
import json
import os
import re
import shutil
import signal
import sys
import time
import tomllib
import urllib.parse
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

# The brain never writes into the plugin's folder. Omarchy reloads a plugin whenever a file
# in its folder changes, and a `__pycache__` written on the first `ask` closed the open
# panel (seen on the device, 11.10.2026). Its own files live in the state folder.
sys.dont_write_bytecode = True

# The brain's own modules only after the switch above: imported before it, they are cached.
import files

SOURCE = "shikigon"  # registration source the server counts (api/v1/auth.py:_QUELLEN)
REPO_DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config" / "shikigon.toml"

# The face vocabulary of the Cosmergon pet, kept so a Shikigon is recognisable.
THRIVING, CONTENT, STRUGGLING, ASLEEP = "thriving", "content", "struggling", "asleep"


def config_path() -> Path:
    """``$XDG_CONFIG_HOME/shikigon/shikigon.toml``. An empty or relative ``XDG_CONFIG_HOME`` counts
    as unset (XDG Base Directory spec): until 0.1.14 an empty one made the path relative, and the
    config of whatever folder the brain was started from named the advisor, its key and the world
    (cos20 #613). The rule is ``files.xdg_home``, also for the state folder."""
    return files.xdg_home("XDG_CONFIG_HOME", Path.home() / ".config") / "shikigon" / "shikigon.toml"


def state_dir() -> Path:
    return files.state_dir()


def private_state() -> Path:
    """The state folder, the player's alone (0700), also when a command by hand comes before
    the first wake (cos20 #492 F8: it was made with the default mode then), and every file in
    it 0600, as a new file where an old one was readable for others (``files.keep_private``).

    The coach's task of 0.1.12 and earlier (``coach.md``: its snapshot with the world's words)
    is removed, so no coding agent finds it there any more (cos20 #492, sixth review)."""
    home = files.private_folder(state_dir())
    files.keep_private(home)
    (home / "coach.md").unlink(missing_ok=True)
    return home


def said_on_stdin(asked: str = "") -> str:
    """One line from stdin: the bar and the terminal send their words this way. At a terminal
    it is asked for (``asked``), so the words never stand in the command line, which every
    local user can read (#11550, cos20 #492 F7)."""
    if sys.stdin is None:
        return ""
    if sys.stdin.isatty():
        if not asked:
            return ""
        # on stderr: stdout is the JSON answer a caller reads
        print(f"{asked}: ", end="", file=sys.stderr, flush=True)
    return sys.stdin.readline().strip()


def json_on_stdin() -> dict[str, Any]:
    """One JSON object on stdin: how the bar sends an address or a model (never as arguments)."""
    try:
        data = files.loads(said_on_stdin())
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def load_config() -> dict[str, Any]:
    """Read the user's config; on first start copy the default next to them."""
    path = config_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO_DEFAULT_CONFIG, path)
    with path.open("rb") as handle:
        return tomllib.load(handle)


def write_private(path: Path, data: dict[str, Any]) -> None:
    """Write JSON readable by the owner only (0600), created that way, never widened: through
    ``files.write_json``, whose temporary file is new and the brain's alone (cos20 #492 S12)."""
    files.write_json(path, data)


def credentials_path() -> Path:
    return state_dir() / "credentials.json"


CREDENTIAL_TEXTS = ("agent_id", "api_key", "expires_at", "world")


def load_credentials() -> dict[str, str] | None:
    """Its key file as texts; ``None`` while no Shikigon lives here. A file that is there but
    cannot be read gives what it can, perhaps nothing (``key_lost``): it is not "no Shikigon",
    which would let ``register`` replace it without ``--new``."""
    path = credentials_path()
    if not path.exists():
        return None
    data = files.read_object(path)
    return {key: data[key] for key in CREDENTIAL_TEXTS if isinstance(data.get(key), str)}


KEY_LOST_WHY = "its key file cannot be read: wake a new one"


def key_lost(creds: dict[str, str] | None) -> bool:
    """True if a key file is there but names no agent or no key. Until 0.1.14 an empty one (a
    crash between writing and the disk) ended the brain on every start, and the bar started it
    again every 30 seconds (cos20 #492, Betreuer-Durchgang b40f78b, finding 5)."""
    return creds is not None and not (creds.get("agent_id") and creds.get("api_key"))


INVITE_FORM = re.compile(r"[A-Z0-9]{4,12}")  # the world's codes: eight of A-Z0-9 (31.10.2026)
INVITE_CHANNEL = "shikigon"  # the way the inviter reached this one (cos20 #543, #603)


def invite_code(cfg: dict[str, Any]) -> str:
    """``[character] invited_by``, in capitals; empty without one. A code of another form ends
    ``register`` with a sentence before anything is sent: passed over in silence, the inviter
    would never learn that the invitation did not count (cos20 #492, 0.1.14 W3)."""
    code = str(cfg.get("character", {}).get("invited_by") or "").strip().upper()
    if code and not INVITE_FORM.fullmatch(code):
        raise SystemExit("invited_by is not an invitation code: letters and digits, as the "
                         "inviting agent was given it (for example K7Q2M9XA).")
    return code


def registration_body(cfg: dict[str, Any]) -> dict[str, Any]:
    """What the server is told when a new Shikigon is born; empty choices stay unset."""
    character = cfg.get("character", {})
    body: dict[str, Any] = {"source": SOURCE}
    if character.get("persona"):
        body["persona"] = character["persona"]
    if character.get("name"):
        body["agent_name"] = character["name"]
    if code := invite_code(cfg):
        body["referral_code"] = code
        body["referral_channel"] = INVITE_CHANNEL
    return body


def server_says(resp: Any) -> str:
    """The server's own sentence in an error answer (``error.message``), or nothing."""
    try:
        body = resp.json()
    except (ValueError, AttributeError):
        return ""
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        return str(error.get("message") or "")
    return str(error or "")


WORLD_KEY_SAFE = ("the world's address must be https (or this machine, localhost): "
                  "its key would travel readable for anyone on the way")


def world_is_safe(cfg: dict[str, Any]) -> bool:
    """The agent's key goes only where an advisor's key may go (H3, cos20 #492 F6)."""
    import connections

    world = cfg.get("world", {}).get("base_url") or "https://cosmergon.com"
    return connections.key_may_go_to(str(world))


def world_send_check(url: str) -> str:
    """Why nothing may be sent to this address of the world right now, or ``""``: the key
    travels encrypted or stays on this machine (H3), and on this machine only to a port that is
    the player's at this moment (H9).

    The SDK asks it with the URL right before every request it sends, each retry included
    (``send_check``, cosmergon-agent >= 0.27.27). Until 0.1.12 the running brain asked once per
    move, before waiting for the model; the move and the SDK's retries came after that wait,
    unchecked, and another local user could take a freed port in between (review #12584)."""
    import connections

    if not connections.key_may_go_to(url):
        return WORLD_KEY_SAFE
    return "" if connections.ours(url, with_key=True) else connections.NOT_OURS


def world_refusal(cfg: dict[str, Any]) -> str:
    """``world_send_check`` for the configured world: read before opening anything to it."""
    return world_send_check(str(cfg.get("world", {}).get("base_url") or "https://cosmergon.com"))


def world_peer_check(cfg: dict[str, Any]) -> Callable[[str, int], str]:
    """Whom a new connection to the world really reached, asked by the SDK before a byte of the
    request goes over it (``peer_check``, cosmergon-agent >= 0.27.29): the same last word the
    brain's own calls have (``connections.async_peer_check``). A name can resolve elsewhere after
    ``world_send_check``, and a proxy from the environment connects to itself."""
    import connections

    world = str(cfg.get("world", {}).get("base_url") or "https://cosmergon.com")
    # Whether the world leads to this machine is asked only of a plain-http world: one the key
    # may go to is ``localhost`` or a loopback address (``key_may_go_to``), and that needs no
    # name resolution. Over https the certificate decides, as in ``connections.ours``; a peer on
    # this machine is still checked in ``peer_refusal``. Until 0.1.13 every world's name was
    # resolved here, when the agent was made, outside any deadline (cos20 #492, seventh review,
    # a variant of finding 4).
    local = urllib.parse.urlsplit(world).scheme == "http" and connections.on_this_machine(world)

    def check(host: str, port: int) -> str:
        return connections.peer_refusal(host, port, local, True)

    return check


DASHBOARD_WORLD = "https://cosmergon.com"  # the terminal client's world (--base-url default)
TERMINAL_ELSEWHERE = ("the terminal client talks only to cosmergon.com, and this Shikigon "
                      "lives in another world: its key stays out of it")


def terminal_refusal(cfg: dict[str, Any], creds: dict[str, str]) -> str:
    """Why the terminal client may not open for this Shikigon, or ``""`` (H3, K9).

    The dashboard runs in a process of its own, with its own world (``--base-url``; Shikigon
    hands no address over as an argument, §1b) and without this brain's port check. Until 0.1.12
    it got the key of a Shikigon from any world and sent it to cosmergon.com. It opens only for
    a Shikigon of that world, so the key goes nowhere it was not given. A key file it cannot read
    is said in a sentence, not in a traceback of the script that hands the key over."""
    import connections

    if not (creds.get("agent_id") and creds.get("api_key")):
        return KEY_LOST_WHY
    home = connections.origin(DASHBOARD_WORLD)
    world = connections.origin(str(cfg.get("world", {}).get("base_url") or DASHBOARD_WORLD))
    given_by = connections.origin(creds.get("world") or world)
    return "" if world == home and given_by == home else TERMINAL_ELSEWHERE


def key_refusal(cfg: dict[str, Any], creds: dict[str, str]) -> str:
    """Why this Shikigon's key may not go to the configured world, or ``""``: a key goes only
    to the world it was given by (H3; class of reviews #11895, #12267). A key kept before 0.1.10
    names no world; it is bound to the one it first goes to after the update, which is the
    configured one, as before."""
    import connections

    world = connections.origin(str(cfg.get("world", {}).get("base_url") or "https://cosmergon.com"))
    given_by = creds.get("world") or ""
    if not given_by:
        write_private(credentials_path(), {**creds, "world": world})
        return ""
    if given_by == world:
        return ""
    host = urllib.parse.urlsplit(given_by).hostname or given_by
    return (f"this Shikigon's key belongs to the world at {host}; for another world, "
            "register a new Shikigon (register --new)")


def register(
    cfg: dict[str, Any], post: Callable[..., Any], replace: bool = False
) -> dict[str, str]:
    """Register an agent of its own and keep its key in a private file.

    An existing Shikigon is never overwritten by accident: its key is its life.
    A world that is away or refuses is said in one sentence, not in a traceback.
    ``post`` is ``connections.bounded_post`` in use and a stub in tests.
    """
    import advisor
    import connections

    if load_credentials() is not None and not replace:
        raise SystemExit("A Shikigon already lives here. Use `register --new` to replace it.")
    if why := world_refusal(cfg):
        raise SystemExit(why)
    url = cfg["world"]["base_url"].rstrip("/") + "/api/v1/auth/register/anonymous-agent"
    try:
        resp = post(url, json=registration_body(cfg), timeout=30)
        code = int(resp.status_code)
    except advisor.call_errors():
        raise SystemExit("The world did not answer. Try again in a moment.") from None
    if code >= 400:
        # The server knows why (too many new agents from one address, registration paused):
        # its sentence is passed on; without one, the number says at least that much.
        raise SystemExit(server_says(resp) or f"The world refused the registration ({code}).")
    # Read by its form, inside the same refusal: until 0.1.13 an answer that was no JSON or had
    # no key ended `register` with a traceback (cos20 #492, sixth review, finding 3).
    try:
        data = resp.json()
        agent_id, api_key = data["agent_id"], data["api_key"]
        expires_at = data.get("expires_at") or ""
    except advisor.call_errors():
        raise SystemExit("The world's answer to the registration could not be read.") from None
    if not (isinstance(agent_id, str) and agent_id and isinstance(api_key, str) and api_key):
        raise SystemExit("The world's answer to the registration named no agent and no key.")
    leave_the_old_one(state_dir())
    creds = {
        "agent_id": agent_id,
        "api_key": api_key,
        "expires_at": str(expires_at)[:64],
        "world": connections.origin(url),  # the key goes to this world only (H3)
    }
    write_private(credentials_path(), creds)
    born = {"agent_id": creds["agent_id"], "expires_at": creds["expires_at"]}
    if invite_code(cfg):  # the world says whether it knew the code; the agent lives either way
        counted = data.get("referral_accepted") is True
        born["invitation"] = "counted" if counted else "not recognised by the world"
    return born


def agent_paths(home: Path) -> list[Path]:
    """What belongs to the agent that lives here, not to the player: its moves, what it learned
    from them, the day its learning budget is measured against, its words, the missions it saw,
    its snapshot, the moves the world kept refusing it. The talk with the advisor is the
    player's (cos20 #468), as are the choice of advisor, its counter of calls and the bar's
    hints."""
    import head
    import lessons
    import speech

    return [home / lessons.JOURNAL_FILE, home / BAR_JOURNAL, home / lessons.LESSONS_FILE,
            home / lessons.REVIEW_FOLDER, home / head.BUDGET_FILE, speech.memo_path(home),
            home / seen_path().name, home / "status.json", home / REFUSALS_FILE]  # fmt: skip


def leave_the_old_one(home: Path) -> None:
    """A new Shikigon starts with nothing of the one that lived here (README: "a new Shikigon
    starts without lessons"). Until 0.1.14 ``register --new`` kept it all: the new one was told
    the old one's lessons and moves, measured its learning budget against the old one's day, and
    proposed the contract the old one had announced to the old one's partner (cos20 #566, F1)."""
    for path in agent_paths(home):
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)


def forget(cfg: dict[str, Any], delete: Callable[..., Any]) -> dict[str, Any]:
    """Remove this Shikigon for good: its agent on the server and its files here.

    The server deletes the agent. A key that has already ended can delete nothing: the
    world keeps that agent as a Vagant, and only the files here go.
    If the server does not answer or refuses, nothing here is removed, so you can try again.
    A key file it cannot read (``key_lost``) has no key to send: only the files here go, as after
    an ended key. Your configuration stays. ``delete`` is ``connections.bounded_delete`` in use
    and a stub in tests.
    """
    import advisor

    creds = load_credentials()
    if creds is None:
        raise SystemExit("No Shikigon lives here.")
    if key_lost(creds):  # nothing to send the server: the files here go, as after a 401
        shutil.rmtree(state_dir())
        lost = "its key file could not be read; the world keeps the agent as a Vagant"
        return {"ok": True, "server": lost, "removed": str(state_dir())}
    if why := world_refusal(cfg) or key_refusal(cfg, creds):
        return {"ok": False, "why": why}
    url = cfg["world"]["base_url"].rstrip("/") + "/api/v1/players/me"
    try:
        resp = delete(url, headers={"Authorization": f"api-key {creds['api_key']}"}, timeout=30)
        code = int(resp.status_code)
    except advisor.call_errors() as exc:
        return {"ok": False, "why": f"the server did not answer ({type(exc).__name__})"}
    if code >= 400 and code != 401:
        return {"ok": False, "why": f"the server answered {code}; nothing was removed"}
    shutil.rmtree(state_dir())
    ended = "the key had ended; the world keeps the agent as a Vagant"
    return {"ok": True, "server": ended if code == 401 else "deleted", "removed": str(state_dir())}


def preset_for(text: str, table: dict[str, list[str]]) -> str | None:
    """The compass preset your words point to, or ``None`` if it did not understand."""
    said = text.lower()
    for preset, words in table.items():
        if any(word.lower() in said for word in words):
            return preset
    return None


def mood(state: Any) -> str:
    """Its face, from the facts of its world.

    Unlike the Cosmergon pet, no field alone is not "struggling" (founder 14.10.2026, cos20
    #451): in a full world every new Shikigon starts without land, and a face that is sad from
    the first minute told a new player something was broken. Struggling means its energy falls
    or a catastrophe is on.
    """
    briefing = getattr(state, "world_briefing", None)
    situation = getattr(briefing, "situation", None)
    if situation is None:
        return CONTENT
    if situation.active_catastrophe:
        return STRUGGLING
    # The state's words are rising / stable / declining (API contract, cos20 14.10.2026);
    # until then this asked for "falling", which the state never sends.
    if situation.energy_trend == "declining":
        return STRUGGLING
    if situation.energy_trend == "rising":
        return THRIVING
    return CONTENT


# Its marauder (the body in the world), by the server's states (Cosmergon
# models/player.py MARAUDER_STATES). An unknown state is shown as it comes.
MARAUDER_TEXT = {
    "on_mission": "its marauder is on a mission",
    "recovery": "its marauder is at home",
    "human_piloted": "its marauder is piloted by a human",
    "geist": "its marauder sleeps (agent paused)",
}


# Without a field in a full world there is one way to land: win it (cos20 #451, 14.10.2026).
# Said as the goal it is; "no free field slot in its cube" read like a dead end to a new
# player, and an agent that already owns a field did not need it at all.
FIRST_FIELD = ("the world is full: it has to win its first field "
               "(gather spores, build bombs, besiege a weak field)")


def situation(state: Any) -> list[str]:
    """Why it does what it does, in words: read from what the server offers it, by its form
    (``advisor.facts_of``: a word or a list where an object belongs is none)."""
    import advisor

    notes = []
    marauder = advisor.facts_of(state, "start_mission").get("marauder_state")
    if isinstance(marauder, str) and marauder:
        notes.append(MARAUDER_TEXT.get(marauder, f"its marauder: {marauder}"))
    full = advisor.facts_of(state, "create_field").get("slots_available") is False
    if full and not getattr(state, "fields", None):
        notes.append(FIRST_FIELD)
    return notes


def mission_kind(action: str, params: dict[str, Any]) -> str:
    """The kind of a mission it starts (``gather_spores``, ``siege_field``…), for the journal;
    empty for any other move. The rule tree sends it as ``params.params.mission_type``."""
    if action != "start_mission":
        return ""
    inner = params.get("params")
    if not isinstance(inner, dict):
        inner = params
    return str(inner.get("mission_type") or "")


# A block the server sends is passed on as it came, if it is small enough for the bar: the
# bar runs inside the shared shell (cos20 #492 F5). Measured 17.10.2026 on the device: the
# tournament block 198 bytes, the server's next step 226.
SERVER_BLOCK_BYTES = 8 * 1024


# Every other text of the server in status.json is cut too (cos20 #492): a name, a persona, a
# compass, the sentences of the situation. Together with the three blocks above the file stays
# far below STATUS_BYTES, whatever the server sends (test_a_hostile_server_cannot_make_status_big).
NAME_CHARS, WORD_CHARS, SENTENCE_CHARS, SENTENCES = 64, 32, 200, 8
STATUS_BYTES = 64 * 1024


def short(text: Any, chars: int) -> str:
    """``text`` as a string of at most ``chars`` characters."""
    return str(text or "")[:chars]


def bounded(block: Any) -> Any:
    """``block`` as the server sent it, or ``None`` when it is past ``SERVER_BLOCK_BYTES``."""
    try:
        size = len(json.dumps(block))
    except (TypeError, ValueError):
        return None
    return block if size <= SERVER_BLOCK_BYTES else None


def snapshot(state: Any, last: dict[str, Any]) -> dict[str, Any]:
    """What the bar shows. Facts only, never the key."""
    import advisor

    rank, rank_of = advisor.place(getattr(state, "ranking", None))
    return {
        "name": short(getattr(state, "agent_name", ""), NAME_CHARS),
        "persona": short(getattr(state, "persona_type", ""), WORD_CHARS),
        "mood": mood(state),
        "energy": round(float(getattr(state, "energy", 0.0)), 1),
        "fields": len(getattr(state, "fields", []) or []),
        # The one ranking (cos20 #504): ``rang`` of ``rang_von``; older SDKs only champion_rang.
        "rank": rank,
        "rank_of": rank_of,
        # Whether it is shown publicly (cos20 #504): yes or no, never the server's text (the bar
        # runs in the shared shell, threat model §1b). Read from the state only; the switch is
        # the terminal's dialog [S], not the bar's.
        "public": bool(getattr(getattr(state, "showcase", None), "consented_version", None)),
        "compass": short(getattr(state, "compass_preset", None) or "autonomous", WORD_CHARS),
        "tick": getattr(state, "tick", 0),
        "situation": [short(s, SENTENCE_CHARS) for s in situation(state)][:SENTENCES],
        # No field in a full world (the bar's first-time hint reads this, not a sentence)
        "first_field": FIRST_FIELD in situation(state),
        # The tournament it plays in, as the server sends it (cos20 #475): passed on, not
        # picked from, so a new field of the server reaches the bar without a change here.
        "tournament": bounded(getattr(state, "tournament", None)),
        # The server's own sentence while something is still missing (where you are, what
        # comes next), and when its key ends. Passed on the same way; ``None`` when the
        # server has nothing to say.
        "next_step": bounded(getattr(state, "next_step", None)),
        "key_expiry": bounded(getattr(state, "key_expiry", None)),
        "last": last,
        "updated_at": int(time.time()),
    }


# The journal is cut to its last 128 KiB once it passes 256 KiB: about a thousand lines, more
# than the lessons read (lessons.TAIL) and far more than the bar shows. It grew by ~80 KiB a
# day on the device, and the bar reads it whole every few seconds.
JOURNAL_KEEP_BYTES = 128 * 1024


# What the bar reads of the journal and the talk: small files the brain writes for it, so the
# shell never reads a file that grows (cos20 #492 F5). journal.jsonl and chat.jsonl stay whole
# for the lessons and the terminal; the talk is the player's (cos20 #468).
BAR_JOURNAL = "journal_recent.json"
BAR_TALK = "talk.json"
BAR_LINES = 12  # journal lines the panel shows
BAR_TURNS = 3  # exchanges the panel shows
QUESTION_CHARS, ANSWER_CHARS = 500, 2000  # longest seen on the device (17.10.2026): 333, 516


def journal(*entries: dict[str, Any]) -> None:
    """Add lines to the journal, then write the bar's view of it once (cos20 #492)."""
    for entry in entries:
        files.append(state_dir() / "journal.jsonl", entry, JOURNAL_KEEP_BYTES)
    show_journal()


# What the bar reads of a journal line (``ShikigonModel.journalLine``), each text cut. A move the
# world named goes into its line several times, uncut (action, words, the head's choice, its
# tries); until 0.1.14 the bar got the line whole, a world's name of any length in each of those
# fields (cos20 #492, fourteenth review, Kür 2). A field added to the journal later reaches the
# bar only once it stands here.
BAR_JOURNAL_TEXTS = ("instruction", "preset", "learned", "why", "mission", "text", "words",
                     "kind", "action")


def bar_line(entry: dict[str, Any]) -> dict[str, Any]:
    """A journal line as the bar shows it: its texts cut to ``SENTENCE_CHARS``, nothing else."""
    line: dict[str, Any] = {"at": entry["at"] if isinstance(entry.get("at"), int) else 0}
    for key in BAR_JOURNAL_TEXTS:
        if key in entry:
            line[key] = None if entry[key] is None else short(entry[key], SENTENCE_CHARS)
    if "ok" in entry:
        line["ok"] = entry["ok"] is not False
    return line


def show_journal() -> None:
    """The journal's last lines for the bar."""
    lines = files.tail(state_dir() / "journal.jsonl", BAR_LINES, line_bytes=1024)
    files.write_json(state_dir() / BAR_JOURNAL, [bar_line(line) for line in lines])


def show_talk() -> None:
    """The talk's last exchanges for the bar, each cut to what the panel can show."""
    turns = files.tail(chat_path(), BAR_TURNS, line_bytes=16 * 1024)
    shown = []
    for turn in turns:
        cut = {**turn, "q": str(turn.get("q", ""))[:QUESTION_CHARS],
               "a": str(turn.get("a", ""))[:ANSWER_CHARS]}
        if "why" in cut:
            cut["why"] = str(cut["why"])[:220]
        shown.append(cut)
    files.write_json(state_dir() / BAR_TALK, shown)


# Tournament missions are run by the server (its assigner), not by this brain; their ends go
# into the journal so the bar can say what happened (cos20 #475). Each end is written once.
# At most MISSIONS_PER_LOOK per look, the server's own number: until 0.1.13 every ended
# mission a world listed cost two files, as many as it listed (cos20 #492, fifth review).
MISSIONS_PER_LOOK = 50
SEEN_KEEP = 200  # mission ids remembered: four looks
ID_CHARS, MISSION_CHARS = 64, 80


def tournament_running(state: Any) -> bool:
    """True while the server reports a running tournament for this agent."""
    brief = getattr(state, "tournament", None)
    return isinstance(brief, dict) and brief.get("status") == "running"


def mission_entry(mission: dict[str, Any], tick_no: int) -> dict[str, Any]:
    """A journal line for a tournament mission that ended, in a player's words."""
    from cosmergon_agent import mission_outcome_text, mission_word

    status = str(mission.get("status") or "")
    text = mission_outcome_text(str(mission.get("outcome") or ""))
    kind = mission_word(str(mission.get("mission_type") or "mission"))
    return {
        "at": int(time.time()),
        "tick": tick_no,
        "mission": short(kind, MISSION_CHARS),
        "text": short(text or ("done" if status == "completed" else status), SENTENCE_CHARS),
        "ok": status != "failed",
    }


def mission_key(mission: dict[str, Any]) -> str:
    """A mission's id as the brain remembers it, cut short."""
    return str(mission.get("mission_id"))[:ID_CHARS]


def seen_path() -> Path:
    return state_dir() / "tournament_seen.json"


def load_seen() -> list[str] | None:
    """Mission ids already written down; ``None`` before the first look.

    A file it cannot read counts as the first look: it remembers what is there and writes the
    file anew. Until 0.1.14 an empty one stopped every move in a tournament, after the move went
    out, until it was deleted by hand (cos20 #612, F4, the variant of F3)."""
    ids = files.read_object(seen_path()).get("ids")
    return [str(i) for i in ids] if isinstance(ids, list) else None


async def tournament_journal(agent: Any, state: Any) -> int:
    """Write the ends of tournament missions since the last look; returns how many.

    The first look only remembers what is there, like the terminal on connecting: an agent
    joining mid-tournament does not get thirty old lines. A failed read changes nothing and
    the move goes on.
    """
    if not tournament_running(state):
        return 0
    from cosmergon_agent.exceptions import ConnectionError as CsgConnectionError
    from cosmergon_agent.exceptions import RateLimitError

    try:
        missions = await agent.missions()
    except (RateLimitError, CsgConnectionError):
        return 0
    if missions is None:
        return 0
    ended = [
        m
        for m in reversed(missions[:MISSIONS_PER_LOOK])  # the server lists newest first
        if isinstance(m, dict) and m.get("body") == "tournament"
        and m.get("status") in ("completed", "failed", "cancelled")
    ]
    seen = load_seen()
    new = [m for m in ended if mission_key(m) not in (seen or [])]
    if seen is not None and new:
        journal(*(mission_entry(m, getattr(state, "tick", 0)) for m in new))
    ids = (seen or []) + [mission_key(m) for m in new]
    files.write_json(seen_path(), {"ids": ids[-SEEN_KEEP:]})
    return 0 if seen is None else len(new)


# Why it sleeps, as one word the bar can act on (a key that ended wants a different next
# step than a world that did not answer). The sentence for you stays in ``last.why``.
KEY_ENDED, WORLD_SILENT, CONFIG = "key_ended", "world_silent", "config"
KEY_LOST = "key_lost"  # its key file cannot be read (``key_lost``): a new one is the way on


def fall_asleep(reason: str, why: str) -> dict[str, Any]:
    """Write the sleeping snapshot and return what is said about it."""
    last = {"action": "none", "ok": False, "why": why}
    write_status({"mood": ASLEEP, "reason": reason, "last": last})
    return last


async def play(agent: Any, action: str, params: dict[str, Any]) -> dict[str, Any]:
    """Send one move: the one door every move goes through. A move the world does not take is
    said in words, never a crash.

    The world takes one move per tick and answers a second one with 429 (a `tick`
    from a terminal next to the running brain); a lost connection is the other case. A move
    that cannot even be built or sent (a parameter httpx refuses, an answer of the world in a
    form the SDK cannot read) is a refusal of its own, never the end of the brain: until 0.1.13
    it raised past this door and ended the brain on every such move (cos20 #492, sixth review,
    findings 2 and 3). A key that ended and a port that changed hands still end the work: the
    caller puts the Shikigon to sleep and says why.
    """
    from cosmergon_agent.exceptions import AuthenticationError, RateLimitError, SendRefusedError
    from cosmergon_agent.exceptions import ConnectionError as CsgConnectionError

    try:
        result = await agent.act(action, **params)
    except RateLimitError:
        why = "the world takes one move per tick: it already moved in this one"
        return {"action": action, "ok": False, "why": why}
    except CsgConnectionError:
        # The SDK says this also after the move went out: an answer that was no JSON, could not
        # be read, or the last try failed. Whether the world took it is not known (``unknown``),
        # so a limit counts it as made (``spend_budget``, cos20 #614).
        return {"action": action, "ok": False, "why": "the world did not answer", "unknown": True}
    except (AuthenticationError, SendRefusedError):
        raise
    except Exception as exc:  # noqa: BLE001 - the door: nothing of one move ends the brain
        why = f"the move could not be sent ({type(exc).__name__})"
        return {"action": action, "ok": False, "why": why, "refused": True}
    why = "" if result.success else str(result.error_message or "")
    done: dict[str, Any] = {"action": action, "ok": bool(result.success), "why": why[:200]}
    if not result.success:
        done["refused"] = True  # the world answered and said no; a 429 or no answer is not that
    kind = mission_kind(action, params)
    if kind:
        done["kind"] = kind  # the journal says "mission: gather spores", not "start_mission"
    # cos20 #533: what the move itself booked (own bookings, not income or later effects). Only
    # numbers go into the journal; free + held + items is the move's effect on total energy.
    data = getattr(result, "data", None)
    effect = data.get("energy_effect") if isinstance(data, dict) else None
    if result.success and isinstance(effect, dict):
        # by their type (``files.finite``): a word there ended the brain until 0.1.13
        done["effect"] = {k: files.finite(effect.get(k) or 0) or 0.0
                          for k in ("free", "held", "items")}  # fmt: skip
    return done


TICK_BUFFER_S = 2.0  # after the server's "next tick at": a hair early is still the old tick


def pause(interval: float, state: Any, now: float | None = None) -> float:
    """Seconds until the next move: at least ``interval``, and never before the next tick.

    A tick of the world is adaptive and often longer than the interval; a move every
    ``interval`` then lands twice in one tick, and the second is refused. The server says
    when its next tick comes (``next_tick_at``); without that word the interval stands.
    """
    now = time.time() if now is None else now
    coming = getattr(state, "next_tick_at", None)
    if not coming or coming <= now:
        return interval
    return max(interval, float(coming) - now + TICK_BUFFER_S)


LATE_TICK_S = 5.0  # the tick the server announced has not come yet: look again this soon

# A move the world refused this many times in a row is paused for this many moves
# (cos20 #491). A third identical refusal tells nothing new, it only costs the move; without
# this a Shikigon could ask for the same refused offer every minute for hours. The same
# numbers as cosmergon-pet (since August).
REFUSALS_BEFORE_PAUSE = 3
PAUSED_MOVES = 30


REFUSALS_FILE = "refusals.json"


def refusals_path() -> Path:
    return state_dir() / REFUSALS_FILE


def _per_move(value: Any) -> dict[str, int]:
    """``{move: number}`` read from the file by its form."""
    return {str(k): files.count(v) for k, v in value.items()} if isinstance(value, dict) else {}


class Refusals:
    """Which moves the world keeps refusing, and which are paused.

    With ``path`` (``refusals_path``) they are kept in a file, read before each look and written
    after each count: a ``tick`` by hand next to the running brain holds back what the brain
    paused, and the brain what the ``tick`` paused. Until 0.1.14 they lived in the running
    brain alone, and a ``tick`` sent at once the move it had just paused (cos20 #492,
    Betreuer-Durchgang b40f78b, finding 6). Without ``path``, in memory only."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.moves = 0
        self._in_a_row: dict[str, int] = {}
        self._paused_until: dict[str, int] = {}
        self._load()

    def _load(self) -> None:
        if self.path is None:
            return
        data = files.read_object(self.path)
        self.moves = files.count(data.get("moves"))
        self._in_a_row = _per_move(data.get("in_a_row"))
        self._paused_until = _per_move(data.get("paused_until"))

    def _save(self) -> None:
        if self.path is None:
            return
        files.write_json(self.path, {
            "moves": self.moves, "in_a_row": self._in_a_row,
            "paused_until": {a: u for a, u in self._paused_until.items() if u > self.moves}})

    def paused(self) -> frozenset[str]:
        self._load()
        return frozenset(a for a, until in self._paused_until.items() if until > self.moves)

    def note(self, last: dict[str, Any]) -> bool:
        """Count one move; ``True`` when this refusal pauses it."""
        self._load()
        pauses = self._count(last)
        self._save()
        return pauses

    def _count(self, last: dict[str, Any]) -> bool:
        self.moves += 1
        action = str(last.get("action") or "")
        if last.get("ok"):
            self._in_a_row.pop(action, None)
            return False
        if not last.get("refused"):
            return False  # a 429 or no answer: a question of time, not of the move
        self._in_a_row[action] = self._in_a_row.get(action, 0) + 1
        if self._in_a_row[action] < REFUSALS_BEFORE_PAUSE:
            return False
        self._in_a_row.pop(action)
        self._paused_until[action] = self.moves + PAUSED_MOVES
        return True


async def choose(decider: Any, state: Any, paused: frozenset[str]) -> tuple[str, dict[str, Any]]:
    """The decider's move, told which moves are paused when it can take that (tree, advisor).

    A decider of the SDK's protocol knows only ``decide(state)``; what it picks is checked
    by the caller all the same.
    """
    import inspect

    choice: tuple[str, dict[str, Any]]
    if paused and "blocked" in inspect.signature(decider.decide).parameters:
        choice = await decider.decide(state, blocked=paused)
    else:
        choice = await decider.decide(state)
    return choice


async def try_again(
    agent: Any, decider: Any, state: Any, last: dict[str, Any], refusals: Refusals | None
) -> dict[str, Any]:
    """Count the move of this tick; after the world said no, a decider that can (the head,
    cos20 #527) chooses again.

    A refusal frees the tick on the server, so the new move counts for this tick. Every move
    is counted here, once, and a refusal BEFORE the decider chooses again: a move this refusal
    pauses is then among ``paused`` and goes out no more in this tick. Until 0.1.14 it was
    counted after the new choice, and a move the world had just refused for the third time
    went out a fourth time, without the sentence that it was paused (cos20 #612, F1). The
    decider ends it (``None``) after its own few tries.
    """
    from words import action_words

    again = getattr(decider, "again", None)
    paused_now: list[str] = []
    while True:
        if refusals is not None and refusals.note(last):
            paused_now.append(str(last.get("action") or ""))
        if again is None or not last.get("refused"):
            break
        paused = refusals.paused() if refusals is not None else frozenset()
        choice = await again(state, last, paused)
        if choice is None:
            break
        action, params = choice
        if action in paused:
            why = f"held back: {action_words(action)} is paused, the world kept refusing it"
            last = {"action": "wait", "ok": True, "why": why}
        elif action == "wait":
            last = {"action": "wait", "ok": True, "why": "chose to wait"}
        else:
            last = await play(agent, action, params)
    said = f"refused {REFUSALS_BEFORE_PAUSE} times, paused for {PAUSED_MOVES} moves"
    for action in paused_now:  # also a move paused before the last one of this tick
        text = said if action == last.get("action") else f"{action_words(action)}: {said}"
        last["why"] = f"{last['why']} — {text}" if last.get("why") else text
    return last


async def tick(
    agent: Any, decider: Any, done_in: int | None = None, refusals: Refusals | None = None
) -> dict[str, Any]:
    """One move: look, choose, act, write it down.

    ``done_in`` is the tick the running brain already chose in. The world takes one move per
    tick, and when "next tick at" comes late the state still shows that tick: then nothing
    is chosen, no advisor is asked, nothing is written (``skipped``). ``refusals`` keeps the
    moves the world refused in a row; a paused move is not played (cos20 #491).
    """
    state = await agent.refresh_state()
    if state is None:
        return fall_asleep(WORLD_SILENT, "no state from the world")
    if done_in and getattr(state, "tick", 0) == done_in:
        return {"action": "", "ok": True, "why": "", "skipped": "same tick"}
    import speech
    from words import action_words, move_words

    paused = refusals.paused() if refusals else frozenset()
    action, params = await choose(decider, state, paused)
    if action in paused:
        why = f"held back: {action_words(action)} is paused, the world kept refusing it"
        last: dict[str, Any] = {"action": "wait", "ok": True, "why": why}
    elif action == "wait":
        last = {"action": "wait", "ok": True, "why": "chose to wait"}
    else:
        last = await play(agent, action, params)
    last = await try_again(agent, decider, state, last, refusals)  # counts it, once
    last["words"] = move_words(last)  # what the panel and its advisor show (words.py)
    advice = getattr(decider, "last", None)  # what an advisor did on this move, if one was asked
    if advice:
        last["advice"] = advice
        if advice.get("speech") and last.get("ok"):  # it wrote: say what (speech.py, #526)
            last["words"] = speech.words(advice["speech"])
        spend_budget(advice, last)
    # its energy and fields when it chose: what it learns from (lessons.py). ``total`` is the
    # goal's measure (energy plus offers, fields, items; cos20 #504): an energy offer moves
    # energy out of the balance but not out of the total.
    entry = {"at": int(time.time()), "tick": getattr(state, "tick", 0),
             "energy": round(float(getattr(state, "energy", 0.0)), 1),
             "fields": len(getattr(state, "fields", []) or []), **last}
    total = getattr(getattr(state, "ranking", None), "gesamtenergie", None)
    if total is not None:
        entry["total"] = round(float(total), 1)
    journal(entry)
    await tournament_journal(agent, state)
    status = snapshot(state, last)
    left = getattr(decider, "budget_left", None)
    if left is not None:  # a number only, for the bar (threat model §1b)
        status["learning_budget_left"] = round(float(left))
    write_status(status)
    return last


def write_status(status: dict[str, Any]) -> None:
    """``status.json`` for the bar, never past ``STATUS_BYTES``: the fields above are cut one by
    one, and this is the one place that holds the bound for all of them, also for a field added
    later (cos20 #492 S13). Past it, the bar gets the mood and the last move only."""
    if len(json.dumps(status)) > STATUS_BYTES:
        last = status.get("last")
        if not isinstance(last, dict):
            last = {}
        kept = {"action": str(last.get("action") or "")[:64], "ok": bool(last.get("ok"))}
        status = {"mood": str(status.get("mood") or "")[:32], "cut": True, "last": kept}
    files.write_json(state_dir() / "status.json", status)


def spend_budget(advice: dict[str, Any], last: dict[str, Any]) -> None:
    """Count what a move the head chose itself lost against its learning budget (cos20 #533).

    The larger of what the world booked for a move it wrote itself (``energy_effect``) and what
    the head's check put on the move before it went out (``gives``, ``HeadDecider._take``, also a
    transfer as the rules' line), the latter only for the move it chose: a word written in its
    place gives nothing. Until 0.1.13 only the world's booking counted, and a world that sends
    none left the rest whole all day, for every transfer up to it (cos20 #492, eleventh review,
    K45).

    Only the move that went out counts, and only if it is the one the head chose: after a
    refusal the rules can play (``fallback``), and a word can stand in its place (``speech``).
    Until 0.1.14 ``own`` of the refused move stayed, and the rules' move was booked as the head's
    loss: a field the rules built after a refused transfer used up the day's budget (cos20 #566,
    F2).

    A move whose answer was lost (``unknown``, ``play``) counts as made: for a limit, not known is
    given. Until 0.1.14 it counted as refused, and the next move had the whole rest again, as
    often as the answers got lost (cos20 #614)."""
    import head
    import lessons

    if not (advice.get("head") and (last.get("ok") or last.get("unknown"))):
        return
    if advice.get("fallback") or last.get("action") != advice.get("choice"):
        return  # what went out is not the move the head chose
    effect = lessons.own_effect(last) if advice.get("own") else None
    loss = -effect if effect is not None else 0.0
    loss = max(loss, files.finite(advice.get("gives")) or 0.0)
    head.budget_spend(state_dir(), loss)


async def instruct(agent: Any, text: str, table: dict[str, list[str]]) -> dict[str, Any]:
    """A word from you. It tilts the compass; the character stays."""
    preset = preset_for(text, table)
    if preset is None:
        return {"heard": text, "preset": None, "ok": False, "why": "did not understand"}
    from cosmergon_agent.exceptions import RateLimitError

    try:
        answer = await agent.set_compass(preset)
    except RateLimitError:  # the world limits how often the compass may change
        why = "the compass was changed too often: try again in a minute"
        journal({"at": int(time.time()), "instruction": text, "preset": preset, "ok": False})
        return {"heard": text, "preset": preset, "ok": False, "why": why}
    ok = not (isinstance(answer, dict) and answer.get("error"))
    journal({"at": int(time.time()), "instruction": text, "preset": preset, "ok": ok})
    return {"heard": text, "preset": preset, "ok": ok}


def make_agent(cfg: dict[str, Any]) -> Any:
    """The SDK client, bound to this Shikigon's own key (never the shared SDK config)."""
    from cosmergon_agent import CosmergonAgent

    creds = load_credentials()
    if creds is None:
        raise SystemExit("No Shikigon yet. Run: shikigon_brain.py register")
    if key_lost(creds):
        raise SystemExit(KEY_LOST_WHY)
    if why := world_refusal(cfg) or key_refusal(cfg, creds):
        raise SystemExit(why)
    return CosmergonAgent(
        api_key=creds["api_key"],
        agent_id=creds["agent_id"],
        base_url=cfg["world"]["base_url"],
        send_check=world_send_check,  # asked right before every request, retries included
        peer_check=world_peer_check(cfg),  # and at the socket of every new connection
    )


async def advise(agent: Any, decider: Any) -> dict[str, Any]:
    """Dry run of the advisor: what the rules propose and what the model picks. Plays nothing."""
    state = await agent.refresh_state()
    if state is None:
        return {"ok": False, "why": "no state from the world", "played": False}
    action, params = await decider.decide(state)
    return {"ok": True, "chosen": action, "params": params, "advice": decider.last, "played": False}


CHAT_KEPT = 2  # earlier exchanges the model is given, so a follow-up question makes sense
MOVES_TOLD = 3  # of its last moves, how many the model is told
NOTES = Path(__file__).resolve().parent / "advisor_notes.md"  # what only the plugin knows
# The parts of the server's game info a player asks about (not: products, affiliate, links).
RULE_PARTS = ("actions", "conway_rules", "economy", "tournaments", "player_tiers")
# Per action, what a program needs and a player does not. Left out to keep the text small
# enough for a model with a 4,000-token window. ``forms`` (cos20 #530, on every action) more
# than doubled it; the head reads them on their own (``world_forms``). ``your_effect`` (cos20 #533,
# a sentence on every action) would grow it again and leave no action "other"; the head learns
# what its moves did from each answer's ``energy_effect`` instead.
PROGRAM_ONLY = ("use_case", "requires", "effects", "forms", "your_effect")
# The rules in a prompt, at most: cosmergon.com's came to 6,191 characters (23.10.2026), and a
# configured world could send up to 8 MiB into every question (cos20 #492, fourth review, K35).
# Twice today's, so the 4,000-token window above still holds them; a cut ends in "…".
RULES_CHARS = 12_000


def chat_path() -> Path:
    return state_dir() / "chat.jsonl"


def chat_tail(count: int) -> list[dict[str, Any]]:
    """The last answered exchanges of the talk with the advisor."""
    # An exchange is under 1 KiB on the device; read generously, the talk is short.
    turns = files.tail(chat_path(), 4 * count, line_bytes=2048)
    return [turn for turn in turns if turn.get("ok")][-count:]


def world_info(cfg: dict[str, Any], get: Callable[..., Any]) -> dict[str, Any]:
    """The world's ``GET /api/v1/game/info`` (public, no key) as an object; ``{}`` if it is
    unreachable, refused, or answers in another form. The one reader of it: ``world_rules`` and
    ``world_forms`` read it by its form from here (cos20 #492, seventh review, finding 2)."""
    import advisor

    if world_refusal(cfg):
        return {}
    url = cfg["world"]["base_url"].rstrip("/") + "/api/v1/game/info"
    try:
        resp = get(url, timeout=15)
        resp.raise_for_status()
        info = resp.json()
    except advisor.call_errors():  # the talk goes on with what the plugin knows itself
        return {}
    return info if isinstance(info, dict) else {}


def world_rules(cfg: dict[str, Any], get: Callable[..., Any]) -> str:
    """The rules as the server itself states them; empty if unreachable. Each action is taken
    only as an object: until 0.1.13 an ``actions`` that was a list or a word ended ``ask`` with a
    traceback. The quote marks are taken out (``advisor.unmarked``) for both readers, ``ask``
    and the head's look back (``review/world.txt``): until 0.1.14 they stood there as the world
    wrote them (cos20 #492, betreuer pass 0f44aeb, B2)."""
    import advisor

    info = world_info(cfg, get)
    if not info:
        return ""
    rules = {part: info[part] for part in RULE_PARTS if part in info}
    given = rules.get("actions")
    actions = {
        str(name): {key: value for key, value in body.items() if key not in PROGRAM_ONLY}
        for name, body in (given.items() if isinstance(given, dict) else ())
        if isinstance(body, dict)
    }
    rules["actions"] = {name: body for name, body in actions.items() if body}
    rules["other_actions"] = sorted(name for name, body in actions.items() if not body)
    return advisor.unmarked(advisor.cut(json.dumps(rules), RULES_CHARS))


def world_forms(cfg: dict[str, Any], get: Callable[..., Any]) -> dict[str, Any]:
    """The form of each move as the server states it (cos20 #530); empty if the world is
    unreachable or publishes none."""
    import head

    return head.forms_of(world_info(cfg, get))


def recent_moves(count: int) -> list[str]:
    """Its last moves from the journal, in words, as a model may read them
    (``advisor.move_for_model``: the world's reason quoted, no names of other agents)."""
    import advisor
    from words import move_words

    lines = files.tail(state_dir() / "journal.jsonl", 10 * count)  # words, missions between
    moves = [advisor.move_for_model(line) for line in lines if "action" in line][-count:]
    return [
        move_words(move)
        if move["ok"]
        else f"{move_words(move)} (refused: {move.get('why', '')})"
        for move in moves
    ]


async def right_now(state: Any) -> str:
    """This Shikigon at this moment, for the advisor: its facts, its next and its last moves."""
    import advisor

    lines = [advisor.facts(state)]
    if state is not None:
        import contracts
        from cosmergon_decider_tree import TreeDecider
        from words import move_words

        options = await advisor.proposals(contracts.Careful(TreeDecider()), state)
        said = [move_words({"action": a, "kind": mission_kind(a, p)}) for a, p in options]
        lines.append("Its rules would play next: " + " or ".join(said))
    done = recent_moves(MOVES_TOLD)
    if done:
        lines.append("Its last moves: " + ", ".join(done))
    return "\n".join(lines)


async def ask(
    agent: Any, cfg: dict[str, Any], question: str, rules: str | None = None, send: Any = None
) -> dict[str, Any]:
    """A question from you to the advisor. Asked and answered on your machine, kept in a file.

    ``rules`` ``None``: read here, inside the boundary of ``with_agent`` (until 0.1.13 the command
    read them before it, and an odd answer of the world ended it with a traceback)."""
    import advisor
    import connections

    if rules is None:
        rules = await asyncio.to_thread(world_rules, cfg, connections.bounded_get)
    state = await agent.refresh_state()
    context = "Notes:\n" + NOTES.read_text(encoding="utf-8")
    if rules:
        context += "\nRules of the world, as its server states them:\n" + rules + "\n"
    context += "\nThis Shikigon right now:\n" + await right_now(state)
    import lessons

    learned = lessons.latest_text(state_dir())
    if learned:
        context += "\nWhat it learned from its own last moves:\n" + learned
    conf = advisor_settings(cfg)
    turn = await advisor.answer(
        conf, state_dir(), question, context, chat_tail(CHAT_KEPT), send
    )
    # Every exchange is kept, the talk is the player's (cos20 #468); the answer as long as the bar
    # shows it: until 0.1.14 a model's answer went in uncut and the terminal read it whole
    # (cos20 #492, twelfth review, B5), and the question too (thirteenth review, Kür 4).
    turn["q"] = str(turn.get("q", ""))[:QUESTION_CHARS]
    turn["a"] = advisor.cut(turn.get("a", ""), ANSWER_CHARS)
    files.append(chat_path(), turn)
    show_talk()
    return turn


class UnknownDecider(Exception):
    """The config names a decider that is not installed."""


def decider_name(cfg: dict[str, Any], conf: dict[str, Any]) -> str:
    """Which decider plays. The panel's line "advise on moves" goes over the config file for
    the built-in ones; its last step lets the player's agent take over (``head``, cos20 #527).
    A decider someone installed is theirs to keep."""
    named = str(cfg.get("brain", {}).get("decider") or "tree")
    advise = conf.get("advise")
    if advise is None or named not in BUILT_IN:
        return named
    if advise == "head":
        return "head"
    return "tree" if advise == "off" else "advised"


BUILT_IN = ("tree", "advised", "head")


def advisor_settings(cfg: dict[str, Any]) -> dict[str, Any]:
    """``advisor.settings`` as every caller in the brain reads them: the one place the day's
    limit is set. While the head has taken over, its limit (``head_max_per_day``, the player's
    to set, cos20 #527 D4) is the one limit, for every call on the one counter: a move, a look
    back, a question, a word, the dry run. Until 0.1.14 two callers set it by hand, and a question
    and the dry run were cut at ``max_per_day`` while the panel showed no limit (cos20 #566, F4)."""
    import advisor

    conf = advisor.settings(cfg, state_dir())
    if decider_name(cfg, conf) == "head":
        conf = {**conf, "max_per_day": conf.get("head_max_per_day", 0)}
    return conf


def advisor_now(cfg: dict[str, Any]) -> dict[str, Any]:
    """Who answers and how often it advises, for the panel."""
    import advisor

    conf = advisor_settings(cfg)
    name = decider_name(cfg, conf)
    advise: int | str = advisor.every(conf) if name == "advised" else 0
    if name == "head":  # the head plays every move
        advise = "head"
    return {
        "picked": advisor.configured(conf),
        "label": advisor.who(conf) if advisor.configured(conf) else "",
        "via": str(conf.get("via") or "http"),
        "advise": advise,
        "calls": advisor.calls_today(state_dir()),
        "limit": int(conf.get("max_per_day", 0) or 0),
    }


WORDS_ON_STDIN = ("words come on stdin or when it asks, never as arguments: arguments are "
                  "readable by every local user")
ADDRESS_ON_STDIN = ("an address comes through the panel or on stdin, never as an argument: it can "
                    "carry a name and a password, and arguments are readable by every local user")


def advisor_command(cfg: dict[str, Any], what: str, rest: list[str]) -> dict[str, Any]:
    """What the panel's "WHO ANSWERS?" needs: look, try, take, switch, drop."""
    if what in ("models", "try") and rest and not rest[0].startswith("agent:"):
        return {"ok": False, "why": ADDRESS_ON_STDIN}  # see the comment on stdin below
    import advisor
    import connections

    home = state_dir()
    conf = advisor_settings(cfg)
    # The bar sends an address, a model and a key on stdin as one JSON line: an address can
    # carry a name and a password, and arguments stand in every local user's process list
    # (cos20 #492 F3). Arguments still work at a terminal for an agent (``agent:pi``), never for
    # an address: it can carry a name and a password (0.1.4, H1/H6).
    given = json_on_stdin() if what in ("models", "try", "key") and not rest else {}
    if what == "list":
        return {"now": advisor_now(cfg), "found": connections.found(conf, connections.bounded_get)}
    where = str(given.get("where") or (rest[0] if rest else ""))
    if what == "models" and where:
        if where.startswith("agent:"):
            return connections.agent_listing(where[6:])
        key = connections.secret_for(home, conf, where)
        return connections.models_at(where, connections.bounded_get, key)
    if what == "key":  # never as an argument, so it never stands in a process list
        if not given.get("key") or not given.get("where"):
            return {"ok": False, "why": "a key comes on stdin with the address it is for"}
        connections.keep_key(home, str(given["key"]), str(given["where"]))
        return {"ok": True}
    connection = str(given.get("connection") or (rest[0] if rest else ""))
    if what == "try" and connection:
        model = str(given.get("model") or (rest[1] if len(rest) > 1 else ""))
        choice = pick(connection, model)
        if choice is None:
            return {"ok": False, "why": "unknown connection, or no model named"}
        tried = {**conf, **choice}
        patient = advisor.sender(tried, home, patience=advisor.ANSWER_PATIENCE)
        result: dict[str, Any] = connections.run_loop(connections.probe(patient))
        if result["ok"]:
            # a new connection replaces the old one whole; how often it advises stays
            before = connections.chosen(home)
            kept: dict[str, Any] = {k: before[k] for k in ("advise", "every") if k in before}
            connections.choose(home, {**kept, **choice})
            result["label"] = advisor.who(tried)
        return result
    if what == "advise" and rest:
        if rest[0] not in ("off", "head") and not rest[0].isdigit():
            return {"ok": False, "why": "advise off, a number of moves, or head"}
        often: int | str = rest[0] if rest[0] in ("off", "head") else max(1, int(rest[0]))
        kept = {key: value for key, value in connections.chosen(home).items() if key != "every"}
        connections.choose(home, {**kept, "advise": often,
                                  **({} if often in ("off", "head") else {"every": often})})
        return {"ok": True, "now": advisor_now(cfg)}
    if what == "forget":
        for name in (connections.CHOICE_FILE, connections.KEY_FILE):
            (home / name).unlink(missing_ok=True)
        return {"ok": True, "now": advisor_now(cfg)}
    usage = ("advisor list | models agent:NAME | key | try agent:NAME [MODEL] | advise off|N|head"
             " | forget — an address goes through the panel or on stdin")
    return {"ok": False, "why": usage}


def pick(connection: str, model: str) -> dict[str, Any] | None:
    """A row of the list as the settings it stands for: ``agent:codex`` or ``http:<address>``."""
    import connections

    kind, _, rest = connection.partition(":")
    if kind == "agent" and connections.AGENTS.get(rest, {}).get("proven"):
        return {"via": "agent", "agent": rest, "agent_model": model}
    if kind == "http" and rest and model:
        return {"via": "http", "base_url": rest, "model": model}
    return None


def make_decider(cfg: dict[str, Any], advised: bool = False) -> Any:
    """The decider named in ``[brain] decider``: ``tree``, ``advised``, ``head``, or an
    installed one.

    Other deciders register under the SDK's entry-point group ``cosmergon.deciders``.
    ``advised=True`` builds the advisor whatever the config names (for the dry run).
    The built-in ones also talk once an advisor is set up (``speech.py``, cos20 #526);
    a decider someone installed is theirs and is not wrapped.
    """
    import advisor
    import speech

    conf = advisor_settings(cfg)
    name = "advised" if advised else decider_name(cfg, conf)
    if name in BUILT_IN:
        import contracts
        from cosmergon_decider_tree import TreeDecider

        # the rules say no to a contract that would cost it (contracts.py, cos20 #492)
        inner: Any = contracts.Careful(TreeDecider())
        if name == "advised":
            inner = advisor.AdvisedDecider(inner, conf, state_dir(), dry=advised)
        elif name == "head":  # the player's agent chooses every move (cos20 #527)
            import connections
            import head

            def forms() -> Any:
                return asyncio.to_thread(world_forms, cfg, connections.bounded_get)

            inner = head.HeadDecider(inner, conf, state_dir(), forms=forms)
        if advised or not speech.wanted(conf):  # a dry run plays and says nothing
            return inner
        key = (load_credentials() or {}).get("api_key", "")
        return speech.TalkingDecider(inner, conf, state_dir(), key)
    from importlib.metadata import entry_points

    installed = {point.name: point for point in entry_points(group="cosmergon.deciders")}
    if name not in installed:
        known = ", ".join(sorted({*BUILT_IN, *installed}))
        raise UnknownDecider(f"unknown decider '{name}': known are {known}")
    return installed[name].load()()


async def learn(cfg: dict[str, Any], state: Any, force: bool = False,
                send: Any = None) -> dict[str, Any] | None:
    """A lesson from its last moves if one is due (``force``: now), written to the journal."""
    import advisor
    import lessons

    home = state_dir()
    conf = advisor_settings(cfg)
    if not advisor.configured(conf):
        return None
    send = send or advisor.sender(conf, home, patience=advisor.ANSWER_PATIENCE)
    head = decider_name(cfg, conf) == "head"
    world, secret = "", ""
    reader: Callable[[Path], Any] | None = None
    if head:  # it has taken over: it looks back often and reads (cos20 #527)
        import connections
        import reading

        secret = (load_credentials() or {}).get("api_key", "")
        world = await asyncio.to_thread(world_rules, cfg, connections.bounded_get)
        # it reads through the brain, never with a tool of its own (cos20 #492, K28)
        reader = functools.partial(reading.mediated, send)
    entry = await lessons.learn(home, conf, advisor.facts(state), send, force=force,
                                head=head, world=world, reader=reader, secret=secret)
    if entry is not None:
        journal(entry)
    return entry


NOTHING_TO_LEARN = ("nothing to learn from yet: it needs an advisor (ask in the panel) and "
                    "thirty moves since its last lesson")


async def learn_now(cfg: dict[str, Any], agent: Any) -> dict[str, Any]:
    """``learn``: a lesson now, without waiting for the hundredth move."""
    state = await agent.refresh_state()
    return await learn(cfg, state, force=True) or {"ok": False, "why": NOTHING_TO_LEARN}


def learning_next(cfg: dict[str, Any], state: Any, learning: Any) -> Any:
    """The lesson running beside the moves: the one still at work, a new one when due, or
    ``None``. A lesson that broke says so on stderr (the bar shows it) and is let go."""
    import lessons

    if learning is not None and not learning.done():
        return learning
    if learning is not None and not learning.cancelled() and learning.exception() is not None:
        print(f"learning failed: {learning.exception()!r}"[:200], file=sys.stderr)
    every = lessons.LEARN_EVERY
    conf = advisor_settings(cfg)
    if decider_name(cfg, conf) == "head":  # it has taken over: it looks back often (#527)
        every = max(1, int(conf.get("review_every", 5) or 5))
    if lessons.due(lessons.journal_tail(state_dir()), every):
        return asyncio.create_task(learn(cfg, state))
    return None


NOT_PLAYED = "this move could not be played"
NOT_PLAYED_COUNT = {"since_start": 0}  # how many, in the sentence of each (status.json, the bar)


async def guarded_tick(
    agent: Any, decider: Any, done_in: int | None = None, refusals: Refusals | None = None
) -> dict[str, Any]:
    """``tick`` behind the boundary of every move: whatever one move raises ends that move,
    never the brain (cos20 #492, sixth review, finding 3).

    The state, the choice and the move read the world's answer through the SDK and the rule tree;
    until 0.1.13 an answer of the wrong form there (a word for a number, a list for an object, no
    JSON at all) ended the brain, and the bar started it again only to see it end on the next
    move. Now the Shikigon sleeps for that move, says why in one line, and tries again on the
    next tick. Each such move is counted, in its own sentence: how often it happened since the
    brain started stands in ``status.json`` and in the bar. A key that ended and a port that
    changed hands pass on: ``with_agent`` puts it to sleep with their own words."""
    from cosmergon_agent.exceptions import AuthenticationError, SendRefusedError

    try:
        return await tick(agent, decider, done_in, refusals)
    except (AuthenticationError, SendRefusedError):
        raise
    except Exception as exc:  # noqa: BLE001 - the boundary of a move
        NOT_PLAYED_COUNT["since_start"] += 1
        count = NOT_PLAYED_COUNT["since_start"]
        why = f"{NOT_PLAYED} ({type(exc).__name__}; {count} since the brain started)"
        print(why, file=sys.stderr)
        return fall_asleep(WORLD_SILENT, why)


async def run(cfg: dict[str, Any], agent: Any, decider: Any) -> None:
    """A move, a pause, a move: the pause follows the world's tick, not only the clock.

    A lesson runs beside the moves, never in their way: through an agent it can take minutes.
    """
    interval = max(30.0, float(cfg.get("brain", {}).get("interval_s", 60)))
    show_journal()  # what the bar shows exists from the start, also after an update
    show_talk()
    done_in: int | None = None
    learning: Any = None
    refusals = Refusals(refusals_path())
    while True:
        if why := world_refusal(cfg):  # the world's port can change hands while it runs (H9)
            fall_asleep(CONFIG, why)
            return
        last = await guarded_tick(agent, decider, done_in, refusals)
        if last.get("skipped"):  # the world's tick is late: one choice per tick, so wait for it
            await asyncio.sleep(LATE_TICK_S)
            continue
        state = getattr(agent, "state", None)
        done_in = getattr(state, "tick", None)
        learning = learning_next(cfg, state, learning)
        await asyncio.sleep(pause(interval, state))


async def with_agent(
    cfg: dict[str, Any], work: Callable[[Any], Awaitable[Any]], *, once: bool = True
) -> Any:
    """Open the SDK connection around one piece of work.

    An expired key is not a crash: the Shikigon falls asleep and says why. Its world
    keeps it as a vagant that follows your last briefing (server side). A single command
    (``once``: tick, advise, ask, instruct, learn) answers anything else that went wrong as JSON
    in one sentence, not with a traceback (cos20 #492, sixth review, finding 3). The run loop
    (``once=False``) has its own boundary around each move (``guarded_tick``); what fails outside
    a move ends it with an exit code, so the bar starts it again.
    """
    from cosmergon_agent.exceptions import AuthenticationError, SendRefusedError

    creds = load_credentials()
    if key_lost(creds):
        return fall_asleep(KEY_LOST, KEY_LOST_WHY)
    if why := world_refusal(cfg) or (key_refusal(cfg, creds) if creds else ""):
        return fall_asleep(CONFIG, why)
    agent = make_agent(cfg)
    try:
        async with agent:
            return await work(agent)
    except AuthenticationError:
        return fall_asleep(KEY_ENDED, "key expired: register a new Shikigon")
    except SendRefusedError as exc:  # the world's port changed hands at the moment of sending
        return fall_asleep(CONFIG, exc.message)
    except Exception as exc:  # one command says why in one sentence
        if not once:
            raise
        return {"ok": False, "why": f"it could not finish this ({type(exc).__name__})"}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="shikigon_brain")
    sub = parser.add_subparsers(dest="command", required=True)
    reg = sub.add_parser("register")
    reg.add_argument("--new", action="store_true", help="replace the Shikigon that lives here")
    for name in ("tick", "run", "status", "advise", "learn"):
        sub.add_parser(name)
    sub.add_parser("terminal", help="may the terminal client open for this Shikigon?")
    gone = sub.add_parser("forget")
    gone.add_argument("--yes", action="store_true", help="yes, delete it on the server and here")
    for name in ("instruct", "ask"):
        # Words come in on stdin, or when it asks at a terminal: arguments stand in
        # /proc/<pid>/cmdline for every local user (#11550). They are taken only to refuse them.
        sub.add_parser(name).add_argument("text", nargs="*")
    who = sub.add_parser("advisor", help="who answers: list, try and take a connection")
    who.add_argument("what", nargs="?", default="list")
    who.add_argument("rest", nargs="*")
    sub.add_parser("sign-in", help="start an agent's own program to sign in there").add_argument(
        "agent")
    return parser


# The umask the brain was started with; ``__main__`` sets its own to 077 (K2) and keeps this one
# for the program ``sign_in`` hands the terminal to.
INHERITED_UMASK = 0o022


def start_program(path: str, argv: list[str]) -> None:
    """Become the program at ``path`` in ``/`` (``sign_in``), with the umask the brain was
    started with."""
    import connections

    folder = connections.neutral_folder()
    os.umask(INHERITED_UMASK)
    os.chdir(folder)
    os.execv(path, argv)


def sign_in(agent: str, start: Callable[[str, list[str]], Any] = start_program) -> str:
    """Start ``agent``'s own program in this terminal, where the player signs in (pi: ``/login``);
    the bar opens the terminal for it. On success it does not return. Otherwise it says why:
    Omarchy's stub stands in the program's place (starting it would install it), or the program
    is not there. The path checked is the path started (``connections.sign_in_argv``). The
    program gets this process's environment and the umask the brain was started with, not
    the brain's own 077: the player may go on working in it.

    It starts in ``/``, as every program Shikigon starts (``connections.neutral_folder``): pi
    reads ``AGENTS.md`` and ``CLAUDE.md`` from its working folder and every folder above it,
    with tools and without asking. Until 0.1.14 it started in the folder the brain was started
    from, and a file someone else wrote there reached its system prompt (cos20 #492,
    Betreuer-Durchgang 0563e7c, B1)."""
    import connections

    try:
        argv = connections.sign_in_argv(agent)
    except connections.Placeholder:
        return connections.placeholder_why(agent) or "Omarchy's placeholder stands in its place"
    except FileNotFoundError:
        return f"{CONTROL.sub('', agent)[:WORD_CHARS]} cannot be signed in to from here"
    try:
        start(argv[0], argv)
    except PermissionError as exc:  # ``/`` is not the system's alone: nothing is started
        return str(exc)
    return ""


def _sign_in(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    """``sign-in``: what ``sign_in`` says, readable before the terminal closes."""
    print(sign_in(args.agent))
    if sys.stdin is not None and sys.stdin.isatty():
        with contextlib.suppress(EOFError):
            input("Press Enter to close this window.")
    return 1


def _local(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    """What needs no Shikigon of its own: the advisor, the terminal's world, the snapshot."""
    if args.command == "terminal":
        why = terminal_refusal(cfg, load_credentials() or {})
        if why:
            print(why)
        return 1 if why else 0
    if args.command == "advisor":
        result = advisor_command(cfg, args.what, args.rest)
        print(json.dumps(result))
        return 0 if result.get("ok", True) else 1
    path = state_dir() / "status.json"
    print(path.read_text(encoding="utf-8") if path.exists() else json.dumps({"mood": ASLEEP}))
    return 0


def _account(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    """Its life on the server: register, a lesson now, forget."""
    import connections

    if args.command == "register":
        print(json.dumps(register(cfg, connections.bounded_post, replace=args.new)))
        return 0
    if args.command == "learn":
        print(json.dumps(connections.run_loop(with_agent(cfg, lambda a: learn_now(cfg, a)))))
        return 0
    if not args.yes:
        why = "this deletes your Shikigon on the server and its files here: add --yes"
        print(json.dumps({"ok": False, "why": why}))
        return 1
    result = forget(cfg, connections.bounded_delete)
    print(json.dumps(result))
    return 0 if result["ok"] else 1


def _moves(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    """A move (tick), the loop (run), or a dry run with the advisor (advise)."""
    import connections

    try:
        decider = make_decider(cfg, advised=args.command == "advise")
    except UnknownDecider as exc:
        # A config it cannot follow is not a crash: it sleeps and says which names it knows.
        print(json.dumps(fall_asleep(CONFIG, str(exc))))
        return 1
    if args.command == "run":
        connections.run_loop(with_agent(cfg, lambda a: run(cfg, a, decider), once=False))
        return 0
    async def work(agent: Any) -> dict[str, Any]:
        if args.command == "tick":  # the pauses of the running brain hold here too
            return await guarded_tick(agent, decider, None, Refusals(refusals_path()))
        return await advise(agent, decider)

    print(json.dumps(connections.run_loop(with_agent(cfg, work))))
    return 0


def _words(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    """A word from the player: a question for the advisor (ask) or for the compass (instruct)."""
    if args.text:
        print(json.dumps({"ok": False, "why": WORDS_ON_STDIN}))
        return 1
    text = said_on_stdin("Your question" if args.command == "ask" else "Your words")
    if not text:
        print(json.dumps({"ok": False, "why": f"nothing to {args.command}: no words on stdin"}))
        return 1
    import connections

    table = cfg.get("instructions", {})
    work = (lambda a: ask(a, cfg, text)) if args.command == "ask" else (
        lambda a: instruct(a, text, table))
    print(json.dumps(connections.run_loop(with_agent(cfg, work))))
    return 0


COMMANDS: dict[str, Callable[[argparse.Namespace, dict[str, Any]], int]] = {
    **dict.fromkeys(("advisor", "status", "terminal"), _local),
    **dict.fromkeys(("register", "learn", "forget"), _account),
    **dict.fromkeys(("tick", "run", "advise"), _moves),
    **dict.fromkeys(("ask", "instruct"), _words),
    "sign-in": _sign_in,
}


# What a terminal reads as a command: C0 (ESC among them), DEL and the 8-bit C1 (CSI, OSC).
CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


class CutLines:
    """stderr as the bar reads it: each line at most ``limit`` characters (H5, §1b). The bar's
    ``SplitParser`` keeps a whole line before it cuts it, so a line from a traceback or a
    server's message is cut here, before it can reach the shared shell.

    Without control characters (K37): a world's sentence on a refused registration goes on
    stderr, and the wake script and ``register`` by hand show it in a terminal."""

    def __init__(self, stream: Any, limit: int = 200) -> None:
        self.stream, self.limit, self.used = stream, limit, 0

    def write(self, text: str) -> int:
        for index, part in enumerate(text.split("\n")):
            if index:
                self.stream.write("\n")
                self.used = 0
            kept = CONTROL.sub("", part)[: max(0, self.limit - self.used)]
            self.stream.write(kept)
            self.used += len(kept)
        return len(text)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.stream, name)


def end_with_its_agents() -> None:
    """The agents the brain started end with it (K14, cos20 #492): when the bar stops it
    (SIGTERM, on every change of "advise on moves"), when its terminal closes (SIGHUP) or on
    Ctrl+C. An agent runs in a session of its own (``connections.run_in_group``); until 0.1.13 it
    went on to its own end then, outside every limit."""
    import connections

    def end(signum: int, _frame: Any) -> None:
        connections.end_groups()
        raise SystemExit(128 + signum)

    for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(signum, end)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    private_state()
    return COMMANDS[args.command](args, load_config())

if __name__ == "__main__":
    INHERITED_UMASK = os.umask(0o077)  # what it makes is the player's alone (K2)
    sys.stderr = CutLines(sys.stderr)
    end_with_its_agents()
    sys.exit(main())
