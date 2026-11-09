"""The hub pages name their language, their landmarks and every control, and a control meets its page's tap floor.

The desktop page holds a 28 px floor (#912); the phone page holds a 44 px floor, an 11 px floor on its functional text
and 4.5:1 on the text it draws (SPEC 1596, #1605). The pages are read as served strings. A control is named by
aria-label, aria-labelledby, an enclosing label or a label's `for`; a placeholder alone is not a name. The height check
reads the declared px heights of every rule whose selector names a control. Controls built by script at run time are
not seen here.
"""
import importlib.util
import os
import re
import tempfile
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
TOKENS = ROOT / "contrib/squad-goal/scripts/web-tokens.css"
# The desktop keeps its 28 px floor; the phone, held in the hand, needs the 44 px a thumb hits (#1605).
MIN_CONTROL_PX = {"PAGE": 28, "MOBILE_PAGE": 44}
MIN_TEXT_PX = 11
MIN_CONTRAST = 4.5
CONTROL = re.compile(r"(^|[\s>+~,])(button|select|input|textarea|summary)\b|\[role=button\]")
HEIGHT = re.compile(r"(?<![\w-])(?:min-)?height\s*:\s*(\d+(?:\.\d+)?)px")
FONT_SIZE = re.compile(r"(?<![\w-])font-size\s*:\s*([^;}]+)")
FONT_SHORTHAND = re.compile(r"(?<![\w-])font\s*:\s*([^;}]+)")
LIVE = re.compile(r"<(\w+)\b([^>]*\baria-live=[\"']?(\w+)[\"']?[^>]*)>")
ROLE = re.compile(r"(?<![\w-])role=[\"']?(\w+)")
LIVE_ROLES = {"status", "alert", "log"}


