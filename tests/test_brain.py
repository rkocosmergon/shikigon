"""Tests for Shikigon's brain. No network: the world is stubbed, the classes are real."""

from __future__ import annotations

import io
import json
import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "brain"))

import shikigon_brain as brain

TABLE = {
    "attack": ["attack", "angreifen"],
    "defend": ["careful", "vorsichtig"],
    "trade": ["trade", "handel"],
}


@pytest.fixture(autouse=True)
def _private_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


class FakeResult:
    def __init__(self, success: bool, message: str = "") -> None:
        self.success = success
        self.error_message = message


class FakeAgent:
    def __init__(self, state: Any, success: bool = True) -> None:
        self._state = state
        self._success = success
        self.acted: list[tuple[str, dict[str, Any]]] = []
        self.compass: list[str] = []

    async def refresh_state(self) -> Any:
        return self._state

    async def act(self, action: str, **params: Any) -> FakeResult:
        self.acted.append((action, params))
        return FakeResult(self._success, "" if self._success else "Not enough energy")

    async def set_compass(self, preset: str) -> dict[str, Any]:
        self.compass.append(preset)
        return {"preset": preset}


class FakeDecider:
    def __init__(self, choice: tuple[str, dict[str, Any]]) -> None:
        self.choice = choice

    async def decide(self, state: Any) -> tuple[str, dict[str, Any]]:
        return self.choice


def _state(trend: str = "stable", fields_owned: int = 1, catastrophe: str | None = None) -> Any:
    situation = SimpleNamespace(
        energy_trend=trend, fields_owned=fields_owned, active_catastrophe=catastrophe
    )
    return SimpleNamespace(
        agent_name="Socket-hand",
        persona_type="warrior",
        energy=1234.56,
        fields=[object()],
        ranking=SimpleNamespace(champion_rang=7),
        compass_preset=None,
        tick=42,
        world_briefing=SimpleNamespace(situation=situation),
    )


# --- words → compass -------------------------------------------------------------


def test_words_point_to_a_compass_preset() -> None:
    assert brain.preset_for("Sei vorsichtig heute", TABLE) == "defend"
    assert brain.preset_for("HANDEL mehr", TABLE) == "trade"


def test_unknown_words_set_nothing() -> None:
    assert brain.preset_for("sing me a song", TABLE) is None


async def test_instruction_sets_the_compass_and_nothing_else() -> None:
    agent = FakeAgent(_state())
    answer = await brain.instruct(agent, "be careful", {"defend": ["careful"]})
    assert answer == {"heard": "be careful", "preset": "defend", "ok": True}
    assert agent.compass == ["defend"]
    assert agent.acted == []


async def test_not_understood_does_not_touch_the_compass() -> None:
    agent = FakeAgent(_state())
    answer = await brain.instruct(agent, "dance", TABLE)
    assert answer["ok"] is False and answer["preset"] is None
    assert agent.compass == []


# --- identity --------------------------------------------------------------------


def test_registration_always_names_the_source_and_skips_empty_choices() -> None:
    body = brain.registration_body({"character": {"persona": "", "name": ""}})
    assert body == {"source": "shikigon"}
    body = brain.registration_body({"character": {"persona": "trader", "name": "Gary"}})
    assert body == {"source": "shikigon", "persona": "trader", "agent_name": "Gary"}


def test_register_keeps_the_key_private_and_never_returns_it() -> None:
    sent: dict[str, Any] = {}

    class Resp:
        status_code = 200

        def json(self) -> dict[str, str]:
            return {"agent_id": "a-1", "api_key": "AGENT-a-1:secret", "expires_at": "x"}

    def post(url: str, json: dict[str, Any], timeout: int) -> Resp:
        sent.update(url=url, body=json)
        return Resp()

    shown = brain.register({"world": {"base_url": "https://example.test/"}}, post)
    assert sent["url"] == "https://example.test/api/v1/auth/register/anonymous-agent"
    assert "api_key" not in shown and "secret" not in json.dumps(shown)
    path = brain.credentials_path()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert brain.load_credentials()["api_key"] == "AGENT-a-1:secret"


class Refused:
    """An error answer of the server, with or without a sentence of its own."""

    def __init__(self, status_code: int, body: Any) -> None:
        self.status_code = status_code
        self.body = body

    def json(self) -> Any:
        if self.body is None:
            raise ValueError("no JSON")
        return self.body


