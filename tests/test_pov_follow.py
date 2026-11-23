"""The follower of the terminal's first-person view: ``brain/pov_follow.py`` and its start in
``brain/shikigon-terminal``.

The pure core (``read_place``, ``read_pad``, ``target``, ``decide``) is tested with the numbers
read on the device (foot window ``at [12,55] size [1416,833]``, 115 x 30 cells of 12 x 27 px,
``pad=14x4``). The loop runs against a fake Hyprland: two AF_UNIX sockets in a thread of this test
that answer the requests from a dict and write them down. Nothing here touches a real Hyprland:
the request strings beyond those verified on the device are marked in ``pov_follow.py``'s report.
Each test names in a comment what it guards.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import select
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

BRAIN = Path(__file__).resolve().parent.parent / "brain"
sys.path.insert(0, str(BRAIN))

import pov_follow as pf

TERMINAL_SCRIPT = BRAIN / "shikigon-terminal"

# --- the numbers of the device ------------------------------------------------------------------

DASH, FOOT, REND, FOREIGN = 4242, 4100, 4300, 5000
TREE: dict[int, int | None] = {DASH: FOOT, REND: DASH, FOREIGN: 1, FOOT: 1}
REND_ADDR = "0x5650d1d7b120"
FOREIGN_ADDR = "0x5650d1d7c340"
PAD = (14, 4, False)
TARGET = pf.Rect(12 + 14 + 58 * 12, 55 + 4 + 2 * 27, 56 * 12, 14 * 27)  # 722, 113, 672, 378
LINE = ('{"v":1,"pid":4242,"visible":true,"col":58,"row":2,"cols":56,"rows":14,"cell_w":12,'
        '"cell_h":27,"term_cols":115,"term_rows":30,"text_w":1380,"text_h":810}')
PLACE = pf.read_place(LINE)
assert PLACE is not None


def place_with(**changes: Any) -> str:
    data = json.loads(LINE)
    data.update(changes)
    return json.dumps(data, separators=(",", ":"))


def client(address: str, cls: str, pid: int, at: Any, size: Any, ws: tuple[int, str] = (1, "1"),
           **more: Any) -> dict[str, Any]:
    """A client as ``hyprctl -j clients`` gives it (the keys decide reads)."""
    return {"address": address, "at": at, "size": size, "pid": pid, "class": cls,
            "floating": False, "fullscreen": 0, "hidden": False, "mapped": True, "monitor": 0,
            "workspace": {"id": ws[0], "name": ws[1]}, **more}  # fmt: skip


def terminal(size: Any = None, **more: Any) -> dict[str, Any]:
    return client("0x5650d1d00001", "org.omarchy.cosmergon", FOOT, [12, 55],
                  [1416, 833] if size is None else size, **more)


def renderer(at: Any = None, size: Any = None, ws: tuple[int, str] = (1, "1"),
             **more: Any) -> dict[str, Any]:
    at = [TARGET.x, TARGET.y] if at is None else at
    size = [TARGET.w, TARGET.h] if size is None else size
    return client(REND_ADDR, pf.RENDERER_CLASS, REND, at, size, ws, **more)


def monitors(active: int = 1, special: int = 0, scale: Any = 1.0) -> list[dict[str, Any]]:
    return [{"id": 0, "scale": scale, "activeWorkspace": {"id": active, "name": str(active)},
             "specialWorkspace": {"id": special, "name": "" if not special else "special:x"}}]


PARKED = (-98, pf.PARKING)
RESIZE = (f'dispatch hl.dsp.window.resize({{ x = {TARGET.w}, y = {TARGET.h}, '
          f'window = "address:{REND_ADDR}" }})')
MOVE = (f'dispatch hl.dsp.window.move({{ x = {TARGET.x}, y = {TARGET.y}, '
        f'window = "address:{REND_ADDR}" }})')
PARK = (f'dispatch hl.dsp.window.move({{ workspace = "{pf.PARKING}", follow = false, '
        f'window = "address:{REND_ADDR}" }})')
HOME = (f'dispatch hl.dsp.window.move({{ workspace = "1", follow = false, '
        f'window = "address:{REND_ADDR}" }})')


def decide(clients: list[Any], mons: Any = None, place: pf.Place | None = None,
           pad: tuple[int, int, bool] = PAD) -> list[str]:
    return pf.decide(place or PLACE, clients, monitors() if mons is None else mons, pad, TREE.get)


# --- read_place ---------------------------------------------------------------------------------


def test_the_real_line_of_the_dashboard_is_read() -> None:
    """Guards: the one line the dashboard writes (device numbers) is accepted, field by field."""
    assert PLACE == pf.Place(pid=4242, visible=True, col=58, row=2, cols=56, rows=14, cell_w=12,
                             cell_h=27, term_cols=115, term_rows=30, text_w=1380, text_h=810)
    assert pf.read_place(LINE.encode()) == PLACE, "bytes as read from the file"
    assert pf.read_place(LINE + "\n") == PLACE


def test_a_hidden_view_with_zeros_is_read() -> None:
    """Guards: ``visible: false`` with the four area numbers 0 is a valid line (it is how the
    view ends before the file is removed)."""
    text = place_with(visible=False, col=0, row=0, cols=0, rows=0)
    got = pf.read_place(text)
    assert got is not None and got.visible is False


def _drop(key: str) -> str:
    data = json.loads(LINE)
    del data[key]
    return json.dumps(data)


@pytest.mark.parametrize("text", [
    place_with(extra=1),  # an extra key
    _drop("text_h"),  # a missing key
    place_with(cell_w=12.0),  # a float where a whole number is
    place_with(col=True),  # a bool is no int
    place_with(visible=1),  # visible is a bool
    place_with(v=True),
    place_with(v=1.0),
    place_with(v=2),
    place_with(col=-1),
    place_with(term_cols=2001),
    place_with(cell_w=201),
    place_with(cell_h=0),
    place_with(text_w=20001),
    place_with(pid=1),
    place_with(pid=4_194_305),
    place_with(pid="4242"),
    place_with(col=60),  # 60 + 56 > 115
    place_with(row=17),  # 17 + 14 > 30
    place_with(text_w=1379),  # 12 * 115 > 1379
    place_with(text_h=809),  # 27 * 30 > 809
    LINE[:-1] + "," + " " * 4100 + "}",  # 4096 bytes and more
    "[]", "null", "1", '"x"', "{", "", "{}",
    '{"v":1,"v":1}',
])
def test_a_line_that_is_not_exactly_the_dashboards_is_refused(text: str) -> None:
    """Guards: ``read_place`` is strict (every kind of wrong gives ``None``, nothing raises)."""
    assert pf.read_place(text) is None


def test_a_line_of_exactly_4096_bytes_is_read_and_one_more_is_not() -> None:
    """Guards: the size bound is 4096 bytes, counted in bytes (not characters)."""
    base = LINE[:-1]
    ok = base + " " * (4096 - len(base) - 1) + "}"
    assert len(ok.encode()) == 4096 and pf.read_place(ok) == PLACE
    assert pf.read_place(ok + " ") is None
    assert pf.read_place(b"\xff\xfe" + LINE.encode()) is None, "not UTF-8"
    assert pf.read_place(LINE + "\x00") is None


def test_a_deeply_nested_line_is_refused_not_raised() -> None:
    """Guards: nesting that breaks the parser's recursion ends as ``None``."""
    assert pf.read_place("[" * 3000) is None
    assert pf.read_place('{"v":' + "[" * 3000 + "]" * 3000 + "}") is None


# --- read_pad -----------------------------------------------------------------------------------


def test_the_pad_of_the_device_is_read() -> None:
    """Guards: the device's foot.ini gives ``(14, 4, False)``."""
    assert pf.read_pad("font=JetBrainsMono Nerd Font:size=15\n[main]\npad=14x4\n") == PAD
    assert pf.read_pad("[main]\nfont=JetBrainsMono Nerd Font:size=15\npad=14x4\n") == PAD


@pytest.mark.parametrize(("text", "want"), [
    ("[main]\npad=14x4 center\n", (14, 4, True)),
    ("[main]\npad = 14x4\n", (14, 4, False)),
    ("[main]\n  pad   =   2x3   center  \n", (2, 3, True)),
    ("[main]\npad=1x1\npad=5x6\n", (5, 6, False)),  # the last one wins
    ("[main]\npad=1x1\n[colors]\npad=5x6\n", (1, 1, False)),  # another section
    ("[colors]\npad=5x6\n", (0, 0, False)),
    ("pad=5x6\n[main]\n", (0, 0, False)),  # before any section
    ("[main]\n# pad=9x9\npad=3x3\n", (3, 3, False)),  # a comment
    ("[main]\n#pad=9x9\n", (0, 0, False)),
    ("[main]\npad=501x4\n", (0, 0, False)),  # out of bounds
    ("[main]\npad=4x501\n", (0, 0, False)),
    ("[main]\npad=500x500\n", (500, 500, False)),
    ("[main]\npad=3x3\npad=garbage\n", (3, 3, False)),  # garbage keeps the earlier one
    ("[main]\npad=-3x3\n", (0, 0, False)),
    ("[main]\npad=3x3 middle\n", (0, 0, False)),
    ("[main]\npad=3x\n", (0, 0, False)),
    ("", (0, 0, False)),
    ("\x00\x01 garbage [[[", (0, 0, False)),
])
def test_read_pad(text: str, want: tuple[int, int, bool]) -> None:
    """Guards: the pad rules (section, last wins, comments, bounds, garbage)."""
    assert pf.read_pad(text) == want


