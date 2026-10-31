"""In the real page (headless Chrome): grouping by status, expand and fold by double-press, a glyph on every card, the pan into view."""
import json

import pytest

from tests.dashboard_helpers import ROOT
from tests.test_dashboard_page import drive, needs_ego

FIX = ROOT / "tests" / "fixtures" / "run-router-waves"

# The four children of this run all landed, so they read as one group "route · 4 passed".
# The record reads and hashes in real time, which the headless virtual clock does not wait for, so the probe spins on message tasks (not timers) until the cards show.
LOAD = ('const q=(s)=>[...document.querySelectorAll(s)];'
        'const press=(el)=>{el.dispatchEvent(new PointerEvent("pointerdown",{bubbles:true,button:0}));window.dispatchEvent(new PointerEvent("pointerup"));};'
        'const gtext=' + json.dumps((FIX / "graph.json").read_text()) + ';const rtext=' + json.dumps((FIX / "run" / "record.jsonl").read_text())
        + ';put("f-graph",gtext,"graph.json");await wait();put("f-record",rtext,"record.jsonl");'
        'const spin=()=>new Promise(r=>{const c=new MessageChannel();c.port1.onmessage=()=>r();c.port2.postMessage(0);});'
        'for(let i=0;i<200000&&!q(".card.child,.card[data-group]").length;i++)await spin();await wait();')


@needs_ego
@pytest.mark.heavy
def test_a_double_press_expands_the_group_card_in_place_and_a_double_press_on_a_child_folds_it_back(tmp_path):
    steps = (LOAD + 'const start={groups:q(".card[data-group]").length,kids:q(".card.child").length,name:(q(".card[data-group] .nm")[0]||{}).textContent};'
             'press(q(".card[data-group]")[0]);press(q(".card[data-group]")[0]);await wait();'
             'const open={groups:q(".card[data-group]").length,kids:q(".card.child").length,ingroup:q(".card.child.ingroup").length,bus:q("#edges g.edge.child").length,panel:!document.body.classList.contains("noside")};'
             'press(q(".card.child")[0]);press(q(".card.child")[0]);await wait();'
             'const shut={groups:q(".card[data-group]").length,kids:q(".card.child").length};'
             'document.title="RESULT "+JSON.stringify({start,open,shut});')
    got = drive(tmp_path, steps)
    assert got["start"] == {"groups": 1, "kids": 0, "name": "route · 4 passed"}
    assert got["open"] == {"groups": 0, "kids": 4, "ingroup": 4, "bus": 1, "panel": False}   # the double-press leaves the panel as it was
    assert got["shut"] == {"groups": 1, "kids": 0}


@needs_ego
@pytest.mark.heavy
def test_every_card_the_record_touched_carries_a_state_glyph(tmp_path):
    steps = (LOAD + 'press(q(".card[data-group]")[0]);press(q(".card[data-group]")[0]);await wait();'
             'const cards=q(".card[data-name]");'
             'document.title="RESULT "+JSON.stringify({cards:cards.length,glyphs:cards.filter(c=>c.querySelector("text.gl")&&c.querySelector("text.gl").textContent.trim()).length});')
    got = drive(tmp_path, steps)
    assert got["cards"] > 4 and got["glyphs"] == got["cards"]


@needs_ego
@pytest.mark.heavy
def test_selecting_a_node_under_the_drawer_pans_it_clear_without_changing_the_zoom(tmp_path):
    """The drawer covers the right edge: a zoomed graph puts a card under it, and pressing that card pans it clear."""
    steps = (LOAD + 'document.getElementById("b-panel").click();await wait();for(let i=0;i<8;i++)document.getElementById("b-in").click();await wait();'
             'const map=document.getElementById("map"),pd=(t,x,y,el)=>(el||window).dispatchEvent(new PointerEvent(t,{bubbles:true,button:0,clientX:x,clientY:y}));'
             'pd("pointerdown",300,400,map);pd("pointermove",550,400);pd("pointerup",550,400);await wait();'
             'const cards=q(".card[data-name]:not([data-group]):not(.child)").sort((a,b)=>b.getBoundingClientRect().right-a.getBoundingClientRect().right);'
             'const drawer=()=>document.getElementById("left").getBoundingClientRect();const last=cards[0].dataset.name;'
             'const under=cards[0].querySelector("rect").getBoundingClientRect().right>drawer().left;'
             'const zoom=document.getElementById("zoom").textContent,tx0=document.getElementById("vp").getAttribute("transform");'
             'press(cards[0]);await wait();'
             'const card=q(".card[data-name]").find(c=>c.dataset.name===last);'
             'document.title="RESULT "+JSON.stringify({under,clear:card.querySelector("rect").getBoundingClientRect().right<=drawer().left+1,panel:drawer().width,'
             'zoomSame:zoom===document.getElementById("zoom").textContent,moved:tx0!==document.getElementById("vp").getAttribute("transform"),open:!document.body.classList.contains("noside")});')
    got = drive(tmp_path, steps)
    assert got["open"] and got["under"]   # the press lands on a card the drawer was covering, so the reveal has work to do
    assert got["zoomSame"] and got["moved"]
    assert got["clear"]   # the rightmost node ends up left of the drawer, not under it


