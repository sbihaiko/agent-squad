"""The machine slots sub-group on the hub (#2435).

Spend & limits holds a Machine slots sub-group, labelled "this machine · all projects": a Runs row and a Gate workers
row, each with its slots in use, its cap and the holder of each slot, and each cap editable into the project settings
file. The gate-worker row divides the cap by the workers of one gate, as the loop gates that fit at once. The sub-group
states that a run carries one or more cards, and names children and reviews as file-only settings with their values. The
sub-group's pure rules run in node as they ship.
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
HUB_PY = SCRIPTS / "hub.py"


@pytest.fixture
def hub(monkeypatch, tmp_path):
    co = tmp_path / "co"
    (co / "docs" / "squad").mkdir(parents=True)
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("HOME", str(tmp_path)),
                 ("SG_CHECKOUT", str(co)), ("SG_HUB_DIR", str(tmp_path)), ("SG_STATE", str(tmp_path / "st")),
                 ("SQUAD_CAPS_DIR", str(tmp_path / "caps"))):
        monkeypatch.setenv(k, v)
    for k in ("SG_STATE_BASE", "SG_SHARED", "SG_PROFILE", "SG_SQUAD", "SG_GATE_WORKERS"):
        monkeypatch.delenv(k, raising=False)
    gspec = importlib.util.spec_from_file_location("ghread", SCRIPTS / "ghread.py")
    gh = importlib.util.module_from_spec(gspec)
    monkeypatch.setitem(sys.modules, "ghread", gh)
    gspec.loader.exec_module(gh)
    spec = importlib.util.spec_from_file_location("hub_machine_slots", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["hub_machine_slots"] = mod
    spec.loader.exec_module(mod)
    return mod, co


def _lease(caps_dir, kind, stem, holder):
    d = Path(caps_dir) / kind
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{stem}.json").write_text(json.dumps({"holder": holder, "pid": os.getpid(), "since": time.time()}))


def _node(mod, body):
    if shutil.which("node") is None:
        pytest.skip("node is required")
    page = mod.PAGE
    esc = re.search(r"^const esc=.*$", page, re.M).group(0)
    sett = page[page.index("// SETT{"):page.index("// }SETT") + len("// }SETT")]
    slot = re.search(r"^// SLOT\{.*?^// \}SLOT$", page, re.M | re.S)
    assert slot, "the page carries no SLOT block"
    stub = 'const STORE={};const $=s=>STORE[s]||(STORE[s]={innerHTML:"",textContent:""});function setBar(){}function setChip(){}'
    out = subprocess.run(["node", "-"], input="\n".join([stub, esc, sett, slot.group(0), body]),
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


# --- the slots route

def test_the_route_answers_runs_and_gate_workers_with_each_holder(hub, tmp_path):
    mod, _ = hub
    _lease(tmp_path / "caps", "runs", "run-a", "run-a")
    _lease(tmp_path / "caps", "gates", "gate-11.slot0", "gate-11")
    got = mod.slots_read()
    assert got["runs"]["cap"] == 10 and got["runs"]["in_use"] == 1
    assert [h["name"] for h in got["runs"]["holders"]] == ["run-a"]
    assert got["gates"]["cap"] == 12 and got["gates"]["in_use"] == 1
    assert got["gates"]["holders"] == ["gate-11"]
    assert got["gates"]["gates_at_once"] == 2, "12 slots of 6 workers per gate fit two gates at once"
    assert got["children"] == 20 and got["reviews"] == 4
    assert got["ceiling"] == {"runs": 10, "children": 20, "gates": 12, "reviews": 4}, "the page bounds a cap by this ceiling"


def test_a_cap_save_asks_for_the_diff_before_it_writes(hub):
    mod, co = hub
    out = mod.slots_set({"runs": "4", "gates": "3", "confirm": False})
    assert out["diff"] == ["Machine slots · Runs: 10 -> 4", "Machine slots · Gate workers: 12 -> 3"]
    assert not (co / "docs" / "squad" / "defaults.yaml").exists()


def test_a_confirmed_cap_save_writes_the_project_settings_file(hub):
    mod, co = hub
    out = mod.slots_set({"runs": "4", "gates": "3", "confirm": True})
    assert out["diff"] == ["Machine slots · Runs: 10 -> 4", "Machine slots · Gate workers: 12 -> 3"]
    data = yaml.safe_load((co / "docs" / "squad" / "defaults.yaml").read_text())
    assert data["caps"] == {"runs": 4, "gates": 3}
    assert mod.slots_read()["runs"]["cap"] == 4
    assert mod.slots_read()["gates"]["cap"] == 3


def test_a_cap_above_the_plugin_default_is_refused_and_writes_nothing(hub):
    mod, co = hub
    out = mod.slots_set({"runs": "11", "gates": "3", "confirm": True})
    assert "err" in out and "caps.runs" in out["err"], out
    assert not (co / "docs" / "squad" / "defaults.yaml").exists()


def test_a_missing_plugin_ceiling_bounds_the_save_at_the_catalog_high(hub, monkeypatch):
    """A plugin file that omits a cap leaves no ceiling to compare, so the catalog's high bound answers for it, never no
    bound at all."""
    mod, co = hub
    ms, _ = mod._slots_parts()
    monkeypatch.setattr(ms, "plugin", lambda path=None: {"runs": None, "children": 20, "gates": 12, "reviews": 4})
    out = mod.slots_set({"runs": "1001", "confirm": True})
    assert "err" in out and "1000" in out["err"], out
    assert not (co / "docs" / "squad" / "defaults.yaml").exists()
    assert "err" not in mod.slots_set({"runs": "4", "confirm": True}), "a save under the bound was refused"


def test_a_cap_that_is_not_a_count_is_refused_and_writes_nothing(hub):
    mod, co = hub
    for bad in ("0", "-2", "six", ""):
        out = mod.slots_set({"runs": bad, "confirm": True})
        assert "err" in out, (bad, out)
    assert not (co / "docs" / "squad" / "defaults.yaml").exists()


# --- the Settings page

def test_the_sub_group_is_labelled_this_machine_all_projects(hub):
    mod, _ = hub
    got = _node(mod, "slotS={file_label:'docs/squad/defaults.yaml',runs:{cap:4,in_use:4,holders:[{name:'a',issues:[2374]}]},"
                      "gates:{cap:3,in_use:0,holders:[],workers:3,gates_at_once:1,gates_running:0},children:12,reviews:4};"
                      "console.log(JSON.stringify({h:slotsHtml()}))")
    h = got["h"]
    assert "Machine slots" in h and "this machine · all projects" in h
    assert "4 of 4" in h and "0 of 3" in h
    assert "#2374" in h, "the runs row does not name the issues that hold its slots"


def test_the_gate_row_reads_the_slots_as_loop_gates_at_once(hub):
    mod, _ = hub
    got = _node(mod, "console.log(JSON.stringify(["
                      "slotGateLine({cap:3,in_use:0,workers:3,slots_per_gate:3,gates_at_once:1,gates_running:0}),"
                      "slotGateLine({cap:12,in_use:7,workers:6,slots_per_gate:6,gates_at_once:2,gates_running:1})]))")
    assert got[0] == "a gate takes 3 slots (3 asked, cap 3) = 1 gate at a time · none running", got[0]
    assert "a gate takes 6 slots (6 asked, cap 12) = 2 gates at a time" in got[1] and "1 gate running" in got[1], got[1]


def test_the_sub_group_states_a_run_carries_one_or_more_cards(hub):
    mod, _ = hub
    got = _node(mod, "slotS={file_label:'docs/squad/defaults.yaml',runs:{cap:4,in_use:0,holders:[]},"
                      "gates:{cap:3,in_use:0,holders:[],workers:3,gates_at_once:1,gates_running:0},children:12,reviews:4};"
                      "console.log(JSON.stringify({h:slotsHtml()}))")
    assert "A run carries one or more cards" in got["h"], got["h"]


def test_one_line_names_children_and_reviews_as_file_only_with_their_values(hub):
    mod, _ = hub
    got = _node(mod, "slotS={file_label:'docs/squad/defaults.yaml',runs:{cap:4,in_use:0,holders:[]},"
                      "gates:{cap:3,in_use:0,holders:[],workers:3,gates_at_once:1,gates_running:0},children:12,reviews:4};"
                      "console.log(JSON.stringify({h:slotsHtml()}))")
    h = got["h"]
    assert re.search(r"File only[^<]*children 12 · reviews 4", h), h
    assert "docs/squad/defaults.yaml" in h


def test_a_typed_cap_stages_and_makes_the_theme_dirty(hub):
    mod, _ = hub
    got = _node(mod, "slotS={file:'/p',file_label:'p',runs:{cap:4,in_use:0,holders:[]},"
                      "gates:{cap:3,in_use:0,holders:[],workers:3,gates_at_once:1,gates_running:0},children:12,reviews:4};"
                      "slotDraft=slotClone(slotS);const clean=slotDirty();slotDraft.runs.cap=6;"
                      "console.log(JSON.stringify({clean,dirty:slotDirty(),caps:slotCaps()}))")
    assert got["clean"] is False
    assert got["dirty"] is True
    assert got["caps"] == {"runs": "6", "gates": "3"}, got["caps"]


def test_a_cleared_or_above_the_ceiling_cap_is_refused_before_it_is_staged(hub):
    mod, _ = hub
    got = _node(mod, "slotS={file:'/p',file_label:'p',ceiling:{runs:10,gates:12},"
                      "runs:{cap:4,in_use:0,holders:[]},gates:{cap:3,in_use:0,holders:[],workers:3,gates_at_once:1,gates_running:0},children:12,reviews:4};"
                      "slotDraft=slotClone(slotS);"
                      "const empty=slotStage('runs','');const afterEmpty=slotDirty();"
                      "const high=slotStage('gates','13');"
                      "console.log(JSON.stringify({empty,afterEmpty,high,dirty:slotDirty(),caps:slotCaps(),msg:slotMsg}))")
    assert got["empty"] is False and got["afterEmpty"] is False, "a cleared cap was staged"
    assert got["high"] is False, "a cap above the ceiling was staged"
    assert got["dirty"] is False
    assert got["caps"] == {"runs": "4", "gates": "3"}, got["caps"]
    assert got["msg"].startswith("!"), got["msg"]


def test_a_valid_cap_is_staged_and_makes_the_theme_dirty(hub):
    mod, _ = hub
    got = _node(mod, "slotS={file:'/p',file_label:'p',ceiling:{runs:10,gates:12},"
                      "runs:{cap:4,in_use:0,holders:[]},gates:{cap:3,in_use:0,holders:[],workers:3,gates_at_once:1,gates_running:0},children:12,reviews:4};"
                      "slotDraft=slotClone(slotS);"
                      "const ok=slotStage('runs','6');"
                      "console.log(JSON.stringify({ok,dirty:slotDirty(),caps:slotCaps(),msg:slotMsg}))")
    assert got["ok"] is True and got["dirty"] is True
    assert got["caps"] == {"runs": "6", "gates": "3"}
    assert got["msg"] == ""


def test_the_staged_cap_diff_is_shown_in_the_sub_group(hub):
    mod, _ = hub
    got = _node(mod, "slotS={file_label:'docs/squad/defaults.yaml',runs:{cap:4,in_use:0,holders:[]},"
                      "gates:{cap:3,in_use:0,holders:[],workers:3,gates_at_once:1,gates_running:0},children:12,reviews:4};"
                      "slotDiff=['Machine slots · Runs: 4 -> 6'];"
                      "console.log(JSON.stringify({h:slotsHtml()}))")
    assert "Runs: 4 -&gt; 6" in got["h"] or "Runs: 4 -> 6" in got["h"], got["h"]


def test_the_sub_group_chip_names_a_full_row(hub):
    mod, _ = hub
    got = _node(mod, "const mk=(r,g)=>({runs:{cap:4,in_use:r,holders:[]},gates:{cap:3,in_use:g,holders:[],workers:3,gates_at_once:1,gates_running:0}});"
                      "console.log(JSON.stringify([slotChip(mk(4,0))[0],slotChip(mk(2,0))[0],slotChip(mk(1,3))[0]]))")
    assert got == ["runs full", "under the caps", "gate workers full"], got


def test_the_sub_group_is_a_table_with_four_headers_a_bar_and_issue_chips(hub):
    """#2513: the group is a table of Slot, In use, Slots and Held by; In use draws a bar; Held by links each issue."""
    mod, _ = hub
    got = _node(mod, "slotS={file:'/p',file_label:'docs/squad/defaults.yaml',ceiling:{runs:10,gates:12},"
                      "runs:{cap:4,in_use:1,holders:[{name:'run-a',issues:[2374,2420]}]},"
                      "gates:{cap:3,in_use:0,holders:[],workers:6,slots_per_gate:3,gates_at_once:1,gates_running:0},children:12,reviews:4};"
                      "slotDraft=slotClone(slotS);"
                      "console.log(JSON.stringify({h:slotsHtml()}))")
    h = got["h"]
    for head in ("Slot", "In use", "Slots", "Held by"):
        assert re.search(rf"<th scope=col[^>]*>{head}</th>", h), head
    assert '<span class="slotbar"><i style="width:25%"></i></span>' in h, "the In use cell draws no bar"
    assert 'href="https://github.com/__REPO__/issues/2374"' in h, "the issue chip does not link to the issue"
    assert ">#2374</a>" in h and ">#2420</a>" in h, "the issue chips are missing"
    assert "run-a" not in h, "Held by names the run folder instead of its issues"
    assert "a gate takes 3 slots (6 asked, cap 3) = 1 gate at a time" in h, "the gate line hides the real arithmetic"
    assert "3 slots ÷ 6 workers" not in h, "the gate line still shows the wrong division"


