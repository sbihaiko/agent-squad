"""The phone's loops per project (SPEC 1596, rule 8; #1599): the Now strip aggregates the hub's loop fact with one row per
project, the strip opens the Loops list, and a row opens that project's loop page. The loop page writes to one project
only, through that project's own route, and its primary follows the state: Cancel drain while draining, with Stop now
under ••• and one more ask that names what it kills; an off loop with open loop-bugs refuses the start with the count and
offers the bugs as its secondary. The Loops list has no switch for all loops at once.

The rules are one client module (`hub-mobile-loop.js`) inlined into the page; the page is booted under node over the stub
DOM of `tests/hub_mobile_stub.py`, whose `fetch` answers `/api/loop` and records every write. The tests assert what
the screens show and what the page sends, never a substring of its source.
"""
import json
import pathlib

import pytest

from tests.hub_mobile_stub import HUB_PY, _module, run_js, run_page

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
LOOP_JS = SCRIPTS / "hub-mobile-loop.js"


def fact():
    """The hub's loop fact: one row per project, each with its own state, live work, landing, change and loop-bugs."""
    return {
        "projects": [
            {"slug": "alpha", "repo": "x/a", "own": True, "state": "draining",
             "live": {"runs": ["/r/x/run-1"], "gates": []},
             "landing": {"issue": 12},
             "change": {"at": "2026-11-02T09:00:00-03:00", "who": "owner", "client": "phone", "action": "drain"},
             "bugs": 0, "bugs_open": [], "write": True, "reason": ""},
            {"slug": "beta", "repo": "x/b", "own": False, "state": "off",
             "live": {"runs": [], "gates": []}, "landing": None,
             "change": {"at": "2026-11-01T18:30:00-03:00", "who": "owner", "from": "desktop", "action": "stop-now"},
             "bugs": 2, "bugs_open": [{"n": 40, "t": "loop-bug:dispatch"}, {"n": 41, "t": "loop-bug:landing"}],
             "write": True, "reason": ""},
            {"slug": "gamma", "repo": "x/c", "own": False, "state": "on",
             "live": {"runs": ["/r/y/run-9"], "gates": ["gate py"]}, "landing": {"issue": 7},
             "change": {"at": "2026-11-02T10:15:00-03:00", "who": "owner", "client": "desktop", "action": "start"},
             "bugs": 0, "bugs_open": [], "write": True, "reason": ""},
        ],
        "changes": [
            {"project": "gamma", "at": "2026-11-02T10:15:00-03:00", "who": "owner", "client": "desktop", "action": "start"},
            {"project": "alpha", "at": "2026-11-02T09:00:00-03:00", "who": "owner", "client": "phone", "action": "drain"},
            {"project": "beta", "at": "2026-11-01T18:30:00-03:00", "who": "owner", "from": "desktop", "action": "stop-now"},
        ],
    }


def routes(body=None, reply=None):
    f = json.dumps(body or fact())
    return {
        "/api/loop": [{"body": f, "status": 200}],
        "/gamma/api/loop": [reply or {"body": json.dumps({"killed": {"runs": ["/r/y/run-9"], "gates": []}}), "status": 200}],
    }


OPEN_LOOP = """
  await __tick(10);
  openLayer("loop", { slug: %s });
"""


@pytest.fixture
def hub(monkeypatch, tmp_path):
    """The page module, loaded in a scoped environment: a test never leaks an SG_* value into the next one."""
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("HOME", str(tmp_path)),
                 ("SG_CHECKOUT", str(tmp_path)), ("SG_HUB_DIR", str(tmp_path / "hub"))):
        monkeypatch.setenv(k, v)
    for k in ("SG_STATE_BASE", "SG_SHARED", "SG_PROFILE", "SG_STATE", "SG_PROFILES_DIR"):
        monkeypatch.delenv(k, raising=False)
    return _module("hub_mobile_loops", HUB_PY)


