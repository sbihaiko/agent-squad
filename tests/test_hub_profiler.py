"""The hub's Profiler tab (#1743): the loop as a hot-spot tree, with its own switch.

The fold from process-table readings to the tree is a pure module beside the hub (ADR-0137), so its tests feed readings in
and read no real process table. The route is tested over HTTP: off is the default and reads nothing, a switch write is
audited, and a tailnet write is refused without the owner's grant (ADR-0162). The page's renderer runs in node.
"""
import http.client
import importlib.util
import json
import pathlib
import re
import shutil
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
HUB_PY = SCRIPTS / "hub.py"
TN_HOST = "box.tail1.ts.net"
S = "/Users/o/.claude/skills/squad-goal/scripts"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def prof():
    return load("hub_profiler_under_test", SCRIPTS / "hub_profiler.py")


# ---------------------------------------------------------------- the fold (pure)
# One reading is {pid: (ppid, cpu seconds, command)}.
FIRST = {
    1: (0, 9.0, "/sbin/launchd"),
    10: (1, 5.0, f"/bin/bash {S}/watchd.sh agent-squad"),
    11: (10, 2.0, f"bash {S}/watch.sh"),
    20: (1, 4.0, "bash /var/folders/ab/cd/T/tmp.Xy12/watch.sh"),
    30: (1, 1.0, "/Applications/Foo Bar.app/Contents/MacOS/Foo Bar --type=gpu"),
}
SECOND = {
    1: (0, 9.0, "/sbin/launchd"),
    10: (1, 5.0, f"/bin/bash {S}/watchd.sh agent-squad"),
    11: (10, 3.0, f"bash {S}/watch.sh"),
    12: (11, 0.5, "sed -n 1p /x/y"),                      # a short-lived helper: it is its driver's own time
    13: (11, 0.25, f"python3 {S}/janitor.py"),
    20: (1, 6.0, "bash /var/folders/ab/cd/T/tmp.Xy12/watch.sh"),
    21: (20, 0.0, "sleep 1"),
    30: (1, 1.5, "/Applications/Foo Bar.app/Contents/MacOS/Foo Bar --type=gpu"),
}


def node(tree, *names):
    for name in names:
        tree = next(c for c in tree["children"] if c["name"] == name)
    return tree


def test_a_process_whose_ancestry_names_a_driver_goes_under_that_driver(prof):
    tree = prof.tree(prof.fold(FIRST, SECOND))
    watch = node(tree, "watchd.sh", "watch.sh")
    assert watch["kind"] == "driver"
    assert watch["self_s"] == pytest.approx(1.5)          # its own second, plus the helper it started
    assert node(watch, "janitor.py")["self_s"] == pytest.approx(0.25)
    assert watch["total_s"] == pytest.approx(1.75)
    assert node(tree, "watchd.sh")["self_s"] == 0 and node(tree, "watchd.sh")["total_s"] == pytest.approx(1.75)


def test_a_short_lived_process_seen_once_counts_as_one_event(prof):
    tree = prof.tree(prof.fold(FIRST, SECOND))
    watch = node(tree, "watchd.sh", "watch.sh")
    assert watch["events"] == 1 and watch["mean_s"] == pytest.approx(1.5)
    assert node(watch, "janitor.py")["events"] == 1
    assert tree["events"] == 0 and tree["total_events"] == 3


def test_the_first_reading_alone_is_a_baseline_with_no_event(prof):
    tree = prof.tree(prof.fold(FIRST, FIRST))
    assert tree["total_s"] == 0 and tree["total_events"] == 0 and tree["children"] == []


def test_an_orphan_under_a_temp_folder_is_named(prof):
    tree = prof.tree(prof.fold(FIRST, SECOND))
    orphan = node(tree, "orphan watch.sh (temp folder)")
    assert orphan["kind"] == "orphan"
    assert orphan["self_s"] == pytest.approx(2.0) and orphan["events"] == 1


