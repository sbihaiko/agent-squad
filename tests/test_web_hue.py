"""Hue distance across the one palette (SPEC 1123, #1135).

The six state tones, the eight series and the accent are one set. Two roles collide when their CIELAB hue differs by
less than 12 degrees and their CIELAB lightness differs by no more than the band: a pair at one hue and one lightness
cannot be told apart on a chart. A role that cannot be separated by hue keeps its name and takes a lightness outside
the band, and that pair passes. The accent is never the first series. A series that is a board column takes the
column's colour from the board definition, never from a second list. The checks read the token file and the board
table; no browser runs.
"""
import importlib.util
import json
import math
import re
import sys
from itertools import combinations
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
TOKENS_FILE = SCRIPTS / "web-tokens.css"
BOARD_FILE = SCRIPTS / "board-definition.json"

TONES = ("--ok", "--warn", "--err", "--run", "--wait", "--none")
SERIES = tuple(f"--s{i}" for i in range(1, 9))
ACCENT = "--acc"
ROLES = (*TONES, *SERIES, ACCENT)

HUE_MIN = 12.0       # degrees of CIELAB hue a pair must keep apart
LIGHT_BAND = 10.0    # CIELAB lightness difference at or under which two hues are comparable
HEX = re.compile(r"#[0-9a-fA-F]{3,8}")


def _srgb(channel):
    channel /= 255
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def lab(hexv):
    """CIELAB (L*, C*, h) for one #rrggbb colour: the hue a reader sees, not a channel's angle."""
    h = hexv.lstrip("#")
    if len(h) in (3, 4):
        h = "".join(c * 2 for c in h[:3])
    r, g, b = (_srgb(int(h[i:i + 2], 16)) for i in (0, 2, 4))
    x = (r * 0.4124 + g * 0.3576 + b * 0.1805) / 0.95047
    y = r * 0.2126 + g * 0.7152 + b * 0.0722
    z = (r * 0.0193 + g * 0.1192 + b * 0.9505) / 1.08883

    def f(t):
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x), f(y), f(z)
    lightness = 116 * fy - 16
    a = 500 * (fx - fy)
    b_ = 200 * (fy - fz)
    chroma = (a * a + b_ * b_) ** 0.5
    return lightness, chroma, math.degrees(math.atan2(b_, a)) % 360


def angle_gap(left, right):
    """The shortest angle between two readings on the hue circle, in degrees."""
    gap = abs(left - right) % 360
    return min(gap, 360 - gap)


def hue_gap(left, right):
    """The shortest CIELAB hue angle between two #rrggbb colours, in degrees."""
    return angle_gap(lab(left)[2], lab(right)[2])


def lightness_gap(left, right):
    return abs(lab(left)[0] - lab(right)[0])


def read_roles(css):
    """The colour of every role in the set, from the token file's :root block."""
    block = re.search(r":root\{([^{}]*)\}", css)
    if not block:
        raise ValueError("the token file has no :root block")
    decls = dict(re.findall(r"(--[\w-]+)\s*:\s*([^;}]+)", block.group(1)))
    out = {}
    for role in ROLES:
        value = decls.get(role)
        if value is None:
            raise ValueError(f"{role} is not a role in the token file")
        value = value.strip()
        if not HEX.fullmatch(value):
            raise ValueError(f"{role} does not resolve to a colour: {value}")
        out[role] = value.lower()
    return out


def collides(left, right, values):
    """The collision of one pair, None when it is separated by hue or by lightness."""
    hue = hue_gap(values[left], values[right])
    light = lightness_gap(values[left], values[right])
    if hue < HUE_MIN and light <= LIGHT_BAND:
        return {"left": left, "right": right, "hue": hue, "light": light}
    return None


def collisions(values):
    """Every pair of the set that shares a hue within 12 degrees at a comparable lightness, in the set's order."""
    return [c for i, left in enumerate(ROLES) for right in ROLES[i + 1:] if (c := collides(left, right, values))]


def board_findings(columns, values):
    """Every column colour that names a colour of its own, or a role the set does not hold."""
    out = []
    for column in columns:
        match = re.fullmatch(r"var\((--[\w-]+)\)", str(column.get("color", "")))
        if not match:
            out.append(f"column: {column.get('name')} draws a colour of its own: {column.get('color')}")
        elif match.group(1) not in ROLES or match.group(1) not in values:
            out.append(f"column: {column.get('name')} reads {match.group(1)}, which is not a role of the set")
    return out


