"""What leaves the brain, checked when it is sent (H9) — and the classes found beside it.

The marketplace review of 0.1.9 (#12356): whose a port on this machine is, was read when the
server was found, not when the next question went out. Each test here was red against 0.1.9
(``c9c348a``). Beside it, the variant review of 20.10.2026 against the reviewer's findings on
other plugins found four more (cos20 #492): a key not bound to its world (K9), answers read
without a limit (K6), a model name taken as an option (K12), an agent's children outliving
its timeout (K14).
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "brain"))

import advisor
import connections
import shikigon_brain as brain
from conftest import endless, hostile, wire

BRAIN = Path(__file__).resolve().parent.parent / "brain"
ME = os.getuid()
STRANGER = 4242 if ME != 4242 else 4343  # a person's account: at or above UID_MIN


@pytest.fixture(autouse=True)
def _private_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


CLIENT, OURS = 50000, "4711"  # the brain's own end of the connection, and its socket inode


def _table(port: int, uid: int | None, taker: int | None = None) -> str:
    """``/proc/net/tcp`` with one listener on ``127.0.0.1:port`` (none for ``uid=None``), the
    brain's own connection to it, and the socket that took that connection. The taker is the
    listener's account unless the test names another: in the kernel a socket a listener took
    keeps that listener's uid (cos20 #492, fifth review)."""
    head = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid\n"
    if uid is None:
        return head
    taker = uid if taker is None else taker
    return head + (
        f"   0: 0100007F:{port:04X} 00000000:0000 0A 0:0 00:0 0 {uid:>5} 0 1 1\n"
        f"   1: 0100007F:{CLIENT:04X} 0100007F:{port:04X} 01 0:0 00:0 0 {ME:>5} 0 {OURS} 1\n"
        f"   2: 0100007F:{port:04X} 0100007F:{CLIENT:04X} 01 0:0 00:0 0 {taker:>5} 0 0 1\n")