def test_the_same_script_under_a_live_parent_is_no_orphan(prof):
    a = {5: (1, 0.0, f"bash {S}/test-watch-cycle.sh"), 6: (5, 1.0, "bash /private/tmp/x/watch.sh")}
    b = {5: (1, 0.0, f"bash {S}/test-watch-cycle.sh"), 6: (5, 2.0, "bash /private/tmp/x/watch.sh")}
    tree = prof.tree(prof.fold(a, b))
    assert node(tree, "test-watch-cycle.sh")["kind"] == "suite"
    assert node(tree, "test-watch-cycle.sh", "watch.sh")["self_s"] == pytest.approx(1.0)


def test_a_tree_that_names_no_loop_part_goes_under_other(prof):
    tree = prof.tree(prof.fold(FIRST, SECOND))
    assert node(tree, "other", "Foo Bar")["self_s"] == pytest.approx(0.5)
    assert node(tree, "other")["kind"] == "other"


@pytest.mark.parametrize("command,name,kind", [
    ("/x/_bundled/claude --output-format stream-json", "child session", "run"),
    ("/x/.venv/bin/python -m squad start --graph g.json", "engine", "run"),
    (f"bash {S}/gate.sh", "gate.sh", "gate"),
    (f"bash {S}/fast-gate.sh --tree /x", "fast-gate.sh", "gate"),
    ("/x/.venv/bin/python -m pytest -n auto", "pytest", "gate"),
    (f"bash {S}/test-janitor-ccdefer.sh", "test-janitor-ccdefer.sh", "suite"),
    (f"/bin/bash {S}/autofrontier.sh", "autofrontier.sh", "driver"),
    ("bash /private/tmp/t/fastloop.sh", "fastloop.sh", "driver"),
    ("vim notes.txt", None, None),
])
def test_a_command_is_named_by_what_it_runs(prof, command, name, kind):
    assert prof.label(command) == ((name, kind) if name else None)


def test_the_rows_sort_by_self_time_and_carry_their_share(prof):
    tree = prof.tree(prof.fold(FIRST, SECOND))
    selfs = [c["self_s"] for c in tree["children"]]
    assert selfs == sorted(selfs, reverse=True)
    assert tree["children"][0]["name"] == "orphan watch.sh (temp folder)"
    assert tree["total_s"] == pytest.approx(4.25) and tree["share"] == 1
    assert tree["children"][0]["share"] == pytest.approx(2.0 / 4.25, abs=1e-3)
    assert set(tree) == {"name", "kind", "self_s", "total_s", "share", "events", "total_events", "mean_s", "children"}


def test_a_reused_pid_is_a_new_process(prof):
    a = {7: (1, 50.0, f"bash {S}/watch.sh")}
    b = {7: (1, 0.5, f"bash {S}/watch.sh")}             # CPU time never goes back: this is another process
    watch = node(prof.tree(prof.fold(a, b)), "watch.sh")
    assert watch["self_s"] == pytest.approx(0.5) and watch["events"] == 1


def test_the_window_keeps_the_last_span_and_restarts_on_reset(prof):
    def reading(cpu, extra=()):
        return {9: (1, cpu, f"bash {S}/watch.sh"), **{p: (9, 0.0, "true") for p in extra}}
    w = prof.Window(span=10)
    w.reset(100.0)
    w.add(100.0, reading(1.0))
    assert w.tree()["total_s"] == 0 and w.window_s(100.0) == 0
    w.add(101.0, reading(2.0, extra=(50,)))
    w.add(102.0, reading(4.0, extra=(51,)))
    assert node(w.tree(), "watch.sh")["self_s"] == pytest.approx(3.0) and node(w.tree(), "watch.sh")["events"] == 2
    assert w.since == 100.0 and w.window_s(102.0) == pytest.approx(2.0)
    w.add(111.5, reading(4.5))                             # the 101 sample is now older than the span
    assert node(w.tree(), "watch.sh")["self_s"] == pytest.approx(2.5) and node(w.tree(), "watch.sh")["events"] == 1
    assert w.window_s(111.5) == pytest.approx(10.0)
    w.reset(200.0)
    assert w.tree()["children"] == [] and w.since == 200.0


