"""The phone's first tab is the Monitor: the same card the desktop hub shows in its left pane, drawn by the same code.

The hub cuts the card's markup, style and script out of the desktop page and substitutes them into the phone page, so one
change draws both. The page is booted under node over the stub DOM of `tests/test_hub_mobile_move.py`, with `/api/load`
answered by the hub's own fact builders, never by a hand-made shape.
"""
import json
import os
import re
import tempfile

import pytest

from tests.test_hub_mobile_move import HUB_PY, _module, run_page
from tests.test_hub_spec_progress import BOARD

NOW = 1_000_000.0
SHA = "0123456789abcdef0123456789abcdef01234567"
TABS = ["monitor", "board", "runs", "terminals", "more"]


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    return _module("hub_mobile_monitor", HUB_PY)


def _load(hub):
    """A `/api/load` body built by the hub's own builders: the footer facts and the open-spec bar."""
    src = {"landing": {"issue": 73, "ts": NOW - 7200}, "quota": {"codex": None, "agy": None}, "loop": True,
           "gate": {"kind": "fast", "result": "fail", "sha": SHA, "ts": NOW - 300, "took": 127, "avg": 169}}
    return {"cpu": 12, "ram": 97, "gpu": None, "warn": False, "facts": hub.foot_items(src, NOW),
            "specs": hub.spec_progress(BOARD, moves={})}


def _routes(hub, loop=None):
    v = loop or {"state": "on", "who": "", "when": "", "write": False, "reason": "read-only from the tailnet",
                 "actions": [], "live": {"runs": [], "gates": []}, "log": []}
    return {"/api/load": [{"body": json.dumps(_load(hub))}], "/api/loop": [{"body": json.dumps(v)}]}


def test_the_first_tab_is_the_monitor(hub):
    page = hub.MOBILE_PAGE
    assert re.findall(r'data-tab="(\w+)"', page) == TABS
    assert re.search(r'<button data-tab="monitor">Monitor</button>', page)
    assert 'data-panel="status"' not in page and 'data-tab="status"' not in page


def test_the_phone_page_carries_the_desktop_card_from_one_source(hub):
    page = hub.MOBILE_PAGE
    block = re.search(r"^// INST\{.*?^// \}INST$", hub.PAGE, re.M | re.S).group(0).replace("__REPO__", hub.REPO)
    assert block in page, "the phone draws with the desktop's own instruments block"
    assert page.count("function gaugeGeom(") == 1, "one copy, never two"
    card = re.search(r"<details id=inst\b.*?</details>", hub.PAGE, re.S).group(0)
    assert card in page, "the phone carries the desktop's own card markup"
    assert "/*INST" not in page and "__REPO__" not in page and "__LOADERR__" not in page


def test_the_monitor_draws_gauges_tiles_and_the_spec_bar(hub):
    out = run_page(hub.MOBILE_PAGE, """
      await loadPoll(); await __tick(10);
      const h = id => String(document.getElementById(id).innerHTML || "");
      return { shown: !document.querySelector('[data-panel="monitor"]').hidden, igs: h("igs"), ian: h("ian"),
               ispec: h("ispec"), sum: document.getElementById("instsr").textContent };
    """, routes=_routes(hub))
    assert out["shown"], "the Monitor is the default for a new visitor"
    assert out["igs"].count("<svg") == 3 and 'stroke="hsl(' in out["igs"] and "ldred" in out["igs"]
    for k in ("Gate", "fast fail", "took 2m07s · avg 2m49s"):
        assert k in out["ian"], k
    assert "Landed" not in out["ian"], "the owner removed the Landed tile (2026-11-01)"
    assert "Loop" not in out["ian"], "the loop state lives in its own panel (#1071)"
    assert "Specs" in out["ispec"] and "role=progressbar" in out["ispec"]
    assert out["sum"] == "1 fail"


@pytest.mark.parametrize("stored, tab", [(None, "monitor"), ("status", "monitor"), ("monitor", "monitor"),
                                         ("board", "board"), ("gone", "monitor")])
def test_a_stored_tab_maps_status_to_monitor(hub, stored, tab):
    assert run_page(hub.MOBILE_PAGE, f"return tabOf({json.dumps(stored)});", routes=_routes(hub)) == tab


