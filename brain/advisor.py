"""A model as Shikigon's advisor. Optional: nothing here runs until you name an endpoint.

On a move: the rule tree proposes up to three moves it finds sound, the model picks one
and says why. Only a proposed move is ever played. No answer, an unreadable one or a
number outside the list means the first proposal, which is the move Shikigon would have
made without an advisor.

On a question: the model answers from what the world tells this Shikigon, on your machine.

What is sent, and only to the endpoint you entered in ``[brain.advisor]``: its name,
persona, energy, fields, compass, what the server says about its situation, the proposals
or your question. Never the key.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import time
import tomllib
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import files

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config" / "shikigon.toml"
PROPOSALS = 3  # how many moves the rules put before the model
WHY_WORDS = 12  # the model's reason, at most
MOVE_TOKENS = 80  # room for the small JSON answer
ANSWER_TOKENS = 300  # room for a short answer to a question
ANSWER_PATIENCE = 3  # a question waits this many times ``timeout_s``: a person can wait, a move not

Send = Callable[[list[dict[str, str]], int], Awaitable[str]]

MOVE_SYSTEM = (
    "You advise a player of Cosmergon, an economy game built on Conway's Game of Life. "
    "The game's rules have already picked the moves that are sound right now. "
    "Choose one of them. Answer with JSON only: "
    '{"choice": <number>, "why": "<at most 12 words>"}'
)

ANSWER_SYSTEM = (
    "You are the advisor of a Cosmergon player. Their agent is called a Shikigon; it lives "
    "in the bar of their desktop and plays on its own. Answer the player's question from "
    "the notes below and from what you know about Conway's Game of Life. If they do not "
    "answer it, say that you do not know. Never invent rules, prices or numbers."
)
# Said again next to the question, where a small model still has it in view.
ANSWER_FORM = "(Answer in the language of this question, in at most three short sentences.)"


def settings(cfg: dict[str, Any], home: Path | None = None) -> dict[str, Any]:
    """The advisor's settings: what the player picked in the panel, over the config file,
    over the defaults shipped in ``config/``.

    The shipped file is the one place the default values stand; a config written before
    the advisor existed simply lacks the section and gets them from there. ``home`` is the
    state folder with the panel's choice (``advisor.json``); without it only the files count.
    """
    import connections

    with DEFAULT_CONFIG.open("rb") as handle:
        shipped = tomllib.load(handle).get("brain", {}).get("advisor", {})
    picked = connections.chosen(home) if home is not None else {}
    return {**shipped, **cfg.get("brain", {}).get("advisor", {}), **picked}


def configured(conf: dict[str, Any]) -> bool:
    """True once it is clear whom to ask: an endpoint with a model, or an agent."""
    import connections

    return connections.usable(conf)


def sender(conf: dict[str, Any], home: Path | None = None, patience: int = 1) -> Send:
    """How this advisor is reached: the player's agent, or an address over HTTP."""
    import connections

    if conf.get("via") == "agent":
        # a move waits ``timeout_s`` and then the rules play; a question may take its time
        on_a_move = patience == 1
        wait = float(conf.get("timeout_s", 20)) if on_a_move else connections.AGENT_ANSWER_S
        return connections.agent_send(conf, wait)
    where = str(conf.get("base_url") or "")
    key = connections.kept_key(home, where) if home is not None else ""
    return http_send(conf, patience=patience, key=key)


def who(conf: dict[str, Any]) -> str:
    """The name that stands in front of an answer."""
    import connections

    return connections.label(conf)


def every(conf: dict[str, Any]) -> int:
    """Ask on every n-th move. Through an agent never on every move: its allowance is the
    player's, and a call starts a whole program."""
    import connections

    n = max(1, int(conf.get("every", 1) or 1))
    return max(n, connections.AGENT_MIN_EVERY) if conf.get("via") == "agent" else n