def _skeleton(h3):
    """The heading's tags and attributes, with each text and each interpolated value removed, so two headings compare by shape."""
    s = re.sub(r"\$\{[^}]*\}", "", h3)
    s = re.sub(r">[^<]*<", "><", s)
    s = re.sub(r'id=\w+', "id=X", s)
    return re.sub(r'class=("[^"]*"|\S+)', "class=C", s)


def test_the_machine_slots_heading_has_the_kanban_heading_structure(hub):
    """#2534: the Machine slots heading is the Kanban group heading: a flex title with its scope, the status at its right."""
    mod, _ = hub
    kanban = re.search(r'<h3 class="blkh khead">.*?</h3>', mod.PAGE, re.S).group(0)
    got = _node(mod, "slotS={file_label:'docs/squad/defaults.yaml',runs:{cap:4,in_use:0,holders:[]},"
                      "gates:{cap:3,in_use:0,holders:[],workers:3,gates_at_once:1,gates_running:0},children:12,reviews:4};"
                      "console.log(JSON.stringify({h:slotsHtml()}))")
    slots = re.search(r"<h3.*?</h3>", got["h"], re.S).group(0)
    assert "khead" in slots and "khead" in kanban, slots
    assert "<span>Machine slots <small class=ksub>" in slots, slots
    assert _skeleton(slots) == _skeleton(kanban), (slots, kanban)


