"""The loop switch on the desktop hub (#923, ADR-0162): the Monitor card and Settings > Loop draw one view of `/api/loop`.

The route is tested over HTTP here, not only as functions: a loopback start writes one audit line, a cross-site write is
refused, and a tailnet write is refused with its reason unless `tailnet.loop_write` is on. The page's renderers are run in
node: the Monitor card and the Loop theme show the state, who changed it and when; the stop confirm lists every live run and gate;
the Access theme holds the tailnet rule. The Loop theme has no Save. Its first line states the state, its three Now tiles carry
the live counts, the health and the last change, and the health values come from the loop answer (#1280).
"""
import http.client
import importlib.util
import json
import os
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
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
GHREAD_PY = ROOT / "contrib/squad-goal/scripts/ghread.py"
TN_HOST = "box.tail1.ts.net"


@pytest.fixture
def hub(monkeypatch, tmp_path):
    state = tmp_path / "st"
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("HOME", str(tmp_path)),
                 ("SG_CHECKOUT", str(tmp_path)), ("SG_HUB_DIR", str(tmp_path)), ("SG_STATE", str(state))):
        monkeypatch.setenv(k, v)
    for k in ("SG_STATE_BASE", "SG_SHARED", "SG_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    gspec = importlib.util.spec_from_file_location("ghread", GHREAD_PY)
    gh = importlib.util.module_from_spec(gspec)
    monkeypatch.setitem(sys.modules, "ghread", gh)
    gspec.loader.exec_module(gh)
    spec = importlib.util.spec_from_file_location("hub_loop_page", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    (state / "auto").mkdir(parents=True, exist_ok=True)
    calls = []

    own_state = state

    def watchd(*a, state=None, checkout=None, profile=None):   # a start brings the supervisor up: this process stands for it
        calls.append(list(a))
        if a == ("start",):
            ((pathlib.Path(state) if state else own_state) / "watchd.pid").write_text(f"{os.getpid()}\n")
        return 0
    mod.loop_watchd = watchd
    tn = {"host": TN_HOST, "allow": ["alice@example.com"]}
    mod.tailnet_block = lambda checkout=None: dict(tn)
    real_source = mod.tailnet_source

    def stub(checkout=None):   # a test of the production seam calls `stub.real`, never the stub
        return dict(tn), str(tmp_path / ".env")
    stub.real = real_source
    mod.tailnet_source = stub
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield mod, srv.server_port, calls, tn, state
    srv.shutdown()
    srv.server_close()


def ask(port, method, body=None, *, origin=None, host=None, path="/api/loop"):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Host": host or f"127.0.0.1:{port}", "Content-Type": "application/json",
               "Origin": origin if origin is not None else f"http://127.0.0.1:{port}"}
    c.request(method, path, body=None if body is None else json.dumps(body), headers=headers)
    r = c.getresponse()
    data = json.loads(r.read() or b"{}")
    c.close()
    return r.status, data


def get_json(port, path, *, host=None, origin=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Host": host or f"127.0.0.1:{port}"}
    if origin is not None:
        headers["Origin"] = origin
    c.request("GET", path, headers=headers)
    r = c.getresponse()
    data = json.loads(r.read() or b"{}")
    c.close()
    return r.status, data


def audit(state):
    p = state / "loop-audit.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.is_file() else []


def as_tailnet(mod, monkeypatch):
    monkeypatch.setattr(mod, "access", lambda h, m: mod.Access(True, "", "alice@example.com", True))


def test_a_loopback_get_reads_the_view_with_the_access_rules(hub):
    _, port, _, _, _ = hub
    code, v = ask(port, "GET")
    assert code == 200
    assert {"state", "who", "when", "live", "log", "write", "reason"} <= set(v)
    assert v["write"] is True and v["tailnet"] is False
    assert v["access"] == {"loop_write": False, "terminals_send": False}   # kept on the loop answer for every existing client (#1285 adds /api/access beside it)
    assert v["actions"] == ["start"]


def test_the_access_route_answers_on_its_own(hub):
    """#1285: the reach and the two tailnet rules have their own read, with the reach and the file that holds the rules."""
    mod, port, _, _, _ = hub
    code, v = get_json(port, "/api/access")
    assert code == 200
    assert v["tailnet"] is False and v["reach"] is True and v["login"] == ""
    assert v["access"] == {"loop_write": False, "terminals_send": False}
    assert v["file"].endswith(".env"), v["file"]


def test_the_access_file_names_the_env_when_only_the_env_holds_the_block(hub):
    """#1285: a project file with no `tailnet` block does not win the named home; the `.env` that holds the block does.
    This calls the production `tailnet_source` (the fixture stub carries it as `.real`), so a wrong path choice is caught."""
    mod, _, _, _, state = hub
    checkout = state.parent
    (checkout / "docs" / "squad").mkdir(parents=True, exist_ok=True)
    (checkout / "docs" / "squad" / "defaults.yaml").write_text((ROOT / "docs" / "squad" / "defaults.yaml").read_text())
    (checkout / ".env").write_text("SQUAD_TAILNET_HOST=box.tail1.ts.net\n")
    tn, path = mod.tailnet_source.real()
    assert tn.get("host") == "box.tail1.ts.net", tn
    assert path == str(checkout / ".env"), path


def test_the_access_file_names_the_tracked_project_file_when_it_holds_the_block(hub):
    """#1285: a tracked project file that itself holds the `tailnet` block wins the named home over the personal `.env`."""
    mod, _, _, _, state = hub
    checkout = state.parent
    (checkout / "docs" / "squad").mkdir(parents=True, exist_ok=True)
    proj = checkout / "docs" / "squad" / "defaults.yaml"
    proj.write_text("tailnet:\n  host: proj.tail1.ts.net\n")
    (checkout / ".env").write_text("SQUAD_TAILNET_ALLOW=alice@example.com\n")
    tn, path = mod.tailnet_source.real()
    assert tn.get("host") == "proj.tail1.ts.net", tn
    assert tn.get("allow") == ["alice@example.com"], tn
    assert path == str(proj.resolve()), path


def test_the_access_route_needs_no_loop_answer(hub, monkeypatch):
    """The Access theme no longer depends on the loop answer for its reach fact (#1285)."""
    mod, port, _, _, _ = hub

    def no_loop(*a, **k): raise RuntimeError("the loop answer is not read here")
    monkeypatch.setattr(mod, "loop_audit_last", no_loop)
    monkeypatch.setattr(mod, "loop_state", no_loop)
    code, v = get_json(port, "/api/access")
    assert code == 200 and v["reach"] is True and v["tailnet"] is False


def test_a_refused_access_read_marks_the_rules_withheld(hub):
    """#1285: a refused read names no rule, login or file, and says so, so the page never reads it as an empty rule.
    The GET seam refuses such a request before any route, so the view's own answer is tested here."""
    mod, _, _, _, _ = hub
    v = mod.access_view(mod.Access(False, "not on the allow list", "", True))
    assert v["reach"] is False and v["withheld"] is True and v["reason"] == "not on the allow list"
    assert v["access"] == {} and v["file"] == "" and v["login"] == ""


def test_a_tailnet_read_states_the_reach_and_the_rules(hub, monkeypatch):
    mod, port, _, tn, _ = hub
    as_tailnet(mod, monkeypatch)
    tn["terminals_send"] = True
    code, v = get_json(port, "/api/access", origin=f"https://{TN_HOST}", host=TN_HOST)
    assert code == 200 and v["tailnet"] is True and v["reach"] is True and v["login"] == "alice@example.com"
    assert v["access"] == {"loop_write": False, "terminals_send": True}


@pytest.mark.parametrize("state,actions", [("off", ["start"]), ("on", ["drain", "stop-now"]), ("draining", ["start", "stop-now"]), ("starting", [])])
def test_the_view_names_the_actions_each_state_allows(hub, monkeypatch, state, actions):
    """The rule that picks the live buttons is the hub's, so the phone page holds no state rule (ADR-0137)."""
    mod, port, _, _, _ = hub
    monkeypatch.setattr(mod, "loop_state", lambda last, now=None, **kw: state)
    assert ask(port, "GET")[1]["actions"] == actions


def test_a_read_only_view_allows_no_action(hub, monkeypatch):
    mod, port, _, _, _ = hub
    as_tailnet(mod, monkeypatch)
    assert ask(port, "GET", origin=f"https://{TN_HOST}", host=TN_HOST)[1]["actions"] == []


def test_a_loopback_start_runs_and_writes_one_audit_line(hub):
    _, port, calls, _, state = hub
    code, out = ask(port, "POST", {"action": "start"})
    assert (code, out.get("ok")) == (200, True)
    assert calls == [["start"]]
    assert [(a["action"], a["who"], a["from"]) for a in audit(state)] == [("start", "owner", "loopback")]
    _, v = ask(port, "GET")
    assert v["state"] == "on" and v["who"] == "owner" and v["when"]


def test_a_cross_site_write_is_refused_and_writes_nothing(hub):
    _, port, calls, _, state = hub
    code, out = ask(port, "POST", {"action": "start"}, origin="https://evil.example")
    assert code == 403 and out["err"] == "forbidden"
    assert calls == [] and audit(state) == []


def test_a_tailnet_write_is_refused_with_its_reason_by_default(hub, monkeypatch):
    mod, port, calls, _, state = hub
    as_tailnet(mod, monkeypatch)
    code, out = ask(port, "POST", {"action": "start"}, origin=f"https://{TN_HOST}", host=TN_HOST)
    assert code == 403 and "SQUAD_TAILNET_LOOP_WRITE" in out["reason"]
    assert calls == [] and audit(state) == []
    _, v = ask(port, "GET", origin=f"https://{TN_HOST}", host=TN_HOST)
    assert v["write"] is False and "SQUAD_TAILNET_LOOP_WRITE" in v["reason"] and v["tailnet"] is True


def test_a_tailnet_write_runs_when_the_owner_grants_it(hub, monkeypatch):
    mod, port, calls, tn, state = hub
    as_tailnet(mod, monkeypatch)
    tn["loop_write"] = True
    code, _ = ask(port, "POST", {"action": "start"}, origin=f"https://{TN_HOST}", host=TN_HOST)
    assert code == 200 and calls == [["start"]]
    assert [(a["who"], a["from"]) for a in audit(state)] == [("alice@example.com", "tailnet")]


def test_the_shipped_default_keeps_tailnet_loop_writes_off():
    text = (ROOT / "config/agent-defaults.yaml").read_text()
    assert re.search(r"^\s*loop_write:\s*false\b", text, re.M)


# ---- the page ----

def page():
    return HUB_PY.read_text()


def section(src, sid):
    m = re.search(rf"<details id={sid} class=sp[^>]*>(.*?)</details>", src, re.S)
    assert m, f"Settings has no section #{sid}"
    return m.group(0)


def test_settings_holds_a_loop_theme_and_an_access_theme():
    src = page()
    tab = src.split("<section id=tab-set", 1)[1].split("</section>", 1)[0]
    assert "<summary>Loop<" in section(tab, "lop")
    assert "<summary>Access<" in section(tab, "acc")


def test_the_loop_theme_has_no_save():
    src = page()
    assert not re.search(r"save", section(src, "lop"), re.I)
    fn = re.search(r"const loopSetHtml=.*?;\n", src, re.S)
    assert fn and not re.search(r"save", fn.group(0), re.I)


def test_the_page_draws_no_dead_start_button():
    src = page()
    assert "The hub has no route that starts the loop" not in src
    assert re.search(r'fetch\("/api/loop"\)', src)
    assert re.search(r'fetch\("/api/loop",\{method:"POST"', src)


def run_js(expr):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    src = page()
    start = src.index("// loop-switch:start")
    end = src.index("// loop-switch:end")
    js = ("const esc=s=>String(s).replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'})[c]);\n"
          + src[start:end] + f"\nprocess.stdout.write(String({expr}));")
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=10)
    assert r.returncode == 0, r.stderr
    return r.stdout


VIEW = {"state": "on", "who": "owner", "when": "2026-10-31T16:02:00-03:00", "write": True, "reason": "",
        "live": {"runs": ["/x/runs/run-20261031-1", "run-20261031-2"], "gates": ["gate pid 4242"]},
        "health": {"live_runs": 2, "live_gates": 1, "cycle_age": 45, "starting_left": None},
        "log": [{"at": "2026-10-31T16:02:00-03:00", "who": "owner", "from": "loopback", "action": "start"}]}

# The answer /api/access gives the Access theme (#1285): the reach, the two tailnet rules and the file they live in.
ACCESS = {"tailnet": False, "reach": True, "access": {"loop_write": False, "terminals_send": True}, "file": "/p/.env"}


def js_view(**over):
    return json.dumps({**VIEW, **over})


def test_the_loop_answer_carries_the_four_health_keys(hub, monkeypatch):
    """The loop answer carries the health object, and its values are measured from the state: the cycle age and the live
    counts the same answer reports, never four empty keys (#1280)."""
    mod, port, _, _, state = hub
    (state / "cycle.log").write_text(time.strftime("%H:%M:%S") + " CYCLE 30s\n")
    monkeypatch.setattr(mod, "loop_live", lambda *a, **kw: {"runs": ["run-1"], "gates": ["gate pid 1"]})
    _, v = ask(port, "GET")
    health = v["health"]
    assert {"cycle_age", "starting_left", "live_runs", "live_gates"} <= set(health), health
    assert isinstance(health["cycle_age"], int) and health["cycle_age"] >= 0, health
    assert health["live_runs"] == len(v["live"]["runs"]) == 1, health
    assert health["live_gates"] == len(v["live"]["gates"]) == 1, health


@pytest.mark.heavy   # four node starts: 0.3 s here, past the 2 s line on the CI runner's cold disk (#2474)
def test_the_theme_opens_on_its_state_line():
    """The theme's first line states on, off, draining or starting, before any other fact (#1280)."""
    for state in ("on", "off", "draining", "starting"):
        out = run_js(f"loopLogHtml({js_view(state=state)})")
        m = re.match(r'<div class="lstate (\w+)">Loop \w+</div>', out)
        assert m and m.group(1) == state, out[:140]


def test_a_draining_loop_turns_the_switch_itself_warn_coloured():
    """While the supervisor winds down the switch reads the warn colour, not the green of a loop that still dispatches."""
    for state in ("on", "off", "draining", "starting"):
        out = run_js(f"loopSw({js_view(state=state)})")
        assert (" dra" in out) is (state == "draining"), f"{state}: {out}"
    src = page()
    for panel in ("#lopb", "#inst"):
        rule = re.search(rf"{panel} \.lsw\.dra\[aria-checked=true\]\{{([^}}]*)\}}", src)
        assert rule and "var(--warn)" in rule.group(1), f"{panel} paints a draining switch warn"


def test_the_live_tile_states_the_run_and_gate_counts_in_every_state():
    """A stop never touches work the owner could not see: the counts show in every state, not only while draining (#1280)."""
    for state in ("on", "off", "draining", "starting"):
        out = run_js(f"loopLogHtml({js_view(state=state)})")
        assert "2 runs · 1 gate" in out, f"{state}: {out}"


def test_the_last_change_tile_names_the_action_who_from_and_when():
    out = run_js(f"loopLogHtml({js_view()})")
    assert "start · owner · loopback · 2026-10-31 16:02" in out, out
    empty = run_js(f"loopLogHtml({js_view(log=[], who='', when='')})")
    assert "none recorded" in empty, empty
    lost = run_js(f"loopLogHtml({js_view(log=[], who='owner', when='2026-10-31T16:02:00-03:00')})")
    assert "none recorded" in lost and "16:02" not in lost, "a truncated log still names no action or source, so it says none recorded"


def test_the_health_tile_reads_the_hubs_values_and_formats_only_the_span():
    """The hub hands the cycle age and the seconds left; the page writes the span and names the state (ADR-0137, #1280)."""
    age = run_js(f"loopLogHtml({js_view()})")
    assert re.search(r'class="tile ok".*?cycle 45s ago', age, re.S), age
    assert "late" not in age and "in 45s" not in age, "the page computed a value the hub did not hand it"
    starting = run_js(f"loopLogHtml({js_view(health={'live_runs': 2, 'live_gates': 1, 'cycle_age': 45, 'starting_left': 62})})")
    assert re.search(r'class="tile warn".*?starting, 62s left', starting, re.S), starting
    failed = run_js(f"loopLogHtml({js_view(state='off', failed='watchd did not start')})")
    assert re.search(r'class="tile err".*?last start failed: watchd did not start', failed, re.S), failed


def test_the_starting_switch_states_the_seconds_left():
    """A disabled switch is never a mystery: the seconds left in the starting window sit by it (#1280)."""
    out = run_js(f"loopSetHtml({js_view(state='starting', health={'live_runs': 2, 'live_gates': 1, 'cycle_age': None, 'starting_left': 62})})")
    assert "Loop starting · 62s left" in out and " disabled" in out, out
    assert re.search(r'role=switch[^>]*title="the loop is starting"', out), "the disabled starting switch carries its tooltip"
    assert run_js(f"loopTip({js_view(state='starting')})") == "the loop is starting"


def test_a_failed_start_offers_try_again_and_it_posts_a_start():
    out = run_js(f"loopSetHtml({js_view(state='off', failed='watchd did not start')})")
    assert re.search(r"<div class=lerr role=alert>The last start failed: watchd did not start", out), out
    assert re.search(r"<button[^>]*data-loop[^>]*>Try again</button>", out), out
    assert re.search(r'loopV\.state==="off"\)\{loopPost\(\{action:"start"\}\)', page()), "the retry does not post a start"


def test_the_drain_rule_is_stated_once_in_the_confirm_dialog():
    """The paragraph above the switch leaves, and the tooltip names the action only: the rule lives in the confirm (#1280)."""
    assert run_js(f"loopTip({js_view(state='on')})") == "turn off"
    assert run_js(f"loopTip({js_view(state='draining')})") == "turn off"
    assert run_js(f"loopTip({js_view(state='off')})") == "turn on"
    for rendered in (run_js(f"loopLogHtml({js_view()})"), run_js(f"loopSetHtml({js_view()})")):
        assert "after the live runs and gates finish" not in rendered and "No new dispatch" not in rendered, rendered
    assert "No new dispatch" in run_js(f"loopConfirmHtml({js_view()})")


def test_the_rule_in_access_is_a_link_that_opens_the_access_theme():
    out = run_js(f"loopSetHtml({js_view(write=False, reason='read-only from the tailnet')})")
    assert re.search(r'<a href="#set" data-sp=acc>Access</a>', out), out


def test_changes_is_a_disclosure_closed_by_default_with_its_count():
    out = run_js(f"loopLogHtml({js_view()})")
    assert re.search(r"<details class=aud><summary>Changes <span class=m>1</span></summary>", out), out
    assert "<details class=aud open" not in out


def test_the_monitor_carries_the_one_loop_switch_and_no_loop_tile():
    """The owner moved the switch into the Monitor: one switch there, no Loop tile, and the panel carries no button."""
    src = page()
    card = src.split("<details id=inst", 1)[1].split("</details>", 1)[0]
    assert "id=iloop" in card, "the Monitor carries the loop slot"
    assert src.count('{k:"Loop",text:') == 0, "the Monitor carries no Loop tile"
    panel = src.split("<details id=lop", 1)[1].split("</details>", 1)[0]
    assert "role=switch" not in panel, "the Loop panel carries no second switch"


def test_the_loop_switch_shows_the_state_and_the_log_keeps_who_and_when():
    """Owner: the switch is one key, so the state sits by it and every who and when stays in the change log."""
    out = run_js(f"loopSetHtml({js_view()})")
    assert "Loop on" in out and "owner" not in out and "16:02" not in out
    log = run_js(f"loopLogHtml({js_view()})")
    assert "owner" in log and "16:02" in log and "loopback" in log and "start" in log


def test_the_loop_theme_shows_the_read_only_reason():
    out = run_js(f"loopSetHtml({js_view(write=False, reason='read-only from the tailnet: SQUAD_TAILNET_LOOP_WRITE is not on')})")
    assert "SQUAD_TAILNET_LOOP_WRITE" in out and "disabled" in out
    assert re.search(r'role=switch[^>]*title="read-only from the tailnet: SQUAD_TAILNET_LOOP_WRITE is not on"', out), \
        "the disabled read-only switch carries its reason as a tooltip"


def test_a_failed_start_is_shown_in_the_loop_theme_and_the_log():
    """A start that did not hold reads off with its failure shown, never a disabled Starting... that hides it (#923 review)."""
    log = [{"at": "2026-10-31T16:02:00-03:00", "who": "owner", "from": "loopback", "action": "start", "failed": "watchd did not start"}]
    out = run_js(f"loopSetHtml({js_view(state='off', failed='watchd did not start', log=log)})")
    assert "watchd did not start" in out and "Loop off" in out and "disabled" not in out
    assert "watchd did not start" in run_js(f"loopLogHtml({js_view(state='off', failed='watchd did not start', log=log)})")


def test_the_stop_confirm_lists_every_live_run_and_gate_and_drains_by_default():
    out = run_js(f"loopConfirmHtml({js_view()})")
    for item in ("run-20261031-1", "run-20261031-2", "gate pid 4242"):
        assert item in out
    drain = re.search(r"<input[^>]*value=drain[^>]*>", out)
    assert drain and "checked" in drain.group(0)
    assert re.search(r"<input[^>]*value=stop-now", out)


def test_a_draining_loop_offers_stop_now_only():
    out = run_js(f"loopConfirmHtml({js_view(state='draining')})")
    assert "value=drain" not in out and re.search(r"<input[^>]*value=stop-now[^>]*checked", out)


def test_the_access_theme_states_three_toned_tiles_of_allowed_or_refused():
    """#1285: three Now tiles, each with one of the hub's tones, and every value is allowed or refused. The setting's name
    sits in the tile's hover, never in the value cell, so no environment variable name is a value."""
    out = run_js(f"accHtml({json.dumps(ACCESS)})")
    tiles = re.findall(r'<div class="tile (\w+)"[^>]*><span class=tk>(.*?)</span><span class=tv>(.*?)</span></div>', out)
    assert [k for _, k, _ in tiles] == ["Reached", "Loop write from the tailnet", "Terminal send from the tailnet"], out
    assert [v for _, _, v in tiles] == ["allowed", "refused", "allowed"], out
    assert [t for t, _, _ in tiles] == ["ok", "err", "ok"], out
    assert all(v in ("allowed", "refused") for _, _, v in tiles)
    assert 'title="SQUAD_TAILNET_LOOP_WRITE (tailnet.loop_write)"' in out, "the loop write tile's hover names its setting"
    assert 'title="SQUAD_TAILNET_TERMINALS_SEND (tailnet.terminals_send)"' in out, "the terminal send tile's hover names its setting"
    assert 'title="the request from this machine (loopback)"' in out, "the reached tile's hover names the route"


def test_the_access_theme_names_the_file_it_reads_in_one_line():
    out = run_js(f"accHtml({json.dumps(ACCESS)})")
    assert out.count("Read from <code>/p/.env</code>.") == 1, out


def test_the_access_theme_escapes_the_file_path_once():
    """A path with markup characters renders as text, and never twice escaped (#1285)."""
    out = run_js(f"accHtml({json.dumps({**ACCESS, 'file': '/a&b/<c>/.env'})})")
    assert "<code>/a&amp;b/&lt;c&gt;/.env</code>" in out, out
    assert "&amp;amp;" not in out and "<c>" not in out, out


def test_the_access_reach_states_allowed_or_refused_and_its_route():
    """A reach the seam refused reads refused; the route and the refusal reason sit in the hover, never the value (#1285)."""
    out = run_js(f"accHtml({json.dumps({**ACCESS, 'reach': False, 'tailnet': True, 'reason': 'not on the list'})})")
    assert re.search(r'<div class="tile err"[^>]*><span class=tk>Reached</span><span class=tv>refused</span>', out), out
    assert 'title="the request over the tailnet, through the hub was refused: not on the list"' in out, out


def test_a_withheld_read_shows_neutral_values_and_never_refused_for_a_rule():
    """A refused viewer never read the rules, so the two rule tiles show a neutral withheld value, never "refused", which would
    claim a rule the page did not read. The reach tile keeps its own refused answer (#1285)."""
    out = run_js(f"accHtml({json.dumps({'tailnet': False, 'reach': False, 'withheld': True, 'access': {}, 'file': ''})})")
    tiles = re.findall(r'<div class="tile (\w+)"[^>]*><span class=tk>(.*?)</span><span class=tv>(.*?)</span></div>', out)
    assert [(k, v) for _, k, v in tiles] == [("Reached", "refused"), ("Loop write from the tailnet", "withheld"),
                                             ("Terminal send from the tailnet", "withheld")], out
    assert [t for t, _, _ in tiles][1:] == ["neutral", "neutral"], out
    assert "SQUAD_TAILNET_LOOP_WRITE" in out and "SQUAD_TAILNET_TERMINALS_SEND" in out, "the hovers keep the setting names"
    assert "Read from" not in out, out


def test_a_genuinely_refused_rule_still_reads_refused():
    out = run_js(f"accHtml({json.dumps(ACCESS)})")
    assert re.search(r"<span class=tk>Loop write from the tailnet</span><span class=tv>refused</span>", out), out


def test_a_refused_http_read_of_the_access_route_is_rejected_before_the_view_runs(hub, monkeypatch):
    """The wire route is gated: a request the seam refuses gets 403 and the view never runs, so no rule, login or file
    path leaves the hub (#1285)."""
    mod, port, _, _, _ = hub
    monkeypatch.setattr(mod, "access", lambda h, m: mod.Access(False, "not on the allow list", "", True))
    ran = []
    monkeypatch.setattr(mod, "access_view", lambda a: ran.append(a) or {})
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", "/api/access", headers={"Host": TN_HOST, "Origin": f"https://{TN_HOST}"})
    r = c.getresponse()
    body = r.read().decode()
    c.close()
    assert r.status == 403 and "not on the allow list" in body, (r.status, body)
    assert ran == [] and ".env" not in body and "alice" not in body


def run_draws(steps):
    """Run each drawing step in order (`acc` sets the access answer, `loop` the loop answer) and read back both reach
    lines after every step (#1285)."""
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    src = page()
    body = "const out=[];"
    for kind, v in steps:
        body += (f"accV={json.dumps(v)};accDraw();" if kind == "acc" else f"loopV={json.dumps(v)};loopDraw();")
        body += "out.push([$('#accnav').textContent,$('#accsr').textContent]);"
    js = ("const esc=s=>String(s).replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'})[c]);\n"
          "const STORE={};const $=s=>STORE[s]||(STORE[s]={textContent:'',innerHTML:'',dataset:{}});\n"
          + src[src.index("// loop-switch:start"):src.index("async function loopPoll(")]
          + "\n" + body + "process.stdout.write(JSON.stringify(out));")
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=10)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_the_access_chip_and_nav_line_follow_the_access_answer_not_the_loop():
    """#1285: the theme's status chip and the nav's small line follow the access answer. A loop answer that disagrees,
    drawn before and after, changes neither line, so a later reintroduction of the coupling in loopDraw fails here."""
    got = run_draws([
        ("acc", {**ACCESS, "tailnet": False}),
        ("loop", {**VIEW, "tailnet": True}),        # the loop answer disagrees and must write nothing
        ("acc", {**ACCESS, "tailnet": True}),
        ("loop", {**VIEW, "tailnet": False}),       # and again, the other way
    ])
    assert got == [["loopback", "loopback"], ["loopback", "loopback"], ["tailnet", "tailnet"], ["tailnet", "tailnet"]], got
    assert "loopV.tailnet" not in page(), "no reach fact reads the loop answer"
    assert re.search(r'fetch\("/api/access"\)', page())


