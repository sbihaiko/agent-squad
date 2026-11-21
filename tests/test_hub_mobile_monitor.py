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

from tests.test_hub_mobile_move import HUB_PY, _module, route_tabs, run_js, run_page
from tests.test_hub_spec_progress import BOARD

NOW = 1_000_000.0
SHA = "0123456789abcdef0123456789abcdef01234567"


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
            "speed": None, "specs": hub.spec_progress(BOARD, moves={})}


def _routes(hub, loop=None):
    v = loop or {"state": "on", "who": "", "when": "", "write": False, "reason": "read-only from the tailnet",
                 "actions": [], "live": {"runs": [], "gates": []}, "log": []}
    return {"/api/load": [{"body": json.dumps(_load(hub))}], "/api/loop": [{"body": json.dumps(v)}]}


def test_the_monitor_draws_the_now_tab(hub):
    """The Monitor is what the Now tab draws (SPEC 1596): the bar names the tab, the panel keeps the card."""
    page = hub.MOBILE_PAGE
    assert re.findall(r'data-tab="(\w+)"', page) == [tab for tab, _ in route_tabs(page)]
    assert re.search(r'<button data-tab="now">Now</button>', page)
    assert '<section data-panel="monitor">' in page, "the Now tab no longer draws the Monitor panel"
    assert 'data-panel="status"' not in page and 'data-tab="status"' not in page


@pytest.mark.node_bound
def test_the_page_boots_with_no_lead_markup(hub):
    """A page served without the lead chart still boots: its read and its click find no element and do no work (#2499)."""
    page = re.sub(r'<article data-screen="lead".*?</article>', "", hub.MOBILE_PAGE, flags=re.S)
    out = run_page(page, "return { booted: true };", routes=_routes(hub))
    assert out == {"booted": True}, out


def test_the_phone_page_carries_the_desktop_card_from_one_source(hub):
    page = hub.MOBILE_PAGE
    block = re.search(r"^// INST\{.*?^// \}INST$", hub.PAGE, re.M | re.S).group(0).replace("__REPO__", hub.REPO)
    assert block in page, "the phone draws with the desktop's own instruments block"
    assert page.count("function gaugeGeom(") == 1, "one copy, never two"
    card = re.search(r"<details id=inst\b.*?</details>", hub.PAGE, re.S).group(0)
    assert card in page, "the phone carries the desktop's own card markup"
    assert "/*INST" not in page and "__REPO__" not in page and "__LOADERR__" not in page


def test_the_monitor_scope_defines_the_formatters_the_cut_calls(hub):
    """The card's script is cut from the desktop page and runs in its own scope. A helper it calls must ride along inside
    that scope, or the card dies on the phone with "Can't find variable" (#1632)."""
    page = hub.MOBILE_PAGE
    i = page.index("const inst = (() => {")
    j = page.index("return { instDraw };", i)
    scope = page[i:j]
    assert "const fpct" in scope, "the quota formatter rides along with the card it draws"
    assert "fpct(" in scope

@pytest.mark.heavy
def test_the_monitor_draws_gauges_tiles_and_the_spec_bar(hub):
    out = run_page(hub.MOBILE_PAGE, """
      await loadPoll(); await __tick(10);
      const h = id => String(document.getElementById(id).innerHTML || "");
      return { shown: !document.querySelector('[data-panel="monitor"]').hidden, igs: h("igs"), ian: h("ian"),
               ispeed: h("ispeed"), sum: document.getElementById("instsr").textContent };
    """, routes=_routes(hub))
    assert out["shown"], "the Monitor is the default for a new visitor"
    assert out["igs"].count("<svg") == 3 and 'stroke="url(#g-' in out["igs"] and "ldred" in out["igs"]
    assert out["ian"] == "", "the owner removed the Gate tile (#1260)"
    assert "Landed" not in out["ian"], "the owner removed the Landed tile (2026-11-01)"
    assert "Loop" not in out["ian"], "the loop state lives in its own panel (#1071)"
    assert "Specs" in out["ispeed"] and "5 of 9 closed - 55%" in out["ispeed"]
    assert out["sum"] == "", "the summary no longer counts the gate; a red gate stays in the footer (#1260)"


CUT_SCOPE = """const $ = () => ({ classList: { toggle() {} }, style: {}, children: [], innerHTML: "", textContent: "",
  querySelector: () => null });
const esc = t => String(t);
"""


def test_the_cut_carries_every_name_the_monitor_draws_with(hub):
    """The cut lands in the phone page's Monitor scope with only `$` and `esc` beside it, so a helper the block draws with
    must ride in the cut itself. `fpct` stayed behind in the desktop's main script, and the card threw "Can't find
    variable: fpct" and drew no quota (#1632)."""
    body = {"cpu": 12, "ram": 97, "gpu": None, "warn": False, "facts": [],
            "subs": [{"label": "Claude", "pct": 42.5, "resets": NOW + 86400}],
            "speed": None, "specs": {"value": "no open spec"}}
    src = (CUT_SCOPE + hub.INST_JS + "\nlet drewError = null;\ntry { instDraw(" + json.dumps(body)
           + "); } catch (e) { drewError = String((e && e.message) || e); }\n")
    err = run_js(src, "drewError")
    assert err is None, f"the cut draws a name it does not carry: {err}"


