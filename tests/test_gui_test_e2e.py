"""GUI test squad e2e suite (local-only): a 3-step script against a fake adapter,
the renderer, and the reverse-drift case against MesenAI's committed files."""
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from squad.gui_test import validate
from squad.gui_test_render import render
from squad.gui_test_run import run
from tests.dashboard_helpers import run_node
from tests.gui_test_helpers import script, step

FORK = Path(__file__).resolve().parent.parent


class FakeAdapter:
    """Home screen with one card; Right is a no-op, A opens the library."""

    def __init__(self):
        self.screen, self.focus, self.log = "play.home", "play.home.open-rom", []

    def inject(self, name, args):
        self.log.append((name, args))
        if name == "pad.press" and args.get("button") == "A":
            self.screen = "play.library"

    def wait(self, condition, timeout):
        return "met" if condition is None or self._holds(condition) else "timeout"

    def _holds(self, cond):
        key, _, want = cond.partition(" == ")
        return {"ui.screen": self.screen, "ui.focused": self.focus}[key] == want

    def check(self, name, args):
        if args["is"].endswith("ghost"):
            return "unknown id"
        return name == "ui.focused" and self.focus == args["is"] or \
            name == "ui.screen" and self.screen == args["is"]


def three_steps():
    return script(
        step("b1.right-holds"),
        step("b1.open-library",
             action={"name": "pad.press", "args": {"button": "A", "ticks": 4}},
             wait={"check": "ui.screen == play.library", "timeout_ticks": 10},
             check={"name": "ui.screen", "args": {"is": "play.library"}}),
        step("b1.ghost", check={"name": "ui.focused", "args": {"is": "play.ghost"}},
             wait={"timeout_ticks": 2}),
    )


def test_three_step_script_runs_against_a_fake_adapter():
    d = three_steps()
    validate(d)
    results = run(d, FakeAdapter())
    assert [(r["id"], r["verdict"]) for r in results] == [
        ("b1.right-holds", "passed"),
        ("b1.open-library", "passed"),
        ("b1.ghost", "failed (unknown id)"),
    ]


def test_wait_timeout_is_a_failed_step_naming_the_wait():
    d = script(step("b1.t", wait={"check": "ui.screen == nowhere", "timeout_ticks": 3}))
    [r] = run(d, FakeAdapter())
    assert r["verdict"] == "failed (timeout waiting for ui.screen == nowhere)"


def test_rendering_is_deterministic_and_lists_every_step():
    d = three_steps()
    md = render(d)
    assert md == render(d)
    for sid in ("b1.right-holds", "b1.open-library", "b1.ghost"):
        assert f"`{sid}`" in md


def _mesenai():
    root = os.environ.get("MESENAI_REPO")
    assert root, "set MESENAI_REPO to the MesenAI checkout holding the pilot script"
    return Path(root)


def test_reverse_drift_committed_markdown_equals_live_render():
    docs = _mesenai() / "docs" / "validation" / "process"
    pilots = sorted(docs.glob("*.gui-test.json"))
    assert pilots, "no committed pilot script"
    for p in pilots:
        d = json.loads(p.read_text(encoding="utf-8"))
        validate(d)
        assert render(d) == p.with_suffix(".md").read_text(encoding="utf-8"), p.name


def test_reverse_drift_vendored_renderer_equals_fork_file_at_pinned_commit():
    vendored = _mesenai() / "scripts" / "vendor" / "agent_squad" / "gui_test_render.py"
    text = vendored.read_text(encoding="utf-8")
    header = {k: v for k, v in (line[2:].split(": ", 1) for line in text.splitlines()
                                if line.startswith("# ") and ": " in line[2:])}
    body = text.split("# ---- end vendored header ----\n", 1)[1].encode()
    pinned = subprocess.run(["git", "-C", str(FORK), "show", f"{header['source-commit']}:squad/gui_test_render.py"],
                            capture_output=True, check=True).stdout
    assert hashlib.sha256(body).hexdigest() == hashlib.sha256(pinned).hexdigest()
    assert header["sha256"] == hashlib.sha256(pinned).hexdigest()


# --- the dashboard draws a script as a step diagram, without running anything (#1180) ---

def _diagram(d):
    return run_node([{"op": "scriptdiagram", "script": d}])[0]


def _svg(d):
    return run_node([{"op": "scriptsvg", "script": d}])[0]


def _two_batches():
    return script() | {"batches": [
        {"id": "b1", "setup": [step("b1.s")], "teardown": [], "steps": [
            step("b1.a", expect="first <step> & more"),
            step("b1.b", mode="manual", variants={"window.mode": "fullscreen"}),
        ]},
        {"id": "b2", "setup": [], "teardown": [step("b2.t")], "steps": [step("b2.a")]},
    ]}


def test_diagram_has_one_node_per_step_in_order_grouped_by_batch():
    m = _diagram(_two_batches())
    assert [b["id"] for b in m["batches"]] == ["b1", "b2"]
    assert [[(s["label"], [n["id"] for n in s["nodes"]]) for s in b["sections"]] for b in m["batches"]] == [
        [("setup", ["b1.s"]), ("steps", ["b1.a", "b1.b"])],
        [("steps", ["b2.a"]), ("teardown", ["b2.t"])],
    ]


def test_diagram_node_carries_step_text_variant_tags_and_manual_flag():
    nodes = {n["id"]: n for b in _diagram(_two_batches())["batches"] for s in b["sections"] for n in s["nodes"]}
    assert nodes["b1.a"]["text"] == "first <step> & more"
    assert nodes["b1.a"]["tags"] == ["all"]
    assert nodes["b1.b"]["tags"] == ["window.mode=fullscreen"]
    assert (nodes["b1.a"]["manual"], nodes["b1.b"]["manual"]) == (False, True)


def test_diagram_svg_draws_each_step_escaped_with_manual_distinct():
    svg = _svg(_two_batches())
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    assert svg.count("data-step=") == 5
    assert "first &lt;step&gt; &amp; more" in svg and "first <step>" not in svg
    assert re.search(r'class="step manual"[^>]*data-step="b1\.b"', svg)
    assert re.search(r'class="step auto"[^>]*data-step="b1\.a"', svg)
    assert svg.index('data-step="b1.a"') < svg.index('data-step="b1.b"') < svg.index('data-step="b2.a"')
    assert svg.count('class="batch"') == 2 and "window.mode=fullscreen" in svg


def test_diagram_refuses_an_unknown_format():
    assert "unknown format" in _diagram({"format": "gui-test/9"})["error"]


def test_pilot_script_opens_as_a_diagram_and_every_node_shows_its_text():
    docs = _mesenai() / "docs" / "validation" / "process"
    pilots = sorted(docs.glob("*.gui-test.json"))
    assert pilots, "no committed pilot script"
    for p in pilots:
        d = json.loads(p.read_text(encoding="utf-8"))
        svg = _svg(d)
        steps = [s for b in d["batches"] for k in ("setup", "steps", "teardown") for s in b[k]]
        assert svg.count("data-step=") == len(steps)
        for s in steps:
            assert f'data-step="{s["id"]}"' in svg
            assert s["expect"].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")[:30] in svg
        assert svg.count('class="step manual"') == sum(s["mode"] == "manual" for s in steps)