def test_the_switch_runs_watchd_for_its_own_profile_not_the_hub_cwd(monkeypatch, tmp_path):
    """The mux serves many profiles from one process and pins each profile's env only while it imports hub.py.
    A switch request later must still drive that profile's loop: watchd gets the profile, its state and its checkout."""
    state = tmp_path / "st"
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("HOME", str(tmp_path)),
                 ("SG_CHECKOUT", str(tmp_path)), ("SG_HUB_DIR", str(tmp_path)), ("SG_STATE", str(state)), ("SG_PROFILE", "pinned")):
        monkeypatch.setenv(k, v)
    gspec = importlib.util.spec_from_file_location("ghread", GHREAD_PY)
    gh = importlib.util.module_from_spec(gspec)
    monkeypatch.setitem(sys.modules, "ghread", gh)
    gspec.loader.exec_module(gh)
    spec = importlib.util.spec_from_file_location("hub_loop_env", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for k in ("SG_PROFILE", "SG_STATE", "SG_CHECKOUT"):   # the mux restores the shell's env after the import
        monkeypatch.delenv(k, raising=False)
    seen = {}
    monkeypatch.setattr(mod.subprocess, "run", lambda argv, **kw: seen.update(argv=argv, **kw) or subprocess.CompletedProcess(argv, 0))
    assert mod.loop_watchd("start") == 0
    assert seen["argv"][-1] == "start"
    assert seen["env"]["SG_PROFILE"] == "pinned" and seen["env"]["SG_STATE"] == str(state)
    assert seen["cwd"] == str(tmp_path)


def _inst_block(src):
    """The Monitor's CSS block: the phone serves this block alone, so a Monitor style outside it never reaches the phone."""
    return src.split("/* INST-CSS{ */", 1)[1].split("/* }INST-CSS */", 1)[0]


def _switch_rules(src):
    """Every CSS rule that styles the loop switch, as (selector list, body), read from the page's one style block."""
    css = re.sub(r"/\*.*?\*/", "", src.split("<style>", 1)[1].split("</style>", 1)[0], flags=re.S)
    out = []
    for block, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        sels = [s.strip() for s in block.split(",")]
        if any(re.search(r"\.ls(w)?\b", s) for s in sels):
            out.append((sels, body))
    return out


def test_the_monitor_switch_is_styled_where_the_monitor_draws_it():
    """#1071 moved the switch into the Monitor and left its styles scoped to the Settings panel, so the Monitor drew a
    bare button: a white box with a loose dot (#1234). Every rule that styles #lopb's switch must name #iloop's too, or
    the two panels drift apart the moment one of them changes."""
    src = page()
    assert "id=iloop" in src.split("<details id=inst", 1)[1].split("</details>", 1)[0]
    rules = _switch_rules(src)
    assert rules, "the page styles the loop switch"
    inst = _inst_block(src)
    everywhere = {s for sels, _ in rules for s in sels}
    orphans, missing = [], []
    for sels, body in rules:
        for sel in sels:
            if not sel.startswith("#lopb ") or "laud" in sel:   # the change log belongs to the Settings panel alone
                continue
            twin = "#inst " + sel[len("#lopb "):]
            if twin not in everywhere:
                orphans.append(sel)
            elif body.strip() not in inst:
                missing.append(twin)
    assert not orphans, f"a switch style reaches only the Settings panel: {orphans}"
    assert not missing, f"a switch style sits outside the block the phone serves: {missing}"
    assert any("#inst .lsw" in sels for sels, _ in rules), "the Monitor's switch is styled"


def test_the_settings_panel_keeps_its_own_row_box():
    """The Monitor's compact row never leaks into Settings > Loop, which keeps the inset box."""
    src = page()
    assert re.search(r"#lopb \.lnow\{[^}]*border:1px solid", src), "the Settings row keeps its box"
    assert re.search(r"#inst \.lnow\{[^}]*border:0", src), "the Monitor row carries no box"


# ---- loops per project (SPEC 1596, rule 8; #1599) ----
# A project is one profile the seam lists, and each project keeps its own state directory: its supervisor pid, its drain
# mark, its live-run list, its change log, its board cache and its loop-bugs. The loop fact lists every one of them, so no
# page reads one project's state as every project's.

def _profile(dirpath, slug, repo, project):
    dirpath.mkdir(parents=True, exist_ok=True)
    (dirpath / f"{slug}.sh").write_text(
        f': "${{SG_REPO:={repo}}}"\n: "${{SG_OWNER:={repo.split("/")[0]}}}"\n'
        f': "${{SG_PROJECT:={project}}}"\n: "${{SG_CHECKOUT:=$HOME/src/{slug}}}"\n')


@pytest.fixture
def farm(monkeypatch, tmp_path):
    """A machine with two projects, `a` and `b`: `a` is the profile in force and is off; `b` runs, drained last from the
    phone, landed a branch, and holds one open loop-bug."""
    base, prof = tmp_path / "base", tmp_path / "profiles"
    _profile(prof, "a", "x/a", 1)
    _profile(prof, "b", "x/b", 2)
    for k, v in (("SG_REPO", "x/a"), ("SG_OWNER", "x"), ("SG_PROJECT", "1"), ("SG_PROFILE", "a"),
                 ("SG_CHECKOUT", str(tmp_path / "a")), ("SG_STATE", str(base / "a")),
                 ("SG_HUB_DIR", str(tmp_path / "hub")), ("HOME", str(tmp_path)),
                 ("SG_PROFILES_DIR", str(prof)), ("SG_STATE_BASE", str(base))):
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("SG_SHARED", raising=False)
    gspec = importlib.util.spec_from_file_location("ghread", GHREAD_PY)
    gh = importlib.util.module_from_spec(gspec)
    monkeypatch.setitem(sys.modules, "ghread", gh)
    gspec.loader.exec_module(gh)
    spec = importlib.util.spec_from_file_location("hub_loop_projects", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for slug in ("a", "b"):
        (base / slug / "auto").mkdir(parents=True, exist_ok=True)
    (base / "b" / "watchd.pid").write_text(f"{os.getpid()}\n")
    (base / "b" / "loop-audit.jsonl").write_text(json.dumps(
        {"at": "2026-11-02T09:00:00-03:00", "who": "owner", "from": "loopback", "client": "phone", "action": "drain"}) + "\n")
    cache = base / "_boards" / "x_2" / "hub-board.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({
        "landed": [{"n": 12, "m": True, "c": "2026-11-01T10:00:00Z"}],
        "items": [{"n": 40, "t": "[loop-bug] the queue stalls", "l": ["loop-bug:dispatch"], "s": "OPEN"},
                  {"n": 41, "t": "a plain bug", "l": ["bug"], "s": "OPEN"}]}))
    def watchd(*a, state=None, checkout=None, profile=None):   # a start brings the named project's supervisor up: this process stands for it
        if a == ("start",):
            (pathlib.Path(state or base / "a") / "watchd.pid").write_text(f"{os.getpid()}\n")
        return 0
    mod.loop_watchd = watchd
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield mod, srv.server_port, base
    srv.shutdown()
    srv.server_close()