def _load(name, path):
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def board():
    return _load("board_columns_hue_guard", SCRIPTS / "board_columns.py")


@pytest.fixture(scope="module")
def values():
    return read_roles(TOKENS_FILE.read_text())


def test_the_guard_walks_the_tone_series_and_accent_set(values):
    """One set: the six tones, the eight series and the accent, every pair walked."""
    assert tuple(ROLES) == (*TONES, *SERIES, ACCENT)
    assert len(ROLES) == len(set(ROLES)) == 15
    assert set(values) == set(ROLES)
    assert len(list(combinations(ROLES, 2))) == 105, "the guard walks every pair of the set"


def test_the_hue_gap_is_the_shortest_way_round_the_circle():
    """0 and 359 are one degree apart, never 359: the rule reads an angle, not a channel."""
    assert angle_gap(350, 5) == 15
    assert angle_gap(5, 350) == 15
    assert angle_gap(0, 180) == 180
    assert angle_gap(97, 109) == 12


def test_the_shipped_palette_has_no_hue_collision(values):
    """No two roles in the set share a hue within 12 degrees at a comparable lightness."""
    found = collisions(values)
    assert not found, "; ".join(f"{c['left']} and {c['right']} at {c['hue']:.1f} degrees, lightness {c['light']:.1f}" for c in found)


@pytest.mark.parametrize("pair, role, literal", [
    (("--acc", "--s1"), "--s1", "#7aa2ff"),
    (("--warn", "--s4"), "--s4", "#f2c14e"),
    (("--run", "--s3"), "--s3", "#5fd3c4"),
    (("--none", "--s2"), "--s2", "#c792ea"),
])
def test_each_named_collision_is_separated_and_a_return_fails(values, pair, role, literal):
    """Every collision the product shipped is separated: putting any one role back makes the guard name that pair."""
    assert not collides(*pair, values), f"{pair[0]} and {pair[1]} still collide in the shipped palette"
    found = {frozenset((c["left"], c["right"])) for c in collisions({**values, role: literal})}
    assert frozenset(pair) in found, f"restoring {role} to {literal} does not make the guard red"


def test_the_third_yellow_keeps_its_name_and_takes_a_darker_lightness(values):
    """`--s4` stays a yellow: its hue is within 12 degrees of `--warn`, and its lightness carries the separation."""
    assert hue_gap(values["--s4"], values["--warn"]) < HUE_MIN, "the pair is meant to be separated by lightness, not by hue"
    assert lightness_gap(values["--s4"], values["--warn"]) > LIGHT_BAND, "the lightness step sits inside the comparable band"
    assert not collides("--warn", "--s4", values), "the guard refuses a pair its own rule accepts"
    assert not collides("--wait", "--s4", values), "the two yellows are one lightness on the chart"
    assert not collides("--warn", "--wait", values), "the two tones are one yellow on the chart"


def test_the_accent_is_not_the_first_series(values):
    """A selected row and a spec-1 card are never one colour."""
    assert values[ACCENT] != values[SERIES[0]]
    assert not collides(ACCENT, SERIES[0], values)
    restored = {**values, SERIES[0]: values[ACCENT]}
    assert collides(ACCENT, SERIES[0], restored), "a first series equal to the accent must be refused"


def test_a_column_series_reads_the_board_definition(board, values):
    """A series that is a board column takes the column's colour from the definition, never a second list."""
    columns = board.view_columns()
    names = [s["name"] for s in json.loads(BOARD_FILE.read_text())["statuses"]]
    assert [c["name"] for c in columns] == names, "the board table is not the definition's own order"
    assert not board_findings(columns, values), "a column colour is not a palette role"
    assert board_findings([{"name": "Todo", "color": "#14b8c0"}], values), "a column colour copied as a literal must be refused"


def test_the_guard_reads_the_shipped_roles_by_name():
    """The set is the palette's own names: a role the token file does not carry, or cannot resolve, fails the read."""
    broken = re.sub(r"--s1:#[0-9a-fA-F]{6}", "--s1:#nope", TOKENS_FILE.read_text(), count=1)
    with pytest.raises(ValueError, match=r"--s1"):
        read_roles(broken)
    with pytest.raises(ValueError, match=r"--s1"):
        read_roles(re.sub(r"--s1:#[0-9a-fA-F]{6}", "", TOKENS_FILE.read_text(), count=1))