class Kernel:
    """Stands in for ``/proc/net/tcp``: who listens on which port and who took the brain's
    connection to it, changed by the test."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, port: int, uid: int | None) -> None:
        self.port, self.uid = port, uid
        self.taker: int | None = None
        monkeypatch.setattr(connections, "proc_tables",
                            lambda: [_table(self.port, self.uid, self.taker)])
        monkeypatch.setattr(connections, "own_addresses", set)
        monkeypatch.setattr(connections, "own_sockets", lambda: {OURS}, raising=False)


def _chat(text: str) -> bytes:
    return json.dumps({"choices": [{"message": {"content": text}}]}).encode()


MODEL = {"via": "http", "base_url": "http://localhost:11434/v1", "model": "m", "timeout_s": 5}
ASK = [{"role": "user", "content": "the private question"}]


# --- H9: a server on this machine is the player's at the moment of sending ---------------------


def test_a_question_never_reaches_a_port_another_user_took_after_it_was_found(
    real_ours: Callable[..., bool], monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel = Kernel(monkeypatch, 11434, ME)
    seen: list[bytes] = []

    def server(request: httpx.Request) -> httpx.Response:
        seen.append(request.content)
        return wire(200, content=_chat("ok"))

    send = advisor.http_send(MODEL, transport=httpx.MockTransport(server))
    assert asyncio.run(send(ASK, 10)) == "ok", "the player's own server answers"
    kernel.uid = STRANGER  # the server stopped; another user took its port
    with pytest.raises(connections.NotOurs):
        asyncio.run(send(ASK, 10))
    kernel.uid = None  # nobody listens: someone could take it before the connection
    with pytest.raises(connections.NotOurs):
        asyncio.run(send(ASK, 10))
    assert len(seen) == 1, "nothing went out after the port changed hands"
    assert issubclass(connections.NotOurs, advisor.call_errors()), "the rules play the move"


def test_a_question_to_a_port_that_is_not_ours_says_why_in_one_sentence(
    real_ours: Callable[..., bool], monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    Kernel(monkeypatch, 11434, STRANGER)
    turn = asyncio.run(advisor.answer(MODEL, tmp_path, "q?", "facts", []))
    assert turn["ok"] is False and connections.NOT_OURS in turn["why"]


@pytest.mark.parametrize("where", [
    "http://localhost:8080/v1", "http://127.0.0.2:8080/v1", "http://[::1]:8080/v1",
    "http://0.0.0.0:8080/v1", "http://localhost/v1",
])
def test_a_typed_address_on_this_machine_is_asked_only_on_the_players_port(
    real_ours: Callable[..., bool], monkeypatch: pytest.MonkeyPatch, where: str,
) -> None:
    Kernel(monkeypatch, connections.port_of(where) or 0, STRANGER)
    asked: list[str] = []
    found = connections.models_at(where, lambda url, **_: asked.append(url))
    assert found == {"ok": False, "models": [], "why": connections.NOT_OURS}
    assert asked == [], "the list was not even asked for"


def test_over_https_the_certificate_guards_and_no_name_is_resolved(
    real_ours: Callable[..., bool], monkeypatch: pytest.MonkeyPatch,
) -> None:
    Kernel(monkeypatch, 443, STRANGER)
    monkeypatch.setattr(connections, "on_this_machine", lambda *_: pytest.fail("no lookup"))
    assert connections.ours("https://localhost/v1") and connections.ours("https://cosmergon.com")


def test_the_bounded_read_itself_reads_the_port_first(
    real_ours: Callable[..., bool], monkeypatch: pytest.MonkeyPatch,
) -> None:
    Kernel(monkeypatch, 1234, STRANGER)
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: pytest.fail("nothing may be sent"))
    with pytest.raises(connections.NotOurs):
        connections.bounded_get("http://localhost:1234/v1/models")


def test_this_machine_is_found_by_every_name_it_goes_by() -> None:
    def resolve(host: str, *_: Any) -> list[Any]:
        table = {"myhost": "127.0.1.1", "api.example": "93.184.216.34", "lan": "192.168.1.20"}
        if host not in table:
            raise OSError("no such name")
        return [(2, 1, 6, "", (table[host], 0))]

    def here(url: str) -> bool:
        return connections.on_this_machine(url, resolve, own=lambda: {"192.168.1.20"})

    assert here("http://localhost:1/v1") and here("http://x.localhost:1/v1")
    assert here("http://myhost:11434/v1"), "the machine's own name (127.0.1.1)"
    assert here("http://lan:11434/v1") and here("http://192.168.1.20:11434/v1"), "own address"
    assert here("http://[::ffff:127.0.0.1]:1/v1") and here("http://0.0.0.0:1/v1")
    assert not here("https://api.example/v1") and not here("http://192.168.1.50:1/v1")
    assert not here("http://unknown.example/v1"), "a name that does not resolve goes nowhere"
    assert connections.port_of("http://localhost/v1") == 80
    assert connections.port_of("https://localhost/v1") == 443


def test_the_world_on_a_port_of_another_user_gets_nothing(
    real_ours: Callable[..., bool], monkeypatch: pytest.MonkeyPatch,
) -> None:
    Kernel(monkeypatch, 8080, STRANGER)
    cfg = {"world": {"base_url": "http://127.0.0.1:8080"}}

    def never(*_: Any, **__: Any) -> Any:
        pytest.fail("nothing may go to that port")

    with pytest.raises(SystemExit, match="not yours"):
        brain.register(cfg, never)
    brain.write_private(brain.credentials_path(), {"agent_id": "a", "api_key": "AGENT-x:s"})
    assert brain.forget(cfg, never)["why"] == connections.NOT_OURS
    assert brain.world_rules(cfg, never) == "" and brain.world_forms(cfg, never) == {}
    with pytest.raises(SystemExit, match="not yours"):
        brain.make_agent(cfg)
    said = asyncio.run(brain.with_agent(cfg, never))
    assert said["why"] == connections.NOT_OURS


async def test_the_running_brain_reads_the_worlds_port_before_every_move(
    real_ours: Callable[..., bool], monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel = Kernel(monkeypatch, 8080, ME)
    moves: list[int] = []

    async def tick(*_: Any) -> dict[str, Any]:
        moves.append(1)
        kernel.uid = STRANGER  # while it plays, the world's port changes hands
        return {"action": "wait", "ok": True}

    async def sleep(_: float) -> None:
        return None

    monkeypatch.setattr(brain, "tick", tick)
    monkeypatch.setattr(brain.asyncio, "sleep", sleep)
    monkeypatch.setattr(brain, "learning_next", lambda *_: None)
    cfg = {"world": {"base_url": "http://127.0.0.1:8080"}, "brain": {"interval_s": 60}}
    await brain.run(cfg, SimpleNamespace(state=None), None)
    assert moves == [1], "no second move went to the other user's port"
    status = json.loads((brain.state_dir() / "status.json").read_text())
    assert status["mood"] == brain.ASLEEP and status["last"]["why"] == connections.NOT_OURS


def _world_on_the_wire(monkeypatch: pytest.MonkeyPatch, seen: list[str]) -> None:
    """The real SDK client, its transport replaced: what reaches the world is written down."""
    from cosmergon_agent import _http

    def world(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        return httpx.Response(200, json={"agent_id": "a", "tick": 5, "energy": 100.0})

    real = _http.async_client
    monkeypatch.setattr(
        _http, "async_client", lambda **kw: real(transport=httpx.MockTransport(world), **kw)
    )


async def test_a_move_after_the_models_answer_never_reaches_a_port_that_changed_hands(
    real_ours: Callable[..., bool], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review of 0.1.12 (#12584): the port was read before the model's answer, and the move went
    out after it. The SDK now asks ``world_send_check`` right before every request it sends.
    Red against ``38a587a``: there the move reached the other user's port."""
    kernel = Kernel(monkeypatch, 8080, ME)
    seen: list[str] = []
    _world_on_the_wire(monkeypatch, seen)

    class Thinking:
        async def decide(self, state: Any) -> tuple[str, dict[str, Any]]:
            kernel.uid = STRANGER  # while the model thinks, another user takes the world's port
            return ("place_cells", {"field_id": "f", "preset": "block"})

    cfg = {"world": {"base_url": "http://127.0.0.1:8080"}, "brain": {"interval_s": 60}}
    brain.write_private(brain.credentials_path(), {"agent_id": "a", "api_key": "AGENT-x:s"})
    said = await brain.with_agent(cfg, lambda agent: brain.tick(agent, Thinking()))
    assert seen == ["GET /api/v1/agents/a/state"], "only the look went out, the move did not"
    assert said["why"] == connections.NOT_OURS