def test_one_held_gate_slot_never_reads_none_running(hub):
    mod, _ = hub
    got = _node(mod, "slotS={file_label:'docs/squad/defaults.yaml',runs:{cap:4,in_use:0,holders:[]},"
                      "gates:{cap:3,in_use:1,holders:['daysuites-98836'],workers:6,slots_per_gate:3,gates_at_once:1,gates_running:0},children:12,reviews:4};"
                      "console.log(JSON.stringify({h:slotsHtml()}))")
    row = re.search(r"<tr><th scope=row[^>]*>Gate workers</th>.*?</tr>", got["h"], re.S).group(0)
    assert "none running" not in row, row
    assert "held by daysuites-98836" in row, row


def test_one_live_run_names_its_issue_chip_in_the_runs_row(hub):
    mod, _ = hub
    got = _node(mod, "slotS={file_label:'docs/squad/defaults.yaml',runs:{cap:4,in_use:1,holders:[{name:'run-a',issues:[2534]}]},"
                      "gates:{cap:3,in_use:0,holders:[],workers:3,gates_at_once:1,gates_running:0},children:12,reviews:4};"
                      "console.log(JSON.stringify({h:slotsHtml()}))")
    row = re.search(r"<tr><th scope=row[^>]*>Runs</th>.*?</tr>", got["h"], re.S).group(0)
    assert ">#2534</a>" in row, row
    assert "no issue named" not in row, row


