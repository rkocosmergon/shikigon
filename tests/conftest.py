"""Shared by all tests: no test reaches a real port on this machine.

Whose a port on this machine is, is read from the kernel right before each sending (H9). The
tests stand in for the network with stubs and transports, and nothing listens on their
addresses, so here every address counts as the player's. The tests of H9 itself take the real
check back (fixture ``real_ours``) and give it a kernel table of their own.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

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