async def proposals(
    tree: Any, state: Any, limit: int = PROPOSALS, paused: frozenset[str] = frozenset()
) -> list[tuple[str, dict]]:
    """Up to ``limit`` different moves, in the rule tree's own order.

    The tree is asked again with its earlier answers blocked; each answer is its best of
    what is left. A wait, or an answer it repeats (a tree before 2.3.7 gave a pending contract
    before the block list), ends the list: there is nothing else it would stand behind.
    ``paused`` are the moves the world kept refusing (cos20 #491): never proposed.
    """
    found: list[tuple[str, dict]] = []
    for _ in range(limit):
        action, params = await tree.decide(
            state, blocked=paused | frozenset(a for a, _ in found)
        )
        if any(action == seen for seen, _ in found) or (action == "wait" and found):
            break
        found.append((action, params))
        if action == "wait":
            break
    return found


def place(ranking: Any) -> tuple[int, int]:
    """Its place in the one ranking and how many are ranked: ``(rank, of)``, ``(0, 0)`` unranked.

    The server sends ``rang``/``rang_von`` from cos20 #504 on (SDK 0.27.13). An older SDK or
    server has only ``champion_rang``; then the place is that, and ``of`` is unknown (0).
    """
    rang = int(getattr(ranking, "rang", 0) or 0)
    if rang:
        return rang, int(getattr(ranking, "rang_von", 0) or 0)
    return int(getattr(ranking, "champion_rang", 0) or 0), 0


def standing(state: Any) -> list[str]:
    """The goal, its place and its own total energy — what it plays for (cos20 #504)."""
    ranking = getattr(state, "ranking", None)
    if ranking is None:
        return []
    lines = []
    goal = str(getattr(ranking, "ziel", "") or "")
    if goal:
        lines.append(f"Goal of the game: {goal}")
    rang, of = place(ranking)
    if rang:
        lines.append(f"Its place in the ranking: #{rang}" + (f" of {of}" if of else ""))
    total = getattr(ranking, "gesamtenergie", None)
    if total is not None:
        lines.append(
            f"Its total energy: {round(float(total))} "
            "(its energy plus what its fields, cubes, rooms and items are worth)"
        )
    return lines


def facts(state: Any) -> str:
    """What the model is told about this Shikigon. Facts of the world, never the key."""
    if state is None:
        return "The world did not answer just now; nothing is known about the agent's state."
    situation = getattr(getattr(state, "world_briefing", None), "situation", None)
    lines = [
        f"Agent: {getattr(state, 'agent_name', '')} ({getattr(state, 'persona_type', '')})",
        f"Energy: {round(float(getattr(state, 'energy', 0.0)), 1)}",
        f"Fields: {len(getattr(state, 'fields', []) or [])}",
        f"Compass: {getattr(state, 'compass_preset', None) or 'autonomous'}",
        *standing(state),
    ]
    if situation is not None:
        lines.append(f"Energy trend: {situation.energy_trend}")
        if situation.active_catastrophe:
            lines.append(f"Catastrophe: {situation.active_catastrophe}")
    step = getattr(state, "next_step", None)
    if step:
        lines.append(f"The server's advice: {step.get('next', '')}")
    offers = open_energy_offers(state)
    if offers:
        lines.append(
            f"Open energy offers on the market: {offers} "
            "(each holds its energy until it is sold or expires)"
        )
    expiry = getattr(state, "key_expiry", None)
    if expiry:
        renews = "; every move it makes extends that" if expiry.get("renews_on_use") else ""
        lines.append(f"Its key is valid {time_left(str(expiry.get('at', '')))}{renews}")
    return "\n".join(lines)


def open_energy_offers(state: Any) -> int:
    """How many of its own energy offers stand unsold; 0 when the server does not say.

    Without this line a model advised selling more energy while 214 offers already held
    almost all of it, and gave as reason that a move extends the key.
    """
    actions = getattr(state, "available_actions", None) or {}
    offered = actions.get("market_list") if isinstance(actions, dict) else None
    if not isinstance(offered, dict):
        return 0
    try:
        return max(int(offered.get("open_energy_listings") or 0), 0)
    except (TypeError, ValueError):
        return 0