@needs_ego
@pytest.mark.heavy
def test_every_card_is_a_keyboard_stop_enter_selects_and_space_opens_and_folds_a_group_and_the_focus_stays(tmp_path):
    steps = (LOAD + 'const key=(el,k)=>el.dispatchEvent(new KeyboardEvent("keydown",{key:k,bubbles:true}));const act=()=>document.activeElement&&document.activeElement.dataset?document.activeElement.dataset.name:null;'
             'const stops=q(".card[data-name]").every(c=>c.getAttribute("tabindex")==="0"&&c.getAttribute("role")==="button"&&c.getAttribute("aria-label"));'
             'const grp=q(".card[data-group]")[0];key(grp," ");await wait();const open={groups:q(".card[data-group]").length,kids:q(".card.child").length,focus:(act()||"").startsWith("route.")};'
             'key(q(".card.child")[0],"Enter");await wait();const sel=q(".card.child.sel").length;'
             'key(document.activeElement," ");await wait();const shut={groups:q(".card[data-group]").length,focus:act()===q(".card[data-group]")[0].dataset.name};'
             'document.title="RESULT "+JSON.stringify({stops,open,sel,shut});')
    got = drive(tmp_path, steps)
    assert got["stops"] is True
    assert got["open"] == {"groups": 0, "kids": 4, "focus": True}
    assert got["sel"] == 1
    assert got["shut"] == {"groups": 1, "focus": True}


@needs_ego
@pytest.mark.heavy
def test_fit_with_the_panel_open_keeps_every_card_left_of_the_drawer(tmp_path):
    """The drawer takes its width off the map, so a fit with it open is a different fit."""
    steps = (LOAD + 'const cards=()=>q(".card[data-name]").map(c=>c.getBoundingClientRect());'
             'document.getElementById("b-fit").click();await wait();const shut=Math.min(...cards().map(r=>r.left));'
             'document.getElementById("b-panel").click();await wait();document.getElementById("b-fit").click();await wait();'
             'const drawer=document.getElementById("left").getBoundingClientRect();'
             'document.title="RESULT "+JSON.stringify({open:drawer.width>300,shut,now:Math.min(...cards().map(r=>r.left)),clear:Math.max(...cards().map(r=>r.right))<=drawer.left+1});')
    got = drive(tmp_path, steps)
    assert got["open"] is True and got["clear"] is True   # nothing sits under the drawer
    assert got["now"] != got["shut"]   # the fit gave the drawer its width


@needs_ego
@pytest.mark.heavy
def test_fit_leaves_every_card_clear_of_the_replay_bar_in_the_panel_footer(tmp_path):
    """#1813: the bar is a row of #footer now, and the fit still has to leave every card above the row's own top."""
    steps = (LOAD + 'press(q(".card[data-group]")[0]);press(q(".card[data-group]")[0]);await wait();document.getElementById("b-fit").click();await wait();'
             'const bar=document.getElementById("replay").getBoundingClientRect(),low=Math.max(...q(".card[data-name]").map(c=>c.getBoundingClientRect().bottom));'
             'document.title="RESULT "+JSON.stringify({clear:low<=bar.top,shown:bar.height>0,'
             'inside:document.getElementById("footer").contains(document.getElementById("replay"))});')
    got = drive(tmp_path, steps)
    assert got == {"clear": True, "shown": True, "inside": True}


