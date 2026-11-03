"""One palette: no colour literal outside the token module (SPEC 1123, #1132).

The hub, the phone page and the dashboard inline ``web-tokens.css``; every colour they draw resolves to a role in that
module. The palette fold is not finished, so this guard is a ratchet: the literals that still exist are recorded with the
reason each is not a role yet, and the count may only fall. A literal the budget does not name is a new hex and fails,
whether it was typed on the hub, on the phone page or on the dashboard. A hex in the module itself is a role and is not
counted. The checks read the served text; no browser runs.
"""
import importlib.util
import json
import os
import re
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
TOKENS_FILE = SCRIPTS / "web-tokens.css"
BUDGET_FILE = SCRIPTS / "web-hex-budget.json"
DASHBOARD = ROOT / "dashboard/agent-squad-run-dashboard.html"
DASHBOARD_SOURCES = [ROOT / "dashboard/page.html", ROOT / "dashboard/page.css",
                     *(ROOT / "dashboard/page").glob("*.js"), *(ROOT / "dashboard/core").glob("*.js")]
PALETTE_ROLES = ("bg", "panel", "card", "input", "fg", "mute", "acc", "ok", "warn", "wait", "err", "run", "none", "drop")

# A hex with a word boundary, never an HTML numeric entity (&#9662;): the sign of a colour, not of a glyph.
HEX = re.compile(r"(?<!&)#[0-9a-fA-F]{3,8}\b")


def _load(name, path):
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _surfaces():
    # The hub module reads its address at import time, so the test names one: a runner with no profile (CI) still renders
    # the same static page, and the guard never depends on the machine it runs on.
    env = {"SG_REPO": "x/y", "SG_OWNER": "x", "SG_PROJECT": "1", "SG_CHECKOUT": str(ROOT),
           "SG_STATE": tempfile.mkdtemp(prefix="web-hex-budget-")}
    before = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        hub = _load("hub_web_hex_budget", SCRIPTS / "hub.py")
        mux = _load("hubmux_web_hex_budget", SCRIPTS / "hubmux.py")
    finally:
        for k, v in before.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return {
        "hub": hub.PAGE,
        "hub-mobile": hub.MOBILE_PAGE,
        "hub-refusal": hub.mobile_refusal("reason").decode(),
        "hub-test-log": hub.test_log_page({"issue": 1, "state": "done", "rc": 0, "dir": "d", "cmd": "c"}, "log").decode(),
        "hubmux-root": mux.root_page({}, "squad hub").decode(),
        # The stylesheets the hub inlines. They are scanned as they are served inside its page (#1185).
        "board-lanes": "<style>" + (SCRIPTS / "board-lanes.css").read_text() + "</style>",
        "run-sheet": "<style>" + (SCRIPTS / "run-sheet.css").read_text() + "</style>",
        # The session overlay's styles and page script, served inside the hub page and the phone page (#1188).
        "hub-overlay": hub.OVERLAY_JS,
        "dashboard": DASHBOARD.read_text(),
    }


def _strip_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"(?<!:)//[^\n]*", "", text)
    return text


def _literals(html, tokens=None):
    """Every colour literal in one served surface, with the token block and every comment removed.

    A colour sits in a CSS declaration body, in a style attribute, or in a quoted string (a script or an SVG attribute).
    A hex in a selector is an element id, not a colour, and is never counted.
    """
    text = html.replace((tokens or TOKENS_FILE.read_text()).strip(), "")
    text = re.sub(r"\$\(\"#[^\"]+\"\)", "", text)   # a selector that names an element, not a colour
    text = re.sub(r"(?:querySelector(?:All)?|getElementById)\(\"#[^\"]+\"\)", "", text)
    css = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", text, flags=re.S))
    css = re.sub(r"[^{}]+(?=\{)", "", _strip_comments(css))   # drop selector text, keep the declaration bodies
    quoted = "\n".join(m.group(0) for m in re.finditer(r"\"[^\"]*\"|'[^']*'", _strip_comments(text)))
    return HEX.findall(css + "\n" + quoted)


