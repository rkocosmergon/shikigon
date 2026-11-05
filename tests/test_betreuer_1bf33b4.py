"""Guards for the betreuer pass on 1bf33b4 (cos20 #492).

B1: a brain that ended outside a move came back every 30 s without end, and the panel never said
why. B2: the README said there is no "move now" next to the IPC target that makes one. B3: without
the world's forms, the rules took the place of a refusal that costs nothing. The logic of the bar
is tested in ``tests/qml/tst_model.qml``; here is what only the sources or the program can show.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "brain"))

import head
import shikigon_brain as brain
from test_advisor import CONF
from test_effect import _with_total
from test_head import Replies, StubTree, _move

SERVICE = (ROOT / "qml" / "Service.qml").read_text(encoding="utf-8")
MODEL = (ROOT / "qml" / "ShikigonModel.js").read_text(encoding="utf-8")
BAR = (ROOT / "qml" / "BarWidget.qml").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
BUDGET = {**CONF, "learning_budget_pct": 5}
OFFER = "c-offered"


@pytest.fixture(autouse=True)
def _private_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


def _program(*args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "brain" / "shikigon_brain.py"), *args],
        check=False, capture_output=True, text=True, timeout=60, env={**os.environ, **env},
    )  # fmt: skip


def _config(text: str) -> None:
    path = brain.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _a_brain_with_a_key() -> None:
    """A world nobody listens at, and a key: it can register nothing and reach no one. Over https:
    a key over plain http goes through no proxy."""
    _config('[world]\nbase_url = "https://127.0.0.1:1"\n')
    state = brain.state_dir()
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    credentials = {"agent_id": "a-test", "api_key": "CSMR-test", "world": "https://127.0.0.1:1"}
    (state / "credentials.json").write_text(json.dumps(credentials))
    (state / "credentials.json").chmod(0o600)


# ---------- B1: the bar tells a brain that stopped from a config it cannot follow ----------


def test_b1_a_config_it_cannot_follow_ends_with_its_own_code() -> None:
    _config("[world\n")
    done = _program("status")
    assert done.returncode == brain.CONFIG_EXIT, done.stdout
    assert done.returncode not in (0, 1), "1 is what any crash ends with"


def test_b1_an_exception_outside_a_move_ends_with_1_and_says_why_on_its_last_line() -> None:
    """Variant (c) of the review, as a program: the last line of stderr is what the panel shows.
    A proxy of an unknown kind ends the run before a move, with nothing sent."""
    _a_brain_with_a_key()
    done = _program("run", ALL_PROXY="gopher://127.0.0.1:1", all_proxy="gopher://127.0.0.1:1")
    assert done.returncode == 1, (done.stdout, done.stderr[-400:])
    last = done.stderr.strip().splitlines()[-1]
    assert "proxy" in last.lower() and len(last) <= 200, last


def test_b1_the_exit_code_is_the_same_in_the_brain_and_the_bar() -> None:
    found = re.search(r"var CONFIG_EXIT = (\d+)", MODEL)
    assert found is not None and int(found.group(1)) == brain.CONFIG_EXIT


def test_b1_the_wait_comes_from_the_model_and_only_a_stop_it_did_not_cause_counts() -> None:
    stop = SERVICE[SERVICE.index("function brainStopped("):]
    stop = stop[:stop.index("\n  }\n")]
    assert "Model.stopsAfter(" in stop and "Model.restartWait(" in stop
    assert "restartLater.restart()" in stop
    runner = SERVICE[SERVICE.index("id: runner"):SERVICE.index("// ---------- Who answers")]
    assert "brainStopped(code)" in runner
    written = r"if \(root\.restartWanted\).*else if \(!mine\) root\.brainStopped\(code\)"
    assert re.search(written, runner, re.DOTALL), "its own stop does not count, a SIGTERM does"


def test_b1_a_brain_that_cannot_start_counts_as_stopped() -> None:
    """Variant (e): Quickshell says ``runningChanged`` and no ``exited`` when the program cannot be
    started (src/io/process.cpp, onErrorOccurred), so no restart came and the panel said nothing."""
    runner = SERVICE[SERVICE.index("id: runner"):SERVICE.index("// ---------- Who answers")]
    changed = runner[runner.index("onRunningChanged:"):runner.index("stderr: SplitParser")]
    assert "root.exitSeen" in changed and "root.brainStopped(-1)" in changed
    exited = runner[runner.index("onExited:"):]
    assert "root.exitSeen = true" in exited


def test_b1_a_new_start_has_no_old_line_to_show() -> None:
    runner = SERVICE[SERVICE.index("id: runner"):SERVICE.index("// ---------- Who answers")]
    changed = runner[runner.index("onRunningChanged:"):runner.index("stderr: SplitParser")]
    assert 'root.lastError = ""' in changed and "root.startedAt = Date.now()" in changed


def test_b1_the_panel_gets_the_stuck_sentence_from_the_service() -> None:
    assert "stuck: service ? service.stuck" in BAR
    held = SERVICE[SERVICE.index("readonly property string stuck"):]
    held = held[:held.index("\n\n")]
    for needed in ("awake", "stops", "runner.running", "waker.running", "Model.stuckSaid("):
        assert needed in held, needed


def test_b1_try_again_with_a_shikigon_brings_the_restart_forward_and_registers_nothing() -> None:
    wake = SERVICE[SERVICE.index("function wake(fresh)"):SERVICE.index("function recheck()")]
    assert "if (awake && !fresh) return tryAgain()" in wake
    again = SERVICE[SERVICE.index("function tryAgain()"):SERVICE.index("function wake(fresh)")]
    assert "restartLater.running" in again and "restartLater.restart()" in again
    assert "waker." not in again, "the timer starts the wake script, with --look"


def test_b1_the_timer_stays_one_that_ends() -> None:
    timer = SERVICE[SERVICE.index("id: restartLater"):SERVICE.index("id: credentialCheck")]
    assert "repeat" not in timer and "waker.look = true" in timer


# ---------- B2: what the README says of the IPC target ``tick`` ----------


def test_b2_the_readme_says_what_tick_does() -> None:
    text = " ".join(README.split())
    assert 'There is no "move now": it moves' not in text
    assert "`tick` makes one move at once, for testing" in text
    assert 'The panel has no "move now"' in text


def test_b2_the_ipc_target_is_still_there() -> None:
    handler = SERVICE[SERVICE.index("IpcHandler {"):]
    assert "function tick(): string { return root.tickNow() }" in handler


# ---------- B3: a refusal gives nothing, also without the world's forms ----------


def _offered() -> Any:
    offer = {"contract_id": OFFER, "contract_type": "non_aggression"}
    return dataclasses.replace(_with_total(1_000_000.0), pending_contracts=(offer,))


async def _head_says(answer: str) -> tuple[tuple[str, dict[str, Any]], Any]:
    decider = head.HeadDecider(StubTree(), BUDGET, brain.state_dir(), send=Replies(answer, answer))
    return await decider.decide(_offered()), decider


async def test_b3_a_refusal_goes_out_without_a_form_under_a_budget() -> None:
    played, _ = await _head_says(_move("reject_contract", {"contract_id": OFFER}, "no"))
    assert played[0] == "reject_contract"
    assert "max_cost" not in played[1] and "strict" not in played[1]


async def test_b3_control_a_refusal_of_what_was_not_offered_stays_refused() -> None:
    played, decider = await _head_says(_move("reject_contract", {"contract_id": "other"}, "no"))
    assert played[0] != "reject_contract"
    assert decider.last is not None and "fallback" in decider.last


async def test_b3_control_a_gift_without_a_form_stays_with_the_rules() -> None:
    played, decider = await _head_says(_move("transfer_energy", {"amount": 100}, "gift"))
    assert played[0] != "transfer_energy"
    assert decider.last is not None and "fallback" in decider.last


def test_b3_gives_nothing_names_the_refusal_only() -> None:
    assert head.gives_nothing("reject_contract", {}, _offered()) is True
    assert head.gives_nothing("transfer_energy", {}, _offered()) is False


async def test_b3_waiting_goes_out_without_a_form() -> None:
    played, _ = await _head_says(_move("wait", {}, "not now"))
    assert played == ("wait", {})


@pytest.mark.parametrize(("reward", "gives"), [(0, True), (5, False)])
def test_b3_a_mission_gives_nothing_only_without_a_reward(reward: int, gives: bool) -> None:
    assert head.gives_nothing("start_mission", {"reward_energy": reward}, _offered()) is gives


@pytest.mark.parametrize(("offer", "gives"), [
    ({"contract_type": "non_aggression"}, True),
    ({"contract_type": "alliance", "terms": {"cost_per_tick": 500}}, False),
])  # fmt: skip
def test_b3_an_answer_to_a_contract_gives_nothing_only_if_it_costs_nothing(
    offer: dict[str, Any], gives: bool
) -> None:
    state = dataclasses.replace(_with_total(1_000_000.0),
                                pending_contracts=({"contract_id": OFFER, **offer},))
    assert head.gives_nothing("accept_contract", {"contract_id": OFFER}, state) is gives


def test_b3_the_readme_says_what_goes_out_without_a_form() -> None:
    text = " ".join(README.split())
    assert "and so is any move the world states no form for." not in text
    assert ("Without a form only what gives nothing goes out: waiting, refusing an offer, "
            "a contract that costs nothing and a mission without a reward.") in text
