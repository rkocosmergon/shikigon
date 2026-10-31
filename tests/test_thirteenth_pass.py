"""The thirteenth review (cos20 #492, the betreuer pass at ``b4ba36a``): three promises the code
did not keep, and four things beyond them.

1. (Pflicht 1) the CHANGELOG said the world's own addresses use the system's trust store. Only the
   brain's own calls to the world do (waking, forgetting, its rules); the moves go through the
   SDK, which checks the world against certifi (cos20 #599 left it so on purpose). The sentence
   says so now, and the first two tests pin both halves over real TLS;
2. (Pflicht 2) the notes told every model the key "is never shown"; the terminal shows it in full
   under K, as the README says;
3. (Pflicht 3) the README's "at least ten minutes apart" left out the hint for the first field,
   which comes at once; its test is ``qml::test_a_slot_offered_later_gets_its_own_hint_and_the_
   first_field_is_celebrated_at_once``;
4. (Kür) a question went into chat.jsonl uncut;
5. (Kür) the bar started the agent's program for its sign-in by name, past the placeholder check;
6. (Kür) an agent that is not asked answered its model list empty, and the panel said "asking…"
   without end (the panel's side: ``qml::test_the_model_step_says_why_it_shows_no_model``);
7. (Kür, the Marauder lines) has its test in ``tests/qml/tst_model.qml``.

Each test is red against ``b4ba36a`` (counter-test), except where its docstring says it guards the
form of a fix.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "brain"))

import connections
import shikigon_brain as brain
from test_advisor import CFG, Model, _state
from test_brain import FakeAgent
from test_sixth_pass import _registered
from test_systemspeicher_599 import ca_in_store, model_server, own_ca  # noqa: F401 (fixtures)
from test_twelfth_pass import _placeholder


@pytest.fixture(autouse=True)
def _private_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


# --- 1: the brain's own calls ask the system's store, the moves certifi ------------------------


@pytest.fixture
def ca_only_in_certifi(own_ca: Path, monkeypatch: pytest.MonkeyPatch) -> Path:  # noqa: F811
    """certifi names the test CA, the system's store does not have it: who trusts the world
    now shows which store it asked."""
    import certifi

    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    monkeypatch.setattr(certifi, "where", lambda: str(own_ca / "ca.pem"))
    return own_ca


async def test_the_moves_trust_certifi_and_the_brains_own_call_does_not(
    ca_only_in_certifi: Path,
) -> None:
    """Guards the form of the narrowed CHANGELOG sentence: the SDK client ``make_agent`` builds
    reaches a world whose CA only certifi has; the brain's door (``world_info``) does not."""
    import asyncio

    with model_server(ca_only_in_certifi) as v1:
        world = v1.removesuffix("/v1")
        cfg = _registered(world)
        assert await asyncio.to_thread(brain.world_info, cfg, connections.bounded_get) == {}, (
            "the brain's door asked certifi")
        async with brain.make_agent(cfg)._create_client() as client:
            answer = await client.get(world + "/api/v1/game/info")
    assert answer.status_code == 200, "the moves no longer go by certifi: say so in the CHANGELOG"


def test_the_brains_own_call_to_the_world_trusts_the_system_store(
    ca_in_store: Path,  # noqa: F811
) -> None:
    """Guards the form: the rules (and so waking and forgetting, the same door) come over the
    system's store."""
    with model_server(ca_in_store) as v1:
        info = brain.world_info(_registered(v1.removesuffix("/v1")), connections.bounded_get)
    assert info == {"data": [{"id": "m"}]}, info


# --- 2: the notes say where the key is shown, as the README does ---------------------------------


def test_the_notes_tell_the_model_where_the_key_is_shown_as_the_readme_does() -> None:
    notes = (ROOT / "brain" / "advisor_notes.md").read_text(encoding="utf-8")
    key = next(line for line in notes.splitlines() if line.startswith("- The key:"))
    readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
    assert "Shown only when you press `K` in the terminal, there in full" in readme
    assert "never shown" not in key, key
    assert "shown only in the terminal, in full, when the player presses K" in key, key