def _literals_by_surface(surfaces=None):
    tokens = TOKENS_FILE.read_text()
    return {name: _literals(html, tokens) for name, html in (surfaces or _surfaces()).items()}


def _budget():
    return json.loads(BUDGET_FILE.read_text())


def _blocks(css):
    return [(s.strip(), d) for s, d in re.findall(r"([^{}]+)\{([^{}]*)\}", _strip_comments(css))]


def _root_overrides(html, tokens):
    """The palette roles a surface redefines outside the shared module: a second palette, not a read of the one."""
    text = _strip_comments(html.replace(tokens.strip(), ""))
    css_blocks = re.findall(r"<style[^>]*>(.*?)</style>", text, flags=re.S) or [text]
    out = []
    for css in css_blocks:
        for selector, body in _blocks(css):
            if not selector.endswith(":root"):
                continue
            for key in re.findall(r"(--[\w-]+)\s*:", body):
                if key.lstrip("-") in PALETTE_ROLES:
                    out.append(key)
    return out


@pytest.fixture(scope="module")
def surfaces():
    return _surfaces()


@pytest.fixture(scope="module")
def budget():
    return _budget()


def test_every_literal_that_survives_carries_the_reason_it_is_not_a_role(budget):
    for literal, reason in budget["literals"].items():
        assert isinstance(reason, str) and reason.strip(), f"{literal} carries no reason"


def test_no_literal_outside_the_token_module_is_new(surfaces, budget):
    known = set(budget["literals"])
    new = {name: sorted(set(found) - known) for name, found in _literals_by_surface(surfaces).items()}
    new = {name: values for name, values in new.items() if values}
    assert not new, f"a hex outside the token module is not a role yet: {new}"


def test_the_literal_count_may_only_fall(surfaces, budget):
    found = {name: len(values) for name, values in _literals_by_surface(surfaces).items()}
    over = {name: (found[name], record) for name, record in budget["surfaces"].items() if found[name] > record}
    assert not over, f"a surface carries more literals than its recorded count: {over}"
    assert sum(found.values()) <= budget["total"], f"{sum(found.values())} literals, recorded total {budget['total']}"


def test_the_guard_catches_a_new_literal_on_each_surface(surfaces):
    """The scanner is not vacuous: a new hex on any guarded surface is found and named."""
    tokens = TOKENS_FILE.read_text()
    for name in LITERAL_SURFACES:
        poisoned = surfaces[name] + "<style>.poison{color:#123456}</style>"   # a literal beside the module
        assert "#123456" in _literals(poisoned, tokens), name


# --- The hub's stylesheets (SPEC 1181, #1185) ---
HUB_STYLESHEETS = ("board-lanes", "run-sheet")


def test_the_guard_names_the_hub_stylesheets(surfaces):
    """The two stylesheets the hub inlines are their own guarded surfaces (#1185)."""
    for name in HUB_STYLESHEETS:
        assert name in surfaces, f"the guard does not read {name}.css"
        assert name in LITERAL_SURFACES, f"{name}.css is not a guarded surface"


def test_the_hub_stylesheets_read_roles(surfaces):
    """The board lanes and the run sheet draw with the roles the token file names (#1185)."""
    for name in HUB_STYLESHEETS:
        assert "var(--" in surfaces[name], f"{name}.css reads no role"


# --- The overlay (SPEC 1181, #1188) ---
def test_the_overlay_is_a_guarded_surface(surfaces):
    """The overlay is its own guarded surface: no literal, and it draws with the roles (#1188)."""
    assert "hub-overlay" in surfaces, "the guard does not read the overlay"
    assert "hub-overlay" in LITERAL_SURFACES, "the overlay is not a guarded surface"
    assert not _literals(surfaces["hub-overlay"], TOKENS_FILE.read_text()), "the overlay carries a colour literal"
    assert "var(--" in surfaces["hub-overlay"], "the overlay reads no role"


