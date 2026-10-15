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


def said_on_stdin() -> str:
    """One line from stdin (the bar and the terminal send their words this way); none on a tty."""
    if sys.stdin is None or sys.stdin.isatty():
        return ""
    return sys.stdin.readline().strip()


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


def snapshot(state: Any, last: dict[str, Any]) -> dict[str, Any]:
    """What the bar shows. Facts only, never the key."""
    ranking = getattr(state, "ranking", None)
    return {
        "name": getattr(state, "agent_name", ""),
        "persona": getattr(state, "persona_type", ""),
        "mood": mood(state),
        "energy": round(float(getattr(state, "energy", 0.0)), 1),
        "fields": len(getattr(state, "fields", []) or []),
        "rank": getattr(ranking, "champion_rang", 0) if ranking else 0,
        "compass": getattr(state, "compass_preset", None) or "autonomous",
        "tick": getattr(state, "tick", 0),
        "situation": situation(state),
        # No field in a full world (the bar's first-time hint reads this, not a sentence)
        "first_field": FIRST_FIELD in situation(state),
        # The tournament it plays in, as the server sends it (cos20 #475): passed on, not
        # picked from, so a new field of the server reaches the bar without a change here.
        "tournament": getattr(state, "tournament", None),
        # The server's own sentence while something is still missing (where you are, what
        # comes next), and when its key ends. Passed on the same way; ``None`` when the
        # server has nothing to say.
        "next_step": getattr(state, "next_step", None),
        "key_expiry": getattr(state, "key_expiry", None),
        "last": last,
        "updated_at": int(time.time()),
    }


# The journal is cut to its last 128 KiB once it passes 256 KiB: about a thousand lines, more
# than the lessons read (lessons.TAIL) and far more than the bar shows. It grew by ~80 KiB a
# day on the device, and the bar reads it whole every few seconds.
JOURNAL_KEEP_BYTES = 128 * 1024


def journal(entry: dict[str, Any]) -> None:
    files.append(state_dir() / "journal.jsonl", entry, JOURNAL_KEEP_BYTES)


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
    done = {"action": action, "ok": bool(result.success), "why": why[:200]}
    kind = mission_kind(action, params)
    if kind:
        done["kind"] = kind  # the journal says "mission: gather spores", not "start_mission"
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


