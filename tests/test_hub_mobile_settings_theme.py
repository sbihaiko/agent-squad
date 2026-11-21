"""The phone carries no Settings entry (SPEC 2498, #2499), and the desktop still opens its Settings tab on a theme.

The desktop's boot routing runs under node over a stubbed DOM.
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


def test_the_phone_carries_no_settings_entry(hub):
    """SPEC 2498 (#2499), user story 39: Settings change only on the desktop, so the phone page holds neither a Settings
    link nor a handoff that opens the desktop Settings tab."""
    page = MOBILE_HTML.read_text()
    assert "settings-handoff" not in page, "the phone still carries the Settings handoff"
    assert not re.search(r'id=["\']more-settings["\']', page), "the phone still carries the Settings link"
    assert "./#set" not in page, "the phone still links a Settings theme address"


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
