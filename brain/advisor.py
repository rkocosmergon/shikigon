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

# What a string of the world may fill in a prompt (cos20 #492, fourth review, K35/K6). The SDK
# reads up to 8 MiB an answer; a configured world put that much into every question on the
# player's model allowance. A fact is a line, a name a name.
FACT_CHARS = 300
QUOTE = ("<<<", ">>>")  # around text another player wrote; the system prompts name these marks

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
    own = cfg.get("brain", {}).get("advisor", {})
    # The key named by ``api_key_env`` belongs to the address of the same section, whatever the
    # panel picks later (``connections.secret_for``, H3; cos20 #492 B1).
    bound = connections.origin(str(own.get("base_url") or ""))
    return {**shipped, **own, **picked, "api_key_env_for": bound}


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
    return http_send(conf, patience=patience, home=home)


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


def cut(value: Any, chars: int) -> str:
    """``value`` as text of at most ``chars`` characters; a cut ends in "…". Not text: as JSON."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= chars else text[: chars - 1] + "…"


def quoted(value: Any, chars: int) -> str:
    """Another player's text for a prompt: between ``QUOTE``, cut to ``chars``, the marks out.

    The one way such a text enters a prompt. ``<`` and ``>`` in the text become ``‹`` and ``›``:
    a message or contract terms with ``>>>`` in them ended the quote, and what followed reached
    the model as the brain's own words (cos20 #492, fourth review). Names of other players go
    through here too; outside the quote a name could carry a sentence of its own.
    """
    inner = cut(value, chars).replace("<", "‹").replace(">", "›")
    return f"{QUOTE[0]}{inner}{QUOTE[1]}"


MOVE_WORD_CHARS, WHY_QUOTED = 40, 120
# A move's word as the rules and the world's offers name it (``start_mission``, ``gather_spores``).
# The world names the moves it offers and its mission types, so a name of another form stays out.
MOVE_WORD = re.compile(rf"[a-z][a-z0-9_]{{0,{MOVE_WORD_CHARS - 1}}}")


def move_for_model(entry: dict[str, Any]) -> dict[str, Any]:
    """One move of the journal as a model may read it: the one way from the journal into a prompt.

    Its tick, the move, whether the world took it, the numbers of its energy, fields and what it
    booked, and the world's reason for a refusal, quoted. The look back's moves, its numbers
    (``lessons.summary``) and the last moves in a prompt all come through here. Never
    ``words`` or ``advice``: they name the agent it wrote to and carry what a model wrote to it.
    Move words only in their form (``MOVE_WORD``): the world names the moves it offers and its
    mission types. Until 0.1.13 the head's look back read each journal line raw
    (``review/moves.txt``), another player's name outside the quote, and the lesson it wrote went
    into every later move (cos20 #492, sixth review, finding 4)."""
    shown: dict[str, Any] = {}
    if type(entry.get("tick")) is int:
        shown["tick"] = entry["tick"]
    for key in ("action", "kind"):  # names the world offers: only in the form of a move's word
        if isinstance(entry.get(key), str) and MOVE_WORD.fullmatch(entry[key]):
            shown[key] = entry[key]
    shown["ok"] = entry.get("ok") is True
    if type(entry.get("fields")) is int:
        shown["fields"] = entry["fields"]
    for key in ("energy", "total"):
        number = files.finite(entry.get(key))
        if number is not None:
            shown[key] = number
    effect = entry.get("effect")
    if isinstance(effect, dict):
        shown["effect"] = {k: files.finite(effect.get(k) or 0) or 0.0
                           for k in ("free", "held", "items")}  # fmt: skip
    if not shown["ok"] and entry.get("why"):
        shown["why"] = quoted(entry["why"], WHY_QUOTED)
    return shown


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
    goal = cut(getattr(ranking, "ziel", "") or "", FACT_CHARS)
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
    name = cut(getattr(state, "agent_name", "") or "", 64)
    persona = cut(getattr(state, "persona_type", "") or "", 64)
    lines = [
        f"Agent: {name} ({persona})",
        f"Energy: {round(float(getattr(state, 'energy', 0.0)), 1)}",
        f"Fields: {len(getattr(state, 'fields', []) or [])}",
        f"Compass: {cut(getattr(state, 'compass_preset', None) or 'autonomous', 64)}",
        *standing(state),
    ]
    if situation is not None:
        lines.append(f"Energy trend: {cut(situation.energy_trend, 64)}")
        if situation.active_catastrophe:
            lines.append(f"Catastrophe: {cut(situation.active_catastrophe, FACT_CHARS)}")
    step = getattr(state, "next_step", None)
    if step:
        lines.append(f"The server's advice: {cut(step.get('next', ''), FACT_CHARS)}")
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
    count = files.finite(facts_of(state, "market_list").get("open_energy_listings") or 0)
    return max(int(count), 0) if count is not None else 0


def facts_of(state: Any, action: str) -> dict[str, Any]:
    """What the world says about one of its moves (``available_actions[action]``), always an
    object: a word or a list in its place is no facts (cos20 #492, sixth review, finding 3)."""
    actions = getattr(state, "available_actions", None)
    facts = actions.get(action) if isinstance(actions, dict) else None
    return facts if isinstance(facts, dict) else {}


def time_left(until: str) -> str:
    """``for another 23 h 40 min``: a span reads the same in every time zone."""
    try:
        end = datetime.datetime.fromisoformat(until)
        minutes = int((end - datetime.datetime.now(datetime.UTC)).total_seconds() // 60)
    except (ValueError, TypeError):  # not a time with a zone: say it as it came, cut like a fact
        return f"until {cut(until, 64)}"  # uncut until 0.1.13: up to 8 MiB of the world's (P5)
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
    try:
        data = files.json_in(text or "")
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
    conf: dict[str, Any], transport: Any = None, patience: int = 1, home: Path | None = None
) -> Send:
    """The call to the endpoint, over the HTTP client the SDK already brings.

    ``transport`` stands in for the network in tests; in use it is ``None``.
    ``patience`` multiplies the time it waits, and that time holds the whole exchange, from the
    request to the last byte of the answer (``connections.exchange``): a move waits that long
    and then the rules play. ``home`` is the state folder with a key the player entered in the
    panel. Which key goes is read on every call (``connections.secret_for``); whether it may go
    there, and whose the port is, the door checks where it sends (H3, H9).
    """

    async def send(messages: list[dict[str, str]], max_tokens: int) -> str:
        import connections

        url = str(conf["base_url"]).rstrip("/") + "/chat/completions"
        secret = connections.secret_for(home, conf, url)
        headers = {"Authorization": f"Bearer {secret}"} if secret else {}
        timeout = float(conf.get("timeout_s", 20)) * patience
        answer = await connections.exchange(
            "POST", url, headers=headers, payload=request_body(conf, messages, max_tokens),
            timeout=timeout, seconds=timeout, transport=transport,
        )
        answer.raise_for_status()
        return str(answer.json()["choices"][0]["message"].get("content") or "")

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
    (a `tick` from a terminal next to the running brain).

    A file it cannot read counts as none, like every other file the brain keeps; the next count
    writes it anew. Until 0.1.14 an empty or broken one stopped every move of the head and every
    question until it was deleted by hand (cos20 #612, F3)."""
    try:
        data = files.loads((home / "advisor_count.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _number(counts: dict[str, Any], key: str) -> int:
    return int(files.finite(counts.get(key)) or 0)


def _keep(home: Path, counts: dict[str, Any]) -> None:
    files.write_json(home / "advisor_count.json", counts)


def calls_today(home: Path) -> int:
    counts = _counts(home)
    return _number(counts, "calls") if counts.get("day") == _today() else 0


def count_call(home: Path) -> None:
    _keep(home, {**_counts(home), "day": _today(), "calls": calls_today(home) + 1})


def count_move(home: Path) -> int:
    """Count a move the model could be asked on; returns how many there have been."""
    counts = _counts(home)
    counts["moves"] = _number(counts, "moves") + 1
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
            import connections

            note["fallback"] = connections.failed_why(exc)
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

        own_words = (connections.AgentError, connections.NotOurs)
        said = str(exc) if isinstance(exc, own_words) else type(exc).__name__
        if connections.untrusted(exc):  # what the player can mend, in words (cos20 #599)
            said = connections.UNTRUSTED
        turn["why"] = f"no answer ({said})"[:220]
        return turn
    turn["ms"] = int((time.monotonic() - started) * 1000)
    turn["a"] = text.strip()
    turn["ok"] = bool(turn["a"])
    if not turn["ok"]:
        turn["why"] = "the model answered nothing"
    return turn
