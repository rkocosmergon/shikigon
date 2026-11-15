"""Betreuer-Durchgang c6f552f (cos20 #492): no writer makes the state folder anew after ``forget``
(B1–B3), and three edges of the brain's own reading (B4, B7, B8).

B1–B3, one class: ``forget`` removes the state folder, and a writer that was under way (a lesson
beside the moves, the partner figures, a question or a word in another process) made it again with
``mkdir(parents=True)`` and wrote into it. Now the folder is made at one place only,
``files.private_folder`` at the start of a command (``private_state``); every writer that finds it
gone ends with ``FileNotFoundError``, and the command that called it says so in a sentence.

B4: a whole number in the config too large for a float ended every command in an ``OverflowError``.
B7: ``display_currency`` took any three letters (``ßßß`` is six capitals). B8: ``read_bookings``
held the world's answer to the number asked for only on paper.

Red on c6f552f, the controls green.
"""

from __future__ import annotations

import ast
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Self

import files
import lessons
import pytest
import shikigon_brain as brain
from cosmergon_agent import config
from test_advisor import CONF, Model
from test_advisor import _state as _newborn
from test_brain import _state
from test_earn_757 import _Agent
from test_lessons import GOOD, _moves, _write

BRAIN = Path(__file__).resolve().parent.parent / "brain"
sys.path.insert(0, str(BRAIN))


@pytest.fixture(autouse=True)
def _home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "sdk-config.toml")


def _gone() -> Path:
    """What ``forget`` leaves: no state folder. Returns where it was."""
    folder = brain.state_dir()
    assert folder.is_dir(), "the conftest makes it, as ``private_state`` does at a command's start"
    shutil.rmtree(folder)
    return folder


# --- the one place that makes the folder (AST, not grep) ----------------------------------------


