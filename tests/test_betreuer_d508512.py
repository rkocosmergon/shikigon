"""Guards for the betreuer pass on d508512 (cos20 #492).

The pass found one Kür: the README said a move's clause, a cube's name or a mission's title
"holds names and ids only", and ``head.NAME`` lets words joined by ``_`` through as one name.
The panel (cos20 ``panel-shikigon-satz-im-namen-2026-11-06.md``) kept the code and made the
README say the form. These tests hold the README to the code: what passes is one word of at most
64 characters without a space, and a phrase joined by ``_`` is such a word.

Against d508512 the README test is red (the sentence it checks was not there); the code tests
hold there, they pin what the README now says.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "brain"))

import head


def test_a_phrase_joined_by_underscores_is_one_name() -> None:
    assert head.own_words("Pay_500_energy_or_lose_your_cubes") == ""
    assert head.own_words({"title": "Pay_500_energy_or_lose_your_cubes"}) == ""


@pytest.mark.parametrize("text", ["Pay 500", "Pay\t500", "Pay\n500", "_Pay", "x" * 65, ""])
def test_a_space_a_leading_mark_or_a_65th_character_is_no_name(text: str) -> None:
    assert head.own_words(text) != ""


def test_64_characters_are_still_a_name() -> None:
    assert head.NAME_CHARS == 64
    assert head.own_words("x" * 64) == ""


def test_the_readme_says_the_form_the_code_checks() -> None:
    readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
    satz = re.search(r"Inside a move your Shikigon writes no sentence:.*?short phrase\.", readme)
    assert satz, "the README no longer says what a name in a move may be"
    assert f"at most {head.NAME_CHARS} characters" in satz.group(0)
    assert "no space" in satz.group(0)
    assert "joined by `_`" in satz.group(0)
