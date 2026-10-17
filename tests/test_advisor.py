"""Tests for the advisor: a model picks among the rule tree's proposals, the rules play.

No network. The world is the real state of a newborn agent (``fixtures/state_newborn.json``,
read from the Cosmergon dev server on 11.10.2026), the tree is the real rule tree, and the
model is a stub that answers what each test says.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "brain"))

import advisor
import shikigon_brain as brain
from test_brain import FakeAgent

CONF = {
    "base_url": "http://model.test/v1",
    "model": "test-model",
    "reasoning_effort": "none",
    "every": 1,
    "timeout_s": 5,
    "max_per_day": 0,
    "api_key_env": "",
}


@pytest.fixture(autouse=True)
def _private_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


def _state(**changes: Any) -> Any:
    from cosmergon_agent.state import GameState

    wire = json.loads(
        (Path(__file__).parent / "fixtures" / "state_newborn.json").read_text(encoding="utf-8")
    )
    return GameState.from_api({**wire, **changes})


def _tree() -> Any:
    from cosmergon_decider_tree import TreeDecider

    return TreeDecider()


def test_the_model_is_told_the_goal_its_place_and_its_total_energy() -> None:
    """cos20 #504: the advisor plays for place 1 by total energy, so it is told so."""
    from types import SimpleNamespace

    ranking = SimpleNamespace(
        ziel="Be #1 in Cosmergon: the agent with the greatest total energy.",
        rang=12,
        rang_von=380,
        champion_rang=40,
        gesamtenergie=4_812_345.6,
    )
    told = advisor.facts(SimpleNamespace(agent_name="Root-storm", persona_type="scientist",
                                         energy=1.0, fields=[], ranking=ranking))
    assert "Goal of the game: Be #1 in Cosmergon" in told
    assert "Its place in the ranking: #12 of 380" in told
    assert "Its total energy: 4812346" in told


def test_an_older_sdk_gives_the_champion_place_and_no_total() -> None:
    from types import SimpleNamespace

    assert advisor.place(SimpleNamespace(champion_rang=7)) == (7, 0)
    assert advisor.place(None) == (0, 0)
    told = advisor.facts(SimpleNamespace(agent_name="a", persona_type="b", energy=1.0, fields=[],
                                         ranking=SimpleNamespace(champion_rang=7)))
    assert "#7" in told and "total energy" not in told


class Model:
    """Stands in for the endpoint: answers ``reply`` or raises it, and counts the calls."""

    def __init__(self, reply: Any) -> None:
        self.reply = reply
        self.asked: list[list[dict[str, str]]] = []

    async def __call__(self, messages: list[dict[str, str]], max_tokens: int) -> str:
        self.asked.append(messages)
        if isinstance(self.reply, Exception):
            raise self.reply
        return str(self.reply)


def _advised(stub: Model, **conf: Any) -> advisor.AdvisedDecider:
    return advisor.AdvisedDecider(_tree(), {**CONF, **conf}, brain.state_dir(), send=stub)


# --- The proposals come from the rules --------------------------------------------------------


async def test_proposals_are_different_moves_and_the_first_is_the_trees_own() -> None:
    state = _state()
    options = await advisor.proposals(_tree(), state)
    names = [action for action, _ in options]
    # A newborn agent: the tree stands behind two moves (a first field, a mission), not three.
    assert 2 <= len(names) <= advisor.PROPOSALS and len(set(names)) == len(names)
    assert "wait" not in names
    assert options[0] == await _tree().decide(state)


async def test_too_little_energy_leaves_one_proposal_and_the_model_is_not_asked() -> None:
    model = Model('{"choice": 2, "why": "x"}')
    decider = _advised(model)
    assert await decider.decide(_state(energy_balance=1.0)) == ("wait", {})
    assert model.asked == [] and decider.last is None


# --- The model picks, the rules keep the last word --------------------------------------------


async def test_the_models_choice_is_played_and_noted_with_its_reason() -> None:
    state = _state()
    options = await advisor.proposals(_tree(), state)
    decider = _advised(Model('{"choice": 2, "why": "A field first, cells need a board."}'))
    assert await decider.decide(state) == options[1]
    assert decider.last is not None
    assert decider.last["model"] == "test-model" and decider.last["choice"] == 2
    assert decider.last["why"] == "A field first, cells need a board."
    assert decider.last["proposals"] == [action for action, _ in options]
    assert "ms" in decider.last and "fallback" not in decider.last