def test_the_sdk_is_handed_the_check_of_the_moment_of_sending() -> None:
    cfg = {"world": {"base_url": "http://127.0.0.1:8080"}}
    brain.write_private(brain.credentials_path(), {"agent_id": "a", "api_key": "AGENT-x:s"})
    agent = brain.make_agent(cfg)
    assert agent._send_check is brain.world_send_check
    assert agent._peer_check is not None


def test_the_worlds_connection_is_judged_by_the_peer_it_really_reached(
    real_ours: Callable[..., bool], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same last word the brain's own calls have: a world on this machine is served only
    by a peer on this machine whose port is the player's (cosmergon-agent ``peer_check``)."""
    kernel = Kernel(monkeypatch, 8080, ME)
    check = brain.world_peer_check({"world": {"base_url": "http://127.0.0.1:8080"}})
    assert check("127.0.0.1", 8080) == ""
    assert check("93.184.216.34", 8080) == connections.PEER_ELSEWHERE, "a proxy, another name"
    kernel.uid = STRANGER
    assert check("127.0.0.1", 8080) == connections.NOT_OURS


@pytest.mark.parametrize(("world", "given_by", "opens"), [
    ("https://cosmergon.com", "https://cosmergon.com", True),
    ("https://cosmergon.com", "", True),  # a key from before 0.1.10 names no world
    ("http://127.0.0.1:8082", "http://127.0.0.1:8082", False),  # the terminal would go elsewhere
    ("https://cosmergon.com", "http://127.0.0.1:8082", False),  # a key of another world
])
def test_the_terminal_opens_only_for_a_shikigon_of_its_own_world(
    world: str, given_by: str, opens: bool,
) -> None:
    """K9: the terminal client talks only to cosmergon.com; until 0.1.12 it got any key."""
    creds = {"agent_id": "a", "api_key": "AGENT-x:s", **({"world": given_by} if given_by else {})}
    why = brain.terminal_refusal({"world": {"base_url": world}}, creds)
    assert (why == "") is opens
    if not opens:
        assert why == brain.TERMINAL_ELSEWHERE


# --- the class guard: every place that sends reads the port first -------------------------------

SENDERS = {"get", "post", "delete", "put", "patch", "stream", "request", "AsyncClient", "Client"}
CHECKS = {"ours", "world_refusal"}


def _own_body(fn: ast.AST) -> list[ast.AST]:
    """The nodes of a function, without those of functions defined inside it."""
    nodes: list[ast.AST] = []
    todo = list(ast.iter_child_nodes(fn))
    while todo:
        node = todo.pop()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            continue
        nodes.append(node)
        todo.extend(ast.iter_child_nodes(node))
    return nodes


def _name(call: ast.Call) -> str:
    func = call.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def _sends(call: ast.Call, params: set[str]) -> bool:
    func = call.func
    if isinstance(func, ast.Attribute) and getattr(func.value, "id", "") == "httpx":
        return func.attr in SENDERS
    name = getattr(func, "id", "")
    return name == "CosmergonAgent" or (name in params and name in {"get", "post", "delete"})


def _checked(call: ast.Call, checkers: set[str]) -> bool:
    """A call of a check, also one handed to a thread (``asyncio.to_thread(may_send, …)``)."""
    if _name(call) == "to_thread" and call.args:
        return getattr(call.args[0], "id", "") in checkers
    return _name(call) in checkers


def _checkers(trees: list[ast.AST]) -> set[str]:
    """``CHECKS`` and every function of the brain that calls one of them: a check moved into a
    function of its own stays a check, and one taken out of it leaves it none (derived)."""
    checkers = set(CHECKS)
    functions = [fn for tree in trees for fn in ast.walk(tree)
                 if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef)]
    while True:
        more = {fn.name for fn in functions if fn.name not in checkers
                and any(isinstance(n, ast.Call) and _checked(n, checkers) for n in _own_body(fn))}
        if not more:
            return checkers
        checkers |= more


