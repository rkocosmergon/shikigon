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
    shikigon_brain.py coach [TEXT]  # the task text for your own coding agent (no model here)

Nothing here ever prints or logs the API key.
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import json
import os
import shutil
import sys
import time
import tomllib
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
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "shikigon" / "shikigon.toml"


def state_dir() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return base / "shikigon"


def private_state() -> Path:
    """The state folder, the player's alone (0700), also when a command by hand comes before
    the first wake (cos20 #492 F8: it was made with the default mode then)."""
    home = state_dir()
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(home, 0o700)
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
        # on stderr: inside "$(shikigon coach)" stdout is what the agent gets
        print(f"{asked}: ", end="", file=sys.stderr, flush=True)
    return sys.stdin.readline().strip()


def json_on_stdin() -> dict[str, Any]:
    """One JSON object on stdin: how the bar sends an address or a model (never as arguments)."""
    try:
        data = json.loads(said_on_stdin())
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
    """Write JSON readable by the owner only (0600), created that way, never widened."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle)
    os.replace(tmp, path)


def credentials_path() -> Path:
    return state_dir() / "credentials.json"


def load_credentials() -> dict[str, str] | None:
    path = credentials_path()
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else None


def registration_body(cfg: dict[str, Any]) -> dict[str, Any]:
    """What the server is told when a new Shikigon is born; empty choices stay unset."""
    character = cfg.get("character", {})
    body: dict[str, Any] = {"source": SOURCE}
    if character.get("persona"):
        body["persona"] = character["persona"]
    if character.get("name"):
        body["agent_name"] = character["name"]
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


def register(
    cfg: dict[str, Any], post: Callable[..., Any], replace: bool = False
) -> dict[str, str]:
    """Register an agent of its own and keep its key in a private file.

    An existing Shikigon is never overwritten by accident: its key is its life.
    A world that is away or refuses is said in one sentence, not in a traceback.
    ``post`` is ``httpx.post`` in use and a stub in tests.
    """
    import advisor

    if load_credentials() is not None and not replace:
        raise SystemExit("A Shikigon already lives here. Use `register --new` to replace it.")
    if not world_is_safe(cfg):
        raise SystemExit(WORLD_KEY_SAFE)
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
    data = resp.json()
    creds = {
        "agent_id": data["agent_id"],
        "api_key": data["api_key"],
        "expires_at": data.get("expires_at", ""),
    }
    write_private(credentials_path(), creds)
    return {"agent_id": creds["agent_id"], "expires_at": creds["expires_at"]}


def forget(cfg: dict[str, Any], delete: Callable[..., Any]) -> dict[str, Any]:
    """Remove this Shikigon for good: its agent on the server and its files here.

    The server deletes the agent. A key that has already ended can delete nothing: the
    world keeps that agent as a Vagant, and only the files here go.
    If the server does not answer or refuses, nothing here is removed, so you can try again.
    Your configuration stays. ``delete`` is ``httpx.delete`` in use and a stub in tests.
    """
    import advisor

    creds = load_credentials()
    if creds is None:
        raise SystemExit("No Shikigon lives here.")
    if not world_is_safe(cfg):
        return {"ok": False, "why": WORLD_KEY_SAFE}
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
    """Why it does what it does, in words: read from what the server offers it."""
    offered = getattr(state, "available_actions", None) or {}
    notes = []
    marauder = (offered.get("start_mission") or {}).get("marauder_state")
    if marauder:
        notes.append(MARAUDER_TEXT.get(marauder, f"its marauder: {marauder}"))
    full = (offered.get("create_field") or {}).get("slots_available") is False
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


def journal(entry: dict[str, Any]) -> None:
    files.append(state_dir() / "journal.jsonl", entry, JOURNAL_KEEP_BYTES)
    show_journal()


def show_journal() -> None:
    """The journal's last lines for the bar."""
    lines = files.tail(state_dir() / "journal.jsonl", BAR_LINES, line_bytes=1024)
    files.write_json(state_dir() / BAR_JOURNAL, lines)


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
SEEN_KEEP = 200  # mission ids remembered; the server lists at most 50 per call