@pytest.mark.node_bound
def test_the_strip_aggregates_every_project_and_names_no_row(hub):
    """The owner removed the per-project rows from Now (2026-11-08): the strip carries the aggregate alone and opens the
    Loops list, which is where every project is named."""
    out = run_page(hub.MOBILE_PAGE, """
      await __tick(10);
      return { agg: $("loops-agg").textContent, sub: $("loops-sub").textContent,
               strip: $("loops-strip").querySelectorAll(".looprow").length };
    """, routes=routes())
    assert out["agg"] == "1 on · 1 off · 1 draining", out
    assert out["sub"] == "3 projects"
    assert out["strip"] == 0, "the strip names no project; the Loops list does"


@pytest.mark.node_bound
def test_the_strip_opens_the_loops_list_with_every_project_and_the_change_log(hub):
    out = run_page(hub.MOBILE_PAGE, """
      await __tick(10);
      $("loops-open").onclick();
      return { open: !$("loops").hidden, agg: $("loops-agg-line").textContent,
               list: $("loops-list").querySelectorAll(".looprow").map(b => b.dataset.loopProject),
               log: $("loops-log").textContent };
    """, routes=routes())
    assert out["open"] is True
    assert "3 projects" in out["agg"]
    assert out["list"] == ["alpha", "beta", "gamma"]
    assert "gamma · owner · desktop" in out["log"] and "beta · owner · desktop" in out["log"], out["log"]
    assert "2026-11-02 10:15" in out["log"], "the change names its time"


@pytest.mark.node_bound
def test_the_loops_list_has_no_switch_for_all_loops_at_once(hub):
    out = run_page(hub.MOBILE_PAGE, """
      await __tick(10);
      $("loops-open").onclick();
      return { all: $("loops").querySelectorAll("[data-loop-all]").length,
               buttons: $("loops").querySelectorAll("button").map(b => [b.id, b.className]) };
    """, routes=routes())
    assert out["all"] == 0
    assert sorted(b[0] for b in out["buttons"] if not b[1].startswith("looprow")) == ["loops-backtop"], \
        "the only control is the one top back and one row per project, so no switch covers all loops"


@pytest.mark.node_bound
def test_the_loops_list_shows_one_back_at_the_top_and_no_action_row(hub):
    """A detail screen carries one back control, at the top (SPEC 2498, #2502). The list holds no action, so its
    action row is not drawn; the same holds for the loop-bugs screen and the Loop chart."""
    out = run_page(hub.MOBILE_PAGE, """
      await __tick(10);
      $("loops-open").onclick();
      return { top: $("loops").querySelectorAll("[data-topback]").filter(b => !b.hidden).map(b => b.id),
               heads: $("loops").querySelectorAll(".scr-head [data-topback]").map(b => b.id),
               backs: $("loops").querySelectorAll("[data-actback]").length,
               row: !!(document.getElementById("loops-actions")) };
    """, routes=routes())
    assert out["top"] == ["loops-backtop"], f"the Loops list shows more than one back control: {out}"
    assert out["heads"] == ["loops-backtop"], f"the Loops list's back is not at the top: {out}"
    assert out["backs"] == 0, f"the Loops list still holds a second back control: {out}"
    assert out["row"] is False, f"the Loops list draws an action row with no action: {out}"


@pytest.mark.node_bound
def test_the_loop_page_shows_one_back_at_the_top_and_no_back_in_its_action_row(hub):
    out = run_page(hub.MOBILE_PAGE, OPEN_LOOP % json.dumps("gamma") + """
      return { top: $("looppage").querySelectorAll("[data-topback]").filter(b => !b.hidden).map(b => b.id),
               heads: $("looppage").querySelectorAll(".scr-head [data-topback]").map(b => b.id),
               backs: $("looppage").querySelectorAll("[data-actback]").length,
               bar: $("loop-actions").hidden,
               actions: [...$("loop-actions").querySelectorAll("button")].filter(b => !b.hidden).map(b => b.id) };
    """, routes=routes())
    assert out["top"] == ["loop-backtop"], f"the Loop control shows more than one back control: {out}"
    assert out["heads"] == ["loop-backtop"], f"the Loop control's back is not at the top: {out}"
    assert out["backs"] == 0, f"the Loop control still holds a second back control: {out}"
    assert out["bar"] is False, f"the row holds Start or Drain and is not drawn: {out}"
    assert out["actions"] and "loop-backact" not in out["actions"], out