def test_the_count_gauges_draw_on_a_390x844_phone(hub):
    """The phone is one 390 by 844 screen and the Monitor is the whole tab: the three count gauges still draw there, and a
    load that carries subscriptions does not stop the draw half way (#1632)."""
    routes = _routes(hub)
    body = _load(hub)
    body["subs"] = [{"label": "Claude", "pct": 42.5, "window": "weekly", "resets": NOW + 86400}]
    routes["/api/load"] = [{"body": json.dumps(body)}]
    out = run_page(hub.MOBILE_PAGE, """
      window.innerWidth = 390; window.innerHeight = 844;   // drawing now sees the phone, not the harness's wide default
      await loadPoll(); await __tick(10);
      const h = id => String(document.getElementById(id).innerHTML || "");
      const inst = document.getElementById("inst");
      return { w: innerWidth, hgt: innerHeight, igs: h("igs"), ispeed: h("ispeed"),
               note: String((document.getElementById("inst-stale") || {}).textContent || ""), stale: inst.classList.contains("stale") };
    """, routes=routes)
    assert (out["w"], out["hgt"]) == (390, 844)
    assert out["igs"].count("<svg") == 3, "the three count gauges draw"
    assert "Specs" in out["ispeed"], "the draw runs to the end of the card"
    assert not out["stale"] and "fpct" not in out["note"], out["note"]


@pytest.mark.parametrize("stored, tab", [(None, "now"), ("status", "now"), ("monitor", "now"), ("board", "work"),
                                         ("terminals", "now"), ("more", "now"), ("gone", "now")])
def test_a_stored_tab_keeps_its_meaning(hub, stored, tab):
    """A tab stored by an older build opens the tab that draws it now, and an unknown name opens the first tab."""
    assert run_page(hub.MOBILE_PAGE, f"return tabOf({json.dumps(stored)});", routes=_routes(hub)) == tab


LOOP_FACT = {"projects": [{"slug": "x", "repo": "x/y", "own": True, "state": "off", "live": {"runs": [], "gates": []},
                           "landing": None, "change": None, "bugs": 0, "bugs_open": [], "write": False,
                           "reason": "read-only from the tailnet"}],
             "changes": []}


def test_the_loop_control_obeys_the_hub(hub):
    """The tailnet loop-write rule stays on the hub: a read-only view refuses the primary and says why. The control sits on
    the project's own loop page, opened from the Now strip (SPEC 1596, rule 8)."""
    out = run_page(hub.MOBILE_PAGE, """
      await __tick(10);
      openLayer("loop", { slug: "x" });
      const b = id => document.getElementById(id);
      return { label: b("loop-primary").textContent, disabled: b("loop-primary").disabled,
               state: b("loop-primary").dataset.state, reason: b("loop-reason").textContent };
    """, routes=_routes(hub, loop=LOOP_FACT))
    assert (out["label"], out["disabled"], out["state"]) == ("Refused", True, "refused")
    assert "read-only from the tailnet" in out["reason"]


def test_the_status_facts_the_monitor_lacks_stay_in_its_panel(hub):
    page = hub.MOBILE_PAGE
    panel = re.search(r'<section data-panel="monitor">.*?</section>', page, re.S).group(0)
    for k in ("t-live", "t-spend", "t-bugs", "warnings"):
        assert f"id={k}" in panel, k
    assert "id=load>" not in panel, "the gauges say the load: the text line is dropped"
    assert "The hub does not answer" in page
    assert re.search(r"#loop-actions button[^{]*\{[^}]*min-height:44px", page), "the loop action row is a thumb target"


@pytest.mark.node_bound
def test_monitor_throughput_and_warnings_render_on_now_only(hub):
    """The Monitor card, the Throughput block and the Warnings live inside the Now panel (SPEC 2498, #2502), so another
    tab never shows them twice."""
    out = run_page(hub.MOBILE_PAGE, """
      const inNow = id => !!document.getElementById(id).closest('[data-panel="monitor"]');
      show("now");
      const now = { inst: inNow("inst"), speed: inNow("ispeed"), warn: inNow("warnings") };
      show("runs");
      return { now, panelHidden: document.querySelector('[data-panel="monitor"]').hidden,
               shown: [...document.querySelectorAll("[data-panel]")].filter(p => !p.hidden).map(p => p.dataset.panel) };
    """, routes=_routes(hub))
    assert out["now"] == {"inst": True, "speed": True, "warn": True}, out
    assert out["panelHidden"] is True and out["shown"] == ["runs"], out


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
