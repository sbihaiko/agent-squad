"""The Now layers and the project sheet give each color one meaning (SPEC 2660, #2665).

An off loop's name wears the text color, so Fault Red keeps the meaning of a fault. "All projects" in the project
sheet wears the accent, so a link never reads as a healthy state. The Loop-bugs layer with nothing open says
"No open loop-bug in this project." in the muted color, and the layer writes that line where it draws itself, so a
redraw keeps it. Every color resolves to a DESIGN.md token through its custom property; no surface names a raw value.

The page is the one the hub serves, composed from `hub-mobile.html` and the client modules it inlines, and booted
under node over the stub DOM of `tests/hub_mobile_stub.py`. The tests read the screens the page draws and the rules
it paints them with, never a private helper.
"""
import json
import pathlib
import re

import pytest

from tests.hub_mobile_stub import HUB_PY, _module, run_page

ROOT = pathlib.Path(__file__).resolve().parent.parent
DESIGN = ROOT / "DESIGN.md"

# The role each page token plays in DESIGN.md, so a test reads a color as its meaning and never as a value.
ROLE = {"fg": "ink", "mute": "ink-muted", "acc": "signal-blue", "ok": "state-ok", "warn": "state-warn", "err": "state-err"}


def fact():
    """One project that is off with no open loop-bug, and one that runs: the two states a loop row can carry."""
    return {"projects": [
        {"slug": "alpha", "repo": "x/a", "own": True, "state": "off",
         "live": {"runs": [], "gates": []}, "landing": None, "change": None,
         "bugs": 0, "bugs_open": [], "write": True, "reason": ""},
        {"slug": "beta", "repo": "x/b", "own": False, "state": "on",
         "live": {"runs": ["/r/x/run-1"], "gates": []}, "landing": {"issue": 7},
         "change": {"at": "2026-11-02T09:00:00-03:00", "who": "owner", "client": "phone", "action": "start"},
         "bugs": 0, "bugs_open": [], "write": True, "reason": ""},
    ], "changes": []}


def routes():
    return {"/api/loop": [{"body": json.dumps(fact()), "status": 200}]}


@pytest.fixture
def hub(monkeypatch, tmp_path):
    """The page module, loaded in a scoped environment: a test never leaks an SG_* value into the next one."""
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("HOME", str(tmp_path)),
                 ("SG_CHECKOUT", str(tmp_path)), ("SG_HUB_DIR", str(tmp_path / "hub"))):
        monkeypatch.setenv(k, v)
    for k in ("SG_STATE_BASE", "SG_SHARED", "SG_PROFILE", "SG_STATE", "SG_PROFILES_DIR"):
        monkeypatch.delenv(k, raising=False)
    return _module("hub_mobile_now_colors", HUB_PY)


def _css(page):
    """The stylesheet the served page carries, tokens included."""
    m = re.search(r"<style>([\s\S]*?)</style>", page)
    assert m, "the served page carries no stylesheet"
    return m.group(1)


def _decls(css, selector):
    """The declarations of the rule that names this selector, alone or in a group."""
    for sel, body in re.findall(r"([^{}]+)\{([^}]*)\}", re.sub(r"/\*[\s\S]*?\*/", "", css)):
        if selector in [s.strip() for s in sel.split(",")]:
            return dict(re.findall(r"([\w-]+)\s*:\s*([^;}]+)", body))
    raise AssertionError(f"the page serves no rule for {selector}")


def _tokens(css):
    root = re.search(r":root\{([^}]*)\}", css)
    assert root, "the page carries no token block"
    return dict(re.findall(r"--([\w-]+):([^;}]+)", root.group(1)))


def _design():
    """The color roles DESIGN.md declares, as a name to value map."""
    text = DESIGN.read_text()
    return dict(re.findall(r"^  ([\w-]+):\s*\"(#[0-9a-fA-F]{3,8})\"", text, re.M))


def _token(css, selector):
    """The one DESIGN.md token a rule paints with. A raw color, a missing token or a token that drifted from DESIGN.md
    fails here, so every color on the screen answers to the design system."""
    value = _decls(css, selector).get("color", "").strip()
    m = re.fullmatch(r"var\((--[\w-]+)\)", value)
    assert m, f"{selector or 'the rule'} paints {value!r}; a surface names a token, never a raw color"
    name = m.group(1)[2:]
    tokens = _tokens(css)
    assert name in tokens, f"{selector} names {m.group(1)}, which the token block does not declare"
    role = ROLE.get(name)
    if role:
        assert tokens[name].strip() == _design()[role], \
            f"--{name} is {tokens[name]}, but DESIGN.md draws {role} as {_design()[role]}"
    return name


