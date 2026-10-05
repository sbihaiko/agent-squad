"""Tests that load the page: headless Chrome, the served source, and the push stream (#86, #106, #118)."""
import json
import os
import re
import signal
import subprocess
from pathlib import Path

import pytest

from tests.dashboard_helpers import OUT, PAGE_SRC, run_node
from tests.helpers import DESIGN, five, node

# --- the real page, in headless Chrome when it exists (the parity guard above never needs it) ---
CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


def _chrome_dump(page):
    """Headless Chrome on the page, one retry on a hang. A fresh profile hangs every call here, so the default one is used; the whole process group is killed on a hang so no
    helper keeps the pipe open. The `chrome` marker also makes `_one_chrome` queue the tests."""
    for attempt in (1, 2):
        proc = subprocess.Popen([str(CHROME), "--headless=new", "--disable-gpu", "--virtual-time-budget=5000", "--dump-dom", page.as_uri()],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, start_new_session=True)
        try:
            return proc.communicate(timeout=9)[0]
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            if attempt == 2:
                raise


def drive(tmp_path, steps):
    """Open the built page from disk, run the steps, and return the JSON the steps put in the title."""
    probe = ('<script>(async()=>{const put=(id,text,name)=>{const dt=new DataTransfer();dt.items.add(new File([text],name));'
             'const i=document.getElementById(id);i.files=dt.files;i.dispatchEvent(new Event("change"));};'
             'const wait=()=>new Promise(r=>setTimeout(r,200));' + steps + '})();</script></body>')
    p = tmp_path / "probe.html"
    p.write_text(OUT.read_text().replace("</body>", probe))
    out = _chrome_dump(p)
    m = re.search(r"<title>RESULT (.*?)</title>", out, re.S)
    assert m is not None, "the headless probe produced no RESULT title"
    return json.loads(m.group(1).replace("&quot;", '"').replace("&amp;", "&"))


def needs_chrome(fn):
    """Skip without Chrome; mark the test `chrome` so the xdist workers take turns (tests/conftest.py)."""
    return pytest.mark.chrome(pytest.mark.skipif(not CHROME.exists(), reason="Chrome is needed to drive the page")(fn))


@needs_chrome
def test_the_page_loads_the_five_phase_fixture_from_disk_and_shows_every_node(tmp_path):
    text = (DESIGN / "five-phases.graph.json").read_text()
    got = drive(tmp_path, f'put("f-graph",{json.dumps(text)},"five-phases.graph.json");await wait();'
                'document.title="RESULT "+JSON.stringify({cards:document.querySelectorAll(".card[data-name]").length,bad:document.querySelectorAll(".card[data-name].err").length,lines:document.querySelectorAll("#edges line").length,side:document.getElementById("side").textContent});')
    assert got["cards"] == len(json.loads(text)["nodes"]) and got["bad"] == 0 and got["lines"] > 20
    assert "valid" in got["side"] and "Served lists" in got["side"] and "never blocked" in got["side"]


@needs_chrome
def test_the_page_marks_a_bad_node_never_blocks_save_and_refuses_a_gate_edit(tmp_path):
    d = five()
    del node(d, "scout")["budget"]
    steps = (f'put("f-graph",{json.dumps(json.dumps(d))},"x.graph.json");await wait();'
             'const bad=[...document.querySelectorAll(".card[data-name].err")].map(c=>c.dataset.name);'
             'const g=[...document.querySelectorAll(".card")].find(c=>c.dataset.name==="spec_gate");'
             'g.dispatchEvent(new PointerEvent("pointerdown",{bubbles:true}));window.dispatchEvent(new PointerEvent("pointerup"));await wait();'
             'const t=document.getElementById("node-json");t.value=t.value.replace(/"check": "[^"]*"/,\'"check": "scope"\');'
             'document.getElementById("b-apply").click();await wait();'
             'document.title="RESULT "+JSON.stringify({bad,msg:document.getElementById("apply-msg").textContent,saveBlocked:document.getElementById("b-save").disabled,ro:document.getElementById("side").textContent.includes("Read-only: check and thresholds")});')
    got = drive(tmp_path, steps)
    assert got["bad"] == ["scout"] and got["saveBlocked"] is False and got["ro"]
    assert "read-only" in got["msg"]


# --- the run that a loopback server serves (ticket #86, ADR-0118) ---

def test_the_served_source_reads_the_record_and_the_graph_by_path_and_is_live():
    r = run_node([{"op": "httpsource", "name": "run-a", "files": {"run/record.jsonl": "REC", "run/graph.json": "GRAPH"}}])[0]
    assert r == {"name": "run-a", "live": True, "graph": "GRAPH", "record": "REC"}


