"""The card shows `Testing · n` from its Gate Tries field, on the board and on the phone (#1255, ADR-0170).

The field rides the one items read the pass already makes, `board()` serves it on the row, and both
card renderers paint what the row carries. A card with no attempt draws nothing.
"""
import importlib.util
import json
import re
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
HUB_PY = SCRIPTS / "hub.py"
MOBILE_HTML = SCRIPTS / "hub-mobile.html"
CARD_RULES = SCRIPTS / "board-lanes.js"
NODE = "node"


@pytest.fixture(scope="module")
def hub():
    mp = pytest.MonkeyPatch()
    mp.setenv("SG_REPO", "sbihaiko/agent-squad")
    mp.setenv("SG_OWNER", "sbihaiko")
    mp.setenv("SG_PROJECT", "4")
    mp.setenv("SG_CHECKOUT", tempfile.mkdtemp())
    mp.setenv("SG_HUB_DIR", tempfile.mkdtemp())
    try:
        spec = importlib.util.spec_from_file_location("hub_gate_tries", HUB_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yield mod
    finally:
        mp.undo()


def _cache(items):
    return {"t": 1.0, "items": items, "prs": [], "lc": {}, "err": None}


def _item(n, tries=None, status="Testing"):
    it = {"n": n, "t": f"card {n}", "s": "open", "st": status, "l": ["wave:2"]}
    if tries is not None:
        it["gt"] = tries
    return it


def _row(data, n):
    for g in data["groups"]:
        for x in g["items"]:
            if x["n"] == n:
                return x
    raise AssertionError(f"issue {n} is on no column of the served board")


# ---- the value, off the cache (board()) -------------------------------------------------------------

def test_a_row_carries_its_gate_tries(hub, monkeypatch):
    monkeypatch.setattr(hub, "board_cache", _cache([_item(1255, 3), _item(1256, 0), _item(1257)]))
    data = hub.board()
    assert _row(data, 1255)["gt"] == 3
    assert _row(data, 1255)["gchip"] == "Testing · 3", "the chip text is a fact the row carries"
    assert "gchip" not in _row(data, 1256), "a card with no attempt draws nothing, not an invented 0"
    assert "gt" not in _row(data, 1256), "a card with no attempt draws nothing, not an invented 0"
    assert "gt" not in _row(data, 1257), "a card the read carried no field for draws nothing"


# ---- the fact, off the route the pass already reads (board_pass()) -----------------------------------

def _fake_gh(calls, items):
    def run(cmd, **kw):
        calls.append(cmd)
        argv = [str(a) for a in cmd]
        joined = " ".join(argv)
        route = next((a for a in argv if isinstance(a, str) and ("/items" in a or "/fields" in a or "/labels" in a)), "")
        if "projectsV2" in joined and "/items" in route:
            out = "".join(json.dumps(i) + "\n" for i in items) + json.dumps({"_page": len(items)}) + "\n"
        elif "issues?state=open" in joined:
            out = ""
        elif "/fields" in route:
            out = json.dumps([{"id": "PVTSSF_status", "name": "Status", "options": []},
                              {"id": "F_TRIES", "name": "Gate Tries", "dataType": "NUMBER"}])
        elif "blockedBy" in joined:
            out = json.dumps({"data": {"repository": {"issues": {"nodes": []}}}})
        elif "/labels?" in joined:
            out = ""
        elif "pullRequests(first:100" in joined:
            out = json.dumps({"data": {"repository": {"pullRequests": {"nodes": []}}}})
        else:
            out = ""
        return subprocess.CompletedProcess(cmd, 0, out, "")
    return run


def test_the_gate_tries_field_rides_the_one_items_read(hub, monkeypatch):
    calls = []
    monkeypatch.setattr(hub.board_seam().binding, "status_field", lambda: {"id": "PVTSSF_status", "opt": {}})
    monkeypatch.setattr(hub, "board_cache", _cache([]))
    monkeypatch.setattr(hub.subprocess, "run", _fake_gh(calls, [_item(1255, 3)]))
    hub.board_pass()
    reads = [c for c in calls if "projectsV2" in " ".join(c) and "/items" in " ".join(c)]
    assert len(reads) == 1, "the items route is read once, not once per card"
    assert "fields[]=F_TRIES" in " ".join(reads[0]), "the read does not ask for the field it projects"
    jq = reads[0][reads[0].index("--jq") + 1]
    assert '"Gate Tries"' in jq, f"the projection drops the field the card needs: {jq}"
    assert hub.board_cache["items"][0]["gt"] == 3, "the pass did not keep the value it read"


# ---- what the desktop page paints (cards()) ----------------------------------------------------------

def _render(hub, rows):
    line = re.search(r"^const cards=.*$", hub.PAGE, re.M)
    assert line, "the card renderer is gone"
    script = (CARD_RULES.read_text() + "const SLOTS=specSlots([]);\n" +
              'const esc=s=>String(s);const isPR=n=>false;const colOf=()=>"#000000";const tint=()=>"";'
              'const lbc=()=>"";const gstrip=()=>"";const age=()=>"";const ageC=()=>"";'
              'const bo={labels:[]};const CN=["Testing"];\n' + line.group(0) +
              f"\nconst ITEMS={json.dumps(rows)};\nconsole.log(cards({{name:'Testing'}},ITEMS,ITEMS));\n")
    out = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout.rstrip("\n")


def test_the_desktop_card_shows_gating_and_its_count(hub):
    html = _render(hub, [{"n": 1255, "t": "a card", "l": [], "gt": 3}])
    assert "Testing · 3" in html, f"the card does not show its Gate Tries: {html}"
    bare = _render(hub, [{"n": 1255, "t": "a card", "l": []}])
    assert "data-k=gate" not in bare, "a card with no attempt wears no gate chip"
    assert "Testing ·" not in bare, "a card with no attempt shows no gate count"


# ---- what the phone paints (cardLink) --------------------------------------------------------------

def test_the_phone_card_shows_gating_and_its_count():
    page = MOBILE_HTML.read_text()
    fn = re.search(r"^function cardLink\(.*?\n\}\n", page, re.M | re.S)
    assert fn, "the phone's card renderer is gone"
    script = ("const mk=()=>({className:'',dataset:{},textContent:'',children:[],classList:{add(){}},"
              "setAttribute(){},append(...c){this.children.push(...c)}});"
              "const document={createElement:mk};"
              "const specColor=()=>'#000';const mSlots=()=>({});const cardSpec=x=>x.sp?x.n:x.pa;const cardColor=()=>'#000';"
              "const issueURL=()=>'https://github.com/x/y/issues/1';" + fn.group(0) +
              "const txt=x=>x.textContent+x.children.map(txt).join('');"
              "const a=cardLink({n:1255,t:'a card',l:[],gt:3,gchip:'Testing · 3'},0);"
              "const b=cardLink({n:1255,t:'a card',l:[]},1);"
              "console.log(JSON.stringify([txt(a),txt(b)]));")
    out = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    with_count, bare = json.loads(out.stdout)
    assert "gate try 3" in with_count, with_count
    assert "gate try" not in bare, bare