@pytest.mark.parametrize(
    ("reply", "reason"),
    [
        ("", "no usable answer"),  # a thinking model that spent its tokens on thinking
        ("I would grow first.", "no usable answer"),
        ('{"choice": 7, "why": "outside"}', "no usable answer"),
        ('{"choice": "place_cells"}', "no usable answer"),
        (TimeoutError(), "TimeoutError"),
        (ConnectionRefusedError(), "ConnectionRefusedError"),
        (KeyError("choices"), "KeyError"),  # an answer in a shape that is not the protocol's
    ],
)
async def test_without_a_usable_answer_the_first_proposal_is_played(
    reply: Any, reason: str
) -> None:
    state = _state()
    decider = _advised(Model(reply))
    assert await decider.decide(state) == await _tree().decide(state)
    assert decider.last is not None
    assert decider.last["fallback"] == reason and decider.last["choice"] == 1


async def test_the_model_cannot_play_a_move_the_rules_did_not_propose() -> None:
    state = _state()
    proposed = await advisor.proposals(_tree(), state)
    decider = _advised(Model('{"choice": 2, "why": "x", "action": "create_cube", "params": {}}'))
    assert await decider.decide(state) == proposed[1]
    assert proposed[1][0] != "create_cube"


def test_a_reason_is_cut_to_twelve_words() -> None:
    picked = advisor.parse('Sure: {"choice": 1, "why": "' + "word " * 30 + '"}', 3)
    assert picked == (1, " ".join(["word"] * 12))
    assert advisor.parse('{"choice": true}', 3) is None


# --- When the model is not asked --------------------------------------------------------------


async def test_the_rule_tree_alone_never_calls_a_model() -> None:
    decider = brain.make_decider({"brain": {"decider": "tree"}})
    assert type(decider).__name__ == "TreeDecider"
    assert not hasattr(decider, "send")


async def test_advised_without_an_endpoint_plays_the_rules_and_says_so() -> None:
    model = Model('{"choice": 2, "why": "x"}')
    state = _state()
    decider = _advised(model, base_url="", model="")
    assert await decider.decide(state) == await _tree().decide(state)
    assert model.asked == []
    assert decider.last is not None and decider.last["fallback"] == "no endpoint configured"


async def test_every_third_move_asks_on_the_third() -> None:
    model = Model('{"choice": 1, "why": "x"}')
    decider = _advised(model, every=3)
    asked = []
    for _ in range(6):
        await decider.decide(_state())
        asked.append(len(model.asked))
    assert asked == [0, 0, 1, 1, 1, 2]


async def test_every_counts_across_processes() -> None:
    """The bar's "move now" is a second process next to the running brain: one count for both."""
    model = Model('{"choice": 1, "why": "x"}')
    for _ in range(3):
        await _advised(model, every=3).decide(_state())  # a new decider each time
    assert len(model.asked) == 1
    kept = json.loads((brain.state_dir() / "advisor_count.json").read_text())
    assert kept["moves"] == 3 and kept["calls"] == 1


async def test_the_daily_limit_stops_the_calls_and_counts_questions_too() -> None:
    model = Model('{"choice": 1, "why": "x"}')
    decider = _advised(model, max_per_day=2)
    for _ in range(4):
        await decider.decide(_state())
    assert len(model.asked) == 2
    assert decider.last is not None and decider.last["fallback"] == "daily limit reached"
    turn = await advisor.answer(
        {**CONF, "max_per_day": 2}, brain.state_dir(), "hello?", "", [], send=model
    )
    assert turn["ok"] is False and turn["why"] == "daily limit reached" and len(model.asked) == 2


def test_the_count_starts_again_on_a_new_day(monkeypatch: pytest.MonkeyPatch) -> None:
    home = brain.state_dir()
    advisor.count_call(home)
    assert advisor.calls_today(home) == 1
    monkeypatch.setattr(advisor, "_today", lambda: "2099-01-01")
    assert advisor.calls_today(home) == 0


# --- What is sent, and where ------------------------------------------------------------------