def _load_run(folder):
    """The probe steps that load a run folder's graph and record, then wait for the cards."""
    return ('const q=(s)=>[...document.querySelectorAll(s)];'
            'const press=(el)=>{el.dispatchEvent(new PointerEvent("pointerdown",{bubbles:true,button:0}));window.dispatchEvent(new PointerEvent("pointerup"));};'
            'const gtext=' + json.dumps((folder / "graph.json").read_text()) + ';const rtext=' + json.dumps((folder / "run" / "record.jsonl").read_text())
            + ';put("f-graph",gtext,"graph.json");await wait();put("f-record",rtext,"record.jsonl");'
            'const spin=()=>new Promise(r=>{const c=new MessageChannel();c.port1.onmessage=()=>r();c.port2.postMessage(0);});'
            'for(let i=0;i<200000&&!document.getElementById("r-pos").textContent;i++)await spin();await wait();')


@needs_ego
@pytest.mark.heavy
def test_a_square_in_a_group_card_is_a_keyboard_stop_enter_selects_that_child_and_the_focus_ring_shows(tmp_path):
    steps = (LOAD + 'const key=(el,k)=>el.dispatchEvent(new KeyboardEvent("keydown",{key:k,bubbles:true}));'
             'const sqs=q("g.sq");const stops=sqs.every(s=>s.getAttribute("tabindex")==="0"&&s.getAttribute("role")==="button"&&s.getAttribute("aria-label"));'
             'sqs[2].focus();const ring=getComputedStyle(sqs[2].querySelector("rect.fr")).stroke;const other=getComputedStyle(sqs[1].querySelector("rect.fr")).stroke;'
             'const want=sqs[2].dataset.sq;key(sqs[2],"Enter");await wait();'
             'const after=q("g.sq.sel").map(s=>s.dataset.sq);const focus=document.activeElement&&document.activeElement.dataset?document.activeElement.dataset.sq:null;'
             'document.title="RESULT "+JSON.stringify({n:sqs.length,stops,ringOn:ring!=="none",otherOff:other==="none",selectedOk:after.length===1&&after[0]===want,focusOk:focus===want,side:!document.body.classList.contains("noside")});')
    got = drive(tmp_path, steps)
    assert got["n"] == 4 and got["stops"] is True
    assert got["ringOn"] is True and got["otherOff"] is True
    assert got["selectedOk"] is True and got["focusOk"] is True and got["side"] is True


@needs_ego
@pytest.mark.heavy
def test_a_record_corrupt_mid_file_shows_its_valid_prefix_under_a_banner_that_names_the_bad_line(tmp_path):
    lines = (FIX / "run" / "record.jsonl").read_text().splitlines()
    bad = "\n".join(lines[:20] + ["{this is not json"] + lines[20:]) + "\n"
    steps = ('const q=(s)=>[...document.querySelectorAll(s)];const gtext=' + json.dumps((FIX / "graph.json").read_text()) + ';const rtext=' + json.dumps(bad)
             + ';put("f-graph",gtext,"graph.json");await wait();put("f-record",rtext,"record.jsonl");'
             'const spin=()=>new Promise(r=>{const c=new MessageChannel();c.port1.onmessage=()=>r();c.port2.postMessage(0);});'
             'const b=document.getElementById("recbad");for(let i=0;i<200000&&b.hidden;i++)await spin();await wait();'
             'document.title="RESULT "+JSON.stringify({shown:!b.hidden,text:b.textContent,cards:q(".card[data-name]").length,replay:document.getElementById("r-pos").textContent});')
    got = drive(tmp_path, steps)
    assert got["shown"] is True and "line 21" in got["text"] and "20 events" in got["text"]
    assert got["cards"] > 0 and "of 20" in got["replay"]


@needs_ego
def test_a_record_with_no_valid_first_line_is_still_refused_and_shows_no_banner(tmp_path):
    steps = ('put("f-record","{nope\\n","record.jsonl");await wait();'
             'document.title="RESULT "+JSON.stringify({hidden:document.getElementById("recbad").hidden,status:document.getElementById("status").textContent});')
    got = drive(tmp_path, steps)
    assert got["hidden"] is True and "line 1 is not JSON" in got["status"]