# ---------------------------------------------------------------- the route
@pytest.fixture(scope="module")
def served(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("profiler")
    state = tmp / "st"
    mp = pytest.MonkeyPatch()
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("HOME", str(tmp)),
                 ("SG_CHECKOUT", str(tmp)), ("SG_HUB_DIR", str(tmp)), ("SG_STATE", str(state))):
        mp.setenv(k, v)
    for k in ("SG_STATE_BASE", "SG_SHARED", "SG_PROFILE"):
        mp.delenv(k, raising=False)
    gh = importlib.util.module_from_spec(importlib.util.spec_from_file_location("ghread", SCRIPTS / "ghread.py"))
    mp.setitem(sys.modules, "ghread", gh)
    gh.__spec__.loader.exec_module(gh)
    mod = load("hub_profiler_page", HUB_PY)
    state.mkdir(parents=True, exist_ok=True)
    tn = {"host": TN_HOST, "allow": ["alice@example.com"]}
    mod.tailnet_block = lambda checkout=None: dict(tn)
    mod.PROFILER_SECS = 0.01
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield mod, srv.server_port, tn, state
    srv.shutdown()
    srv.server_close()
    mp.undo()


class Table:
    """A process table the test owns: every read is counted, and each read gives the driver one more CPU second."""

    def __init__(self):
        self.reads = 0

    def __call__(self):
        self.reads += 1
        P = type("P", (), {})
        rows = {}
        for pid, (ppid, cpu, command) in {8: (1, float(self.reads), f"bash {S}/watch.sh")}.items():
            p = P()
            p.pid, p.ppid, p.cpu, p.command = pid, ppid, cpu, command
            rows[pid] = p
        return rows


@pytest.fixture
def hub(served, monkeypatch):
    mod, port, tn, state = served
    table = Table()
    monkeypatch.setattr(mod, "process_table", table)
    tn.pop("loop_write", None)
    yield mod, port, tn, state, table
    mod.profiler_set(False, "test", "loopback")
    wait(lambda: not mod.profiler_running())
    for name in ("profiler.json", "profiler-audit.jsonl"):
        (state / name).unlink(missing_ok=True)


def wait(done, secs=5.0):
    end = time.monotonic() + secs
    while not done():
        assert time.monotonic() < end, "the condition did not hold in time"
        time.sleep(0.01)


def ask(port, method, body=None, *, origin=None, host=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Host": host or f"127.0.0.1:{port}", "Content-Type": "application/json",
               "Origin": origin if origin is not None else f"http://127.0.0.1:{port}"}
    c.request(method, "/api/profiler", body=None if body is None else json.dumps(body), headers=headers)
    r = c.getresponse()
    data = json.loads(r.read() or b"{}")
    c.close()
    return r.status, data


def audit(state):
    p = state / "profiler-audit.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.is_file() else []


def as_tailnet(mod, monkeypatch):
    monkeypatch.setattr(mod, "access", lambda h, m: mod.Access(True, "", "alice@example.com", True))


def test_off_is_the_default_and_it_reads_no_process_table(hub):
    mod, port, _, _, table = hub
    code, v = ask(port, "GET")
    assert code == 200 and v["on"] is False and v["tree"] is None and v["since"] is None
    assert v["write"] is True
    time.sleep(0.05)
    assert table.reads == 0 and not mod.profiler_running()


