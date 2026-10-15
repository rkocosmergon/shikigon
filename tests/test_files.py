"""The files the brain writes: whole JSON atomically, JSON lines under a lock, cut to a size."""

from __future__ import annotations

import json
import multiprocessing
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "brain"))

import files
import shikigon_brain as brain


@pytest.fixture(autouse=True)
def _private_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


def _lines(path: Path) -> list[dict[str, int]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_a_whole_file_is_replaced_never_left_half_written(tmp_path: Path) -> None:
    path = tmp_path / "state" / "status.json"
    files.write_json(path, {"mood": "content"})
    files.write_json(path, {"mood": "thriving"})
    assert json.loads(path.read_text(encoding="utf-8")) == {"mood": "thriving"}
    assert sorted(p.name for p in path.parent.iterdir()) == ["status.json"]  # no .tmp left


def test_without_a_limit_every_line_stays(tmp_path: Path) -> None:
    path = tmp_path / "chat.jsonl"
    for n in range(500):
        files.append(path, {"n": n, "a": "x" * 100})
    assert [entry["n"] for entry in _lines(path)] == list(range(500))


def test_past_twice_the_limit_the_file_keeps_its_last_whole_lines(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    for n in range(2000):
        files.append(path, {"n": n}, keep_bytes=1024)
        assert path.stat().st_size <= 2 * 1024 + 20, n  # one line past the limit at most
    kept = _lines(path)  # every line whole: the cut never leaves half of one
    assert kept[-1]["n"] == 1999
    assert [entry["n"] for entry in kept] == list(range(kept[0]["n"], 2000))  # nothing missing


def test_the_tail_skips_a_broken_line_and_a_cut_first_line(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    path.write_text('{"n": 0}\n{"n": 1\nnot json\n[1, 2]\n{"n": 4}\n', encoding="utf-8")
    assert files.tail(path, 10) == [{"n": 0}, {"n": 4}]
    assert files.tail(path, 1) == [{"n": 4}]
    assert files.tail(tmp_path / "nowhere.jsonl", 5) == []
    long = tmp_path / "long.jsonl"
    long.write_text("".join(json.dumps({"n": n}) + "\n" for n in range(1000)), encoding="utf-8")
    assert [e["n"] for e in files.tail(long, 3, line_bytes=12)] == [997, 998, 999]


def _writer(path: str, who: int) -> None:
    for n in range(300):
        files.append(Path(path), {"who": who, "n": n, "pad": "y" * 40}, keep_bytes=4096)


def test_two_writers_at_once_leave_only_whole_lines(tmp_path: Path) -> None:
    """The run loop and an `instruct` write the journal at the same moment."""
    path = tmp_path / "journal.jsonl"
    workers = [multiprocessing.Process(target=_writer, args=(str(path), who)) for who in range(4)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(30)
        assert worker.exitcode == 0
    kept = _lines(path)  # raises on a line that two writers or a cut broke
    assert kept and path.stat().st_size <= 2 * 4096 + 100
    for who in range(4):  # each writer's lines that remain are in its own order
        mine = [entry["n"] for entry in kept if entry["who"] == who]
        assert mine == sorted(mine)


# --- In the brain: the journal is cut, the talk is kept, a broken line breaks nothing ----------


def test_the_journal_is_cut_and_the_talk_kept() -> None:
    for n in range(4000):
        brain.journal({"at": n, "action": "place_preset", "ok": True, "pad": "z" * 60})
    journal = brain.state_dir() / "journal.jsonl"
    assert journal.stat().st_size <= 2 * brain.JOURNAL_KEEP_BYTES
    assert _lines(journal)[-1]["at"] == 3999
    for n in range(300):
        files.append(brain.chat_path(), {"q": f"q{n}", "a": "w" * 900, "ok": True})
    assert len(_lines(brain.chat_path())) == 300  # the talk is the player's: kept whole (#468)


def test_a_broken_line_does_not_stop_the_advisor() -> None:
    home = brain.state_dir()
    home.mkdir(parents=True)
    (home / "journal.jsonl").write_text(
        '{"at": 1, "action": "place_preset", "ok": true}\n{"at": 2, "act\n', encoding="utf-8")
    (home / "chat.jsonl").write_text('{"q": "a", "a": "b", "ok": true}\n{"q": "c', encoding="utf-8")
    assert len(brain.recent_moves(5)) == 1
    assert brain.chat_tail(5) == [{"q": "a", "a": "b", "ok": True}]
