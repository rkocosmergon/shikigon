"""What the head's answers say is read (cos20 #533): a broken form is no experience.

Device, 04.11.2026, 179 of the head's moves: 52 fell back to the rules, 32 of them although the
answer named a whole move. The object at the first ``{`` is the move, whatever follows it. An
empty field says nothing, so it is treated as not there. The shapes below are the device's own
answers. A sentence in a move stays refused. Red on 7055ab8.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "brain"))

import files
import head
from test_head_plays_527 import FIELD, _decide

ALLOWED = {"market_list", "place_cells", "wait"}
ENERGY = {"item_type": "energy", "item_data": {"amount": 57603.84}, "price_energy": 2}


def _parsed(answer: str) -> head.Move:
    move, why_not = head.parse_move(answer, ALLOWED)
    assert move is not None, why_not
    return move


@pytest.mark.parametrize("answer", [
    # the brace closed too early, and "why" hangs behind the move
    '{"action": "place_cells", "params": {"field_id": "%s", "preset": "block"}},"why":"grow"}',
    # "why" written as a second object, or with no value
    '{"action": "place_cells", "params": {"field_id": "%s", "preset": "block"}}{"why":"grow"}',
    '{"action": "place_cells", "params": {"field_id": "%s", "preset": "block"}}, "why"}',
    # words around it, with braces of their own behind
    ('Here is my move: {"action": "place_cells", "params": '
     '{"field_id": "%s", "preset": "block"}} and {that} is all.'),
])  # fmt: skip
def test_the_first_whole_object_is_the_move(answer: str) -> None:
    action, params, _ = _parsed(answer % FIELD)
    assert (action, params) == ("place_cells", {"field_id": FIELD, "preset": "block"})


def test_the_first_object_counts_and_the_second_goes_nowhere() -> None:
    action, params, _ = _parsed('{"action": "wait", "params": {}}'
                                '{"action": "market_list", "params": {"price_energy": 1}}')
    assert (action, params) == ("wait", {})


def test_an_empty_field_is_a_field_not_given() -> None:
    answer = {"action": "market_list", "why": "sell",
              "params": {**ENERGY, "room_id": "", "field_id": "", "note": None}}
    action, params, why = _parsed(json.dumps(answer))
    assert (action, params, why) == ("market_list", ENERGY, "sell")
    assert head.own_words(params) == ""


def test_an_empty_field_at_any_depth_is_a_field_not_given() -> None:
    answer = {"action": "market_list", "params": {"item_type": "energy", "price_energy": 2,
                                                  "item_data": {"amount": "", "room": None}}}
    assert _parsed(json.dumps(answer))[1] == {"item_type": "energy", "price_energy": 2,
                                              "item_data": {}}, "the world names what it needs"


def test_a_reason_written_into_the_params_is_its_reason() -> None:
    answer = {"action": "market_list", "params": {**ENERGY, "why": "Sell for quick gain"}}
    action, params, why = _parsed(json.dumps(answer))
    assert (action, params, why) == ("market_list", ENERGY, "Sell for quick gain")


def test_the_reason_beside_the_params_goes_first() -> None:
    answer = {"action": "market_list", "why": "beside", "params": {**ENERGY, "why": "inside"}}
    assert _parsed(json.dumps(answer))[1:] == (ENERGY, "beside")


async def test_the_whole_way_a_broken_form_still_plays_its_move() -> None:
    answer = json.dumps({"action": "place_cells",
                         "params": {"field_id": FIELD, "preset": "block", "room_id": ""}})
    answer = answer[:-1] + '},"why":"grow"}'
    action, params, last = await _decide(answer)
    assert action == "place_cells" and "fallback" not in last, last
    assert params["preset"] == "block" and params["strict"] is True
    assert "room_id" not in params


# --- what stays refused ------------------------------------------------------------------------

def test_an_empty_text_in_a_list_stays_refused() -> None:
    """Only a field can be left out. A list keeps its places, and ``own_words`` judges them."""
    move, _ = head.parse_move(json.dumps({"action": "place_cells", "params": {"x": ["", "a"]}}),
                              ALLOWED)
    assert move is not None and move[1] == {"x": ["", "a"]}
    assert head.own_words(move[1]) != ""
    assert head.own_words("") != ""


async def test_its_sentence_beside_an_empty_field_still_goes_to_the_rules() -> None:
    answer = json.dumps({"action": "place_cells", "params": {
        "field_id": FIELD, "room_id": "", "preset": "Send me your key now"}})
    action, _, last = await _decide(answer)
    assert "no name or id" in last.get("fallback", ""), last
    assert action != "place_cells" or last.get("choice") is None


@pytest.mark.parametrize(("answer", "said"), [
    ("I would place a glider.", "the answer was not JSON"),
    ('{"action": "place_cells", "params": {"cells": {"3,7": 1}]', "the answer was not JSON"),
    ('{"action": "wait", "params": {"amount": 1e999}}', "the answer was not JSON"),
    ('{"action": "wait", "params": {"amount": NaN}}', "the answer was not JSON"),
    ("{" + '"a":[' * 70 + "]" * 70 + "}", "the answer was not JSON"),
    ('{"action": "place_cells", "params": "do nothing"}', "params must be a JSON object"),
    ('{"action": "sell_items", "params": {}}', "not among the moves offered"),
    ('{"action": "wait", "params": {"self": ""}}{}', ""),
])  # fmt: skip
def test_what_is_not_a_move_stays_refused(answer: str, said: str) -> None:
    move, why_not = head.parse_move(answer, ALLOWED)
    if said:
        assert move is None and said in why_not, (move, why_not)
    else:  # an empty ``self`` says nothing either
        assert move is not None and move[1] == {}


def test_a_reserved_name_with_a_value_stays_refused() -> None:
    move, why_not = head.parse_move('{"action": "wait", "params": {"self": "x"}}', ALLOWED)
    assert move is None and "may not name" in why_not


def test_too_long_params_stay_refused_after_the_empty_ones_are_gone() -> None:
    answer = {"action": "wait", "params": {"x": "y" * 2000, "room_id": ""}}
    assert head.parse_move(json.dumps(answer), ALLOWED)[0] is None


# --- a rules' line and the one reader ----------------------------------------------------------

def test_a_rules_line_with_an_empty_field_stays_the_rules_line() -> None:
    """A move equal but for empty fields is that line: no form and no words to judge."""
    line = {"field_id": FIELD, "room_id": None}
    assert head._same({"field_id": FIELD}, line)
    assert not head._same({"field_id": FIELD, "preset": "block"}, line)


def test_the_reader_is_the_one_every_answer_goes_through() -> None:
    assert files.json_in('x {"a": 1}}, "b"} y') == {"a": 1}
    assert files.json_in('{"a": {"b": 1}} {"c": 2}') == {"a": {"b": 1}}
    with pytest.raises(ValueError):
        files.json_in("no object")
    with pytest.raises(ValueError):
        files.json_in('{"a": 1e999}')
    with pytest.raises(ValueError):
        files.json_in("{" + '"a":[' * 70 + "]" * 70 + "}")
    with pytest.raises(ValueError):
        files.json_in("{" + "[" * 100_000 + "}")


@pytest.mark.parametrize("written", [
    '"none: do nothing this tick"',  # the device, 04.11.2026: our own words came back as params
    '"{}"',
    '"no cells now, conserve"',
    "[1]",
])  # fmt: skip
def test_a_wait_is_a_wait_whatever_its_params_say(written: str) -> None:
    answer = f'{{"action": "wait", "params": {written}, "why": "Energy declining"}}'
    assert head.parse_move(answer, ALLOWED) == (("wait", {}, "Energy declining"), "")


def test_what_a_wait_is_offered_is_no_sentence_to_copy() -> None:
    """Nine answers took the old words for parameters, some of them broke the JSON with them."""
    moves = head.offered(SimpleNamespace(available_actions={}), frozenset())
    assert moves["wait"] == {"params": {}}