@needs_ego
@pytest.mark.heavy
def test_a_live_record_shows_no_banner_for_a_half_written_last_line_and_a_banner_for_a_completed_bad_line(tmp_path):
    lines = (FIX / "run" / "record.jsonl").read_text().splitlines()
    start = json.loads(lines[0])
    start["defaults"] = {"engine": {"dashboard": {"reread_seconds": 0.05}}}   # the engine's own reread setting: a short tick keeps the probe inside the virtual-time budget
    lines[0] = json.dumps(start)
    head = "\n".join(lines[:20]) + "\n"
    steps = ('try{const q=(s)=>[...document.querySelectorAll(s)];const b=document.getElementById("recbad");'
             'const spin=()=>new Promise(r=>{const c=new MessageChannel();c.port1.onmessage=()=>r();c.port2.postMessage(0);});'
             'const gtext=' + json.dumps((FIX / "graph.json").read_text()) + ';let rec=' + json.dumps(head + '{"event":"cal') + ';'
             'const sleep=(ms)=>new Promise(r=>setTimeout(r,ms));'
             'await loadRunSource({name:"live",live:true,graph:async()=>gtext,record:async()=>rec});'
             'for(let i=0;i<200000&&!q(".card[data-name]").length;i++)await spin();await wait();'
             'const half={hidden:b.hidden,events:S.events.length};'
             # the line is finished as non-JSON and later lines follow: the next reread must show the banner
             'rec=' + json.dumps(head + "{this is not json\n" + "\n".join(lines[20:24]) + "\n") + ';'
             'for(let i=0;i<30&&b.hidden;i++)await sleep(100);'
             'document.title="RESULT "+JSON.stringify({half,shown:!b.hidden,text:b.textContent,events:S.events.length,cards:q(".card[data-name]").length});}catch(e){document.title="RESULT "+JSON.stringify({err:String(e)});}')
    got = drive(tmp_path, steps)
    assert "err" not in got, got
    assert got["half"] == {"hidden": True, "events": 20}
    assert got["shown"] is True and "line 21" in got["text"] and "20 events" in got["text"]
    assert got["events"] == 20 and got["cards"] > 0


@needs_ego
@pytest.mark.heavy
def test_150_children_expand_at_a_readable_zoom_with_the_first_cards_in_view(tmp_path):
    from tests.dashboard_helpers import synth_run
    run = synth_run(tmp_path / "n150", 150)
    steps = (_load_run(run) + 'const g=q(".card[data-group]")[0];const name=g.querySelector(".nm").textContent;press(g);press(g);await wait();'
             'const first=q(".card.child").find(c=>c.dataset.name==="route.router.1").getBoundingClientRect();'
             'document.title="RESULT "+JSON.stringify({name,kids:q(".card.child").length,zoom:document.getElementById("zoom").textContent,left:first.left,top:first.top,inside:first.right<innerWidth&&first.bottom<innerHeight});')
    got = drive(tmp_path, steps)
    assert got["name"] == "route · 150 passed" and got["kids"] == 150
    assert got["zoom"] == "70%" and got["left"] >= 0 and got["top"] >= 0 and got["inside"] is True


@needs_ego
@pytest.mark.heavy
def test_a_dispatch_with_no_children_and_a_dispatch_whose_children_all_failed_draw(tmp_path):
    from tests.dashboard_helpers import synth_run
    zero = _load_run(synth_run(tmp_path / "n0", 0)) + 'document.title="RESULT "+JSON.stringify({groups:q(".card[data-group]").length,kids:q(".card.child").length,cards:q(".card[data-name]").length,err:q(".card[data-name].err").length});'
    got0 = drive(tmp_path, zero)
    assert got0["groups"] == 0 and got0["kids"] == 0 and got0["cards"] > 0 and got0["err"] == 0
    allfail = _load_run(synth_run(tmp_path / "f4", 4, "fail")) + ('document.title="RESULT "+JSON.stringify({name:q(".card[data-group] .nm")[0].textContent,cls:q(".card[data-group]")[0].getAttribute("class"),'
                                                                    'sq:q("g.sq.s-failed").length,glyphs:q("g.sq text").map(t=>t.textContent).join("")});')
    got4 = drive(tmp_path, allfail)
    assert got4["name"] == "route · 4 failed" and "s-failed" in got4["cls"] and got4["sq"] == 4 and set(got4["glyphs"]) == {"✗"}