def _by_slug(v):
    return {p["slug"]: p for p in v.get("projects") or []}


def test_the_loop_fact_lists_every_project_with_its_own_state(farm):
    _, port, _ = farm
    code, v = get_json(port, "/api/loop")
    assert code == 200
    ps = _by_slug(v)
    assert set(ps) == {"a", "b"}
    assert ps["a"]["state"] == "off" and ps["b"]["state"] == "on"
    assert ps["a"]["repo"] == "x/a" and ps["b"]["repo"] == "x/b"


def test_a_project_drains_by_its_own_drain_mark(farm):
    _, port, base = farm
    (base / "b" / "auto" / "drain").write_text("")
    ps = _by_slug(get_json(port, "/api/loop")[1])
    assert ps["b"]["state"] == "draining" and ps["a"]["state"] == "off"


def test_a_project_names_its_live_runs_its_landing_its_last_change_and_its_loop_bugs(farm):
    _, port, _ = farm
    ps = _by_slug(get_json(port, "/api/loop")[1])
    b = ps["b"]
    assert b["live"]["runs"] == [] and b["live"]["gates"] == []
    assert b["landing"]["issue"] == 12 and b["landing"]["ts"] > 0
    assert b["change"]["action"] == "drain" and b["change"]["who"] == "owner"
    assert b["change"]["client"] == "phone" and b["change"]["at"].startswith("2026-11-02")
    assert b["bugs"] == 1 and [x["n"] for x in b["bugs_open"]] == [40]
    assert ps["a"]["landing"] is None and ps["a"]["change"] is None and ps["a"]["bugs"] == 0