# --- A cap save goes through the comment-keeping writer (#2543): the settings file is hand-edited, its comments hold the
# owner's ruling, and a save of one cap must not rewrite, recomment or copy the personal `.env` tailnet block into it.
def test_a_cap_save_keeps_the_comments_of_the_settings_file(hub):
    mod, co = hub
    f = co / "docs" / "squad" / "defaults.yaml"
    f.write_text("# Owner ruling: four runs, because of the gate slots.\ncaps:\n  runs: 5    # five ran ahead of the gates\n  gates: 4\n")
    out = mod.slots_set({"runs": "4", "confirm": True})
    assert "err" not in out, out
    text = f.read_text()
    assert "# Owner ruling: four runs, because of the gate slots." in text, text
    assert "  runs: 4" in text and "# five ran ahead of the gates" in text, text
    assert yaml.safe_load(text) == {"caps": {"runs": 4, "gates": 4}}


def test_a_cap_save_never_copies_a_tailnet_key_from_the_env_file(hub):
    mod, co = hub
    (co / ".env").write_text("SQUAD_TAILNET_HOST=my-mac.example-tailnet.ts.net\nSQUAD_TAILNET_ALLOW=owner@example.com\n")
    out = mod.slots_set({"runs": "4", "confirm": True})
    assert "err" not in out, out
    text = (co / "docs" / "squad" / "defaults.yaml").read_text()
    assert "tailnet" not in text and "my-mac" not in text and "owner@example.com" not in text, text
SLOTS_FIXTURE = {
    "file_label": "docs/squad/defaults.yaml",
    "runs": {"cap": 10, "in_use": 1, "holders": [{"name": "run-a", "issues": [2534]}]},
    "gates": {"cap": 12, "in_use": 6, "holders": ["gate-11"], "workers": 6, "slots_per_gate": 6,
              "gates_at_once": 2, "gates_running": 1},
    "children": 20, "reviews": 4, "ceiling": {"runs": 10, "children": 20, "gates": 12, "reviews": 4},
}

RENDER_SLOTS = """
(args) => {
  const [fixture] = args;
  location.hash = "#set-lim";
  slotS = fixture;
  slotDraft = JSON.parse(JSON.stringify(fixture));
  setTab("set");
  document.getElementById("lim").open = true;
  document.getElementById("lbody").innerHTML = slotsHtml();
  return true;
}
"""

MEASURE_SLOTS = """
() => {
  const text = el => { const rg = document.createRange(); rg.selectNodeContents(el);
                       const r = rg.getBoundingClientRect(); return { l: r.left, r: r.right, t: r.top, b: r.bottom,
                       lines: new Set(Array.from(rg.getClientRects()).map(q => Math.round(q.top))).size }; };
  const box = el => { const r = el.getBoundingClientRect(); return { l: r.left, r: r.right, t: r.top, b: r.bottom }; };
  return { innerWidth: window.innerWidth,
           rows: [...document.querySelectorAll("#lbody table.stab tbody tr")].map(tr => ({
             name: tr.querySelector("th").textContent,
             label: text(tr.querySelector("th")),
             bar: box(tr.querySelector(".slotbar")),
             now: text(tr.querySelector(".slotnow")),
             input: box(tr.querySelector("input")),
             cells: [...tr.children].map(box),
             held: box(tr.querySelector(".slothold")),
             heldtext: text(tr.querySelector(".slothold")),
           })) };
}
"""