@pytest.mark.node_bound
def test_a_row_opens_that_projects_loop_page(hub):
    out = run_page(hub.MOBILE_PAGE, """
      await __tick(10);
      $("loops-open").onclick();
      $("loops-list").querySelectorAll(".looprow").find(b => b.dataset.loopProject === "gamma").onclick();
      return { open: !$("looppage").hidden, title: $("loop-title").textContent,
               state: $("loop-state").textContent, facts: $("loop-facts").textContent,
               primary: $("loop-primary").textContent };
    """, routes=routes())
    assert out["open"] is True
    assert out["title"] == "Loop · gamma"
    assert out["state"] == "Loop On"
    assert "issue #7" in out["facts"] and "run run-9" in out["facts"]
    assert out["primary"] == "Drain"


@pytest.mark.node_bound
def test_a_draining_project_offers_cancel_drain_and_keeps_stop_now_under_the_dots(hub):
    out = run_page(hub.MOBILE_PAGE, OPEN_LOOP % json.dumps("alpha") + """
      const before = { label: $("loop-primary").textContent, state: $("loop-primary").dataset.state,
                       dots: $("loop-dots").hidden, secondary: $("loop-secondary").hidden };
      $("loop-dots").onclick();
      const menu = $("loop-menu").hidden;
      $("loop-menu-stop").onclick();
      return { before, menu, confirm: $("loop-confirm").hidden, text: $("loop-confirm-text").textContent,
               posts: __state.posts };
    """, routes=routes())
    assert out["before"] == {"label": "Cancel drain", "state": "available", "dots": False, "secondary": True}
    assert out["menu"] is False, "the dots menu holds Stop now"
    assert out["confirm"] is False and "Stop now kills: run run-1" in out["text"], out["text"]
    assert out["posts"] == [], "the first ask writes nothing"


@pytest.mark.node_bound
def test_stop_now_writes_that_projects_own_route_with_the_phone_as_its_source(hub):
    out = run_page(hub.MOBILE_PAGE, OPEN_LOOP % json.dumps("gamma") + """
      $("loop-dots").onclick();
      $("loop-menu-stop").onclick();
      $("loop-confirm-go").onclick();
      await __tick(20);
      return { posts: __state.posts, confirm: $("loop-confirm").hidden };
    """, routes=routes())
    assert [p["path"] for p in out["posts"]] == ["/gamma/api/loop"], "one project's own route, never all loops"
    body = json.loads(out["posts"][0]["opts"]["body"])
    assert body == {"action": "stop-now", "client": "phone", "confirm": True}
    assert out["posts"][0]["opts"]["method"] == "POST"
    assert out["confirm"] is True, "the ask closes once it wrote"


@pytest.mark.node_bound
def test_an_off_project_with_loop_bugs_refuses_the_start_and_opens_the_bugs(hub):
    out = run_page(hub.MOBILE_PAGE, OPEN_LOOP % json.dumps("beta") + """
      const first = { label: $("loop-primary").textContent, disabled: $("loop-primary").disabled,
                      state: $("loop-primary").dataset.state, reason: $("loop-reason").textContent,
                      secondary: $("loop-secondary").textContent, dots: $("loop-dots").hidden };
      $("loop-secondary").onclick();
      return { first, bugs: !$("loopbugs").hidden, title: $("bugs-title").textContent,
               list: $("bugs-list").textContent };
    """, routes=routes())
    assert out["first"]["label"] == "Start" and out["first"]["disabled"] is True
    assert out["first"]["state"] == "refused"
    assert "2 loop-bugs open" in out["first"]["reason"]
    assert out["first"]["secondary"] == "Loop-bugs"
    assert out["first"]["dots"] is True, "a stopped loop has nothing to kill"
    assert out["bugs"] is True and out["title"] == "Loop-bugs · beta"
    assert "#40 loop-bug:dispatch" in out["list"] and "#41 loop-bug:landing" in out["list"]


