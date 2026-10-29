"""A model server whose certificate comes from the player's own CA (cos20 #599).

The brain's door checks https against the system's trust store, where Arch keeps a CA the
player added (``trust anchor --store``), not against certifi alone. The tests go over real TLS:
a stand-in transport (``transport=``) leaves httpx's ``verify`` unused, so a test on it would
never see which store was asked. The CA is made for each run with the ``openssl`` program and
handed to OpenSSL through ``SSL_CERT_FILE``, the variable OpenSSL reads when it loads its
default places. Until 0.1.14 a server on this machine was asked with certifi only
(``trust_env=False``) and the first test was red.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import ssl
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "brain"))

import advisor
import connections

MODELS = {"data": [{"id": "m"}]}


def openssl(*args: str, cwd: Path) -> None:
    subprocess.run(["openssl", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture(scope="module")
def own_ca(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A CA of the player's own and a certificate for ``127.0.0.1`` signed by it."""
    assert shutil.which("openssl"), "the openssl program makes the test CA"
    d = tmp_path_factory.mktemp("own_ca")
    curve = ("-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes")
    openssl("req", "-x509", *curve, "-keyout", "ca.key", "-out", "ca.pem", "-days", "2",
            "-subj", "/CN=Shikigon test CA", "-addext", "basicConstraints=critical,CA:TRUE",
            "-addext", "keyUsage=critical,keyCertSign,cRLSign", cwd=d)
    openssl("req", *curve, "-keyout", "server.key", "-out", "server.csr", "-subj", "/CN=127.0.0.1",
            cwd=d)
    (d / "server.ext").write_text(
        "subjectAltName=IP:127.0.0.1\nbasicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth\n"
        "subjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid\n", encoding="utf-8")
    openssl("x509", "-req", "-in", "server.csr", "-CA", "ca.pem", "-CAkey", "ca.key",
            "-CAcreateserial", "-out", "server.pem", "-days", "2", "-extfile", "server.ext",
            cwd=d)
    return d


@contextmanager
def model_server(ca: Path) -> Iterator[str]:
    """A real https server on ``127.0.0.1`` with the certificate of ``own_ca``; it lists one
    model. Yields its ``/v1`` address."""

    class Handler(BaseHTTPRequestHandler):
        def _any(self) -> None:
            body = json.dumps(MODELS).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = _any  # the names http.server calls

        def log_message(self, *_: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(ca / "server.pem", ca / "server.key")
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"https://127.0.0.1:{server.server_address[1]}/v1"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def ca_in_store(own_ca: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The player's CA where OpenSSL looks for trusted ones."""
    monkeypatch.setenv("SSL_CERT_FILE", str(own_ca / "ca.pem"))
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    return own_ca


@pytest.fixture
def ca_not_in_store(own_ca: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The system's store as it is: the test CA is not in it."""
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    return own_ca


def test_a_model_server_with_the_players_own_ca_lists_its_models(ca_in_store: Path) -> None:
    with model_server(ca_in_store) as base_url:
        found = connections.models_at(base_url, connections.bounded_get)
    assert found == {"ok": True, "models": ["m"], "why": ""}, found


def test_a_certificate_of_a_ca_not_in_the_store_is_still_refused(ca_not_in_store: Path) -> None:
    """The check stays sharp: the store is where trust comes from, not a way around it."""
    with model_server(ca_not_in_store) as base_url:
        found = connections.models_at(base_url, connections.bounded_get)
        with pytest.raises(Exception) as raised:
            connections.bounded_get(base_url + "/models")
    assert found["ok"] is False
    assert found["why"] == connections.UNTRUSTED, found
    assert connections.untrusted(raised.value)
    assert connections.failed_why(raised.value) == connections.UNTRUSTED_SHORT


def test_an_answer_says_why_the_server_was_not_trusted(
    ca_not_in_store: Path, tmp_path: Path
) -> None:
    """A question the player asks: the panel says what to do, not the kind of error."""
    with model_server(ca_not_in_store) as base_url:
        conf = {"via": "http", "base_url": base_url, "model": "m", "timeout_s": 5}
        turn = asyncio.run(advisor.answer(conf, tmp_path, "which move?", "", [],
                                          send=advisor.http_send(conf, home=tmp_path)))
    assert turn["ok"] is False
    assert turn["why"] == f"no answer ({connections.UNTRUSTED})", turn


def test_other_failures_keep_their_kind() -> None:
    """Only a certificate the store does not vouch for gets the sentence."""
    assert not connections.untrusted(ConnectionRefusedError())
    assert connections.failed_why(ConnectionRefusedError()) == "ConnectionRefusedError"