def test_the_served_source_turns_a_missing_file_or_a_failed_fetch_into_null_like_the_other_sources():
    r = run_node([{"op": "httpsource", "name": "x", "files": {"run/record.jsonl": "REC", "run/graph.json": None}},
                  {"op": "httpsource", "name": "x", "files": {"run/record.jsonl": "throw"}}])
    assert r[0]["graph"] is None and r[0]["record"] == "REC"
    assert r[1]["record"] is None and r[1]["graph"] is None


def test_a_page_served_over_http_loads_its_run_with_no_folder_click_and_the_file_page_keeps_the_picker():
    src = PAGE_SRC()
    assert "core.httpSource(" in src and "location.protocol" in src
    assert "run/info.json" in src
    assert "showDirectoryPicker" in src and "restoreFolder" in src   # the file:// path stays


# --- the served page follows a run by push (ticket #106, ADR-0122) ---

def _ev(pos, event="progress", **kw):
    return {"pos": pos, "event": event, **kw}


def _push(events, steps, throws=False):
    return run_node([{"op": "push", "events": events, "steps": steps, "throws": throws}])[0]


def test_a_source_that_is_given_a_stream_function_offers_the_stream_and_one_that_is_not_offers_none():
    r = run_node([{"op": "httpstream", "stream": True}, {"op": "httpstream", "stream": False}])
    assert r[0] == {"hasStream": True, "opened": ["run/events"]}
    assert r[1] == {"hasStream": False, "opened": []}


def test_the_folder_handle_and_folder_list_sources_offer_no_stream_and_only_the_served_page_opens_one():
    src = PAGE_SRC()
    assert "stream" not in src[src.index("function handleSource"): src.index("const IDB_NAME")]
    assert src.count("new EventSource(") == 1
    assert "core.httpSource((p) => fetch(p, { cache: \"no-store\" }), name, " in src


def test_a_pushed_event_extends_the_events_and_a_pushed_verdict_ends_the_follow_and_closes_the_stream():
    r = _push([_ev(1, "run_start")], [{"line": _ev(2)}, {"line": _ev(3, "verdict")}])
    assert r["changed"] == [[1, 2], [1, 2, 3]] and r["ended"] == 1 and r["closed"] == 1 and r["fail"] == []


def test_an_event_the_page_already_has_is_skipped_so_a_replay_after_reconnect_paints_nothing_twice():
    r = _push([_ev(1, "run_start"), _ev(2)], [{"line": _ev(1, "run_start")}, {"line": _ev(2)}, {"line": _ev(3)}])
    assert r["changed"] == [[1, 2, 3]]


def test_a_position_gap_is_reported_and_never_painted():
    r = _push([_ev(1, "run_start")], [{"line": _ev(3)}, {"line": _ev(2)}])
    assert r["changed"] == [] and len(r["fail"]) == 1 and "gap" in r["fail"][0] and r["closed"] == 1


def test_the_end_event_closes_the_stream_so_the_browser_does_not_reconnect_in_a_loop():
    r = _push([_ev(1, "run_start")], [{"end": "verdict"}])
    assert r["ended"] == 1 and r["closed"] == 1 and r["fail"] == []


def test_an_end_for_any_other_reason_falls_back_to_the_re_read_with_the_reason():
    r = _push([_ev(1, "run_start")], [{"end": "gap"}])
    assert r["ended"] == 0 and r["closed"] == 1 and "gap" in r["fail"][0]


def test_a_dropped_stream_reconnects_then_gives_up_after_three_errors_and_a_closed_one_gives_up_at_once():
    r = _push([_ev(1, "run_start")], [{"error": False}, {"error": False}, {"error": False}])
    assert len(r["status"]) == 2 and len(r["fail"]) == 1 and r["closed"] == 1
    r = _push([_ev(1, "run_start")], [{"error": True}])
    assert len(r["fail"]) == 1
    r = _push([_ev(1, "run_start")], [{"error": False}, {"error": False}, {"line": _ev(2)}, {"error": False}, {"error": False}])
    assert r["fail"] == [] and r["changed"] == [[1, 2]]


def test_a_stream_that_throws_or_pushes_garbage_leaves_the_page_following_by_re_read():
    assert len(_push([_ev(1, "run_start")], [], throws=True)["fail"]) == 1
    assert len(_push([_ev(1, "run_start")], [{"line": "{not json"}])["fail"]) == 1


def test_the_page_follows_by_push_when_the_source_has_a_stream_else_by_the_timer_and_says_which():
    src = PAGE_SRC()
    assert "core.followPush(" in src and "src.stream" in src
    assert "setTimeout(tick" in src
    assert "pushed" in src and "Rereading the record every" in src
    assert "setInterval(() => { if (S.replay" in src   # the totals bar keeps counting between events


def test_no_control_in_the_page_sets_either_interval():
    static = PAGE_SRC()
    static = static[: static.index("<script>")]
    assert not re.search(r"reread|re-read|watch_seconds|interval", static, re.I)