@pytest.mark.node_bound
def test_the_rule_module_holds_the_aggregate_and_the_primary_alone():
    src = LOOP_JS.read_text()
    assert run_js(src, 'loopAggregate([{state:"on"},{state:"on"},{state:"off"}])') == "2 on · 1 off"
    assert run_js(src, "loopAggregate([])") == "No project"
    assert run_js(src, 'loopPrimary({state:"draining"}, true, "").label') == "Cancel drain"
    assert run_js(src, 'loopPrimary({state:"on"}, true, "").label') == "Drain"
    refused = run_js(src, 'loopPrimary({state:"off", bugs:2}, true, "")')
    assert refused["state"] == "refused" and "2 loop-bugs open" in refused["sub"]
    assert "fetch(" not in src and "POST" not in src


def test_a_change_line_names_what_it_cannot_read():
    """AC1 wants time, source and author on every change. An old audit line that carries none of them says so in each
    column, so the row never reads as an anonymous change (ADR-0137: the rule lives in the module, not the view)."""
    src = LOOP_JS.read_text()
    assert run_js(src, 'loopChangeLine({})') == "unknown author · unknown source · time not read · unknown action"
    assert run_js(src, 'loopChangeLine({who:"owner", action:"start"})') == "owner · unknown source · time not read · start"
    assert run_js(src, "loopChangeLine(null)") == "no change yet"


def test_the_loop_rules_are_inlined_and_the_page_names_no_all_loops_switch(hub):
    assert LOOP_JS.read_text() in hub.MOBILE_PAGE
    assert "/*LOOP_RULES*/" not in hub.MOBILE_PAGE
    assert "data-loop-all" not in hub.MOBILE_PAGE


# --- the fact the pages draw comes from the hub's real profile discovery, never a hand-built stub ---

def _profile(dirpath, slug, repo, project, checkout):
    dirpath.mkdir(parents=True, exist_ok=True)
    (dirpath / f"{slug}.sh").write_text(
        f': "${{SG_REPO:={repo}}}"\n: "${{SG_OWNER:={repo.split("/")[0]}}}"\n'
        f': "${{SG_PROJECT:={project}}}"\n: "${{SG_CHECKOUT:={checkout}}}"\n')


def test_the_loop_fact_is_read_from_every_projects_own_state(monkeypatch, tmp_path):
    """The phone's pages are drawn from the hub's loop fact, so the fact is tested against the real seam: two profiles in
    a real profiles directory, each with its own state directory. A regression in `address.sh --list` parsing or in the
    per-project context read shows here, not only on a live hub (AC1; #1599)."""
    import os
    base, prof = tmp_path / "base", tmp_path / "profiles"
    _profile(prof, "a", "x/a", 1, tmp_path / "a")
    _profile(prof, "b", "x/b", 2, tmp_path / "b")
    for k, v in (("SG_REPO", "x/a"), ("SG_OWNER", "x"), ("SG_PROJECT", "1"), ("SG_PROFILE", "a"),
                 ("SG_CHECKOUT", str(tmp_path / "a")), ("SG_STATE", str(base / "a")),
                 ("SG_HUB_DIR", str(tmp_path / "hub")), ("HOME", str(tmp_path)),
                 ("SG_PROFILES_DIR", str(prof)), ("SG_STATE_BASE", str(base))):
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("SG_SHARED", raising=False)
    mod = _module("hub_loops_fact", HUB_PY)
    for slug in ("a", "b"):
        (base / slug / "auto").mkdir(parents=True, exist_ok=True)
    (base / "b" / "watchd.pid").write_text(f"{os.getpid()}\n")
    (base / "b" / "loop-audit.jsonl").write_text(json.dumps(
        {"at": "2026-11-02T09:00:00-03:00", "who": "owner", "client": "phone", "action": "drain"}) + "\n")
    mod.tailnet_block = lambda checkout=None: {"host": "", "allow": [], "loop_write": False}
    v = mod.loop_view(mod.Access(True, "", "", False))
    ps = {p["slug"]: p for p in v["projects"]}
    assert set(ps) == {"a", "b"}, "every profile the seam lists is one row of the fact"
    assert ps["a"]["own"] is True and ps["b"]["own"] is False
    assert ps["a"]["state"] == "off" and ps["b"]["state"] == "on"
    assert ps["b"]["change"]["client"] == "phone"
    assert [c["project"] for c in v["changes"]] == ["b"], "the change log carries the project that changed"
