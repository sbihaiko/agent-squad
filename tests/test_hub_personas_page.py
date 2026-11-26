"""GitHub issue #3010: the Personas theme in the hub Settings page (SPEC 3009).

The Settings page carries a Personas theme beside Slack, on the desktop nav and the narrow-screen theme select. The theme
draws the read route's groups: one row for each client and persona id, with the name, the Gemini voice, the tone, the
declaring projects and the clients whose default the id is. Nothing is editable.

The seam under test is the served page: its nav, its theme panel and its row renderer run in node. No browser runs here.
"""
import importlib.util
import json
import pathlib
import re
import shutil
import subprocess
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib" / "squad-goal" / "scripts" / "hub.py"


@pytest.fixture(scope="module")
def hub():
    mp = pytest.MonkeyPatch()
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("SG_SQUAD", str(ROOT))):
        mp.setenv(k, v)
    for k in ("SG_HUB_DIR", "SG_STATE", "SG_CHECKOUT", "SG_SHARED", "SG_PROFILES_DIR"):
        mp.setenv(k, tempfile.mkdtemp())
    spec = importlib.util.spec_from_file_location("hub_personas_page", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    yield mod
    mp.undo()


def nav_themes(page):
    nav = re.search(r'<nav class=snav aria-label="Settings themes">(.*?)</nav>', page, re.S).group(1)
    return re.findall(r"<button data-sp=(\w+)>", nav)


def test_the_settings_nav_shows_a_personas_theme_beside_slack(hub):
    """#3010 acceptance 1: the desktop theme list carries Personas, right after Slack."""
    nav = nav_themes(hub.PAGE)
    assert "prs" in nav, nav
    assert nav.index("prs") == nav.index("slk") + 1, nav
    button = re.search(r"<button data-sp=prs>(.*?)</button>", hub.PAGE, re.S).group(1)
    assert "Personas" in button, button


def test_the_narrow_screen_theme_select_offers_personas(hub):
    """#3010 acceptance 2: the phone's theme select carries the Personas theme."""
    sel = re.search(r"<select id=ssel[^>]*>(.*?)</select>", hub.PAGE, re.S).group(1)
    assert "<option value=prs>Personas</option>" in sel, sel


def test_the_personas_theme_panel_is_on_the_page(hub):
    """The theme shows one panel at a time: the Personas panel carries the theme's own body and its nav state."""
    assert re.search(r"<details id=prs class=sp data-sp=prs>", hub.PAGE), "the Personas panel is missing"
    assert "id=prbody" in hub.PAGE, "the Personas panel carries no body"


def test_the_theme_hash_opens_personas(hub):
    """The phone's Settings link names the theme: a `#set-prs` address opens the Personas theme."""
    m = re.search(r"^const themeHash=.*$", hub.PAGE, re.M).group(0)
    assert "prs" in m, m


def _node(body):
    if shutil.which("node") is None:
        pytest.skip("node is required")
    out = subprocess.run(["node", "-"], input=body, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout


def _block(page):
    block = re.search(r"^// PRS\{.*?^// \}PRS$", page, re.M | re.S)
    assert block, "the page carries no PRS block"
    return block.group(0)


ROW = {"id": "hal", "name": "HAL9K", "voice": "Kore", "tone": "Calm and direct.",
       "projects": [{"id": "alpha", "label": "alpha", "file": "slack.yaml"}],
       "default_of": ["acme"], "drift": True, "read_only": False}


def test_a_row_stacks_each_field_under_its_own_label_on_a_narrow_screen(hub):
    """#3010 acceptance 11: every field of a row carries its own label, and the narrow media query puts each field on its
    own line under that label. The text breaks between words alone, so no word is cut."""
    esc = re.search(r"^const esc=.*$", hub.PAGE, re.M).group(0)
    src = "\n".join([esc, _block(hub.PAGE), f"console.log(JSON.stringify(prRow({json.dumps(ROW)})))"])
    html = json.loads(_node(src))
    for label in ("Persona", "Voice", "Tone", "Projects", "Default of", "Flags"):
        assert f'data-l="{label}"' in html, (label, html)
    page = hub.PAGE
    narrow = re.search(r"@media \(max-width:600px\)\{[^}]*#prbody[^}]*\}", page)
    assert narrow, "the Personas table has no narrow-screen rule"
    assert "content:attr(data-l)" in page, "a stacked cell does not show its label"
    rules = re.search(r"#tab-set #prbody \.ptab td\{[^}]*\}", page).group(0)
    assert "overflow-wrap:break-word" in rules and "hyphens:none" in rules, rules
    assert '<wbr>' in html, "the file name carries a break point after each dot, so no word is cut inside"


def test_the_renderer_draws_each_client_group_with_its_rows(hub):
    """The theme draws one heading for each client, and each row names the persona, its voice and tone, its declaring
    projects with their files, and its default clients. A drift and a read-only row each carry their flag."""
    esc = re.search(r"^const esc=.*$", hub.PAGE, re.M).group(0)
    answer = {"err": None, "groups": [
        {"id": "acme", "name": "Acme", "rows": [ROW, {**ROW, "id": "iara", "name": "Iara", "voice": "Sulafat",
                                                      "drift": False, "read_only": True, "default_of": []}]},
        {"id": "ghost", "name": "ghost", "rows": []}]}
    stub = 'const STORE={};const $=s=>STORE[s]||(STORE[s]={innerHTML:"",textContent:""});'
    body = "\n".join([stub, esc, _block(hub.PAGE),
                      f"prS={json.dumps(answer)};prRender();console.log(JSON.stringify({{html:$('#prbody').innerHTML,nav:$('#prsnav').textContent}}))"])
    got = json.loads(_node(body))
    html = got["html"]
    assert "Acme" in html and "ghost" not in html, "a group with no row draws no heading: " + html
    assert "HAL9K" in html and "Kore" in html and "Calm and direct." in html, html
    assert "alpha" in html and "slack.<wbr>yaml" in html, html
    assert "acme" in html, html
    assert "drift" in html.lower() and "read-only" in html.lower(), html
    assert html.count('<tr><td data-l="Persona">') == 2, html
    assert "1" in got["nav"], got["nav"]


def test_edit_and_add_open_the_row_of_the_client_whose_table_holds_the_button(hub):
    """Issue #3028: two clients declare one persona id with different projects and voices. Edit and "Add to project" in
    the second client's table open that client's row, never the first match by id alone."""
    esc = re.search(r"^const esc=.*$", hub.PAGE, re.M).group(0)
    first = {**ROW, "voice": "Alnilam", "drift": False,
             "projects": [{"id": "zesty", "label": "zesty", "file": "slack.yaml"}]}
    second = {**ROW, "voice": "Charon", "drift": False,
              "projects": [{"id": "squad", "label": "squad", "file": "slack.yaml"}]}
    answer = {"err": None, "projects": [{"id": "zesty"}, {"id": "squad"}, {"id": "other"}],
              "groups": [{"id": "wellzesta", "name": "Wellzesta", "rows": [first]},
                         {"id": "sandro", "name": "Sandro", "rows": [second]}]}
    stub = 'const STORE={};const $=s=>STORE[s]||(STORE[s]={innerHTML:"",textContent:""});'
    probe = (
        f"prS={json.dumps(answer)};prRender();"
        "const html=$('#prbody').innerHTML;"
        "const edits=[...html.matchAll(/<button ([^>]*)data-predit=\"[^\"]*\"[^>]*>Edit<\\/button>/g)].map(m=>m[0]);"
        "const attrs=s=>Object.fromEntries([...s.matchAll(/data-([a-z]+)=\"([^\"]*)\"/g)].map(m=>[m[1],m[2]]));"
        "const e2=attrs(edits[1]);prStartEdit(e2.predit,e2.prclient);"
        "const ed={voice:prEd.voice,projects:prEd.projects};"
        "const adds=[...html.matchAll(/<button [^>]*data-praddrow=\"[^\"]*\"[^>]*>/g)].map(m=>m[0]);"
        "const a2=attrs(adds[1]);prStartAdd(a2.praddrow,a2.prclient);"
        "console.log(JSON.stringify({ed,add:prNew.projects}))")
    body = "\n".join([stub, esc, _block(hub.PAGE), probe])
    got = json.loads(_node(body))
    assert got["ed"] == {"voice": "Charon", "projects": ["squad"]}, got
    assert got["add"] == ["zesty", "other"], got