EGO_SLOTS = """
import fs from "node:fs";
const task = await taskSpace(SPACE_NAME);
const out = {};
try {
  const page = task.page("p1");
  for (const width of [1000, 900, 1440]) {
    await page.cdp("Emulation.setDeviceMetricsOverride", { width, height: 900, deviceScaleFactor: 1, mobile: false });
    await page.goto(URL);
    await page.evaluate(RENDER_SLOTS, [FIXTURE, width]);
    out[width] = await page.evaluate(MEASURE_SLOTS);
    await page.screenshot({ path: SHOT_DIR + "/slots-" + width + ".png" });
  }
  console.log("EGO_SLOTS:" + JSON.stringify(out));
} finally {
  await task.finish({ keep: [] });
}
"""


def _overlap(a, b):
    return a["l"] < b["r"] and b["l"] < a["r"] and a["t"] < b["b"] and b["t"] < a["b"]


def _split_top(text, sep):
    """Splits `text` at `sep`, but never inside parentheses or a quote: a ';' in url("data:image/svg+xml;utf8,…") is
    part of the value, not the end of a declaration."""
    parts, buf, depth, quote = [], [], 0, ""
    for ch in text:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
            buf.append(ch)
        elif ch in "([":
            depth += 1
            buf.append(ch)
        elif ch in ")]":
            depth -= 1
            buf.append(ch)
        elif ch == sep and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return parts


def _decls(body):
    """A rule body's declarations as a property -> value map. A ':' or ';' inside a parenthesized or quoted value
    stays in the value, so url("data:image/svg+xml;utf8,…") reads as one declaration. No browser is involved."""
    out = {}
    for part in _split_top(body, ";"):
        bits = _split_top(part, ":")
        if len(bits) >= 2 and bits[0].strip():
            out[bits[0].strip().lower()] = ":".join(bits[1:]).strip()
    return out


def _stab_decl(css, selector):
    """The declarations of the one CSS rule of `selector`, as a property -> value map."""
    return _style(css, selector)


# --- #2540: the slots geometry, read from the served CSS with no browser ------------------------------------------
# A browser-free layout model keeps the pixel checks in the default gate. It reads the served stylesheet, walks the
# inline-size chain from the viewport down to the table, and measures text off the advance table below.
_ADV15 = {
    " ": 3.98, "·": 4.22, "#": 9.21, "%": 13.65, "(": 5.49, ")": 5.49, "+": 9.21, ",": 4.22, "-": 6.84, ".": 4.22,
    "/": 4.34, ":": 4.22, "=": 9.21, "0": 9.21, "1": 6.72, "2": 8.82, "3": 9.17, "4": 9.42, "5": 9.04, "6": 9.32,
    "7": 8.31, "8": 9.35, "9": 9.32, "a": 8.04, "b": 8.98, "c": 8.16, "d": 8.98, "e": 8.33, "f": 5.19, "g": 8.91,
    "h": 8.59, "i": 3.47, "j": 3.46, "k": 7.91, "l": 3.56, "m": 12.82, "n": 8.52, "o": 8.63, "p": 8.92, "q": 8.91,
    "r": 5.48, "s": 7.62, "t": 5.21, "u": 8.52, "v": 7.90, "w": 11.38, "x": 7.63, "y": 7.91, "z": 7.85, "A": 9.87,
    "B": 9.62, "C": 10.50, "D": 10.66, "E": 8.70, "F": 8.35, "G": 10.96, "H": 10.90, "I": 3.78, "J": 7.84, "K": 9.65,
    "L": 8.28, "M": 12.88, "N": 10.90, "O": 11.34, "P": 9.29, "Q": 11.34, "R": 9.57, "S": 9.32, "T": 9.27, "U": 10.83,
    "V": 9.87, "W": 14.28, "X": 9.95, "Y": 9.59, "Z": 9.69, "÷": 8.11,
}   # the advance of each glyph of the hub's font stack in px at 15 px (--fs-body), measured on the ego-browser render


def _text_px(text, size=15.0):
    """The width of `text` in the hub's font: an unknown glyph counts as 15 px, so the model never under-reports."""
    return sum(_ADV15.get(c, 15.0) for c in text) * size / 15.0


