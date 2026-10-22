"""The host checklist's rows for what the brain hands the shell and the system (cos20 #492).

Each test is a finding of the full review on 17.10.2026 (F1–F8) and was red against 0.1.3.
No network and no agent is started.
"""

from __future__ import annotations

import io
import json
import stat
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "brain"))

import advisor
import connections
import shikigon_brain as brain
from conftest import endless, hostile


@pytest.fixture(autouse=True)
def _private_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


def _listing(names: list[Any]) -> Any:
    body = {"data": [{"id": name} for name in names]}
    return SimpleNamespace(status_code=200, json=lambda: body, raise_for_status=lambda: None)


# --- F1: a model list reaches the shell only bounded -------------------------------------------


def test_a_model_list_reaches_the_bar_short_printable_and_bounded() -> None:
    names = ["ok-model", "x" * 500, "bad\nline\u0007", ""] + [f"m{i}" for i in range(1000)]
    found = connections.models_at("http://localhost:1234/v1", lambda url, **_: _listing(names))
    assert found["ok"] is True
    assert len(found["models"]) == connections.MODELS_KEPT
    assert "x" * 500 not in found["models"], "a name too long for the panel is left out"
    assert "badline" in found["models"], "control characters are taken out"
    assert all(len(name) <= connections.NAME_CHARS for name in found["models"])


def test_a_list_past_the_limit_is_said_not_read() -> None:
    def too_long(url: str, **_: Any) -> Any:
        raise connections.TooLong(url)

    found = connections.models_at("http://localhost:1234/v1", too_long)
    assert found == {"ok": False, "models": [], "why": "its list of models is too long"}


def test_the_bounded_read_stops_at_its_limit() -> None:
    # A real server that never stops (until 0.1.11 a stub of httpx.stream: it measured a body
    # already in memory, cos20 #492).
    with hostile(endless) as (address, _), pytest.raises(connections.TooLong):
        connections.bounded_get(f"{address}/v1/models")


def test_a_failure_names_the_host_never_a_password_in_the_address() -> None:
    def away(url: str, **_: Any) -> Any:
        raise OSError("refused")

    why = connections.models_at("https://me:secret@llm.example/v1", away)["why"]
    assert "secret" not in why and "llm.example" in why


# --- F2: a server "on this machine" is the player's own ----------------------------------------

TCP = """\
  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
   0: 0100007F:2CAB 00000000:0000 0A 00000000:00000000 00:00000000 00000000   957        0 1 1
   1: 0100007F:04D2 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1001        0 2 1
   2: 0100007F:04D2 0100007F:9C40 01 00000000:00000000 00:00000000 00000000  1000        0 3 1
"""  # 0x2CAB = 11435 (a system service), 0x04D2 = 1234 (listened on by uid 1001)


def test_a_local_server_counts_only_when_it_is_the_players_or_the_systems() -> None:
    assert connections.listeners(1234, [TCP]) == [1001], "only listening sockets"
    assert connections.own_server("http://localhost:11435/v1", [TCP], me=1000) is True
    assert connections.own_server("http://localhost:1234/v1", [TCP], me=1000) is False
    assert connections.own_server("http://localhost:1234/v1", [TCP], me=1001) is True
    assert connections.own_server("http://localhost:9999/v1", [TCP], me=1000) is False


def test_a_server_another_person_runs_is_not_offered_or_asked() -> None:
    asked: list[str] = []

    def get(url: str, **_: Any) -> Any:
        asked.append(url)
        return _listing(["m"])

    rows = connections.found({}, get, is_installed=lambda agent: False,
                             is_own=lambda url: "11434" in url)
    assert [row["text"] for row in rows] == ["Ollama on this machine"]
    assert all("1234/" not in url for url in asked), "the other server is not even asked"


# --- F3: an address and a model come on stdin, never as arguments -----------------------------


def test_the_address_comes_on_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(connections, "models_at",
                        lambda where, get, key="": seen.append(where) or {"ok": True})
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"where": "https://me:pw@llm.example/v1"}\n'))
    assert brain.advisor_command(brain.load_config(), "models", []) == {"ok": True}
    assert seen == ["https://me:pw@llm.example/v1"]


def test_a_connection_and_its_model_come_on_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    async def says_ok(messages: list[dict[str, str]], max_tokens: int) -> str:
        return "OK"

    monkeypatch.setattr(advisor, "sender", lambda conf, state=None, patience=1: says_ok)
    line = '{"connection": "http:http://x/v1", "model": "m"}\n'
    monkeypatch.setattr(sys, "stdin", io.StringIO(line))
    assert brain.advisor_command(brain.load_config(), "try", [])["ok"] is True
    assert connections.chosen(brain.state_dir())["base_url"] == "http://x/v1"


# --- F4: a kept key goes only to the address it was given for -----------------------------------


def test_a_key_is_bound_to_the_address_it_was_given_for(monkeypatch: pytest.MonkeyPatch) -> None:
    home = brain.state_dir()
    line = '{"key": "sk-a", "where": "https://a.example/v1"}\n'
    monkeypatch.setattr(sys, "stdin", io.StringIO(line))
    assert brain.advisor_command(brain.load_config(), "key", []) == {"ok": True}
    assert stat.S_IMODE((home / "advisor_key").stat().st_mode) == 0o600
    assert connections.kept_key(home, "https://a.example/v1/") == "sk-a"
    assert connections.kept_key(home, "https://a.example:443/other") == "sk-a"
    assert connections.kept_key(home, "https://b.example/v1") == "", "another address gets none"
    assert connections.kept_key(home, "http://a.example/v1") == "", "nor the same host unencrypted"
    sent: list[dict[str, str]] = []
    connections.models_at("https://b.example/v1",
                          lambda url, **kw: sent.append(kw["headers"]) or _listing(["m"]),
                          connections.kept_key(home, "https://b.example/v1"))
    assert sent == [{}]


