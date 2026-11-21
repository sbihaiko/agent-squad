"""One visual language across the web surfaces (#908).

Every served surface carries the one token block in contrib/squad-goal/scripts/web-tokens.css. The checks read each
surface's served text and resolve the values in it: no font size under 13 px, body text at 15 px, the token color pairs
and every hard-coded text color at 4.5:1 or more, a 2 px focus ring, dark native controls, and control heights of 32 px
on the desktop and 44 px on the phone. No browser runs; every value is computed from the text the surface sends. The
height check models the cascade by rule, not by element match: a phone rule restated by the token block replaces its
desktop twin.
"""
import importlib.util
import os
import re
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
TOKENS_FILE = SCRIPTS / "web-tokens.css"
DASHBOARD = ROOT / "dashboard/agent-squad-run-dashboard.html"


def _token_module():
    return re.sub(r"/\*\s*literal-carveout\b.*?\*/\s*", "", TOKENS_FILE.read_text(), flags=re.S).strip()


FLOOR_PX = 13
BODY_PX = 15
CONTRAST_MIN = 4.5
DESKTOP_CONTROL_PX = 32
PHONE_CONTROL_PX = 44
TEXT_TOKENS = ["--fg", "--mute", "--acc", "--ok", "--warn", "--err", "--run", "--none", "--wait", "--drop",
               "--s1", "--s2", "--s3", "--s4", "--s5", "--s6", "--s7", "--s8"]   # the spec palette (#917)
BG_TOKENS = ["--bg", "--panel"]
CONTROLS = ("button", "select", "input", "textarea", "summary", "label", "[role=button]")
CONTROL_TAGS = ("button", "select", "input", "textarea", "summary", "label")


def _load(name, path):
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _surfaces():
    # The hub module reads its address at import time, so the test names one: a runner with no profile (CI, the fast
    # gate) still renders the same static page, and the guard never depends on the machine it runs on.
    env = {"SG_REPO": "x/y", "SG_OWNER": "x", "SG_PROJECT": "1", "SG_CHECKOUT": str(ROOT),
           "SG_STATE": tempfile.mkdtemp(prefix="web-tokens-")}
    before = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        hub = _load("hub_web_tokens", SCRIPTS / "hub.py")
        mux = _load("hubmux_web_tokens", SCRIPTS / "hubmux.py")
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
        "hub-test-log": hub.test_log_page({"issue": 908, "state": "done", "rc": 0, "dir": "d", "cmd": "c"}, "log").decode(),
        "hubmux-root": mux.root_page({}, "squad hub").decode(),
        "dashboard": DASHBOARD.read_text(),
    }


def _served_css(html):
    return "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", html, flags=re.S))


def _strip_comments(css):
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _decls(body):
    return [(k.strip(), v.strip()) for k, v in (p.split(":", 1) for p in body.split(";") if ":" in p)]


def _blocks(css):
    return [(s.strip(), d) for s, d in re.findall(r"([^{}]+)\{([^{}]*)\}", _strip_comments(css))]


def _vars(css):
    out = {}
    for sel, body in _blocks(css):
        if sel == ":root":
            out.update({k: v for k, v in _decls(body) if k.startswith("--")})
    return out


def _resolve(value, tokens):
    for _ in range(5):
        value = re.sub(r"var\((--[\w-]+)\)", lambda m: tokens.get(m.group(1), m.group(0)), value)
    return value


def _px(value):
    m = re.search(r"(\d+(?:\.\d+)?)px", value)
    return float(m.group(1)) if m else None


def _size(raw, tokens):
    value = _resolve(raw.strip(), tokens)
    if re.fullmatch(r"\d+(?:\.\d+)?", value):
        return float(value)
    return _px(value)


def _hex_lum(color):
    h = color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    assert re.fullmatch(r"[0-9a-fA-F]{6}", h), color
    chans = []
    for i in (0, 2, 4):
        c = int(h[i:i + 2], 16) / 255
        chans.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * chans[0] + 0.7152 * chans[1] + 0.0722 * chans[2]


