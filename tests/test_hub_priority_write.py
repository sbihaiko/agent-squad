"""The owner's priority dropdown on a Kanban card (#2289).

A choice sends one POST to the hub; the hub runs the board writer's `--priority` path in the background, so the page
answers at once. The card draws the chosen mark with a `pending` chip until the writer's answer, a proved write changes
the served value, and a refusal names its reason on the card and keeps the read's value. A choice equal to the present
value sends no write and writes no audit line (ADR-0144). Only the hub's own origin writes, and a tailnet write needs its
own grant (ADR-0162). A spec's write reaches its open children by the inheritance rule (#1979).
"""
import http.client
import importlib.util
import json
import pathlib
import sys
import threading
import types
from http.server import ThreadingHTTPServer

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
GHREAD_PY = ROOT / "contrib/squad-goal/scripts/ghread.py"
TN_HOST = "box.tail1.ts.net"


@pytest.fixture
def hub(monkeypatch, tmp_path):
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("HOME", str(tmp_path)),
                 ("SG_CHECKOUT", str(tmp_path)), ("SG_HUB_DIR", str(tmp_path / "hub")), ("SG_STATE", str(tmp_path / "state"))):
        monkeypatch.setenv(k, v)
    (tmp_path / "state").mkdir()
    for k in ("SG_SHARED", "SG_PROFILE", "SG_STATE_BASE"):
        monkeypatch.delenv(k, raising=False)
    gspec = importlib.util.spec_from_file_location("ghread", GHREAD_PY)
    gh = importlib.util.module_from_spec(gspec)
    monkeypatch.setitem(sys.modules, "ghread", gh)
    gspec.loader.exec_module(gh)
    spec = importlib.util.spec_from_file_location("hub_priority_write", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.board_cache = {"t": 1, "err": None, "errs": [], "prs": [], "lc": {}, "cases": [], "parts": {},
                       "items": [{"n": 9, "i": "I9", "r": "x/y", "t": "spec card", "s": "OPEN", "st": "Todo",
                                  "l": ["spec"], "ot": 1, "p": "low", "pa": None}]}
    mod.priority_calls = []
    mod.priority_release = threading.Event()
    mod.priority_release.set()

    def fake_call(n, level):
        mod.priority_calls.append((n, level))
        mod.priority_release.wait(10)
        return types.SimpleNamespace(returncode=0, stdout=f"#{n} priority {level}\n", stderr="")

    monkeypatch.setattr(mod, "priority_call", fake_call)
    monkeypatch.setattr(mod, "priority_backfill", types.SimpleNamespace(propagate=lambda *a, **k: {}))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield mod, srv.server_port
    mod.priority_release.set()
    mod.wait_for_priorities(10)
    srv.shutdown()
    srv.server_close()


def ask(port, body, *, origin=None, host=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    headers = {"Host": host or f"127.0.0.1:{port}", "Content-Type": "application/json",
               "Origin": origin if origin is not None else f"http://127.0.0.1:{port}"}
    c.request("POST", "/api/board/priority", body=json.dumps(body), headers=headers)
    r = c.getresponse()
    data = json.loads(r.read() or b"{}")
    c.close()
    return r.status, data


def audit(mod):
    p = mod.PRIORITY_AUDIT
    return [json.loads(x) for x in p.read_text().splitlines()] if p.is_file() else []


def card_row(mod, n):
    return next(x for g in mod.board()["groups"] for x in g["items"] if x["n"] == n)


def as_tailnet(mod, monkeypatch):
    monkeypatch.setattr(mod, "access", lambda h, m: mod.Access(True, "", "alice@example.com", True))


def test_a_choice_sends_one_board_write_and_the_card_shows_it_pending(hub):
    mod, port = hub
    mod.priority_release.clear()

    code, out = ask(port, {"issue": 9, "priority": "high"})

    assert code == 200 and out == {"ok": True, "issue": 9, "priority": "high", "changed": True, "pending": True}
    row = card_row(mod, 9)
    assert row["pr"]["level"] == "high" and row["prp"] is True
    mod.priority_release.set()
    mod.wait_for_priorities(10)
    assert mod.priority_calls == [(9, "high")]
    row = card_row(mod, 9)
    assert row["pr"]["level"] == "high" and not row.get("prp") and "prr" not in row


def test_a_refused_write_keeps_the_old_mark_and_names_the_reason(hub, monkeypatch):
    mod, port = hub

    def refuse(n, level):
        return types.SimpleNamespace(returncode=1, stdout="", stderr="#9: the Priority was not carried\n")

    monkeypatch.setattr(mod, "priority_call", refuse)

    code, out = ask(port, {"issue": 9, "priority": "urgent"})
    assert code == 200 and out["pending"] is True
    mod.wait_for_priorities(10)

    row = card_row(mod, 9)
    assert row["pr"]["level"] == "low" and "the Priority was not carried" in row["prr"]
    assert card_row(mod, 9).get("prp") is None


def test_the_chosen_level_equal_to_the_present_value_sends_no_write_and_no_audit(hub):
    mod, port = hub

    code, out = ask(port, {"issue": 9, "priority": "low"})

    assert code == 200 and out["changed"] is False
    assert mod.priority_calls == [] and audit(mod) == []
    assert card_row(mod, 9)["pr"]["level"] == "low"


def test_a_write_from_another_origin_is_refused(hub, monkeypatch):
    mod, port = hub

    code, out = ask(port, {"issue": 9, "priority": "high"}, origin="http://evil.example")

    assert code == 403 and out["err"] == "forbidden"
    assert mod.priority_calls == [] and audit(mod) == []


def test_a_tailnet_write_needs_its_own_grant(hub, monkeypatch):
    mod, port = hub
    as_tailnet(mod, monkeypatch)
    monkeypatch.setattr(mod, "tailnet_block", lambda checkout=None: {"host": TN_HOST, "loop_write": True})

    code, out = ask(port, {"issue": 9, "priority": "high"}, origin=f"https://{TN_HOST}", host=TN_HOST)

    assert code == 403 and "tailnet.board_priority_write" in out["reason"]
    assert mod.priority_calls == [] and audit(mod) == []


def test_a_granted_tailnet_write_runs_and_audits_its_origin(hub, monkeypatch):
    mod, port = hub
    as_tailnet(mod, monkeypatch)
    monkeypatch.setattr(mod, "tailnet_block", lambda checkout=None: {"host": TN_HOST, "board_priority_write": True})

    code, _ = ask(port, {"issue": 9, "priority": "high"}, origin=f"https://{TN_HOST}", host=TN_HOST)
    mod.wait_for_priorities(10)

    assert code == 200
    assert [(a["issue"], a["from_where"]) for a in audit(mod)] == [(9, "tailnet")]


def test_each_write_appends_one_audit_line_with_card_old_new_and_time(hub):
    mod, port = hub
    mod.priority_release.clear()

    ask(port, {"issue": 9, "priority": "urgent"})
    ask(port, {"issue": 9, "priority": "normal"})   # refused while the first is in flight: no second line
    mod.priority_release.set()
    mod.wait_for_priorities(10)

    lines = audit(mod)
    assert len(lines) == 1
    assert (lines[0]["issue"], lines[0]["from"], lines[0]["to"]) == (9, "low", "urgent")
    assert lines[0]["at"]


def test_a_spec_write_carries_the_value_to_open_children(hub, monkeypatch):
    mod, port = hub
    mod.board_cache["items"] = [
        {"n": 9, "i": "I9", "r": "x/y", "t": "spec", "s": "OPEN", "st": "Todo", "l": ["spec"], "ot": 1, "p": "low"},
        {"n": 10, "r": "x/y", "t": "child open", "s": "OPEN", "st": "Todo", "l": [], "ot": 1, "pa": 9},
        {"n": 11, "r": "x/y", "t": "child closed", "s": "CLOSED", "st": "Done", "l": [], "ot": 1, "pa": 9},
    ]
    calls = []
    monkeypatch.setattr(mod, "priority_backfill", types.SimpleNamespace(
        propagate=lambda state, repo, spec, version, value, by: calls.append((repo, spec, value, by)) or {}))

    ask(port, {"issue": 9, "priority": "high"})
    mod.wait_for_priorities(10)

    repo, spec, value, by = calls[0]
    assert (repo, spec, value) == ("x/y", 9, "high")
    assert set(by) == {9, 10} and by[9]["sub_issues"] == [10] and by[9]["state"] == "open"


def test_a_spec_write_sets_the_real_child_priority(hub, monkeypatch):
    mod, port = hub
    mod.board_cache["items"] = [
        {"n": 9, "i": "I9", "r": "x/y", "t": "spec", "s": "OPEN", "st": "Todo", "l": ["spec"], "ot": 1, "p": "low"},
        {"n": 10, "r": "x/y", "t": "child", "s": "OPEN", "st": "Todo", "l": [], "ot": 1, "pa": 9},
        {"n": 12, "r": "x/y", "t": "child bug", "s": "OPEN", "st": "Todo", "l": ["bug"], "ot": 1, "pa": 9},
        {"n": 11, "r": "x/y", "t": "child closed", "s": "CLOSED", "st": "Done", "l": [], "ot": 1, "pa": 9},
    ]
    real = importlib.util.spec_from_file_location("priority_backfill_real", ROOT / "contrib/squad-goal/scripts/priority_backfill.py")
    backfill = importlib.util.module_from_spec(real)
    real.loader.exec_module(backfill)
    monkeypatch.setattr(mod, "priority_backfill", backfill)
    written = []
    monkeypatch.setattr(mod, "board_seam", lambda: types.SimpleNamespace(set_priority=lambda n, v: written.append((n, v))))

    ask(port, {"issue": 9, "priority": "normal"})
    mod.wait_for_priorities(10)

    assert sorted(written) == [(10, "normal"), (12, "high")]


def test_a_pull_request_card_shows_no_priority_dropdown(hub):
    mod, _ = hub
    mod.board_cache["prs"] = [{"n": 77, "t": "a pr", "u": 1, "r": "x/y"}]

    lane = mod.board()["prs"]["items"]

    assert lane and "pr" not in lane[0]


def test_the_page_offers_the_four_levels_with_keyboard_and_close_rules(hub):
    mod, _ = hub
    page = mod.PAGE

    assert 'PRD_LEVELS=["urgent","high","normal","low"]' in page
    assert "api/board/priority" in page
    assert 'e.key==="ArrowDown"' in page and 'e.key==="ArrowUp"' in page
    assert 'e.key==="Enter"' in page and 'e.key==="Escape"' in page
    assert "prdClose()" in page