def test_the_loop_control_sits_in_the_card_and_obeys_the_hub(hub):
    """The tailnet loop-write rule stays on the hub: a read-only view disables every button and says why."""
    out = run_page(hub.MOBILE_PAGE, """
      await loopView();
      const b = id => document.getElementById(id);
      return { inCard: !!b("loop-start").closest("#iloop"),
               disabled: ["loop-start", "loop-drain", "loop-now"].map(i => b(i).disabled),
               reason: b("loop-reason").textContent };
    """, routes=_routes(hub))
    assert out["inCard"]
    assert out["disabled"] == [True, True, True]
    assert "read-only from the tailnet" in out["reason"]


def test_the_status_facts_the_monitor_lacks_stay_in_its_panel(hub):
    page = hub.MOBILE_PAGE
    panel = re.search(r'<section data-panel="monitor">.*?</section>', page, re.S).group(0)
    for k in ("t-live", "t-spend", "t-bugs", "warnings"):
        assert f"id={k}" in panel, k
    assert "id=load>" not in panel, "the gauges say the load: the text line is dropped"
    assert "The hub does not answer" in page
    assert re.search(r"#loop-ctl button[^{]*\{[^}]*min-height:44px", page)


def test_the_phone_card_never_folds(hub):
    """The Monitor is the whole tab on the phone: a tap on its title does nothing. The desktop card still folds."""
    out = run_page(hub.MOBILE_PAGE, """
      const s = document.getElementById("inst").querySelector("summary");
      let prevented = false;
      const ev = { target: s, preventDefault() { prevented = true; } };
      (s.listeners.click || []).forEach(f => f(ev));
      if (s.onclick) s.onclick(ev);
      return { prevented, tab: s.tabIndex };
    """, routes=_routes(hub))
    assert out["prevented"], "a tap on the summary never toggles the card"
    assert out["tab"] == -1, "the inert title takes no focus"
    assert "preventDefault" not in hub.INST_JS, "the shared card code leaves the desktop's fold alone"


def _stale(hub, fail):
    good = {"body": json.dumps(_load(hub))}
    routes = _routes(hub)
    routes["/api/load"] = [good, good, good]
    return run_page(hub.MOBILE_PAGE, """
      const b = id => document.getElementById(id);
      const real = fetch;
      """ + fail + """
      await loadPoll();
      const bad = { cls: b("inst").classList.contains("stale"), note: b("inst-stale") && !b("inst-stale").hidden,
                    text: b("inst-stale") ? b("inst-stale").textContent : "", inCard: !!(b("inst-stale") && b("inst-stale").closest("#inst")) };
      fetch = real; __state.routes["/api/load"] = [""" + json.dumps(good) + """];
      await loadPoll();
      return { bad, cls: b("inst").classList.contains("stale"), hidden: b("inst-stale").hidden };
    """, routes=routes)


@pytest.mark.parametrize("fail", [
    '__state.routes["/api/load"] = [{ body: "{}", status: 500 }];',
    'fetch = () => Promise.reject(new Error("offline"));',
], ids=["non-ok", "network"])
def test_a_failed_load_read_marks_the_card_stale_and_the_next_good_one_clears_it(hub, fail):
    """The card has no countdown to freeze since the owner removed the next-cycle counter (2026-11-01): an old reading
    dims and says why, and one good read puts it right."""
    out = _stale(hub, fail)
    assert out["bad"]["cls"] and out["bad"]["note"] and out["bad"]["inCard"], out
    assert out["bad"]["text"], "the card says why it is not current"
    assert not out["cls"] and out["hidden"], "the next good read clears it"


def test_an_imported_pr_link_is_a_44px_target_on_the_phone(hub):
    page = hub.MOBILE_PAGE
    css = page[page.index("<style>"):page.index("</style>")]
    tail = css[css.rindex("/* }INST-CSS */"):]
    assert re.search(r"#inst \.at a\{[^}]*min-height:44px", tail), "the phone override follows the shared card style"
    assert re.search(r"#inst \.at a\{[^}]*min-width:44px", tail)