async def tick(agent: Any, decider: Any, done_in: int | None = None) -> dict[str, Any]:
    """One move: look, choose, act, write it down.

    ``done_in`` is the tick the running brain already chose in. The world takes one move per
    tick, and when "next tick at" comes late the state still shows that tick: then nothing
    is chosen, no advisor is asked, nothing is written (``skipped``).
    """
    state = await agent.refresh_state()
    if state is None:
        return fall_asleep(WORLD_SILENT, "no state from the world")
    if done_in and getattr(state, "tick", 0) == done_in:
        return {"action": "", "ok": True, "why": "", "skipped": "same tick"}
    action, params = await decider.decide(state)
    if action == "wait":
        last = {"action": "wait", "ok": True, "why": "chose to wait"}
    else:
        last = await play(agent, action, params)
    from words import move_words

    last["words"] = move_words(last)  # what the panel and its advisor show (words.py)
    advice = getattr(decider, "last", None)  # what an advisor did on this move, if one was asked
    if advice:
        last["advice"] = advice
    # its energy and fields when it chose: what it learns from (lessons.py)
    entry = {"at": int(time.time()), "tick": getattr(state, "tick", 0),
             "energy": round(float(getattr(state, "energy", 0.0)), 1),
             "fields": len(getattr(state, "fields", []) or []), **last}
    journal(entry)
    await tournament_journal(agent, state)
    files.write_json(state_dir() / "status.json", snapshot(state, last))
    return last


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
# enough for a model with a 4,000-token window.
PROGRAM_ONLY = ("use_case", "requires", "effects")


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
    the two built-in ones; a decider someone installed is theirs to keep."""
    named = str(cfg.get("brain", {}).get("decider") or "tree")
    advise = conf.get("advise")
    if advise is None or named not in ("tree", "advised"):
        return named
    return "tree" if advise == "off" else "advised"


def advisor_now(cfg: dict[str, Any]) -> dict[str, Any]:
    """Who answers and how often it advises, for the panel."""
    import advisor

    conf = advisor.settings(cfg, state_dir())
    name = decider_name(cfg, conf)
    return {
        "picked": advisor.configured(conf),
        "label": advisor.who(conf) if advisor.configured(conf) else "",
        "via": str(conf.get("via") or "http"),
        "advise": advisor.every(conf) if name == "advised" else 0,
        "calls": advisor.calls_today(state_dir()),
        "limit": int(conf.get("max_per_day", 0) or 0),
    }


def advisor_command(cfg: dict[str, Any], what: str, rest: list[str]) -> dict[str, Any]:
    """What the panel's "WHO ANSWERS?" needs: look, try, take, switch, drop."""
    import advisor
    import connections
    import httpx

    home = state_dir()
    conf = advisor.settings(cfg, home)
    if what == "list":
        return {"now": advisor_now(cfg), "found": connections.found(conf, httpx.get)}
    if what == "models" and rest:
        if rest[0].startswith("agent:"):
            return {"ok": True, "models": connections.agent_models(rest[0][6:])}
        return connections.models_at(rest[0], httpx.get, connections.kept_key(home))
    if what == "key":  # from stdin, so it never stands in a process list
        connections.keep_key(home, sys.stdin.readline())
        return {"ok": True}
    if what == "try" and rest:
        choice = pick(rest[0], rest[1] if len(rest) > 1 else "")
        if choice is None:
            return {"ok": False, "why": f"unknown connection '{rest[0]}'"}
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
        often = "off" if rest[0] == "off" else max(1, int(rest[0]))
        kept = {key: value for key, value in connections.chosen(home).items() if key != "every"}
        connections.choose(home, {**kept, "advise": often,
                                  **({} if often == "off" else {"every": often})})
        return {"ok": True, "now": advisor_now(cfg)}
    if what == "forget":
        for name in (connections.CHOICE_FILE, connections.KEY_FILE):
            (home / name).unlink(missing_ok=True)
        return {"ok": True, "now": advisor_now(cfg)}
    usage = "advisor list | models WHERE | key | try ID [MODEL] | advise off|N | forget"
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
    """The decider named in ``[brain] decider``: ``tree``, ``advised``, or an installed one.

    Other deciders register under the SDK's entry-point group ``cosmergon.deciders``.
    ``advised=True`` builds the advisor whatever the config names (for the dry run).
    """
    import advisor

    conf = advisor.settings(cfg, state_dir())
    name = "advised" if advised else decider_name(cfg, conf)
    if name in ("tree", "advised"):
        from cosmergon_decider_tree import TreeDecider

        if name == "tree":
            return TreeDecider()
        return advisor.AdvisedDecider(TreeDecider(), conf, state_dir(), dry=advised)
    from importlib.metadata import entry_points

    installed = {point.name: point for point in entry_points(group="cosmergon.deciders")}
    if name not in installed:
        known = ", ".join(sorted({"tree", "advised", *installed}))
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
    entry = await lessons.learn(home, conf, advisor.facts(state), send, force=force)
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
    if lessons.due(lessons.journal_tail(state_dir())):
        return asyncio.create_task(learn(cfg, state))
    return None


async def run(cfg: dict[str, Any], agent: Any, decider: Any) -> None:
    """A move, a pause, a move: the pause follows the world's tick, not only the clock.

    A lesson runs beside the moves, never in their way: through an agent it can take minutes.
    """
    interval = max(30.0, float(cfg.get("brain", {}).get("interval_s", 60)))
    done_in: int | None = None
    learning: Any = None
    while True:
        last = await tick(agent, decider, done_in)
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
    for name in ("instruct", "ask"):
        # Without text the words come in on stdin: arguments stand in /proc/<pid>/cmdline for
        # every local user (#11550). The argument form stays for cosmergon-dashboard <= 0.27.9.
        sub.add_parser(name).add_argument("text", nargs="*")
    sub.add_parser("coach").add_argument("text", nargs="*")
    who = sub.add_parser("advisor", help="who answers: list, try and take a connection")
    who.add_argument("what", nargs="?", default="list")
    who.add_argument("rest", nargs="*")
    return parser


def _local(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    """What needs no Shikigon of its own: the coach's task, the advisor, the snapshot."""
    if args.command == "coach":
        print(coach_prompt(cfg, " ".join(args.text)))
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
    text = " ".join(args.text) or said_on_stdin()
    if not text:
        why = f"nothing to {args.command}: give the words as an argument or on stdin"
        print(json.dumps({"ok": False, "why": why}))
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
    return COMMANDS[args.command](args, load_config())

if __name__ == "__main__":
    sys.exit(main())