def time_left(until: str) -> str:
    """``for another 23 h 40 min``: a span reads the same in every time zone."""
    try:
        end = datetime.datetime.fromisoformat(until)
        minutes = int((end - datetime.datetime.now(datetime.UTC)).total_seconds() // 60)
    except (ValueError, TypeError):  # not a time with a zone: say it as it came
        return f"until {until}"
    if minutes <= 0:
        return "no longer (it has ended)"
    return f"for another {minutes // 60} h {minutes % 60} min"


def move_messages(
    state: Any, options: list[tuple[str, dict]], learned: str = ""
) -> list[dict[str, str]]:
    """The proposals for the model; ``learned`` is its newest lesson (lessons.py), if any."""
    moves = "\n".join(
        f"{number}. {action} {json.dumps(params)[:160]}"
        for number, (action, params) in enumerate(options, start=1)
    )
    lesson = f"\n\nWhat it learned from its own last moves:\n{learned}" if learned else ""
    return [
        {"role": "system", "content": MOVE_SYSTEM},
        {"role": "user", "content": f"{facts(state)}{lesson}\n\nMoves:\n{moves}"},
    ]


def answer_messages(
    question: str, context: str, earlier: list[dict[str, Any]]
) -> list[dict[str, str]]:
    """The question with what the model may answer from; ``earlier`` is the talk so far."""
    messages = [{"role": "system", "content": f"{ANSWER_SYSTEM}\n\n{context}"}]
    for turn in earlier:
        messages.append({"role": "user", "content": str(turn.get("q", ""))})
        messages.append({"role": "assistant", "content": str(turn.get("a", ""))})
    messages.append({"role": "user", "content": f"{question}\n{ANSWER_FORM}"})
    return messages


def parse(text: str, count: int) -> tuple[int, str] | None:
    """The model's choice (1-based) and its reason, or ``None`` if there is no usable one."""
    found = re.search(r"\{.*\}", text or "", re.DOTALL)
    if found is None:
        return None
    try:
        data = json.loads(found.group(0))
    except ValueError:
        return None
    choice = data.get("choice") if isinstance(data, dict) else None
    if isinstance(choice, bool) or not isinstance(choice, int) or not 1 <= choice <= count:
        return None
    why = " ".join(str(data.get("why") or "").split()[:WHY_WORDS])
    return choice, why[:120]


def request_body(
    conf: dict[str, Any], messages: list[dict[str, str]], max_tokens: int
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": conf["model"],
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }
    if conf.get("reasoning_effort"):
        body["reasoning_effort"] = conf["reasoning_effort"]
    return body


def http_send(
    conf: dict[str, Any], transport: Any = None, patience: int = 1, key: str = ""
) -> Send:
    """The call to the endpoint, over the HTTP client the SDK already brings.

    ``transport`` stands in for the network in tests; in use it is ``None``.
    ``patience`` multiplies the time it waits. ``key`` is one the player entered in the
    panel; a key named through ``api_key_env`` comes first.
    """

    async def send(messages: list[dict[str, str]], max_tokens: int) -> str:
        import connections
        import httpx

        headers = {}
        secret = os.environ.get(str(conf.get("api_key_env") or ""), "") or key
        url = str(conf["base_url"]).rstrip("/") + "/chat/completions"
        if secret:
            if not connections.key_may_go_to(url):
                raise connections.AgentError(connections.KEY_ONLY_SAFE)
            headers["Authorization"] = f"Bearer {secret}"
        timeout = float(conf.get("timeout_s", 20)) * patience
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
            resp = await client.post(
                url, json=request_body(conf, messages, max_tokens), headers=headers
            )
            resp.raise_for_status()
            return str(resp.json()["choices"][0]["message"].get("content") or "")

    return send


def call_errors() -> tuple[type[Exception], ...]:
    """What a call can fail with: the network, a wrong address, an odd answer, an agent
    that said no."""
    import connections
    import httpx

    odd_answer = (ValueError, LookupError, TypeError, AttributeError)
    return (httpx.HTTPError, httpx.InvalidURL, OSError, connections.AgentError, *odd_answer)


def _today() -> str:
    return datetime.datetime.now(datetime.UTC).date().isoformat()


def _counts(home: Path) -> dict[str, Any]:
    """The advisor's counters. In a file, because a move can come from a second process
    (a `tick` from a terminal next to the running brain)."""
    path = home / "advisor_count.json"
    if not path.exists():
        return {}
    return dict(json.loads(path.read_text(encoding="utf-8")))


def _keep(home: Path, counts: dict[str, Any]) -> None:
    files.write_json(home / "advisor_count.json", counts)


def calls_today(home: Path) -> int:
    counts = _counts(home)
    return int(counts.get("calls", 0)) if counts.get("day") == _today() else 0


def count_call(home: Path) -> None:
    _keep(home, {**_counts(home), "day": _today(), "calls": calls_today(home) + 1})


def count_move(home: Path) -> int:
    """Count a move the model could be asked on; returns how many there have been."""
    counts = _counts(home)
    counts["moves"] = int(counts.get("moves", 0)) + 1
    _keep(home, counts)
    return int(counts["moves"])


def limit_reached(conf: dict[str, Any], home: Path) -> bool:
    limit = int(conf.get("max_per_day", 0) or 0)
    return limit > 0 and calls_today(home) >= limit


class AdvisedDecider:
    """The rule tree, with a model choosing among the tree's own proposals.

    Satisfies the SDK's ``Decider`` protocol. ``last`` says what the advisor did on the
    latest move (``None`` when it was not consulted); the brain writes it into the journal.
    """

    name = "advised"
    version = "0.1.0"

    def __init__(
        self,
        tree: Any,
        conf: dict[str, Any],
        home: Path,
        send: Send | None = None,
        dry: bool = False,
    ) -> None:
        self.tree = tree
        self.conf = conf
        self.home = home
        self.send = send or sender(conf, home)
        self.dry = dry  # a dry run always asks and is not counted as a move
        self.last: dict[str, Any] | None = None

    def _not_asked(self) -> str | None:
        """Why the model is not asked on this move; ``None`` when it is."""
        if not configured(self.conf):
            return "no endpoint configured"
        if limit_reached(self.conf, self.home):
            return "daily limit reached"
        return None

    async def decide(
        self, state: Any, blocked: frozenset[str] = frozenset()
    ) -> tuple[str, dict[str, Any]]:
        self.last = None
        options = await proposals(self.tree, state, paused=blocked)
        if len(options) < 2 or (not self.dry and count_move(self.home) % every(self.conf)):
            return options[0]
        note: dict[str, Any] = {
            "model": who(self.conf),
            "proposals": [action for action, _ in options],
            "choice": 1,
        }
        self.last = note
        reason = self._not_asked()
        if reason is not None:
            note["fallback"] = reason
            return options[0]
        started = time.monotonic()
        import lessons

        learned = lessons.latest_text(self.home)
        count_call(self.home)
        try:
            text = await self.send(move_messages(state, options, learned), MOVE_TOKENS)
        except call_errors() as exc:  # the endpoint failed: the rules play on
            note["fallback"] = type(exc).__name__
            return options[0]
        note["ms"] = int((time.monotonic() - started) * 1000)
        picked = parse(text, len(options))
        if picked is None:
            note["fallback"] = "no usable answer"
            return options[0]
        note["choice"], note["why"] = picked
        return options[picked[0] - 1]

    async def healthcheck(self) -> bool:
        return True


async def answer(
    conf: dict[str, Any],
    home: Path,
    question: str,
    context: str,
    earlier: list[dict[str, Any]],
    send: Send | None = None,
) -> dict[str, Any]:
    """One answer to one question. A failure is said in words, never raised.

    ``context`` is everything the model may answer from; the caller puts it together.
    """
    turn: dict[str, Any] = {"at": int(time.time()), "q": question, "a": "", "ok": False}
    if not configured(conf):
        turn["why"] = "nobody is picked to answer yet"
        return turn
    turn["model"] = who(conf)
    if limit_reached(conf, home):
        turn["why"] = "daily limit reached"
        return turn
    started = time.monotonic()
    count_call(home)
    send = send or sender(conf, home, patience=ANSWER_PATIENCE)
    try:
        text = await send(answer_messages(question, context, earlier), ANSWER_TOKENS)
    except call_errors() as exc:  # the endpoint did not answer: say so, the game goes on
        import connections

        said = str(exc) if isinstance(exc, connections.AgentError) else type(exc).__name__
        turn["why"] = f"no answer ({said})"[:220]
        return turn
    turn["ms"] = int((time.monotonic() - started) * 1000)
    turn["a"] = text.strip()
    turn["ok"] = bool(turn["a"])
    if not turn["ok"]:
        turn["why"] = "the model answered nothing"
    return turn