def sending_functions(sources: dict[str, str] | None = None) -> dict[str, bool]:
    """Every function in the brain that opens a connection, and whether it reads the port
    first. Derived from the code, never a list kept by hand."""
    if sources is None:
        sources = {p.name: p.read_text(encoding="utf-8") for p in sorted(BRAIN.glob("*.py"))}
    trees = {name: ast.parse(text) for name, text in sources.items()}
    checkers = _checkers(list(trees.values()))
    found: dict[str, bool] = {}
    for name, tree in trees.items():
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            params = {a.arg for a in fn.args.args + fn.args.kwonlyargs}
            calls = [n for n in _own_body(fn) if isinstance(n, ast.Call)]
            sends = [c.lineno for c in calls if _sends(c, params)]
            if not sends:
                continue
            checks = [c.lineno for c in calls if _checked(c, checkers)]
            found[f"{name}:{fn.name}"] = bool(checks) and min(checks) < min(sends)
    return found


def test_every_place_that_sends_reads_the_port_first() -> None:
    found = sending_functions()
    # One door for the brain's own calls since 0.1.13 (``exchange``): ``bounded_call`` and the
    # advisor's ``send`` go through it, so it is the place that must read the port first.
    assert {"connections.py:exchange", "shikigon_brain.py:register",
            "shikigon_brain.py:make_agent"} <= set(found), sorted(found)
    assert all(found.values()), [where for where, ok in found.items() if not ok]


def test_the_guard_turns_red_on_a_place_that_sends_unchecked(tmp_path: Path) -> None:
    sources = {p.name: p.read_text(encoding="utf-8") for p in sorted(BRAIN.glob("*.py"))}
    source = sources["connections.py"]
    unchecked = source.replace(
        "    if not ours(url, with_key=with_key):\n        raise NotOurs(NOT_OURS)\n", "", 1)
    assert unchecked != source
    assert sending_functions(sources)["connections.py:exchange"], "checked as it stands"
    found = sending_functions({**sources, "connections.py": unchecked})
    assert found["connections.py:exchange"] is False, "red once the check is gone"


def test_no_unbounded_reader_is_handed_on() -> None:
    """``httpx.get``/``post``/``delete`` read a whole answer, unpacked; the brain hands on
    ``bounded_get``, ``bounded_post`` and ``bounded_delete`` instead (K6, K29)."""
    whole = {"get", "post", "delete", "put", "patch", "request", "stream"}
    for path in BRAIN.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Attribute) and getattr(node.value, "id", "") == "httpx":
                assert node.attr not in whole, f"{path.name}:{node.lineno} httpx.{node.attr}"


# --- K9: the world's key goes only to the world that gave it ------------------------------------