@pytest.mark.node_bound
def test_an_off_loops_name_wears_the_text_color_so_fault_red_keeps_one_meaning(hub):
    out = run_page(hub.MOBILE_PAGE, """
      await __tick(10);
      $("loops-open").onclick();
      const rows = $("loops-list").querySelectorAll(".looprow");
      const off = rows.find(b => b.dataset.loopProject === "alpha");
      const on = rows.find(b => b.dataset.loopProject === "beta");
      return { off: off.className, name: off.querySelector("b").textContent,
               on: on.className, onName: on.querySelector("b").textContent };
    """, routes=routes())
    assert out["off"] == "looprow off" and out["name"] == "alpha", out
    assert out["on"] == "looprow on" and out["onName"] == "beta", out
    css = _css(hub.MOBILE_PAGE)
    assert _token(css, ".looprow.off b") == "fg", "an off loop's name is not drawn in the text color"
    assert _token(css, ".looprow.on b") == "ok", "a running loop keeps its healthy green"


@pytest.mark.node_bound
def test_all_projects_wears_the_accent_color_not_a_healthy_state(hub):
    out = run_page(hub.MOBILE_PAGE, """
      await __tick(10);
      switcherPaint($("pswl"), { repos: [{ slug: "alpha", repo: "x/a", live: [], alert: "" }] }, "alpha", "/mobile");
      return { html: String($("pswl").innerHTML || "") };
    """, routes={})
    rows = [(cls, " ".join(re.sub(r"<[^>]+>", " ", body).split()))
            for cls, body in re.findall(r'<a class="psw-r([^"]*)"[^>]*>(.*?)</a>', out["html"])]
    assert len(rows) == 2 and rows[0][1].startswith("alpha "), out["html"]
    assert rows[-1][1] == "All projects", out["html"]
    assert "psw-all" in rows[-1][0], "the sheet ends with the link to every project"
    assert _token(_css(hub.MOBILE_PAGE), "#pswl .psw-all") == "acc", \
        '"All projects" is not drawn in the accent color'


@pytest.mark.node_bound
def test_the_bugs_layer_with_nothing_open_says_so_in_the_muted_color_and_keeps_it_on_a_redraw(hub):
    out = run_page(hub.MOBILE_PAGE, """
      await __tick(10);
      openLayer("loop", { slug: "alpha" });
      openLayer("bugs");
      const first = { msg: $("bugs-msg").textContent, list: $("bugs-list").textContent,
                      title: $("bugs-title").textContent, open: !$("loopbugs").hidden };
      openLayer("bugs");   // the layer draws itself again, as a return to its entry does
      return { first, again: $("bugs-msg").textContent };
    """, routes=routes())
    assert out["first"]["open"] is True, "the Loop-bugs layer did not open"
    assert out["first"]["title"] == "Loop-bugs · alpha", out["first"]
    assert out["first"]["list"] == "", "a project with no open loop-bug draws no row"
    assert out["first"]["msg"] == "No open loop-bug in this project.", out["first"]["msg"]
    assert out["again"] == "No open loop-bug in this project.", "a redraw of the layer lost the message"
    assert _token(_css(hub.MOBILE_PAGE), "#bugs-msg") == "mute", \
        "the empty Loop-bugs message is not drawn in the muted color"


def test_a_scrolling_loop_layer_ends_above_the_tab_bar(hub):
    """The tab bar paints over every layer (56 px plus the home indicator). A layer that scrolls under it keeps a
    bottom inset, so its last line is reachable in landscape, where the Loops list and a long Loop-bugs list overflow."""
    css = _css(hub.MOBILE_PAGE)
    bar = re.search(r"nav button\{[^}]*min-height:(\d+)px", css)
    assert bar, "the tab bar names no height"
    for selector in ("#loops-content", "#bugs-content"):
        pad = _decls(css, selector).get("padding-bottom", "")
        m = re.search(r"calc\((\d+)px \+ env\(safe-area-inset-bottom\)\)", pad)
        assert m, f"{selector} leaves no room under its last line: padding-bottom is {pad!r}"
        assert int(m.group(1)) >= int(bar.group(1)), \
            f"{selector} clears {m.group(1)} px of a {bar.group(1)} px tab bar"