def _contrast(a, b):
    hi, lo = sorted([_hex_lum(a), _hex_lum(b)], reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _hex_of(value, tokens):
    if value is None:
        return None
    m = re.match(r"\s*(#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?)(?![\w-])", _resolve(value, tokens))
    return m.group(1) if m else None


def _phone_inner(css):
    m = re.search(r"@media\s*\(max-width:\s*600px\)\s*\{(.*)\}", _strip_comments(css), flags=re.S)
    return m.group(1) if m else ""


def _control_classes(html):
    names = set()
    tags = "|".join(CONTROL_TAGS)
    for attrs in re.findall(rf"<(?:{tags})\b([^>]*)>", html):
        m = re.search(r"\bclass=(?:\"([^\"]*)\"|'([^']*)'|([\w-]+))", attrs)
        if m:
            names.update(next(g for g in m.groups() if g).split())
    return names


def _is_control(sel, classes):
    if any(re.search(rf"(^|[\s,>]){re.escape(c)}(?=[\s,:.#\[>+~]|$)", sel) for c in CONTROLS):
        return True
    last = re.split(r"[\s>+~]+", sel)[-1]
    return any(c in re.findall(r"\.([\w-]+)", last) for c in classes)


def _effective_px(decls, tokens):
    sizes = [_px(_resolve(decls[k], tokens)) for k in ("height", "min-height") if k in decls]
    sizes = [s for s in sizes if s is not None]
    return max(sizes) if sizes else None


def _phone_rules(css):
    rules = _blocks(css)
    restated = {sel for sel, _ in _blocks(_phone_inner(TOKENS_FILE.read_text()))}
    last = {sel: i for i, (sel, _) in enumerate(rules) if sel in restated}
    return [rule for i, rule in enumerate(rules) if rule[0] not in restated or last[rule[0]] == i]


@pytest.fixture(scope="module")
def surfaces():
    return _surfaces()


@pytest.fixture(scope="module")
def tokens():
    return _vars(TOKENS_FILE.read_text())


def test_the_tokens_block_is_one_file():
    assert TOKENS_FILE.is_file(), "the one token block is missing"


def test_every_surface_links_the_one_tokens_block(surfaces):
    block = _token_module()
    for name, html in surfaces.items():
        assert block in html, f"{name} does not carry the token block"


def test_no_served_font_size_under_the_floor(surfaces):
    for name, html in surfaces.items():
        vars_ = _vars(_served_css(html))
        raws = re.findall(r"\bfont(?:-size)?\s*:\s*([^;}\"'\n]+)", _served_css(html) + html)
        raws += re.findall(r"\bfont-size\s*=\s*[\"']?(\d+(?:\.\d+)?)", html)
        for raw in raws:
            size = _size(raw, vars_)
            if size is not None:
                assert size >= FLOOR_PX, f"{name}: font size {size}px under {FLOOR_PX}px in '{raw.strip()}'"


def test_body_text_is_fifteen_px(surfaces):
    for name, html in surfaces.items():
        css = _served_css(html)
        vars_ = _vars(css)
        found = [dict(_decls(d)) for s, d in _blocks(css) if s == "body"]
        found += [dict(_decls(d)) for d in re.findall(r"<body[^>]*style=\"([^\"]*)\"", html)]
        for decls in found:
            for key in ("font", "font-size"):
                if key in decls:
                    size = _px(_resolve(decls[key], vars_))
                    if size is not None:
                        assert size == BODY_PX, f"{name}: body text {size}px"


def test_token_color_pairs_meet_contrast(tokens):
    for fg in TEXT_TOKENS:
        for bg in BG_TOKENS:
            ratio = _contrast(tokens[fg], tokens[bg])
            assert ratio >= CONTRAST_MIN, f"{fg} on {bg} is {ratio:.2f}:1"


def test_every_served_text_color_meets_contrast(surfaces, tokens):
    failures = []
    for name, html in surfaces.items():
        vars_ = _vars(_served_css(html))
        sources = [dict(_decls(body)) for _, body in _blocks(_served_css(html))]
        sources += [dict(_decls(style)) for style in re.findall(r"style=\"([^\"]*)\"", html)]
        for decls in sources:
            fg = _hex_of(decls.get("color"), vars_)
            if fg is None:
                continue
            if "background" in decls or "background-color" in decls:
                bg = _hex_of(decls.get("background", decls.get("background-color")), vars_)
                backgrounds = [bg] if bg else []
            else:
                backgrounds = [tokens["--panel"], tokens["--bg"]]
            for bg in backgrounds:
                ratio = _contrast(fg, bg)
                if ratio < CONTRAST_MIN:
                    failures.append(f"{name}: {fg} on {bg} is {ratio:.2f}:1")
    assert not failures, "\n".join(sorted(set(failures)))


def test_focus_ring_is_two_px_and_covers_every_control(tokens):
    rules = _blocks(TOKENS_FILE.read_text())
    ring = [(s, d) for s, d in rules if ":focus-visible" in s]
    assert ring, "no :focus-visible rule in the tokens block"
    selector, body = ring[0]
    outline = dict(_decls(body)).get("outline", "")
    width = _px(_resolve(outline, tokens))
    assert width == 2, f"focus ring is {width}px"
    for want in ("button", "a", "input", "select", "textarea", "summary"):
        assert re.search(rf"(^|,|\s){want}(?=[:,\s]|$)", selector), f"focus ring misses {want}"


def test_no_surface_removes_the_focus_ring(surfaces):
    for name, html in surfaces.items():
        for sel, body in _blocks(_served_css(html)):
            outline = dict(_decls(body)).get("outline", "")
            assert not re.match(r"(none|0)\b", outline), f"{name}: {sel} removes the outline"


def test_selects_and_inputs_are_dark(surfaces, tokens):
    root = [d for s, d in _blocks(TOKENS_FILE.read_text()) if s == ":root"]
    assert any(dict(_decls(d)).get("color-scheme") == "dark" for d in root)
    for name, html in surfaces.items():
        css = _served_css(html)
        vars_ = _vars(css)
        for sel, body in _blocks(css):
            parts = [p.strip() for p in sel.split(",")]
            if not any(p in ("select", "input") for p in parts):
                continue
            bg = dict(_decls(body)).get("background")
            if bg is None:
                continue
            color = _resolve(bg, vars_).split()[0]
            if color.startswith("#"):
                assert _hex_lum(color) < 0.03, f"{name}: {sel} is light ({color})"


def test_control_heights_meet_the_desktop_and_phone_floor(surfaces):
    for name, html in surfaces.items():
        css = _served_css(html)
        vars_ = _vars(css)
        classes = _control_classes(html)
        floor = PHONE_CONTROL_PX if name == "hub-mobile" else DESKTOP_CONTROL_PX
        rules = _phone_rules(css) if name == "hub-mobile" else _blocks(css)
        for sel, body in rules:
            if not _is_control(sel, classes):
                continue
            px = _effective_px(dict(_decls(body)), vars_)
            if px is not None:
                assert px >= floor, f"{name}: {sel} is {px}px tall, floor {floor}px"


def test_the_phone_media_block_sets_the_phone_target(tokens):
    m = re.search(r"@media\s*\(max-width:\s*600px\)\s*\{(.*)\}", _strip_comments(TOKENS_FILE.read_text()), flags=re.S)
    assert m, "no phone media block in the tokens"
    heights = [_px(_resolve(d.get("min-height", ""), tokens)) for _, b in _blocks(m.group(1)) for d in [dict(_decls(b))]]
    assert heights and min(h for h in heights if h is not None) >= PHONE_CONTROL_PX


# --- Typography roles across the hub, phone and dashboard (#1138) ---

def test_typography_sizes_are_the_five_shared_roles(surfaces, tokens):
    expected = {"--fs-caption": "13px", "--fs-body": "15px", "--fs-field": "16px",
                "--fs-heading": "17px", "--fs-display": "21px"}
    assert {role: tokens.get(role) for role in expected} == expected
    assert {_role for _role in tokens if _role.startswith("--fs-")} == set(expected)
    for name, html in surfaces.items():
        css = _served_css(html)
        for raw in re.findall(r"font-size\s*:\s*([^;}]+)", css):
            assert "var(--fs-" in raw, f"{name} has a non-token font-size: {raw.strip()}"
        for weight in re.findall(r"font-weight\s*:\s*([^;}]+)", css):
            assert weight.strip() in {"400", "600"}, f"{name} uses font weight {weight.strip()}"
    assert tokens["--font-mono"] == "ui-monospace,SFMono-Regular,Menlo,monospace"
    assert "table,.row,.r,.tot,.tile,.mrow,.runrow,.cell,.hidbar,#totals,#progress,.card,.group-card" in _served_css(surfaces["hub"])


def test_phone_text_fields_use_field_size(surfaces, tokens):
    css = _served_css(surfaces["hub-mobile"])
    rules = _blocks(css)
    text_fields = [(sel, dict(_decls(body))) for sel, body in rules
                   if re.search(r"(?:^|[,\s])(?:input|textarea)(?:[,\s:#.]|$)", sel)]
    assert text_fields, "the phone has no text-field rules"
    for selector, decls in text_fields:
        font = decls.get("font-size", decls.get("font", ""))
        if font:
            assert _size(font, tokens) == 16, f"{selector} resolves to {font!r}, not 16px"


def test_identifiers_keep_mono_and_numeric_rows_keep_tabular_figures(surfaces):
    token_css = TOKENS_FILE.read_text()
    assert ".kr" in token_css and ".runrow" in token_css
    phone_css = _served_css(surfaces["hub-mobile"])
    for selector in (".card .cid", ".runid"):   # the sheet's identifiers left the page with the sheet (#2501)
        decls = _rule(phone_css, selector)
        font = " ".join(decls.get(key, "") for key in ("font", "font-family"))
        assert "var(--font-mono)" in font, f"phone identifier {selector} is not monospace"
    assert "font-variant-numeric:tabular-nums" in token_css
    dashboard = (ROOT / "dashboard/page.css").read_text()
    assert "font-family:var(--font-mono)" in dashboard and ".card .nm" in dashboard, \
        "dashboard node identifiers are not monospace"


def test_effective_bold_elements_use_six_hundred(surfaces):
    phone = (SCRIPTS / "hub-mobile.html").read_text()
    assert "b,strong,th,h1,h2,h3,h4,h5,h6{font-weight:600}" in TOKENS_FILE.read_text()
    assert "runid" in phone and "font:600 var(--fs-body) var(--font-mono)" in phone
    dashboard = (ROOT / "dashboard/page.css").read_text()
    assert "b,strong,th,h1,h2,h3,h4,h5,h6{font-weight:600}" in TOKENS_FILE.read_text()
    assert ".card .nm" in dashboard and "font-weight:600" in dashboard


# --- The shared palette and the chart roles (SPEC 1181, #1184) ---
# The roles every surface already draws with, and the literal each one replaces. This ticket only names them in the token
# file; a surface reads a role later, in its own batch. `--highlight` carries #fff, which #ffffff writes long.
SHARED_ROLES = {
    "--well": "#0d1117",
    "--inset": "#10141a",
    "--table-head": "#101a16",
    "--float": "#11151c",
    "--bubble": "#161c24",
    "--raise": "#2a3140",
    "--hover": "#2e3846",
    "--well-line": "#232a36",
    "--bubble-line": "#26303c",
    "--line-strong": "#353d4b",
    "--tint-ok": "#12302a",
    "--tint-ok-line": "#2e6b4f",
    "--ink-strong": "#eef1f5",
    "--ink-body": "#c9d1d9",
    "--ink-soft": "#b8c0cf",
    "--ink-muted": "#8b949e",
    "--ink-tool": "#9aa3b2",
    "--code-ink": "#e6c07b",
    "--on-tint": "#e6edf3",
    "--highlight": "#fff",
    "--s1-faint": "#14b8c055",
    "--scrim": "#000a",
    "--shadow-soft": "#0008",
    "--shadow-float": "#000b",
    "--shadow-deep": "#000c",
    "--edge": "#667085",   # the meaningful non-text boundary (#1133)
}

# The chart family: a chart carries no literal of its own. A structural role reads the palette; a mark the palette does not
# name yet gets its own value here, the one place a colour literal lives.
CHART_ROLES = {
    "--chart-grid": "#2a303b",
    "--chart-axis": "#8a93a3",
    "--chart-ink": "#b8c0cf",
    "--chart-value": "#e6e8ee",
    "--chart-bg": "#161a21",
    "--chart-hollow": "#0f1115",
    "--chart-track": "#2a303b",
    "--chart-needle": "#e6e8ee",
    "--chart-bar": "#5aa9ff",
    "--chart-bar-max": "#3ecf8e",
    "--chart-ok": "#3ecf8e",
    "--chart-warn": "#f5b041",
    "--chart-err": "#ff6b6b",
    "--chart-none": "#b79cf6",
    "--chart-run": "#2dd4bf",
    "--chart-drop": "#c678dd",
    "--chart-series-1": "#14b8c0",
    "--chart-series-2": "#d88cd0",
    "--chart-series-3": "#2cbcd8",
    "--chart-series-4": "#b99126",
    "--chart-series-5": "#ff8a5c",
    "--chart-series-6": "#9ccc65",
    "--chart-series-7": "#f48fb1",
    "--chart-series-8": "#90a4ae",
    "--chart-series-extra": "#e5c07b",
    "--chart-series-other": "#8a93a3",
    "--chart-series-fallback": "#888",
    "--chart-held": "#000",
}

SURFACE_HEX = re.compile(r"(?<!&)#[0-9a-fA-F]{3,8}\b")


def _strip_all_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    return re.sub(r"(?<!:)//[^\n]*", "", text)


def _surface_colours(html):
    """Every colour literal in one served surface, with the token block and every comment removed.

    The scan is the hex guard's (tests/test_web_hex_budget.py): a colour sits in a declaration body, a style attribute or a
    quoted string, and a hex in a selector is an element id.
    """
    text = html.replace(_token_module(), "")
    css = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", text, flags=re.S))
    css = re.sub(r"[^{}]+(?=\{)", "", _strip_all_comments(css))
    quoted = "\n".join(m.group(0) for m in re.finditer(r"\"[^\"]*\"|'[^']*'", _strip_all_comments(text)))
    return SURFACE_HEX.findall(css + "\n" + quoted)


def _long_hex(value):
    """One spelling of a colour: #fff and #ffffff are one value, and #000a and #000000aa are one too."""
    h = value.lstrip("#").lower()
    if len(h) in (3, 4):
        h = "".join(c * 2 for c in h)
    return "#" + h


def _phone_tokens():
    """The roles a phone viewport resolves: the :root block, then the phone media block in cascade order.

    The two themes are the desktop and the phone blocks of the one file. The phone block sets density, never colour, so
    every role resolves to the same value in both; a role redefined differently there would break this.
    """
    css = TOKENS_FILE.read_text()
    out = _vars(css)
    out.update(_vars(_phone_inner(css)))
    return out


def _role_value(tokens, role):
    value = _resolve(f"var({role})", tokens)
    assert re.fullmatch(r"#[0-9a-fA-F]{3,8}", value), f"{role} does not resolve to a colour: {value}"
    return value


def test_every_colour_two_surfaces_share_is_a_named_role(surfaces):
    """A literal two surfaces draw with is a role in the token file; no second palette survives."""
    counts = {}
    for html in surfaces.values():
        for literal in set(_surface_colours(html)):
            counts[literal] = counts.get(literal, 0) + 1
    shared = {literal for literal, n in counts.items() if n >= 2}
    named = {_long_hex(v) for v in _vars(TOKENS_FILE.read_text()).values() if re.fullmatch(r"#[0-9a-fA-F]{3,8}", v)}
    missing = sorted(literal for literal in shared if _long_hex(literal) not in named)
    assert not missing, f"a colour two surfaces share is not a role: {missing}"


def test_every_role_resolves_in_both_themes(tokens):
    """Every shared and chart role resolves on the desktop and the phone, and its value is the one it replaces."""
    phone = _phone_tokens()
    for role, value in {**SHARED_ROLES, **CHART_ROLES}.items():
        assert role in tokens, f"{role} is not a role in the token file"
        desktop, handset = _role_value(tokens, role), _role_value(phone, role)
        assert handset == desktop, f"{role} is {handset} on the phone and {desktop} on the desktop"
        assert _long_hex(desktop) == _long_hex(value), f"{role} is {desktop}, not {value}"


def test_the_chart_family_reads_the_palette(tokens):
    """A structural chart role reads the palette instead of copying a value."""
    sources = {
        "--chart-grid": "--line",
        "--chart-axis": "--mute",
        "--chart-ink": "--ink-soft",
        "--chart-value": "--fg",
        "--chart-bg": "--panel",
        "--chart-hollow": "--bg",
        "--chart-track": "--line",
        "--chart-needle": "--fg",
        "--chart-bar": "--acc",
        "--chart-bar-max": "--ok",
        "--chart-ok": "--ok",
        "--chart-warn": "--warn",
        "--chart-err": "--err",
        "--chart-none": "--none",
        "--chart-run": "--run",
        "--chart-drop": "--drop",
        "--chart-series-other": "--mute",
        **{f"--chart-series-{i}": f"--s{i}" for i in range(1, 9)},
    }
    for role, source in sources.items():
        assert tokens[role] == f"var({source})", f"{role} copies a value instead of reading {source}"


def test_a_surface_reads_the_token_file_not_a_copy_of_its_value(surfaces):
    """The token block is inlined verbatim, and no surface declares a shared role beside it."""
    block = _token_module()
    for name in ("hub", "hub-mobile", "dashboard"):
        assert block in surfaces[name], f"{name} does not carry the token block"
        for role in (*SHARED_ROLES, *CHART_ROLES):
            assert len(re.findall(re.escape(role) + r"\s*:", surfaces[name])) == 1, f"{name} declares {role} outside the token file"


def test_the_token_file_wins_over_the_overlay(tokens):
    """Where the overlay drew a different value, the palette keeps its own; the overlay follows it in #1188."""
    assert tokens["--fg"] == "#e6e8ee", "the overlay's #d8dde6 must not replace --fg"
    assert tokens["--mute"] == "#8a93a3", "the overlay's #b8c0cf is --ink-soft, never --mute"
    assert tokens["--acc"] == "#5aa9ff", "the overlay's accent must not replace --acc"
    assert tokens["--ok"] == "#3ecf8e", "the overlay's #5fd38d must not replace --ok"
    assert tokens["--warn"] == "#f5b041", "the overlay's yellow must not replace --warn"


# --- Meaningful non-text (SPEC 1123, #1133) ---
# A boundary, a mark or a control that carries meaning is visible at 3:1 on both surfaces. `line` stays a decorative rule
# and may sit low. A card keeps `line` at rest and takes `edge` on hover and on selection.
NON_TEXT_MIN = 3.0
# A state mark is a tone drawn as a border, a dot or a glyph: a card's outcome and the run-state pill.
STATE_MARK_ROLES = ["--ok", "--err", "--warn", "--wait", "--run", "--none", "--mute", "--acc"]
# A chart mark draws a value: a series, a bar, a needle or a status band. A grid line, a track and an underlay band are
# decoration and stay out of this class.
CHART_MARK_ROLES = [f"--chart-series-{i}" for i in range(1, 9)] + [
    "--chart-series-extra", "--chart-series-other", "--chart-series-fallback",
    "--chart-bar", "--chart-bar-max", "--chart-needle",
    "--chart-ok", "--chart-warn", "--chart-err", "--chart-none", "--chart-run", "--chart-drop",
]

def _rules(css, selector):
    """Every declaration body the stylesheet gives `selector`: a selector may repeat, and the last one wins."""
    bodies = [dict(_decls(body)) for sel, body in _blocks(css) if selector in [p.strip() for p in sel.split(",")]]
    assert bodies, f"the stylesheet has no rule for {selector}"
    return bodies


def _rule(css, selector):
    return _rules(css, selector)[-1]


def test_the_edge_role_has_a_stated_purpose():
    """A new colour is a role with a sentence that says what it carries (#1133)."""
    comment = TOKENS_FILE.read_text().split(":root{")[0]
    assert "edge" in comment, "the token module never names the edge role"
    assert re.search(r"edge[^.]*3:1", comment, re.S), "the edge role does not state its 3:1 purpose"


def test_meaningful_non_text_meets_three_to_one(tokens):
    """`edge`, a state mark, a chart mark and the focus ring: 3:1 on panel and card (#1133)."""
    for role in ["--edge", *STATE_MARK_ROLES, *CHART_MARK_ROLES, "--focus"]:
        for bg_role in ("--panel", "--card"):
            ratio = _contrast(_role_value(tokens, role), _role_value(tokens, bg_role))
            assert ratio >= NON_TEXT_MIN, f"{role} on {bg_role} is {ratio:.2f}:1, under {NON_TEXT_MIN}:1"


def test_a_card_keeps_line_at_rest_and_takes_edge_on_hover_and_selection():
    """The resting border is decoration; the hover and the selection are the card meaning `edge` carries (#1133)."""
    css = (ROOT / "dashboard/page.css").read_text()
    assert _rule(css, ".card rect")["stroke"] == "var(--line)", "a card's resting border is `line`"
    for selector in (".card:hover rect", ".card.sel rect"):
        for body in _rules(css, selector):
            assert body["stroke"] == "var(--edge)", f"{selector} reads {body['stroke']}, not the edge role"
    for body in _rules(css, ".group-card.sel > rect"):
        assert body["stroke"] == "var(--edge)", "a selected group card reads the edge role"


def test_a_dialog_border_reads_the_edge_role(surfaces):
    """A dialog separates from the page by its panel step and an `edge` border (#1133)."""
    css = _served_css(surfaces["hub"])
    for selector in ("#ldg", "#sdlg"):
        border = _rule(css, selector).get("border", "")
        assert "var(--edge)" in border, f"{selector} reads {border!r}, not the edge role"


def test_the_switch_track_reads_the_edge_role(surfaces, tokens):
    """The off track is the one control mark a switch carries; the on track is `ok` (#1133)."""
    css = _served_css(surfaces["hub"])
    off = _rule(css, "#inst .lsw").get("background")
    on = _rule(css, "#inst .lsw[aria-checked=true]").get("background")
    assert off == "var(--edge)", "the switch's off track is not the edge role"
    assert on == "var(--ok)", "the switch's on track is not `ok`"
    for role in (off, on):
        value = _role_value(tokens, re.fullmatch(r"var\((--[\w-]+)\)", role).group(1))
        for bg_role in ("--panel", "--card"):
            ratio = _contrast(value, _role_value(tokens, bg_role))
            assert ratio >= NON_TEXT_MIN, f"the switch track {role} on {bg_role} is {ratio:.2f}:1"


# --- Tints and the ink drawn on them (SPEC 1123, #1136) ---
# A tinted chip draws its tone at the one published opacity over card, and its text is onTint (`--on-tint`).
TINT_TONES = ["--ok", "--warn", "--err", "--run", "--acc"]
TINTED_FILLS = {
    "#bbody .pbg.green": "--ok",
    "#bbody .pbg.conflict": "--err",
    "#bbody .pbg.checks-red": "--err",
    "#bbody .prs .it .cir.pass": "--ok",
    "#bbody .prs .it .cir.fail": "--err",
    "#bbody .prs .it .cir.pending": "--run",
    "#bbody .age.warm": "--warn",
    "#bbody .age.old": "--err",
    "#bbody .gc.ok": "--ok",
    "#bbody .gc.warn": "--warn",
    "#bbody .gc.bad": "--err",
    "#bbody .gc.debt": "--acc",
}
TINT_INK_SELECTORS = [s if not s.startswith("#bbody .gc.") else f"{s} b" for s in TINTED_FILLS]


def _tint_alpha(tokens):
    m = re.fullmatch(r"(\d+(?:\.\d+)?)%", tokens["--tint-alpha"].strip())
    assert m, f"--tint-alpha is not a percentage: {tokens['--tint-alpha']}"
    return float(m.group(1)) / 100


def _blend(fg, bg, alpha):
    """The colour a tone at `alpha` draws over `bg`, composited per channel as a browser does for color-mix."""
    f, b = (_long_hex(c)[1:7] for c in (fg, bg))
    return "#" + "".join(f"{round(alpha * int(f[i:i + 2], 16) + (1 - alpha) * int(b[i:i + 2], 16)):02x}" for i in (0, 2, 4))


def test_the_tint_opacity_is_a_token(tokens):
    assert "--tint-alpha" in tokens, "the tint opacity is not a role in the token file"
    assert 0 < _tint_alpha(tokens) <= 0.3


def test_on_tint_meets_four_point_five_on_every_computed_tint(tokens):
    alpha = _tint_alpha(tokens)
    on_tint = _role_value(tokens, "--on-tint")
    for tone in TINT_TONES:
        for bg_role in ("--panel", "--card"):
            fill = _blend(_role_value(tokens, tone), _role_value(tokens, bg_role), alpha)
            ratio = _contrast(on_tint, fill)
            assert ratio >= CONTRAST_MIN, f"--on-tint on {tone} at {alpha:.0%} over {bg_role} is {ratio:.2f}:1"


def test_a_tinted_chip_fills_with_its_tone_at_the_published_opacity(surfaces):
    css = _served_css(surfaces["hub"])
    for selector, tone in TINTED_FILLS.items():
        fill = _rule(css, selector).get("background")
        want = f"color-mix(in srgb,var({tone}) var(--tint-alpha),var(--card))"
        assert fill == want, f"{selector} fills {fill!r}, not {want}"


def test_a_tinted_chip_inks_on_tint(surfaces):
    css = _served_css(surfaces["hub"])
    for selector in TINT_INK_SELECTORS:
        ink = _rule(css, selector).get("color")
        assert ink == "var(--on-tint)", f"{selector} inks {ink!r}, not onTint"


def test_shared_visual_rhythm_and_surface_borders(surfaces, tokens):
    expected = {
        "--space-1": "4px", "--space-2": "8px", "--space-3": "12px", "--space-4": "16px",
        "--space-6": "24px", "--radius-sm": "4px", "--radius-md": "6px", "--radius-lg": "8px",
        "--radius-xl": "14px", "--radius-full": "9999px", "--border-thin": "1px",
        "--border-medium": "2px", "--border-thick": "3px", "--row-h": "32px", "--chip-h": "20px",
        "--card-gap-x": "10px", "--card-gap-y": "8px", "--row-h-phone": "44px",
        "--card-gap-x-phone": "12px", "--card-gap-y-phone": "10px", "--page-gutter-phone": "12px",
    }
    assert {name: tokens.get(name) for name in expected} == expected
    assert tokens.get("--cols-phone") == "1"
    for name, html in surfaces.items():
        css = _served_css(html)
        for _, body in _blocks(css):
            decls = dict(_decls(body))
            for prop in ("border-radius", "border-top-left-radius", "border-top-right-radius",
                         "border-bottom-left-radius", "border-bottom-right-radius"):
                for raw in decls.get(prop, "").split():
                    assert _radius_allowed(raw, tokens), f"{name}: unsupported radius {raw}"
            for prop, value in _decls(body):
                if prop in {"stroke", "stroke-width"}:
                    continue
                assert not re.search(r"(?<![\w.-])1\.5px(?![\w.-])", value), f"{name}: 1.5px outside drawing stroke: {prop}: {value}"
                if prop in {"border", "border-top", "border-right", "border-bottom", "border-left", "border-width",
                            "border-top-width", "border-right-width", "border-bottom-width", "border-left-width"}:
                    sizes = re.findall(r"(\d+(?:\.\d+)?)px", _resolve(value, tokens))
                    assert all(float(size) in {1, 2, 3} for size in sizes), f"{name}: unsupported {prop} width {value}"


def _radius_allowed(raw, tokens):
    value = _px(_resolve(raw, tokens))
    return raw == "0" or (value is not None and value in {4, 6, 8, 14, 9999})


def _non_stroke_15px(css):
    return [f"{selector}: {prop}: {value}" for selector, body in _blocks(_strip_comments(css))
            for prop, value in _decls(body)
            if re.search(r"(?<![\w.-])1\.5px(?![\w.-])", value) and prop not in {"stroke", "stroke-width"}]


def test_the_rhythm_guard_rejects_out_of_scale_values():
    assert _non_stroke_15px("svg path { stroke-width: 1.5px }") == []
    assert _non_stroke_15px("button { border: 1.5px solid }") == ["button: border: 1.5px solid"]
    assert not _radius_allowed("10px", {})
    assert _radius_allowed("var(--radius-md)", {"--radius-md": "6px"})
    assert all(not _non_stroke_15px(_served_css(html)) for html in _surfaces().values())


def test_motion_entry_and_discrete_sheet_exit_use_their_own_timing(surfaces):
    css = _strip_comments(_served_css(surfaces["hubmux-root"]))
    item = next(body for selector, body in _blocks(css) if selector == "li")
    assert "var(--motion-duration-short) var(--motion-entry-easing)" in item

    mobile = _strip_comments(_served_css(surfaces["hub-mobile"]))
    hidden_sheet = next(body for selector, body in _blocks(mobile) if selector == "#psheet[hidden]")
    assert re.search(r"display\s+0s\s+allow-discrete", hidden_sheet)


def test_motion_tokens_and_one_reduced_motion_block_cover_every_surface(surfaces):
    token_css = _strip_comments(TOKENS_FILE.read_text())
    tokens = _vars(token_css)
    expected = {"--motion-duration-short": "150ms", "--motion-duration-enter": "200ms",
                "--motion-duration-exit": "150ms", "--motion-duration-long": "400ms",
                "--motion-entry-easing": "ease-out", "--motion-exit-easing": "ease-in"}
    assert {name: tokens.get(name) for name in expected} == expected
    blocks = re.findall(r"@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}", token_css)
    assert len(blocks) == 1, f"the token module has {len(blocks)} reduced-motion blocks"
    body = blocks[0]
    for uncovered in (".sub .qw .fl", "#bbody .it", "li"):
        assert uncovered in body, f"reduced motion misses the newly covered transition {uncovered}"
    for name, html in surfaces.items():
        served = _strip_comments(_served_css(html))
        css = served
        # The needle is the declarations, never the comments: a surface carries its own comment head (the dashboard's
        # page opens on its own note), and a comment difference says nothing about the tokens served.
        token = _strip_comments(TOKENS_FILE.read_text()).strip()
        assert token in served, f"{name}: the shared token module is not served"
        token_end = served.index(token) + len(token)
        local_css = served[token_end:]
        reduced_selectors = set(re.findall(r"([^{}]+)\{[^{}]*(?:transition|animation)[^{}]*\}", body))
        for selector, declarations in _blocks(local_css):
            if re.search(r"(?:animation(?:-[\w-]+)?|transition(?:-[\w-]+)?)\s*:", declarations):
                normalized = selector.strip()
                assert normalized not in reduced_selectors, \
                    f"{name}: surface CSS redeclares motion for {normalized} after shared tokens"
        assert len(re.findall(r"@media\s*\(prefers-reduced-motion:\s*reduce\)", css)) == 1, \
            f"{name}: the reduced-motion block is not the one shared token rule"
        assert "infinite" not in css.lower(), f"{name}: a motion effect repeats forever"
        for selector, declarations in _blocks(css):
            for prop, value in _decls(declarations):
                if prop == "transition" or prop.startswith("animation"):
                    if value not in {"none", "none!important"}:
                        assert "var(--motion-duration-" in value, f"{name}: {selector.strip()} has an un-tokenized duration: {value}"
            if re.search(r"(?:^|;)\s*(?:animation(?:-[\w-]+)?|transition(?:-[\w-]+)?)\s*:", declarations):
                assert selector.strip() in body, f"{name}: reduced motion misses animated selector {selector.strip()}"
    assert "transition:none" in body and "animation:none" in body


def test_the_surviving_hand_tuned_fill_states_its_purpose():
    comment = TOKENS_FILE.read_text().split(":root{")[0]
    assert re.search(r"--tint-ok[^.]*approved", comment, re.S), "--tint-ok names no purpose"