def test_the_dashboard_writes_no_palette_of_its_own():
    for path in DASHBOARD_SOURCES:
        assert not _root_overrides(path.read_text(), TOKENS_FILE.read_text()), f"{path.relative_to(ROOT)} defines a palette of its own"


def test_the_hub_and_the_dashboard_read_the_one_module(surfaces):
    """The two surfaces inline the same module, and neither overrides a palette role beside it."""
    tokens = TOKENS_FILE.read_text()
    for name in ("hub", "dashboard"):
        assert tokens.strip() in surfaces[name], f"{name} does not carry the token module"
        assert not _root_overrides(surfaces[name], tokens), f"{name} overrides a palette role beside the module"


def test_the_hub_and_the_dashboard_render_the_same_card_ink_and_accent(surfaces):
    """Card, ink and accent are roles, and both surfaces resolve them to the same values in the one module."""
    values = dict(re.findall(r"(--(?:card|fg|mute|acc))\s*:\s*([^;}\"]+)", TOKENS_FILE.read_text()))
    assert {"--card", "--fg", "--acc"} <= set(values), values
    for name in ("hub", "dashboard"):
        for role in ("--card", "--fg", "--acc"):
            assert f"var({role})" in surfaces[name], f"{name} does not read {role}"
    # one module, so the resolved card, ink and accent are one value on both surfaces
    assert len({values[r] for r in ("--card", "--fg", "--acc")}) == 3


# --- The palette fold (SPEC 1123, #1134) ---
# Every colour a surface draws with is a role in the one module. The phone reads the same roles at its own density, and
# no surface fetches an asset from outside itself.
PALETTE_SURFACES = ("hub", "hub-mobile", "hubmux-root", "hub-refusal", "hub-test-log", "dashboard")
# A surface a page inlines: it reads the one module from the page that carries it, so it holds no palette of its own.
# The literal guard reads it as its own surface, so a colour typed here cannot pass (#1185, #1188).
INLINE_SURFACES = ("board-lanes", "run-sheet", "hub-overlay")
LITERAL_SURFACES = (*PALETTE_SURFACES, *INLINE_SURFACES)


def test_no_surface_carries_a_colour_literal(surfaces):
    """A colour outside the token module is a second palette, wherever it is typed."""
    found = {name: sorted(set(values)) for name, values in _literals_by_surface(surfaces).items()
             if name in LITERAL_SURFACES and values}
    assert not found, f"a surface still carries a colour literal: {found}"


def test_the_phone_corrects_the_misspelt_token(surfaces):
    """The phone reads `mute`, never the misspelling `muted` whose fallback hid the defect."""
    assert "var(--muted" not in surfaces["hub-mobile"], "the phone still reads the misspelt --muted"
    assert "var(--mute)" in surfaces["hub-mobile"], "the phone does not read --mute"


def test_no_surface_declares_a_palette_of_its_own(surfaces):
    """Every surface inlines the one module and redefines no palette role beside it."""
    tokens = TOKENS_FILE.read_text()
    for name in PALETTE_SURFACES:
        assert tokens.strip() in surfaces[name], f"{name} does not carry the token module"
        assert not _root_overrides(surfaces[name], tokens), f"{name} defines a palette of its own"


def test_the_accent_and_the_first_series_are_not_one_colour():
    """The collisions the spec names: the accent is not the first series, and no two tones share a hue."""
    values = dict(re.findall(r"(--[\w-]+)\s*:\s*(#[0-9a-fA-F]{3,8})\s*[;}]", TOKENS_FILE.read_text()))
    for left, right in (("--acc", "--s1"), ("--warn", "--s4"), ("--run", "--s3"), ("--none", "--s2")):
        assert values[left] != values[right], f"{left} and {right} are one colour: {values[left]}"


def test_the_pages_fetch_no_external_asset(surfaces):
    """A page carries no webfont, icon library or sprite: nothing it draws arrives from outside."""
    pattern = re.compile(r"<(?:script|link|img|iframe)\b[^>]*(?:src|href)\s*=\s*[\"']?https?://", re.I)
    for name, html in surfaces.items():
        assert not pattern.search(html), f"{name} fetches an external asset"