def tournament_running(state: Any) -> bool:
    """True while the server reports a running tournament for this agent."""
    brief = getattr(state, "tournament", None)
    return isinstance(brief, dict) and brief.get("status") == "running"


def mission_entry(mission: dict[str, Any], tick_no: int) -> dict[str, Any]:
    """A journal line for a tournament mission that ended, in a player's words."""
    from cosmergon_agent import mission_outcome_text, mission_word

    status = str(mission.get("status") or "")
    text = mission_outcome_text(str(mission.get("outcome") or ""))
    return {
        "at": int(time.time()),
        "tick": tick_no,
        "mission": mission_word(str(mission.get("mission_type") or "mission")),
        "text": text or ("done" if status == "completed" else status),
        "ok": status != "failed",
    }


def seen_path() -> Path:
    return state_dir() / "tournament_seen.json"


def load_seen() -> list[str] | None:
    """Mission ids already written down; ``None`` before the first look."""
    path = seen_path()
    if not path.exists():
        return None
    return [str(i) for i in json.loads(path.read_text(encoding="utf-8")).get("ids", [])]


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
        for m in reversed(missions)  # the server lists newest first
        if m.get("body") == "tournament" and m.get("status") in ("completed", "failed", "cancelled")
    ]
    seen = load_seen()
    new = [m for m in ended if str(m.get("mission_id")) not in (seen or [])]
    if seen is not None:
        for mission in new:
            journal(mission_entry(mission, getattr(state, "tick", 0)))
    ids = (seen or []) + [str(m.get("mission_id")) for m in new]
    files.write_json(seen_path(), {"ids": ids[-SEEN_KEEP:]})
    return 0 if seen is None else len(new)


# Why it sleeps, as one word the bar can act on (a key that ended wants a different next
# step than a world that did not answer). The sentence for you stays in ``last.why``.
KEY_ENDED, WORLD_SILENT, CONFIG = "key_ended", "world_silent", "config"


def fall_asleep(reason: str, why: str) -> dict[str, Any]:
    """Write the sleeping snapshot and return what is said about it."""
    last = {"action": "none", "ok": False, "why": why}
    files.write_json(state_dir() / "status.json", {"mood": ASLEEP, "reason": reason, "last": last})
    return last


async def play(agent: Any, action: str, params: dict[str, Any]) -> dict[str, Any]:
    """Send one move. A move the world does not take is said in words, never a crash.

    The world takes one move per tick and answers a second one with 429 (a `tick`
    from a terminal next to the running brain); a lost connection is the other case.
    """
    from cosmergon_agent.exceptions import ConnectionError as CsgConnectionError
    from cosmergon_agent.exceptions import RateLimitError

    try:
        result = await agent.act(action, **params)
    except RateLimitError:
        why = "the world takes one move per tick: it already moved in this one"
        return {"action": action, "ok": False, "why": why}
    except CsgConnectionError:
        return {"action": action, "ok": False, "why": "the world did not answer"}
    why = "" if result.success else (result.error_message or "")
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
        done["effect"] = {k: float(effect.get(k) or 0.0) for k in ("free", "held", "items")}
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