def test_the_change_log_spans_projects_and_names_each_one(farm):
    _, port, _ = farm
    v = get_json(port, "/api/loop")[1]
    top = v["changes"][0]
    assert top["project"] == "b" and top["action"] == "drain" and top["client"] == "phone"


def test_a_projects_write_follows_that_projects_own_tailnet_grant(farm, monkeypatch):
    mod, port, _ = farm
    as_tailnet(mod, monkeypatch)
    monkeypatch.setattr(mod, "tailnet_source",
                        lambda checkout=None: ({"loop_write": str(checkout).endswith("/b")}, ""))
    ps = _by_slug(get_json(port, "/api/loop", origin=f"https://{TN_HOST}", host=TN_HOST)[1])
    assert ps["b"]["write"] is True and ps["a"]["write"] is False
    assert "SQUAD_TAILNET_LOOP_WRITE" in ps["a"]["reason"] and ps["b"]["reason"] == ""


def test_the_loop_switch_records_which_page_changed_it(farm):
    _, port, base = farm
    code, _ = ask(port, "POST", {"action": "start", "client": "phone"})
    assert code == 200
    last = [json.loads(x) for x in (base / "a" / "loop-audit.jsonl").read_text().splitlines()][-1]
    assert last["client"] == "phone" and last["action"] == "start"