# --- target -------------------------------------------------------------------------------------


def test_the_target_with_the_numbers_of_the_device() -> None:
    """Guards: x = terminal x + pad + col*cell_w, y likewise, w = cols*cell_w, h = rows*cell_h."""
    assert pf.target(PLACE, (12, 55), (1416, 833), PAD) == pf.Rect(
        12 + 14 + 58 * 12, 55 + 4 + 2 * 27, 56 * 12, 14 * 27)
    assert TARGET == pf.Rect(722, 113, 672, 378)


def test_the_target_with_center_uses_half_of_the_free_room() -> None:
    """Guards: ``center`` margin = (window - text) // 2, not the pad."""
    got = pf.target(PLACE, (12, 55), (1416, 833), (14, 4, True))
    assert got == pf.Rect(12 + (1416 - 1380) // 2 + 696, 55 + (833 - 810) // 2 + 54, 672, 378)
    odd = pf.target(PLACE, (0, 0), (1417, 834), (0, 0, True))
    assert odd is not None and odd.x == (1417 - 1380) // 2 + 696


@pytest.mark.parametrize(("at", "size"), [
    ((12, 55), (1300, 833)),  # the window is narrower than the area reaches
    ((12, 55), (1416, 400)),  # lower than it reaches
    ((12, 55), (0, 0)),
])
def test_an_area_outside_the_window_has_no_target(at: tuple[int, int],
                                                  size: tuple[int, int]) -> None:
    """Guards: a rectangle that is not inside the terminal window gives ``None``."""
    assert pf.target(PLACE, at, size, PAD) is None
    assert pf.target(PLACE, at, (1416, 833), PAD) is not None, "control: the full window"


def test_an_empty_area_has_no_target() -> None:
    """Guards: width or height 0 is no rectangle."""
    empty = pf.read_place(place_with(cols=0, rows=0))
    assert empty is not None and pf.target(empty, (12, 55), (1416, 833), PAD) is None


# --- decide: where the renderer belongs ---------------------------------------------------------


def test_a_renderer_already_in_place_gets_no_request() -> None:
    """Guards: nothing is sent when the window is where it belongs (the idle case)."""
    assert decide([terminal(), renderer()]) == []


def test_a_renderer_in_the_wrong_place_gets_one_batch_resize_before_move() -> None:
    """Guards: one request, the resize BEFORE the move (a resize keeps the centre), the numbers
    from the target."""
    got = decide([terminal(), renderer(at=[0, 0], size=[100, 100])])
    assert got == ["[[BATCH]]" + RESIZE + ";" + MOVE]
    assert got[0].index("resize") < got[0].index("move")


def test_only_what_differs_is_sent() -> None:
    """Guards: a wrong size alone gives a resize alone, a wrong position a move alone."""
    assert decide([terminal(), renderer(size=[100, 100])]) == [RESIZE]
    assert decide([terminal(), renderer(at=[0, 0])]) == [MOVE]


def test_the_view_that_is_not_wanted_waits_on_the_special_workspace() -> None:
    """Guards: ``visible: false`` parks the window; once parked, nothing more is sent."""
    place = pf.read_place(place_with(visible=False, col=0, row=0, cols=0, rows=0))
    assert decide([terminal(), renderer()], place=place) == [PARK]
    assert decide([terminal(), renderer(ws=PARKED)], place=place) == []


def test_another_active_workspace_parks_the_renderer() -> None:
    """Guards: the terminal is not on the workspace its monitor shows: park, and only once."""
    assert decide([terminal(), renderer()], monitors(active=2)) == [PARK]
    assert decide([terminal(), renderer(ws=PARKED)], monitors(active=2)) == []


def test_a_terminal_that_is_hidden_or_not_mapped_parks_the_renderer() -> None:
    """Guards: the terminal must be mapped and not hidden."""
    assert decide([terminal(hidden=True), renderer()]) == [PARK]
    assert decide([terminal(mapped=False), renderer()]) == [PARK]


def test_back_on_the_workspace_the_renderer_returns_and_is_placed() -> None:
    """Guards: from the special workspace back to the terminal's workspace, then size, then
    position, in one batch (the move to the workspace first)."""
    got = decide([terminal(), renderer(ws=PARKED, at=[5, 5], size=[50, 50])])
    assert got == ["[[BATCH]]" + HOME + ";" + RESIZE + ";" + MOVE]
    again = decide([terminal(), renderer(ws=PARKED)])
    assert again == ["[[BATCH]]" + HOME + ";" + RESIZE + ";" + MOVE], "re-placed after the move"


def test_the_renderer_on_another_normal_workspace_is_brought_home() -> None:
    """Guards: a renderer left on workspace 2 follows its terminal back to workspace 1."""
    got = decide([terminal(), renderer(ws=(2, "2"))])
    assert got[0].startswith("[[BATCH]]" + HOME)


def test_the_terminal_on_a_shown_special_workspace_counts_as_shown() -> None:
    """Guards: ``or the monitor's shown special workspace`` of the rule; the name is the
    terminal's workspace."""
    special = (-99, "special:term")
    mons = monitors(active=2, special=-99)
    got = decide([terminal(ws=special), renderer(ws=PARKED)], mons)
    assert got[0].startswith("[[BATCH]]" + HOME.replace('"1"', '"special:term"'))


def test_a_fullscreen_window_of_another_hides_the_view() -> None:
    """Guards: another client fullscreen on the terminal's workspace parks the renderer; one on
    another workspace, an unmapped one, and the terminal itself do not."""
    other = client("0x5650d1d00099", "firefox", 7000, [0, 0], [1920, 1080], fullscreen=2)
    assert decide([terminal(), renderer(), other]) == [PARK]
    assert decide([terminal(), renderer(), {**other, "workspace": {"id": 2, "name": "2"}}]) == []
    assert decide([terminal(), renderer(), {**other, "mapped": False}]) == []
    assert decide([terminal(fullscreen=2), renderer()]) == [], "the terminal itself may be"
    assert decide([terminal(), renderer(fullscreen=2)]) == [], "the renderer itself may be"


def test_a_maximized_window_is_no_fullscreen() -> None:
    """Guards: ``fullscreen`` 1 (maximized) of another window does not park the view; 2 and 3 and
    a bool true (older Hyprland) do; a value that is none of them counts as fullscreen. A terminal
    that is maximized or fullscreen keeps its view in place."""
    other = client("0x5650d1d00099", "firefox", 7000, [0, 0], [1920, 1080])
    assert decide([terminal(), renderer(), {**other, "fullscreen": 1}]) == []
    for mode in (2, 3, True, 7, -1):
        assert decide([terminal(), renderer(), {**other, "fullscreen": mode}]) == [PARK], mode
    for mode in (1, 2, 3):
        assert decide([terminal(fullscreen=mode), renderer()]) == [], mode
        got = decide([terminal(fullscreen=mode), renderer(at=[0, 0])])
        assert got == [MOVE], "and it is still placed"


def test_a_scale_that_is_no_plausible_number_does_nothing() -> None:
    """Guards: a scale that is no number, a bool, nan, 0 or outside 0.5 to 4: no request, not
    even a park; nor a monitor to read it from."""
    bad = renderer(at=[0, 0], size=[1, 1])
    for scale in (None, "1", True, float("nan"), 0, 0.25, 4.5, -1):
        assert decide([terminal(), bad], monitors(scale=scale)) == [], scale
    assert decide([terminal(), bad], monitors(scale=None),
                  pf.read_place(place_with(visible=False))) == []
    assert decide([terminal(), bad], []) == [], "no monitor to read the scale from"


def test_a_scale_of_one_is_followed_as_int_or_float() -> None:
    """Guards: control for the test above: 1 and 1.0 are followed."""
    bad = renderer(at=[0, 0], size=[1, 1])
    assert decide([terminal(), bad], monitors(scale=1)) != []
    assert decide([terminal(), bad], monitors(scale=1.0)) != []


def test_a_scale_is_read_as_the_exact_fraction() -> None:
    """Guards: Hyprland prints 5/3 as 1.666667 and 1.25 as 1.25: both become the fraction."""
    assert pf._scale(1.666667) == Fraction(5, 3)
    assert pf._scale(1.25) == Fraction(5, 4) and pf._scale(2) == Fraction(2)


# At a scale the window is in logical pixels (physical / scale); the dashboard's numbers are
# physical. Device-like numbers at 1.5: foot 944 x 555 logical = 1416 x 832.5 physical.
@pytest.mark.parametrize("scale", [Fraction(5, 4), Fraction(3, 2), Fraction(5, 3), Fraction(2)])
def test_the_target_at_a_scale_lies_inside_the_area_and_leaves_no_more_than_a_pixel(
        scale: Fraction) -> None:
    """Guards: logical rectangle = physical area / scale, start rounded up, end rounded down: the
    rectangle never stands beyond the area (checked back in physical pixels) and is smaller than
    the area by less than 2 logical pixels per side; the pad is taken as physical, too."""
    at, size = (10, 20), (round(1416 / scale), round(833 / scale))
    got = pf.target(PLACE, at, size, PAD, scale)
    assert got is not None
    phys_x0 = (got.x - at[0]) * scale - round(14 * scale)
    phys_y0 = (got.y - at[1]) * scale - round(4 * scale)
    assert phys_x0 >= 58 * 12 and phys_y0 >= 2 * 27, "starts at or after the area"
    assert phys_x0 + got.w * scale <= (58 + 56) * 12, "ends at or before it"
    assert phys_y0 + got.h * scale <= (2 + 14) * 27
    assert got.w >= 56 * 12 / scale - 2 and got.h >= 14 * 27 / scale - 2


def test_the_target_at_scale_two_is_exact_half() -> None:
    """Guards: scale 2: pad 14x4 is 28x8 physical; 58 cells of 12 are 696 physical; the logical
    rectangle is half of it, no rounding at all."""
    got = pf.target(PLACE, (12, 55), (708, 417), PAD, Fraction(2))
    assert got == pf.Rect(12 + (28 + 696) // 2, 55 + (8 + 54) // 2, 336, 189)


def test_the_target_at_a_scale_with_center_uses_the_physical_free_room() -> None:
    """Guards: ``center`` at 1.5: the free room is measured in physical pixels (window * scale -
    text), the result converted back."""
    got = pf.target(PLACE, (0, 0), (944, 555), (0, 0, True), Fraction(3, 2))
    pad_x, pad_y = (round(944 * 1.5) - 1380) // 2, (round(555 * 1.5) - 810) // 2
    assert got == pf.Rect(math.ceil((pad_x + 696) / 1.5), math.ceil((pad_y + 54) / 1.5),
                          math.floor((pad_x + 696 + 672) / 1.5) - math.ceil((pad_x + 696) / 1.5),
                          math.floor((pad_y + 54 + 378) / 1.5) - math.ceil((pad_y + 54) / 1.5))


def test_the_renderer_is_placed_at_a_scale() -> None:
    """Guards: decide at monitor scale 1.5 sends the logical rectangle (the old code sent
    nothing at a scale other than 1)."""
    resize = (f'dispatch hl.dsp.window.resize({{ x = 448, y = 252, '
              f'window = "address:{REND_ADDR}" }})')
    move = (f'dispatch hl.dsp.window.move({{ x = 490, y = 95, '
            f'window = "address:{REND_ADDR}" }})')  # 12 + ceil(717 / 1.5), 55 + ceil(60 / 1.5)
    got = decide([terminal(size=[944, 555]), renderer(at=[0, 0], size=[1, 1])],
                 monitors(scale=1.5))
    assert got == ["[[BATCH]]" + resize + ";" + move]


def test_a_scaled_renderer_in_place_gets_no_request() -> None:
    """Guards: at a scale the window that already lies on the logical rectangle is left alone."""
    rect = pf.target(PLACE, (12, 55), (944, 555), PAD, Fraction(3, 2))
    assert rect is not None
    assert decide([terminal(size=[944, 555]),
                   renderer(at=[rect.x, rect.y], size=[rect.w, rect.h])],
                  monitors(scale=1.5)) == []


# --- decide: whose windows are touched (the security core) --------------------------------------


def test_a_pov_window_of_a_foreign_process_is_never_touched() -> None:
    """Guards: the class alone is not enough: the window's process must be a child of the
    dashboard. A foreign ``com.cosmergon.pov`` is in no request, shown or not shown."""
    foreign = client(FOREIGN_ADDR, pf.RENDERER_CLASS, FOREIGN, [0, 0], [10, 10])
    for place in (PLACE, pf.read_place(place_with(visible=False, col=0, row=0, cols=0, rows=0))):
        for clients in ([terminal(), foreign], [terminal(), foreign, renderer(at=[1, 1])]):
            sent = decide(clients, place=place)
            assert FOREIGN_ADDR not in "".join(sent)
    assert decide([terminal(), foreign]) == []
    assert decide([terminal(), foreign, renderer(at=[1, 1])]) == [MOVE], "only the child"


def test_a_window_of_another_class_is_never_touched_even_as_a_child() -> None:
    """Guards: a child of the dashboard with another class (and a prefix/suffix of ours) stays."""
    for cls in ("firefox", "com.cosmergon.pov2", "xcom.cosmergon.pov", "", "COM.COSMERGON.POV"):
        other = client(REND_ADDR, cls, REND, [0, 0], [10, 10])
        assert decide([terminal(), other]) == [], cls


def test_the_terminal_is_found_by_the_parent_of_the_dashboard_only() -> None:
    """Guards: a window of another process whose class looks like the terminal's is no terminal;
    a client list without the dashboard's parent, or with two windows of it, does nothing."""
    fake = client("0x5650d1d00002", "org.omarchy.cosmergon", FOREIGN, [12, 55], [1416, 833])
    assert decide([fake, renderer(at=[0, 0])]) == []
    assert decide([terminal(), terminal(), renderer(at=[0, 0])]) == [], "two windows of it"
    assert pf.decide(PLACE, [terminal(), renderer(at=[0, 0])], monitors(), PAD,
                     lambda pid: None) == [], "no parent known"
    assert pf.decide(PLACE, [terminal(), renderer(at=[0, 0])], monitors(), PAD,
                     {DASH: 1, REND: DASH}.get) == [], "the parent is init"


def test_two_windows_of_the_dashboards_parent_get_no_request() -> None:
    """Guards: a ``foot --server`` owns several windows of the one process: none is chosen, so
    nothing is sent (no move, no park); control: one window of it is followed."""
    other = {**terminal(), "address": "0x5650d1d00002"}
    assert decide([terminal(), other, renderer(at=[0, 0])]) == []
    assert decide([terminal(), other, renderer(at=[0, 0])],
                  place=pf.read_place(place_with(visible=False))) == []
    assert decide([terminal(), renderer(at=[0, 0])]) == [MOVE]


def test_the_child_check_uses_the_dashboards_pid_from_the_file() -> None:
    """Guards: the renderer must be a child of ``place.pid``, not of any pid."""
    other = pf.read_place(place_with(pid=4999))
    assert other is not None
    tree = {**TREE, 4999: FOOT}  # another dashboard in the same terminal: REND is not its child
    assert pf.decide(other, [terminal(), renderer(at=[0, 0])], monitors(), PAD, tree.get) == []


@pytest.mark.parametrize("address", [
    '0x12"; os.exit()', '0x5650d1d7b120"; hl.dispatch(evil)', "0xABCDEF", "0X5650d1d7b120", "0x",
    "5650d1d7b120", "0x" + "1" * 17, "", "0x12 0x13", "0x12\n", "address:0x12", None, 12,
    [REND_ADDR],
])
def test_an_address_that_is_not_hex_is_never_used(address: Any) -> None:
    """Guards: a client whose address is not ``0x`` and 1 to 16 lower-case hex digits is in no
    request (no text of Hyprland's JSON reaches Lua)."""
    odd = client(address, pf.RENDERER_CLASS, REND, [0, 0], [10, 10])
    sent = decide([terminal(), odd])
    assert sent == []
    assert all("os.exit" not in s and "evil" not in s for s in sent)


def test_a_workspace_name_outside_the_alphabet_is_never_sent() -> None:
    """Guards: the terminal's workspace name reaches a request only from ``[A-Za-z0-9:_-]``."""
    evil = '1"; os.exit(); "'
    sent = decide([terminal(ws=(1, evil)), renderer(ws=PARKED)])
    assert sent == [], "no name to bring it by: nothing"
    assert all("os.exit" not in s for s in decide([terminal(ws=(1, evil)), renderer()]))
    assert decide([terminal(ws=(1, "x" * 65)), renderer(ws=PARKED)]) == []
    assert decide([terminal(ws=(1, "a-b_c:1")), renderer(ws=PARKED)])[0].count("a-b_c:1") == 1


@pytest.mark.parametrize("change", [
    {"at": [1.5, 2]}, {"at": [1]}, {"at": "0,0"}, {"size": [1, True]}, {"pid": "4300"},
    {"pid": None}, {"workspace": None}, {"workspace": {"id": "1", "name": "1"}},
    {"workspace": {"id": 1}}, {"mapped": 1}, {"hidden": None}, {"monitor": 0.5},
    {"fullscreen": "yes"}, {"class": None},
])
def test_a_client_of_the_wrong_form_is_not_used(change: dict[str, Any]) -> None:
    """Guards: a renderer entry with a field of another type is left alone, nothing raises."""
    odd = {**renderer(at=[0, 0], size=[1, 1]), **change}
    assert decide([terminal(), odd]) == []


def test_other_input_of_the_wrong_form_gives_nothing() -> None:
    """Guards: a JSON that is no list of clients, or a client that is no object, never raises."""
    assert pf.decide(PLACE, {"a": 1}, monitors(), PAD, TREE.get) == []
    assert pf.decide(PLACE, [None, 3, "x", terminal(), renderer(at=[0, 0])], monitors(), PAD,
                     TREE.get) == [MOVE]
    assert pf.decide(PLACE, [terminal(), renderer(at=[0, 0])], "nope", PAD, TREE.get) == []


def test_two_renderers_of_the_dashboard_are_both_placed_and_nothing_else() -> None:
    """Guards: every child window of the class is followed, in one batch."""
    second = {**renderer(at=[0, 0]), "address": "0x5650d1d7b999", "pid": 4301}
    tree = {**TREE, 4301: DASH}
    got = pf.decide(PLACE, [terminal(), renderer(at=[1, 1]), second], monitors(), PAD, tree.get)
    assert len(got) == 1 and got[0].startswith("[[BATCH]]")
    assert got[0].count("hl.dsp.window.move") == 2 and "0x5650d1d7b999" in got[0]
    assert got[0].count(REND_ADDR) == 1


# --- /proc: the parent of a process -------------------------------------------------------------


def test_the_parent_of_this_process_is_read_from_proc() -> None:
    """Guards: ``proc_ppid`` reads field 4; none for a pid that is not there."""
    assert pf.proc_ppid(os.getpid()) == os.getppid()
    assert pf.proc_ppid(2**22 + 5) is None
    assert pf.proc_ppid(0) is None or isinstance(pf.proc_ppid(0), int)


def test_a_process_name_with_spaces_and_brackets_does_not_move_the_parent(tmp_path: Path) -> None:
    """Guards: split after the LAST ``)`` of ``/proc/<pid>/stat``: a name like ``a) S 1 (b`` would
    give a wrong field to a split on spaces or at the first bracket."""
    sleep = shutil.which("sleep")
    assert sleep
    odd = tmp_path / "a) S 1 (b"
    odd.symlink_to(sleep)
    child = subprocess.Popen([str(odd), "30"])
    try:
        for _ in range(100):  # until the exec is done and the name is the new one
            raw = Path(f"/proc/{child.pid}/stat").read_text()
            if "a) S 1 (b" in raw:
                break
            time.sleep(0.02)
        assert "a) S 1 (b" in raw, raw
        assert pf.proc_ppid(child.pid) == os.getpid()
    finally:
        child.kill()
        child.wait()


# --- the loop against a fake Hyprland -----------------------------------------------------------


class FakeHypr:
    """Hyprland's two sockets in a short folder (an AF_UNIX path is at most 107 bytes). The
    request socket answers from ``clients``/``monitors`` and writes every request down; ``mode``
    ``"close"`` hangs up without an answer, ``"junk"`` answers with text that is no JSON."""

    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="pf"))
        self.sig = "abcdef0123_1_2"
        folder = self.root / "hypr" / self.sig
        folder.mkdir(parents=True)
        self.request_path = str(folder / ".socket.sock")
        self.events_path = str(folder / ".socket2.sock")
        self.clients: Any = []
        self.monitors: Any = monitors()
        self.requests: list[str] = []
        self.mode = "ok"
        self.event_conns: list[socket.socket] = []
        self.requested = threading.Condition()
        (self.root / "config" / "foot").mkdir(parents=True)
        (self.root / "config" / "foot" / "foot.ini").write_text("[main]\npad=14x4\n")
        self._servers = [self._listen(self.request_path), self._listen(self.events_path)]
        for target, server in ((self._answer, self._servers[0]), (self._hold, self._servers[1])):
            threading.Thread(target=target, args=(server,), daemon=True).start()

    @staticmethod
    def _listen(path: str) -> socket.socket:
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(path)
        server.listen(8)
        return server

    def _answer(self, server: socket.socket) -> None:
        while True:
            try:
                conn, _ = server.accept()
            except OSError:
                return
            with conn, contextlib.suppress(OSError):
                conn.settimeout(2)
                request = conn.recv(65536).decode()
                with self.requested:
                    self.requests.append(request)
                    self.requested.notify_all()
                if self.mode == "close":
                    continue
                if self.mode == "junk":
                    conn.sendall(b"unknown request")
                elif request == "j/clients":
                    conn.sendall(json.dumps(self.clients).encode())
                elif request == "j/monitors":
                    conn.sendall(json.dumps(self.monitors).encode())
                else:
                    count = max(1, request.count("dispatch "))
                    conn.sendall("\n\n\n".join(["ok"] * count).encode())

    def _hold(self, server: socket.socket) -> None:
        while True:
            try:
                conn, _ = server.accept()
            except OSError:
                return
            self.event_conns.append(conn)

    def emit(self, text: str) -> None:
        for conn in self.event_conns:
            conn.sendall(text.encode())

    def drop_events(self) -> None:
        for conn in self.event_conns:
            conn.close()
        self.event_conns = []

    def wait_for(self, count: int) -> None:
        with self.requested:
            assert self.requested.wait_for(lambda: len(self.requests) >= count, 5), self.requests

    def env(self) -> dict[str, str]:
        return {"XDG_RUNTIME_DIR": str(self.root), "HYPRLAND_INSTANCE_SIGNATURE": self.sig,
                "XDG_CONFIG_HOME": str(self.root / "config")}

    def close(self) -> None:
        self.drop_events()
        for server in self._servers:
            server.close()
        shutil.rmtree(self.root, ignore_errors=True)


