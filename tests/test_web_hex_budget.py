"""One palette: no colour literal outside the token module or named formulas.

The guard reads every served surface. The token module owns the palette and names the only formula carve-outs; a new
literal elsewhere fails. The checks read served text; no browser runs.
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
DASHBOARD_SOURCES = [ROOT / "dashboard/page.html", ROOT / "dashboard/page.css",
                     *(ROOT / "dashboard/page").glob("*.js"), *(ROOT / "dashboard/core").glob("*.js")]
PALETTE_ROLES = ("bg", "panel", "card", "input", "fg", "mute", "acc", "ok", "warn", "wait", "err", "run", "none", "drop")

# A hex with a word boundary, never an HTML numeric entity (&#9662;): the sign of a colour, not of a glyph.
HEX = re.compile(r"(?<!&)#[0-9a-fA-F]{3,8}\b")
LITERAL_CARVEOUTS = {
    "image": ("url(", r"url(\s*)\("),
    "third-party embed": ("<iframe", r"(<\/?)iframe\b"),
    "computed blend": ("color-mix(", r"color-mix(\s*)\("),
}
BUDGET_FILE = SCRIPTS / "web-hex-budget.json"


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
        "dashboard": DASHBOARD.read_text(),
    }


def _strip_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"(?<!:)//[^\n]*", "", text)
    return text


def _token_module(tokens=None):
    token_text = tokens or TOKENS_FILE.read_text()
    return re.sub(r"/\*\s*literal-carveout\b.*?\*/", "", token_text, flags=re.S).strip()


def _literals(html):
    """Every uncarved colour literal in one served surface."""
    token_text = TOKENS_FILE.read_text()
    text = html.replace(_token_module(token_text), "")
    text = re.sub(r"/\*\s*literal-carveout\b.*?\*/", "", text, flags=re.S)
    for name, (_, token_pattern) in LITERAL_CARVEOUTS.items():
        if re.search(rf"/\*\s*literal-carveout\s+{re.escape(name)}\s*:", token_text):
            replacement = {"image": "image-formula", "third-party embed": "embed",
                           "computed blend": "blend-formula"}[name]
            def replace_token(match, name=name, replacement=replacement):
                if name == "third-party embed":
                    return match.group(1) + replacement
                return replacement + match.group(1) + "("
            text = re.sub(token_pattern, replace_token, text, flags=re.I)
    text = re.sub(r"\$\(\"#[^\"]+\"\)", "", text)
    text = re.sub(r"(?:querySelector(?:All)?|getElementById)\(\"#[^\"]+\"\)", "", text)
    css = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", text, flags=re.S))
    css = re.sub(r"[^{}]+(?=\{)", "", _strip_comments(css))
    quoted = "\n".join(m.group(0) for m in re.finditer(r"\"[^\"]*\"|'[^']*'", _strip_comments(text)))
    scripts = "\n".join(re.findall(r"<script[^>]*>(.*?)</script>", text, flags=re.S))
    scripts = re.sub(r"(?:querySelector(?:All)?|getElementById)\([\"'][^\"']*[\"']\)", "", scripts)
    scripts = re.sub(r"\$\([\"'][^\"']*[\"']\)", "", scripts)
    return HEX.findall(css + "\n" + quoted + "\n" + _strip_comments(scripts))


def _literals_by_surface(surfaces=None):
    return {name: _literals(html) for name, html in (surfaces or _surfaces()).items()}


def _hub_javascript_literals(html):
    scripts = "\n".join(re.findall(r"<script[^>]*>(.*?)</script>", html, flags=re.S))
    scripts = re.sub(r"(?:querySelector(?:All)?|getElementById)\([\"'][^\"']*[\"']\)", "", scripts)
    scripts = re.sub(r"\$\([\"'][^\"']*[\"']\)", "", scripts)
    return HEX.findall(_strip_comments(scripts))


def test_hub_javascript_has_no_colour_literals(surfaces):
    assert not _hub_javascript_literals(surfaces["hub"]), "hub JavaScript carries a colour literal"


def test_the_guard_catches_a_colour_literal_in_hub_javascript(surfaces):
    poisoned = surfaces["hub"].replace("</script>", 'const poison = "#123456";</script>', 1)
    assert "#123456" in _hub_javascript_literals(poisoned)


def test_hub_charts_read_chart_roles(surfaces):
    scripts = "\n".join(re.findall(r"<script[^>]*>(.*?)</script>", surfaces["hub"], flags=re.S))
    regions = (
        ("function bars(", "function cmp(", ("--chart-grid", "--chart-axis", "--chart-bar", "--chart-bar-max")),
        ("const PCOL", "function hlPie(", ("--chart-series-1", "--chart-series-other", "--chart-bg")),
        ("const flowAxis", "const cfdAxis", ("--chart-grid", "--chart-axis")),
        ("const ltChart", "const flowLead", ("--chart-series-3", "--chart-series-8", "--chart-ok", "--chart-series-5")),
    )
    for start, end, roles in regions:
        region = scripts.split(start, 1)[1].split(end, 1)[0]
        for role in roles:
            assert f"var({role})" in region, f"{start} does not read {role}"

    bar = regions[0]
    bar_script = scripts.split(bar[0], 1)[1].split(bar[1], 1)[0]
    value = re.search(r"--chart-bar\s*:\s*([^;}]+)", TOKENS_FILE.read_text()).group(1)
    assert value not in bar_script, "the bar chart copies the role value instead of reading its role"


def test_the_guard_rejects_a_literal_in_both_hub_sides(surfaces):
    hub = surfaces["hub"]
    css_poison = hub.replace("</style>", ".poison{color:#123456}</style>", 1)
    js_poison = hub.replace("</script>", 'const poison = "#123456";</script>', 1)
    assert "#123456" in _literals(css_poison), "the hub CSS literal escaped the guard"
    assert "#123456" in _literals(js_poison), "the hub JavaScript literal escaped the guard"


@pytest.fixture(scope="module")
def surfaces():
    return _surfaces()


def _blocks(css):
    return [(s.strip(), d) for s, d in re.findall(r"([^{}]+)\{([^{}]*)\}", _strip_comments(css))]


def _root_overrides(html, tokens):
    """The palette roles a surface redefines outside the shared module: a second palette, not a read of the one."""
    text = _strip_comments(html.replace(_token_module(tokens), ""))
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


def test_the_guard_rejects_a_new_literal_on_every_surface_and_allows_named_formulas(surfaces):
    assert set(surfaces) == set(LITERAL_SURFACES), "every served surface is explicitly covered"
    for name, surface in surfaces.items():
        poisoned = surface.replace("</style>", ".poison{color:#123456}</style>", 1) if "</style>" in surface else surface + '<style>.poison{color:#123456}</style>'
        found = _literals_by_surface({name: poisoned})[name]
        assert "#123456" in found, f"{name}: scanner did not see injected literal"
        assert not _literals_by_surface({name: surface})[name], f"{name}: existing literal"

    formulas = (
        '<style>.image{background-image:url("image.svg")}</style>',
        '<iframe src="https://example.test/embed"></iframe>',
        '<style>.blend{color:color-mix(in srgb, var(--fg), var(--bg))}</style>',
    )
    for name, example in zip(LITERAL_CARVEOUTS, formulas, strict=True):
        tokens = TOKENS_FILE.read_text()
        assert f"literal-carveout {name}:" in tokens
        assert not _literals(example), f"named {name} formula is not allowed"
    assert _literals('<style>.blend{color:color-mix(in srgb, var(--fg), var(--bg)); outline-color:#123456}</style>')
    assert _literals('<style>.image{background:url(#123456)}</style>')
    assert _literals('<iframe style="color:#123456"></iframe>')


def test_the_completed_literal_budget_ledger_is_absent():
    assert not BUDGET_FILE.exists()


def test_the_guard_scans_exactly_every_named_surface(surfaces):
    found = _literals_by_surface(surfaces)
    assert set(found) == set(LITERAL_SURFACES)
    assert all(not values for values in found.values()), found


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


def test_the_dashboard_writes_no_palette_of_its_own():
    for path in DASHBOARD_SOURCES:
        assert not _root_overrides(path.read_text(), TOKENS_FILE.read_text()), f"{path.relative_to(ROOT)} defines a palette of its own"


def test_the_hub_and_the_dashboard_read_the_one_module(surfaces):
    """The two surfaces inline the same module, and neither overrides a palette role beside it."""
    tokens = TOKENS_FILE.read_text()
    for name in ("hub", "dashboard"):
        assert _token_module(tokens) in surfaces[name], f"{name} does not carry the token module"
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
INLINE_SURFACES = ("board-lanes", "run-sheet")
LITERAL_SURFACES = (*PALETTE_SURFACES, *INLINE_SURFACES)


def test_no_surface_carries_a_colour_literal(surfaces):
    """Every named served surface is clear outside the named formulas."""
    found = _literals_by_surface(surfaces)
    assert set(found) == set(LITERAL_SURFACES)
    assert not {name: sorted(set(values)) for name, values in found.items() if values}, found


def test_the_phone_corrects_the_misspelt_token(surfaces):
    """The phone reads `mute`, never the misspelling `muted` whose fallback hid the defect."""
    assert "var(--muted" not in surfaces["hub-mobile"], "the phone still reads the misspelt --muted"
    assert "var(--mute)" in surfaces["hub-mobile"], "the phone does not read --mute"


def test_no_surface_declares_a_palette_of_its_own(surfaces):
    """Every surface inlines the one module and redefines no palette role beside it."""
    tokens = TOKENS_FILE.read_text()
    for name in PALETTE_SURFACES:
        assert _token_module(tokens) in surfaces[name], f"{name} does not carry the token module"
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