def test_the_profile_list_is_the_seams_own_list(farm):
    """The per-project rows are discovered from the one seam. This runs the real `address.sh --list` over the farm's real
    profiles directory, so a change in what the seam prints is caught here and not only on a live hub (#1599)."""
    mod, _, _ = farm
    out = subprocess.run(["bash", str(mod.SCRIPTS / "address.sh"), "--list"], capture_output=True, text=True, timeout=20)
    seam = [ln.split("\t")[0] for ln in out.stdout.splitlines() if ln.strip()]
    assert seam == ["a", "b"], f"the seam prints the two slugs: {out.stdout!r}"
    assert mod.profile_list() == seam
    assert [r["slug"] for r in mod.project_contexts()] == ["a", "b"], "every profile the seam lists is a project the hub serves"


def test_a_write_to_one_project_lands_in_that_projects_own_state(farm):
    """A loop page writes to one project only: `/<slug>/api/loop` applies the start to that project's state directory and
    audits it there, and the hub's own project is untouched (AC4; ADR-0162)."""
    _, port, base = farm
    (base / "b" / "watchd.pid").unlink()   # project b is off, so a start applies to it
    code, out = ask(port, "POST", {"action": "start", "client": "phone"}, path="/b/api/loop")
    assert code == 200, out
    assert (base / "b" / "watchd.pid").is_file(), "the start ran that project's own supervisor, not the hub's"
    assert [x["action"] for x in audit(base / "b")] == ["drain", "start"], "the change is audited in that project's log"
    assert audit(base / "a") == [], "the hub's own project is never written by another project's page"


