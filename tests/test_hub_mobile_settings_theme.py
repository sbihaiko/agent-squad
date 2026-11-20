"""The phone's Settings entry opens the desktop Settings tab on the theme last used there (SPEC 1117, #1286).

The phone's handoff block and the desktop's boot routing run under node over a stubbed DOM.
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
MOBILE_HTML = ROOT / "contrib/squad-goal/scripts/hub-mobile.html"
NODE = shutil.which("node")

needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_mobile_settings_theme", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _handoff_block(page):
    assert page.count("// settings-handoff:begin") == 1 and page.count("// settings-handoff:end") == 1, "the phone has no single handoff block"
    return page[page.index("// settings-handoff:begin"):page.index("// settings-handoff:end")]


def _phone_link_href(stored):
    page = MOBILE_HTML.read_text()
    block = _handoff_block(page)
    src = (
        f"const STORED={json.dumps(stored)};"
        "const localStorage={getItem:k=>k==='hub'?STORED:null};"
        "const els={'more-settings':{href:'./#set'}};const $=id=>els[id];\n"
        f"{block}\n"
        "console.log(JSON.stringify(els['more-settings'].href));"
    )
    return _node(src)


def _desktop_tab_and_theme(hub, hash_):
    page = hub.PAGE
    sth = re.search(r"^const STH=[^\n]*", page, re.M).group(0)
    first = re.search(r"^const SET_FIRST=[^\n]*", page, re.M).group(0)
    initial = re.search(r"^let sTheme=[^\n]*", page, re.M).group(0)
    hash_map = re.search(r"^const HASH=[^\n]*", page, re.M).group(0)
    tab_fn = re.search(r"^function tabFromLocation\(.*$", page, re.M).group(0)
    boot = re.search(r"^const themeHash=[^\n]*", page, re.M).group(0)
    src = (
        f"const location={{hash:{json.dumps(hash_)}}};\nconst ls={{}};\n{sth}\n{first}\n{initial}\n{hash_map}\n{tab_fn}\n{boot}\n"
        "console.log(JSON.stringify([tabFromLocation(),sTheme]));"
    )
    return json.loads(_node(src))


def _node(src):
    result = subprocess.run([NODE, "-e", src], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@needs_node
@pytest.mark.parametrize(
    "stored,href",
    [
        ('{"sth":"acc"}', "./#set-acc"),
        ('{"sth":"sub","pins":[]}', "./#set-sub"),
        ("{}", "./#set-lim"),
        ('{"sth":"bogus"}', "./#set-lim"),
        ('{"sth":"lop"}', "./#set-lim"),
        ("not json", "./#set-lim"),
    ],
)
def test_the_phone_settings_link_carries_the_last_theme_or_the_first_one(stored, href):
    assert json.loads(_phone_link_href(stored)) == href


def test_the_phone_theme_list_and_first_theme_match_the_desktop(hub):
    """#2515: the phone handoff names the desktop's themes in the desktop's order and falls back to the desktop's first
    theme, so the two defaults cannot drift."""
    desktop = re.search(r"^const STH=\{(.*?)\};", hub.PAGE, re.M).group(1)
    themes = re.findall(r"(\w+):", desktop)
    first = re.search(r'^const SET_FIRST="(\w+)";', hub.PAGE, re.M).group(1)
    block = _handoff_block(MOBILE_HTML.read_text())
    phone_list = re.findall(r'"(\w+)"', re.search(r"SET_THEMES = \[(.*?)\]", block).group(1))
    fallbacks = re.search(r't : "(\w+)"; \} catch \(e\) \{ return "(\w+)";', block)
    assert fallbacks, "the phone handoff no longer falls back to a first theme"
    assert phone_list == themes, f"the phone names {phone_list}, the desktop {themes}"
    assert set(fallbacks.groups()) == {first}, f"the phone falls back to {fallbacks.groups()}, the desktop to {first!r}"


@needs_node
def test_an_old_loop_theme_link_opens_the_first_remaining_theme(hub):
    """#2515: a saved #set-lop link opens the first theme left, never a Settings page with no theme shown."""
    assert _desktop_tab_and_theme(hub, "#set-lop") == ["set", "lim"]


@needs_node
def test_the_desktop_opens_settings_on_the_themed_hash(hub):
    assert _desktop_tab_and_theme(hub, "#set-acc") == ["set", "acc"]
    assert _desktop_tab_and_theme(hub, "#set-bogus") == ["set", "lim"]


def _redirect_condition(hub):
    """The desktop page's narrow-screen redirect condition, read from the served page (#1446)."""
    m = re.search(r"<script>if\((.*?)\)location\.replace", hub.PAGE, re.S)
    assert m, "the desktop page has no narrow-screen redirect"
    return m.group(1)


STORE_STUB = (
    "const store={};"
    "const localStorage={getItem:k=>(k in store?store[k]:null),setItem:(k,v)=>{store[k]=String(v)},"
    "removeItem:k=>{delete store[k]}};"
    "const matchMedia=q=>({matches:true});"
)


@needs_node
def test_the_phone_settings_entry_names_the_desktop_view_before_it_navigates(hub):
    """#1446: the Settings entry writes squad-view=desktop for the saved theme's anchor, so once it runs the desktop
    page's narrow-screen redirect is false and the owner is not sent back to the phone."""
    block = _handoff_block(MOBILE_HTML.read_text())
    cond = _redirect_condition(hub)
    src = (
        STORE_STUB +
        "store['hub']=JSON.stringify({sth:'acc'});"
        "const els={'more-settings':{href:'./#set'}};const $=id=>els[id];\n"
        f"{block}\n"
        "els['more-settings'].onclick();\n"
        "console.log(JSON.stringify({href:els['more-settings'].href,view:store['squad-view']??null,"
        f"redirect:Boolean({cond})}}));"
    )
    got = json.loads(_node(src))
    assert got["href"] == "./#set-acc", "the entry does not open the saved Settings theme"
    assert got["view"] == "desktop", "the entry does not name the desktop view"
    assert got["redirect"] is False, "the redirect sends the owner back to the phone"


@needs_node
def test_the_desktop_mobile_link_clears_the_view_preference(hub):
    """#1446: the desktop 'mobile view' link clears squad-view, so the narrow-screen redirect works again."""
    m = re.search(r'<script>(document\.getElementById\("mobile-link"\).*?)</script>', hub.PAGE, re.S)
    assert m, "the desktop page has no mobile view link script"
    cond = _redirect_condition(hub)
    src = (
        STORE_STUB +
        "store['squad-view']='desktop';"
        "const el={href:'#'};"
        "const document={getElementById:id=>el};"
        "const location={pathname:'/p/x'};"
        f"{m.group(1)}\n"
        "el.onclick();\n"
        "console.log(JSON.stringify({href:el.href,view:store['squad-view']??null,"
        f"redirect:Boolean({cond})}}));"
    )
    got = json.loads(_node(src))
    assert got["href"] == "/p/x/mobile"
    assert got["view"] is None, "the mobile view link leaves the view preference stored"
    assert got["redirect"] is True, "the cleared preference leaves the redirect inert"