@pytest.mark.parametrize(
    ("answer", "said"),
    [
        # the server's own sentence for its limit per address (shortened)
        (Refused(429, {"error": {"code": 429, "message": "Max 5 anonymous agents per IP."}}),
         "Max 5 anonymous agents per IP."),
        (Refused(429, {"error": "Rate limit exceeded: 3 per 1 minute"}),
         "Rate limit exceeded: 3 per 1 minute"),
        (Refused(503, None), "The world refused the registration (503)."),
        (Refused(500, {"detail": "x"}), "The world refused the registration (500)."),
    ],
)
def test_a_world_that_refuses_a_new_agent_is_said_in_its_own_sentence(
    answer: Refused, said: str
) -> None:
    with pytest.raises(SystemExit) as stop:
        brain.register(CFG, lambda url, json, timeout: answer)
    assert str(stop.value) == said
    assert brain.load_credentials() is None


def test_a_world_that_is_away_at_birth_is_said_in_one_sentence() -> None:
    def post(url: str, json: dict[str, Any], timeout: int) -> None:
        raise OSError("unreachable")

    with pytest.raises(SystemExit) as stop:
        brain.register(CFG, post)
    assert "did not answer" in str(stop.value)
    assert brain.load_credentials() is None


def test_a_living_shikigon_is_not_replaced_by_accident() -> None:
    brain.write_private(
        brain.credentials_path(), {"agent_id": "old", "api_key": "k", "expires_at": ""}
    )

    def post(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("must not reach the server")

    with pytest.raises(SystemExit):
        brain.register({"world": {"base_url": "https://example.test"}}, post)
    assert brain.load_credentials()["agent_id"] == "old"


# --- a move ----------------------------------------------------------------------


async def test_a_move_is_acted_written_down_and_shown_without_the_key() -> None:
    agent = FakeAgent(_state(trend="rising"))
    last = await brain.tick(agent, FakeDecider(("place_cells", {"preset": "blinker"})))
    assert last == {"action": "place_cells", "ok": True, "why": "", "words": "place cells"}
    assert agent.acted == [("place_cells", {"preset": "blinker"})]
    status = json.loads((brain.state_dir() / "status.json").read_text())
    assert status["mood"] == "thriving" and status["rank"] == 7 and status["fields"] == 1
    assert status["rank_of"] == 0, "an older SDK knows no pool size (cos20 #504)"
    assert status["public"] is False, "no showcase in the state: not shown publicly"
    assert "api_key" not in json.dumps(status)
    line = (brain.state_dir() / "journal.jsonl").read_text().strip().splitlines()[-1]
    assert json.loads(line)["action"] == "place_cells"


def test_the_bar_knows_whether_it_is_shown_publicly() -> None:
    """cos20 #504: the consent version from the state, read only (the switch is the terminal's)."""
    state = _state()
    state.showcase = SimpleNamespace(consented_version="v2", total_energy_public=True)
    assert brain.snapshot(state, {})["public"] is True
    state.showcase = SimpleNamespace(consented_version="x" * 100_000)
    assert brain.snapshot(state, {})["public"] is True, "a yes, not the server's text"


async def test_waiting_is_a_choice_not_an_action() -> None:
    agent = FakeAgent(_state())
    last = await brain.tick(agent, FakeDecider(("wait", {})))
    assert last["action"] == "wait" and agent.acted == []


async def test_a_refused_move_says_why() -> None:
    agent = FakeAgent(_state(), success=False)
    last = await brain.tick(agent, FakeDecider(("evolve", {})))
    assert last == {"action": "evolve", "ok": False, "why": "Not enough energy",
                    "refused": True, "words": "evolve a field"}  # refused: cos20 #491


async def test_no_state_means_asleep() -> None:
    last = await brain.tick(FakeAgent(None), FakeDecider(("wait", {})))
    assert last["ok"] is False
    status = json.loads((brain.state_dir() / "status.json").read_text())
    assert status["mood"] == "asleep"


@pytest.mark.parametrize(
    ("trend", "fields", "catastrophe", "face"),
    [
        ("rising", 1, None, "thriving"),
        ("stable", 1, None, "content"),
        ("declining", 1, None, "struggling"),
        ("stable", 0, None, "content"),  # no land yet is the normal start, not a struggle
        ("declining", 0, None, "struggling"),
        ("rising", 3, "plague", "struggling"),
    ],
)
def test_face_follows_its_world(
    trend: str, fields: int, catastrophe: str | None, face: str
) -> None:
    assert brain.mood(_state(trend, fields, catastrophe)) == face


# --- the real classes ------------------------------------------------------------


def _wire_state() -> dict[str, Any]:
    return {
        "agent_id": "a-1",
        "agent_name": "Socket-hand",
        "persona_type": "warrior",
        "energy_balance": 5000.0,
        "fields": [],
        "cubes": [],
        "compass_preset": "defend",
        "tick": 9,
        "world_briefing": {"agent_situation": {"energy_trend": "declining", "fields_owned": 0}},
        "available_actions": {},
    }


def test_snapshot_reads_the_real_sdk_state() -> None:
    from cosmergon_agent.state import GameState

    state = GameState.from_api(_wire_state())
    shown = brain.snapshot(state, {"action": "wait", "ok": True, "why": ""})
    assert shown["compass"] == "defend"
    assert shown["mood"] == "struggling"
    assert shown["name"] == "Socket-hand" and shown["tick"] == 9


def test_situation_says_why_it_waits() -> None:
    """Founder 09.10.2026: the panel must say that its marauder is on a mission."""
    state = SimpleNamespace(available_actions={
        "start_mission": {"available": False, "marauder_state": "on_mission"},
        "create_field": {"available": False, "slots_available": False},
    })
    assert brain.situation(state) == ["its marauder is on a mission", brain.FIRST_FIELD]
    owner = SimpleNamespace(fields=[object()], available_actions=state.available_actions)
    assert brain.situation(owner) == ["its marauder is on a mission"], (
        "an agent with a field does not need to hear that the world is full")
    shown = brain.snapshot(state, {"action": "wait", "ok": True, "why": ""})
    assert shown["situation"] == brain.situation(state)
    assert shown["first_field"] is True, "no field in a full world: the bar's first hint says so"
    assert brain.snapshot(owner, {"action": "wait", "ok": True, "why": ""})["first_field"] is False


async def test_a_mission_goes_into_the_journal_by_its_kind() -> None:
    """#451, 14.10.2026: the journal showed start_mission again and again."""
    payload = {"params": {"mission_type": "gather_spores", "params": {}, "reward_energy": 0}}
    last = await brain.tick(FakeAgent(_state()), FakeDecider(("start_mission", payload)))
    assert last["kind"] == "gather_spores"
    line = json.loads((brain.state_dir() / "journal.jsonl").read_text().splitlines()[-1])
    assert line["kind"] == "gather_spores" and line["action"] == "start_mission"
    assert brain.mission_kind("market_list", {}) == ""
    scout = {"mission_type": "scout_terminal"}
    assert brain.mission_kind("start_mission", scout) == "scout_terminal"


def test_situation_names_an_unknown_marauder_state_and_stays_quiet_without_facts() -> None:
    odd = SimpleNamespace(available_actions={"start_mission": {"marauder_state": "frozen"}})
    assert brain.situation(odd) == ["its marauder: frozen"]
    assert brain.situation(SimpleNamespace()) == []
    free = SimpleNamespace(available_actions={"create_field": {"slots_available": True}})
    assert brain.situation(free) == []


async def test_the_real_tree_answers_on_the_real_state() -> None:
    from cosmergon_agent.state import GameState
    from cosmergon_decider_tree import TreeDecider

    action, params = await TreeDecider().decide(GameState.from_api(_wire_state()))
    assert isinstance(action, str) and isinstance(params, dict)


# --- The tournament in the bar (cos20 #475) --------------------------------------------------
# The block as the server sent it for Root-storm in tournament #269 (10.10.2026 17:4xZ).
TOURNAMENT = {
    "tournament_id": "15d46d8f-8e1e-4f88-b6a3-0504c65121d0",
    "number": 269,
    "status": "running",
    "starts_at": "2026-10-10T13:30:00+00:00",
    "ends_at": "2026-10-11T04:30:00+00:00",
    "compass": "attack",
    "my_score": {"energy": 19861.125, "territory": 2, "tier": 2, "vitality": 180.0},
    "leader": {"energy": 60788.25, "territory": 5, "tier": 2, "vitality": 249.0},
    "bodies": [
        {"body_slot": slot, "state": "recovery", "hp": 100, "dead": False, "on_mission": slot < 3}
        for slot in range(4)
    ],
}


def _mission(mid: str, status: str, outcome: str | None, body: str = "tournament") -> dict:
    return {"mission_id": mid, "mission_type": "gather_spores", "status": status,
            "outcome": outcome, "body": body}


class TournamentAgent(FakeAgent):
    """A world with a running tournament; the server's mission list can change between looks."""

    def __init__(self, state: Any, missions: list[dict] | None = None) -> None:
        super().__init__(state)
        self.missions_now: list[dict] | None = missions or []
        self.looks = 0
        self.fail_with: Exception | None = None

    async def missions(self) -> list[dict] | None:
        self.looks += 1
        if self.fail_with is not None:
            raise self.fail_with
        return self.missions_now


def _in_tournament(**changes: Any) -> Any:
    state = _state()
    state.tournament = {**TOURNAMENT, **changes}
    return state


def _journal() -> list[dict]:
    path = brain.state_dir() / "journal.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_the_tournament_block_is_passed_on_as_the_server_sent_it() -> None:
    shown = brain.snapshot(_in_tournament(), {"action": "wait", "ok": True, "why": ""})
    assert shown["tournament"] == TOURNAMENT
    assert brain.snapshot(_state(), {"action": "wait", "ok": True, "why": ""})["tournament"] is None


async def test_no_running_tournament_no_question_to_the_server() -> None:
    for state in (_state(), _in_tournament(status="scheduled")):
        agent = TournamentAgent(state)
        assert await brain.tournament_journal(agent, state) == 0
        assert agent.looks == 0


async def test_first_look_remembers_then_each_end_is_written_once_in_a_players_words() -> None:
    state = _in_tournament()
    old = [_mission("m1", "completed", "collected=1"), _mission("m0", "failed", "no_box_mega_bomb")]
    agent = TournamentAgent(state, old)
    assert await brain.tournament_journal(agent, state) == 0  # joined mid-tournament: no flood
    assert _journal() == []
    agent.missions_now = [
        _mission("m3", "active", None),  # still running: not an end
        _mission("m2", "failed", "capture_cooldown_active"),
        _mission("w1", "failed", "out_of_mega_bombs", body="main"),  # the main world: not here
        *old,
    ]
    assert await brain.tournament_journal(agent, state) == 1
    (entry,) = _journal()
    assert entry["mission"] == "collecting" and entry["ok"] is False
    assert entry["text"] == "someone else took it first; the field is protected for now"
    assert await brain.tournament_journal(agent, state) == 0  # no end twice
    assert len(_journal()) == 1


async def test_a_failed_look_changes_nothing_and_the_move_goes_on() -> None:
    from cosmergon_agent.exceptions import ConnectionError as CsgConnectionError
    from cosmergon_agent.exceptions import RateLimitError

    state = _in_tournament()
    agent = TournamentAgent(state)
    for failure in (RateLimitError(retry_after=5), CsgConnectionError("down")):
        agent.fail_with = failure
        assert await brain.tournament_journal(agent, state) == 0
    agent.fail_with = None
    agent.missions_now = None  # the server could not be read
    assert await brain.tournament_journal(agent, state) == 0
    assert not brain.seen_path().exists()
    agent.fail_with = RateLimitError(retry_after=5)
    last = await brain.tick(agent, FakeDecider(("place_cells", {"preset": "blinker"})))
    assert last["ok"] is True  # the move was made and written
    shown = json.loads((brain.state_dir() / "status.json").read_text(encoding="utf-8"))
    assert shown["tournament"]["number"] == 269


# --- First start: the server's sentence, why it sleeps, and a way out (cos20 #451, V2/V3) ------


def test_the_servers_sentence_and_the_keys_end_are_passed_on_as_they_came() -> None:
    from cosmergon_agent.state import GameState

    step = {"you_are_here": "no field yet", "next": "create your first field", "where": "x"}
    expiry = {"at": "2026-10-12T01:39:06+00:00", "renews_on_use": True}
    state = GameState.from_api({**_wire_state(), "next_step": step, "key_expiry": expiry})
    shown = brain.snapshot(state, {"action": "wait", "ok": True, "why": ""})
    assert shown["next_step"] == step and shown["key_expiry"] == expiry
    silent = brain.snapshot(GameState.from_api(_wire_state()), {})
    assert silent["next_step"] is None and silent["key_expiry"] is None


async def test_each_sleep_says_why_in_a_word_the_bar_can_act_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cosmergon_agent.exceptions import AuthenticationError

    def shown() -> dict[str, Any]:
        return json.loads((brain.state_dir() / "status.json").read_text(encoding="utf-8"))

    await brain.tick(FakeAgent(None), FakeDecider(("wait", {})))
    assert shown()["reason"] == "world_silent" and shown()["mood"] == "asleep"

    class Ended:
        async def __aenter__(self) -> Any:
            raise AuthenticationError("key expired")

        async def __aexit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(brain, "make_agent", lambda cfg: Ended())

    async def never(agent: Any) -> None:
        raise AssertionError("no work with an ended key")

    last = await brain.with_agent({}, never)
    assert shown()["reason"] == "key_ended" and shown()["last"] == last
    assert "register a new Shikigon" in last["why"]


class Gone:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


def _living() -> None:
    brain.write_private(brain.credentials_path(), {"agent_id": "a-1", "api_key": "its-key"})
    brain.journal({"at": 1, "action": "wait", "ok": True})


CFG = {"world": {"base_url": "https://world.test/"}}


def test_forget_deletes_it_on_the_server_with_its_own_key_and_removes_its_files() -> None:
    _living()
    asked: list[tuple[str, dict[str, str]]] = []

    def delete(url: str, headers: dict[str, str], timeout: int) -> Gone:
        asked.append((url, headers))
        return Gone(200)

    result = brain.forget(CFG, delete)
    assert asked == [("https://world.test/api/v1/players/me", {"Authorization": "api-key its-key"})]
    assert result["ok"] and result["server"] == "deleted" and "its-key" not in json.dumps(result)
    assert not brain.state_dir().exists()
    with pytest.raises(SystemExit):
        brain.forget(CFG, delete)  # nobody lives here any more
    assert len(asked) == 1


def test_forget_with_an_ended_key_removes_the_files_and_says_what_stays_in_the_world() -> None:
    _living()
    result = brain.forget(CFG, lambda url, headers, timeout: Gone(401))
    assert result["ok"] and "Vagant" in result["server"]
    assert not brain.state_dir().exists()


@pytest.mark.parametrize("answer", [Gone(500), Gone(429), OSError("unreachable")])
def test_forget_keeps_everything_when_the_server_refuses_or_is_away(answer: Any) -> None:
    _living()

    def delete(url: str, headers: dict[str, str], timeout: int) -> Gone:
        if isinstance(answer, Exception):
            raise answer
        return answer

    result = brain.forget(CFG, delete)
    assert result["ok"] is False and "its-key" not in json.dumps(result)
    assert brain.load_credentials() is not None  # the key is still there: try again later
    assert (brain.state_dir() / "journal.jsonl").exists()


def test_forget_wants_a_clear_yes(capsys: pytest.CaptureFixture[str]) -> None:
    _living()
    assert brain.main(["forget"]) == 1
    assert "--yes" in json.loads(capsys.readouterr().out)["why"]
    assert brain.load_credentials() is not None


def test_every_command_has_its_handler() -> None:
    """The parser and the table of handlers name the same commands, none more, none less."""
    parser = brain._parser()
    sub = next(a for a in parser._actions if a.dest == "command")
    assert set(sub.choices) == set(brain.COMMANDS)


def test_the_words_come_in_on_stdin_not_as_an_argument(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Arguments stand in /proc/<pid>/cmdline for every local user (#11550): the bar sends
    a question or an instruction on stdin, one line."""
    heard: list[str] = []

    async def instruct(agent: Any, text: str, table: dict[str, list[str]]) -> dict[str, Any]:
        heard.append(text)
        return {"ok": True}

    async def with_agent(cfg: dict[str, Any], work: Any) -> Any:
        return await work(None)

    monkeypatch.setattr(brain, "instruct", instruct)
    monkeypatch.setattr(brain, "with_agent", with_agent)
    monkeypatch.setattr(sys, "stdin", io.StringIO("be careful\n"))
    assert brain.main(["instruct"]) == 0
    assert heard == ["be careful"]
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    capsys.readouterr()
    assert brain.main(["instruct"]) == 1
    assert "stdin" in json.loads(capsys.readouterr().out)["why"]
    # 0.1.4: no words as arguments, from anyone (the dashboard sends them on stdin since 0.27.10)
    for command in ("instruct", "ask", "coach"):
        assert brain.main([command, "be", "bold"]) == 1, command
        assert "never as arguments" in json.loads(capsys.readouterr().out)["why"]
    assert heard == ["be careful"]


# --- One move per tick (found on the dev server, 11.10.2026) -----------------------------------
# The world answers a second move in the same tick with 429, and the SDK raises. Until then
# that ended the brain with a traceback: on "move now" after a move of its own, and in `run`
# whenever a tick lasted longer than the interval.


class RefusingAgent(FakeAgent):
    """An agent whose world refuses with ``failure`` (an SDK exception)."""

    def __init__(self, state: Any, failure: Exception) -> None:
        super().__init__(state)
        self.failure = failure

    async def act(self, action: str, **params: Any) -> FakeResult:
        raise self.failure

    async def set_compass(self, preset: str) -> dict[str, Any]:
        raise self.failure


async def test_a_second_move_in_one_tick_is_said_in_words_not_a_crash() -> None:
    from cosmergon_agent.exceptions import ConnectionError as CsgConnectionError
    from cosmergon_agent.exceptions import RateLimitError

    agent = RefusingAgent(_state(), RateLimitError(retry_after=30))
    last = await brain.tick(agent, FakeDecider(("place_cells", {"preset": "blinker"})))
    assert last["action"] == "place_cells" and last["ok"] is False
    assert "one move per tick" in last["why"]
    line = json.loads((brain.state_dir() / "journal.jsonl").read_text().splitlines()[-1])
    assert line["ok"] is False and line["action"] == "place_cells"
    agent.failure = CsgConnectionError("down")
    last = await brain.tick(agent, FakeDecider(("evolve", {})))
    assert last == {"action": "evolve", "ok": False, "why": "the world did not answer",
                    "words": "evolve a field"}


async def test_a_compass_changed_too_often_is_said_in_words_not_a_crash() -> None:
    from cosmergon_agent.exceptions import RateLimitError

    agent = RefusingAgent(_state(), RateLimitError(retry_after=30))
    heard = await brain.instruct(agent, "attack now", TABLE)
    assert heard["ok"] is False and heard["preset"] == "attack" and "too often" in heard["why"]


def test_the_pause_waits_for_the_worlds_next_tick_but_never_less_than_the_interval() -> None:
    def at(next_tick_at: float | None) -> SimpleNamespace:
        return SimpleNamespace(next_tick_at=next_tick_at)

    assert brain.pause(60.0, at(1000.0 + 74.0), now=1000.0) == 74.0 + brain.TICK_BUFFER_S
    assert brain.pause(60.0, at(1000.0 + 20.0), now=1000.0) == 60.0  # the tick comes sooner
    assert brain.pause(60.0, at(990.0), now=1000.0) == 60.0  # an old word: the interval stands
    assert brain.pause(60.0, at(None), now=1000.0) == 60.0
    assert brain.pause(60.0, None, now=1000.0) == 60.0


async def test_the_loop_pauses_by_the_state_of_its_last_move(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time as clock

    slept: list[float] = []

    class Stop(Exception):
        pass

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        if len(slept) == 2:
            raise Stop

    agent = FakeAgent(_state())
    agent.state = SimpleNamespace(next_tick_at=clock.time() + 100.0)
    monkeypatch.setattr(brain.asyncio, "sleep", sleep)
    with pytest.raises(Stop):
        await brain.run({"brain": {"interval_s": 60}}, agent, FakeDecider(("wait", {})))
    assert len(slept) == 2 and all(95.0 < seconds < 103.0 for seconds in slept)


class CountingDecider(FakeDecider):
    def __init__(self, choice: tuple[str, dict[str, Any]]) -> None:
        super().__init__(choice)
        self.asked = 0

    async def decide(self, state: Any) -> tuple[str, dict[str, Any]]:
        self.asked += 1
        return self.choice


async def test_in_a_tick_it_already_chose_in_nothing_is_chosen_asked_or_written() -> None:
    """Seen on the device (11.10.2026): 4 of 34 advised moves fell into a used tick."""
    agent = FakeAgent(_state())  # the world shows tick 42
    decider = CountingDecider(("place_cells", {"preset": "blinker"}))
    last = await brain.tick(agent, decider, done_in=42)
    assert last["skipped"] == "same tick"
    assert decider.asked == 0 and agent.acted == []
    assert not (brain.state_dir() / "journal.jsonl").exists()
    played = await brain.tick(agent, decider, done_in=41)  # a new tick: it plays
    assert played["action"] == "place_cells" and decider.asked == 1 and len(agent.acted) == 1
    # "move now" from the bar passes no tick: it always tries, and the world answers
    await brain.tick(agent, decider)
    assert decider.asked == 2


async def test_the_loop_waits_for_a_late_tick_instead_of_moving_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slept: list[float] = []

    class Stop(Exception):
        pass

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        if len(slept) == 4:
            raise Stop

    class LateAgent(FakeAgent):
        """The world stays in tick 42 for two more looks, then moves on."""

        def __init__(self) -> None:
            super().__init__(_state())
            self.ticks = [42, 42, 42, 43]
            self.state: Any = None

        async def refresh_state(self) -> Any:
            self.state = _state()
            self.state.tick = self.ticks.pop(0)
            return self.state

    agent = LateAgent()
    decider = CountingDecider(("place_cells", {"preset": "blinker"}))
    monkeypatch.setattr(brain.asyncio, "sleep", sleep)
    with pytest.raises(Stop):
        await brain.run({"brain": {"interval_s": 60}}, agent, decider)
    assert slept == [60.0, brain.LATE_TICK_S, brain.LATE_TICK_S, 60.0]
    assert decider.asked == 2 and len(agent.acted) == 2


# --- Your own coding agent as its coach (cos20 #451, the way through Omarchy) -------------------


def test_the_coachs_task_names_the_skill_the_paths_and_the_snapshot_never_the_key() -> None:
    _living()
    status = {"mood": "content", "name": "Root-storm"}
    brain.files.write_json(brain.state_dir() / "status.json", status)
    task = brain.coach_prompt(CFG, "  why does it wait?  ")
    assert brain.SKILL.exists() and str(brain.SKILL) in task
    assert f"State folder: {brain.state_dir()}" in task
    assert "<world> is: https://world.test\n" in task
    assert "shikigon_brain.py" in task and '"name": "Root-storm"' in task
    assert task.endswith("The player asks: why does it wait?")
    assert "its-key" not in task


def test_without_a_question_or_a_snapshot_the_task_still_stands() -> None:
    task = brain.coach_prompt(CFG, "")
    assert '{"mood": "asleep"}' in task
    assert task.endswith("What is my Shikigon doing, and what could I do next?")


def test_the_coach_is_given_the_python_that_has_the_brains_packages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`coach` itself runs with any Python; `instruct` and `tick` need the brain's own."""
    monkeypatch.setenv("HOME", str(tmp_path))
    assert brain.brain_python() == sys.executable  # not set up yet
    own = tmp_path / ".local" / "share" / "shikigon" / "venv" / "bin" / "python"
    own.parent.mkdir(parents=True)
    own.write_text("")
    assert brain.brain_python() == str(own)
    assert f"<brain> is: {own} " in brain.coach_prompt(CFG, "")


def test_the_skill_names_only_commands_the_brain_has_and_keeps_its_three_nevers() -> None:
    import re

    skill = brain.SKILL.read_text(encoding="utf-8")
    assert skill.startswith("---\nname: shikigon-coach\ndescription: ")
    named = set(re.findall(r"`<brain> ([a-z]+)", skill)) | {"forget", "register"}
    assert {"status", "advise", "instruct"} <= named
    # No moves for it (14.10.2026): the bar has no "move now" (founder 11.10.), and a coach's
    # move would only take the one it makes by itself in that tick.
    assert "tick" not in named and "Do not make moves for it" in skill
    for command in sorted(named):
        with pytest.raises(SystemExit) as stop:
            brain.main([command, "--help"])
        assert stop.value.code == 0, f"the skill names `{command}`, the brain does not know it"
    for never in ("Never read, print or copy `credentials.json`", "Never run `forget`",
                  "Never edit files"):
        assert never in skill


def test_running_the_brain_writes_nothing_into_the_plugins_folder(tmp_path: Path) -> None:
    """Omarchy reloads a plugin when a file in its folder changes; that closed the open panel.

    Seen on the device (11.10.2026): the first `ask` wrote `brain/__pycache__`.
    """
    import shutil
    import subprocess

    root = Path(brain.__file__).resolve().parent.parent
    plugin = tmp_path / "plugin"
    shutil.copytree(root / "brain", plugin / "brain", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(root / "config", plugin / "config")
    before = sorted(str(p.relative_to(plugin)) for p in plugin.rglob("*"))
    # Without the switch from outside, so the brain's own line is what is tested. `advise`
    # loads the advisor (the module that was cached) and then stops: no Shikigon lives here.
    env = {k: v for k, v in os.environ.items() if k != "PYTHONDONTWRITEBYTECODE"}
    done = subprocess.run(
        [sys.executable, str(plugin / "brain" / "shikigon_brain.py"), "advise"],
        env=env, capture_output=True, text=True, timeout=60, check=False,
    )
    assert "No Shikigon yet" in done.stderr, done.stderr
    assert sorted(str(p.relative_to(plugin)) for p in plugin.rglob("*")) == before


# --- A move the world keeps refusing is paused (cos20 #491) ---------------------------------------
# Without a pause a Shikigon asked for the same refused offer (listing limit) every minute for
# hours. Three refusals in a row pause the move for 30 moves, as in cosmergon-pet. The answers
# are the SDK's own ActionResult of a real 409.

LIMIT_TEXT = "Listing limit reached: 25 active listings per account"
LIMIT_409 = {"error": {"code": 409, "message": LIMIT_TEXT, "type": "http_error"}}


class WorldSaysNo(FakeAgent):
    """Refuses ``market_list`` the way the server does; everything else goes through."""

    async def act(self, action: str, **params: Any) -> Any:
        from cosmergon_agent.action import ActionResult

        self.acted.append((action, params))
        if action == "market_list":
            return ActionResult.from_response(action, 409, LIMIT_409)
        return ActionResult.from_response(action, 200, {"result": {}})


class BlockAwareDecider:
    """Like the rule tree: takes ``blocked`` and picks the next best move."""

    def __init__(self) -> None:
        self.told: list[frozenset[str]] = []

    async def decide(
        self, state: Any, blocked: frozenset[str] = frozenset()
    ) -> tuple[str, dict[str, Any]]:
        self.told.append(blocked)
        return ("place_cells", {}) if "market_list" in blocked else ("market_list", {})


async def test_three_refusals_pause_the_move_for_thirty_moves() -> None:
    agent, decider, refusals = WorldSaysNo(_state()), BlockAwareDecider(), brain.Refusals()
    said = [await brain.tick(agent, decider, refusals=refusals) for _ in range(3)]
    assert [m["action"] for m in said] == ["market_list"] * 3 and said[0]["refused"] is True
    assert "paused" not in said[1]["why"]
    assert said[2]["why"] == ("Listing limit reached: 25 active listings per account"
                              " — refused 3 times, paused for 30 moves")  # fmt: skip
    line = json.loads((brain.state_dir() / "journal.jsonl").read_text().splitlines()[-1])
    assert "paused for 30 moves" in line["why"] and line["words"] == "sell on the market"
    fourth = await brain.tick(agent, decider, refusals=refusals)
    assert decider.told[-1] == frozenset({"market_list"}) and fourth["action"] == "place_cells"
    for _ in range(brain.PAUSED_MOVES - 2):
        await brain.tick(agent, decider, refusals=refusals)
    assert refusals.paused() == frozenset({"market_list"})
    await brain.tick(agent, decider, refusals=refusals)  # the 30th move after the pause
    assert refusals.paused() == frozenset()
    assert (await brain.tick(agent, decider, refusals=refusals))["action"] == "market_list"


async def test_a_429_or_no_answer_never_pauses_a_move() -> None:
    from cosmergon_agent.exceptions import ConnectionError as CsgConnectionError
    from cosmergon_agent.exceptions import RateLimitError

    refusals = brain.Refusals()
    for failure in (RateLimitError(retry_after=30), CsgConnectionError("down")) * 3:
        agent = RefusingAgent(_state(), failure)
        last = await brain.tick(agent, FakeDecider(("market_list", {})), refusals=refusals)
        assert last["ok"] is False and "refused" not in last
    assert refusals.paused() == frozenset()


async def test_a_move_that_goes_through_starts_the_count_again() -> None:
    refusals = brain.Refusals()
    no, yes = {"action": "evolve", "ok": False, "refused": True}, {"action": "evolve", "ok": True}
    for last in (no, no, yes, no, no):
        assert refusals.note(dict(last)) is False
    assert refusals.paused() == frozenset()
    assert refusals.note(dict(no)) is True and refusals.paused() == frozenset({"evolve"})


async def test_a_decider_that_knows_no_block_list_still_plays_no_paused_move() -> None:
    """A decider of the SDK's protocol has only ``decide(state)``: its paused pick waits."""
    agent, refusals = WorldSaysNo(_state()), brain.Refusals()
    for _ in range(3):
        await brain.tick(agent, FakeDecider(("market_list", {})), refusals=refusals)
    held = await brain.tick(agent, FakeDecider(("market_list", {})), refusals=refusals)
    assert len(agent.acted) == 3 and held["action"] == "wait" and held["ok"] is True
    assert held["why"] == "held back: sell on the market is paused, the world kept refusing it"


async def test_the_advisor_never_proposes_a_paused_move() -> None:
    import advisor

    decider = BlockAwareDecider()
    options = await advisor.proposals(decider, _state(), paused=frozenset({"market_list"}))
    assert options[0][0] == "place_cells"
    assert all("market_list" in told for told in decider.told)


def test_moves_are_said_in_the_sdks_words() -> None:
    """cos20 #486: the bar and the terminal say a move the same way (SDK action_word).

    ``say`` and ``propose_counter`` were missing from Shikigon's own table: the journal showed
    the raw names. Unknown moves still read with spaces.
    """
    from words import action_words, move_words

    assert action_words("say") == "say something"
    assert move_words({"action": "propose_counter"}) == "make a counter-offer"
    assert move_words({"action": "market_list"}) == "sell on the market"
    assert action_words("some_new_move") == "some new move"