@pytest.fixture
def hypr() -> Iterator[FakeHypr]:
    fake = FakeHypr()
    try:
        yield fake
    finally:
        fake.close()


def put_place(hypr: FakeHypr, text: str, mode: int = 0o600) -> Path:
    """The dashboard's way: a 0700 folder, the file 0600, replaced by a rename."""
    folder = hypr.root / "cosmergon"
    folder.mkdir(mode=0o700, exist_ok=True)
    temp = folder / "pov-place.tmp"
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w") as out:
        out.write(text)
    os.chmod(temp, mode)
    final = folder / "pov-place.json"
    os.replace(temp, final)
    return final


def follower(hypr: FakeHypr) -> pf.Follower:
    return pf.Follower(hypr.request_path, hypr.events_path,
                       str(hypr.root / "cosmergon" / "pov-place.json"),
                       str(hypr.root / "config" / "foot" / "foot.ini"), os.getppid(), TREE.get)


def kinds(hypr: FakeHypr) -> list[str]:
    return [r.split(" ", 1)[0] for r in hypr.requests]


def test_a_step_sends_the_rule_once_and_places_the_window(hypr: FakeHypr) -> None:
    """Guards: the window rule goes out first and once, then the place file, ``j/clients``,
    ``j/monitors`` and the batch; the foot.ini of the user is read for the pad."""
    hypr.clients = [terminal(), renderer(at=[0, 0], size=[100, 100])]
    put_place(hypr, LINE)
    state = follower(hypr)
    sent = pf.step(state, set(), 100.0)
    assert sent == ["[[BATCH]]" + RESIZE + ";" + MOVE]
    assert hypr.requests[0] == pf.RULE and hypr.requests[0].startswith("eval hl.window_rule(")
    assert 'class = "^com\\\\.cosmergon\\\\.pov$"' in pf.RULE and "float = true" in pf.RULE
    assert hypr.requests[1:3] == ["j/clients", "j/monitors"] and hypr.requests[3] == sent[0]
    pf.step(state, set(), 100.0 + 2)
    assert hypr.requests.count(pf.RULE) == 1


