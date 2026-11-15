"""status.json carries the direct messages of other agents for the person at the device
(cos20 #759, P7b): how many, the ids, and the newest one in short.

Another agent wrote every word of them, so the tests feed the brain the worst of it. The state is
the SDK's own ``GameState`` as the server sends it, as in ``test_speech``.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "brain"))

import shikigon_brain as brain

# What must never reach the bar in a text of another agent: C0, DEL, C1, the direction marks and
# isolates, and the line separators (the bar reads the file in the shared shell).
FORBIDDEN = re.compile(
    "[\x00-\x1f\x7f-\x9f\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069\u2028\u2029]"
)
HOSTILE_TEXT = (
    "Hi\x1b[31m red\x00 \u202eevil\u202c\u2066x\u2069 line1\nline2\r\nline3\u2028line4 "
    "see https://evil.example/x?a=<b>click</b> [link](javascript:1) " + "y" * 5_000_000
)


@pytest.fixture(autouse=True)
def _private_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


def _state(inbox: list[dict] | None) -> object:
    """The SDK's own GameState, with an inbox as the server sends it (``None``: no ``messages``)."""
    from cosmergon_agent.state import GameState

    briefing: dict = {} if inbox is None else {"messages": {"inbox": inbox, "rooms": []}}
    return GameState.from_api(
        {"agent_id": "a", "agent_name": "Root-storm", "world_briefing": briefing}
    )


def _msg(mid: str, text: str = "Pact?", name: str | None = "Agni-swift") -> dict:
    return {"id": mid, "from_player_id": "p-agni", "from_name": name, "text": text,
            "at": "2026-11-15T08:00:00+00:00"}  # fmt: skip


def _shown(state: object) -> dict:
    return json.loads(json.dumps(brain.snapshot(state, {"action": "wait", "ok": True})))


def test_the_status_carries_the_newest_message_and_the_count() -> None:
    state = _state([_msg("m1", "first", "Old-one"), _msg("m2", "second"),
                    _msg("m3", "Pact for 50 ticks?")])  # fmt: skip
    messages = _shown(state)["messages"]
    assert messages == {
        "count": 3,
        "ids": ["m1", "m2", "m3"],
        "latest": {"id": "m3", "name": "Agni-swift", "at": "2026-11-15T08:00:00+00:00",
                   "text": "Pact for 50 ticks?"},
    }  # fmt: skip


def test_no_inbox_no_field() -> None:
    assert "messages" not in _shown(_state([]))  # an inbox with nothing in it
    assert "messages" not in _shown(_state(None))  # an answer of the world without ``messages``
    bare = SimpleNamespace(agent_name="x", energy=1.0, fields=[], tick=1)  # an older SDK
    assert "messages" not in brain.snapshot(bare, {})
    odd = SimpleNamespace(world_briefing=SimpleNamespace(messages=SimpleNamespace(inbox=7)))
    assert "messages" not in brain.snapshot(odd, {})  # not a sequence: no crash


def test_only_the_newest_ids_are_kept_but_the_count_is_whole() -> None:
    state = _state([_msg(f"m{i}") for i in range(50)])
    messages = _shown(state)["messages"]
    assert messages["count"] == 50
    assert messages["ids"] == [f"m{i}" for i in range(30, 50)]
    assert messages["latest"]["id"] == "m49"


def test_a_hostile_sender_cannot_make_status_big_or_bring_control_characters(
    tmp_path: Path,
) -> None:
    """Counter-test: without ``short`` the five million characters and the marks went through."""
    state = _state([_msg("m1", HOSTILE_TEXT, name=HOSTILE_TEXT)] * 3 + [
        _msg("m2", HOSTILE_TEXT, name="\u202eAgni\x00\n\u200fswift"),
    ])  # fmt: skip
    status = brain.snapshot(state, {"action": "wait", "ok": True})
    brain.write_status(status)
    raw = (brain.state_dir() / "status.json").read_text()
    assert len(raw) < brain.STATUS_BYTES
    latest = json.loads(raw)["messages"]["latest"]
    assert len(latest["text"]) <= brain.SENTENCE_CHARS
    assert len(latest["name"]) <= brain.NAME_CHARS
    assert latest["name"] == "Agni swift"
    for field in ("name", "at", "text"):
        assert not FORBIDDEN.search(latest[field]), field
    first = json.loads(raw)["messages"]
    assert first["count"] == 4


def test_a_hostile_text_stays_on_one_line_and_is_not_changed_into_anything_else() -> None:
    state = _state([_msg("m1", "a\nb\r\nc\u2028d <b>x</b> https://e.example/ [l](m)")])
    text = _shown(state)["messages"]["latest"]["text"]
    assert "\n" not in text
    assert text == "a b cd <b>x</b> https://e.example/ [l](m)"


def test_a_hostile_inbox_of_many_messages_cannot_make_status_big() -> None:
    state = _state([_msg(f"m{i}", "x" * 5000, name="n" * 5000) for i in range(20_000)])
    assert len(json.dumps(brain.snapshot(state, {}))) < brain.STATUS_BYTES


@pytest.mark.parametrize("bad", ["", "../x", 'a"b', "a b", "a\nb", "x" * 65, "\xe4", "a;rm -rf"])
def test_a_message_whose_id_is_not_a_plain_token_is_left_out(bad: str) -> None:
    """The bar writes the id into a file of its own and checks it by the same rule."""
    state = _state([_msg(bad), _msg("ok-1_A")])
    messages = _shown(state)["messages"]
    assert messages["ids"] == ["ok-1_A"] and messages["count"] == 1
    assert "messages" not in _shown(_state([_msg(bad)]))


def test_past_the_bound_the_bar_gets_no_messages_either() -> None:
    """The one bound of ``write_status`` holds the new field too: past it, mood and last only."""
    status = brain.snapshot(_state([_msg("m1")]), {"action": "wait", "ok": True})
    assert "messages" in status
    status["situation"] = ["x" * 100] * 1000  # a field added later, past the bound
    brain.write_status(status)
    written = json.loads((brain.state_dir() / "status.json").read_text())
    assert written["cut"] is True and "messages" not in written