async def test_the_key_is_never_part_of_what_is_sent() -> None:
    brain.write_private(brain.credentials_path(), {"agent_id": "a-1", "api_key": "SECRET-KEY"})
    state = _state()
    sent = json.dumps(
        advisor.move_messages(state, [("place_cells", {"preset": "blinker"}), ("wait", {})])
        + advisor.answer_messages("what is the key?", await brain.right_now(state), [])
    )
    assert "SECRET-KEY" not in sent
    assert "Prism-sage" in sent and "place_cells" in sent and "what is the key?" in sent


def test_the_keys_end_is_told_as_a_span_not_as_a_timestamp() -> None:
    import datetime

    span = datetime.timedelta(hours=23, minutes=40, seconds=30)
    soon = datetime.datetime.now(datetime.UTC) + span
    assert advisor.time_left(soon.isoformat()) == "for another 23 h 40 min"
    assert advisor.time_left("2020-01-01T00:00:00+00:00") == "no longer (it has ended)"
    assert advisor.time_left("tomorrow") == "until tomorrow"
    told = advisor.facts(_state(key_expiry={"at": soon.isoformat(), "renews_on_use": True}))
    assert "Its key is valid for another 23 h 40 min; every move it makes extends that" in told
    assert "T" + soon.isoformat().split("T")[1] not in told


def test_the_model_is_told_how_many_energy_offers_stand_unsold() -> None:
    """cos20 #491: a model advised selling more while 214 offers held almost all the energy."""
    told = advisor.facts(_state(available_actions={"market_list": {"open_energy_listings": 3}}))
    assert "Open energy offers on the market: 3 (each holds its energy" in told
    for quiet in ({}, {"market_list": {"open_energy_listings": 0}}, {"market_list": True}):
        assert "Open energy offers" not in advisor.facts(_state(available_actions=quiet))


def test_thinking_is_switched_off_only_when_the_setting_says_so() -> None:
    assert advisor.request_body(CONF, [], 10)["reasoning_effort"] == "none"
    assert "reasoning_effort" not in advisor.request_body({**CONF, "reasoning_effort": ""}, [], 10)