def _registered(post_url: list[str]) -> Callable[..., Any]:
    def post(url: str, **_: Any) -> Any:
        post_url.append(url)
        body = {"agent_id": "a1", "api_key": "AGENT-a1:secret", "expires_at": ""}
        return SimpleNamespace(status_code=200, json=lambda: body)

    return post


def test_the_worlds_key_goes_only_to_the_world_that_gave_it() -> None:
    brain.register({"world": {"base_url": "https://world.test/"}}, _registered([]))
    assert brain.load_credentials()["world"] == "https://world.test:443"
    other = {"world": {"base_url": "https://other.test"}}

    def never(*_: Any, **__: Any) -> Any:
        pytest.fail("the key may not go to another world")

    with pytest.raises(SystemExit, match="world.test") as said:
        brain.make_agent(other)
    assert "secret" not in str(said.value)
    assert "world.test" in brain.forget(other, never)["why"]
    assert "world.test" in asyncio.run(brain.with_agent(other, never))["why"]
    brain.make_agent({"world": {"base_url": "https://world.test"}})  # its own world: fine


def test_a_key_from_before_names_no_world_and_is_bound_to_the_one_it_goes_to_first() -> None:
    brain.write_private(brain.credentials_path(), {"agent_id": "a", "api_key": "AGENT-a:s"})
    brain.make_agent({"world": {"base_url": "https://cosmergon.com"}})
    assert brain.load_credentials()["world"] == "https://cosmergon.com:443"
    with pytest.raises(SystemExit, match="cosmergon.com"):
        brain.make_agent({"world": {"base_url": "https://other.test"}})


# --- K6: an answer is read only up to its limit -------------------------------------------------


def test_a_model_answer_is_read_only_up_to_its_limit() -> None:
    huge = _chat("x" * (connections.ANSWER_BYTES + 1))
    send = advisor.http_send(
        MODEL, transport=httpx.MockTransport(lambda request: wire(200, content=huge)))
    with pytest.raises(connections.TooLong):
        asyncio.run(send(ASK, 10))
    assert issubclass(connections.TooLong, advisor.call_errors()), "the rules play the move"


def test_the_worlds_rules_are_read_bounded() -> None:
    # A real server that never ends its answer: the way over a socket (the stub of httpx.stream
    # used before measured a body already in memory, cos20 #492).
    with hostile(endless) as (address, _):
        assert brain.world_rules({"world": {"base_url": address}}, connections.bounded_get) == ""


# --- K12: a model name is a name, never an option ------------------------------------------------


def test_a_model_name_is_never_an_option_of_the_agents_command() -> None:
    with pytest.raises(connections.AgentError, match="cannot start with"):
        connections.agent_argv({"agent": "pi", "agent_model": "--tools=bash"})
    argv = connections.agent_argv({"agent": "pi", "agent_model": "ollama/qwen3:4b"})
    assert argv[-2:] == ["--model", "ollama/qwen3:4b"]


# --- K14: on a timeout the agent ends with everything it started ---------------------------------


def _alive(pid: int) -> bool:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    return stat.rsplit(")", 1)[1].split()[0] != "Z"  # a zombie has ended


def test_on_a_timeout_the_agent_ends_with_everything_it_started(tmp_path: Path) -> None:
    pidfile = tmp_path / "child"
    argv = ["sh", "-c", f"sleep 30 & echo $! > {pidfile}; wait"]
    with pytest.raises(subprocess.TimeoutExpired):
        connections.run_in_group(argv, input="", timeout=0.5)
    child = int(pidfile.read_text())
    for _ in range(20):
        if not _alive(child):
            break
        time.sleep(0.05)
    assert not _alive(child), "the agent's own child ended with it"
    assert connections.run_agent.__defaults__ is not None
    assert connections.run_in_group in connections.run_agent.__defaults__


# --- §1b: the brain's stderr reaches the bar cut, line by line ----------------------------------


def test_a_line_on_the_brains_stderr_is_cut_before_the_bar_keeps_it() -> None:
    import io

    out = io.StringIO()
    cut = brain.CutLines(out)
    cut.write("x" * 10_000)
    cut.write("y" * 10 + "\nshort\n" + "z" * 500)
    lines = out.getvalue().split("\n")
    assert [len(line) for line in lines] == [200, 5, 200]
    assert lines[1] == "short"
    source = (BRAIN / "shikigon_brain.py").read_text(encoding="utf-8")
    assert "sys.stderr = CutLines(sys.stderr)" in source, "the running brain uses it"

