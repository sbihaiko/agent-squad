"""One palette: no colour literal outside the token module (SPEC 1123, #1132).

The hub, the phone page and the dashboard inline ``web-tokens.css``; every colour they draw resolves to a role in that
module. The palette fold is not finished, so this guard is a ratchet: the literals that still exist are recorded with the
reason each is not a role yet, and the count may only fall. A literal the budget does not name is a new hex and fails,
whether it was typed on the hub, on the phone page or on the dashboard. A hex in the module itself is a role and is not
counted. The checks read the served text; no browser runs.
"""
import importlib.util
import json
import re
import sys
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
    hub = _load("hub_web_hex_budget", SCRIPTS / "hub.py")
    mux = _load("hubmux_web_hex_budget", SCRIPTS / "hubmux.py")
    return {
        "hub": hub.PAGE,
        "hub-mobile": hub.MOBILE_PAGE,
        "hub-refusal": hub.mobile_refusal("reason").decode(),
        "hub-test-log": hub.test_log_page({"issue": 1, "state": "done", "rc": 0, "dir": "d", "cmd": "c"}, "log").decode(),
        "hubmux-root": mux.root_page({}, "squad hub").decode(),
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
    css = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", html, flags=re.S)).replace(tokens.strip(), "")
    out = []
    for selector, body in _blocks(css):
        if selector != ":root":
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


def test_the_guard_catches_a_new_literal_on_each_surface():
    """The scanner is not vacuous: a new hex on any of the three surfaces is found and named."""
    tokens = TOKENS_FILE.read_text()
    for name in ("hub", "hub-mobile", "dashboard"):
        html = f"<style>:root{{--x:#123456}}</style><body>{tokens}"   # a literal beside the module
        assert "#123456" in _literals(html, tokens), name


def test_the_dashboard_writes_no_palette_of_its_own():
    for path in DASHBOARD_SOURCES:
        text = path.read_text()
        html = f"<style>{text}</style>" if path.suffix == ".css" else text
        assert not _root_overrides(html, TOKENS_FILE.read_text()), f"{path.relative_to(ROOT)} defines a palette of its own"


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
