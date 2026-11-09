"""The phone page's visual floor (SPEC 1596, #1605): no emoji, and every icon is an inline SVG drawn at one stroke width.

The page is read as the string the hub serves, its markup and its script. An emoji is a pictographic code point the page
must not carry; an icon is an inline `svg.ico` whose stroke comes from the one `.ico` rule, so no icon can drift to a
second weight. A control that draws an icon glyph as its whole word is a text icon, not a drawing, and fails here.
"""
import importlib.util
import os
import re
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HUB_PY = os.path.join(ROOT, "contrib/squad-goal/scripts/hub.py")
EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️]")
# The glyphs a page reaches for when it has no icon set: angle quotes, guillemets and bullets.
GLYPH = re.compile("[‹›«»•]")
BUTTON = re.compile(r"<button\b[^>]*>(.*?)</button>", re.S)
STROKE = re.compile(r"(?<![\w-])stroke-width\s*:\s*([\d.]+)")


@pytest.fixture(scope="module")
def mobile():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_mobile_icons", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.MOBILE_PAGE


def _page_only(page):
    """The page before its script: the markup and the stylesheet, with the served bodies stripped."""
    return page[: page.index("<script>")]


def test_the_phone_page_draws_no_emoji(mobile):
    found = sorted(set(EMOJI.findall(mobile)))
    assert found == [], f"the phone page draws emoji: {found}"


def test_no_button_draws_an_icon_glyph_as_its_word(mobile):
    offenders = [m.group(1).strip()[:40] for m in BUTTON.finditer(mobile) if GLYPH.search(m.group(1))]
    assert offenders == [], f"a button draws a glyph instead of an icon: {offenders}"


def test_the_phone_page_draws_its_icons_as_inline_svgs(mobile):
    icons = re.findall(r'<svg\b[^>]*class="?ico\b[^>]*>', _page_only(mobile))
    assert len(icons) >= 10, f"the phone page draws {len(icons)} inline icons, not the controls it names"


def test_one_rule_holds_the_one_stroke_width_of_every_icon(mobile):
    css = re.sub(r"/\*.*?\*/", "", _page_only(mobile), flags=re.S)
    rule = re.search(r"\.ico\{([^}]*)\}", css)
    assert rule, "the phone page declares no .ico rule"
    widths = set(STROKE.findall(rule.group(1)))
    assert len(widths) == 1, f"the .ico rule declares {sorted(widths)} stroke widths, not one"
    assert re.search(r"(?<![\w-])fill\s*:\s*none", rule.group(1)), "an icon is filled, not stroked"
    inline = re.findall(r'<svg\b[^>]*class="?ico\b[^>]*>', _page_only(mobile))
    for tag in inline:
        assert "stroke-width" not in tag, f"an icon carries its own stroke width: {tag[:80]}"


def test_the_script_builds_no_icon_from_a_glyph(mobile):
    assert '"\\u2039 ' not in mobile and "\"‹ " not in mobile, "the script prefixes a glyph onto a Back"
    assert 'textContent = "\\u203a"' not in mobile and 'textContent = "›"' not in mobile, \
        "the script draws a row's chevron as a glyph"