def test_a_key_without_its_address_is_not_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("sk-bare\n"))
    assert brain.advisor_command(brain.load_config(), "key", [])["ok"] is False
    assert not (brain.state_dir() / "advisor_key").exists()


def test_a_key_kept_by_0_1_3_names_no_address_and_goes_to_none() -> None:
    """A bare line names no origin. Until 0.1.12 it was bound to whatever ``advisor.json``
    named when it was read, so it followed the next pick (cos20 #492 B3)."""
    home = brain.private_state()
    (home / "advisor_key").write_text("sk-old")
    connections.choose(home, {"via": "http", "base_url": "https://a.example/v1", "model": "m"})
    assert connections.kept_key(home, "https://a.example/v1") == ""
    assert connections.kept_key(home, "https://b.example/v1") == ""


# --- F5: the bar reads small views, and server blocks are bounded -------------------------------


def test_the_talk_for_the_bar_is_its_last_three_exchanges_cut_to_the_panel() -> None:
    from files import append

    for n in range(5):
        append(brain.chat_path(), {"at": n, "q": f"q{n}", "a": "a" * 5_000, "ok": True})
    brain.show_talk()
    shown = json.loads((brain.state_dir() / brain.BAR_TALK).read_text())
    assert [turn["q"] for turn in shown] == ["q2", "q3", "q4"]
    assert all(len(turn["a"]) == brain.ANSWER_CHARS for turn in shown)
    assert len(brain.chat_path().read_text().splitlines()) == 5, "the talk itself stays whole"


def test_the_journal_for_the_bar_is_its_last_lines() -> None:
    for n in range(30):
        brain.journal({"at": n, "action": "wait", "ok": True})
    shown = json.loads((brain.state_dir() / brain.BAR_JOURNAL).read_text())
    assert [line["at"] for line in shown] == list(range(18, 30))


def test_a_server_block_too_big_for_the_bar_is_left_out() -> None:
    assert brain.bounded({"status": "running"}) == {"status": "running"}
    assert brain.bounded({"rows": ["x" * 100] * 200}) is None
    assert brain.bounded(None) is None


def test_a_hostile_server_cannot_make_status_big() -> None:
    """Every text of the server in status.json is cut (cos20 #492): the bar reads the file in
    the shared shell. Counter-test: on 0.1.4 (d82710c) name, persona, compass and the
    catastrophe went through whole."""
    huge = "x" * 5_000_000
    state = SimpleNamespace(
        agent_name=huge, persona_type=huge, compass_preset=huge, energy=1.0, fields=[],
        tick=1, ranking=None, showcase=None,
        world_briefing=SimpleNamespace(situation=SimpleNamespace(
            energy_trend="falling", fields_owned=0, active_catastrophe=huge)),
        tournament={"rows": [huge]}, next_step={"next": huge}, key_expiry={"at": huge},
    )
    shown = brain.snapshot(state, {"action": "wait", "ok": True, "why": ""})
    assert len(json.dumps(shown)) < brain.STATUS_BYTES
    assert len(shown["name"]) == brain.NAME_CHARS


def test_an_address_is_never_taken_as_an_argument(tmp_path: Path) -> None:
    """0.1.4 (H1/H6): ``advisor models|try`` take an agent as an argument, never an address —
    it can carry a name and a password (https://me:pw@host). Counter-test: d82710c took it."""
    cfg = {"world": {}, "character": {}, "brain": {}}
    for what in ("models", "try"):
        said = brain.advisor_command(cfg, what, ["https://me:pw@models.example/v1"])
        assert said == {"ok": False, "why": brain.ADDRESS_ON_STDIN}, what


# --- F6: the agent's key travels only encrypted or within home ----------------------------------


def test_a_world_over_plain_http_gets_no_key() -> None:
    cfg = {"world": {"base_url": "http://cosmergon.example"}, "character": {}}
    posted: list[str] = []
    with pytest.raises(SystemExit, match="https"):
        brain.register(cfg, lambda url, **_: posted.append(url))
    assert posted == []
    assert brain.world_is_safe({"world": {"base_url": "https://cosmergon.com"}})
    assert brain.world_is_safe({"world": {"base_url": "http://localhost:8080"}})


# --- F7: private words never stand in a command line --------------------------------------------


class _Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_at_a_terminal_the_words_are_asked_for_on_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "stdin", _Terminal("what is a blinker?\n"))
    assert brain.said_on_stdin("Your question") == "what is a blinker?"
    out = capsys.readouterr()
    assert out.out == "" and "Your question" in out.err


def test_the_coach_task_goes_to_a_private_file_and_the_command_line_holds_its_path(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    brain.files.write_json(brain.state_dir() / "status.json", {"name": "x", "energy": 4242.0})
    monkeypatch.setattr(sys, "stdin", _Terminal("why does it wait?\n"))
    assert brain.main(["coach"]) == 0
    printed = capsys.readouterr().out
    path = brain.state_dir() / "coach.md"
    assert printed.strip() == f"Read the task in {path} and follow it."
    assert "4242" not in printed and "why does it wait" not in printed
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "4242" in path.read_text() and "why does it wait?" in path.read_text()


# --- F8: the state folder is the player's alone, whoever makes it -------------------------------


def test_a_command_by_hand_before_the_first_wake_makes_the_folder_private(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert not brain.state_dir().exists()
    brain.main(["status"])
    assert stat.S_IMODE(brain.state_dir().stat().st_mode) == 0o700