class Refusals:
    """Which moves the world keeps refusing, and which are paused. Kept only while it runs."""

    def __init__(self) -> None:
        self.moves = 0
        self._in_a_row: dict[str, int] = {}
        self._paused_until: dict[str, int] = {}

    def paused(self) -> frozenset[str]:
        return frozenset(a for a, until in self._paused_until.items() if until > self.moves)

    def note(self, last: dict[str, Any]) -> bool:
        """Count one move; ``True`` when this refusal pauses it."""
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
    """After the world said no, a decider that can (the head, cos20 #527) chooses again.

    A refusal frees the tick on the server, so the new move counts for this tick. Each
    refusal is counted, so a move the world keeps refusing is paused as before. The decider
    ends it (``None``) after its own few tries.
    """
    again = getattr(decider, "again", None)
    while again is not None and last.get("refused"):
        paused = refusals.paused() if refusals is not None else frozenset()
        choice = await again(state, last, paused)
        if choice is None:
            break  # the last refusal is counted by the caller, like any move's
        if refusals is not None:
            refusals.note(last)
        action, params = choice
        if action == "wait":
            return {"action": "wait", "ok": True, "why": "chose to wait"}
        last = await play(agent, action, params)
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
        last = await try_again(agent, decider, state, await play(agent, action, params), refusals)
    if refusals is not None and refusals.note(last):
        said = f"refused {REFUSALS_BEFORE_PAUSE} times, paused for {PAUSED_MOVES} moves"
        last["why"] = f"{last['why']} — {said}" if last.get("why") else said
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
    files.write_json(state_dir() / "status.json", status)
    return last