class _Landmarks(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.html_lang = None
        self.landmarks = set()

    def handle_starttag(self, tag, attrs):
        if tag == "html":
            self.html_lang = dict(attrs).get("lang")
        if tag in ("main", "aside"):
            self.landmarks.add(tag)


CONTROL_TAG = re.compile(r"<(input|select|textarea)\b((?:[^>\"']|\"[^\"]*\"|'[^']*')*)>")
LABEL_FOR = re.compile(r"<label\b[^>]*?\bfor=[\"']?([\w-]+)")


def _markup(page):
    p = _Landmarks()
    p.feed(page)
    p.close()
    return p


def _inside_label(page, pos):
    before = page[:pos]
    return before.rfind("<label") > before.rfind("</label>")


def _unnamed(page):
    fors = set(LABEL_FOR.findall(page))
    out = []
    for m in CONTROL_TAG.finditer(page):
        attrs = m.group(2)
        if re.search(r"(?<![\w-])type=[\"']?(hidden|button|submit|reset)\b", attrs):
            continue
        ident = re.search(r"(?<![\w-])id=[\"']?([\w-]+)", attrs)
        named = (
            re.search(r"(?<![\w-])aria-(label|labelledby)=", attrs)
            or (ident and ident.group(1) in fors)
            or _inside_label(page, m.start())
        )
        if not named:
            out.append(m.group(0)[:120])
    return out


def _short_controls(page, floor):
    css = "".join(re.findall(r"<style>(.*?)</style>", page, flags=re.S))
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out = []
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        if not any(CONTROL.search(s) for s in m.group(1).split(",")):
            continue
        for h in HEIGHT.findall(m.group(2)):
            if float(h) < floor:
                out.append(f"{m.group(1).strip()} -> {h}px")
    return out


def _css(page):
    return re.sub(r"/\*.*?\*/", "", "".join(re.findall(r"<style>(.*?)</style>", page, flags=re.S)), flags=re.S)


def _tokens():
    return dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", TOKENS.read_text()))


def _size(value, tokens):
    """One font declaration to px: a token reference reads the token file, a bare px stays as it is."""
    value = value.strip()
    m = re.fullmatch(r"var\((--[\w-]+)\)", value)
    if m:
        value = tokens.get(m.group(1), value)
    m = re.fullmatch(r"(\d+(?:\.\d+)?)px", value.strip())
    return float(m.group(1)) if m else None


def _small_text(page):
    """Every declared text size under the floor, with the rule that declares it."""
    tokens = _tokens()
    out = []
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", _css(page)):
        body = m.group(2)
        for decl in FONT_SIZE.findall(body) + FONT_SHORTHAND.findall(body):
            for part in decl.split():
                px = _size(part, tokens)
                if px is not None and px < MIN_TEXT_PX:
                    out.append(f"{m.group(1).strip()} -> {part}")
    return out


def _luminance(hex_color):
    channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(fg, bg):
    hi, lo = sorted([_luminance(fg), _luminance(bg)], reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _live_without_role(page):
    """Each live region and the role it announces as: an aria-live without a role announces as nothing named.

    `aria-live="off"` is no live region at all, so it is not read here."""
    out = []
    for m in LIVE.finditer(page):
        if m.group(3) == "off":
            continue
        role = ROLE.search(m.group(2))
        if not role or role.group(1) not in LIVE_ROLES:
            out.append(m.group(0)[:100])
    return out


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_accessibility", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(params=["PAGE", "MOBILE_PAGE"])
def served(request, hub):
    return request.param, getattr(hub, request.param)


def test_every_hub_page_declares_its_language(served):
    name, page = served
    assert _markup(page).html_lang == "en", f"{name} has no lang=en on its html element"


def test_the_desktop_page_has_main_and_aside_landmarks(hub):
    assert {"main", "aside"} <= _markup(hub.PAGE).landmarks


def test_the_phone_page_has_a_main_landmark(hub):
    assert "main" in _markup(hub.MOBILE_PAGE).landmarks


def test_every_form_control_has_a_name(served):
    name, page = served
    assert _unnamed(page) == [], f"{name} has unnamed controls"


def test_no_control_is_declared_under_its_page_floor(served):
    name, page = served
    floor = MIN_CONTROL_PX[name]
    assert _short_controls(page, floor) == [], f"{name} declares a control under {floor} px"


def test_the_phone_page_holds_a_44_px_tap_floor(hub):
    assert MIN_CONTROL_PX["MOBILE_PAGE"] == 44, "the phone's tap floor is not the 44 px a thumb hits"


@pytest.mark.parametrize("name", ["PAGE", "MOBILE_PAGE"])
def test_every_live_region_announces_with_a_role(hub, name):  # noqa: F811
    page = getattr(hub, name)
    assert _live_without_role(page) == [], f"{name} carries a live region with no announced role"


def test_no_functional_text_falls_under_11_px_on_the_phone_page(hub):  # noqa: F811
    assert _small_text(hub.MOBILE_PAGE) == [], "the phone page declares text under the 11 px floor"


def test_the_phone_page_text_clears_4_5_to_1_on_the_surfaces_it_draws_on(hub):  # noqa: F811
    tokens = _tokens()
    css = _css(hub.MOBILE_PAGE)
    pairs = [("fg", "bg"), ("fg", "panel"), ("mute", "bg"), ("mute", "panel"), ("acc", "bg"), ("acc", "panel"),
             ("ok", "panel"), ("warn", "panel"), ("err", "panel"), ("on-tone", "err"), ("on-tint", "tint-err")]
    for fg, bg in pairs:
        # The page must draw this pair, or the pair guards a colour no reader ever sees.
        assert re.search(rf"color:var\(--{re.escape(fg)}\)", css), f"the phone page draws no text in --{fg}"
        assert re.search(rf"background:var\(--{re.escape(bg)}\)", css) or bg == "bg", f"the phone page draws no surface in --{bg}"
        ratio = _contrast(tokens[f"--{fg}"], tokens[f"--{bg}"])
        assert ratio >= MIN_CONTRAST, f"--{fg} on --{bg} is {ratio:.2f}:1, under {MIN_CONTRAST}:1"