def test_switching_on_starts_the_sampler_and_the_route_returns_the_tree(hub):
    mod, port, _, state, table = hub
    code, out = ask(port, "POST", {"on": True, "client": "desktop"})
    assert (code, out.get("on")) == (200, True)
    wait(lambda: table.reads >= 3)
    code, v = ask(port, "GET")
    assert code == 200 and v["on"] is True and v["since"] and v["window_s"] >= 0
    watch = next(c for c in v["tree"]["children"] if c["name"] == "watch.sh")
    assert watch["self_s"] >= 2 and watch["kind"] == "driver"
    assert [(a["action"], a["who"], a["from"], a["client"]) for a in audit(state)] == [("on", "owner", "loopback", "desktop")]


def test_switching_off_stops_the_sampler_and_is_audited(hub):
    mod, port, _, state, table = hub
    ask(port, "POST", {"on": True})
    wait(lambda: table.reads >= 2)
    code, out = ask(port, "POST", {"on": False})
    assert (code, out.get("on")) == (200, False)
    wait(lambda: not mod.profiler_running())
    seen = table.reads
    time.sleep(0.05)
    assert table.reads == seen
    assert [a["action"] for a in audit(state)] == ["on", "off"]
    assert ask(port, "GET")[1]["tree"] is None


def test_a_write_that_changes_nothing_writes_no_audit_line(hub):
    _, port, _, state, _ = hub
    assert ask(port, "POST", {"on": False})[0] == 200
    assert audit(state) == []


def test_the_switch_survives_a_hub_restart(hub):
    mod, port, _, state, table = hub
    (state / "profiler.json").write_text(json.dumps({"on": True, "since": 5.0}))
    assert not mod.profiler_running()
    mod.profiler_ensure()                                   # what the hub calls when it starts
    wait(lambda: table.reads >= 2)
    assert mod.profiler_running() and ask(port, "GET")[1]["on"] is True


def test_a_bad_body_is_refused_and_changes_nothing(hub):
    mod, port, _, state, table = hub
    for body in ({}, {"on": "yes"}, [True]):
        assert ask(port, "POST", body)[0] == 400
    assert audit(state) == [] and not mod.profiler_running() and table.reads == 0


def test_a_cross_site_write_is_refused_and_writes_nothing(hub):
    mod, port, _, state, table = hub
    code, out = ask(port, "POST", {"on": True}, origin="https://evil.example")
    assert code == 403 and out["err"] == "forbidden"
    assert audit(state) == [] and not mod.profiler_running() and table.reads == 0


def test_a_tailnet_write_is_refused_with_its_reason_by_default(hub, monkeypatch):
    mod, port, _, state, table = hub
    as_tailnet(mod, monkeypatch)
    code, out = ask(port, "POST", {"on": True}, origin=f"https://{TN_HOST}", host=TN_HOST)
    assert code == 403 and "SQUAD_TAILNET_LOOP_WRITE" in out["reason"]
    assert audit(state) == [] and not mod.profiler_running() and table.reads == 0
    _, v = ask(port, "GET", origin=f"https://{TN_HOST}", host=TN_HOST)
    assert v["write"] is False and "SQUAD_TAILNET_LOOP_WRITE" in v["reason"]


def test_a_tailnet_write_runs_when_the_owner_grants_it(hub, monkeypatch):
    mod, port, tn, state, _ = hub
    as_tailnet(mod, monkeypatch)
    tn["loop_write"] = True
    code, _ = ask(port, "POST", {"on": True}, origin=f"https://{TN_HOST}", host=TN_HOST)
    assert code == 200
    assert [(a["who"], a["from"]) for a in audit(state)] == [("alice@example.com", "tailnet")]


def test_the_loop_audit_holds_no_profiler_line(hub):
    """The loop reads its own state from the last line of its audit: a profiler change must never be that line."""
    _, port, _, state, _ = hub
    ask(port, "POST", {"on": True})
    assert not (state / "loop-audit.jsonl").exists()


# ---------------------------------------------------------------- the pages
def page():
    return HUB_PY.read_text()