def test_the_rule_is_sent_again_after_a_config_reload(hypr: FakeHypr) -> None:
    """Guards: ``configreloaded`` drops a rule set by ``eval``; it is set again."""
    state = follower(hypr)
    pf.step(state, set(), 1.0)
    pf.step(state, {"configreloaded"}, 1.01)
    assert hypr.requests == [pf.RULE, pf.RULE]


def test_a_rule_that_was_not_taken_is_tried_again_after_a_second(hypr: FakeHypr) -> None:
    """Guards: a rule Hyprland did not answer ``ok`` stays due, but not at every wake."""
    hypr.mode = "close"
    state = follower(hypr)
    pf.step(state, set(), 1.0)
    pf.step(state, set(), 1.1)
    assert hypr.requests == [pf.RULE] and state.rule_due
    hypr.mode = "ok"
    pf.step(state, set(), 2.5)
    assert hypr.requests == [pf.RULE, pf.RULE] and not state.rule_due


def test_an_idle_wake_asks_nothing(hypr: FakeHypr) -> None:
    """Guards: with the place file unchanged, no event and less than a second, a wake sends and
    reads nothing; after a second it looks again; a new file is seen at once (no wait)."""
    hypr.clients = [terminal(), renderer()]
    put_place(hypr, LINE)
    state = follower(hypr)
    assert pf.step(state, set(), 10.0) == []
    count = len(hypr.requests)
    for tick in range(1, 20):
        pf.step(state, set(), 10.0 + tick * 0.05)
    assert len(hypr.requests) == count, "nothing for 0.95 s"
    pf.step(state, set(), 11.1)
    assert hypr.requests[count:] == ["j/clients", "j/monitors"], "a second: looked, in place"
    hypr.clients = [terminal(), renderer(at=[0, 0])]
    put_place(hypr, place_with(col=57))  # another file (a new inode and time)
    sent = pf.step(state, set(), 11.15)
    assert len(sent) == 1, "the new file at once, not after a second"