def test_a_write_to_a_project_the_hub_does_not_serve_is_refused(farm):
    """A slug the hub serves no profile for is refused, and no state directory is read or written from it."""
    _, port, base = farm
    code, out = ask(port, "POST", {"action": "start", "client": "phone"}, path="/nope/api/loop")
    assert code == 404 and "nope" in (out.get("reason") or ""), out
    assert not (base / "nope").exists()


def test_the_loop_answer_names_the_loop_bug_a_drain_waits_on(hub):
    """The view carries why the loop is off: the loop-bug the loop drained itself for, or the owner (#1708, ADR-0146)."""
    _, port, _, _, state = hub
    assert ask(port, "GET")[1]["cause"] == {}
    (state / "auto" / "loop-bug-drain").write_text("1699\n")
    assert ask(port, "GET")[1]["cause"] == {"issue": 1699}
    (state / "auto" / "owner-stop").write_text("")
    assert ask(port, "GET")[1]["cause"] == {"owner": True}, "the owner mark wins over a stale drain mark"
    (state / "auto" / "owner-stop").unlink()
    (state / "auto" / "loop-bug-drain").write_text("not a number\n")
    assert ask(port, "GET")[1]["cause"] == {}


def test_a_refused_start_lifts_no_pause(hub):
    """A start watchd refuses, as it does for an open loop-bug, keeps the pause, the drain and the mark: the hub lifts them
    only after the start was not refused (#1708)."""
    mod, port, calls, _, state = hub
    auto = state / "auto"
    for f in ("off", "drain", "drain-pause"):
        (auto / f).write_text("")
    (auto / "loop-bug-drain").write_text("1699\n")
    mod.loop_watchd = lambda *a, **kw: calls.append(list(a)) or 1
    code, out = ask(port, "POST", {"action": "start"})
    assert code == 500 and out["err"] == "watchd did not start", out
    assert calls == [["start"]]
    assert all((auto / f).exists() for f in ("off", "drain", "drain-pause", "loop-bug-drain"))
    assert [a.get("failed") for a in audit(state)] == ["watchd did not start"]