def test_the_tab_stands_between_flow_and_settings_on_the_desktop_page(served):
    mod = served[0]
    top = mod.PAGE[mod.PAGE.index("<div class=tabs role=tablist>"):]
    top = top[:top.index("</div>")]
    assert re.findall(r"<button data-t=(\w+) role=tab>([^<]+)</button>", top) == [
        ("board", "Kanban"), ("runs", "Runs"), ("flow", "Flow"), ("prof", "Profiler"), ("set", "Settings")]
    assert mod.PAGE.index("<section id=tab-flow") < mod.PAGE.index("<section id=tab-prof") < mod.PAGE.index("<section id=tab-set")


def test_the_entry_stands_before_settings_on_the_mobile_page():
    """The phone has no Flow tab: its More menu holds the Lead Time chart and the link to Settings, so the entry stands
    between them. It names the desktop view before it navigates, as the Settings entry does (#1446)."""
    html = (SCRIPTS / "hub-mobile.html").read_text()
    menu = html[html.index('<div id="more-menu">'):]
    menu = menu[:menu.index("</div>")]
    assert re.findall(r">([^<>\n]+)</(?:button|a)>", menu) == ["Cost and usage", "Lead time", "Profiler", "Settings"]
    assert re.search(r'<a class="more-link" id="more-profiler" href="\./#profiler">Profiler</a>', menu)
    assert re.search(r'\$\("more-profiler"\)\.onclick\s*=\s*\(\)\s*=>\s*localStorage\.setItem\("squad-view","desktop"\)', html)


def test_the_page_polls_only_while_the_tab_shows_and_the_switch_is_on():
    src = page()
    assert re.search(r'fetch\("/api/profiler"\)', src)
    assert re.search(r'fetch\("/api/profiler",\{method:"POST"', src)
    assert re.search(r'^const profDue=\(tab,hidden,v\)=>tab==="prof"&&!hidden&&!!v&&v\.on;', src, re.M)


def run_js(expr):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    src = page()
    js = ("const esc=s=>String(s).replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'})[c]);\n"
          + src[src.index("// profiler:start"):src.index("// profiler:end")] + f"\nprocess.stdout.write(String({expr}));")
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=10)
    assert r.returncode == 0, r.stderr
    return r.stdout


def leaf(name, self_s, **kw):
    return {"name": name, "kind": "driver", "self_s": self_s, "total_s": self_s, "share": 0.25, "events": 4, "total_events": 4,
            "mean_s": self_s / 4, "children": [], **kw}


VIEW = {"on": True, "since": 1.0, "window_s": 125, "write": True, "reason": "",
        "tree": {"name": "loop", "kind": "root", "self_s": 0, "total_s": 8.0, "share": 1, "events": 0, "total_events": 12,
                 "mean_s": 0, "children": [
                     leaf("watchd.sh", 0.0, total_s=6.0, share=0.75, events=0, mean_s=0,
                          children=[leaf("watch.sh", 6.0, share=0.75, events=8, mean_s=0.75)]),
                     leaf("orphan watch.sh (temp folder)", 2.0, kind="orphan")]}}


def test_the_poll_rule_is_false_off_the_tab_or_with_the_switch_off():
    out = json.loads(run_js('JSON.stringify([profDue("prof",false,{on:true}),profDue("flow",false,{on:true}),'
                            'profDue("prof",true,{on:true}),profDue("prof",false,{on:false}),profDue("prof",false,null)])'))
    assert out == [True, False, False, False, False]


def test_the_view_draws_one_row_per_node_with_its_bar_and_columns():
    out = run_js(f"profHtml({json.dumps(VIEW)},new Set())")
    assert [h for h in re.findall(r"<th[^>]*>([^<]+)</th>", out)] == ["Hot spot", "Self time", "Average time", "Events"]
    rows = re.findall(r"<tr data-p=\"([^\"]*)\"", out)
    assert rows == ["loop", "loop/watchd.sh", "loop/orphan watch.sh (temp folder)"], "a closed node shows no child"
    assert re.search(r'data-p="loop/watchd\.sh".*?<i class=pbar style="width:75\.0%">', out, re.S)
    assert "75.0%" in out and "6.00 s" in out and "125 s" in out