def test_an_event_makes_the_window_looked_at_again(hypr: FakeHypr) -> None:
    """Guards: a watched event is a reason to look at once (moves make no event, the others
    do); the second pass finds it where it belongs."""
    hypr.clients = [terminal(), renderer()]
    put_place(hypr, LINE)
    state = follower(hypr)
    pf.step(state, set(), 10.0)
    count = len(hypr.requests)
    hypr.clients = [terminal(), renderer(at=[0, 0])]
    assert pf.step(state, {"workspacev2"}, 10.01) == [MOVE]
    assert len(hypr.requests) > count


def test_without_a_place_file_nothing_is_asked_and_nothing_raises(hypr: FakeHypr) -> None:
    """Guards: no view, no file: only the rule; no ``j/clients``."""
    state = follower(hypr)
    for second in range(3):
        assert pf.step(state, {"openwindow"}, float(second * 2)) == []
    assert hypr.requests == [pf.RULE]


def test_a_place_file_that_is_wrong_is_not_followed(hypr: FakeHypr, capsys: Any) -> None:
    """Guards: a file with another form, a symlink, a file others may write, one that is too long:
    nothing sent; the wrong form said once."""
    hypr.clients = [terminal(), renderer(at=[0, 0])]
    state = follower(hypr)
    path = put_place(hypr, place_with(extra=1))
    for second in range(3):
        assert pf.step(state, set(), 1.0 + second * 2) == []
    assert capsys.readouterr().err.count("pov_follow: place:") == 1
    put_place(hypr, LINE, mode=0o666)
    assert pf.step(state, set(), 20.0) == [], "writable by others"
    put_place(hypr, LINE)
    other = path.parent / "other.json"
    other.write_text(LINE)
    path.unlink()
    path.symlink_to(other)
    assert pf.step(state, set(), 30.0) == [], "a link is not followed"
    path.unlink()
    put_place(hypr, " " * 5000 + LINE)
    assert pf.step(state, set(), 40.0) == []
    put_place(hypr, LINE)
    assert len(pf.step(state, set(), 50.0)) == 1, "control: the right file is followed"


@pytest.mark.parametrize("mode", ["close", "junk"])
def test_a_hyprland_that_does_not_answer_does_not_end_the_loop(hypr: FakeHypr, mode: str,
                                                                capsys: Any) -> None:
    """Guards: no answer or an answer that is no JSON: no raise, one line per kind, and the
    follower goes on when Hyprland answers again."""
    hypr.mode = mode
    put_place(hypr, LINE)
    state = follower(hypr)
    for second in range(4):
        assert pf.step(state, set(), 10.0 + second * 2) == []
    assert len(capsys.readouterr().err.strip().splitlines()) <= 2, "one line per kind"
    hypr.mode = "ok"
    hypr.clients = [terminal(), renderer(at=[0, 0])]
    assert pf.step(state, set(), 30.0) == [MOVE]


def test_a_missing_socket_does_not_raise(tmp_path: Path, capsys: Any) -> None:
    """Guards: the request socket is gone: every step returns, one line on stderr per kind."""
    short = Path(tempfile.mkdtemp(prefix="pf"))
    try:
        place = short / "pov-place.json"
        place.write_text(LINE)
        place.chmod(0o600)
        state = pf.Follower(str(short / "none.sock"), str(short / "none2.sock"), str(place),
                            str(short / "foot.ini"), os.getppid(), TREE.get)
        for second in range(3):
            assert pf.step(state, set(), float(second * 2)) == []
        err = capsys.readouterr().err.strip().splitlines()
        assert 1 <= len(err) <= 3 and len(set(err)) == len(err)
    finally:
        shutil.rmtree(short, ignore_errors=True)


def test_a_refused_request_is_said_once(hypr: FakeHypr, capsys: Any) -> None:
    """Guards: an answer other than ``ok`` for a part of a batch is reported (once)."""
    hypr.clients = [terminal(), renderer(at=[0, 0], size=[1, 1])]
    put_place(hypr, LINE)
    state = follower(hypr)

    original = pf.ask

    def refusing(path: str, request: str) -> str:
        return "Lua error" if request.startswith("[[BATCH]]") else original(path, request)

    pf.ask, saved = refusing, pf.ask  # type: ignore[assignment]
    try:
        pf.step(state, set(), 1.0)
        pf.step(state, {"workspace"}, 1.1)
    finally:
        pf.ask = saved
    assert capsys.readouterr().err.count("pov_follow: refused") == 1


def test_the_events_are_read_without_blocking_and_filtered(hypr: FakeHypr) -> None:
    """Guards: ``wait`` returns at once on an event and names only the watched ones; a wake
    without an event takes about 50 ms."""
    state = follower(hypr)
    started = time.monotonic()
    assert pf.wait(state) == set()
    first = time.monotonic() - started
    assert state.events is not None
    deadline = time.monotonic() + 3
    while not hypr.event_conns and time.monotonic() < deadline:
        time.sleep(0.01)
    hypr.emit("activewindowv2>>abc\nmovewindowv2>>1,2,3\nsomething>>x\nmonitoraddedv2>>1,2,3\n")
    seen = set()
    while len(seen) < 2 and time.monotonic() < deadline:
        seen |= pf.wait(state)
    assert seen == {"activewindowv2", "movewindowv2"}
    assert first < 0.5
    hypr.emit("closewindow>>0x1\nopenwin")  # a line that is not finished waits for its end
    assert pf.wait(state) == {"closewindow"}
    hypr.emit("dow>>x\n")
    assert pf.wait(state) == {"openwindow"}


def test_a_broken_event_socket_is_closed_and_opened_again_after_a_pause(hypr: FakeHypr) -> None:
    """Guards: the event stream ends: no raise, the socket is dropped, opened again later, and
    the next wake looks at everything (events may have passed)."""
    state = follower(hypr)
    pf.wait(state)
    deadline = time.monotonic() + 3
    while not hypr.event_conns and time.monotonic() < deadline:
        time.sleep(0.01)
    hypr.drop_events()
    for _ in range(5):
        pf.wait(state)
        if state.events is None:
            break
    assert state.events is None
    state.checked = 5.0
    pf.connect_events(state, state.events_retry - 0.5)  # inside the pause: no new try
    assert state.events is None
    pf.connect_events(state, state.events_retry + 0.01)
    assert state.events is not None and state.checked == float("-inf")


def test_the_loop_ends_when_its_parent_is_gone(hypr: FakeHypr) -> None:
    """Guards: ``run`` returns 0 at once when ``getppid()`` is not the parent it started with."""
    state = follower(hypr)
    state.ppid = os.getppid() + 12345
    assert pf.run(state) == 0
    assert hypr.requests == [], "it did nothing"


