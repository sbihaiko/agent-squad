"""GitHub issue #926: Settings > Spend & limits sets the token guard's cap, its pause factor and its on/off switch.

The setting is the profile the token guard sources on every pass (`env.sh` reads it again each time), so a save takes
effect on the guard's next pass with no restart. The hub renders the theme, previews the diff, validates it, writes the
profile atomically and records every change with its local time (ADR-0137 keeps the rules here, never in the view; ADR-0030
makes the write atomic).

No browser runs: the seam under test is the hub's own functions and its `GET`/`POST /api/limits` route.
"""
import datetime as dt
import http.client
import importlib.util
import json
import pathlib
import re
import shutil
import subprocess
import threading
from http.server import ThreadingHTTPServer

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib" / "squad-goal" / "scripts" / "hub.py"

# A profile shaped like the ones the loop carries: the `:=` form with a trailing comment, a second setting, and a line
# the writer must never touch.
PROFILE = """\
# the profile under test
: "${SG_REPO:=x/y}"
: "${SG_TOKEN_BURN_CAP:=1000}"   # the daily token cap
: "${SG_TOKEN_PAUSE_FACTOR:=2}"
"""


@pytest.fixture(scope="module")
def ctx(tmp_path_factory):
    """The hub imported against a profile directory, a state dir and a checkout of its own; nothing here is the live copy."""
    root = tmp_path_factory.mktemp("spend-limits")
    prof, state, co = root / "profiles", root / "state", root / "checkout"
    for d in (prof, state, co):
        d.mkdir()
    (prof / "t.sh").write_text(PROFILE)
    mp = pytest.MonkeyPatch()
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("SG_PROFILE", "t"),
                 ("SG_PROFILES_DIR", str(prof)), ("SG_STATE", str(state)), ("SG_CHECKOUT", str(co)),
                 ("SG_HUB_DIR", str(root / "hub")), ("HOME", str(root))):
        mp.setenv(k, v)
    for k in ("SG_STATE_BASE", "SG_SHARED", "SG_TOKEN_GUARD", "SG_TOKEN_BURN_CAP", "SG_TOKEN_PAUSE_FACTOR"):
        mp.delenv(k, raising=False)
    spec = importlib.util.spec_from_file_location("hub_spend_limits", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    yield {"hub": mod, "prof": prof, "state": state}
    mp.undo()


@pytest.fixture(autouse=True)
def fresh(ctx):
    """Every test starts from the profile above, an empty state dir and no ledger."""
    (ctx["prof"] / "t.sh").write_text(PROFILE)
    for p in ctx["state"].iterdir():
        p.unlink()
    return ctx


def profile_text(ctx):
    return (ctx["prof"] / "t.sh").read_text()


def sourced(ctx, key):
    """One key as bash reads it out of the written profile: the same read the token guard makes."""
    out = subprocess.run(["bash", "-c", '. "$1" >/dev/null 2>&1; printf "%s" "${' + key + '}"', "_", str(ctx["prof"] / "t.sh")],
                         capture_output=True, text=True, timeout=20)
    return out.stdout


def save(ctx, **body):
    return ctx["hub"].limits_set(body)


# --- what the theme reads

def test_the_profile_the_guard_reads_supplies_guard_cap_and_factor(ctx):
    (ctx["prof"] / "t.sh").write_text('SG_TOKEN_GUARD=off\nSG_TOKEN_BURN_CAP=2000000\nSG_TOKEN_PAUSE_FACTOR=4\n')
    r = ctx["hub"].limits_read()
    assert (r["guard"], r["cap"], r["factor"]) == ("off", 2000000, 4)


def test_the_guard_defaults_hold_when_the_profile_names_nothing(ctx):
    (ctx["prof"] / "t.sh").write_text("# an empty profile\n")
    r = ctx["hub"].limits_read()
    assert (r["guard"], r["cap"], r["factor"], r["window_h"]) == ("on", 1500000, 3, 24)


def test_the_theme_shows_the_last_hour_and_window_burn(ctx, monkeypatch):
    monkeypatch.setattr(ctx["hub"], "burn_read", lambda **kw: {"total": 100 if kw.get("hours") == 1 else 2400, "burners": []})
    r = ctx["hub"].limits_read()
    assert (r["hour_burn"], r["window_burn"]) == (100, 2400)


def test_the_theme_shows_whether_dispatch_is_paused(ctx):
    assert ctx["hub"].limits_read()["paused"] is False
    (ctx["state"] / "token-pause").write_text("paused\n")
    assert ctx["hub"].limits_read()["paused"] is True


def test_the_pause_line_is_the_factor_times_the_caps_hourly_share(ctx):
    """1500000 over 24 h is 62500 an hour; at 3x the line is 187500, the number the guard itself computes."""
    (ctx["prof"] / "t.sh").write_text('SG_TOKEN_BURN_CAP=1500000\nSG_TOKEN_PAUSE_FACTOR=3\n')
    assert ctx["hub"].limits_read()["pause_line"] == 187500


# --- the preview and the write

def test_a_preview_shows_the_diff_and_writes_nothing(ctx):
    before = profile_text(ctx)
    out = save(ctx, cap="2000000", factor="2", guard="on", confirm=False)
    assert out == {"diff": ["Spend & limits · token cap: 1000 -> 2000000"]}, out
    assert profile_text(ctx) == before


def test_the_guard_switch_is_its_own_diff_line(ctx):
    out = save(ctx, guard="off", cap="1000", factor="2", confirm=False)
    assert out["diff"] == ["token guard: off"], out


def test_a_confirmed_save_writes_the_settings_and_leaves_every_other_line(ctx):
    out = save(ctx, guard="off", cap="2000000", factor="4", confirm=True)
    assert out["diff"] == ["Spend & limits · token cap: 1000 -> 2000000", "Spend & limits · pause factor: 2 -> 4", "token guard: off"], out
    text = profile_text(ctx)
    assert "# the profile under test" in text and ': "${SG_REPO:=x/y}"' in text and "# the daily token cap" in text
    assert (sourced(ctx, "SG_TOKEN_BURN_CAP"), sourced(ctx, "SG_TOKEN_PAUSE_FACTOR"), sourced(ctx, "SG_TOKEN_GUARD")) == ("2000000", "4", "off")
    assert subprocess.run(["bash", "-n", str(ctx["prof"] / "t.sh")], capture_output=True).returncode == 0
    assert out["state"]["cap"] == 2000000 and out["state"]["guard"] == "off"


@pytest.mark.parametrize("line", [': "${SG_TOKEN_BURN_CAP:=1000}"', "SG_TOKEN_BURN_CAP=1000", "export SG_TOKEN_BURN_CAP=1000"])
def test_the_cap_line_is_replaced_in_every_form_it_takes(ctx, line):
    (ctx["prof"] / "t.sh").write_text("# before\n" + line + "   # cap\n# after\n")
    out = save(ctx, cap="2000", factor="3", guard="on", confirm=True)
    assert out["diff"] == ["Spend & limits · token cap: 1000 -> 2000"], out
    assert sourced(ctx, "SG_TOKEN_BURN_CAP") == "2000"
    assert profile_text(ctx).replace("2000", "1000") == "# before\n" + line + "   # cap\n# after\n"


def test_a_profile_that_names_no_cap_gets_the_line_appended(ctx):
    (ctx["prof"] / "t.sh").write_text("# nothing here\n")
    save(ctx, cap="2000", factor="3", guard="on", confirm=True)
    assert sourced(ctx, "SG_TOKEN_BURN_CAP") == "2000"
    assert profile_text(ctx).startswith("# nothing here\n")


@pytest.mark.parametrize("bad", ["1.5", "0", "-2", "abc", "", "1e6", "12 34", "1_000", True])
def test_a_cap_that_is_not_a_positive_whole_number_is_refused(ctx, bad):
    before = profile_text(ctx)
    out = save(ctx, cap=bad, factor="2", guard="on", confirm=True)
    assert out.get("err"), out
    assert "positive whole number" in out["err"]
    assert profile_text(ctx) == before


@pytest.mark.parametrize("typed,stored", [("6M", 6000000), ("250M", 250000000), ("1T", 1000000000000)])
def test_a_cap_typed_with_a_unit_suffix_stores_a_plain_integer(ctx, typed, stored):
    out = save(ctx, cap=typed, factor="2", guard="on", confirm=True)
    assert not out.get("err"), out
    assert out["state"]["cap"] == stored, out
    assert sourced(ctx, "SG_TOKEN_BURN_CAP") == str(stored)


@pytest.mark.parametrize("bad", ["0", "-1", "abc", "", True])
def test_a_pause_factor_that_is_not_a_positive_number_is_refused(ctx, bad):
    before = profile_text(ctx)
    out = save(ctx, cap="1000", factor=bad, guard="on", confirm=True)
    assert out.get("err"), out
    assert profile_text(ctx) == before


def test_a_guard_state_other_than_on_or_off_is_refused(ctx):
    before = profile_text(ctx)
    out = save(ctx, cap="1000", factor="2", guard="maybe", confirm=True)
    assert out.get("err"), out
    assert profile_text(ctx) == before


# --- the record of every change

def test_each_change_is_recorded_with_its_local_time_and_the_last_one_is_shown(ctx):
    save(ctx, cap="2000", factor="2", guard="on", confirm=True)
    save(ctx, cap="2000", factor="2", guard="off", confirm=True)
    lines = (ctx["state"] / "spend-limits-ledger.jsonl").read_text().splitlines()
    assert len(lines) == 2, lines
    recs = [json.loads(x) for x in lines]
    assert [r["text"] for r in recs] == ["Spend & limits · token cap: 1000 -> 2000", "token guard: off"]
    for r in recs:
        dt.datetime.strptime(r["at"], "%Y-%m-%d %H:%M:%S")   # a local time, read by a person
    assert ctx["hub"].limits_read()["last_change"]["text"] == "token guard: off"


def test_a_save_that_changes_nothing_writes_no_ledger_line(ctx):
    out = save(ctx, cap="1000", factor="2", guard="on", confirm=True)
    assert out["diff"] == [] and out["state"]["cap"] == 1000
    assert not (ctx["state"] / "spend-limits-ledger.jsonl").exists()


# --- the route

@pytest.mark.heavy   # serves the theme and saves it: 1.4 s here, 2 s on a CI runner
def test_the_route_serves_the_theme_and_takes_a_confirmed_save(ctx):
    mod = ctx["hub"]
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        port = srv.server_port
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request("GET", "/api/limits", headers={"Host": f"127.0.0.1:{port}"})
        r = c.getresponse()
        got = json.loads(r.read() or b"{}")
        assert r.status == 200 and (got["guard"], got["cap"], got["factor"]) == ("on", 1000, 2), got
        c.close()
        head = {"Host": f"127.0.0.1:{port}", "Origin": f"http://127.0.0.1:{port}", "Content-Type": "application/json"}
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request("POST", "/api/limits", body=json.dumps({"cap": "3000", "factor": "2", "guard": "on", "confirm": False}), headers=head)
        r = c.getresponse()
        assert json.loads(r.read())["diff"] == ["Spend & limits · token cap: 1000 -> 3000"]
        assert sourced(ctx, "SG_TOKEN_BURN_CAP") == "1000"
        c.close()
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request("POST", "/api/limits", body=json.dumps({"cap": "3000", "factor": "2", "guard": "on", "confirm": True}), headers=head)
        r = c.getresponse()
        assert r.status == 200 and sourced(ctx, "SG_TOKEN_BURN_CAP") == "3000"
        c.close()
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request("POST", "/api/limits", body=json.dumps({"cap": "1.5", "factor": "2", "guard": "on", "confirm": True}), headers=head)
        r = c.getresponse()
        assert r.status == 400 and "positive whole number" in json.loads(r.read())["err"]
        c.close()
    finally:
        srv.shutdown()
        srv.server_close()


# --- the page

def test_the_page_carries_the_theme_and_fits_a_phone(ctx):
    page = ctx["hub"].served_page()
    assert "Spend &amp; limits" in page
    assert "id=lbody" in page and "function limRender(" in page and "function limLoad(" in page
    assert '"lim"' in page   # the accordion's open state is remembered like the other three
    m = re.search(r"@media \(max-width:600px\)\{body\{height:auto\}(.*?)\}\}", page, re.S)
    assert m, "no phone rule: the theme would scroll sideways at 390 px"
    phone = m.group(1)
    # The docked board lays the window out as a two-column grid, so the phone states that layout too: a rule that only
    # turns the flex columns around leaves the 300 px sidebar and the board column wider than the screen (#926).
    assert "body.dockb #win{display:block" in phone, phone
    assert "#side{width:auto" in phone, phone


# --- the theme's two blocks (#1282): four facts in Now, the three settings in Settings

LIM = {"profile": "/p/profiles/t.sh", "file": "/p/profiles/t.sh", "guard": "on", "cap": 1500000, "factor": 3,
       "window_h": 24, "pause_line": 187500, "hour_burn": 100, "window_burn": 2400, "paused": False,
       "last_change": {"at": "2026-11-04 09:12:00", "text": "Spend & limits · token cap: 1000 -> 2000"}}


def lim_eval(hub, script):
    """The theme's own helpers in node, over a fake turn of the page: the script runs last and prints one JSON line."""
    if shutil.which("node") is None:
        pytest.skip("node is required")
    page = hub.PAGE
    esc = re.search(r"^const esc=.*$", page, re.M)
    assert esc, "the page lost the esc helper the harness slices out"
    tk = re.search(r"tk=n=>[^;]*;", page)
    assert tk, "the page lost the tk helper the harness slices out"
    sett = page[page.index("// SETT{"):page.index("// }SETT")]
    lim = page[page.index("let limS=null"):page.index("async function limLoad()")]
    # The harness runs these slices, not the page: a helper that leaves its region would make the test read a copy.
    for fn in ("function setTile(", "function setNow(", "function setRow(", "function setSettings(", "function setChip("):
        assert fn in sett, f"{fn!r} left the // SETT region: fix the harness slice, not the renderer"
    assert "function limRender(" in lim and "setNow(" in lim, "the theme moved out of the // SETT and limRender slices"
    harness = """
const STORE={};
const $=s=>STORE[s]||(STORE[s]={innerHTML:"",textContent:"",className:""});
const setBar=()=>{};
"""
    body = "\n".join([harness, esc.group(0), tk.group(0), sett, lim, script])
    p = subprocess.run(["node", "-"], input=body, capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def lim_draw(hub, view):
    """The theme's own renderer in node over one view: the two blocks the owner reads, and both chips."""
    return lim_eval(hub, f"limS={json.dumps(view)};limRender();"
                         'console.log(JSON.stringify({html:STORE["#lbody"].innerHTML,'
                         'sr:STORE["#limsr"].textContent,srcls:STORE["#limsr"].className,'
                         'nav:STORE["#limnav"].textContent,navcls:STORE["#limnav"].className}))')


def lim_blocks(out):
    html = out["html"]
    i, j = html.index("data-blk=now"), html.index("data-blk=settings")
    i, j = html.rindex("<div class=setblk ", 0, i), html.rindex("<div class=setblk ", 0, j)   # each block whole
    return html[i:j], html[j:]


def test_the_now_block_holds_four_fact_tiles_and_no_input(ctx):
    now, _ = lim_blocks(lim_draw(ctx["hub"], LIM))
    tiles, note = now.split("<p class=note>", 1)
    assert re.findall(r'<div class="tile ([a-z]+)">', tiles) == ["ok", "neutral", "neutral", "neutral"], tiles
    assert re.findall(r"<span class=tk>(.*?)</span>", tiles) == \
           ["Dispatch", "Last hour", "Window · 24 h", "Pauses over"], tiles
    # Four tiles and nothing else in the container: a fifth, unlabelled fact cannot slip in.
    assert re.sub(r'<div class="tile [a-z]+">.*?</div>', "", tiles) == \
           '<div class=setblk data-blk=now data-theme="lim"><h3 class=blkh>Now</h3><div class=now></div>', tiles
    for ctl in ("<input", "<select", "<textarea", "<button"):
        assert ctl not in now, f"the Spend & limits Now block carries {ctl}"
    # The note row carries the profile path and no other fact: the last change is data, never a fifth line.
    assert "Last change" not in now, "a fifth fact sits in the Now block, outside its four tiles"
    assert re.fullmatch(rf"Profile file: <code>{re.escape(LIM['profile'])}</code></p></div>", note), note


def test_the_profile_path_is_visible_text_in_now_not_a_tooltip(ctx):
    now, _ = lim_blocks(lim_draw(ctx["hub"], LIM))
    assert "Profile file" in now and LIM["profile"] in now, now
    visible = re.sub(r'\btitle="[^"]*"', "", now)   # what a reader sees, with every hover removed
    assert LIM["profile"] in visible, "the profile path is readable only on a hover"


def test_the_settings_block_holds_only_the_three_editable_settings(ctx):
    _, settings = lim_blocks(lim_draw(ctx["hub"], LIM))
    assert settings.count("class=setrow") == 3, settings
    assert re.findall(r"<span class=sl>(.*?)</span>", settings) == \
           ["Token guard", "Daily token cap", "Pause factor"], settings
    for fact in ("class=\"tile", "class=tile", "<span class=tk>", "Last hour", "Pauses over", "Profile file"):
        assert fact not in settings, f"the Settings block carries the fact {fact!r}"


def test_the_guard_is_a_switch_that_shows_its_state(ctx):
    _, settings = lim_blocks(lim_draw(ctx["hub"], LIM))
    assert "select id=limg" not in settings and "id=limg type=checkbox" in settings, settings
    assert re.search(r"<input id=limg type=checkbox role=switch[^>]* checked", settings), "a guard on does not read as on"
    assert "Off: dispatch never pauses on token burn." in settings, settings
    off = lim_blocks(lim_draw(ctx["hub"], dict(LIM, guard="off")))[1]
    assert re.search(r"<input id=limg type=checkbox role=switch[^>]*>", off) and \
           not re.search(r"<input id=limg[^>]* checked", off), "a guard off reads checked"


def test_a_flipped_switch_answers_the_word_the_hub_reads(ctx):
    assert lim_eval(ctx["hub"], 'STORE["#limg"]={checked:true};console.log(JSON.stringify(limVals().guard))') == "on"
    assert lim_eval(ctx["hub"], 'STORE["#limg"]={checked:false};console.log(JSON.stringify(limVals().guard))') == "off"


def test_the_cap_reads_with_a_unit_suffix_and_its_field_takes_text(ctx):
    _, settings = lim_blocks(lim_draw(ctx["hub"], LIM))
    field = re.search(r"<input id=limc ([^>]*)>", settings)
    assert field, settings
    assert 'value="1.5M"' in field.group(0), field.group(0)
    assert "type=number" not in field.group(0), "a number input refuses a unit suffix"
    got = lim_eval(ctx["hub"], 'console.log(JSON.stringify([1500000,6000000,250000000,1000000000000,999,8000000000]'
                                ".map(limCapFmt)))")
    assert got == ["1.5M", "6M", "250M", "1T", "999", "8B"], got


def test_the_cap_field_accepts_a_k_m_b_or_t_suffix(ctx):
    got = lim_eval(ctx["hub"], 'console.log(JSON.stringify(["6M","250M","1T","1500000","2.5K","1.5M","1.5","abc","0M",'
                                '"-3M",""].map(limCapParse)))')
    assert got == [6000000, 250000000, 1000000000000, 1500000, 2500, 1500000, None, None, None, None, None], got


def test_the_pause_factor_row_shows_the_hourly_burn_it_pauses_at(ctx):
    """The row states the arithmetic behind the pause line, so the number is never read as a bare multiple (#2434)."""
    _, settings = lim_blocks(lim_draw(ctx["hub"], LIM))
    assert "3 × (cap ÷ 24) = 188K tokens/h" in settings, settings
    other = dict(LIM, cap=1000000000000, factor=2, pause_line=83333333332)
    _, settings = lim_blocks(lim_draw(ctx["hub"], other))
    assert "2 × (cap ÷ 24) = 83.33B tokens/h" in settings, settings


def test_a_paused_dispatch_shows_err_on_its_tile_and_on_both_chips(ctx):
    out = lim_draw(ctx["hub"], dict(LIM, paused=True))
    now, _ = lim_blocks(out)
    assert 'class="tile err"' in now and ">paused<" in now, now
    assert (out["sr"], out["nav"]) == ("paused", "paused"), out
    assert out["srcls"].split() == ["sr", "err"] and out["navcls"].split() == ["err"], out


def test_the_summary_chip_reports_the_same_dispatch_state_as_the_nav_line(ctx):
    for view, want in ((LIM, "on"), (dict(LIM, paused=True), "paused"),
                       (dict(LIM, guard="off"), "guard off · never pauses")):
        out = lim_draw(ctx["hub"], view)
        assert out["sr"] == out["nav"] == want, out


def test_a_guard_off_names_itself_and_shows_no_pause_line(ctx):
    """With the guard off dispatch never pauses on token burn, so the tile is greyed and the chip says so (#2434)."""
    out = lim_draw(ctx["hub"], dict(LIM, guard="off"))
    now, _ = lim_blocks(out)
    pause = re.search(r'<div class="tile ([a-z]+)"><span class=tk>Pauses over</span><span class=tv>(.*?)</span></div>', now)
    assert pause, now
    assert pause.group(1) == "muted", pause.group(0)
    assert not re.search(r"\d", pause.group(2)), f"the tile still shows a pause line: {pause.group(2)!r}"
    assert out["sr"] == "guard off · never pauses" and out["navcls"].split() == [], out


def test_a_guard_on_keeps_the_pause_line_its_tone_and_its_number(ctx):
    now, _ = lim_blocks(lim_draw(ctx["hub"], LIM))
    pause = re.search(r'<div class="tile ([a-z]+)"><span class=tk>Pauses over</span><span class=tv>(.*?)</span></div>', now)
    assert pause and pause.group(1) == "neutral", now
    assert pause.group(2) == "188K tokens/h", pause.group(2)