def test_a_start_that_holds_lifts_the_pause(hub):
    _, port, _, _, state = hub
    auto = state / "auto"
    for f in ("off", "drain", "drain-pause"):
        (auto / f).write_text("")
    assert ask(port, "POST", {"action": "start"})[0] == 200
    assert not any((auto / f).exists() for f in ("off", "drain", "drain-pause"))


def test_the_page_names_the_loop_bug_beside_the_state():
    """Draining reads "for the loop-bug #N" and off reads "until the loop-bug #N closes", in the state line and the switch
    line; an owner stop and a loop that is on name no issue (#1708)."""
    cause = {"issue": 1699}
    for fn in ("loopLogHtml", "loopSetHtml"):
        assert "Loop draining for the loop-bug #1699" in run_js(f"{fn}({js_view(state='draining', cause=cause)})"), fn
        assert "Loop off until the loop-bug #1699 closes" in run_js(f"{fn}({js_view(state='off', cause=cause)})"), fn
        assert "loop-bug" not in run_js(f"{fn}({js_view(state='off', cause={'owner': True}, log=[])})"), fn
        assert "loop-bug" not in run_js(f"{fn}({js_view(state='on', cause=cause, log=[])})"), fn


def test_the_log_names_the_issue_of_a_loop_written_line():
    """The loop is the second writer of the audit: its drain and its start name the issue (#1708, ADR-0162)."""
    log = [{"at": "2026-11-14T03:00:00-03:00", "who": "loop", "from": "autobug", "action": "drain", "issue": 1699},
           {"at": "2026-11-14T04:00:00-03:00", "who": "loop", "from": "health", "action": "start", "issue": 1699}]
    out = run_js(f"loopLogHtml({js_view(log=log)})")
    assert "drain for the loop-bug #1699" in out and "start: no loop-bug is open after #1699" in out