def test_start_wants_a_hyprland_of_this_user(hypr: FakeHypr,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    """Guards: ``start`` gives ``None`` (the program ends 0, doing nothing) unless the runtime
    folder is absolute, ours and exists, the signature has its form and the socket exists."""
    for key, value in hypr.env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(pf.os, "getppid", lambda: 4321)  # a test run may sit right under init
    got = pf.start()
    assert got is not None and got.request_path == hypr.request_path
    assert got.place_path == str(hypr.root / "cosmergon" / "pov-place.json")
    assert got.ppid == 4321
    monkeypatch.setattr(pf.os, "getppid", lambda: 1)
    assert pf.start() is None, "already orphaned"
    monkeypatch.setattr(pf.os, "getppid", lambda: 4321)
    for sig in ("", "ABCDEF", "xyz", "abcdef_", "abcdef0123_1_2_3", "abcdef0123\n", "abc"):
        monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", sig)
        assert pf.start() is None, repr(sig)
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", hypr.sig)
    monkeypatch.delenv("HYPRLAND_INSTANCE_SIGNATURE")
    assert pf.start() is None
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", hypr.sig)
    monkeypatch.setenv("XDG_RUNTIME_DIR", "relative/dir")
    assert pf.start() is None
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(hypr.root / "missing"))
    assert pf.start() is None
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(hypr.root))
    os.unlink(hypr.request_path)
    assert pf.start() is None, "no request socket"


def _run_follower(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-I", str(BRAIN / "pov_follow.py")],
                          env={"PATH": os.environ.get("PATH", "/usr/bin"), **env},
                          capture_output=True, text=True, timeout=10, check=False)


def test_the_program_ends_at_once_where_there_is_no_hyprland(hypr: FakeHypr) -> None:
    """Guards: started as the terminal script starts it (python -I), outside Hyprland it ends 0
    without output; so with a bad signature or no socket."""
    for env in ({}, {"HYPRLAND_INSTANCE_SIGNATURE": hypr.sig},
                {**hypr.env(), "HYPRLAND_INSTANCE_SIGNATURE": "../../etc"},
                {**hypr.env(), "XDG_RUNTIME_DIR": str(hypr.root / "none")}):
        done = _run_follower(env)
        assert (done.returncode, done.stdout, done.stderr) == (0, "", ""), env