def _mkdir_calls() -> set[tuple[str, str, str, bool]]:
    """Every call that makes a folder in ``brain/*.py``: file, function, name, with ``parents``."""
    found: set[tuple[str, str, str, bool]] = set()
    for path in sorted(BRAIN.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for function in ast.walk(tree):
            if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for node in ast.walk(function):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and (
                    node.func.attr in ("mkdir", "makedirs", "mkdtemp")
                ):
                    parents = node.func.attr == "makedirs" or any(
                        k.arg == "parents" for k in node.keywords)
                    found.add((path.name, function.name, node.func.attr, parents))
    return found


def test_the_state_folder_is_made_at_one_place_only() -> None:
    assert _mkdir_calls() == {
        ("files.py", "private_folder", "mkdir", True),
        ("lessons.py", "review_folder", "mkdir", False),  # its own subfolder, no ``parents``
        ("shikigon_brain.py", "load_config", "mkdir", True),  # the config folder
    }


# --- the sink, one writer after another --------------------------------------------------------


def _write_json() -> None:
    files.write_json(brain.state_dir() / "x.json", {"a": 1})


def _append() -> None:
    files.append(brain.state_dir() / "x.jsonl", {"a": 1})


def _held() -> None:
    with files.held(brain.state_dir()):
        pass


def _lines_held() -> None:
    with files.lines_held(brain.state_dir() / "x.jsonl"):
        pass


def _one_move() -> None:
    with files.one_move(brain.state_dir()):
        pass


def _keep() -> None:
    lessons.keep(brain.state_dir(), {"at": 1, "lessons": "x"})


def _review_folder() -> None:
    lessons.review_folder(brain.state_dir(), [], "", "")


WRITERS = [_write_json, _append, _held, _lines_held, _one_move, _keep, _review_folder]


@pytest.mark.parametrize("writer", WRITERS, ids=lambda w: w.__name__.strip("_"))
def test_a_writer_finds_the_folder_gone_and_makes_none(writer: Any) -> None:
    folder = _gone()
    with pytest.raises(FileNotFoundError):
        writer()
    assert not folder.exists()


@pytest.mark.parametrize("writer", WRITERS, ids=lambda w: w.__name__.strip("_"))
def test_control_the_same_writer_writes_where_the_folder_is(writer: Any) -> None:
    writer()
    assert brain.state_dir().is_dir()


async def test_a_move_without_the_folder_says_so_and_makes_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The game loop's move and a ``tick`` by hand: one sentence, no folder, no traceback."""
    monkeypatch.setattr(brain, "gone", lambda: False)  # the look before the lock saw it still
    folder = _gone()
    assert await brain.tick(None, None, until_gone=True) == brain.GONE_MOVE
    assert await brain.tick(None, None) == brain.GONE_MOVE
    assert not folder.exists()


# --- three whole ways, the folder removed while the answer is still out -------------------------


async def test_a_lesson_that_ends_after_forget_makes_no_folder(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """B1: ``forget`` runs while the model is asked; the lesson is ready afterwards."""
    _write(_moves(100))
    folder = brain.state_dir()

    async def forgotten_meanwhile(messages: list[dict[str, str]], max_tokens: int) -> str:
        shutil.rmtree(folder)
        return GOOD

    cfg = {"brain": {"advisor": CONF}}
    entry = await brain.learn(cfg, _state(), force=True, send=forgotten_meanwhile)
    assert entry is not None and entry["ok"] is False and "could not be kept" in entry["why"]
    assert not folder.exists()
    assert "the lesson could not be kept (FileNotFoundError)" in capsys.readouterr().err


async def test_control_a_lesson_without_forget_is_kept() -> None:
    _write(_moves(100))
    cfg = {"brain": {"advisor": CONF}}
    entry = await brain.learn(cfg, _state(), force=True, send=Model(GOOD))
    assert entry is not None and entry["ok"] is True
    assert lessons.kept(brain.state_dir())


class _ForgottenWhileAsked(_Agent):
    """The world answers after ``forget`` removed the folder."""

    async def partner(self) -> Any:
        _gone()
        return await super().partner()


async def test_partner_figures_that_come_after_forget_make_no_folder(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """B2: ``earn_refresh`` runs beside the moves and writes ``earn.json`` after up to 30 s."""
    folder = brain.state_dir()
    await brain.earn_refresh(_ForgottenWhileAsked())
    assert not folder.exists()
    assert "earn figures not available (FileNotFoundError)" in capsys.readouterr().err


async def test_control_partner_figures_without_forget_are_kept() -> None:
    await brain.earn_refresh(_Agent())
    assert brain.earn_kept() is not None


class _Entered:
    """The SDK client of a command: an async context, a state, nothing else."""

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def refresh_state(self) -> Any:
        return _newborn()  # a state the rule tree can read, as ``ask`` shows it to the model


async def test_a_question_answered_after_forget_makes_no_folder(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """B3: ``ask`` in another process; ``forget`` runs while the model is asked. The command ends
    in a sentence, and ``keep_unasked`` does not make the talk's folder anew either."""
    import connections

    cfg = {"world": {"base_url": "https://world.test"}, "brain": {"advisor": CONF}}
    files.write_json(brain.credentials_path(), {"agent_id": "a1", "api_key": "AGENT-a1:k",
                                                "world": connections.origin("https://world.test")})
    monkeypatch.setattr(brain, "make_agent", lambda _cfg: _Entered())
    folder = brain.state_dir()

    async def forgotten_meanwhile(messages: list[dict[str, str]], max_tokens: int) -> str:
        shutil.rmtree(folder)
        return "An answer."

    said = await brain.with_agent(
        cfg, lambda agent: brain.ask(agent, cfg, "How is it going?", "", forgotten_meanwhile))
    brain.keep_unasked("How is it going?", said)
    assert said["ok"] is False and "FileNotFoundError" in said["why"], said
    assert not folder.exists()
    assert "could not be kept in the talk (FileNotFoundError)" in capsys.readouterr().err


async def test_a_word_after_forget_keeps_its_answer_and_makes_no_folder(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``instruct``: the world took the compass; the journal line cannot be written any more."""
    from test_brain import FakeAgent

    folder = brain.state_dir()

    class Forgetting(FakeAgent):
        async def set_compass(self, preset: str) -> dict[str, Any]:
            shutil.rmtree(folder)
            return {"preset": preset}

    table = {"defend": ["steady"]}
    said = await brain.instruct(Forgetting(_state()), "steady", table)
    assert said == {"heard": "steady", "preset": "defend", "ok": True}
    assert not folder.exists()
    assert "its journal could not be written (FileNotFoundError)" in capsys.readouterr().err


def test_an_advisor_command_after_forget_says_so_and_makes_no_folder(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``advisor advise`` of the panel in a process of its own, the folder removed by then."""
    import argparse

    folder = _gone()
    args = argparse.Namespace(command="advisor", what="advise", rest=["5"])
    assert brain._local(args, {"brain": {"advisor": CONF}}) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and "nothing was kept" in out["why"], out
    assert not folder.exists()


# --- B4, B7, B8 ---------------------------------------------------------------------------------


def test_b4_a_whole_number_too_large_for_a_float_is_a_sentence(
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = brain.config_path()
    path.parent.mkdir(parents=True)
    shipped = brain.REPO_DEFAULT_CONFIG.read_text(encoding="utf-8")
    path.write_text(shipped.replace("interval_s = 60", "interval_s = 1" + "0" * 400),
                    encoding="utf-8")
    assert brain.main(["status"]) == brain.CONFIG_EXIT
    said = json.loads(capsys.readouterr().out)
    assert "brain.interval_s in the config is not a number" in said["why"], said


def test_b4_control_a_number_is_a_number() -> None:
    assert brain.config_kind(60) == "a number" and brain.config_kind(1.5) == "a number"
    assert brain.config_kind(10**400) == "no number" and brain.config_kind(float("inf")) == (
        "no number")


@pytest.mark.parametrize("code", ["ßßß", "αβγ", "ABCD", "EU", "€€€", "٣٣٣"])
def test_b7_a_currency_that_is_not_three_latin_capitals_is_eur(code: str) -> None:
    config.CONFIG_PATH.write_text(f'[display]\ncurrency = "{code}"\n', encoding="utf-8")
    assert brain.display_currency() == "EUR"


@pytest.mark.parametrize("given, kept", [("USD", "USD"), ("usd", "USD"), ("Gbp", "GBP")])
def test_b7_control_three_latin_letters_stay(given: str, kept: str) -> None:
    config.CONFIG_PATH.write_text(f'[display]\ncurrency = "{given}"\n', encoding="utf-8")
    assert brain.display_currency() == kept


class _Ledger:
    """A world that sends more rows than asked for."""

    def __init__(self, events: Any, bookings: Any) -> None:
        self._events, self._bookings = events, bookings

    async def get_energy_events(self, tier: str, limit: int) -> Any:
        return self._events

    async def get_energy_transactions(self, limit: int) -> Any:
        return self._bookings


async def test_b8_more_rows_than_asked_are_cut_to_the_number_asked() -> None:
    events = [{"n": i} for i in range(brain.LEDGER_EVENTS * 20)]
    bookings = [{"n": i} for i in range(brain.LEDGER_BOOKINGS * 20)]
    got_events, got_bookings = await brain.read_bookings(_Ledger(events, bookings))
    assert got_events == events[: brain.LEDGER_EVENTS]
    assert got_bookings == bookings[: brain.LEDGER_BOOKINGS]


async def test_b8_only_lists_count() -> None:
    assert await brain.read_bookings(_Ledger({"a": 1}, "text")) == (None, None)
    assert await brain.read_bookings(_Ledger(None, None)) == (None, None)


async def test_b8_control_rows_within_the_number_come_whole() -> None:
    events, bookings = [{"n": 1}], [{"n": 2}, {"n": 3}]
    assert await brain.read_bookings(_Ledger(events, bookings)) == (events, bookings)
    assert await brain.read_bookings(object()) == (None, None)