def _wrap(text, width, anywhere):
    """What a greedy word wrap draws in `width` px: the line count and the widest unbreakable word. With
    `overflow-wrap:anywhere` a word wider than the line breaks inside itself; without it the word overflows."""
    lines, cur, widest = 0, None, 0.0
    space = _text_px(" ")
    for word in text.split():
        w = _text_px(word)
        widest = max(widest, w)
        if cur is not None and cur + space + w <= width:
            cur += space + w
        else:
            if cur is not None:
                lines += 1
            if anywhere and w > width:
                broken = int(w // width)
                lines += broken
                cur = w - broken * width
            else:
                cur = w
    return lines + (1 if cur is not None else 0), widest


def _page_css(mod):
    """Every rule of the page's style blocks, in one string."""
    return "".join(re.findall(r"<style>(.*?)</style>", mod.PAGE, re.S))


def _css_rules(css):
    """Every rule as (condition, selector, declarations). A rule inside @media or @container keeps its condition;
    a condition is read one level deep, as the page writes it."""
    text = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out = []

    def walk(body, cond):
        i = 0
        while True:
            j = body.find("{", i)
            if j < 0:
                return
            head = body[i:j].strip()
            depth, k = 1, j + 1
            while depth and k < len(body):
                depth += (body[k] == "{") - (body[k] == "}")
                k += 1
            inner = body[j + 1:k - 1]
            if head.startswith("@"):
                walk(inner, head)
            else:
                out.append((cond, head, _decls(inner)))
            i = k

    walk(text, "")
    return out


def _applies(cond, viewport=None, container=None):
    """Whether a rule's condition holds here: @media reads the viewport width and @container its container's inline
    size. A rule outside a condition always holds."""
    if not cond:
        return True
    kind = re.match(r"@(media|container)\b", cond)
    if not kind:
        return True
    width = viewport if kind.group(1) == "media" else container
    if width is None:
        return False
    for name, value in re.findall(r"\(\s*(min|max)-width\s*:\s*(\d+(?:\.\d+)?)px", cond):
        if (name == "max" and width > float(value)) or (name == "min" and width < float(value)):
            return False
    return True


def _style(css, selector, viewport=None, container=None):
    """What the page computes for `selector` here: every rule of that exact selector that applies, merged in order."""
    sels = (selector,) if isinstance(selector, str) else selector
    out = {}
    for cond, sel, decls in _css_rules(css):
        if _applies(cond, viewport, container) and set(sels) & {s.strip() for s in _split_top(sel, ",")}:
            out.update(decls)
    return out


def _px(value, default=0.0):
    m = re.match(r"(-?\d+(?:\.\d+)?)px", (value or "").strip())
    return float(m.group(1)) if m else default


def _pad_x(value):
    """The left and right padding of a shorthand, in px."""
    parts = (value or "0").split()
    vals = [_px(p) for p in parts] + [0.0] * (4 - len(parts))
    if len(parts) <= 1:
        return 2 * vals[0]
    return vals[1] * 2


def _pcts(value):
    return [float(x[:-1]) / 100 for x in (value or "").split() if re.fullmatch(r"\d+(?:\.\d+)?%", x)]


def _slots_layout(mod, viewport):
    """The Machine slots table's geometry at a viewport width, read from the served CSS.

    The inline-size chain: #content is the viewport less #side; .pane padding; #tab-set .swrap is the lesser of its
    max-width and what is left; .spanels is .swrap less the theme nav and the gap; the table is .spanels less the
    details border and the .sb padding. The @container query on .spanels moves the Held by cell to its own row.
    """
    css = _page_css(mod)
    side = _px(_style(css, "#side").get("width"), 280)
    pane_pad = _pad_x(_style(css, ".pane").get("padding"))
    swrap = _style(css, "#tab-set .swrap")
    nav = _px(swrap.get("grid-template-columns", "").split()[0] if swrap else "", 200)
    gap = _px(swrap.get("gap"), 16)
    swrap_max = _px(swrap.get("max-width"), 1e9)
    border = 2 * _px(_style(css, ".sp:is(details)").get("border", "1px").split()[0], 1)
    sb_pad = _pad_x(_style(css, "#tab-set .sp .sb").get("padding"))
    container = min(viewport - side - pane_pad, swrap_max) - nav - gap
    table = container - border - sb_pad
    cell = _style(css, "#tab-set .stab tbody th", viewport=viewport, container=container)
    pad_r = _pads_right(cell.get("padding"))
    base_cols = [_pcts(_style(css, f"#tab-set .stab .{c}").get("width")) for c in ("scol1", "scol2", "scol3", "scol4")]
    base_cols = [c[0] for c in base_cols if c]
    row_rule = _style(css, "#tab-set .stab tr", viewport=viewport, container=container)
    narrow = bool(re.search(r"1\s*/\s*-1", _style(css, "#tab-set .stab tbody td.scol4", viewport=viewport,
                                                    container=container).get("grid-column", "")))
    cols = _pcts(row_rule.get("grid-template-columns")) if narrow else []
    wide_label = bool(re.search(r"1\s*/\s*-1", cell.get("grid-column", "")))   # a very narrow column: the label takes the row
    input_max = _px(_style(css, "#tab-set .stab input").get("max-width"), 96)
    label_ws = cell.get("white-space", "normal")
    wrap_any = cell.get("overflow-wrap", "normal") in ("anywhere", "break-word")
    use = _style(css, "#tab-set .stab .slotuse", viewport=viewport, container=container)
    bar = _style(css, "#tab-set .stab .slotbar", viewport=viewport, container=container)
    use_gap = _px(use.get("gap"), 8)
    bar_min = _px(bar.get("min-width"))
    held_cell = _style(css, "#tab-set .stab tbody td.scol4", viewport=viewport, container=container)
    held_wrap = _style(css, "#tab-set .stab tbody td", viewport=viewport, container=container).get(
        "overflow-wrap", "normal") in ("anywhere", "break-word")
    gates = SLOTS_FIXTURE["gates"]
    gate_line = _node(mod, f"slotS={json.dumps(SLOTS_FIXTURE)};console.log(JSON.stringify([slotGateLine(slotS.gates)]))")[0]
    rows = []
    for name, label, use_txt, held_txt in (
            ("Runs", "Runs", f"{SLOTS_FIXTURE['runs']['in_use']} of {SLOTS_FIXTURE['runs']['cap']}", "#2534"),
            ("Gate workers", "Gate workers", f"{gates['in_use']} of {gates['cap']}", gate_line)):
        w1 = table if wide_label else (cols[0] if narrow else base_cols[0]) * table
        w2 = (table - input_max) if (narrow and not cols) else (cols[1] if narrow else base_cols[1]) * table
        avail1 = w1 - pad_r
        needed = _text_px(label)
        overflow = label_ws == "nowrap" and needed > avail1 + 0.5
        lines = 1 if needed <= avail1 else (None if label_ws == "nowrap" else _wrap(label, avail1, wrap_any)[0])
        held_width = (table if narrow else base_cols[3] * table) - _pads_right(held_cell.get("padding"))
        held_lines, widest = _wrap(held_txt, held_width, held_wrap)
        rows.append({
            "name": name,
            "label_needed": round(needed, 1), "label_width": round(avail1, 1),
            "label_right": round(w1 if overflow else min(needed, w1), 1), "bar_left": round(w1, 1),
            "label_overflow": overflow, "label_lines": lines,
            "use_needed": round(bar_min + use_gap + _text_px(use_txt), 1), "use_width": round(w2 - pad_r, 1),
            "use_overflow": bar_min + use_gap + _text_px(use_txt) > w2 - pad_r + 0.5,
            "held_width": round(held_width, 1), "held_lines": held_lines,
            "held_cut": (not held_wrap) and widest > held_width + 0.5,
            "held_own_row": narrow and "grid-column" in held_cell,
        })
    return {"viewport": viewport, "container": round(container, 1), "table": round(table, 1),
            "narrow": narrow, "wide_label": wide_label, "cols": cols, "base_cols": base_cols, "rows": rows}


def _pads_right(value):
    """The right padding of a shorthand, in px: the second value, or the one value it names."""
    vals = [_px(p) for p in (value or "0").split()]
    return vals[1] if len(vals) >= 2 else (vals[0] if vals else 0.0)


def test_the_slots_table_pins_its_columns_and_reserves_the_gap_before_each_cap(hub):
    """A fixed table layout with a pinned width per column keeps the label from shrinking under a wide Slots cell, and
    the Slots cell reserves a gap before its input so the 'N of M' text never touches the cap field. This check runs
    with no browser, so the guard holds in the default gate."""
    mod, _ = hub
    css = mod.PAGE
    assert _stab_decl(css, "#tab-set .stab").get("table-layout") == "fixed", \
        "the slots table has no deterministic column layout, so a wide cell shrinks the label column"
    for col in ("scol1", "scol2", "scol3", "scol4"):
        width = _stab_decl(css, f"#tab-set .stab .{col}").get("width", "")
        assert re.fullmatch(r"\d+%", width), f"the {col} column carries no pinned percentage width: {width!r}"
    gap = _stab_decl(css, "#tab-set .stab td.scol3").get("padding-left", "")
    assert re.fullmatch(r"\d+px", gap) and int(gap[:-2]) >= 6, f"no gap before the cap input: {gap!r}"
    got = _node(mod, "slotS={file_label:'docs/squad/defaults.yaml',runs:{cap:4,in_use:1,holders:[]},"
                      "gates:{cap:3,in_use:0,holders:[],workers:3,gates_at_once:1,gates_running:0},children:12,reviews:4};"
                      "console.log(JSON.stringify({h:slotsHtml()}))")
    h = got["h"]
    for col in ("scol1", "scol2", "scol3", "scol4"):
        assert len(re.findall(rf'class=("?)[^"]*\b{col}\b', h)) >= 2, \
            f"the slots table markup does not carry {col} on its header and row cells"


def test_a_parenthesized_value_keeps_its_colon_and_semicolon():
    """The declaration reader splits a rule body at ';' and ':' only outside parentheses and quotes, so a data URL
    that carries both stays one declaration instead of a handful of broken ones."""
    css = ('#x .y{background:url("data:image/svg+xml;utf8,<svg xmlns=\'http://x\'/>");color:red}')
    assert _stab_decl(css, "#x .y") == {
        "background": 'url("data:image/svg+xml;utf8,<svg xmlns=\'http://x\'/>")', "color": "red"}


def test_the_slots_table_puts_held_by_on_its_own_row_at_a_narrow_column(hub):
    """#2540: at a narrow Settings column a container query gives the Held by cell its own row below the other three,
    so the gate line wraps in the full table width. The three pinned widths stay. At a wide column the four cells
    share one row. The model reads the served CSS, so the default gate checks it with no browser."""
    mod, _ = hub
    lay = _slots_layout(mod, 1000)
    assert lay["container"] == 472 and lay["table"] == 446, lay   # the inline-size chain reproduces the render
    assert lay["narrow"] is True, "no container query moves the Held by cell at a narrow Settings column"
    assert lay["cols"] == lay["base_cols"][:3], (lay["cols"], lay["base_cols"])
    for row in lay["rows"]:
        assert row["held_own_row"] is True, row
    wide = _slots_layout(mod, 1440)
    assert wide["narrow"] is False and wide["table"] == 878, wide
    assert all(row["held_own_row"] is False for row in wide["rows"]), wide


def test_no_slots_label_clips_its_cell_and_the_gate_line_wraps_inside_its_own(hub):
    """#2540: at 1000 and 900 px the label of each row stays inside its 25% cell (a nowrap label spills over the bar)
    and wraps to at most two lines, and the gate line wraps inside the Held by cell. Browser-free, so it runs in the
    default gate."""
    mod, _ = hub
    for vw in (1000, 900):
        lay = _slots_layout(mod, vw)
        for row in lay["rows"]:
            assert row["label_overflow"] is False, (vw, row)
            assert row["label_right"] <= row["bar_left"], (vw, row)
            assert row["label_lines"] is not None and row["label_lines"] <= 2, (vw, row)
            assert row["held_cut"] is False, (vw, row)


def test_the_label_takes_the_whole_row_under_a_very_narrow_column(hub):
    """#2540: at 768 px the table is 214 px, too narrow for three cells on one line: the label takes the row and the
    bar and the cap field share the line below, so the field keeps its 96 px and the label stays on one line."""
    mod, _ = hub
    lay = _slots_layout(mod, 768)
    assert lay["table"] == 214 and lay["wide_label"] is True, lay
    for row in lay["rows"]:
        assert row["label_lines"] == 1, row
        assert row["use_width"] >= 96, row


def test_the_in_use_bar_keeps_its_gap_before_the_cap_field_at_an_intermediate_width(hub):
    """#2540: at 900 px the bar, its gap and the 'N of M' text fit the In use column, so the text never reaches the
    Slots field."""
    mod, _ = hub
    for vw in (1000, 900):
        lay = _slots_layout(mod, vw)
        for row in lay["rows"]:
            assert row["use_overflow"] is False, (vw, row)


@pytest.mark.skipif(shutil.which("ego-browser") is None, reason="ego-browser is needed to render the Machine slots group")
def test_no_machine_slots_label_overlaps_its_bar_and_the_gate_label_wraps_to_two_lines(hub, tmp_path):
    """The rendered check beside the browser-free model: the same rules, measured on a real render."""
    mod, _ = hub
    page = tmp_path / "hub.html"
    page.write_text(mod.PAGE)
    shots = tmp_path / "shots"
    shots.mkdir()
    head = (f"const SPACE_NAME = {json.dumps(f'agent-squad slots {os.getpid()} {time.time_ns()}')};\n"
            f"const URL = {json.dumps(page.as_uri())};\n"
            f"const SHOT_DIR = {json.dumps(str(shots))};\n"
            f"const FIXTURE = {json.dumps(SLOTS_FIXTURE)};\n"
            f"const RENDER_SLOTS = {RENDER_SLOTS};\nconst MEASURE_SLOTS = {MEASURE_SLOTS};\n")
    run = subprocess.run(["ego-browser", "nodejs"], input=head + EGO_SLOTS, capture_output=True, text=True, timeout=240)
    seen = [line for line in (run.stdout + run.stderr).splitlines() if line.startswith("EGO_SLOTS:")]
    assert seen, f"ego-browser rendered no frame: {(run.stdout + run.stderr)[-2000:]}"
    frames = json.loads(seen[-1][len("EGO_SLOTS:"):])
    for width in ("1000", "900", "1440"):
        frame = frames[width]
        assert frame["innerWidth"] == int(width), \
            f"the viewport is {frame['innerWidth']} px, not the emulated {width} px"
        assert len(frame["rows"]) == 2, f"{width} px: the table drew {len(frame['rows'])} data rows, not two"
        for row in frame["rows"]:
            assert not _overlap(row["label"], row["bar"]), f"{width} px: {row['name']} label overlaps its bar: {row}"
            assert row["label"]["r"] <= row["bar"]["l"], \
                f"{width} px: {row['name']} label runs past its cell into the bar: {row}"
            assert row["input"]["l"] - row["now"]["r"] >= 6, \
                f"{width} px: {row['name']} 'N of M' touches the Slots field: {row}"
            assert row["label"]["lines"] <= 2, \
                f"{width} px: {row['name']} text wraps to {row['label']['lines']} lines: {row}"
        gate = frame["rows"][1]
        if int(width) <= 1000:
            bottom = max(cell["b"] for cell in gate["cells"][:3])
            assert gate["held"]["t"] >= bottom - 0.5, f"{width} px: Held by is not on its own row: {gate}"
        else:
            assert len({round(cell["t"], 1) for cell in gate["cells"]}) == 1, \
                f"{width} px: the four cells do not share one row: {gate}"
        assert gate["heldtext"]["r"] <= gate["held"]["r"] + 0.5 and gate["heldtext"]["b"] <= gate["held"]["b"] + 0.5, \
            f"{width} px: the gate line runs out of its cell: {gate}"