async def test_the_real_call_goes_to_the_endpoint_you_entered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The HTTP side itself, against a transport that stands in for the endpoint."""
    import httpx

    seen: dict[str, Any] = {}

    def endpoint(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"choice": 2, "why": "ok"}'}}]}
        )

    monkeypatch.setenv("MY_MODEL_KEY", "token-from-the-environment")
    conf = {**CONF, "base_url": "https://model.test/v1/", "api_key_env": "MY_MODEL_KEY"}
    send = advisor.http_send(conf, transport=httpx.MockTransport(endpoint))
    assert await send([{"role": "user", "content": "hi"}], 40) == '{"choice": 2, "why": "ok"}'
    assert seen["url"] == "https://model.test/v1/chat/completions"
    assert seen["auth"] == "Bearer token-from-the-environment"
    assert seen["body"]["model"] == "test-model" and seen["body"]["max_tokens"] == 40


async def test_a_key_never_goes_over_plain_http_to_the_open_internet() -> None:
    import connections
    import httpx

    asked: list[str] = []
    answer = {"choices": [{"message": {"content": "ok"}}]}
    transport = httpx.MockTransport(
        lambda request: asked.append(str(request.url)) or httpx.Response(200, json=answer))
    plain = advisor.http_send({**CONF, "base_url": "http://model.test/v1"}, transport=transport,
                              key="sk-paid")
    with pytest.raises(connections.AgentError, match="only over https"):
        await plain([{"role": "user", "content": "hi"}], 10)
    assert asked == [], "nothing left the machine"
    home = advisor.http_send({**CONF, "base_url": "http://10.20.30.40:11434/v1"},
                             transport=transport, key="sk-paid")
    assert await home([{"role": "user", "content": "hi"}], 10) == "ok"
    without = advisor.http_send({**CONF, "base_url": "http://model.test/v1"}, transport=transport)
    assert await without([{"role": "user", "content": "hi"}], 10) == "ok"  # no key, no rule


async def test_an_endpoint_that_fails_is_a_fallback_not_a_crash() -> None:
    import httpx

    down = httpx.MockTransport(lambda request: httpx.Response(503))
    state = _state()
    decider = advisor.AdvisedDecider(
        _tree(), CONF, brain.state_dir(), send=advisor.http_send(CONF, transport=down)
    )
    assert await decider.decide(state) == await _tree().decide(state)
    assert decider.last is not None and decider.last["fallback"] == "HTTPStatusError"


# --- In the brain: journal, config, dry run ---------------------------------------------------


async def test_the_journal_carries_model_choice_and_reason() -> None:
    agent = FakeAgent(_state())
    decider = _advised(Model('{"choice": 2, "why": "A field first."}'))
    last = await brain.tick(agent, decider)
    line = json.loads((brain.state_dir() / "journal.jsonl").read_text().splitlines()[-1])
    assert line["advice"]["model"] == "test-model" and line["advice"]["why"] == "A field first."
    assert line["action"] == line["advice"]["proposals"][1] == agent.acted[0][0]
    shown = json.loads((brain.state_dir() / "status.json").read_text())
    assert shown["last"]["advice"] == last["advice"]


async def test_a_move_of_the_rule_tree_alone_has_no_advice_in_the_journal() -> None:
    await brain.tick(FakeAgent(_state()), _tree())
    line = json.loads((brain.state_dir() / "journal.jsonl").read_text().splitlines()[-1])
    assert "advice" not in line


async def test_the_dry_run_shows_the_pick_and_plays_nothing() -> None:
    agent = FakeAgent(_state())
    shown = await brain.advise(agent, _advised(Model('{"choice": 2, "why": "Try it."}')))
    assert agent.acted == [] and shown["played"] is False
    assert shown["chosen"] == shown["advice"]["proposals"][1]
    assert not (brain.state_dir() / "journal.jsonl").exists()


def test_settings_come_from_the_shipped_file_and_the_users_win() -> None:
    shipped = advisor.settings({})
    assert shipped["base_url"] == "" and shipped["model"] == ""  # off as shipped
    assert not advisor.configured(shipped)
    assert shipped["reasoning_effort"] == "none" and shipped["every"] == 1
    mine = advisor.settings({"brain": {"advisor": {"model": "mine", "every": 4}}})
    assert mine["model"] == "mine" and mine["every"] == 4
    assert mine["timeout_s"] == shipped["timeout_s"]


def test_the_shipped_config_plays_the_rule_tree() -> None:
    cfg = brain.load_config()
    assert cfg["brain"]["decider"] == "tree"
    assert type(brain.make_decider(cfg)).__name__ == "TreeDecider"
    assert type(brain.make_decider({"brain": {"decider": "advised"}})).__name__ == "AdvisedDecider"


def test_the_dry_run_builds_the_advisor_whatever_the_config_names() -> None:
    cfg = {"brain": {"decider": "tree", "advisor": {"every": 5}}}
    decider = brain.make_decider(cfg, advised=True)
    assert type(decider).__name__ == "AdvisedDecider" and decider.dry


async def test_a_dry_run_always_asks_and_does_not_shift_the_count() -> None:
    """Seen on the device: three dry runs left ``moves: 3`` behind, as if it had played."""
    model = Model('{"choice": 1, "why": "x"}')
    await _advised(model, every=3).decide(_state())
    await _advised(model, every=3).decide(_state())
    dry = _advised(model, every=3)
    dry.dry = True
    await dry.decide(_state())
    assert len(model.asked) == 1 and dry.last is not None
    kept = json.loads((brain.state_dir() / "advisor_count.json").read_text())
    assert kept["moves"] == 2 and kept["calls"] == 1
    await _advised(model, every=3).decide(_state())  # the third real move is the one that asks
    assert len(model.asked) == 2


def test_an_unknown_decider_sleeps_and_names_the_known_ones(
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = brain.config_path()
    path.parent.mkdir(parents=True)
    path.write_text('[world]\nbase_url = "http://world.test"\n[brain]\ndecider = "oracle"\n')
    assert brain.main(["tick"]) == 1
    shown = json.loads((brain.state_dir() / "status.json").read_text())
    assert shown["mood"] == "asleep"
    assert "oracle" in shown["last"]["why"]
    assert "advised" in shown["last"]["why"] and "tree" in shown["last"]["why"]
    assert json.loads(capsys.readouterr().out)["ok"] is False


# --- A question to the advisor ----------------------------------------------------------------

CFG = {"world": {"base_url": "http://world.test"}, "brain": {"advisor": CONF}}


async def test_a_question_is_answered_and_kept_with_the_models_name() -> None:
    model = Model("  A blinker is three cells in a row that flip every tick.  ")
    agent = FakeAgent(_state())
    turn = await brain.ask(agent, CFG, "What is a blinker?", "RULES-OF-THE-WORLD", model)
    assert turn["ok"] and turn["model"] == "test-model"
    assert turn["a"] == "A blinker is three cells in a row that flip every tick."
    kept = [json.loads(line) for line in brain.chat_path().read_text().splitlines()]
    assert kept == [turn]
    system = model.asked[0][0]["content"]
    assert "RULES-OF-THE-WORLD" in system and "Blinker:" in system and "Prism-sage" in system
    assert "Its rules would play next: create a field or mission: gather spores" in system
    assert model.asked[0][-1]["role"] == "user"
    assert model.asked[0][-1]["content"] == "What is a blinker?\n" + advisor.ANSWER_FORM


async def test_the_advisor_is_told_the_last_moves_also_a_refused_one() -> None:
    state = _state()
    await brain.tick(FakeAgent(state), _tree())
    await brain.tick(FakeAgent(state, success=False), _tree())
    model = Model("an answer")
    await brain.ask(FakeAgent(state), CFG, "What happened?", "", model)
    from words import move_words

    action, params = await _tree().decide(state)
    first = move_words({"action": action, "kind": brain.mission_kind(action, params)})
    told = f"Its last moves: {first}, {first} (refused: Not enough energy)"
    assert told in model.asked[0][0]["content"]


async def test_a_follow_up_question_carries_the_last_two_exchanges() -> None:
    model = Model("an answer")
    agent = FakeAgent(_state())
    for question in ("one?", "two?", "three?", "and now?"):
        await brain.ask(agent, CFG, question, "", model)
    asked = [m["content"].split("\n")[0] for m in model.asked[-1] if m["role"] == "user"]
    assert asked == ["two?", "three?", "and now?"]


async def test_without_a_model_the_question_says_what_is_missing_and_calls_nothing() -> None:
    model = Model("never")
    cfg = {"world": CFG["world"], "brain": {}}
    turn = await brain.ask(FakeAgent(_state()), cfg, "What now?", "", model)
    # The panel then shows "WHO ANSWERS?"; from a terminal: `advisor list`, `advisor try`.
    assert turn["ok"] is False and turn["why"] == "nobody is picked to answer yet"
    assert model.asked == []


async def test_a_silent_or_failing_model_is_said_in_words() -> None:
    agent = FakeAgent(_state())
    silent = await brain.ask(agent, CFG, "What now?", "", Model(""))
    assert silent["ok"] is False and silent["why"] == "the model answered nothing"
    failed = await brain.ask(agent, CFG, "What now?", "", Model(TimeoutError()))
    assert failed["ok"] is False and "TimeoutError" in failed["why"]
    assert brain.chat_tail(5) == []  # failures are kept in the file, not fed back to the model


def test_the_rules_come_from_the_server_and_an_unreachable_one_is_no_crash() -> None:
    class Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {
                "actions": {
                    "place_cells": {"description": "Place cells", "use_case": "U1"},
                    "say": {"use_case": "U12", "requires": [], "effects": ["message_sent"]},
                },
                "products": {"x": 1},
                "economy": {"e": 1},
            }

    asked: list[str] = []

    def get(url: str, timeout: int) -> Resp:
        asked.append(url)
        return Resp()

    def down(url: str, timeout: int) -> Resp:
        raise OSError("unreachable")

    rules = json.loads(brain.world_rules(CFG, get))
    assert asked == ["http://world.test/api/v1/game/info"]
    # What a player asks about: not the shop, and per action not what only a program needs.
    assert rules == {
        "actions": {"place_cells": {"description": "Place cells"}},
        "other_actions": ["say"],
        "economy": {"e": 1},
    }
    assert brain.world_rules(CFG, down) == ""


async def test_a_paused_move_is_never_among_the_advisors_proposals() -> None:
    """cos20 #491: a move the world kept refusing reaches neither the model nor the board."""
    state = _state()
    first = (await advisor.proposals(_tree(), state))[0][0]
    decider = _advised(Model('{"choice": 1, "why": "x"}'))
    action, _ = await decider.decide(state, blocked=frozenset({first}))
    assert action != first
    options = await advisor.proposals(_tree(), state, paused=frozenset({first}))
    assert first not in [a for a, _ in options]