def test_a_node_opens_to_its_children_and_closes_again():
    out = run_js(f"profHtml({json.dumps(VIEW)},new Set(['loop/watchd.sh']))")
    rows = re.findall(r"<tr data-p=\"([^\"]*)\"", out)
    assert rows == ["loop", "loop/watchd.sh", "loop/watchd.sh/watch.sh", "loop/orphan watch.sh (temp folder)"]
    assert re.search(r'data-p="loop/watchd\.sh" aria-expanded=true', out)
    assert re.search(r'data-p="loop/watchd\.sh/watch\.sh" aria-level=3', out)
    closed = run_js(f"profHtml({json.dumps(VIEW)},new Set(['loop']))")
    assert re.findall(r"<tr data-p=\"([^\"]*)\"", closed) == ["loop"], "the root closes too"


def test_the_view_states_the_switch_and_an_off_profiler_draws_no_tree():
    on = run_js(f"profHtml({json.dumps(VIEW)},new Set())")
    assert re.search(r'role=switch aria-label="Profiler" aria-checked="true" data-prof', on)
    off = run_js(f"profHtml({json.dumps({**VIEW, 'on': False, 'tree': None})},new Set())")
    assert re.search(r'aria-checked="false"', off) and "<table" not in off and "The profiler is off" in off
    ro = run_js(f"profHtml({json.dumps({**VIEW, 'write': False, 'reason': 'read-only from the tailnet'})},new Set())")
    assert re.search(r'data-prof disabled[^>]*title="read-only from the tailnet"', ro)


# ---------------------------------------------------------------- behind the many-repository hub
@pytest.fixture
def mux(tmp_path, monkeypatch):
    prof, co = tmp_path / "profiles", tmp_path / "co"
    prof.mkdir()
    co.mkdir()
    (prof / "alpha.sh").write_text(f': "${{SG_REPO:=x/alpha}}"; : "${{SG_OWNER:=x}}"; : "${{SG_PROJECT:=3}}"; : "${{SG_CHECKOUT:={co}}}"\n')
    monkeypatch.setenv("SG_PROFILES_DIR", str(prof))
    monkeypatch.setenv("SG_STATE_BASE", str(tmp_path / "state"))
    hubmux = load("hubmux_profiler_under_test", SCRIPTS / "hubmux.py")
    apps = hubmux.load_apps()
    mod = apps["alpha"]["mod"]
    mod.runs = lambda: []
    mod.PROFILER_SECS = 0.01
    table = Table()
    mod.process_table = table
    srv = hubmux.server(0, apps)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    yield mod, srv.server_address[1], table
    mod.profiler_set(False, "test", "loopback")
    wait(lambda: not mod.profiler_running())
    srv.shutdown()
    srv.server_close()


def test_a_profile_behind_the_many_repository_hub_takes_the_switch(mux):
    """The many-repository hub forwards a write by the name of its handler: a route it does not forward answers nothing."""
    mod, port, table = mux
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("POST", "/alpha/api/profiler", body=json.dumps({"on": True}),
              headers={"Host": f"127.0.0.1:{port}", "Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}"})
    r = c.getresponse()
    out = json.loads(r.read() or b"{}")
    assert (r.status, out.get("on")) == (200, True)
    wait(lambda: table.reads >= 2)
    c.request("GET", "/alpha/api/profiler", headers={"Host": f"127.0.0.1:{port}"})
    v = json.loads(c.getresponse().read())
    c.close()
    assert v["on"] is True and v["tree"]["name"] == "loop"