def test_the_follower_ends_with_the_process_that_started_it(hypr: FakeHypr) -> None:
    """Guards: started in the background by a shell, as ``shikigon-terminal`` does, it sends its
    rule, lives while the shell lives (control: still there after 1 s) and is gone soon after the
    shell ended (its end shows as the end of the pipe both hold)."""
    env = {**os.environ, **hypr.env(), "PY": sys.executable, "POV": str(BRAIN / "pov_follow.py")}
    shell = subprocess.Popen(["/bin/sh", "-c", '"$PY" -I "$POV" & sleep 1.5'], env=env,
                             stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
    assert shell.stdout is not None
    try:
        hypr.wait_for(1)
        assert hypr.requests[0] == pf.RULE
        alive, _, _ = select.select([shell.stdout], [], [], 1.0)
        assert alive == [], "the follower lives while its parent lives"
        shell.wait(timeout=5)
        ended = time.monotonic()
        gone, _, _ = select.select([shell.stdout], [], [], 3.0)
        assert gone == [shell.stdout] and shell.stdout.read() == b"", "ended with its parent"
        assert time.monotonic() - ended < 2.0
    finally:
        shell.kill()
        shell.stdout.close()
        shell.wait()


# --- shikigon-terminal: the follower's start ----------------------------------------------------

DASHBOARD = """#!{python} -I
import os, pathlib, sys, time
seen = pathlib.Path(os.environ["SEEN"])
key = os.read(3, 100).decode()
(seen / "dashboard.json").write_text(__import__("json").dumps(
    {{"argv": sys.argv[1:], "pid": os.getpid(), "key": key}}))
if os.environ.get("WAIT_FOR_FOLLOWER"):
    for _ in range(300):
        if (seen / "follower.json").exists():
            break
        time.sleep(0.02)
"""
FOLLOWER = """#!{python} -I
import json, os, pathlib, sys
fds = {{}}
for n in (0, 1, 2):
    fds[n] = os.readlink(f"/proc/self/fd/{{n}}")
try:
    os.fstat(3)
    three = True
except OSError:
    three = False
print("follower stdout")
print("follower stderr", file=sys.stderr)
pathlib.Path(os.environ["SEEN"], "follower.json").write_text(json.dumps(
    {{"argv": sys.argv[1:], "ppid": os.getppid(), "fd3": three, "fds": fds}}))
"""


def _terminal_run(tmp_path: Path, *, signature: str | None, fd3_open: bool = True,
                  wait: bool = True) -> tuple[subprocess.CompletedProcess[str], Path]:
    """``shikigon-terminal`` with a stand-in dashboard (it reads the key from descriptor 3) and a
    stand-in follower, both behind the environment's ``python``. The script is started by a shell
    that leaves descriptor 3 open (``exec 3</dev/zero``): what a careless starter might do."""
    home, state = tmp_path / "home", tmp_path / "state" / "shikigon"
    bin_ = home / ".local" / "share" / "shikigon" / "venv" / "bin"
    bin_.mkdir(parents=True)
    state.mkdir(parents=True)
    seen = tmp_path / "seen"
    seen.mkdir()
    for name, text in (("dashboard-stand-in", DASHBOARD), ("follower-stand-in", FOLLOWER)):
        (bin_ / name).write_text(text.format(python=sys.executable))
        (bin_ / name).chmod(0o755)
    (bin_ / "python").write_text(
        '#!/bin/sh\n'
        'if [ "$1 $2 $3" = "-I -m cosmergon_agent.dashboard" ]; then\n'
        f'    shift 3; exec "{bin_}/dashboard-stand-in" "$@"\n'
        'fi\n'
        'case "$2" in */pov_follow.py)\n'
        f'    exec "{bin_}/follower-stand-in" "$@" ;;\n'
        'esac\n'
        f'exec "{sys.executable}" "$@"\n'
    )
    (bin_ / "python").chmod(0o755)
    creds = {"agent_id": "a-1", "api_key": "AGENT-x:y", "world": "https://cosmergon.com:443"}
    (state / "credentials.json").write_text(json.dumps(creds))
    env = {**os.environ, "HOME": str(home), "XDG_STATE_HOME": str(tmp_path / "state"),
           "XDG_CONFIG_HOME": str(tmp_path / "config"), "SEEN": str(seen)}  # fmt: skip
    env.pop("HYPRLAND_INSTANCE_SIGNATURE", None)
    if signature is not None:
        env["HYPRLAND_INSTANCE_SIGNATURE"] = signature
    if wait:
        env["WAIT_FOR_FOLLOWER"] = "1"
    opener = "exec 3</dev/zero; " if fd3_open else ""
    done = subprocess.run(["/bin/bash", "-c", opener + 'exec "$0"', str(TERMINAL_SCRIPT)],
                          env=env, input="\n", capture_output=True, text=True, timeout=60,
                          check=False)  # fmt: skip
    return done, seen


def test_the_terminal_starts_the_follower_under_hyprland(tmp_path: Path) -> None:
    """Guards: with the signature set the follower is started with the venv's Python under -I and
    the script of the brain's folder; the dashboard start line is as before (key on fd 3)."""
    done, seen = _terminal_run(tmp_path, signature="abcdef0123_1_2")
    assert done.returncode == 0, done.stdout + done.stderr
    follower_ran = json.loads((seen / "follower.json").read_text())
    assert follower_ran["argv"] == ["-I", str(BRAIN / "pov_follow.py")]
    dashboard = json.loads((seen / "dashboard.json").read_text())
    assert dashboard["argv"] == ["--api-key-fd", "3"] and dashboard["key"] == "AGENT-x:y\n"


def test_the_terminal_starts_no_follower_without_hyprland(tmp_path: Path) -> None:
    """Guards: no ``HYPRLAND_INSTANCE_SIGNATURE`` (or an empty one): no follower; the dashboard
    still starts."""
    for number, signature in enumerate((None, "")):
        done, seen = _terminal_run(tmp_path / str(number), signature=signature, wait=False)
        time.sleep(0.5)
        assert done.returncode == 0, done.stdout + done.stderr
        assert (seen / "dashboard.json").exists()
        assert not (seen / "follower.json").exists(), repr(signature)


def test_the_follower_does_not_inherit_the_keys_descriptor(tmp_path: Path) -> None:
    """Guards: the key travels on descriptor 3 to the dashboard only. The follower is started
    before the exec, with ``3<&-``: even when the one who started the script left descriptor 3
    open (here a /dev/zero), the follower has none; the dashboard has the key's pipe on it."""
    done, seen = _terminal_run(tmp_path, signature="abcdef0123_1_2", fd3_open=True)
    assert done.returncode == 0, done.stdout + done.stderr
    assert json.loads((seen / "follower.json").read_text())["fd3"] is False
    assert json.loads((seen / "dashboard.json").read_text())["key"] == "AGENT-x:y\n"
    _, seen2 = _terminal_run(tmp_path / "again", signature="abcdef0123_1_2", fd3_open=False)
    assert json.loads((seen2 / "follower.json").read_text())["fd3"] is False, "and when none is"


def test_the_follower_has_no_terminal_and_no_input(tmp_path: Path) -> None:
    """Guards: stdin, stdout and stderr of the follower are /dev/null, in this order (``2>&1``
    after ``>/dev/null``): what it prints does not reach the terminal."""
    done, seen = _terminal_run(tmp_path, signature="abcdef0123_1_2")
    ran = json.loads((seen / "follower.json").read_text())
    assert ran["fds"] == {"0": "/dev/null", "1": "/dev/null", "2": "/dev/null"}
    assert "follower std" not in done.stdout + done.stderr


def test_the_followers_parent_is_the_dashboards_process(tmp_path: Path) -> None:
    """Guards: ``exec`` keeps the process id, so the follower started before it has the
    dashboard's pid as its parent: the rule that it ends when its parent changes ends it with the
    dashboard (and not with the shell that no longer exists)."""
    _, seen = _terminal_run(tmp_path, signature="abcdef0123_1_2")
    follower_ran = json.loads((seen / "follower.json").read_text())
    dashboard = json.loads((seen / "dashboard.json").read_text())
    assert follower_ran["ppid"] == dashboard["pid"]


def test_the_follower_is_started_isolated_and_from_the_brains_folder() -> None:
    """Guards: the line in the script: the venv's Python, ``-I``, the brain's folder, in the
    background, only under the signature, with the redirections in the order that matters."""
    text = TERMINAL_SCRIPT.read_text()
    line = '"$venv/bin/python" -I "${brain%/*}/pov_follow.py" </dev/null >/dev/null 2>&1 3<&- &'
    assert line in text
    assert text.index(line) < text.index("exec \"$venv/bin/python\" -I -m cosmergon_agent")
    assert 'if [ -n "${HYPRLAND_INSTANCE_SIGNATURE:-}" ]; then\n' in text
    assert text.index('if [ -n "${HYPRLAND_INSTANCE_SIGNATURE:-}" ]; then') < text.index(line)


# --- the presence file the dashboard looks for --------------------------------------------------


def followed(hypr: FakeHypr) -> pf.Follower:
    """A follower that also says it is there (``pov-follow.json`` beside the place file)."""
    state = follower(hypr)
    state.follow_path = str(hypr.root / "cosmergon" / "pov-follow.json")
    return state


def test_the_start_gives_the_presence_path_beside_the_place_file(
        hypr: FakeHypr, monkeypatch: pytest.MonkeyPatch) -> None:
    """Guards: ``start`` names ``<runtime>/cosmergon/pov-follow.json`` (what the dashboard reads);
    without Hyprland it gives no follower, so nothing is ever written."""
    for key, value in hypr.env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(pf.os, "getppid", lambda: 4321)
    got = pf.start()
    assert got is not None and got.follow_path == str(hypr.root / "cosmergon" / "pov-follow.json")
    monkeypatch.delenv("HYPRLAND_INSTANCE_SIGNATURE")
    assert pf.start() is None
    assert not (hypr.root / "cosmergon").exists()


def test_announce_writes_the_file_0600_in_a_0700_folder(hypr: FakeHypr) -> None:
    """Guards: the contract of the dashboard: a regular file of ours, 0600, JSON with version 1
    and our pid, at most 4096 bytes, in a folder 0700 that was made here."""
    state = followed(hypr)
    pf.announce(state)
    path = Path(state.follow_path)
    info = os.lstat(path)
    assert stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600
    assert stat.S_IMODE(os.lstat(path.parent).st_mode) == 0o700
    raw = path.read_bytes()
    assert len(raw) <= 4096 and json.loads(raw) == {"version": 1, "pid": os.getpid()}
    assert [p.name for p in path.parent.iterdir()] == ["pov-follow.json"], "no leftover"


def test_the_end_of_the_loop_removes_the_file_when_it_is_ours(hypr: FakeHypr) -> None:
    """Guards: ``run`` announces, and removes the file when the loop ends (parent gone)."""
    state = followed(hypr)
    state.ppid = os.getppid() + 12345
    seen: list[bool] = []
    real = pf.announce
    pf.announce = lambda s: (real(s), seen.append(Path(s.follow_path).exists()))[0]  # type: ignore[assignment]
    try:
        assert pf.run(state) == 0
    finally:
        pf.announce = real  # type: ignore[assignment]
    assert seen == [True] and not Path(state.follow_path).exists()


def test_a_file_with_another_pid_is_left_by_the_end(hypr: FakeHypr) -> None:
    """Guards: a newer follower's file stays; a file that is no JSON object stays too."""
    state = followed(hypr)
    pf.announce(state)
    path = Path(state.follow_path)
    for text in (json.dumps({"version": 1, "pid": os.getpid() + 1}), "[1]", "junk"):
        path.write_text(text)
        os.chmod(path, 0o600)
        pf.withdraw(state)
        assert path.read_text() == text, text


def test_a_folder_that_is_a_link_gets_nothing_and_raises_nothing(hypr: FakeHypr) -> None:
    """Guards: ``cosmergon`` as a link (to a folder of ours) is not followed: nothing is written
    there, ``announce`` and ``withdraw`` end without an error."""
    elsewhere = hypr.root / "elsewhere"
    elsewhere.mkdir(mode=0o700)
    os.symlink(elsewhere, hypr.root / "cosmergon")
    state = followed(hypr)
    pf.announce(state)
    pf.withdraw(state)
    assert list(elsewhere.iterdir()) == []


def test_a_folder_open_to_others_gets_nothing(hypr: FakeHypr) -> None:
    """Guards: a folder of ours that others may enter is not used (the dashboard refuses it)."""
    (hypr.root / "cosmergon").mkdir(mode=0o700)
    os.chmod(hypr.root / "cosmergon", 0o755)
    state = followed(hypr)
    pf.announce(state)
    assert list((hypr.root / "cosmergon").iterdir()) == []


def test_a_link_at_the_path_is_replaced_not_followed(hypr: FakeHypr) -> None:
    """Guards: a link at ``pov-follow.json`` to another file: that file is unchanged, the path
    holds a regular file afterwards."""
    folder = hypr.root / "cosmergon"
    folder.mkdir(mode=0o700)
    other = hypr.root / "other.txt"
    other.write_text("keep")
    os.symlink(other, folder / "pov-follow.json")
    state = followed(hypr)
    pf.announce(state)
    assert other.read_text() == "keep"
    info = os.lstat(state.follow_path)
    assert stat.S_ISREG(info.st_mode) and json.loads(Path(state.follow_path).read_text())["pid"]


def test_a_leftover_temporary_file_does_not_block(hypr: FakeHypr) -> None:
    """Guards: a temporary file of an earlier run (a fixed name and a name of the kind the writer
    makes) lies in the folder; the file is written all the same."""
    folder = hypr.root / "cosmergon"
    folder.mkdir(mode=0o700)
    for name in ("pov-follow.json.tmp", ".pov-follow.json.abcdef", "pov-follow.tmp"):
        (folder / name).write_text("old")
    state = followed(hypr)
    pf.announce(state)
    assert json.loads(Path(state.follow_path).read_text())["pid"] == os.getpid()


def test_without_a_presence_path_nothing_is_written(hypr: FakeHypr) -> None:
    """Guards: a follower without ``follow_path`` (as the other tests build it) writes nothing."""
    state = follower(hypr)
    state.ppid = os.getppid() + 12345
    assert pf.run(state) == 0
    assert not (hypr.root / "cosmergon").exists()


def test_a_signal_ends_the_loop_and_the_file_goes(hypr: FakeHypr) -> None:
    """Guards: the handlers of SIGTERM, SIGINT and SIGHUP end the loop (not the process); the
    file is removed and the handlers that stood before are back."""
    before = {sig: signal.getsignal(sig) for sig in pf.ENDING}
    for sig in pf.ENDING:
        state = followed(hypr)
        timer = threading.Timer(0.3, os.kill, (os.getpid(), sig))
        timer.start()
        assert pf.run(state) == 0 and state.stop
        timer.join()
        assert not Path(state.follow_path).exists(), sig
    assert {sig: signal.getsignal(sig) for sig in pf.ENDING} == before


def test_sigterm_ends_the_program_and_removes_its_file(hypr: FakeHypr) -> None:
    """Guards: the real program (python -I, started by a shell as the terminal does) writes the
    file, and after SIGTERM the file is gone and it ends 0."""
    env = {**os.environ, **hypr.env(), "PY": sys.executable, "POV": str(BRAIN / "pov_follow.py")}
    shell = subprocess.Popen(["/bin/sh", "-c", '"$PY" -I "$POV" & echo $!; wait'], env=env,
                             stdout=subprocess.PIPE, stdin=subprocess.DEVNULL, text=True)
    assert shell.stdout is not None
    try:
        pid = int(shell.stdout.readline())
        path = hypr.root / "cosmergon" / "pov-follow.json"
        deadline = time.monotonic() + 5
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert json.loads(path.read_text()) == {"version": 1, "pid": pid}
        os.kill(pid, signal.SIGTERM)
        shell.wait(timeout=5)
        assert not path.exists()
    finally:
        shell.kill()
        shell.stdout.close()
        shell.wait()


# --- the place file is watched ------------------------------------------------------------------


def test_the_place_file_is_seen_at_once_and_an_idle_wake_is_long(hypr: FakeHypr) -> None:
    """Guards: with inotify ``wait`` returns within a few milliseconds of the dashboard's rename
    (before: a look every 50 ms); an idle wake is the long ``IDLE_WAKE``, so the idle load falls
    to a fifth; a write of another file in the folder does not wake it as the place file."""
    put_place(hypr, LINE)
    state = follower(hypr)
    pf.wait(state)
    assert state.watch is not None, "the watch is open"
    started = time.monotonic()
    pf.wait(state)
    assert time.monotonic() - started > pf.IDLE_WAKE * 0.8, "idle: the long wake"
    (hypr.root / "cosmergon" / "other.json").write_text("{}")
    assert "place" not in pf.wait(state), "another file of the folder"
    timer = threading.Timer(0.1, put_place, (hypr, place_with(col=57)))
    timer.start()
    seen: set[str] = set()
    began = time.monotonic()
    while "place" not in seen and time.monotonic() - began < 2:
        seen |= pf.wait(state)
    timer.join()
    assert "place" in seen
    assert time.monotonic() - began < 0.1 + 0.03, "within a few ms of the rename"


def test_without_inotify_the_place_file_is_looked_at_every_wake(
        hypr: FakeHypr, monkeypatch: pytest.MonkeyPatch) -> None:
    """Guards: where the C library has no inotify the short ``WAKE`` stays."""
    monkeypatch.setattr(pf, "open_watch", lambda folder: None)
    put_place(hypr, LINE)
    state = follower(hypr)
    pf.wait(state)
    started = time.monotonic()
    pf.wait(state)
    assert state.watch is None and time.monotonic() - started < pf.IDLE_WAKE * 0.8


def test_a_watch_whose_folder_went_is_opened_again(hypr: FakeHypr) -> None:
    """Guards: the runtime folder removed and made again: the watch closes (and the file is looked
    at), and the next wake after the pause watches the new folder."""
    put_place(hypr, LINE)
    state = follower(hypr)
    pf.wait(state)
    assert state.watch is not None
    shutil.rmtree(hypr.root / "cosmergon")
    seen: set[str] = set()
    for _ in range(3):
        seen |= pf.wait(state)
    assert "place" in seen and state.watch is None
    put_place(hypr, LINE)
    state.watch_retry = 0.0
    pf.wait(state)
    assert state.watch is not None


# --- the timing trace ---------------------------------------------------------------------------


def traced(hypr: FakeHypr) -> tuple[pf.Follower, Path]:
    state = follower(hypr)
    path = hypr.root / "cosmergon" / "trace.jsonl"
    state.trace_path = str(path)
    return state, path


def test_a_replacement_is_traced_from_the_files_time_to_the_answer(hypr: FakeHypr) -> None:
    """Guards: one line per re-placement: the cause (``place``), the monotonic time of the file's
    modification (about now) and the time after Hyprland's answer; the file is 0600."""
    hypr.clients = [terminal(), renderer(at=[0, 0])]
    put_place(hypr, LINE)
    state, path = traced(hypr)
    before = time.monotonic()
    assert pf.step(state, set(), 5.0)
    after = time.monotonic()
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["kind"] == "place"
    assert before - 1 < rows[0]["t0"] <= rows[0]["t1"] <= after
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_an_event_and_a_recheck_are_traced_with_their_kinds(hypr: FakeHypr) -> None:
    """Guards: the cause of a pass is the event's name (from the wake-up time) or ``recheck``."""
    put_place(hypr, LINE)
    state, path = traced(hypr)
    hypr.clients = [terminal(), renderer()]
    pf.step(state, set(), 5.0)
    hypr.clients = [terminal(), renderer(at=[0, 0])]
    state.woke = time.monotonic() - 0.5
    assert pf.step(state, {"workspacev2"}, 5.1)
    hypr.clients = [terminal(), renderer(at=[0, 0])]
    assert pf.step(state, set(), 7.0)
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [r["kind"] for r in rows] == ["workspacev2", "recheck"]
    assert rows[0]["t1"] - rows[0]["t0"] >= 0.5, "from the wake-up, not from the step"


def test_nothing_sent_is_nothing_traced_and_no_path_is_no_file(hypr: FakeHypr) -> None:
    """Guards: a pass that sends no request writes no line; without a trace path no file is made."""
    hypr.clients = [terminal(), renderer()]
    put_place(hypr, LINE)
    state, path = traced(hypr)
    assert pf.step(state, set(), 5.0) == [] and not path.exists()
    hypr.clients = [terminal(), renderer(at=[0, 0])]
    plain = follower(hypr)
    assert pf.step(plain, set(), 5.0)
    assert [p.name for p in (hypr.root / "cosmergon").iterdir()] == ["pov-place.json"]


def test_a_trace_file_that_is_a_link_or_open_to_others_is_not_written(
        hypr: FakeHypr, capsys: Any) -> None:
    """Guards: the trace is opened without following a link and only written when it is a file of
    ours that no one else may read; a refusal is said once and the follower goes on."""
    hypr.clients = [terminal(), renderer(at=[0, 0])]
    put_place(hypr, LINE)
    state, path = traced(hypr)
    target_file = hypr.root / "victim"
    target_file.write_text("keep")
    path.symlink_to(target_file)
    assert pf.step(state, set(), 5.0)
    assert target_file.read_text() == "keep"
    path.unlink()
    path.write_text("")
    path.chmod(0o666)
    assert pf.step(state, set(), 7.0)
    assert path.read_text() == ""
    assert capsys.readouterr().err.count("pov_follow: trace") == 1


@pytest.mark.parametrize("name", [
    "", "../x", "a/b", "/etc/passwd", ".hidden", "a b", "a\n", "x" * 65, "pov-place.json",
    "pov-follow.json", "é",
])
def test_a_trace_name_that_is_no_plain_file_name_is_ignored(
        hypr: FakeHypr, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """Guards: ``COSMERGON_POV_FOLLOW_TRACE`` is a file name for the runtime folder; anything
    else (a path, a name starting with a dot, one of the view's own files) gives no trace."""
    for key, value in hypr.env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(pf.os, "getppid", lambda: 4321)
    monkeypatch.setenv(pf.TRACE_ENV, name)
    got = pf.start()
    assert got is not None and got.trace_path == ""


def test_a_plain_trace_name_gives_a_path_in_the_runtime_folder(
        hypr: FakeHypr, monkeypatch: pytest.MonkeyPatch) -> None:
    """Guards: the name goes to ``<runtime>/cosmergon/<name>``."""
    for key, value in hypr.env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(pf.os, "getppid", lambda: 4321)
    monkeypatch.setenv(pf.TRACE_ENV, "trace-1.jsonl")
    got = pf.start()
    assert got is not None
    assert got.trace_path == str(hypr.root / "cosmergon" / "trace-1.jsonl")


def test_the_summary_gives_count_median_p90_and_maximum() -> None:
    """Guards: ``summarize`` in milliseconds; a line that is not a trace line is skipped."""
    lines = [json.dumps({"kind": "place", "t0": 1.0, "t1": 1.0 + n / 1000}) for n in range(1, 11)]
    got = pf.summarize("\n".join(["junk", *lines, '{"t0":1}', "[]"]))
    assert got == "n=10 median=5.5ms p90=9.0ms max=10.0ms"
    assert pf.summarize("") is None and pf.summarize("junk\n") is None


def test_the_summary_runs_as_a_program(tmp_path: Path) -> None:
    """Guards: ``python -I pov_follow.py --trace-summary <file>`` prints it and ends 0; a missing
    file or one without lines ends non-zero."""
    trace = tmp_path / "t.jsonl"
    trace.write_text('{"kind":"place","t0":1.0,"t1":1.004}\n')
    cmd = [sys.executable, "-I", str(BRAIN / "pov_follow.py"), "--trace-summary"]
    done = subprocess.run([*cmd, str(trace)], capture_output=True, text=True, check=False)
    assert done.returncode == 0 and done.stdout == "n=1 median=4.0ms p90=4.0ms max=4.0ms\n"
    assert subprocess.run([*cmd, str(tmp_path / "none")], capture_output=True,
                          check=False).returncode == 2
    trace.write_text("")
    assert subprocess.run([*cmd, str(trace)], capture_output=True, check=False).returncode == 1