def spend_budget(advice: dict[str, Any], last: dict[str, Any]) -> None:
    """Count what a move the head chose itself lost against its learning budget (cos20 #533)."""
    import head
    import lessons

    effect = lessons.own_effect(last)
    if advice.get("head") and advice.get("own") and last.get("ok") and effect is not None:
        head.budget_spend(state_dir(), -effect)


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
    return CosmergonAgent(
        api_key=creds["api_key"],
        agent_id=creds["agent_id"],
        base_url=cfg["world"]["base_url"],
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
# than doubled it; the head reads them on their own (``world_forms``).
PROGRAM_ONLY = ("use_case", "requires", "effects", "forms")


def chat_path() -> Path:
    return state_dir() / "chat.jsonl"


def chat_tail(count: int) -> list[dict[str, Any]]:
    """The last answered exchanges of the talk with the advisor."""
    # An exchange is under 1 KiB on the device; read generously, the talk is short.
    turns = files.tail(chat_path(), 4 * count, line_bytes=2048)
    return [turn for turn in turns if turn.get("ok")][-count:]


def world_rules(cfg: dict[str, Any], get: Callable[..., Any]) -> str:
    """The rules as the server itself states them (public, no key); empty if unreachable."""
    import advisor

    url = cfg["world"]["base_url"].rstrip("/") + "/api/v1/game/info"
    try:
        resp = get(url, timeout=15)
        resp.raise_for_status()
        info = resp.json()
    except advisor.call_errors():  # the talk goes on with what the plugin knows itself
        return ""
    if not isinstance(info, dict):
        return ""
    rules = {part: info[part] for part in RULE_PARTS if part in info}
    actions = {
        name: {key: value for key, value in body.items() if key not in PROGRAM_ONLY}
        for name, body in (rules.get("actions") or {}).items()
        if isinstance(body, dict)
    }
    rules["actions"] = {name: body for name, body in actions.items() if body}
    rules["other_actions"] = sorted(name for name, body in actions.items() if not body)
    return json.dumps(rules)


def world_forms(cfg: dict[str, Any], get: Callable[..., Any]) -> dict[str, Any]:
    """The form of each move as the server states it (public, no key; cos20 #530); empty if
    the world is unreachable or publishes none."""
    import advisor
    import head

    url = cfg["world"]["base_url"].rstrip("/") + "/api/v1/game/info"
    try:
        resp = get(url, timeout=15)
        resp.raise_for_status()
        return head.forms_of(resp.json())
    except advisor.call_errors():
        return {}


def recent_moves(count: int) -> list[str]:
    """Its last moves from the journal, in words."""
    lines = files.tail(state_dir() / "journal.jsonl", 10 * count)  # words, missions between
    moves = [line for line in lines if "action" in line][-count:]
    from words import move_words

    return [
        move_words(move)
        if move.get("ok")
        else f"{move_words(move)} (refused: {move.get('why', '')})"
        for move in moves
    ]


async def right_now(state: Any) -> str:
    """This Shikigon at this moment, for the advisor: its facts, its next and its last moves."""
    import advisor

    lines = [advisor.facts(state)]
    if state is not None:
        from cosmergon_decider_tree import TreeDecider
        from words import move_words

        options = await advisor.proposals(TreeDecider(), state)
        said = [move_words({"action": a, "kind": mission_kind(a, p)}) for a, p in options]
        lines.append("Its rules would play next: " + " or ".join(said))
    done = recent_moves(MOVES_TOLD)
    if done:
        lines.append("Its last moves: " + ", ".join(done))
    return "\n".join(lines)


async def ask(
    agent: Any, cfg: dict[str, Any], question: str, rules: str, send: Any = None
) -> dict[str, Any]:
    """A question from you to the advisor. Asked and answered on your machine, kept in a file."""
    import advisor

    state = await agent.refresh_state()
    context = "Notes:\n" + NOTES.read_text(encoding="utf-8")
    if rules:
        context += "\nRules of the world, as its server states them:\n" + rules + "\n"
    context += "\nThis Shikigon right now:\n" + await right_now(state)
    import lessons

    learned = lessons.latest_text(state_dir())
    if learned:
        context += "\nWhat it learned from its own last moves:\n" + learned
    conf = advisor.settings(cfg, state_dir())
    turn = await advisor.answer(
        conf, state_dir(), question, context, chat_tail(CHAT_KEPT), send
    )
    files.append(chat_path(), turn)  # kept whole: the talk is the player's (cos20 #468)
    show_talk()
    return turn


SKILL = Path(__file__).resolve().parent.parent / "skill" / "SKILL.md"


def brain_python() -> str:
    """The Python that has the brain's packages: its own environment, once it is set up."""
    own = Path.home() / ".local" / "share" / "shikigon" / "venv" / "bin" / "python"
    return str(own) if own.exists() else sys.executable


def coach_prompt(cfg: dict[str, Any], question: str) -> str:
    """The task for the player's own coding agent (on Omarchy: ``omarchy agent prompt``).

    Shikigon connects to no model here: it hands the agent the facts and points it to the
    skill file, the way Omarchy's own crash helper does. The snapshot never holds the key.
    """
    path = state_dir() / "status.json"
    shown = path.read_text(encoding="utf-8") if path.exists() else json.dumps({"mood": ASLEEP})
    asks = question.strip() or "What is my Shikigon doing, and what could I do next?"
    return "\n".join(
        [
            "You are coaching the player of a Shikigon, a Cosmergon agent in their Omarchy bar.",
            f"Read this skill file first and follow it: {SKILL}",
            "(If your harness has no skill mechanism, read the file directly.)",
            "",
            f"<brain> is: {brain_python()} {Path(__file__).resolve()}",
            f"State folder: {state_dir()}",
            f"<world> is: {cfg['world']['base_url'].rstrip('/')}",
            f"Its snapshot right now: {shown}",
            "",
            f"The player asks: {asks}",
        ]
    )


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


def advisor_now(cfg: dict[str, Any]) -> dict[str, Any]:
    """Who answers and how often it advises, for the panel."""
    import advisor

    conf = advisor.settings(cfg, state_dir())
    name = decider_name(cfg, conf)
    advise: int | str = advisor.every(conf) if name == "advised" else 0
    limit_key = "max_per_day"
    if name == "head":  # the head plays every move; a limit is the player's to set (#527 D4)
        advise, limit_key = "head", "head_max_per_day"
    return {
        "picked": advisor.configured(conf),
        "label": advisor.who(conf) if advisor.configured(conf) else "",
        "via": str(conf.get("via") or "http"),
        "advise": advise,
        "calls": advisor.calls_today(state_dir()),
        "limit": int(conf.get(limit_key, 0) or 0),
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
    conf = advisor.settings(cfg, home)
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
            return {"ok": True, "models": connections.agent_models(where[6:])}
        key = connections.kept_key(home, where)
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
        result = asyncio.run(connections.probe(patient))
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

    conf = advisor.settings(cfg, state_dir())
    name = "advised" if advised else decider_name(cfg, conf)
    if name in BUILT_IN:
        from cosmergon_decider_tree import TreeDecider

        inner: Any = TreeDecider()
        if name == "advised":
            inner = advisor.AdvisedDecider(inner, conf, state_dir(), dry=advised)
        elif name == "head":  # the player's agent chooses every move (cos20 #527)
            import head
            import httpx

            inner = head.HeadDecider(inner, conf, state_dir(),
                                     forms=lambda: asyncio.to_thread(world_forms, cfg, httpx.get))
            # one counter, one limit: the head's, which is the player's to set (D4)
            conf = {**conf, "max_per_day": conf.get("head_max_per_day", 0)}
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
    conf = advisor.settings(cfg, home)
    if not advisor.configured(conf):
        return None
    send = send or advisor.sender(conf, home, patience=advisor.ANSWER_PATIENCE)
    head = decider_name(cfg, conf) == "head"
    world, secret = "", ""
    reader: Callable[[Path], Any] | None = None
    if head:  # it has taken over: it looks back often, reads if it can (cos20 #527)
        import connections
        import httpx

        conf = {**conf, "max_per_day": conf.get("head_max_per_day", 0)}
        secret = (load_credentials() or {}).get("api_key", "")
        if connections.reads_back(conf):
            world = await asyncio.to_thread(world_rules, cfg, httpx.get)
            reader = functools.partial(
                connections.review_send, conf, wait=connections.AGENT_ANSWER_S)
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
    import advisor

    every = lessons.LEARN_EVERY
    conf = advisor.settings(cfg, state_dir())
    if decider_name(cfg, conf) == "head":  # it has taken over: it looks back often (#527)
        every = max(1, int(conf.get("review_every", 5) or 5))
    if lessons.due(lessons.journal_tail(state_dir()), every):
        return asyncio.create_task(learn(cfg, state))
    return None


async def run(cfg: dict[str, Any], agent: Any, decider: Any) -> None:
    """A move, a pause, a move: the pause follows the world's tick, not only the clock.

    A lesson runs beside the moves, never in their way: through an agent it can take minutes.
    """
    interval = max(30.0, float(cfg.get("brain", {}).get("interval_s", 60)))
    show_journal()  # what the bar shows exists from the start, also after an update
    show_talk()
    done_in: int | None = None
    learning: Any = None
    refusals = Refusals()
    while True:
        last = await tick(agent, decider, done_in, refusals)
        if last.get("skipped"):  # the world's tick is late: one choice per tick, so wait for it
            await asyncio.sleep(LATE_TICK_S)
            continue
        state = getattr(agent, "state", None)
        done_in = getattr(state, "tick", None)
        learning = learning_next(cfg, state, learning)
        await asyncio.sleep(pause(interval, state))


async def with_agent(cfg: dict[str, Any], work: Callable[[Any], Awaitable[Any]]) -> Any:
    """Open the SDK connection around one piece of work.

    An expired key is not a crash: the Shikigon falls asleep and says why. Its world
    keeps it as a vagant that follows your last briefing (server side).
    """
    from cosmergon_agent.exceptions import AuthenticationError

    if not world_is_safe(cfg):
        return fall_asleep(CONFIG, WORLD_KEY_SAFE)
    agent = make_agent(cfg)
    try:
        async with agent:
            return await work(agent)
    except AuthenticationError:
        return fall_asleep(KEY_ENDED, "key expired: register a new Shikigon")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="shikigon_brain")
    sub = parser.add_subparsers(dest="command", required=True)
    reg = sub.add_parser("register")
    reg.add_argument("--new", action="store_true", help="replace the Shikigon that lives here")
    for name in ("tick", "run", "status", "advise", "learn"):
        sub.add_parser(name)
    gone = sub.add_parser("forget")
    gone.add_argument("--yes", action="store_true", help="yes, delete it on the server and here")
    for name in ("instruct", "ask", "coach"):
        # Words come in on stdin, or when it asks at a terminal: arguments stand in
        # /proc/<pid>/cmdline for every local user (#11550). They are taken only to refuse them.
        sub.add_parser(name).add_argument("text", nargs="*")
    who = sub.add_parser("advisor", help="who answers: list, try and take a connection")
    who.add_argument("what", nargs="?", default="list")
    who.add_argument("rest", nargs="*")
    return parser


def _local(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    """What needs no Shikigon of its own: the coach's task, the advisor, the snapshot."""
    if args.command == "coach":
        if args.text:
            print(json.dumps({"ok": False, "why": WORDS_ON_STDIN}))
            return 1
        # The task holds its snapshot (energy is the player's alone) and the question: it goes
        # to a file for the owner only, and the agent's command line names the file alone
        # (`omarchy agent prompt` takes its prompt as an argument; cos20 #492 F7).
        asked = said_on_stdin("Your question (Enter: none)")
        path = private_state() / "coach.md"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(coach_prompt(cfg, asked) + "\n")
        print(f"Read the task in {path} and follow it.")
        return 0
    if args.command == "advisor":
        result = advisor_command(cfg, args.what, args.rest)
        print(json.dumps(result))
        return 0 if result.get("ok", True) else 1
    path = state_dir() / "status.json"
    print(path.read_text(encoding="utf-8") if path.exists() else json.dumps({"mood": ASLEEP}))
    return 0


def _account(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    """Its life on the server: register, a lesson now, forget."""
    if args.command == "register":
        import httpx

        print(json.dumps(register(cfg, httpx.post, replace=args.new)))
        return 0
    if args.command == "learn":
        print(json.dumps(asyncio.run(with_agent(cfg, lambda a: learn_now(cfg, a)))))
        return 0
    if not args.yes:
        why = "this deletes your Shikigon on the server and its files here: add --yes"
        print(json.dumps({"ok": False, "why": why}))
        return 1
    import httpx

    result = forget(cfg, httpx.delete)
    print(json.dumps(result))
    return 0 if result["ok"] else 1


def _moves(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    """A move (tick), the loop (run), or a dry run with the advisor (advise)."""
    try:
        decider = make_decider(cfg, advised=args.command == "advise")
    except UnknownDecider as exc:
        # A config it cannot follow is not a crash: it sleeps and says which names it knows.
        print(json.dumps(fall_asleep(CONFIG, str(exc))))
        return 1
    if args.command == "run":
        asyncio.run(with_agent(cfg, lambda a: run(cfg, a, decider)))
        return 0
    act = tick if args.command == "tick" else advise
    print(json.dumps(asyncio.run(with_agent(cfg, lambda a: act(a, decider)))))
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
    if args.command == "ask":
        import httpx

        rules = world_rules(cfg, httpx.get)
        print(json.dumps(asyncio.run(with_agent(cfg, lambda a: ask(a, cfg, text, rules)))))
    else:
        table = cfg.get("instructions", {})
        print(json.dumps(asyncio.run(with_agent(cfg, lambda a: instruct(a, text, table)))))
    return 0


COMMANDS: dict[str, Callable[[argparse.Namespace, dict[str, Any]], int]] = {
    **dict.fromkeys(("coach", "advisor", "status"), _local),
    **dict.fromkeys(("register", "learn", "forget"), _account),
    **dict.fromkeys(("tick", "run", "advise"), _moves),
    **dict.fromkeys(("ask", "instruct"), _words),
}


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    private_state()
    return COMMANDS[args.command](args, load_config())

if __name__ == "__main__":
    sys.exit(main())
