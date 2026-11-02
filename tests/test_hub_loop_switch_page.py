"""The loop switch on the desktop hub (#923, ADR-0162): the Monitor card and Settings > Loop draw one view of `/api/loop`.

The route is tested over HTTP here, not only as functions: a loopback start writes one audit line, a cross-site write is
refused, and a tailnet write is refused with its reason unless `tailnet.loop_write` is on. The page's renderers are run in
node: the Monitor card and the Loop theme show the state, who changed it and when; the stop confirm lists every live run and gate;
the Access theme holds the tailnet rule. The Loop theme has no Save.
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

    def watchd(*a):   # a start brings the supervisor up: this process stands for it
        calls.append(list(a))
        if a == ("start",):
            (state / "watchd.pid").write_text(f"{os.getpid()}\n")
        return 0
    mod.loop_watchd = watchd
    tn = {"host": TN_HOST, "allow": ["alice@example.com"]}
    mod.tailnet_block = lambda: dict(tn)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield mod, srv.server_port, calls, tn, state
    srv.shutdown()
    srv.server_close()


def ask(port, method, body=None, *, origin=None, host=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Host": host or f"127.0.0.1:{port}", "Content-Type": "application/json",
               "Origin": origin if origin is not None else f"http://127.0.0.1:{port}"}
    c.request(method, "/api/loop", body=None if body is None else json.dumps(body), headers=headers)
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
    assert v["access"] == {"loop_write": False, "terminals_send": False}
    assert v["actions"] == ["start"]


@pytest.mark.parametrize("state,actions", [("off", ["start"]), ("on", ["drain", "stop-now"]), ("draining", ["stop-now"]), ("starting", [])])
def test_the_view_names_the_actions_each_state_allows(hub, monkeypatch, state, actions):
    """The rule that picks the live buttons is the hub's, so the phone page holds no state rule (ADR-0137)."""
    mod, port, _, _, _ = hub
    monkeypatch.setattr(mod, "loop_state", lambda last, now=None: state)
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


VIEW = {"state": "on", "who": "owner", "when": "2026-10-31T16:02:00-03:00", "write": True, "reason": "", "tailnet": False,
        "access": {"loop_write": False, "terminals_send": False},
        "live": {"runs": ["/x/runs/run-20261031-1", "run-20261031-2"], "gates": ["gate pid 4242"]},
        "log": [{"at": "2026-10-31T16:02:00-03:00", "who": "owner", "from": "loopback", "action": "start"}]}


def js_view(**over):
    return json.dumps({**VIEW, **over})


def test_the_monitor_shows_the_loop_state_once_and_never_its_switch():
    """The owner read the loop twice in one panel: Monitor carries one read-only row, and the switch lives in its own
    panel (owner, 2026-11-01)."""
    src = page()
    card = src.split("<details id=inst", 1)[1].split("</details>", 1)[0]
    assert "id=iloop" not in card and "loopCardHtml" not in src
    assert src.count('{k:"Loop",text:') == 1     # one Loop row, the tile the summary reads
    assert "loopB(" not in card                  # the Monitor never draws the switch


def test_the_loop_theme_shows_state_who_when_and_the_change_log():
    out = run_js(f"loopSetHtml({js_view()})")
    assert "owner" in out and "16:02" in out and "loopback" in out and "start" in out


def test_the_loop_theme_shows_the_read_only_reason():
    out = run_js(f"loopSetHtml({js_view(write=False, reason='read-only from the tailnet: SQUAD_TAILNET_LOOP_WRITE is not on')})")
    assert "SQUAD_TAILNET_LOOP_WRITE" in out and "disabled" in out


def test_a_failed_start_is_shown_in_the_loop_theme_and_the_log():
    """A start that did not hold reads off with its failure shown, never a disabled Starting... that hides it (#923 review)."""
    log = [{"at": "2026-10-31T16:02:00-03:00", "who": "owner", "from": "loopback", "action": "start", "failed": "watchd did not start"}]
    out = run_js(f"loopSetHtml({js_view(state='off', failed='watchd did not start', log=log)})")
    assert out.count("watchd did not start") >= 2
    assert "Start loop" in out and "disabled" not in out


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


def test_the_access_theme_holds_the_tailnet_rule():
    out = run_js(f"accHtml({js_view()})")
    assert "SQUAD_TAILNET_LOOP_WRITE" in out and "SQUAD_TAILNET_TERMINALS_SEND" in out and "refused" in out
    out = run_js(f"accHtml({js_view(access={'loop_write': True, 'terminals_send': False}, tailnet=True)})")
    assert "allowed" in out and "tailnet" in out


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
