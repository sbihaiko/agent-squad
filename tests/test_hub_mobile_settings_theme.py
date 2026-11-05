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


def _phone_link_href(stored):
    page = MOBILE_HTML.read_text()
    assert page.count("// settings-handoff:begin") == 1 and page.count("// settings-handoff:end") == 1, "the phone has no single handoff block"
    block = page[page.index("// settings-handoff:begin"):page.index("// settings-handoff:end")]
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
    hash_map = re.search(r"^const HASH=[^\n]*", page, re.M).group(0)
    tab_fn = re.search(r"^function tabFromLocation\(.*$", page, re.M).group(0)
    boot = re.search(r"^const themeHash=[^\n]*", page, re.M).group(0)
    src = (
        f"const location={{hash:{json.dumps(hash_)}}};\n{sth}\nlet sTheme='lop';\n{hash_map}\n{tab_fn}\n{boot}\n"
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
        ("{}", "./#set-lop"),
        ('{"sth":"bogus"}', "./#set-lop"),
        ("not json", "./#set-lop"),
    ],
)
def test_the_phone_settings_link_carries_the_last_theme_or_loop(stored, href):
    assert json.loads(_phone_link_href(stored)) == href


@needs_node
def test_the_desktop_opens_settings_on_the_themed_hash(hub):
    assert _desktop_tab_and_theme(hub, "#set-acc") == ["set", "acc"]
    assert _desktop_tab_and_theme(hub, "#set-bogus") == ["set", "lop"]
