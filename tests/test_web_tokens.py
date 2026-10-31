"""One visual language across the web surfaces (#908).

Every served surface carries the one token block in contrib/squad-goal/scripts/web-tokens.css. The checks read each
surface's served text and resolve the values in it: no font size under 13 px, body text at 15 px, the token color pairs
and every hard-coded text color at 4.5:1 or more, a 2 px focus ring, dark native controls, and control heights of 32 px
on the desktop and 44 px on the phone. No browser runs; every value is computed from the text the surface sends. The
height check models the cascade by rule, not by element match: a phone rule restated by the token block replaces its
desktop twin.
"""
import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
TOKENS_FILE = SCRIPTS / "web-tokens.css"
DASHBOARD = ROOT / "dashboard/agent-squad-run-dashboard.html"

FLOOR_PX = 13
BODY_PX = 15
CONTRAST_MIN = 4.5
DESKTOP_CONTROL_PX = 32
PHONE_CONTROL_PX = 44
TEXT_TOKENS = ["--fg", "--mute", "--acc", "--ok", "--warn", "--err", "--run", "--none", "--wait", "--drop"]
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
    hub = _load("hub_web_tokens", SCRIPTS / "hub.py")
    mux = _load("hubmux_web_tokens", SCRIPTS / "hubmux.py")
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
    block = TOKENS_FILE.read_text().strip()
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
