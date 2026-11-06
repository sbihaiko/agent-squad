"""The resting boundary of an inactive indicator and a card border meets 3:1 on panel and card in every theme (#1456)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOKENS_FILE = ROOT / "contrib/squad-goal/scripts/web-tokens.css"
PAGE_CSS = ROOT / "dashboard/page.css"
NON_TEXT_MIN = 3.0
SURFACES = ("--panel", "--card")


def _decls(text):
    return {name: value.strip() for name, value in re.findall(r"(--[\w-]+)\s*:\s*([^;{}]+)", text)}


def _themes(css):
    root = _decls(re.search(r":root\{([^}]*)\}", css).group(1))
    phone = dict(root)
    for media in re.findall(r"@media[^{]*\{((?:[^{}]*\{[^{}]*\})*)\}", css):
        phone.update(_decls(media))
    return {"desktop": root, "phone": phone}


MIX = re.compile(r"color-mix\(in srgb,\s*var\((--[\w-]+)\),\s*var\((--[\w-]+)\)\s*(\d+)%\)")


def _mix(tokens, formula):
    a, b, pct = MIX.fullmatch(formula).groups()
    share = int(pct) / 100
    ca, cb = (tokens[a].lstrip("#"), tokens[b].lstrip("#"))
    channels = [round((1 - share) * int(ca[i:i + 2], 16) + share * int(cb[i:i + 2], 16)) for i in (0, 2, 4)]
    return "#" + "".join(f"{c:02x}" for c in channels)


def _luminance(hex_colour):
    h = hex_colour.lstrip("#")
    channels = []
    for i in (0, 2, 4):
        c = int(h[i:i + 2], 16) / 255
        channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast(a, b):
    hi, lo = sorted([_luminance(a), _luminance(b)], reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_the_resting_border_role_meets_three_to_one_on_panel_and_card_in_every_theme():
    m = re.search(r"--edge-rest\s*:\s*(color-mix\([^;]*\))", PAGE_CSS.read_text())
    assert m, "dashboard/page.css defines no --edge-rest role"
    for theme, tokens in _themes(TOKENS_FILE.read_text()).items():
        for surface in SURFACES:
            ratio = _contrast(_mix(tokens, m.group(1)), tokens[surface])
            assert ratio >= NON_TEXT_MIN, f"--edge-rest on {surface} in the {theme} theme is {ratio:.2f}:1, under 3:1"


def test_inactive_group_squares_read_the_resting_border_role():
    rest = re.search(r"\.group-card \.sq rect\s*\{([^}]*)\}", PAGE_CSS.read_text()).group(1)
    assert "stroke:var(--edge-rest)" in rest, "an inactive group square still reads --line at rest"


def test_the_line_token_keeps_its_value_and_its_card_use():
    css = TOKENS_FILE.read_text()
    assert _themes(css)["desktop"]["--line"] == "#2a303b", "the line token changed its value"
    rest = re.search(r"\.card rect\s*\{([^}]*)\}", PAGE_CSS.read_text()).group(1)
    assert "stroke:var(--line)" in rest, "a card's resting border no longer reads line"
