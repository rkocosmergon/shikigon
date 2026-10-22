"""Shared by all tests: no test reaches a real port on this machine.

Whose a port on this machine is, is read from the kernel right before each sending (H9). The
tests stand in for the network with stubs and transports, and nothing listens on their
addresses, so here every address counts as the player's. The tests of H9 itself take the real
check back (fixture ``real_ours``) and give it a kernel table of their own.
"""

from __future__ import annotations

import contextlib
import json as _json
import sys
import threading
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "brain"))

import connections

# ``None`` only in the counter-test against 0.1.9, which had no check when sending: each test
# of H9 is then red on its own, not the whole run at this import.
REAL_OURS: Callable[..., bool] | None = getattr(connections, "ours", None)


@pytest.fixture(autouse=True)
def _ports_are_the_players(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(connections, "ours", lambda base_url, *_, **__: True, raising=False)


@pytest.fixture
def real_ours(monkeypatch: pytest.MonkeyPatch) -> Callable[..., bool] | None:
    monkeypatch.setattr(connections, "ours", REAL_OURS, raising=False)
    return REAL_OURS


class _OnTheWire(httpx.SyncByteStream, httpx.AsyncByteStream):
    """A body in pieces, as it comes over a connection."""

    def __init__(self, data: bytes) -> None:
        self._parts = [data[i : i + 65536] for i in range(0, len(data), 65536)] or [b""]

    def __iter__(self) -> Iterator[bytes]:
        yield from self._parts

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for part in self._parts:
            yield part


def wire(status: int = 200, *, content: bytes = b"", json: object = None,
         headers: dict[str, str] | None = None) -> httpx.Response:
    """``httpx.Response`` as a transport hands it on: its body not yet read.

    ``httpx.Response(content=...)`` reads and unpacks its body when it is made, before any
    hook or count of the brain, so a test built on it measured the bound on a body already in
    memory (cos20 #492: the limit passed with the old transport stubs, and the reviewer found it
    passed on the wire)."""
    body = content if json is None else _json.dumps(json).encode()
    return httpx.Response(status, headers=headers or {}, stream=_OnTheWire(body))


@contextmanager
def hostile(respond: Callable[[BaseHTTPRequestHandler], None]) -> Iterator[tuple[str, list[dict]]]:
    """A real HTTP server on ``127.0.0.1`` that answers with ``respond``: the way over a socket
    and a transport, the one the reviewer looked at (§0 rule 4: misuse is measured). Yields its
    address and the headers of every request it got."""
    seen: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def _any(self) -> None:
            seen.append({k.lower(): v for k, v in self.headers.items()})
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            with contextlib.suppress(OSError):
                respond(self)

        do_GET = do_POST = do_DELETE = _any  # the names http.server calls

        def log_message(self, *_: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", seen
    finally:
        server.shutdown()
        server.server_close()


def sends(body: bytes, coding: str = "") -> Callable[[BaseHTTPRequestHandler], None]:
    """An answer of ``body``, marked as encoded with ``coding``."""

    def respond(handler: BaseHTTPRequestHandler) -> None:
        handler.send_response(200)
        if coding:
            handler.send_header("Content-Encoding", coding)
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    return respond


def endless(handler: BaseHTTPRequestHandler) -> None:
    """An answer that never ends (no length; HTTP/1.0 reads to the close)."""
    handler.send_response(200)
    handler.end_headers()
    while True:
        handler.wfile.write(b"x" * 65536)