# --- 4: a question is kept as long as the bar shows it -------------------------------------------


async def test_a_long_question_is_kept_as_long_as_the_bar_shows_it() -> None:
    await brain.ask(FakeAgent(_state()), CFG, "q" * 50_000, "", Model("an answer"))
    kept = [json.loads(line) for line in brain.chat_path().read_text().splitlines()]
    assert len(kept) == 1 and len(kept[0]["q"]) == brain.QUESTION_CHARS


# --- 5: the sign-in starts the path it checked, never the placeholder ----------------------------


def _starts() -> tuple[list[tuple[str, list[str]]], Any]:
    started: list[tuple[str, list[str]]] = []
    return started, lambda path, argv: started.append((path, argv))


def test_the_sign_in_never_starts_omarchys_placeholder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    mark = _placeholder(tmp_path, monkeypatch)
    started, start = _starts()
    why = brain.sign_in("pi", start)
    assert started == [] and "Omarchy's placeholder" in why, why
    assert not mark.exists()


def test_the_sign_in_starts_the_very_path_it_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    folder = tmp_path / "bin"
    folder.mkdir()
    pi = folder / "pi"
    pi.write_text("#!/bin/sh\nexit 0\n")
    pi.chmod(0o755)
    monkeypatch.setenv("PATH", f"{folder}:/usr/bin:/bin")
    started, start = _starts()
    assert brain.sign_in("pi", start) == ""
    assert started == [(str(pi), [str(pi)])]


def test_the_program_signed_in_to_gets_the_umask_the_brain_was_started_with(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The brain's own 077 (K2) is not the player's: they may go on working in that pi."""
    done: list[Any] = []
    monkeypatch.setattr(brain, "INHERITED_UMASK", 0o002)
    monkeypatch.setattr(brain.os, "umask", lambda mask: done.append(("umask", mask)) or 0o077)
    monkeypatch.setattr(brain.os, "execv", lambda path, argv: done.append(("execv", path, argv)))
    brain.start_program("/usr/bin/pi", ["/usr/bin/pi"])
    assert done == [("umask", 0o002), ("execv", "/usr/bin/pi", ["/usr/bin/pi"])]


@pytest.mark.parametrize("agent", ["codex", "sh", "../pi", "", "pi\x1b[2J"])
def test_the_sign_in_takes_only_an_agent_shikigon_asks(agent: str) -> None:
    """Codex is known but not tried; every other word is no agent of Shikigon's."""
    started, start = _starts()
    why = brain.sign_in(agent, start)
    assert started == [] and why.endswith("cannot be signed in to from here"), why
    assert "\x1b" not in why


def test_the_bar_signs_in_through_the_brain() -> None:
    """Guards the form: the one start of an agent's program in the bar goes through ``sign-in``."""
    service = (ROOT / "qml" / "Service.qml").read_text(encoding="utf-8")
    assert 'brainCommand(["sign-in", String(agent)])' in service
    assert ".concat(argv)" not in service
    assert "openSignIn(pickedRow.agent)" in (ROOT / "qml" / "BarWidget.qml").read_text()


# --- 6: an agent that is not asked says why instead of an empty list -----------------------------


def test_an_agent_the_placeholder_stands_in_for_says_why_it_lists_no_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    mark = _placeholder(tmp_path, monkeypatch)
    said = brain.advisor_command(CFG, "models", ["agent:pi"])
    assert said["ok"] is False and said["models"] == [], said
    assert "Omarchy's placeholder" in said["why"], said
    assert not mark.exists()


def test_an_agent_at_a_version_not_tried_says_why_it_lists_no_model() -> None:
    def run(argv: list[str], **_: Any) -> Any:
        return SimpleNamespace(returncode=0, stdout="9.9.9", stderr="")

    said = connections.agent_listing("pi", run)
    assert said == {"ok": False, "models": [], "why": connections.untried("pi", run)}, said
    assert "9.9.9" in said["why"]
