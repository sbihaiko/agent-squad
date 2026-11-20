"""GUI test squad e2e suite (local-only): a 3-step script against a fake adapter,
the renderer, and the reverse-drift case against MesenAI's committed files.

A test that reads a MesenAI checkout on this runner carries `@pytest.mark.host_bound`. `tests/conftest.py` deselects
every host-bound test when `MESENAI_REPO`/`MESENAI_ROOT` name no directory on disk: deselected, never skipped
(ADR-0018 item 6, ADR-0062 items 2 and 3), and the run names them. The rest of the file runs without the host.
"""
import copy
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from squad.gui_test import ScriptError, validate
from squad.gui_test_adapter import FakeAdapter as TickingFake
from squad.gui_test_render import render
from squad.gui_test_run import run
from tests import real_app_guard as guard
from tests.dashboard_helpers import run_node
from tests.gui_test_helpers import script, step

FORK = Path(__file__).resolve().parent.parent


# The shared dashboard runner is started once per session by tests/conftest.py `_one_dashboard_runner`, never here: a
# file-level teardown would stop the session's process, and the next file's first call would pay the cold start (#2580).


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


def test_three_step_script_runs_against_a_fake_adapter(tmp_path):
    d = three_steps()
    validate(d)
    results = run(d, TickingFake(tmp_path))
    assert [(r["id"], r["verdict"]) for r in results] == [
        ("b1.right-holds", "passed"),
        ("b1.open-library", "passed"),
        ("b1.ghost", "failed (unknown id)"),
    ]


def test_wait_timeout_is_a_failed_step_naming_the_wait(tmp_path):
    d = script(step("b1.t", wait={"check": "ui.screen == nowhere", "timeout_ticks": 3}))
    [r] = run(d, TickingFake(tmp_path))
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


@pytest.mark.host_bound(reason="needs the MesenAI checkout to render the pilot script live (MESENAI_REPO)")
def test_reverse_drift_committed_markdown_equals_live_render():
    docs = _mesenai() / "docs" / "validation" / "process"
    pilots = sorted(docs.glob("*.gui-test.json"))
    assert pilots, "no committed pilot script"
    for p in pilots:
        d = json.loads(p.read_text(encoding="utf-8"))
        validate(d)
        assert render(d) == p.with_suffix(".md").read_text(encoding="utf-8"), p.name


@pytest.mark.host_bound(reason="needs the MesenAI checkout at its pinned commit to compare the vendored renderer (MESENAI_REPO)")
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


@pytest.mark.host_bound(reason="needs the MesenAI pilot script to open as a diagram (MESENAI_REPO)")
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
            # Mirror the renderer's escXml (& < > and ") so a quoted expect still matches.
            want = s["expect"].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
            assert want[:30] in svg
            assert s["expect"].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")[:30] in svg
        assert svg.count('class="step manual"') == sum(s["mode"] == "manual" for s in steps)


# --- #1181: the runner against a fake adapter: a step runs, captures and shows `executed` ---

from tests.gui_test_contract import CONTRACT, check_contract  # noqa: E402


def _three_step_run(tmp_path):
    d = script(
        step("b1.right-holds"),
        step("b1.open-library", action={"name": "pad.press", "args": {"button": "A", "ticks": 4}},
             wait={"check": "ui.screen == play.library", "timeout_ticks": 10},
             check={"name": "ui.screen", "args": {"is": "play.library"}}),
        step("b1.still-library", action={"name": "pad.press", "args": {"button": "Right", "ticks": 4}},
             wait={"check": "ui.screen == play.library", "timeout_ticks": 10},
             check={"name": "ui.screen", "args": {"is": "play.library"}}),
    )
    adapter = TickingFake(tmp_path)
    events = []
    validate(d, adapter.capabilities())
    return d, adapter, events, run(d, adapter, record=events.append)


def test_three_step_script_shows_three_nodes_each_with_its_screenshot(tmp_path):
    _, _, events, results = _three_step_run(tmp_path)
    assert [r["verdict"] for r in results] == ["passed"] * 3
    nodes = run_node([{"op": "guirun", "events": events}])[0]
    assert [(n["id"], n["status"], n["verdict"]) for n in nodes] == [
        ("b1.right-holds", "executed", "passed"), ("b1.open-library", "executed", "passed"),
        ("b1.still-library", "executed", "passed")]
    for n in nodes:
        assert Path(n["capture"]["path"]).read_bytes().startswith(b"\x89PNG")
        assert len(n["capture"]["sha256"]) == 64


def test_step_capture_and_verdict_events_are_replay_events_in_order(tmp_path):
    _, _, events, _ = _three_step_run(tmp_path)
    assert [(e["event"], e["node"]) for e in events[:3]] == [
        ("gui_step", "b1.right-holds"), ("gui_capture", "b1.right-holds"), ("gui_verdict", "b1.right-holds")]
    assert [e["pos"] for e in events] == list(range(len(events)))
    cut = run_node([{"op": "guirun", "events": events, "cursor": 4}])[0]
    assert [(n["id"], n["status"]) for n in cut] == [("b1.right-holds", "executed"), ("b1.open-library", "running")]


def test_no_capture_is_taken_before_the_wait_condition_is_met(tmp_path):
    _, adapter, _, _ = _three_step_run(tmp_path)
    assert adapter.captured_screens == ["play.home", "play.library", "play.library"]


def test_a_wait_timeout_takes_no_capture_and_the_node_is_not_executed(tmp_path):
    d = script(step("b1.t", wait={"check": "ui.screen == nowhere", "timeout_ticks": 3}))
    adapter, events = TickingFake(tmp_path), []
    [r] = run(d, adapter, record=events.append)
    assert r["verdict"].startswith("failed (timeout")
    assert adapter.captured_screens == []
    assert [e["event"] for e in events] == ["gui_step", "gui_verdict"]
    [n] = run_node([{"op": "guirun", "events": events}])[0]
    assert (n["status"], n["capture"]) == ("failed", None)


def test_validator_rejects_before_running_what_the_adapter_does_not_advertise(tmp_path):
    d = script(step("b1.k", action={"name": "key.press", "args": {"key": "x", "ticks": 1}}),
               step("b1.c", check={"name": "fs.exists", "args": {"is": "x"}}))
    d["requires"] = {"actions": ["pad.press", "key.press"], "checks": ["ui.screen", "fs.exists"]}
    adapter = TickingFake(tmp_path)
    with pytest.raises(ScriptError) as e:
        validate(d, adapter.capabilities())
    assert "b1.k: adapter does not advertise action key.press" in e.value.problems
    assert "b1.c: adapter does not advertise check fs.exists" in e.value.problems
    assert adapter.log == []


def test_the_fake_adapter_passes_the_shared_contract_suite(tmp_path):
    check_contract(lambda: TickingFake(tmp_path))
    assert {"launch", "inject", "check", "wait", "capture", "teardown", "capabilities"} <= set(CONTRACT)


# --- #1183: the emulator adapter (pad only): fixtures, launch, hook, capture ---

import sys  # noqa: E402

from squad.gui_test_emulator import EmulatorAdapter, FixtureError, run_launched  # noqa: E402

STUB = [sys.executable, str(FORK / "tests" / "fake_hook_app.py")]
PILOT = "play-pad-only.gui-test.json"


def _emulator(tmp_path, **kw):
    return EmulatorAdapter(tmp_path / "out", app_cmd=kw.pop("app_cmd", STUB), library=kw.pop("library", tmp_path / "lib"),
                           work_dir=tmp_path / "work", **kw)


@pytest.mark.heavy
def test_the_emulator_adapter_passes_the_shared_contract_suite(tmp_path):
    made = []

    def factory():
        a = _emulator(tmp_path / f"c{len(made)}")
        made.append(a)
        return a
    try:
        check_contract(factory)
    finally:
        for a in made:
            a.teardown()
    assert len(made) == len(CONTRACT)


# --- #1780: the adapter and the runner drive the verbs the hook answers since #1779 --------------------------

# The verbs ADR-0272's hook answers since #1779 (hook version 2), in the order the adapter advertises them: the
# pad press it always had, the release and the chord beside it, the keyboard, and the goal read.
VERBS_1779 = ["pad.press", "pad.release", "pad.chord", "key.press", "nav.goal"]


def _verbs_asked(path):
    """The actions the stub application was asked for, in order: one JSON object per line, appended by the fixture."""
    return [json.loads(line)["action"] for line in path.read_text().splitlines()]


def test_the_emulator_adapter_advertises_the_verbs_the_hook_answers_since_1779(tmp_path):
    """#1780: the hook answers `pad.release`, `pad.chord` and `key.press`, and reads a goal's state and moves under
    `nav.goal`, since #1779. An adapter that drives it advertises them, and a verb it does not advertise is the
    capability the derivation names when it turns the step that writes it into a case a person walks. What it reads
    is what the hook answers and no more: the checks are the application's own UI state."""
    caps = _emulator(tmp_path).capabilities()
    assert caps["actions"] == VERBS_1779
    assert set(caps["checks"]) == {"ui.screen", "ui.focused", "ui.visible", "ui.dialogs",
                                   "ui.ring", "ui.footer", "ui.surface", "ui.items"}
    assert caps["input_families"] == ["ticks"]


def test_the_adapter_drives_the_new_verbs_over_the_hook(tmp_path):
    """#1780: each verb reaches the hook as its own action, and the keyboard's own press is the one that moves the
    application - the chord and the release are answered without one. A refusal here is the adapter not asking."""
    verbs = tmp_path / "verbs.jsonl"
    d = script(
        step("b1.open", action={"name": "pad.press", "args": {"button": "A", "ticks": 4}},
             wait={"check": "ui.screen == play.library", "timeout_ticks": 60},
             check={"name": "ui.screen", "args": {"is": "play.library"}}),
        step("b1.enter", action={"name": "key.press", "args": {"key": "Enter", "ticks": 2}},
             wait={"check": "ui.screen == play.home", "timeout_ticks": 60},
             check={"name": "ui.screen", "args": {"is": "play.home"}}),
        step("b1.chord", action={"name": "pad.chord", "args": {"buttons": ["Select", "Start"], "ticks": 4}},
             wait={"check": "ui.surface == none", "timeout_ticks": 8},
             check={"name": "ui.surface", "args": {"is": "none"}}),
        step("b1.release", action={"name": "pad.release", "args": {"button": "A", "ticks": 1}},
             wait={"check": "ui.surface == none", "timeout_ticks": 8},
             check={"name": "ui.surface", "args": {"is": "none"}}),
    )
    d["requires"] = {"actions": VERBS_1779, "checks": ["ui.screen", "ui.surface"]}
    d["variants"] = {"window.mode": ["windowed"]}
    adapter = _emulator(tmp_path, env={"FAKE_HOOK_VERBS": str(verbs)})
    validate(d, adapter.capabilities())
    results = run_launched(d, adapter)
    assert [(r["id"], r["verdict"]) for r in results] == [("b1.open", "passed"), ("b1.enter", "passed"),
                                                          ("b1.chord", "passed"), ("b1.release", "passed")]
    assert _verbs_asked(verbs) == ["pad.press", "key.press", "pad.chord", "pad.release"]


def _goal_script():
    """A `nav.goal` setup step and the case it establishes: the pilot's `after-game.reach-home` shape (#1780)."""
    d = script(step("b1.reach-library", role="setup",
                    precondition="by hand: the screen is play.home",
                    action={"name": "nav.goal", "args": {"goal": "ui.screen == play.library"}},
                    wait={"check": "ui.screen == play.library", "timeout_ticks": 60},
                    check={"name": "ui.screen", "args": {"is": "play.library"}}),
               step("b1.library-open", precondition="ui.screen == play.library",
                    wait={"check": "ui.screen == play.library", "timeout_ticks": 8},
                    check={"name": "ui.screen", "args": {"is": "play.library"}}))
    d["batches"][0]["setup"] = [d["batches"][0]["steps"].pop(0)]
    d["requires"] = {"actions": ["pad.press", "nav.goal"], "checks": ["ui.screen"]}
    d["variants"] = {"window.mode": ["windowed"]}
    return d


def test_a_goal_step_asks_the_hook_and_waits_for_a_chooser(tmp_path):
    """#1780: `nav.goal` is the hook's read of the state a move is chosen from and the moves it accepts (ADR-0271
    items 1-2), never a press. An adapter that advertises it is asked; with no chooser (CI, ADR-0271 Decision 6 A)
    the step is `pending`, and asking is what keeps it from being a red one: injected as a step of its own the read
    moves nothing, so the wait behind it would time out."""
    verbs = tmp_path / "verbs.jsonl"
    d = _goal_script()
    adapter = _emulator(tmp_path, env={"FAKE_HOOK_VERBS": str(verbs)})
    validate(d, adapter.capabilities())
    results = run_launched(d, adapter)
    assert (results[0]["id"], results[0]["verdict"]) == ("b1.reach-library", "pending")
    # The goal step asks the read and nothing else; the press after it is the case that follows, which the pending
    # setup never established.
    assert _verbs_asked(verbs) == ["nav.goal", "pad.press"]


def test_a_goal_step_the_adapter_drives_itself_is_executed_not_pending(tmp_path):
    """#1780: the read above is one kind of adapter, and a stale fake is the other: an adapter that lists `nav.goal`
    among the actions it runs drives the goal itself, so the step's own wait and check are what decide it, exactly as
    they decide every other action that adapter runs. The #1193-#1197 pilot fixtures were derived against fakes of
    that kind - every `*.reach-*` setup step of theirs is `automated`, and their assertions hold the run's `pending`
    set to the fixture's `manual` one - so routing an advertised `nav.goal` to the chosen-by-Nobody path turns those
    setups into rows nobody established and the batches red."""
    from tests.gui_test_pilot_fake import PilotFake

    d = _goal_script()
    adapter = PilotFake(tmp_path)
    validate(d, adapter.capabilities())
    results = run_launched(d, adapter)
    assert [(r["id"], r["verdict"]) for r in results] == [("b1.reach-library", "passed"),
                                                          ("b1.library-open", "passed")]
    assert ("nav.goal", {"goal": "ui.screen == play.library"}) in adapter.log


def test_a_goal_is_reached_through_the_hooks_own_moves_and_the_navigators_choice(tmp_path):
    """#1780: the hook hands the moves it accepts in the duration family its own clock allows, and the runner's
    Navigator chooses among them (ADR-0271 items 2-3). Confirm on the Home opens the library, so a chooser that
    takes the hook's own navigation move reaches the goal the pilot's setup names."""
    from squad.gui_test_goal import Navigator

    d = _goal_script()
    adapter = _emulator(tmp_path, env={"FAKE_HOOK_VERBS": str(tmp_path / "verbs.jsonl")})
    validate(d, adapter.capabilities())
    events = []
    navigator = Navigator(lambda state, options: {"choice": "pad.press Confirm"}, max_decisions=4)
    results = run_launched(d, adapter, record=events.append, goal=navigator)
    assert [(r["id"], r["verdict"]) for r in results] == [("b1.reach-library", "passed"),
                                                          ("b1.library-open", "passed")]
    [decision] = [e for e in events if e["event"] == "gui_decision"]
    assert decision["options"] == ["pad.press Up", "pad.press Down", "pad.press Left", "pad.press Right",
                                   "pad.press Confirm", "pad.press Back"]


def test_the_runner_keeps_a_home_step_naming_one_of_those_verbs_automated(tmp_path):
    """#1780: the same capability list the run command validates against decides manual vs automated, so a step the
    adapter can now drive stays a case the runner executes instead of a row a person walks."""
    from squad.gui_test_subset import runnable

    caps = _emulator(tmp_path).capabilities()
    d = script(step("home.first-focus"),
               step("home.release", action={"name": "pad.release", "args": {"button": "A", "ticks": 2}},
                    wait={"timeout_ticks": 8}),
               step("home.chord", action={"name": "pad.chord", "args": {"buttons": ["Select", "Start"], "ticks": 4}},
                    wait={"timeout_ticks": 8}),
               step("home.key", action={"name": "key.press", "args": {"key": "Enter", "ticks": 2}},
                    wait={"timeout_ticks": 8}),
               step("home.second-focus"))
    d["requires"] = {"actions": caps["actions"], "checks": ["ui.screen", "ui.focused"]}
    d["variants"] = {"window.mode": ["windowed"]}
    out = runnable(d, caps)
    assert out["manual"] == []
    assert out["kept"] == len(d["batches"][0]["steps"])


def _slice_batch(b, ids):
    """Keep only the asked-for steps of `b`; its setup/teardown stay, the way `--case` keeps them (#1241).

    A batch's `setup`/`teardown` are steps too, and the runner runs `setup + steps` in the batch's own process
    (`squad.gui_test_supervise.run_supervised`). The pilot's `home` batch opens with a `home.first-focus` setup
    step (#1251) that waits for the focus its cases assume, so a slice of its cases still has to pass it; a slice
    that names it explicitly gets it back as a step of the continuation (`_pilot_for_the_real_app`).
    """
    b["steps"] = [s for s in b["steps"] if s["id"] in ids]
    return b


def _pilot_slice(ids):
    """The pilot's own steps by id, in their own batches, with `requires` trimmed to what the adapter advertises."""
    d = json.loads((_mesenai() / "docs" / "validation" / "process" / PILOT).read_text(encoding="utf-8"))
    for b in d["batches"]:
        _slice_batch(b, ids)
    d["batches"] = [b for b in d["batches"] if b["steps"]]
    d["requires"] = {"actions": ["pad.press"], "checks": ["ui.screen", "ui.focused", "ui.visible", "ui.dialogs"]}
    d["variants"] = {"window.mode": ["windowed"]}
    return d


def test_a_script_runs_unattended_through_the_adapter_with_a_capture_per_step(tmp_path):
    d = script(step("b1.home"), step("b1.open", action={"name": "pad.press", "args": {"button": "A", "ticks": 2}},
                                     wait={"check": "ui.screen == play.library", "timeout_ticks": 40},
                                     check={"name": "ui.screen", "args": {"is": "play.library"}}))
    d["requires"]["actions"] = ["pad.press"]
    d["variants"] = {"window.mode": ["windowed"]}
    events = []
    adapter = _emulator(tmp_path)
    validate(d, adapter.capabilities())
    results = run_launched(d, adapter, record=events.append)
    assert [(r["id"], r["verdict"]) for r in results] == [("b1.home", "passed"), ("b1.open", "passed")]
    nodes = run_node([{"op": "guirun", "events": events}])[0]
    assert [(n["id"], n["status"]) for n in nodes] == [("b1.home", "executed"), ("b1.open", "executed")]
    for n in nodes:
        assert Path(n["capture"]["path"]).read_bytes().startswith(b"\x89PNG")
    assert adapter.teardown() == []


def test_a_run_reads_what_the_player_sees_through_the_adapter(tmp_path):
    """#1282: the ring, the action bar, the open surface and the focused list's entries, driven through the
    runner against the stub application - each one answered from the hook's own UI state, never a pixel."""
    d = script(
        step("b1.ring", check={"name": "ui.ring", "args": {"is": "play.home.open-rom"}}),
        step("b1.bar", check={"name": "ui.footer", "args": {"is": "confirm"}}),
        step("b1.surface", check={"name": "ui.surface", "args": {"is": "none"}}),
        step("b1.open", action={"name": "pad.press", "args": {"button": "A", "ticks": 2}},
             wait={"check": "ui.screen == play.library", "timeout_ticks": 40},
             check={"name": "ui.surface", "args": {"is": "play.library"}}),
        # The library is up: its own wait, or the runner's default (the Home's focus) would time out.
        step("b1.items", wait={"check": "ui.screen == play.library", "timeout_ticks": 40},
             check={"name": "ui.items", "args": {"contains": "play.library.tile.Contra (USA).nes"}}),
        step("b1.bar-again", wait={"check": "ui.screen == play.library", "timeout_ticks": 40},
             check={"name": "ui.footer", "args": {"contains": "back"}}),
    )
    d["requires"] = {"actions": ["pad.press"],
                     "checks": ["ui.ring", "ui.footer", "ui.surface", "ui.items", "ui.screen"]}
    d["variants"] = {"window.mode": ["windowed"]}
    adapter = _emulator(tmp_path)
    validate(d, adapter.capabilities())
    try:
        results = run_launched(d, adapter, record=lambda e: None)
        assert [(r["id"], r["verdict"]) for r in results] == [
            ("b1.ring", "passed"), ("b1.bar", "passed"), ("b1.surface", "passed"),
            ("b1.open", "passed"), ("b1.items", "passed"), ("b1.bar-again", "passed")]
    finally:
        adapter.teardown()


def test_a_player_visible_check_naming_an_unknown_id_is_unknown_id(tmp_path):
    """A renamed control or surface breaks the run loudly, never answers false (ADR-0272 item 6)."""
    adapter = _emulator(tmp_path)
    adapter.launch({})
    try:
        assert adapter.check("ui.surface", {"is": "play.renamed"}) == "unknown id"
        assert adapter.check("ui.footer", {"contains": "quit"}) == "unknown id"
        assert adapter.check("ui.ring", {"is": "play.home.renamed"}) == "unknown id"
        # The focused list's entries resolve the same way (#1282): the shared contract suite asks every
        # advertised check with one id, and a check that answers a bare False there reads as a state
        # mismatch instead of the renamed entry the run has to break on. An empty `is` stays a comparison.
        assert adapter.check("ui.items", {"is": "play.home.renamed"}) == "unknown id"
        assert adapter.check("ui.items", {"contains": "play.home.renamed"}) == "unknown id"
        assert adapter.check("ui.items", {"is": []}) is True
    finally:
        adapter.teardown()


def test_the_names_issue_1282_asks_for_outside_the_ui_namespace_are_refused_by_the_format(tmp_path):
    """`emu.paused`, `pad.lamps` and `pad.haptics` are names issue #1282 asks for that ADR-0272 item 7 puts
    outside a gui-test/1 check: `emu.*` is reserved (not part of v1) and `pad.*` is the action namespace.
    The adapter answers all three when asked directly; a script may not name them, and the validator says so
    rather than the run quietly checking nothing. Resolving this is the owner's - the ADR is not amended."""
    adapter = _emulator(tmp_path)
    adapter.launch({})
    try:
        assert adapter.check("emu.paused", {"is": False}) is True
        assert adapter.check("pad.lamps", {"port": 1, "is": "lit"}) is True
        assert adapter.check("pad.haptics", {"pad": 1, "at_least": 1}) is True
        assert adapter.check("pad.lamps", {"port": 9, "is": "lit"}) == "unknown id"
    finally:
        adapter.teardown()
    d = script(step("b1.paused", check={"name": "emu.paused", "args": {"is": False}}),
               step("b1.lamps", check={"name": "pad.lamps", "args": {"port": 1, "is": "lit"}}))
    with pytest.raises(ScriptError) as refused:
        validate(d, adapter.capabilities())
    assert "emu.paused is reserved and not part of gui-test/1" in str(refused.value)
    assert "unknown check namespace pad" in str(refused.value)


def test_a_missing_rom_fails_the_batch_clearly_before_any_launch(tmp_path):
    d = script(step("b1.s", precondition="fixture rom"),
               fixtures={"rom": {"path": "<library>/Contra (USA).nes", "sha1": "0" * 40}})
    d["requires"]["actions"] = ["pad.press"]
    adapter = _emulator(tmp_path)
    [r] = run_launched(d, adapter)
    assert r["verdict"].startswith("failed (fixture:") and "Contra (USA).nes" in r["verdict"] and "not found" in r["verdict"]
    assert r["capture"] is None
    assert not list((tmp_path / "work").glob("*/hook.sock"))


def test_a_rom_with_the_wrong_hash_is_refused_naming_both_hashes(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "Contra (USA).nes").write_bytes(b"NES\x1a" + bytes(12) + b"prg")
    adapter = _emulator(tmp_path)
    with pytest.raises(FixtureError) as e:
        adapter.launch({"rom": {"path": "<library>/Contra (USA).nes", "sha1": "0" * 40}})
    assert hashlib.sha1(b"prg").hexdigest() in str(e.value) and "0" * 40 in str(e.value)


def test_a_rom_matching_its_no_intro_sha1_is_placed_before_launch(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "Contra (USA).nes").write_bytes(b"NES\x1a" + bytes(12) + b"prg")
    adapter = _emulator(tmp_path)
    adapter.launch({"rom": {"path": "<library>/Contra (USA).nes", "sha1": hashlib.sha1(b"prg").hexdigest()}})
    try:
        assert (adapter.session_dir / "roms" / "Contra (USA).nes").read_bytes().endswith(b"prg")
    finally:
        adapter.teardown()


def test_a_placeholder_sha1_is_a_failure_not_a_skip(tmp_path):
    adapter = _emulator(tmp_path)
    with pytest.raises(FixtureError, match="placeholder"):
        adapter.launch({"rom": {"path": "<library>/x.nes", "sha1": "<no-intro sha1>"}})


def test_an_unimplemented_settings_profile_fails_clearly(tmp_path):
    with pytest.raises(FixtureError, match="history-one-favorite"):
        _emulator(tmp_path).launch({"settings": {"profile": "history-one-favorite"}})


def test_a_missing_pack_or_file_fixture_fails_clearly(tmp_path):
    with pytest.raises(FixtureError, match="pack.*not found"):
        _emulator(tmp_path).launch({"pack": {"path": str(tmp_path / "nope")}})
    with pytest.raises(FixtureError, match="file.*not found"):
        _emulator(tmp_path).launch({"files": {"a/b.txt": str(tmp_path / "nope.txt")}})


def test_a_process_crash_marks_the_step_failed_and_the_next_batch_starts_fresh(tmp_path):
    d = script(step("b1.crash", action={"name": "pad.press", "args": {"button": "A", "ticks": 2}}))
    d["batches"].append({"id": "b2", "setup": [], "teardown": [], "steps": [step("b2.after")]})
    d["requires"]["actions"] = ["pad.press"]
    adapter = _emulator(tmp_path, env={"FAKE_HOOK_MODE": "crash"})
    results = run_launched(d, adapter)
    assert [(r["id"], r["verdict"]) for r in results] == [("b1.crash", "failed (process crash)"), ("b2.after", "failed (process crash)")]


def test_a_portable_session_runs_from_a_clone_with_its_own_settings_and_leaves_the_app_folder_alone(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    launcher = app / "Mesen"
    launcher.write_text(f"#!/bin/sh\nexec {sys.executable} {FORK / 'tests' / 'fake_hook_app.py'} \"$@\"\n")
    launcher.chmod(0o755)
    adapter = EmulatorAdapter(tmp_path / "out", app_cmd=[str(launcher)], work_dir=tmp_path / "work", portable=True)
    adapter.launch({})
    try:
        clone = adapter.session_dir / "app"
        assert adapter.command[0] == str(clone / "Mesen")
        assert json.loads((clone / "settings.json").read_text())["Preferences"] == {"UiMode": "Player", "Workspace": "Play", "SingleInstance": False}
        assert not (app / "settings.json").exists()
    finally:
        assert adapter.teardown() == []
    assert not list((tmp_path / "work").glob("mg-*"))


def test_the_emulator_process_runs_with_the_hook_flags_and_an_isolated_home(tmp_path):
    adapter = _emulator(tmp_path)
    adapter.launch({})
    try:
        flags = " ".join(adapter.command)
        assert "--test-hook=" in flags and "--test-hook-token=" in flags
        assert adapter.env["HOME"] == str(adapter.session_dir / "home")
        assert adapter.env["XDG_CONFIG_HOME"] == str(adapter.session_dir / "home")
    finally:
        adapter.teardown()


# --- #1183: the real emulator. These need a build of MesenAI main (with the hook) and fail, never skip, without one ---

THREE_PILOT_STEPS = ["launch.first-run-home", "launch.open-rom-focused", "home.focus-holds-up"]
# The home batch opens with a `home.first-focus` setup step (#1251): the focus its first case used to assume is now
# waited for. A slice keeps the setup its batch needs, the way `--case` does (#1241), so the three cases run as four
# steps - and every one of them still has to pass.
THREE_PILOT_RUN = ["launch.first-run-home", "launch.open-rom-focused", "home.first-focus", "home.focus-holds-up"]


def _env_path(name, what):
    value = os.environ.get(name)
    assert value, f"set {name} to {what}"
    return Path(value).expanduser()


@pytest.mark.slow(reason="starts the real emulator three times (one process per batch) and waits on its window")
@pytest.mark.host_bound(reason="needs the MesenAI checkout and its emulator to run the pilot steps (MESENAI_REPO)")
def test_three_pilot_steps_run_on_the_real_emulator_with_no_pad_attached(tmp_path):
    app = _env_path("MESENAI_APP", "the built emulator launcher, e.g. <repo>/bin/osx-arm64/Release/Mesen (a build of main with the test hook)")
    assert app.is_file(), f"MESENAI_APP {app} is not a file"
    d = _pilot_slice(THREE_PILOT_STEPS)
    adapter = EmulatorAdapter(tmp_path / "out", app_cmd=[str(app)], work_dir=None, startup_timeout=120, portable=True)
    validate(d, adapter.capabilities())
    events = []
    results = run_launched(d, adapter, record=events.append)
    assert [(r["id"], r["verdict"]) for r in results] == [(i, "passed") for i in THREE_PILOT_RUN], results
    nodes = run_node([{"op": "guirun", "events": events}])[0]
    assert [(n["id"], n["status"], n["verdict"]) for n in nodes] == [(i, "executed", "passed") for i in THREE_PILOT_RUN]
    for n in nodes:
        assert Path(n["capture"]["path"]).read_bytes().startswith(b"\x89PNG")
    assert len({n["capture"]["sha256"] for n in nodes}) >= 1


def test_a_headless_tree_that_is_the_binarys_checkout_is_refused_and_a_separate_one_runs(tmp_path):
    """#1414: the headless case builds the UI into its tree; that must never be the tree MESEN_GUI_BINARY comes from."""
    from squad.gui_test_headless import SharedTree, headless_repo
    build, headless = tmp_path / "e2e-build", tmp_path / "e2e-headless"
    binary = build / "bin" / "osx-arm64" / "Release" / "Mesen"
    binary.parent.mkdir(parents=True); binary.write_text("x"); headless.mkdir()
    with pytest.raises(SharedTree, match="MESEN_GUI_BINARY"):
        headless_repo({"MESENAI_HEADLESS_REPO": str(build), "MESEN_GUI_BINARY": str(binary)})
    link = tmp_path / "alias"; link.symlink_to(build)
    with pytest.raises(SharedTree):
        headless_repo({"MESENAI_HEADLESS_REPO": str(link), "MESENAI_APP": str(binary)})
    assert headless_repo({"MESENAI_HEADLESS_REPO": str(headless), "MESEN_GUI_BINARY": str(binary)}) == headless
    assert headless_repo({"MESENAI_HEADLESS_REPO": str(build)}) == build  # no binary configured: nothing to collide with


@pytest.mark.slow(reason="runs dotnet test on the headless suite, which builds the UI first")
@pytest.mark.host_bound(reason="needs the MesenAI emulator hook to drive the Home over the socket (MESENAI_REPO)")
def test_hook_e2e_case_of_1182_drives_the_home_over_the_socket():
    """#1182's GuiTestHook_e2e_a_runner_drives_the_Home_over_the_socket, run for real: one case passed, none skipped."""
    from squad.gui_test_headless import SharedTree, headless_repo
    _env_path("MESENAI_HEADLESS_REPO", "a MesenAI checkout with `make core` built (its own bin/, never the read-only MESENAI_REPO)")
    try:
        repo = headless_repo(os.environ)
    except SharedTree as e:
        pytest.skip(str(e))
    lib = _env_path("MESEN_CORE_LIB", "the MesenCore.dylib built from that checkout")
    assert lib.is_file(), f"MESEN_CORE_LIB {lib} is not a file"
    p = subprocess.run(
        ["dotnet", "test", "UI.HeadlessTests/UI.HeadlessTests.csproj", "-p:RuntimeIdentifier=osx-arm64",
         "--filter", "FullyQualifiedName~GuiTestHookTests.GuiTestHook_e2e_a_runner_drives_the_Home_over_the_socket"],
        cwd=repo, env={**os.environ, "MESEN_CORE_LIB": str(lib)}, capture_output=True, text=True, timeout=1500)
    out = p.stdout + p.stderr
    assert p.returncode == 0, out[-2000:]
    summary = re.search(r"Passed:\s*(\d+).*?Skipped:\s*(\d+)|Skipped:\s*(\d+).*?Passed:\s*(\d+)", out.replace("\n", " "))
    assert re.search(r"Passed:\s*1\b", out) and not re.search(r"Skipped:\s*[1-9]", out), out[-2000:]
    assert summary

# --- #1192: manual steps and degrade-to-checklist ---

from squad.gui_test_run import failed  # noqa: E402


def _mixed_script():
    return script(
        step("b1.auto"),
        step("b1.look", mode="manual", expect="the card glows"),
    )


def _shot(tmp_path, name="look.png"):
    p = tmp_path / name
    p.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    return str(p)


def test_a_manual_step_records_the_persons_verdict_and_screenshot_on_its_node(tmp_path):
    events, asked = [], []

    def prompt(s):
        asked.append((s["id"], s["expect"]))
        return {"verdict": "passed", "screenshot": _shot(tmp_path)}

    results = run(_mixed_script(), TickingFake(tmp_path), record=events.append, prompt=prompt)
    assert asked == [("b1.look", "the card glows")]
    assert [(r["id"], r["verdict"]) for r in results] == [("b1.auto", "passed"), ("b1.look", "passed")]
    nodes = {n["id"]: n for n in run_node([{"op": "guirun", "events": events}])[0]}
    assert (nodes["b1.look"]["status"], nodes["b1.look"]["verdict"]) == ("executed", "passed")
    assert nodes["b1.look"]["capture"]["path"].endswith("look.png")
    assert len(nodes["b1.look"]["capture"]["sha256"]) == 64
    assert nodes["b1.look"]["manual"] is True


def test_a_manual_step_nobody_ran_ends_pending_and_does_not_fail_the_run(tmp_path):
    events = []
    results = run(_mixed_script(), TickingFake(tmp_path), record=events.append)
    assert [(r["id"], r["verdict"]) for r in results] == [("b1.auto", "passed"), ("b1.look", "pending")]
    assert not failed(results)
    nodes = {n["id"]: n for n in run_node([{"op": "guirun", "events": events}])[0]}
    assert (nodes["b1.look"]["status"], nodes["b1.look"]["capture"]) == ("pending", None)


def test_a_failed_manual_verdict_fails_the_run(tmp_path):
    results = run(_mixed_script(), TickingFake(tmp_path),
                  prompt=lambda s: {"verdict": "failed", "screenshot": _shot(tmp_path)})
    assert failed(results)


def test_a_script_whose_adapter_is_unavailable_runs_entirely_as_a_checklist(tmp_path):
    events, asked = [], []

    def prompt(s):
        asked.append(s["id"])
        return {"verdict": "passed", "screenshot": _shot(tmp_path, f"{s['id']}.png")}

    results = run(three_steps(), None, record=events.append, prompt=prompt)
    assert asked == ["b1.right-holds", "b1.open-library", "b1.ghost"]
    assert [r["verdict"] for r in results] == ["passed"] * 3
    nodes = run_node([{"op": "guirun", "events": events}])[0]
    assert [(n["status"], n["manual"]) for n in nodes] == [("executed", True)] * 3


def test_an_unavailable_adapter_with_nobody_at_the_checklist_leaves_every_step_pending():
    results = run(three_steps(), None)
    assert [r["verdict"] for r in results] == ["pending"] * 3
    assert not failed(results)


# --- #1188: screenshot privacy gate: a capture marked sensitive is held until a person approves it ---

from squad.gui_test_gate import approve, egress  # noqa: E402


def _privacy_script():
    return script(
        step("b1.plain"),
        step("b1.file-dialog", sensitive=True,
             action={"name": "pad.press", "args": {"button": "A", "ticks": 4}},
             wait={"check": "ui.screen == play.library", "timeout_ticks": 10},
             check={"name": "ui.screen", "args": {"is": "play.library"}}),
    )


def _privacy_run(tmp_path):
    d, events = _privacy_script(), []
    adapter = TickingFake(tmp_path)
    validate(d, adapter.capabilities())
    return run(d, adapter, record=events.append), events


def test_sensitive_must_be_a_boolean_and_a_step_may_omit_it():
    d = _privacy_script()
    validate(d)
    d["batches"][0]["steps"][1]["sensitive"] = "yes"
    with pytest.raises(ScriptError) as e:
        validate(d)
    assert "b1.file-dialog: sensitive must be true or false" in e.value.problems


def test_a_marked_capture_is_held_and_nothing_is_sent_before_approval(tmp_path):
    results, events = _privacy_run(tmp_path)
    assert [(r["id"], r["held"]) for r in results] == [("b1.plain", False), ("b1.file-dialog", True)]
    assert [e["held"] for e in events if e["event"] == "gui_capture"] == [False, True]
    sent = []
    out = egress(results, sent.append, tmp_path / "approvals.json")
    assert out == {"sent": ["b1.plain"], "held": ["b1.file-dialog"]}
    assert sent == [results[0]["capture"]["path"]]


def test_an_unmarked_capture_flows_without_a_prompt(tmp_path):
    d = script(step("b1.a"), step("b1.b"))
    results = run(d, TickingFake(tmp_path))
    sent = []
    out = egress(results, sent.append, tmp_path / "approvals.json")
    assert out == {"sent": ["b1.a", "b1.b"], "held": []}
    assert not (tmp_path / "approvals.json").exists()


def test_approval_by_a_person_releases_exactly_that_capture(tmp_path):
    results, _ = _privacy_run(tmp_path)
    ap = tmp_path / "approvals.json"
    cap = results[1]["capture"]
    approve(ap, "b1.file-dialog", cap["sha256"], by="sandro")
    sent = []
    assert egress(results, sent.append, ap) == {"sent": ["b1.plain", "b1.file-dialog"], "held": []}
    assert cap["path"] in sent


def test_an_approval_does_not_cover_a_different_capture_of_the_same_step(tmp_path):
    results, _ = _privacy_run(tmp_path)
    ap = tmp_path / "approvals.json"
    approve(ap, "b1.file-dialog", "0" * 64, by="sandro")
    sent = []
    assert egress(results, sent.append, ap)["held"] == ["b1.file-dialog"]


def test_approval_needs_a_named_person(tmp_path):
    with pytest.raises(ValueError):
        approve(tmp_path / "a.json", "b1.x", "0" * 64, by="")


def test_dashboard_shows_which_captures_are_held_and_withholds_their_path(tmp_path):
    results, events = _privacy_run(tmp_path)
    nodes = {n["id"]: n for n in run_node([{"op": "guirun", "events": events}])[0]}
    assert (nodes["b1.plain"]["held"], nodes["b1.file-dialog"]["held"]) == (False, True)
    assert nodes["b1.plain"]["capture"]["path"] and nodes["b1.file-dialog"]["capture"]["path"] is None


def test_dashboard_releases_a_held_capture_on_its_approval_event(tmp_path):
    results, events = _privacy_run(tmp_path)
    cap = results[1]["capture"]
    events = events + [{"event": "gui_approve", "node": "b1.file-dialog", "sha256": cap["sha256"],
                        "by": "sandro", "pos": len(events)}]
    nodes = {n["id"]: n for n in run_node([{"op": "guirun", "events": events}])[0]}
    assert nodes["b1.file-dialog"]["held"] is False
    assert nodes["b1.file-dialog"]["capture"]["path"] == cap["path"]


def test_script_diagram_marks_a_sensitive_step():
    svg = _svg(_privacy_script())
    assert re.search(r'class="step auto sensitive"[^>]*data-step="b1\.file-dialog"', svg)
    assert re.search(r'class="step auto"[^>]*data-step="b1\.plain"', svg)

# --- #1185: batches with fixtures, crash/hang supervision and state restored ---

import time  # noqa: E402

from squad.gui_test_supervise import run_supervised  # noqa: E402


def _batch(bid, *steps, setup=(), teardown=()):
    return {"id": bid, "setup": list(setup), "teardown": list(teardown), "steps": list(steps)}


def _two_batch_script(b1_steps, **over):
    d = script(**over)
    d["batches"] = [_batch("b1", *b1_steps),
                    _batch("b2", step("b2.a", action={"name": "pad.press", "args": {"button": "A", "ticks": 4}},
                                      wait={"check": "ui.screen == play.library", "timeout_ticks": 10},
                                      check={"name": "ui.screen", "args": {"is": "play.library"}}))]
    return d


def _verdicts(out):
    return {r["id"]: r["verdict"] for r in out["results"]}


@pytest.mark.heavy
def test_a_crash_mid_batch_fails_that_batch_and_the_next_batch_runs_in_a_fresh_process(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    d = _two_batch_script([step("b1.one"), step("b1.two")])
    out = run_supervised(d, "tests.gui_test_fakes:CrashAdapter",
                         {"marker": str(tmp_path / "m"), "report_dir": str(reports)},
                         out_dir=tmp_path / "out", report_dir=reports)
    b1, b2 = out["batches"]
    assert (b1["id"], b1["verdict"]) == ("b1", "failed (process crash)")
    assert "native fault in the render thread" in b1["output"]
    assert [Path(p).name for p in b1["crash_reports"]] == ["Fake-crash.ips"]
    assert _verdicts(out) == {"b1.one": "failed (process crash)", "b1.two": "skipped (batch failed)",
                              "b2.a": "passed"}
    assert b2["verdict"] == "passed"


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


@pytest.mark.heavy
def test_a_hang_is_killed_after_the_step_timeout_and_leaves_no_orphan(tmp_path):
    pid_file = tmp_path / "grandchild.pid"
    d = _two_batch_script([step("b1.one")])
    d["batches"] = d["batches"][:1]
    t0 = time.monotonic()
    out = run_supervised(d, "tests.gui_test_fakes:HangAdapter", {"pid_file": str(pid_file)},
                         step_timeout_s=1, out_dir=tmp_path / "out")
    assert time.monotonic() - t0 < 8
    assert out["batches"][0]["verdict"] == "failed (hang)"
    assert _verdicts(out) == {"b1.one": "failed (hang)"}
    pid = int(pid_file.read_text())
    for _ in range(50):
        if not _alive(pid):
            break
        time.sleep(0.1)
    assert not _alive(pid)


def test_a_failed_precondition_skips_the_rest_of_its_batch_only(tmp_path):
    d = _two_batch_script([step("b1.one", precondition="ui.screen == play.library"), step("b1.two")])
    out = run_supervised(d, "squad.gui_test_adapter:FakeAdapter", out_dir=tmp_path / "out")
    assert _verdicts(out) == {"b1.one": "failed (precondition ui.screen == play.library)",
                              "b1.two": "skipped (batch failed)", "b2.a": "passed"}
    assert [b["verdict"] for b in out["batches"]] == ["failed (precondition ui.screen == play.library)", "passed"]


def test_settings_and_fixtures_are_byte_identical_after_the_run_even_when_the_app_mutates_them(tmp_path):
    settings, rom = tmp_path / "settings.json", tmp_path / "Contra.nes"
    settings.write_bytes(b'{"volume": 7}\n')
    rom.write_bytes(bytes(range(256)))
    ghost = tmp_path / "created-by-run.cfg"
    before = (settings.read_bytes(), rom.read_bytes())
    d = _two_batch_script([step("b1.one")], fixtures={"restore": [str(settings), str(rom), str(ghost)]})
    run_supervised(d, "tests.gui_test_fakes:SettingsAdapter", out_dir=tmp_path / "out")
    assert (settings.read_bytes(), rom.read_bytes()) == before
    assert not ghost.exists()


# --- #1189: vision verdict with cost on nodes and a per-run spend cap ---

def test_vision_verdict_and_spend_cap_end_to_end(tmp_path):
    from squad.gui_test_vision import Vision

    def model(png, expect):  # a stand-in that reads the expected text: right text compatible, wrong text incompatible
        return {"answer": "compatible" if "library" in expect else "incompatible", "saw": "a library list", "cost": 0.03}

    def look(sid, expect):
        s = step(sid, expect=expect, action={"name": "pad.press", "args": {"button": "A", "ticks": 1}}, wait={"timeout_ticks": 2})
        del s["check"]
        return s

    d = script(step("b1.checked", check={"name": "ui.focused", "args": {"is": "play.home.open-rom"}}, wait={"timeout_ticks": 2}),
               look("b1.right", "the library lists games"), look("b1.wrong", "the settings page is open"),
               look("b1.after-cap", "the library lists games"))
    validate(d)
    events = []
    results = run(d, TickingFake(tmp_path), record=events.append, vision=Vision(model, cap=0.05))
    assert [(r["id"], r["verdict"]) for r in results] == [
        ("b1.checked", "passed"), ("b1.right", "passed"), ("b1.wrong", "needs review"),
        ("b1.after-cap", "skipped (spend cap $0.05 reached)")]
    nodes = {n["id"]: n for n in run_node([{"op": "guirun", "events": events}])[0]}
    assert [nodes[i]["cost"] for i in ("b1.right", "b1.wrong")] == [0.03, 0.03]
    assert "cost" not in nodes["b1.checked"] and nodes["b1.after-cap"]["status"] == "skipped"
    assert "spend cap" in nodes["b1.after-cap"]["stop"]


def test_the_recorded_vision_measurement_covers_the_fixed_screenshot_set():
    fix = FORK / "tests" / "fixtures" / "gui_vision"
    cases = json.loads((fix / "cases.json").read_text())
    rec = json.loads((fix / "measurement.json").read_text())
    assert rec["total"] == len(cases) and rec["correct"] == rec["total"] and rec["model"]
    assert [(c["image"], c["expect"], c["want"]) for c in rec["cases"]] == [(c["image"], c["expect"], c["answer"]) for c in cases]
    assert all(c["got"] == c["want"] for c in rec["cases"])


# --- #1190: a failed run opens one bug with its evidence ---

from squad.gui_test_report import evidence_store, file_bug_with, report_failures  # noqa: E402


def _two_failures_script():
    return script(
        step("b1.ok"),
        step("b1.bad-screen", expect="the library is showing",
             action={"name": "pad.press", "args": {"button": "A", "ticks": 4}},
             wait={"check": "ui.screen == play.library", "timeout_ticks": 10},
             check={"name": "ui.screen", "args": {"is": "play.home"}}),
        step("b1.never", expect="the dialog appears",
             wait={"check": "ui.screen == nowhere", "timeout_ticks": 2}),
    )


class _Sinks:
    def __init__(self):
        self.stored, self.bugs = [], []

    def store(self, path, name):
        self.stored.append((path, name))
        return f"https://evidence.example/{name}"

    def file_bug(self, title, body, priority=None):
        self.bugs.append((title, body, priority))
        return "https://github.com/sbihaiko/MesenAI/issues/9999"


def test_1190_failed_run_opens_one_bug_listing_both_steps_with_inline_evidence(tmp_path):
    d = _two_failures_script()
    results = run(d, TickingFake(tmp_path))
    sinks = _Sinks()
    url = report_failures(d, results, sinks.store, sinks.file_bug, tmp_path / "approvals.json")
    assert url == "https://github.com/sbihaiko/MesenAI/issues/9999"
    [(title, body, _)] = sinks.bugs
    assert "tiny" in title
    for sid, expect in (("b1.bad-screen", "the library is showing"), ("b1.never", "the dialog appears")):
        assert f"`{sid}`" in body and expect in body
    assert "failed (timeout waiting for ui.screen == nowhere)" in body
    # the failed step's own screenshot and the previous step's render inline as images
    shot = results[1]["capture"]["sha256"][:12]
    prev = results[0]["capture"]["sha256"][:12]
    assert re.search(rf"!\[[^\]]*\]\(https://evidence\.example/[^)]*{shot}[^)]*\)", body)
    assert re.search(rf"!\[[^\]]*\]\(https://evidence\.example/[^)]*{prev}[^)]*\)", body)
    # the timed-out step took no capture, so it says so instead of linking one
    assert "no screenshot" in body


def test_1190_clean_run_creates_no_issue_and_stores_nothing(tmp_path):
    d = script(step("b1.a"), step("b1.b"))
    sinks = _Sinks()
    assert report_failures(d, run(d, TickingFake(tmp_path)), sinks.store, sinks.file_bug,
                           tmp_path / "approvals.json") is None
    assert sinks.bugs == [] and sinks.stored == []


def test_1190_a_pending_step_alone_does_not_open_a_bug(tmp_path):
    d = _mixed_script()
    sinks = _Sinks()
    results = run(d, None)
    assert report_failures(d, results, sinks.store, sinks.file_bug, tmp_path / "a.json") is None


def test_1190_a_held_screenshot_is_never_stored_and_the_bug_says_it_is_held(tmp_path):
    d = _privacy_script()
    d["batches"][0]["steps"][1]["check"]["args"]["is"] = "play.home"
    results = run(d, TickingFake(tmp_path))
    sinks = _Sinks()
    report_failures(d, results, sinks.store, sinks.file_bug, tmp_path / "approvals.json")
    held_sha = results[1]["capture"]["sha256"]
    assert all(held_sha[:12] not in name for _, name in sinks.stored)
    assert "held pending approval" in sinks.bugs[0][1]


def test_1190_evidence_store_commits_to_a_dedicated_branch_and_returns_a_rendering_url(tmp_path):
    png = tmp_path / "a.png"
    png.write_bytes(b"\x89PNG-bytes")
    calls = []

    def runner(args, **kw):
        calls.append(args)
        return ""

    store = evidence_store("sbihaiko/MesenAI", branch="gui-test-evidence", runner=runner)
    url = store(str(png), "run1/b1.bad-screen-abc123.png")
    assert url == ("https://github.com/sbihaiko/MesenAI/blob/gui-test-evidence/"
                   "run1/b1.bad-screen-abc123.png?raw=true")
    [args] = calls
    assert args[:4] == ["gh", "api", "--method", "PUT"]
    assert "repos/sbihaiko/MesenAI/contents/run1/b1.bad-screen-abc123.png" in args


def test_1190_the_bug_goes_through_report_bug_sh(tmp_path):
    calls = []
    fb = file_bug_with("/repo", runner=lambda args, **kw: calls.append(args) or "Issue created: u/1\n")
    fb("t", "b", "P1")
    assert calls == [["/repo/scripts/report-bug.sh", "t", "b", "P1"]]


# ---- #1184: objective checks (ui.*, emu.ram, fs.*, log.*) ----
def _one(adapter, check=None, wait=None, root=None, **kw):
    s = step("c.one", check=check, wait=wait or {"timeout_ticks": 3}, **kw)
    if check is None:
        del s["check"]
    [r] = run(script(s), adapter, root=root)
    return r["verdict"]


def _ck(name, **args):
    return {"name": name, "args": args}


def test_objective_check_families_each_pass_and_fail(tmp_path):
    a = TickingFake(tmp_path)
    a.ram[0xF0] = 3
    a.log_lines.append("library opened")
    (tmp_path / "out.txt").write_text("saved ok")
    assert _one(a, _ck("ui.screen", **{"is": "play.home"}), root=tmp_path) == "passed"
    assert _one(a, _ck("emu.ram", addr="0xF0", **{"is": "0x03"}), root=tmp_path) == "passed"
    assert _one(a, _ck("fs.exists", path="out.txt", **{"is": True}), root=tmp_path) == "passed"
    assert _one(a, _ck("fs.contains", path="out.txt", **{"is": "saved"}), root=tmp_path) == "passed"
    assert _one(a, _ck("log.contains", **{"is": "library opened"}), root=tmp_path) == "passed"
    v = _one(a, _ck("ui.screen", **{"is": "play.library"}), root=tmp_path)
    assert v == "failed (expected ui.screen == play.library, observed play.home)"
    v = _one(a, _ck("emu.ram", addr="0xF0", **{"is": "0x04"}), root=tmp_path)
    assert v == "failed (expected emu.ram 0xF0 == 0x04, observed 3)"
    v = _one(a, _ck("fs.exists", path="gone.txt", **{"is": True}), root=tmp_path)
    assert v == "failed (expected fs.exists gone.txt == True, observed false)"
    assert _one(a, _ck("fs.contains", path="out.txt", **{"is": "nope"}), root=tmp_path) == \
        "failed (expected 'out.txt' contains 'nope', observed saved ok)".replace("'out.txt'", "out.txt")
    assert _one(a, _ck("log.contains", **{"is": "crash"}), root=tmp_path) == \
        "failed (expected log contains 'crash', observed library opened)"


def test_wait_on_runner_predicate_that_never_holds_times_out_naming_it(tmp_path):
    v = _one(TickingFake(tmp_path), wait={"check": "fs.exists never.txt == true", "timeout_ticks": 5}, root=tmp_path)
    assert v == "failed (timeout waiting for fs.exists never.txt == true)"
    v = _one(TickingFake(tmp_path), wait={"check": "emu.ram 0x10 == 0x99", "timeout_ticks": 5}, root=tmp_path)
    assert v == "failed (timeout waiting for emu.ram 0x10 == 0x99)"
    v = _one(TickingFake(tmp_path), wait={"check": "log.contains == ready", "timeout_ticks": 5}, root=tmp_path)
    assert v == "failed (timeout waiting for log.contains == ready)"


def test_wait_on_runner_predicate_met_once_it_appears(tmp_path):
    a = TickingFake(tmp_path)
    (tmp_path / "late.txt").write_text("x")
    v = _one(a, wait={"check": "fs.exists late.txt == true", "timeout_ticks": 5}, root=tmp_path,
             check=_ck("fs.exists", path="late.txt", **{"is": True}))
    assert v == "passed"


# --- #1186: CI verdict: a missing native core or a failed step is red; needs-review/pending alone is green ---

from squad.gui_test_ci import ci_verdict  # noqa: E402


def _r(*verdicts):
    return [{"id": f"b1.s{i}", "verdict": v, "capture": None, "held": False} for i, v in enumerate(verdicts)]


def test_ci_a_run_with_the_native_core_unavailable_is_red_never_a_skip():
    code, summary = ci_verdict(_r("pending", "pending"), core_available=False)
    assert code != 0
    assert "native core" in summary


def test_ci_a_run_with_a_failed_step_is_red_and_names_it():
    code, summary = ci_verdict(_r("passed", "failed (timeout waiting for x)"), core_available=True)
    assert code != 0
    assert "b1.s1" in summary


def test_ci_only_needs_review_and_pending_steps_is_green_and_listed_in_the_summary():
    code, summary = ci_verdict(_r("passed", "needs review", "pending"), core_available=True)
    assert code == 0
    assert "needs review" in summary and "b1.s1" in summary and "b1.s2" in summary


def test_ci_a_clean_run_is_green(tmp_path):
    results = _three_step_run(tmp_path)[3]
    code, _ = ci_verdict(results, core_available=True)
    assert code == 0


# --- #1187: key.* through the hook: a keyboard-driven step runs unattended and its node shows its check ---

from squad.gui_test_adapter import KeyboardFake  # noqa: E402


def _keyboard_script():
    d = script(
        step("b1.key-open", action={"name": "key.press", "args": {"key": "Enter", "ticks": 4}},
             wait={"check": "ui.screen == play.library", "timeout_ticks": 10},
             check={"name": "ui.screen", "args": {"is": "play.library"}}),
    )
    d["requires"] = {"actions": ["key.press"], "checks": ["ui.screen"]}
    return d


def test_a_keyboard_step_runs_unattended_and_its_node_shows_its_check(tmp_path):
    d, adapter, events = _keyboard_script(), KeyboardFake(tmp_path), []
    validate(d, adapter.capabilities())
    results = run(d, adapter, record=events.append)
    assert [(r["id"], r["verdict"]) for r in results] == [("b1.key-open", "passed")]
    assert adapter.log == [("key.press", {"key": "Enter", "ticks": 4})]
    [n] = run_node([{"op": "guirun", "events": events}])[0]
    assert n["check"] == {"name": "ui.screen", "args": {"is": "play.library"}}
    assert n["action"] == "key.press"


def test_the_emulator_adapter_advertises_key_press_and_the_pad_only_fake_does_not(tmp_path):
    assert "key.press" in KeyboardFake(tmp_path).capabilities()["actions"]
    assert "key.press" not in TickingFake(tmp_path).capabilities()["actions"]


def test_a_key_the_hook_does_not_know_does_not_open_the_library(tmp_path):
    d = _keyboard_script()
    d["batches"][0]["steps"][0]["action"]["args"]["key"] = "NoSuchKey"
    d["batches"][0]["steps"][0]["wait"] = {"check": "ui.screen == play.library", "timeout_ticks": 6}
    [r] = run(d, KeyboardFake(tmp_path))
    assert r["verdict"] == "failed (timeout waiting for ui.screen == play.library)"
# --- #1191: pointer.*, text.type and window.mode as the first variant axis; the local-only real-window mode ---

from squad.gui_test_variants import PermissionsMissing, RealWindowAdapter, RealWindowRefused, run_variants  # noqa: E402


class _Modal(TickingFake):
    def __init__(self, out_dir):
        super().__init__(out_dir)
        self.modes = []

    def set_variant(self, axis, value):
        self.modes.append(value)


def _v1191_script(*steps):
    d = script(*steps)
    d["requires"] = {"actions": ["pad.press", "pointer.click", "text.type", "window.mode"],
                     "checks": ["ui.screen", "ui.focused"]}
    return d


def test_1191_a_step_tagged_for_both_window_modes_runs_once_per_mode(tmp_path):
    both = step("b1.both", variants={"window.mode": ["windowed", "fullscreen"]})
    win = step("b1.win", variants={"window.mode": "windowed"})
    untagged = step("b1.any")
    d = _v1191_script(both, win, untagged)
    a = _Modal(tmp_path)
    out = run_variants(d, a, axis="window.mode")
    assert [(r["id"], r["variant"]) for r in out] == [
        ("b1.both", "windowed"), ("b1.win", "windowed"), ("b1.any", "windowed"),
        ("b1.both", "fullscreen"), ("b1.any", "fullscreen")]
    assert [m for m in a.modes] == ["windowed", "fullscreen"]


def test_1191_pointer_and_text_actions_reach_the_adapter(tmp_path):
    click = step("b1.click", action={"name": "pointer.click", "args": {"x": 10, "y": 20, "button": "left"}})
    typed = step("b1.type", action={"name": "text.type", "args": {"text": "contra"}})
    d = _v1191_script(click, typed)
    a = TickingFake(tmp_path)
    validate(d, {**a.capabilities(), "actions": a.capabilities()["actions"] + ["pointer.click", "text.type"],
                 "variants": a.capabilities()["variants"]})
    run_variants(d, a, axis="window.mode")
    assert ("pointer.click", {"x": 10, "y": 20, "button": "left"}) in a.log
    assert ("text.type", {"text": "contra"}) in a.log


def test_1191_a_full_screen_pilot_case_runs_locally_in_the_real_window_mode(tmp_path):
    fs = step("b1.fs", variants={"window.mode": "fullscreen"})
    a = RealWindowAdapter(TickingFake(tmp_path), env={}, permissions=lambda: {"accessibility", "screen-recording"})
    out = run_variants(_v1191_script(fs), a, axis="window.mode")
    assert [(r["id"], r["variant"], r["verdict"]) for r in out] == [("b1.fs", "fullscreen", "passed")]
    assert a.real_window is True


def test_1191_the_real_window_mode_refuses_to_start_without_the_permissions(tmp_path):
    a = RealWindowAdapter(TickingFake(tmp_path), env={}, permissions=lambda: {"accessibility"})
    with pytest.raises(PermissionsMissing) as e:
        a.launch({})
    assert "screen-recording" in str(e.value) and "accessibility" not in str(e.value)
    assert a.inner.log == []


def test_1191_the_real_window_mode_is_not_used_in_ci(tmp_path):
    for env in ({"CI": "true"}, {"GITHUB_ACTIONS": "true"}):
        a = RealWindowAdapter(TickingFake(tmp_path), env=env,
                              permissions=lambda: {"accessibility", "screen-recording"})
        with pytest.raises(RealWindowRefused):
            a.launch({})


def test_1210_a_failed_objective_check_names_expected_and_observed_and_skips_the_model(tmp_path):
    from squad.gui_test_vision import Vision

    asked = []

    def model(png, expect):
        asked.append(expect)
        return {"answer": "compatible", "saw": "x", "cost": 0.01}

    d = script(step("b1.wrong", action={"name": "pad.press", "args": {"button": "A", "ticks": 4}},
                    wait={"check": "ui.screen == play.library", "timeout_ticks": 10},
                    check={"name": "ui.screen", "args": {"is": "play.home"}}))
    [r] = run(d, TickingFake(tmp_path), vision=Vision(model))
    assert r["verdict"] == "failed (expected ui.screen == play.home, observed play.library)"
    assert asked == [] and "vision" not in r
# ---- #1199: goal navigation (ADR-0271) ----

def _goal_step(sid, goal="ui.screen == play.library", family="pad", **over):
    over.setdefault("role", "setup")
    return step(sid, action={"name": "nav.goal", "args": {"goal": goal, "input": family}},
                wait={"check": goal, "timeout_ticks": 10}, check={"name": "ui.screen", "args": {"is": "play.library"}}, **over)


class _Chooser:
    """A stand-in for Jev: always picks the first option offered, so only the withdrawal of failed moves gets it anywhere."""

    def __init__(self, cost=0.00002):
        self.calls, self.cost = [], cost

    def __call__(self, state, options):
        self.calls.append((state, list(options)))
        return {"choice": options[0], "probabilities": {o: 1 / len(options) for o in options}, "cost": self.cost}


@pytest.mark.parametrize("family", ["pad", "key"])
def test_1199_a_goal_is_reached_from_home_with_no_fixed_path_by_pad_and_by_keyboard(tmp_path, family):
    from squad.gui_test_goal import Navigator
    d = script(_goal_step("b1.pre", family=family))
    validate(d)
    chooser = _Chooser()
    events = []
    [r] = run(d, TickingFake(tmp_path), record=events.append, goal=Navigator(chooser))
    assert r["verdict"] == "passed"
    assert len(chooser.calls) > 1  # the first pick fails from home, so it is withdrawn and Jev is asked again
    first, second = chooser.calls[0][1], chooser.calls[1][1]
    assert first[0] not in second and len(second) == len(first) - 1
    assert all("png" not in json.dumps(s).lower() for s, _ in chooser.calls)  # state is typed JSON, never pixels
    assert {"screen", "focus", "controls", "options"} <= set(chooser.calls[0][0])


def test_1199_each_decision_is_a_sub_node_with_options_choice_probabilities_snapshot_and_cost_and_counts_against_the_cap(tmp_path):
    from squad.gui_test_goal import Navigator
    from squad.gui_test_vision import Vision
    vision = Vision(lambda png, expect: {"answer": "compatible", "saw": "", "cost": 0}, cap=1.0)
    events = []
    run(script(_goal_step("b1.pre")), TickingFake(tmp_path), record=events.append, goal=Navigator(_Chooser(cost=0.25), max_spend=5.0), vision=vision)
    [node] = [n for n in run_node([{"op": "guirun", "events": events}])[0] if n["id"] == "b1.pre"]
    kids = node["decisions"]
    assert len(kids) >= 2 and kids[-1]["choice"] in kids[-1]["options"]
    assert set(kids[0]) >= {"options", "choice", "probabilities", "state", "cost"}
    assert vision.spent == pytest.approx(0.25 * len(kids))
    assert node["cost"] == pytest.approx(0.25 * len(kids))


def test_1199_caps_end_the_step_failed_goal_not_reached_with_the_last_state(tmp_path):
    from squad.gui_test_goal import Navigator
    events = []
    [r] = run(script(_goal_step("b1.pre")), TickingFake(tmp_path), record=events.append, goal=Navigator(_Chooser(), max_decisions=1))
    assert r["verdict"].startswith("failed (goal not reached")
    assert "play.home" in r["verdict"]
    [r2] = run(script(_goal_step("b1.pre")), TickingFake(tmp_path), goal=Navigator(_Chooser(cost=0.5), max_spend=0.6))
    assert r2["verdict"].startswith("failed (goal not reached") and "spend" in r2["verdict"]


def test_1199_the_validator_rejects_a_goal_as_the_action_of_a_step_under_test():
    d = script(_goal_step("b1.bad", role="under-test"))
    with pytest.raises(ScriptError, match="nav.goal is not allowed on a step under test"):
        validate(d)
    no_goal = script(step("b1.nogoal", role="setup", action={"name": "nav.goal", "args": {}}))
    with pytest.raises(ScriptError, match="nav.goal needs a goal"):
        validate(no_goal)


def test_1199_a_replay_reuses_the_recorded_moves_and_makes_no_jev_call(tmp_path):
    from squad.gui_test_goal import Navigator, recorded_moves
    d = script(_goal_step("b1.pre"))
    events = []
    run(d, TickingFake(tmp_path), record=events.append, goal=Navigator(_Chooser()))
    chooser = _Chooser()
    again = TickingFake(tmp_path)
    [r] = run(d, again, goal=Navigator(chooser, replay=recorded_moves(events)))
    assert r["verdict"] == "passed" and chooser.calls == []
    assert again.screen == "play.library"


def test_1199_with_no_navigator_a_goal_step_is_pending_never_failed(tmp_path):
    [r] = run(script(_goal_step("b1.pre")), TickingFake(tmp_path))
    assert r["verdict"] == "pending"


def test_1199_the_jev_chooser_sends_the_typed_ui_state_and_the_options_as_one_choice(tmp_path):
    from squad.gui_test_goal import Navigator, jev_chooser
    asked = []

    class Client:
        def ask(self, state, questions):
            asked.append((state, questions))
            opts = list(questions["move"]["criteria"])
            return {"move": {"choice": opts[-1], "probabilities": {o: 1 / len(opts) for o in opts}}}, "jev"

    [r] = run(script(_goal_step("b1.pre")), TickingFake(tmp_path), goal=Navigator(jev_chooser(Client(), "ui.screen == play.library")))
    assert r["verdict"] == "passed" and len(asked) == 1  # the last offered move is A
    assert asked[0][0]["ui"]["screen"] == "play.home" and "image" not in json.dumps(asked[0]).lower()


# ---- #1200: the exploratory trap sweep (ADR-0271) ----

def _sweep_step(sid="b1.sweep", bound=40, **args):
    return step(sid, role="under-test", action={"name": "nav.sweep", "args": {"input": "pad", "bound": bound, **args}},
                wait={"timeout_ticks": 600}, expect="No screen reachable by pad is one no advertised action leaves.")


def _sweep_script(*steps):
    d = script(*steps)
    d["requires"]["actions"].append("nav.sweep")
    return d


def _sweep(tmp_path, edges, step_=None, **kw):
    from squad.gui_test_goal import Navigator
    from tests.gui_test_fakes import GraphAdapter
    events = []
    d = _sweep_script(step_ or _sweep_step())
    validate(d)
    [r] = run(d, GraphAdapter(tmp_path, edges, dialogs=kw.pop("dialogs", ())), record=events.append,
              goal=Navigator(_Chooser(), **kw))
    return r, events


def test_1200_a_planted_trap_screen_is_caught_naming_the_screen_and_the_path_that_reached_it(tmp_path):
    from tests.gui_test_fakes import TRAP_APP
    r, events = _sweep(tmp_path, TRAP_APP)
    assert r["verdict"].startswith("failed (trap screen play.about")
    assert "play.home -[pad.press A]-> play.settings -[pad.press A]-> play.about" in r["verdict"]
    assert any(e["event"] == "gui_decision" for e in events)  # same mechanism as nav.goal: Jev's Choice per move


def test_1200_a_trap_free_fixture_passes_within_the_bound(tmp_path):
    from tests.gui_test_fakes import CLEAN_APP
    r, events = _sweep(tmp_path, CLEAN_APP)
    assert r["verdict"] == "passed"
    assert sum(e["event"] == "gui_decision" for e in events) <= 40


def test_1200_a_sweep_that_cannot_finish_within_the_bound_fails_naming_the_bound(tmp_path):
    from tests.gui_test_fakes import CLEAN_APP
    r, _ = _sweep(tmp_path, CLEAN_APP, step_=_sweep_step(bound=2))
    assert r["verdict"].startswith("failed (sweep bound 2 reached")


def test_1200_the_dialogs_scope_only_judges_dialog_screens(tmp_path):
    from tests.gui_test_fakes import TRAP_APP
    r, _ = _sweep(tmp_path, TRAP_APP, step_=_sweep_step(scope="dialogs"))
    assert r["verdict"] == "passed"  # About is a dead end but not a dialog
    r, _ = _sweep(tmp_path, TRAP_APP, step_=_sweep_step(scope="dialogs"), dialogs=["play.about"])
    assert r["verdict"].startswith("failed (trap screen play.about")


def test_1200_a_sweep_by_keyboard_uses_the_keyboard_moves(tmp_path):
    from squad.gui_test_goal import Navigator
    from tests.gui_test_fakes import GraphAdapter
    edges = {"play.home": {"Enter": "play.dead"}}
    d = _sweep_script(_sweep_step())
    d["batches"][0]["steps"][0]["action"]["args"]["input"] = "key"
    [r] = run(d, GraphAdapter(tmp_path, edges), goal=Navigator(_Chooser()))
    assert r["verdict"].startswith("failed (trap screen play.dead") and "key.press Enter" in r["verdict"]


def test_1200_a_sweep_replays_without_asking_jev_and_with_no_navigator_ends_pending(tmp_path):
    from squad.gui_test_goal import Navigator, recorded_moves
    from tests.gui_test_fakes import TRAP_APP, GraphAdapter
    d = _sweep_script(_sweep_step())
    events = []
    run(d, GraphAdapter(tmp_path, TRAP_APP), record=events.append, goal=Navigator(_Chooser()))
    [r] = run(d, GraphAdapter(tmp_path, TRAP_APP), goal=Navigator(None, replay=recorded_moves(events)))
    assert r["verdict"].startswith("failed (trap screen play.about")
    [p] = run(d, GraphAdapter(tmp_path, TRAP_APP))
    assert p["verdict"] == "pending"


def test_1200_the_validator_needs_a_positive_integer_bound():
    d = _sweep_script(_sweep_step(bound=0))
    with pytest.raises(ScriptError, match="nav.sweep needs a positive integer bound"):
        validate(d)


@pytest.mark.host_bound(reason="needs the MesenAI pilot dialogs to sweep the trap (MESENAI_REPO)")
def test_1200_the_pilot_dialogs_no_trap_sweep_is_one_nav_sweep_step():
    root = _mesenai()
    d = json.loads((root / "docs/validation/process/play-pad-only.gui-test.json").read_text())
    validate(d)
    sweeps = [s for b in d["batches"] for s in b["setup"] + b["steps"] + b["teardown"] if s.get("action", {}).get("name") == "nav.sweep"]
    if not sweeps:  # the pilot step lands with MesenAI's #1200 PR; until it is on main the read-only checkout lacks it
        pytest.skip("MESENAI_REPO's pilot script has no nav.sweep step yet")
    [s] = sweeps
    assert s["action"]["args"]["scope"] == "dialogs" and s["id"] == "dialogs.no-trap"
# --- #1193: the pilot's Library, Favorites and opening-and-playing cases, converted and run end to end ---

from tests.gui_test_pilot_fake import PilotFake  # noqa: E402

CASES_1193 = ([f"LIB-0{n}" for n in range(1, 10)] + ["FAV-01", "FAV-02"] + [f"GAME-0{n}" for n in range(1, 5)])
PILOT_1193 = FORK / "tests" / "fixtures" / "gui_test" / "play-pad-only-1193.json"


def _pilot_1193():
    d = json.loads(PILOT_1193.read_text(encoding="utf-8"))
    validate(d)
    return d


def _steps_1193(d, *batches):
    return [s for b in d["batches"] if b["id"] in batches for k in ("setup", "steps", "teardown") for s in b[k]]


def test_1193_every_library_favorites_and_game_case_is_converted_none_dropped():
    d = _pilot_1193()
    steps = _steps_1193(d, "library", "favorites", "favorites-home", "game", "game-archive", "game-bios")
    for case in CASES_1193:
        assert any(s["expect"].startswith(case + ":") or f" {case}:" in s["expect"] or f"({case})" in s["expect"]
                   for s in steps), f"{case} has no step"


def _run_1193(tmp_path, d, **fixtures):
    results = []
    for batch in d["batches"]:
        if batch["id"] in ("launch", "home", "home-after-game"):
            continue
        view = {**d, "batches": [batch]}
        adapter = PilotFake(tmp_path, **fixtures.get(batch["id"], {}))
        results += [{**r, "batch": batch["id"]} for r in run_variants(view, adapter, axis="window.mode")]
    return results


def test_1193_the_batches_run_end_to_end_in_both_window_modes(tmp_path):
    d = _pilot_1193()
    results = _run_1193(tmp_path, d, **{"game-archive": {"tile": "zip"}, "game-bios": {"tile": "fds"},
                                    "favorites-home": {"favorites": 1}})
    bad = [(r["batch"], r["id"], r["variant"], r["verdict"]) for r in results
           if r["verdict"].startswith("failed")]
    assert bad == []
    assert {r["variant"] for r in results} == {"windowed", "fullscreen"}
    manual = {s["id"] for s in _steps_1193(d, "library", "favorites", "favorites-home", "game", "game-archive",
                                           "game-bios") if s["mode"] == "manual"}
    assert manual and {r["id"] for r in results if r["verdict"] == "pending"} == manual
    assert {r["id"] for r in results if r["verdict"] == "passed"}.isdisjoint(manual)


def test_1193_a_deviation_in_the_library_fails_that_step_and_the_run(tmp_path):
    d = _pilot_1193()
    results = _run_1193(tmp_path, d, library={"y_focuses_search": False})
    failed = [r["id"] for r in results if r["verdict"].startswith("failed")]
    assert "lib.search-y-focuses-field" in failed
    from squad.gui_test_run import failed as run_failed
    assert run_failed(results)


# --- #1183: the emulator adapter lives in MesenAI (ADR-0272 item 6, P1-A); the fork only loads it from MESENAI_ROOT ---

import sys  # noqa: E402

PILOT = "play-pad-only.gui-test.json"
THREE_PILOT_STEPS = ["launch.first-run-home", "launch.open-rom-focused", "home.focus-holds-up"]
HEADLESS_CASE = "GuiTestHookTests.GuiTestHook_e2e_a_runner_drives_the_Home_over_the_socket"


def _need(name, what):
    """A real-emulator case FAILS, never skips, when its environment is not set (a skip would read green)."""
    value = os.environ.get(name)
    assert value, f"set {name} to {what}"
    return Path(value).expanduser()


def _mesenai_root():
    return _need("MESENAI_ROOT", "a MesenAI checkout holding scripts/gui_test/mesen_gui_adapter.py")


def _adapter_class(root):
    sys.path.insert(0, str(root / "scripts" / "gui_test"))
    try:
        import importlib
        return importlib.import_module("mesen_gui_adapter").MesenGuiAdapter
    finally:
        sys.path.remove(str(root / "scripts" / "gui_test"))


def _pilot_for_the_real_app(ids):
    """The pilot's own steps, each batch keeping only `ids` (in every role); the launch/home steps need no rom or pack, so those fixtures are dropped."""
    d = json.loads((_mesenai_root() / "docs" / "validation" / "process" / PILOT).read_text(encoding="utf-8"))
    for b in d["batches"]:
        _slice_batch(b, ids)
    d["batches"] = [b for b in d["batches"] if b["steps"]]
    # The runner opens one fresh application process per batch (ADR-0272 item 6), so a batch that carries no setup
    # step can only run as the continuation of the batch before it. The pilot's `home` batch is exactly that: its
    # steps are preconditioned on `ui.focused == play.home.open-rom`, the state its preceding `launch` batch
    # establishes, and the Home's first focus lands a few ticks after the window opens. A second process would press
    # the pad at a Home that has focused nothing yet, so the continuation stays in the session of the batch before.
    # A slice that names the batch's setup step (`home.first-focus`) asks for it as a case of the continuation: it waits
    # on the state the batch before leaves, so it runs in that batch's process, in order.
    kept = []
    for b in d["batches"]:
        asked = [s for s in b["setup"] if s["id"] in ids]
        if kept and asked and len(asked) == len(b["setup"]):
            kept[-1]["steps"] = kept[-1]["steps"] + b["setup"] + b["steps"]
        else:
            kept.append(b)
    d["batches"] = kept
    # `requires` stays the pilot's own: its automated steps now press, connect and disconnect pads and read the ring,
    # lamps and surface through the hook (MesenAI #1283), so narrowing it to pad.press made the run refuse to start.
    d["variants"] = {"window.mode": ["windowed"]}
    # The three steps need no rom and no pack: keep only the settings fixture, the one kind every adapter build supports
    # (the pilot also declares pack/rom-* kinds for its later batches, which the adapter rejects at launch).
    d["fixtures"] = {k: v for k, v in d["fixtures"].items() if k == "settings"}
    return d


@pytest.fixture
def short_work():
    """A Unix socket path is capped near 100 bytes, which pytest's tmp_path blows through."""
    import shutil
    import tempfile
    d = Path(tempfile.mkdtemp(prefix="mg-", dir="/tmp"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _synthetic_rom(tmp_path):
    rom = tmp_path / "lib" / "Contra (USA).nes"
    rom.parent.mkdir()
    rom.write_bytes(b"NES\x1a" + bytes(12) + b"prg")
    return rom, hashlib.sha1(b"prg").hexdigest()


def test_the_worker_loads_the_adapter_class_from_mesenai_root(tmp_path):
    """squad.gui_test_worker resolves `module:cls` against <MESENAI_ROOT>/scripts/gui_test."""
    pkg = tmp_path / "scripts" / "gui_test"
    pkg.mkdir(parents=True)
    (pkg / "probe_adapter.py").write_text("class Probe:\n    def __init__(self, **kw):\n        pass\n    def capabilities(self):\n        return {'probe': True}\n")
    p = subprocess.run([sys.executable, "-m", "squad.gui_test_worker", "probe_adapter:Probe", "{}"], cwd=FORK,
                       input=json.dumps({"call": "capabilities"}) + "\n", capture_output=True, text=True, timeout=30,
                       env={**os.environ, "MESENAI_ROOT": str(tmp_path)})
    assert json.loads(p.stdout.splitlines()[0]) == {"ok": {"probe": True}}, p.stderr[-800:]


def _run_pilot_on_the_real_app(tmp_path, short_work, ids):
    root = _mesenai_root()
    binary = _need("MESEN_GUI_BINARY", "the built Mesen launcher of a MesenAI build with the test hook")
    _need("MESEN_CORE_LIB", "the MesenCore library built from the same checkout")
    assert binary.is_file(), f"MESEN_GUI_BINARY {binary} is not a file"
    d = _pilot_for_the_real_app(ids)
    # The adapter seeds `{}`, and this build then boots into the classic menu UI after tick 0 (screen None), so the
    # Play first run is seeded here, through the adapter's own `files` fixture, until the adapter seeds it itself.
    seed = tmp_path / "settings.json"
    # SingleInstance off: the app's instance guard is a machine-wide named mutex, so while any other Mesen runs (a
    # sibling suite, the user's own copy) a second launch hands off to it and exits 0 before opening its hook.
    seed.write_text(json.dumps({"Preferences": {"UiMode": "Player", "Workspace": "Play", "SingleInstance": False}}))
    d["fixtures"]["files"] = [{"src": str(seed), "dest": "settings.json"}]
    adapter = _adapter_class(root)(binary=str(binary), workdir=short_work, connect_timeout=120)
    validate(d, adapter.capabilities())
    events, results = [], []
    for batch in d["batches"]:
        session = adapter.launch(d["fixtures"])
        try:
            mark = len(events)
            try:
                results += run({**d, "batches": [batch]}, _Shot(session, tmp_path / "shots"), record=events.append)
            except Exception as e:  # noqa: BLE001 - what run_supervised does with an adapter error: the step in flight fails with it
                open_step = [ev["node"] for ev in events[mark:] if ev["event"] == "gui_step"][-1]
                verdict = f"failed ({e})"
                events.append({"event": "gui_verdict", "node": open_step, "pos": len(events), "verdict": verdict})
                results.append({"id": open_step, "verdict": verdict, "capture": None})
        finally:
            left = session.teardown()  # `quit` over the hook sometimes ends in a SIGSEGV (exit -11): reported, not asserted here
            assert all("quit not acknowledged" in x or "exited -11" in x for x in left), left
    return results, run_node([{"op": "guirun", "events": events}])[0]


@pytest.mark.slow(reason="starts the real emulator twice (one process per batch) and waits on its window")
@pytest.mark.host_bound(reason="needs the MesenAI adapter and its emulator to capture each pilot step (MESENAI_REPO)")
def test_three_pilot_steps_are_executed_through_the_mesenai_adapter_with_a_capture_each(tmp_path, short_work):
    results, nodes = _run_pilot_on_the_real_app(tmp_path, short_work, THREE_PILOT_STEPS)
    assert [(n["id"], n["status"], n["verdict"]) for n in nodes] == [(i, "executed", "passed") for i in THREE_PILOT_RUN], nodes
    assert [r["verdict"] for r in results] == ["passed"] * len(THREE_PILOT_RUN), results
    for n in nodes:
        assert Path(n["capture"]["path"]).read_bytes().startswith(b"\x89PNG"), n


class _Shot:
    """The run's capture seam: `run` asks the adapter for a screenshot per step; the session needs a path."""

    def __init__(self, session, out):
        out.mkdir(exist_ok=True)
        self._s, self._out, self._n = session, out, 0

    def __getattr__(self, name):
        return getattr(self._s, name)

    def inject(self, action, args):
        """A press right after launch can answer `unknown button Up on pad 1` until the app has built its key names
        (measured: the first inject after launch fails, the same one a second later is taken); retry it for up to 10 s."""
        deadline = time.monotonic() + 10
        while True:
            try:
                return self._s.inject(action, args)
            except Exception as e:
                if "unknown button" not in str(e) or time.monotonic() > deadline:
                    raise
                time.sleep(0.25)

    def wait(self, condition, timeout_ticks):
        """A tick-only wait (`wait` with no `check`, as home.focus-holds-up has) reaches the adapter as None, which
        MesenGuiAdapter.wait cannot read (`None.strip`); the hook's own tick counter is the clock, so wait it out here."""
        if condition is not None:
            return self._s.wait(condition, timeout_ticks)
        start = self._s._ask("state")["tick"]
        while self._s._ask("state")["tick"] < start + timeout_ticks:
            time.sleep(0.02)
        return "met"

    def capture(self, path=None):
        self._n += 1
        return self._s.capture(path or self._out / f"shot-{self._n}.png")


@pytest.mark.slow(reason="runs dotnet test on the headless suite, which builds the UI first")
@pytest.mark.host_bound(reason="needs the MesenAI emulator hook for the e2e case (MESENAI_REPO)")
def test_the_hook_e2e_case_passes_exactly_one_and_skips_none():
    """scripts/gui_test/run_headless_e2e.py: the skip=green trap closed, the verdict read off the summary line.

    The runner requires its whole hook e2e case set, and #1242 made that two: the Home walk over the socket and the
    fresh home's library drive. So the summary this asserts is two passed, none failed, none skipped."""
    root = _mesenai_root()
    lib = _need("MESEN_CORE_LIB", "the MesenCore library built from the MesenAI checkout")
    assert lib.is_file(), f"MESEN_CORE_LIB {lib} is not a file"
    p = subprocess.run([sys.executable, str(root / "scripts" / "gui_test" / "run_headless_e2e.py")], cwd=root,
                       env={**os.environ, "MESEN_CORE_LIB": str(lib)}, capture_output=True, text=True, timeout=1500)
    out = p.stdout + p.stderr
    assert p.returncode == 0, out[-2000:]
    assert re.search(r"Failed:\s*0,\s*Passed:\s*2,\s*Skipped:\s*0,\s*Total:\s*2", out), out[-2000:]
    assert HEADLESS_CASE.split(".")[1] in out or "Passed!" in out


@pytest.mark.host_bound(reason="needs the MesenAI ROM fixture to check its SHA-1 (MESENAI_REPO)")
def test_a_missing_rom_fixture_or_a_wrong_sha1_fails_clearly(tmp_path, short_work):
    root = _mesenai_root()
    adapter = _adapter_class(root)(binary=str(_need("MESEN_GUI_BINARY", "the built Mesen launcher")), workdir=short_work)
    absent = {"rom": {"path": str(tmp_path / "nope" / "Contra (USA).nes"), "sha1": "0" * 40}}
    with pytest.raises(Exception, match=r"rom fixture missing: .*Contra \(USA\)\.nes"):
        adapter.launch(absent)
    rom, sha1 = _synthetic_rom(tmp_path)
    with pytest.raises(Exception, match=rf"sha1 {sha1} is not the expected {'0' * 40}"):
        adapter.launch({"rom": {"path": str(rom), "sha1": "0" * 40}})
    assert not list(short_work.glob("**/hook.sock"))
# --- #1194: the pilot's Pause overlay and Settings cases, converted and run end to end ---

from tests.gui_test_pilot_fake_1194 import PauseSettingsFake  # noqa: E402

CASES_1194 = [f"P4-0{n}" for n in range(1, 10)] + [f"SET-0{n}" for n in range(1, 8)]
PILOT_1194 = FORK / "tests" / "fixtures" / "gui_test" / "play-pad-only-1194.json"


def _pilot_1194():
    d = json.loads(PILOT_1194.read_text(encoding="utf-8"))
    validate(d)
    return d


BATCHES_1194 = ("pause", "settings", "pause-pack-multi", "settings-menu-sounds")


def _steps_1194(d):
    return [s for b in d["batches"] if b["id"] in BATCHES_1194 for k in ("setup", "steps", "teardown")
            for s in b[k]]


def _run_1194(tmp_path, d, **switches):
    """Each batch needs its own state (a running game, W-P4 open): each runs on its own fake."""
    results = []
    for batch in d["batches"]:
        if batch["id"] in BATCHES_1194:
            view = {**d, "batches": [batch]}
            results += [{**r, "batch": batch["id"]}
                        for r in run_variants(view, PauseSettingsFake(tmp_path, **switches), axis="window.mode")]
    return results


def test_1194_every_pause_and_settings_case_is_converted_none_dropped_across_the_four_batches():
    steps = _steps_1194(_pilot_1194())
    for case in CASES_1194:
        assert any(f"{case}:" in s["expect"] or f"{case} (" in s["expect"] for s in steps), f"{case} has no step"


def test_1194_the_four_batches_run_end_to_end_in_both_window_modes(tmp_path):
    d = _pilot_1194()
    results = _run_1194(tmp_path, d)
    bad = [(r["batch"], r["id"], r["variant"], r["verdict"]) for r in results if r["verdict"].startswith("failed")]
    assert bad == []
    assert {r["variant"] for r in results} == {"windowed", "fullscreen"}
    for batch in BATCHES_1194:
        assert {r["variant"] for r in results if r["batch"] == batch} == {"windowed", "fullscreen"}, batch
    manual = {s["id"] for s in _steps_1194(d) if s["mode"] == "manual"}
    assert manual and {r["id"] for r in results if r["verdict"] == "pending"} == manual
    assert {r["id"] for r in results if r["verdict"] == "passed"}.isdisjoint(manual)
    automated = {s["id"] for s in _steps_1194(d) if s["mode"] == "automated"}
    assert automated <= {r["id"] for r in results if r["verdict"] == "passed"}


def test_1194_exit_full_screen_is_checked_in_full_screen_only(tmp_path):
    results = _run_1194(tmp_path, _pilot_1194())
    ran = {r["variant"] for r in results if r["id"] == "settings.exit-fullscreen-left-of-done"}
    assert ran == {"fullscreen"}


def test_1194_a_pause_that_b_does_not_resume_fails_that_step_and_the_run(tmp_path):
    from squad.gui_test_run import failed as run_failed
    results = _run_1194(tmp_path, _pilot_1194(), b_resumes=False)
    assert "pause.b-resumes" in [r["id"] for r in results if r["verdict"].startswith("failed")]
    assert run_failed(results)


def test_1194_a_done_the_pad_cannot_reach_fails_every_tab(tmp_path):
    results = _run_1194(tmp_path, _pilot_1194(), done_unreachable=True)
    failed = {r["id"] for r in results if r["verdict"].startswith("failed")}
    assert {f"settings.{t}-done-reachable" for t in ("display", "look", "audio", "controls", "system")} <= failed


# --- #1195: the pilot's controller sheet, port lamps, hot-plug, focus-loss and pad-loss cases ---

from tests.gui_test_pilot_fake_1195 import ControllerFake  # noqa: E402

CASES_1195 = [f"CTL-0{n}" for n in range(1, 6)] + [f"LAMP-0{n}" for n in range(1, 4)] + [f"LOSS-0{n}" for n in range(1, 5)]
BATCHES_1195 = ("controller", "controller-done", "controller-b", "controller-no-game", "lamps", "lamps-game", "loss")
PILOT_1195 = FORK / "tests" / "fixtures" / "gui_test" / "play-pad-only-1195.json"
PHYSICAL_1195 = {"lamp.four-lamps-p1", "lamp.second-pad-hotplug", "loss.focus-lost-pauses", "loss.pad-unplug-pauses",
                 "loss.pad-off-mid-menu", "loss.pad-sleep", "ctl.bind-extra-button"}


def _pilot_1195():
    d = json.loads(PILOT_1195.read_text(encoding="utf-8"))
    validate(d)
    return d


def _steps_1195(d):
    return [s for b in d["batches"] if b["id"] in BATCHES_1195 for k in ("setup", "steps", "teardown") for s in b[k]]


def _run_1195(tmp_path, d, **switches):
    results = []
    for batch in d["batches"]:
        if batch["id"] in BATCHES_1195:
            view = {**d, "batches": [batch]}
            results += [{**r, "batch": batch["id"]}
                        for r in run_variants(view, ControllerFake(tmp_path, **switches), axis="window.mode")]
    return results


def test_1195_every_controller_lamp_and_loss_case_is_converted_none_dropped():
    steps = _steps_1195(_pilot_1195())
    for case in CASES_1195:
        assert any(f"{case}:" in s["expect"] or f"{case} (" in s["expect"] for s in steps), f"{case} has no step"


def test_1195_the_batches_run_end_to_end_and_physical_cases_stay_manual(tmp_path):
    d = _pilot_1195()
    results = _run_1195(tmp_path, d)
    bad = [(r["batch"], r["id"], r["variant"], r["verdict"]) for r in results if r["verdict"].startswith("failed")]
    assert bad == []
    assert {r["variant"] for r in results} == {"windowed", "fullscreen"}
    manual = {s["id"] for s in _steps_1195(d) if s["mode"] == "manual"}
    assert PHYSICAL_1195 <= manual
    assert {r["id"] for r in results if r["verdict"] == "pending"} == manual
    assert {r["id"] for r in results if r["verdict"] == "passed"}.isdisjoint(manual)
    automated = {s["id"] for s in _steps_1195(d) if s["mode"] == "automated"}
    assert automated <= {r["id"] for r in results if r["verdict"] == "passed"}


def test_1195_a_physical_only_step_has_no_action_so_nothing_is_faked():
    for s in _steps_1195(_pilot_1195()):
        if s["id"] in PHYSICAL_1195:
            assert s["mode"] == "manual" and "action" not in s and "check" not in s


def test_1195_a_sheet_that_does_not_open_fails_that_step_and_the_run(tmp_path):
    from squad.gui_test_run import failed as run_failed
    results = _run_1195(tmp_path, _pilot_1195(), sheet_opens=False)
    assert "ctl.open-sheet" in [r["id"] for r in results if r["verdict"].startswith("failed")]
    assert run_failed(results)


def test_1195_a_capture_that_b_does_not_cancel_fails_the_step(tmp_path):
    results = _run_1195(tmp_path, _pilot_1195(), b_cancels_capture=False)
    assert "ctl.b-cancels-capture" in [r["id"] for r in results if r["verdict"].startswith("failed")]


# --- #1196: the pilot's full screen, sizes and dialogs no-trap cases, converted and run end to end ---

from tests.gui_test_pilot_fake_1196 import TRAP_SURFACES, TrapFake  # noqa: E402

BATCHES_1196 = ("fullscreen-switch", "fullscreen-exit", "fullscreen-notes", "fullscreen-extra-large", "trap")
PILOT_1196 = FORK / "tests" / "fixtures" / "gui_test" / "play-pad-only-1196.json"
CASES_1196 = [f"FS-0{n}" for n in range(1, 6)] + [f"TRAP-0{n}" for n in range(1, 5)]


def _pilot_1196():
    d = json.loads(PILOT_1196.read_text(encoding="utf-8"))
    validate(d)
    return d


def _steps_1196(d):
    return [s for b in d["batches"] if b["id"] in BATCHES_1196 for k in ("setup", "steps", "teardown") for s in b[k]]


def _run_1196(tmp_path, d, **switches):
    results = []
    for batch in d["batches"]:
        if batch["id"] in BATCHES_1196:
            view = {**d, "batches": [batch]}
            results += [{**r, "batch": batch["id"]}
                        for r in run_variants(view, TrapFake(tmp_path, **switches), axis="window.mode")]
    return results


def test_1196_every_full_screen_sizes_and_trap_case_is_converted_none_dropped():
    steps = _steps_1196(_pilot_1196())
    for case in CASES_1196:
        assert any(f"{case}:" in s["expect"] or f"{case} (" in s["expect"] for s in steps), f"{case} has no step"


def test_1196_every_trap_01_surface_the_pilot_names_has_a_b_step():
    ids = {s["id"] for s in _steps_1196(_pilot_1196())}
    for surface in TRAP_SURFACES:
        assert f"trap.{surface}-b-closes" in ids or f"trap.{surface}-b" in ids, surface
    assert len(TRAP_SURFACES) == 23 and {"trap.setup-sheet-b", "trap.tools-sheets-b"} <= ids


def test_1196_the_batches_run_end_to_end_in_the_modes_they_are_tagged_for(tmp_path):
    d = _pilot_1196()
    results = _run_1196(tmp_path, d)
    bad = [(r["batch"], r["id"], r["variant"], r["verdict"]) for r in results if r["verdict"].startswith("failed")]
    assert bad == []
    steps = _steps_1196(d)
    tagged = {s["id"]: s.get("variants", {}).get("window.mode") for s in steps}
    for r in results:
        assert tagged[r["id"]] in (None, r["variant"]), r
    assert {r["variant"] for r in results if tagged[r["id"]] == "fullscreen"} == {"fullscreen"}
    assert {r["variant"] for r in results if tagged[r["id"]] == "windowed"} == {"windowed"}
    manual = {s["id"] for s in steps if s["mode"] == "manual"}
    assert manual and {r["id"] for r in results if r["verdict"] == "pending"} == manual
    automated = {s["id"] for s in steps if s["mode"] == "automated"}
    assert automated <= {r["id"] for r in results if r["verdict"] == "passed"}


def test_1196_a_surface_b_does_not_close_fails_that_surface_and_the_run(tmp_path):
    from squad.gui_test_run import failed as run_failed
    results = _run_1196(tmp_path, _pilot_1196(), stuck={"play.pack.detail"})
    assert {r["id"] for r in results if r["verdict"].startswith("failed")} >= {"trap.pack-detail-b-closes"}
    assert run_failed(results)


def test_1196_a_native_dialog_left_open_fails_the_no_native_dialog_check(tmp_path):
    results = _run_1196(tmp_path, _pilot_1196(), native_dialog_on={"play.cheats.add-code"})
    assert "trap.add-a-code-no-native-dialog" in [r["id"] for r in results if r["verdict"].startswith("failed")]


def test_1196_exit_full_screen_that_drops_the_sheet_fails_fs_02(tmp_path):
    results = _run_1196(tmp_path, _pilot_1196(), exit_drops_sheet=True)
    assert "fs.exit-a-sheet-stays" in [r["id"] for r in results if r["verdict"].startswith("failed")]


# --- #1197: the pilot's two-pad, feel and complete-journey cases, converted and run end to end ---

from tests.gui_test_pilot_fake_1197 import JourneyFake  # noqa: E402

BATCHES_1197 = ("two-pads", "feel", "journey", "journey-fullscreen")
PILOT_1197 = FORK / "tests" / "fixtures" / "gui_test" / "play-pad-only-1197.json"
CASES_1197 = [f"PAD2-0{n}" for n in range(1, 5)] + [f"PAD-0{n}" for n in range(1, 5)] + ["JOURNEY-01", "JOURNEY-02"]
MANUAL_1197 = {"two-pads.chord-on-the-other-pad", "two-pads.alternate-pads", "two-pads.players-rows",
               "two-pads.replug-the-first-pad", "feel.hold-repeats", "feel.tap-quickly", "feel.stick-nudge",
               "feel.diagonal", "journey.felt-long", "journey.under-five-minutes"}


def _pilot_1197():
    d = json.loads(PILOT_1197.read_text(encoding="utf-8"))
    validate(d)
    return d


def _steps_1197(d, batches=BATCHES_1197):
    return [s for b in d["batches"] if b["id"] in batches for k in ("setup", "steps", "teardown") for s in b[k]]


def _run_1197(tmp_path, d, batches=BATCHES_1197, **switches):
    results = []
    for batch in d["batches"]:
        if batch["id"] in batches:
            view = {**d, "batches": [batch]}
            results += [{**r, "batch": batch["id"]}
                        for r in run_variants(view, JourneyFake(tmp_path, **switches), axis="window.mode")]
    return results


def test_1197_every_two_pad_feel_and_journey_case_is_converted_none_dropped():
    steps = _steps_1197(_pilot_1197())
    for case in CASES_1197:
        assert any(f"{case}:" in s["expect"] or f"{case} (" in s["expect"] for s in steps), f"{case} has no step"


def test_1197_the_batches_run_end_to_end_in_both_window_modes_and_physical_and_feel_stay_manual(tmp_path):
    d = _pilot_1197()
    results = _run_1197(tmp_path, d)
    assert [(r["batch"], r["id"], r["variant"], r["verdict"]) for r in results if r["verdict"].startswith("failed")] == []
    steps = _steps_1197(d)
    assert {s["id"] for s in steps if s["mode"] == "manual"} == MANUAL_1197
    assert {r["id"] for r in results if r["verdict"] == "pending"} == MANUAL_1197
    assert {r["variant"] for r in results if r["batch"] == "journey-fullscreen"} == {"fullscreen"}
    assert {r["variant"] for r in results if r["batch"] == "journey"} == {"windowed", "fullscreen"}
    assert {s["id"] for s in steps if s["mode"] == "automated"} <= {r["id"] for r in results if r["verdict"] == "passed"}


REAL_APP_1197 = ("launch", "home", "home-after-game")  # these three run on the real app, in the #1183 case above


def _fake_for_1197(tmp_path, batch):
    if batch in ("library", "favorites", "game"):
        return PilotFake(tmp_path)
    if batch == "favorites-home":
        return PilotFake(tmp_path, favorites=1)
    if batch == "game-archive":
        return PilotFake(tmp_path, tile="zip")
    if batch == "game-bios":
        return PilotFake(tmp_path, tile="fds")
    if batch in ("pause", "settings"):
        return PauseSettingsFake(tmp_path)
    if batch in BATCHES_1195:
        return ControllerFake(tmp_path)
    if batch in BATCHES_1196:
        return TrapFake(tmp_path)
    return JourneyFake(tmp_path)


def test_1197_the_full_pilot_runs_end_to_end_across_both_window_modes(tmp_path):
    """Every batch of the pilot, each on the fake of the slice that converted it; with this batch none is left manual-only."""
    d = _pilot_1197()
    results, ran = [], []
    for batch in d["batches"]:
        if batch["id"] in REAL_APP_1197:
            continue
        ran.append(batch["id"])
        view = {**d, "batches": [batch]}
        results += [{**r, "batch": batch["id"]} for r in
                    run_variants(view, _fake_for_1197(tmp_path, batch["id"]), axis="window.mode")]
    assert set(BATCHES_1197) <= set(ran) and set(ran) == {b["id"] for b in d["batches"]} - set(REAL_APP_1197)
    assert [(r["batch"], r["id"], r["variant"], r["verdict"]) for r in results if r["verdict"].startswith("failed")] == []
    assert {r["variant"] for r in results} == {"windowed", "fullscreen"}
    manual = {s["id"] for s in _steps_1197(d, tuple(ran)) if s["mode"] == "manual"}
    assert {r["id"] for r in results if r["verdict"] == "pending"} == manual


def test_1197_a_quit_that_does_not_land_on_home_fails_the_journey_and_the_run(tmp_path):
    from squad.gui_test_run import failed as run_failed
    results = _run_1197(tmp_path, _pilot_1197(), quit_stays=True)
    assert {r["id"] for r in results if r["verdict"].startswith("failed")} >= {"journey.quit-lands-home"}
    assert run_failed(results)


def test_1197_a_full_screen_switch_that_loses_the_sheet_fails_the_full_screen_journey(tmp_path):
    results = _run_1197(tmp_path, _pilot_1197(), switch_drops_sheet=True)
    assert "journey-fs.switch-focus-kept" in [r["id"] for r in results if r["verdict"].startswith("failed")]


# --- #1198: a second script (runbook section 1, the composition editor) on a second adapter, run with no runner change ---

from tests.gui_test_compose_fake import ComposeEditorFake  # noqa: E402

COMPOSE_1198 = FORK / "tests" / "fixtures" / "gui_test" / "composition-editor-runbook.json"


def _run_1198(tmp_path, **switches):
    d = json.loads(COMPOSE_1198.read_text(encoding="utf-8"))
    validate(d)
    fake = ComposeEditorFake(tmp_path, **switches)
    fake.launch(d["fixtures"])
    validate(d, fake.capabilities())
    return d, run(d, fake, root=tmp_path)


def test_1198_the_composition_editor_script_runs_end_to_end_on_its_own_adapter_with_fs_checks(tmp_path):
    d, results = _run_1198(tmp_path)
    assert d["target"] == "compose-editor" and d["target"] != _pilot_1197()["target"]
    assert {r["id"]: r["verdict"] for r in results} == {
        **{s["id"]: "passed" for b in d["batches"] for s in b["steps"] if s["mode"] == "automated"},
        "compose.reslice": "pending", "compose.survives-bootstrap": "pending"}
    assert {s["check"]["name"] for b in d["batches"] for s in b["steps"] if "check" in s} >= {"fs.exists", "log.contains"}


def test_1198_a_save_that_leaves_the_sheet_in_auto_fails_the_promotion_step(tmp_path):
    _, results = _run_1198(tmp_path, save_stays_in_auto=True)
    bad = {r["id"] for r in results if r["verdict"].startswith("failed")}
    assert bad == {"compose.save", "compose.save-says-so"}


# --- #1222: a command that runs a whole gui-test/1 script end to end (the /to-test-scenario Run step) ---


RUN_SCRIPT = FORK / "scripts" / "gui_test_run_script.py"
SMOKE = FORK / "tests" / "fixtures" / "gui_test" / "run-script-smoke.json"
SHAPE_SPEC = "tests.gui_test_run_script_fake:ShapeAdapter"


def _shape(**switches):
    """A supervised run of a one-step script on the adapter that answers like mesen-gui."""
    d = _two_batch_script([step("b1.one", **switches)])
    return d


def test_1222_a_failed_check_names_expected_and_observed_from_the_adapters_answer(tmp_path):
    """mesen-gui answers a check with {"passed": ..., "observed": ...}; a bool-only reader calls that a pass."""
    d = _shape(check={"name": "ui.screen", "args": {"is": "play.library"}})
    out = run_supervised(d, SHAPE_SPEC, out_dir=tmp_path / "out")
    assert _verdicts(out)["b1.one"] == "failed (expected play.library, observed play.home)"


def _hold_step(lost_ticks, tmp_path):
    """A hold like home.focus-holds-up (press 4 ticks, wait 4 ticks, focus must still be Open a ROM) on a slow host."""
    d = _shape(action={"name": "pad.press", "args": {"button": "Up", "ticks": 4}}, wait={"timeout_ticks": 4},
               check={"name": "ui.focused", "args": {"is": "play.home.open-rom"}})
    return run_supervised(d, "tests.gui_test_run_script_fake:SlowHostAdapter", {"lost_ticks": lost_ticks},
                          out_dir=tmp_path / "out")


def test_1418_a_hold_step_whose_focus_is_unread_at_the_check_settles_on_ticks_and_says_it_retried(tmp_path):
    """Load makes the hook read no focus right after the hold; the step re-reads for more emulated ticks (never host
    time), passes when the focus is back, and reports the retry beside the pass."""
    out = _hold_step(lost_ticks=10, tmp_path=tmp_path)
    assert _verdicts(out)["b1.one"] == "passed"
    result = next(r for r in out["results"] if r["id"] == "b1.one")
    assert result["timing"]["retried"] and result["timing"]["settled"], result


def test_1418_a_hold_step_whose_focus_never_returns_gets_a_timing_verdict_not_a_plain_miss(tmp_path):
    out = _hold_step(lost_ticks=10_000, tmp_path=tmp_path)
    assert _verdicts(out)["b1.one"].startswith("failed (timing: focus unread after")


def test_1418_a_hold_that_really_moved_the_focus_still_fails_on_the_observed_id_without_a_retry(tmp_path):
    d = _shape(action={"name": "pad.press", "args": {"button": "Up", "ticks": 4}}, wait={"timeout_ticks": 4},
               check={"name": "ui.focused", "args": {"is": "play.library"}})
    out = run_supervised(d, SHAPE_SPEC, out_dir=tmp_path / "out")
    assert _verdicts(out)["b1.one"] == "failed (expected play.library, observed play.home.open-rom)"
    assert "timing" not in out["results"][0]


def test_1222_an_unknown_id_fails_that_step_and_leaves_the_next_batch_running(tmp_path):
    """mesen-gui raises UnknownId; the step fails (unknown id), the batch is not the unit that fails."""
    d = _shape(check={"name": "ui.focused", "args": {"is": "play.ghost"}})
    out = run_supervised(d, SHAPE_SPEC, out_dir=tmp_path / "out")
    assert _verdicts(out) == {"b1.one": "failed (unknown id)", "b2.a": "passed"}


def _run_script_cmd(script_path, out, *extra, spec=SHAPE_SPEC, env=None, artifacts=None):
    args = [sys.executable, str(RUN_SCRIPT), str(script_path), "--adapter", spec, "--out", str(out),
            "--artifacts", str(artifacts or Path(str(out) + ".artifacts")), *extra]
    return subprocess.run(args, cwd=FORK, capture_output=True, text=True, timeout=600,
                          env={**os.environ, **(env or {})})


def test_1222_the_command_runs_a_fixture_script_and_writes_one_result_per_step(tmp_path):
    out = tmp_path / "results.json"
    p = _run_script_cmd(SMOKE, out)
    assert p.returncode == 1, p.stdout + p.stderr
    r = json.loads(out.read_text())
    assert r["format"] == "gui-test-results/1"
    assert r["run_id"] and r["platform"] and r["adapter"] == SHAPE_SPEC and r["build_sha"]
    assert r["script"]["path"] == str(SMOKE)
    assert r["script"]["sha256"] == hashlib.sha256(SMOKE.read_bytes()).hexdigest()
    verdicts = {s["id"]: s["verdict"] for s in r["steps"]}
    assert verdicts == {
        "smoke.open-library": "passed",
        "smoke.still-home": "failed (expected play.home, observed play.library)",
        "smoke.unknown-id": "failed (unknown id)",
        "smoke.manual": "pending",
        "smoke.unchecked": "needs review",
    }
    assert r["status"] == "failed" and r["summary"]["executed"] == 4
    assert r["bug"]["filed"] is False and "opt-in" in r["bug"]["reason"]
    assert r["steps"][0]["capture"]["path"].startswith(str(tmp_path))


def test_1222_a_script_with_no_failed_step_exits_zero(tmp_path):
    clean = tmp_path / "clean.json"
    clean.write_text(json.dumps(script(step("b1.one"))))
    out = tmp_path / "results.json"
    p = _run_script_cmd(clean, out)
    assert p.returncode == 0, p.stdout + p.stderr
    r = json.loads(out.read_text())
    assert r["status"] == "passed" and [s["verdict"] for s in r["steps"]] == ["passed"]


def test_1222_a_script_naming_what_the_adapter_does_not_advertise_is_rejected_before_launch(tmp_path):
    d = json.loads(SMOKE.read_text())
    first = d["batches"][0]["steps"][0]
    d["requires"]["actions"] = ["pad.press", "key.press"]
    d["requires"]["checks"] = ["ui.screen", "ui.focused", "ui.dialogs"]
    first["action"] = {"name": "key.press", "args": {"key": "Enter", "ticks": 4}}
    first["wait"] = {"timeout_ticks": 4}
    first["check"] = {"name": "ui.dialogs", "args": {"is": []}}
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(d))
    out = tmp_path / "results.json"
    p = _run_script_cmd(bad, out)
    text = p.stdout + p.stderr
    assert p.returncode != 0
    assert "adapter does not advertise action key.press" in text
    assert "adapter does not advertise check ui.dialogs" in text
    assert not Path(str(out) + ".artifacts").exists(), "the application must not be launched for a rejected script"


def test_1222_missing_environment_makes_the_run_fail_and_never_skip(tmp_path):
    out = tmp_path / "results.json"
    p = _run_script_cmd(SMOKE, out, spec="mesen-gui",
                        env={"MESENAI_ROOT": "", "MESEN_GUI_BINARY": "", "MESEN_CORE_LIB": ""})
    text = p.stdout + p.stderr
    assert p.returncode != 0
    for name in ("MESENAI_ROOT", "MESEN_GUI_BINARY", "MESEN_CORE_LIB"):
        assert name in text, text
    assert "skipped" not in text
    assert json.loads(out.read_text())["status"] == "failed"


def test_1222_a_core_that_cannot_be_loaded_fails_the_run(tmp_path):
    root = tmp_path / "mesenai"
    (root / "scripts" / "gui_test").mkdir(parents=True)
    binary = tmp_path / "Mesen"
    binary.write_bytes(b"#!/bin/sh\n")
    lib = tmp_path / "MesenCore.dylib"
    lib.write_bytes(b"this is not a shared library")
    out = tmp_path / "results.json"
    p = _run_script_cmd(SMOKE, out, spec="mesen-gui",
                        env={"MESENAI_ROOT": str(root), "MESEN_GUI_BINARY": str(binary), "MESEN_CORE_LIB": str(lib)})
    text = p.stdout + p.stderr
    assert p.returncode != 0 and "cannot be loaded" in text and "skipped" not in text


def test_1222_a_run_that_executed_no_step_is_a_failure(tmp_path):
    only = step("b1.manual-only")
    for key in ("action", "wait", "check"):
        only.pop(key)
    only["mode"] = "manual"
    manual = tmp_path / "manual.json"
    manual.write_text(json.dumps(script(only)))
    out = tmp_path / "results.json"
    p = _run_script_cmd(manual, out)
    assert p.returncode != 0
    r = json.loads(out.read_text())
    assert [s["verdict"] for s in r["steps"]] == ["pending"] and r["summary"]["executed"] == 0
    assert r["reasons"] == ["no step executed"]


def _run_script_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("gui_test_run_script", RUN_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_1222_one_bug_lists_every_failed_step_and_never_one_per_step(tmp_path):
    mod = _run_script_module()
    d = script(step("a"), step("b"), step("c"))
    for s in d["batches"][0]["steps"]:
        s["expect"] = f"what {s['id']} shows"
    rows = [{"id": "a", "verdict": "failed (expected x, observed y)", "capture": None, "held": False,
             "batch": "b1", "mode": "automated", "expect": "what a shows"},
            {"id": "b", "verdict": "failed (unknown id)", "capture": None, "held": False,
             "batch": "b1", "mode": "automated", "expect": "what b shows"},
            {"id": "c", "verdict": "passed", "capture": None, "held": False,
             "batch": "b1", "mode": "automated", "expect": "what c shows"}]
    calls = []
    detail = mod.file_one_bug(d, rows, "/repo", tmp_path / "approvals.json",
                              runner=lambda args, **kw: calls.append(args) or "Issue created: https://example.invalid/1\n")
    assert len(calls) == 1, calls
    title, body = calls[0][1], calls[0][2]
    assert title == "tiny: a — expected x, observed y (+1 more steps)", "the title names the failure, not a count"
    assert "### `a`" in body and "### `b`" in body and "### `c`" not in body
    assert "failed (expected x, observed y)" in body
    assert detail["url"] == "https://example.invalid/1" and detail["existing"] is False


# `home.first-focus` is the home batch's setup step (#1251): a run reports the batch's setup with its cases, so it is
# one of the ids this batch is asked for, and it is the state `home.focus-holds-up` is read from. MesenAI ccfd2dfc5
# (#1492) moved the batch's pad connect - `launch.port-lamp-p1` - from the launch batch's steps to the head of the home
# batch's, so the setup is asked for before any pad is connected; `_pilot_for_the_real_app` merges that setup into the
# session of the batch before, which puts `home.first-focus` third and leaves the connect right after it.
HOME_BATCH_IDS = ["launch.first-run-home", "launch.open-rom-focused", "home.first-focus", "launch.port-lamp-p1",
                  "home.focus-holds-up", "home.focus-holds-down", "home.focus-holds-left", "home.focus-holds-right",
                  "home.ring-visible", "home.open-library", "home.back-to-home", "home.settings-by-pad-gap"]
# None of these is manual any more: MesenAI #1283 made the lamp, ring and surface cases automated (the hook decides them).
HOME_BATCH_MANUAL = set()
# `home.open-library` waits on `ui.screen == play.library`, and no control in the application carries that id yet: the
# pilot says its ids are provisional until the in-app AutomationIds land (#1182). It is the one step this can fail on
# today, and it is asserted as a subset so the case keeps its meaning when that slice lands and the step passes.
HOME_BATCH_MAY_FAIL = {"home.open-library"}


@pytest.mark.host_bound(reason="needs the MesenAI pilot batches on the emulator (MESENAI_REPO)")
def test_1222_a_batch_with_no_setup_keeps_the_session_of_the_batch_before_it():
    """The runner opens one process per batch (ADR-0272 item 6), so the pilot's launch -> home continuation stays one batch."""
    d = _pilot_for_the_real_app(HOME_BATCH_IDS)
    assert [b["id"] for b in d["batches"]] == ["launch"], [b["id"] for b in d["batches"]]
    assert [s["id"] for b in d["batches"] for s in b["steps"]] == HOME_BATCH_IDS
    for b in d["batches"][1:]:
        assert b["setup"], f"batch {b['id']} carries no setup step, so only the batch before it can open it"


@pytest.mark.slow(reason="starts the real emulator once per batch of the pilot's Home batch and drives it over the hook")
@pytest.mark.host_bound(reason="needs the MesenAI native core to run the Home batch (MESENAI_REPO)")
def test_1222_the_pilot_home_batch_runs_through_the_command_against_the_native_core(tmp_path, short_work, clean_app_start):
    """AC 4: the committed pilot's Home batch, through the command, on the built application."""
    root = _mesenai_root()
    _need("MESEN_GUI_BINARY", "the built Mesen launcher of a MesenAI build with the test hook")
    _need("MESEN_CORE_LIB", "the MesenCore library built from the same checkout")
    d = _pilot_for_the_real_app(HOME_BATCH_IDS)
    # The committed Home batch's steps that need pad.release or pad.chord are `manual` (#1251): mesen-gui advertises
    # pad.press alone, so a person walks those and the run reports them as pending. What is left to inject is the
    # batch's setup and the steps that press the pad with an advertised button.
    d["fixtures"]["note"] = "steps needing pad.release or pad.chord are manual: mesen-gui advertises pad.press only"
    script_path = tmp_path / "pilot-home.json"
    script_path.write_text(json.dumps(d))
    out = short_work / "results.json"
    p = _run_script_cmd(script_path, out, "--step-timeout", "180", spec="mesen-gui",
                        env={"MESENAI_ROOT": str(root)})
    r = json.loads(out.read_text())
    guard.assert_launched(r, clean_app_start)
    assert {s["id"] for s in r["steps"]} == set(HOME_BATCH_IDS)
    assert {s["id"] for s in r["steps"] if s["verdict"] == "pending"} == HOME_BATCH_MANUAL
    failed = {s["id"] for s in r["steps"] if s["verdict"].startswith("failed")}
    verdicts = {s["id"]: s["verdict"] for s in r["steps"] if s["id"] in failed}
    assert failed <= HOME_BATCH_MAY_FAIL and not [b for b in r["batches"] if "crash" in b["verdict"] or "hang" in b["verdict"]], verdicts
    assert p.returncode == (1 if failed else 0), p.stdout + p.stderr
    passed = {s["id"] for s in r["steps"] if s["verdict"] == "passed"}
    assert passed >= set(HOME_BATCH_IDS) - HOME_BATCH_MANUAL - HOME_BATCH_MAY_FAIL - {"home.back-to-home"}
    assert r["summary"]["executed"] >= 6 and r["build_sha"] != "unknown"
    for result_step in r["steps"]:
        if result_step["verdict"] == "passed":
            assert Path(result_step["capture"]["path"]).read_bytes().startswith(b"\x89PNG"), result_step
# --- /to-test-scenario plans the committed pilot and publishes idempotently (MesenAI #1223) ---

class _ScenarioGitHub:
    """A recorded-call stand-in for the real REST runner: no network, no `gh`."""

    def __init__(self):
        self.calls = []
        self._next = 100

    def __call__(self, op, **kw):
        self.calls.append((op, kw))
        if op == "item_status":
            return "Backlog"           # the parent is boarded in Backlog (#895)
        if op == "create_issue":
            self._next += 1
            return {"number": self._next, "id": 900 + self._next}
        return {}

    def of(self, op):
        return [kw for o, kw in self.calls if o == op]


def _pilot_1223():
    return json.loads((_mesenai() / "docs" / "validation" / "process"
                       / "play-pad-only.gui-test.json").read_text(encoding="utf-8"))


@pytest.mark.host_bound(reason="needs the committed MesenAI pilot script to plan its cases (MESENAI_REPO)")
def test_1223_the_plan_covers_every_committed_pilot_step_and_labels_none_of_them_ready_for_agent():
    from squad import to_test_scenario as tts
    d = _pilot_1223()
    validate(d)
    plan = tts.build_plan(d, script_path="docs/validation/process/play-pad-only.gui-test.json",
                          purpose="the pad-only Play script")
    planned = [c for i in plan["issues"] for c in i["cases"]]
    assert planned == tts.step_ids(d) and len(planned) > 100
    assert all("Test-Case" in i["labels"] and "ready-for-agent" not in i["labels"] for i in plan["issues"])
    assert plan["parent"]["labels"] == ["ready-for-agent", "wave:01", "Test-Scenario"]
    # `goals` is the plan's own derivation - the steps that navigate by `nav.goal`/`nav.sweep` - and the committed
    # pad-only pilot has none: #1228 replaced its goal navigation with the pad press the case describes, so every case
    # it carries is planned above and the goal list is honestly empty.
    assert plan["manual"] and plan["goals"] == []


@pytest.mark.host_bound(reason="needs the MesenAI pilot script to publish twice (MESENAI_REPO)")
def test_1223_publishing_the_pilot_twice_updates_and_never_duplicates_a_case():
    from squad import to_test_scenario as tts
    d = _pilot_1223()
    plan = tts.build_plan(d, granularity="per-batch")
    gh = _ScenarioGitHub()
    first = tts.publish(plan, gh, board=True)
    nodes = len(plan["areas"]) + sum(len(a["containers"]) for a in plan["areas"])
    assert len(gh.of("create_issue")) == len(plan["issues"]) + nodes + 1
    assert len(gh.of("add_sub_issue")) == len(plan["issues"]) + nodes
    again = _ScenarioGitHub()
    second = tts.publish(plan, again, saved=first["map"], board=True)
    assert again.of("create_issue") == [] and second["issues"] == first["issues"] and second["removed"] == []
# --- to_test_scenario write-back (#1226): the committed run's results land on the pilot's issues ---------------
# --- to_test_scenario (#1224): the pilot's case ids are covered by its own scenario ---------------

class _IssueTracker:
    """A GitHub stand-in that remembers what it was told, so a second write-back sees the first one's state.

    It answers `issue_state` from its own memory: the state of the issue and the verdict of the last comment a
    write-back left there. No network, no `gh`, no model.
    """

    def __init__(self, numbers):
        self.state = {n: {"state": "open", "verdict": None} for n in numbers}
        self.comments = {n: [] for n in numbers}
        self.calls = []

    def __call__(self, op, **kw):
        self.calls.append((op, kw))
        number = kw.get("number")
        if op == "issue_state":
            return dict(self.state[number])
        if op == "comment":
            self.comments[number].append(kw["body"])
            verdict = re.match(r"\*\*(passed|failed|needs review|pending)\*\*", kw["body"])
            if verdict and number in self.state:
                self.state[number]["verdict"] = verdict.group(1)
        elif op == "close":
            self.state[number]["state"] = "closed"
        elif op == "reopen":
            self.state[number]["state"] = "open"
        return {}

    def ops(self):
        return [op for op, _ in self.calls]


def _write_back_fixture():
    """The committed run of #1222 and the pilot's map: the real results file, the real plan, a mapped issue each."""
    from squad import to_test_scenario as tts

    results = FORK / "docs" / "evidence" / "1222" / "play-pad-only-home.results.json"
    if not os.environ.get("MESENAI_REPO") or not results.is_file():
        pytest.skip("the committed results file of #1222 and MESENAI_REPO are needed")
    pilot_path = _mesenai() / "docs" / "validation" / "process" / "play-pad-only.gui-test.json"
    pilot = json.loads(pilot_path.read_text(encoding="utf-8"))
    plan = tts.build_plan(pilot, script_path="docs/validation/process/play-pad-only.gui-test.json")
    cases = sorted({case for issue in plan["issues"] for case in issue["cases"]})
    mapping = {case: 2000 + pos for pos, case in enumerate(cases)}
    return tts, results, plan, mapping, cases


def test_1226_the_committed_run_writes_its_verdicts_back_and_never_files_a_bug(tmp_path):
    """One real results file and the pilot's map: comments, closes, pending cases and the run's own bug, no network."""
    tts, results, plan, mapping, cases = _write_back_fixture()
    map_path = tmp_path / "play-pad-only.gui-test.issues.json"
    map_path.write_text(json.dumps({**mapping, "parent": 1999}))
    tracker = _IssueTracker(list(mapping.values()) + [1999])
    out = tts.write_back(results, map_path, tracker, plan=plan)

    assert out["counts"] == {"passed": 6, "failed": 1, "pending": 4}
    # The pilot script keeps growing; the run covers 11 of its cases, every other mapped case has no result.
    assert out["ok"] is False and out["reason"].startswith(f"{len(cases) - 11} case(s) have no result")
    assert out["bug"] == "https://github.com/sbihaiko/MesenAI/issues/1230"
    assert "file_bug" not in tracker.ops()
    assert tracker.state[mapping["home.open-library"]]["state"] == "open"
    assert "failed" in tracker.comments[mapping["home.open-library"]][0]
    assert sorted(out["closed"]) == ["home.focus-holds-down", "home.focus-holds-left", "home.focus-holds-right",
                                     "home.focus-holds-up", "launch.first-run-home", "launch.open-rom-focused"]
    assert all(tracker.state[mapping[c]]["state"] == "closed" for c in out["closed"])
    assert "home.back-to-home" in out["pending"] and "launch.port-lamp-p1" in out["pending"]
    summary = tracker.comments[1999][0]
    assert "https://github.com/sbihaiko/MesenAI/issues/1230" in summary and "darwin-arm64" in summary
    assert len(cases) >= 67 and cases == sorted(mapping)


def test_1226_the_same_run_written_back_twice_adds_no_case_comment_and_reopens_nothing(tmp_path):
    tts, results, plan, mapping, cases = _write_back_fixture()
    map_path = tmp_path / "play-pad-only.gui-test.issues.json"
    map_path.write_text(json.dumps({**mapping, "parent": 1999}))
    tracker = _IssueTracker(list(mapping.values()) + [1999])
    first = tts.write_back(results, map_path, tracker, plan=plan)
    before = {number: len(bodies) for number, bodies in tracker.comments.items() if number != 1999}
    second = tts.write_back(results, map_path, tracker, plan=plan)

    assert first["commented"] and second["commented"] == [] and second["reopened"] == []
    assert sorted(second["unchanged"]) == sorted(first["commented"])
    assert {n: len(b) for n, b in tracker.comments.items() if n != 1999} == before
    assert len(tracker.comments[1999]) == 2          # the summary runs every time; the cases do not


# --- #1244: a failed or needs-review screenshot reaches the evidence branch and the case's comment ---

from squad.gui_test_report import upload_evidence  # noqa: E402
from squad.gui_test_vision import Vision  # noqa: E402


class _EvidenceStore:
    """Fake evidence store: records every push and returns the URL an issue renders inline."""

    def __init__(self):
        self.pushed = []

    def __call__(self, path, name):
        url = f"https://github.com/sbihaiko/MesenAI/blob/gui-test-evidence/{name}?raw=true"
        self.pushed.append((path, name, url))
        return url


def _blind(sid, expect):
    """An automated step with no check: its verdict is the vision model's."""
    s = step(sid, expect=expect, wait={"timeout_ticks": 2})
    del s["check"]
    return s


def _every_verdict_script():
    return script(
        step("b1.passed"),
        step("b1.bad-screen", expect="the library is showing",
             action={"name": "pad.press", "args": {"button": "A", "ticks": 4}},
             wait={"check": "ui.screen == play.library", "timeout_ticks": 10},
             check={"name": "ui.screen", "args": {"is": "play.home"}}),
        _blind("b1.unsure", "the dialog is open"),
        step("b1.ran-by-hand", mode="manual", expect="the card glows"),
        step("b1.nobody-ran", mode="manual", expect="the tooltip shows"),
    )


def _every_verdict_run(tmp_path):
    """One step of every verdict: passed, failed, needs review (vision), a hand-run pass and a pending one."""
    d = _every_verdict_script()
    validate(d)
    vision = Vision(lambda png, expect: {"answer": "incompatible", "saw": "a dialog", "cost": 0.01})

    def prompt(s):
        return {"verdict": "passed", "screenshot": _shot(tmp_path, f"{s['id']}.png")} \
            if s["id"] == "b1.ran-by-hand" else None

    results = run(d, TickingFake(tmp_path), prompt=prompt, vision=vision)
    return {r["id"]: r for r in results}, results


def test_1244_only_failed_and_needs_review_captures_are_uploaded(tmp_path):
    by_id, results = _every_verdict_run(tmp_path)
    assert [(i, by_id[i]["verdict"]) for i in by_id] == [
        ("b1.passed", "passed"), ("b1.bad-screen", "failed (expected ui.screen == play.home, observed play.library)"),
        ("b1.unsure", "needs review"), ("b1.ran-by-hand", "passed"), ("b1.nobody-ran", "pending")]
    store = _EvidenceStore()
    out = {r["id"]: r for r in upload_evidence(results, store, tmp_path / "approvals.json")}
    # the failed and the needs-review capture went up; the name carries the capture it belongs to
    assert [name for _, name, _ in store.pushed] == [
        f"b1.bad-screen-{by_id['b1.bad-screen']['capture']['sha256'][:12]}.png",
        f"b1.unsure-{by_id['b1.unsure']['capture']['sha256'][:12]}.png"]
    assert store.pushed[0][0] == by_id["b1.bad-screen"]["capture"]["path"]
    # the step records the URL the store handed back, which is what the case's comment links
    assert out["b1.bad-screen"]["evidence"] == store.pushed[0][2]
    assert out["b1.unsure"]["evidence"] == store.pushed[1][2]
    # a passed step uploads nothing, even when a person did hand in a screenshot for it
    assert "evidence" not in out["b1.passed"] and "evidence" not in out["b1.ran-by-hand"]
    assert "evidence" not in out["b1.nobody-ran"]
    assert len(store.pushed) == 2
    # the run's own results are left as the runner recorded them
    assert "evidence" not in by_id["b1.bad-screen"]


def _sensitive_failure(tmp_path):
    """A sensitive step that fails, and a plain step after it: the gate holds the capture, the run goes on."""
    d = _privacy_script()
    d["batches"][0]["steps"][1]["check"]["args"]["is"] = "play.home"
    d["batches"][0]["steps"].append(step("b1.after"))
    results = run(d, TickingFake(tmp_path))
    assert [(r["id"], r["verdict"], r["held"]) for r in results] == [
        ("b1.plain", "passed", False),
        ("b1.file-dialog", "failed (expected ui.screen == play.home, observed play.library)", True),
        ("b1.after", "passed", False)]
    return results


def test_1244_a_capture_the_gate_withholds_is_recorded_withheld_and_the_run_goes_on(tmp_path):
    results = _sensitive_failure(tmp_path)
    store = _EvidenceStore()
    out = upload_evidence(results, store, tmp_path / "approvals.json")
    by_id = {r["id"]: r for r in out}
    assert by_id["b1.file-dialog"]["evidence"] == "withheld"
    assert store.pushed == []                       # nothing left the machine, and nothing else needed to
    assert [r["verdict"] for r in out] == [r["verdict"] for r in results]   # the run is unchanged by the gate


def test_1244_an_approved_capture_is_uploaded_and_the_step_records_its_url(tmp_path):
    results = _sensitive_failure(tmp_path)
    ap = tmp_path / "approvals.json"
    approve(ap, "b1.file-dialog", results[1]["capture"]["sha256"], by="sandro")
    store = _EvidenceStore()
    out = {r["id"]: r for r in upload_evidence(results, store, ap)}
    assert [name for _, name, _ in store.pushed] == [
        f"b1.file-dialog-{results[1]['capture']['sha256'][:12]}.png"]
    assert out["b1.file-dialog"]["evidence"] == store.pushed[0][2]


def test_1244_a_green_run_uploads_nothing(tmp_path):
    d = script(step("b1.a"), step("b1.b"))
    store = _EvidenceStore()
    out = upload_evidence(run(d, TickingFake(tmp_path)), store, tmp_path / "approvals.json")
    assert [r["verdict"] for r in out] == ["passed", "passed"]
    assert store.pushed == [] and all("evidence" not in r for r in out)


def test_1244_two_concurrent_uploads_do_not_collide_on_the_evidence_branch(tmp_path):
    import threading
    import time

    by_id, _ = _every_verdict_run(tmp_path)
    inside, overlaps = [], []

    class _SlowStore(_EvidenceStore):
        """Takes long enough that two unserialized pushes to the branch would be seen overlapping."""

        def __call__(self, path, name):
            inside.append(name)
            if len(inside) > 1:
                overlaps.append(list(inside))
            time.sleep(0.05)
            inside.remove(name)
            return super().__call__(path, name)

    store, ap, lock = _SlowStore(), tmp_path / "approvals.json", tmp_path / "evidence.lock"
    start = threading.Barrier(2)

    def one_run(result):
        start.wait()
        return upload_evidence([result], store, ap, lock_path=lock)

    threads = [threading.Thread(target=one_run, args=(by_id[i],)) for i in ("b1.bad-screen", "b1.unsure")]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert overlaps == []                       # the two pushes never stood on the branch at once
    assert len(store.pushed) == 2 and not lock.exists()


def test_1244_a_lock_a_dead_run_left_behind_is_taken_over(tmp_path):
    import os
    import time

    lock = tmp_path / "evidence.lock"
    lock.write_text("stale")
    old = time.time() - 3600
    os.utime(lock, (old, old))
    store = _EvidenceStore()
    by_id, results = _every_verdict_run(tmp_path)
    # a run that died holding the lock never blocks the next one for good
    out = upload_evidence([by_id["b1.bad-screen"]], store, tmp_path / "approvals.json", lock_path=lock)
    assert out[0]["evidence"] == store.pushed[0][2] and not lock.exists()


def _comment_on(results, cases, tmp_path, store=None):
    """Write a run back and return the case comment's body, as the write-back would post it."""
    from squad import to_test_scenario as tts
    out = upload_evidence(results, store or _EvidenceStore(), tmp_path / "approvals.json")
    plan = {"name": "tiny", "issues": [{"cases": [c]} for c in cases]}
    published = {"parent": 1, "issues": {c: 10 + i for i, c in enumerate(cases)}}
    gh = _ScenarioGitHub()
    tts.write_results(plan, {"run_id": "r-1244", "script_sha": "abc", "platform": "macos-arm64",
                             "build_sha": "def", "core_available": True, "results": out}, gh, published)
    return {kw["number"]: kw["body"] for kw in gh.of("comment")}


def test_1244_the_case_comment_shows_the_failed_steps_screenshot_inline(tmp_path):
    by_id, results = _every_verdict_run(tmp_path)
    bodies = _comment_on(results, ["b1.bad-screen"], tmp_path)
    url = {r["id"]: r for r in upload_evidence(results, _EvidenceStore(), tmp_path / "a.json")}[
        "b1.bad-screen"]["evidence"]
    assert url.startswith("https://") and url.endswith(".png?raw=true")
    assert f"![b1.bad-screen]({url})" in bodies[10]


def test_1244_a_withheld_capture_is_said_to_be_withheld_and_never_linked(tmp_path):
    results = _sensitive_failure(tmp_path)
    body = _comment_on(results, ["b1.file-dialog"], tmp_path)[10]
    assert "withheld" in body and "![" not in body


def _rows_with_captures(tmp_path):
    """One row per verdict, shaped as `scripts/gui_test_run_script.py` writes them (id, verdict, capture, held)."""
    rows = []
    for sid, verdict in (("a", "passed"), ("b", "failed (expected x, observed y)"), ("c", "needs review"),
                         ("d", "pending")):
        p = Path(_shot(tmp_path, f"{sid}.png"))
        rows.append({"id": sid, "batch": "b1", "mode": "automated", "expect": f"what {sid} shows", "verdict": verdict,
                     "capture": {"path": str(p), "size": p.stat().st_size,
                                 "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}, "held": False})
    return rows


def test_1244_the_run_command_publishes_only_when_a_repo_is_named(tmp_path):
    mod = _run_script_module()
    rows, store = _rows_with_captures(tmp_path), _EvidenceStore()
    # a command a person runs names no repo: nothing is published, the capture stays local (ADR-0135 rule 5)
    assert mod.attach_evidence(rows, None, tmp_path / "approvals.json", store=store) == rows
    assert store.pushed == []
    out = mod.attach_evidence(rows, "sbihaiko/MesenAI", tmp_path / "approvals.json",
                              store=store, lock=tmp_path / "evidence.lock")
    assert [name for _, name, _ in store.pushed] == [
        f"b-{rows[1]['capture']['sha256'][:12]}.png", f"c-{rows[2]['capture']['sha256'][:12]}.png"]
    assert [r.get("evidence") for r in out] == [None, store.pushed[0][2], store.pushed[1][2], None]


def test_1244_the_store_makes_the_orphan_evidence_branch_before_the_first_push(tmp_path):
    """The contents API refuses a branch it does not have, so a store that only PUTs fails on the very first run."""
    png = tmp_path / "a.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    calls, puts = [], []

    def runner(args, **kw):
        calls.append((args, kw.get("input")))
        if args[4].startswith("repos/sbihaiko/MesenAI/contents/"):
            puts.append(args)
            if len(puts) == 1:      # the branch is not there yet: what GitHub really answers
                raise subprocess.CalledProcessError(1, args, output="",
                                                    stderr="Branch gui-test-evidence not found")
            return '{"content": {"path": "x"}}'
        if args[4].endswith("/git/blobs"):
            return '{"sha": "blob1"}'
        if args[4].endswith("/git/trees"):
            return '{"sha": "tree1"}'
        if args[4].endswith("/git/commits"):
            return '{"sha": "commit1"}'
        return '{"ref": "refs/heads/gui-test-evidence"}'

    store = evidence_store("sbihaiko/MesenAI", runner=runner)
    url = store(str(png), "b1.bad-abc123.png")
    assert url == ("https://github.com/sbihaiko/MesenAI/blob/gui-test-evidence/b1.bad-abc123.png?raw=true")
    posts = [(a, i) for a, i in calls if a[4].startswith("repos/sbihaiko/MesenAI/git/")]
    assert [a[4].rsplit("/", 1)[-1] for a, _ in posts] == ["blobs", "trees", "commits", "refs"]
    body = {a[4].rsplit("/", 1)[-1]: json.loads(i) for a, i in posts}
    assert body["trees"] == {"tree": [{"path": "README.md", "mode": "100644", "type": "blob", "sha": "blob1"}]}
    assert body["commits"]["tree"] == "tree1" and "parents" not in body["commits"]   # a root commit: no shared history
    assert body["refs"] == {"ref": "refs/heads/gui-test-evidence", "sha": "commit1"}
    assert len(puts) == 2                                               # the one refused push is retried, not dropped


def test_1244_repushing_a_capture_already_on_the_branch_updates_it_with_its_blob_sha(tmp_path):
    """A second run of the same failing step pushes the same name; the contents API refuses an update without a sha."""
    png = tmp_path / "a.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    calls, puts = [], []

    def runner(args, **kw):
        calls.append((args, kw.get("input")))
        if "PUT" in args:
            puts.append(args)
            if len(puts) == 1:
                raise subprocess.CalledProcessError(1, args, output='', stderr='"sha" wasn\'t supplied.')
            return '{"content": {"path": "x"}}'
        return '{"sha": "oldsha"}'      # the GET that reads the file already on the branch

    store = evidence_store("sbihaiko/MesenAI", runner=runner)
    assert store(str(png), "b1.bad-abc123.png").endswith("b1.bad-abc123.png?raw=true")
    [read] = [a for a, _ in calls if "PUT" not in a]
    assert read[2] == "repos/sbihaiko/MesenAI/contents/b1.bad-abc123.png?ref=gui-test-evidence"
    assert "sha=oldsha" in puts[1] and len(puts) == 2


# --- #1241: the run command files its bug only when asked, and a partial run records its selection ---


def _bug_helper(tmp_path, output="Issue created: https://example.invalid/1241", exit_code=0):
    """A `scripts/report-bug.sh` stand-in, because that helper is the only route a run's bug takes.

    It logs every call it gets — the title in one file, the body in another — so a case can say how many bugs a run
    filed and what they said. `exit_code` is the helper failing the way a missing token or no network does.
    """
    root = tmp_path / "mesenai"
    helper = root / "scripts" / "report-bug.sh"
    helper.parent.mkdir(parents=True, exist_ok=True)
    script_text = '#!/bin/sh\nprintf "%s\\n" "$1" >> ' + json.dumps(str(root / "bug-titles.log")) + "\n"
    script_text += 'printf "%s" "$2" >> ' + json.dumps(str(root / "bug-body.log")) + "\n"
    script_text += f'echo "no network" >&2\nexit {exit_code}\n' if exit_code else \
        "printf '%s\\n' " + json.dumps(output) + "\n"
    helper.write_text(script_text)
    helper.chmod(0o755)
    return root, root / "bug-titles.log"


def _run_cmd(monkeypatch, tmp_path, script_path, *extra, root=None, spec=SHAPE_SPEC, registered=True,
             fresh_github=True):
    """The run command driven in-process on its own contract: argv in, one results file out.

    The adapter registry is how a fake adapter gets a name the command accepts; a literal `module:Class` spec is never
    the registered emulator adapter, which is what keeps a test's run off the board even when it asks for a bug. The bug
    doors are pointed at the fake GitHub (#1249), so a filing lands there and never on the real one; `fresh_github`
    False keeps the board between two runs of one case.
    """
    if fresh_github:
        fake_github.reset()
    extra = (*extra, "--github-runner", FAKE_GITHUB)
    import importlib.util
    monkeypatch.chdir(FORK)
    monkeypatch.setenv("MESENAI_ROOT", str(root if root is not None else tmp_path / "mesenai"))
    loaded = importlib.util.spec_from_file_location("gui_test_run_script_1241", RUN_SCRIPT)
    mod = importlib.util.module_from_spec(loaded)
    loaded.loader.exec_module(mod)
    adapter = "shape-1241"
    if registered:
        monkeypatch.setitem(mod.ADAPTERS, adapter, {"spec": spec, "env": (), "repo": "example/1241"})
    else:
        adapter = spec
    out = tmp_path / "results.json"
    code = mod.main([str(script_path), "--adapter", adapter, "--out", str(out),
                     "--artifacts", str(tmp_path / "artifacts"), *extra])
    return code, json.loads(out.read_text())


def _two_batch_file(tmp_path):
    path = tmp_path / "two-batches.json"
    path.write_text(json.dumps(_two_batch_script([step("b1.one")])))
    return path


def test_1241_a_red_run_files_no_bug_unless_it_is_asked(tmp_path, monkeypatch):
    """A red run used to file a bug by default: acceptance runs of #1222 left 9 duplicates on the real board."""
    root, titles = _bug_helper(tmp_path)
    code, r = _run_cmd(monkeypatch, tmp_path, SMOKE, root=root)
    assert code == 1 and r["status"] == "failed" and r["summary"]["failed"] == 2
    assert r["bug"]["filed"] is False and "--file-bug" in r["bug"]["reason"]
    assert not titles.exists(), "a run nobody asked for a bug must leave the board alone"


def test_1241_asked_it_files_exactly_one_bug_for_every_failed_step_and_records_its_url(tmp_path, monkeypatch):
    root, titles = _bug_helper(tmp_path)
    code, r = _run_cmd(monkeypatch, tmp_path, SMOKE, "--file-bug", root=root)
    assert code == 1
    assert titles.read_text().splitlines() == [
        "run-script-smoke: smoke.still-home — expected play.home, observed play.library (+1 more steps)"], \
        "one bug, never one per step, and its title says what failed"
    body = (root / "bug-body.log").read_text()
    assert "### `smoke.still-home`" in body and "### `smoke.unknown-id`" in body, "every failed step, in one bug"
    assert "### `smoke.open-library`" not in body, "a step that passed is not a failed step of this bug"
    assert r["bug"]["filed"] is True and r["bug"]["url"] == "https://example.invalid/1241"


def test_1241_asked_but_not_the_emulator_adapter_it_still_files_nothing(tmp_path, monkeypatch):
    root, titles = _bug_helper(tmp_path)
    code, r = _run_cmd(monkeypatch, tmp_path, SMOKE, "--file-bug", root=root, registered=False)
    assert code == 1 and r["bug"]["filed"] is False and "registered emulator adapter" in r["bug"]["reason"]
    assert not titles.exists(), "a run against a fake adapter never touches GitHub"


@pytest.mark.heavy
def test_1241_a_filing_that_fails_is_recorded_and_leaves_the_exit_code_alone(tmp_path, monkeypatch):
    """No token, no network: the run still ends red on its steps, and the file says why no bug is there."""
    root, _ = _bug_helper(tmp_path, exit_code=3)
    code, r = _run_cmd(monkeypatch, tmp_path, SMOKE, "--file-bug", root=root)
    assert code == 1 and r["status"] == "failed" and r["summary"]["failed"] == 2
    assert r["bug"]["filed"] is False and "report-bug.sh failed" in r["bug"]["reason"]
    clean = tmp_path / "clean.json"
    clean.write_text(json.dumps(script(step("b1.one"))))
    code, r = _run_cmd(monkeypatch, tmp_path, clean, "--file-bug", root=root)
    assert code == 0 and r["bug"]["filed"] is False and r["bug"]["reason"] == "no step failed"


def test_1241_a_bug_helper_that_cannot_run_is_recorded_and_never_raised(tmp_path, monkeypatch):
    root, _ = _bug_helper(tmp_path)
    (root / "scripts" / "report-bug.sh").chmod(0o644)
    code, r = _run_cmd(monkeypatch, tmp_path, SMOKE, "--file-bug", root=root)
    assert code == 1 and r["bug"]["filed"] is False and "report-bug.sh failed" in r["bug"]["reason"]


def test_1241_the_opt_out_flag_is_gone_because_filing_is_opt_in(tmp_path, monkeypatch):
    import importlib.util
    monkeypatch.chdir(FORK)
    loaded = importlib.util.spec_from_file_location("gui_test_run_script_1241_flag", RUN_SCRIPT)
    mod = importlib.util.module_from_spec(loaded)
    loaded.loader.exec_module(mod)
    with pytest.raises(SystemExit):
        mod.main([str(SMOKE), "--adapter", SHAPE_SPEC, "--out", str(tmp_path / "never.json"), "--no-report-bug"])


def test_1241_a_full_run_records_no_selection_and_a_narrowed_one_names_it(tmp_path, monkeypatch):
    path = _two_batch_file(tmp_path)
    code, r = _run_cmd(monkeypatch, tmp_path, path)
    assert code == 0 and "selection" not in r, "a full run is not a partial run"
    assert {s["id"] for s in r["steps"]} == {"b1.one", "b2.a"}
    code, r = _run_cmd(monkeypatch, tmp_path, path, "--batch", "b2")
    assert r["selection"] == {"kind": "batch", "value": "b2"}
    assert {s["id"] for s in r["steps"]} == {"b2.a"}


def test_1241_a_case_selection_keeps_the_setup_steps_it_needs(tmp_path, monkeypatch):
    d = _two_batch_script([step("b1.two"), step("b1.three")])
    d["batches"][0]["setup"] = [step("b1.setup-one")]
    path = tmp_path / "with-setup.json"
    path.write_text(json.dumps(d))
    code, r = _run_cmd(monkeypatch, tmp_path, path, "--case", "b1.two")
    assert code == 0 and r["selection"] == {"kind": "case", "value": "b1.two"}
    assert [s["id"] for s in r["steps"]] == ["b1.setup-one", "b1.two"]


def test_1241_an_unknown_selector_is_refused_before_anything_is_launched(tmp_path, monkeypatch):
    path = _two_batch_file(tmp_path)
    code, r = _run_cmd(monkeypatch, tmp_path, path, "--case", "b1.ghost")
    assert code != 0 and r["status"] == "failed"
    assert any("b1.ghost" in reason for reason in r["reasons"]), r["reasons"]
    assert any("b1.two" in reason or "b2.a" in reason for reason in r["reasons"]), "it names what the script does have"
    assert "selection" not in r
    assert not (tmp_path / "artifacts").exists(), "a refused selector must not launch the application"


# --- #1249: the bug a red run files names the failure, and the same failure is never filed twice ---

from squad.gui_test_report import bug_title, comment_on_bug_with, failure_key, failure_words, find_open_bug_with, report_failures_detail  # noqa: E402
from tests import gui_test_github_fake as fake_github  # noqa: E402

FAKE_GITHUB = "tests.gui_test_github_fake:shell"
REPO_1249 = "example/1249"


class _Board:
    """A fake GitHub runner around one board: the search door reads the issues it holds, the file door adds one.

    Filing is the fake board's own, not the checkout helper's: these cases are about which issue a run belongs to,
    and only the run-command case below goes through the helper route a real run takes.
    """

    def __init__(self, repo=REPO_1249):
        fake_github.reset()
        self.find_open_bug = find_open_bug_with(repo, fake_github.shell)
        self.comment_on_bug = comment_on_bug_with(repo, fake_github.shell)
        self.filed = []

    @property
    def issues(self):
        return fake_github.ISSUES

    @property
    def comments(self):
        return fake_github.COMMENTS

    @property
    def doors(self):
        return {"find_open_bug": self.find_open_bug, "comment_on_bug": self.comment_on_bug}

    def file_bug(self, title, body, priority=None):
        number = 1000 + len(self.issues) + 1
        url = f"https://example.invalid/bugs/{number}"
        self.issues.append({"number": number, "title": title, "body": body, "url": url})
        self.filed.append((title, body))
        return url

    def store(self, path, name):
        return f"https://evidence.example/{name}"


def test_1249_the_title_names_the_script_the_step_and_the_failure():
    """The old title was `GUI test <script>: N failed step(s)`: nine of them in one afternoon say nothing."""
    one = [{"id": "home.focus-holds-up", "verdict": "failed (expected play.home.open-rom, observed none)"}]
    assert failure_words("failed (expected play.home.open-rom, observed none)") == \
        "expected play.home.open-rom, observed none"
    assert failure_words("failed (timeout waiting for ui.screen == play.library)") == \
        "timeout waiting for ui.screen == play.library"
    assert failure_words("failed (unknown id)") == "unknown id"
    assert bug_title("play-pad-only", one) == \
        "play-pad-only: home.focus-holds-up — expected play.home.open-rom, observed none"
    two = one + [{"id": "home.open-library", "verdict": "failed (timeout waiting for ui.screen == play.library)"}]
    assert bug_title("play-pad-only", two) == \
        "play-pad-only: home.focus-holds-up — expected play.home.open-rom, observed none (+1 more steps)"
    other = [{"id": "home.focus-holds-up", "verdict": "failed (expected play.home.open-rom, observed play.library)"}]
    assert bug_title("play-pad-only", other) != bug_title("play-pad-only", one), "two failures, two titles"
    assert failure_key("play-pad-only", one[0]) == "play-pad-only: home.focus-holds-up — expected play.home.open-rom, observed none"
    for title in (bug_title("play-pad-only", one), bug_title("play-pad-only", two)):
        assert not re.search(r"\d+ failed step", title), title


def test_1249_a_run_of_a_failure_already_open_comments_instead_of_filing_a_second_bug(tmp_path):
    d = _two_failures_script()
    results = run(d, TickingFake(tmp_path))
    board = _Board()
    first = report_failures_detail(d, results, board.store, board.file_bug, tmp_path / "a.json",
                                   run={"run_id": "run-1", "build_sha": "abc123", "platform": "darwin-arm64"},
                                   **board.doors)
    assert first["existing"] is False and len(board.issues) == 1 and board.comments == []
    again = report_failures_detail(d, results, board.store, board.file_bug, tmp_path / "a.json",
                                   run={"run_id": "run-2", "build_sha": "def456", "platform": "darwin-arm64"},
                                   **board.doors)
    assert again["existing"] is True and again["url"] == first["url"], "the run belongs to the open bug"
    assert len(board.issues) == 1, "the same failure is one open bug, however many runs hit it"
    [comment] = board.comments
    assert comment["number"] == board.issues[0]["number"]
    for text in ("run-2", "def456", "### `b1.bad-screen`", "### `b1.never`",
                 "the library is showing", "failed (timeout waiting for ui.screen == nowhere)"):
        assert text in comment["body"], text


def test_1249_a_different_failure_is_a_second_bug_with_its_own_readable_title(tmp_path):
    board = _Board()
    d = _two_failures_script()
    report_failures_detail(d, run(d, TickingFake(tmp_path)), board.store, board.file_bug, tmp_path / "a.json",
                           **board.doors)
    other = _two_failures_script()
    other["batches"][0]["steps"][1]["check"]["args"]["is"] = "play.library"  # that step passes; the wait times out
    report_failures_detail(other, run(other, TickingFake(tmp_path)), board.store, board.file_bug, tmp_path / "a.json",
                           **board.doors)
    titles = [i["title"] for i in board.issues]
    assert len(titles) == 2 and len(set(titles)) == 2, titles
    assert titles[0].startswith("tiny: b1.bad-screen — ") and titles[1].startswith("tiny: b1.never — "), titles
    assert board.comments == [], "a different failure is a new bug, never a comment on an unrelated one"


def test_1249_a_look_up_that_cannot_run_files_a_bug_rather_than_losing_the_failure(tmp_path):
    """No `gh`, no network: the run still opens its bug, and the file says why it could not check for a twin."""
    board = _Board()
    board.find_open_bug = lambda key: (_ for _ in ()).throw(OSError("no gh"))
    d = _two_failures_script()
    detail = report_failures_detail(d, run(d, TickingFake(tmp_path)), board.store, board.file_bug,
                                    tmp_path / "a.json", **board.doors)
    assert detail["existing"] is False and len(board.issues) == 1
    assert "look-up" in detail["lookup_failed"]


@pytest.mark.heavy
def test_1249_the_run_command_records_the_issue_the_run_belongs_to(tmp_path, monkeypatch):
    root, titles = _bug_helper(tmp_path)
    fake_github.reset()
    code, first = _run_cmd(monkeypatch, tmp_path, SMOKE, "--file-bug", root=root)
    assert code == 1 and first["bug"]["filed"] is True
    assert first["bug"]["existing"] is False and len(fake_github.ISSUES) == 1
    assert first["bug"]["url"] == fake_github.ISSUES[0]["url"] and fake_github.ISSUES[0]["url"]
    assert first["bug"]["title"].startswith("run-script-smoke: smoke.still-home — ")
    code, again = _run_cmd(monkeypatch, tmp_path, SMOKE, "--file-bug", root=root, fresh_github=False)
    assert again["bug"]["filed"] is True and again["bug"]["existing"] is True
    assert again["bug"]["url"] == first["bug"]["url"], "the second run names the issue it was attached to"
    assert len(fake_github.ISSUES) == 1 and len(titles.read_text().splitlines()) == 1, "one bug, never two"
    [comment] = fake_github.COMMENTS
    assert again["run_id"] in comment["body"] and again["build_sha"] in comment["body"]
    assert "### `smoke.still-home`" in comment["body"] and "### `smoke.unknown-id`" in comment["body"]


def test_1249_a_comment_that_cannot_be_posted_still_reports_the_run(tmp_path):
    """An open bug the run cannot comment on is still a run to report: it files its own, and says why."""
    board = _Board()
    d = _two_failures_script()
    results = run(d, TickingFake(tmp_path))
    report_failures_detail(d, results, board.store, board.file_bug, tmp_path / "a.json", **board.doors)
    board.comment_on_bug = lambda issue, body: (_ for _ in ()).throw(OSError("no network"))
    detail = report_failures_detail(d, results, board.store, board.file_bug, tmp_path / "a.json", **board.doors)
    assert detail["existing"] is False and len(board.issues) == 2 and board.comments == []
    assert "comment" in detail["lookup_failed"] and board.issues[0]["url"] in detail["lookup_failed"]
@pytest.mark.host_bound(reason="needs the MesenAI pilot script to narrow to the pad adapter (MESENAI_REPO)")
def test_1242_the_pilot_script_narrows_to_what_the_pad_adapter_accepts_without_losing_a_step():
    """The whole committed pilot script against a target that advertises less than `mesen-gui` does: the pilot as
    committed is refused before launch (item 6) - dozens of its steps observe `ui.focused`, which this target has no
    check for - and the narrowing is a `gui-test/1` script the same validator accepts, with every step of the pilot
    still in it, `manual` where it must be, naming what it is missing (MesenAI #1242).

    The committed pilot is already #1251's narrowing against the real adapter, so what this takes away is the
    capability drift moves: the day the hook loses a check, the script committed against it narrows again instead of
    going stale in silence."""
    from squad import to_test_scenario as tts
    from squad.gui_test_subset import runnable
    from squad.to_test_scenario import coverage, read_scenario, read_script, script_steps
    pilot = _pilot_1223()
    caps = {"actions": ["pad.press"], "checks": ["ui.screen", "ui.visible", "ui.dialogs"],
            "input_families": ["pad"], "variants": {"window.mode": ["windowed"]}}
    with pytest.raises(ScriptError):
        validate(pilot, caps)
    out = runnable(pilot, caps)
    validate(out["script"], caps)
    assert tts.step_ids(out["script"]) == tts.step_ids(pilot)
    steps = [s for b in out["script"]["batches"] for p in ("setup", "steps", "teardown") for s in (b.get(p) or [])]
    assert len(steps) == len(tts.step_ids(pilot))  # converted steps are counted once, and none is dropped
    assert all(s["mode"] == "manual" or (s.get("check") or {}).get("name") in caps["checks"] for s in steps)
    by_id = {s["id"]: s for s in steps}
    assert all(e["missing"] and by_id[e["id"]]["mode"] == "manual" for e in out["manual"])
    assert all(m in by_id[e["id"]]["expect"] for e in out["manual"] for m in e["missing"])  # a person reads why
    docs = _mesenai() / "docs" / "validation" / "process"
    script_path, md_path = docs / "play-pad-only.gui-test.json", docs / "play-pad-only.gui-test.md"
    if not (script_path.exists() and md_path.exists()):
        pytest.skip("MESENAI_REPO holds no committed pilot script and its rendered Markdown")
    script, scenario = read_script(script_path), read_scenario(md_path)
    ids = [s["id"] for s in script_steps(script)]
    report = coverage(scenario, script)
    assert len(ids) == len(set(ids)) >= 67
    assert (report["total_cases"], report["total_steps"]) == (len(ids), len(ids))
    assert report["missing_steps"] == [] and report["steps_without_case"] == []
    # The pilot is converted: every step records the content hash of its case as the Markdown has it now, so the
    # script is reusable - no case is uncovered, no step is orphaned and nothing is stale.
    assert report["reusable"] is True and report["stale"] == []


@pytest.mark.host_bound(reason="needs the MesenAI pilot script and its content hashes (MESENAI_REPO)")
def test_1242_a_script_with_its_content_hashes_stripped_is_refused_as_stale_not_for_gaps(tmp_path):
    """The narrowing's other half, pinned without leaning on the committed pilot being un-converted: the same script
    with every step's `content_hash` removed still covers every case and orphans no step - so `reusable` is what the
    hashes decide, never the ids - and every case is stale, since nothing says which version of the case the step was
    converted from. Recording the hashes again is the whole of what makes it reusable (MesenAI #1242)."""
    from squad.to_test_scenario import coverage, read_scenario, read_script, record_hashes
    docs = _mesenai() / "docs" / "validation" / "process"
    script_path, md_path = docs / "play-pad-only.gui-test.json", docs / "play-pad-only.gui-test.md"
    if not (script_path.exists() and md_path.exists()):
        pytest.skip("MESENAI_REPO holds no committed pilot script and its rendered Markdown")
    script, scenario = read_script(script_path), read_scenario(md_path)
    stripped = copy.deepcopy(script)
    for batch in stripped["batches"]:
        for part in ("setup", "steps", "teardown"):
            for s in batch.get(part, []):
                s.pop("content_hash", None)
    path = tmp_path / "play-pad-only.gui-test.json"
    path.write_text(json.dumps(stripped), encoding="utf-8")
    report = coverage(scenario, read_script(path))  # still a `gui-test/1` script the validator accepts
    assert report["missing_steps"] == [] and report["steps_without_case"] == []
    assert report["stale"] == [c["id"] for c in scenario["cases"]]
    assert report["reusable"] is False
    assert coverage(scenario, record_hashes(scenario, read_script(path)))["reusable"] is True


# --- #1243: the run command runs one variant of a script, and records it as a partial run ---

VARIANT_SPEC = "tests.gui_test_run_script_fake:VariantAdapter"
ONE_MODE_SPEC = "tests.gui_test_run_script_fake:OneModeAdapter"


def _variant_file(tmp_path):
    """Two batches: b1 carries one case per window mode plus an untagged one, b2 a single untagged case."""
    d = _two_batch_script([step("b1.any"),
                           step("b1.win", variants={"window.mode": "windowed"}),
                           step("b1.fs", variants={"window.mode": "fullscreen"})])
    path = tmp_path / "variants.json"
    path.write_text(json.dumps(d))
    return path


def test_1243_a_variant_selection_runs_that_variant_only_and_records_it(tmp_path, monkeypatch):
    """A full run of this script runs all three cases; one variant runs the cases it applies to, and says so."""
    code, r = _run_cmd(monkeypatch, tmp_path, _variant_file(tmp_path), "--variant", "window.mode=fullscreen",
                       spec=VARIANT_SPEC)
    assert code == 0, r["reasons"]
    assert r["selection"] == {"kind": "variant", "value": "window.mode=fullscreen"}
    assert [s["id"] for s in r["steps"]] == ["b1.any", "b1.fs", "b2.a"], \
        "the windowed case is not run, and an untagged case runs in every variant"
    switched = (tmp_path / "artifacts" / "variant.log").read_text().splitlines()
    assert switched == ["window.mode=fullscreen"] * 2, "each batch's fresh process is switched before it launches"


def test_1243_an_axis_that_runs_one_way_needs_no_switch(tmp_path, monkeypatch):
    """The emulator adapter advertises `window.mode: [windowed]` and has no hook: selecting it filters, nothing switches."""
    code, r = _run_cmd(monkeypatch, tmp_path, _variant_file(tmp_path), "--variant", "window.mode=windowed",
                       spec=ONE_MODE_SPEC)
    assert code == 0, r["reasons"]
    assert r["selection"] == {"kind": "variant", "value": "window.mode=windowed"}
    assert [s["id"] for s in r["steps"]] == ["b1.any", "b1.win", "b2.a"]


def test_1243_an_unknown_variant_is_refused_before_anything_is_launched(tmp_path, monkeypatch):
    path = _variant_file(tmp_path)
    code, r = _run_cmd(monkeypatch, tmp_path, path, "--variant", "window.mode=huge")
    assert code != 0 and r["status"] == "failed" and "selection" not in r
    assert any("window.mode=huge" in reason for reason in r["reasons"]), r["reasons"]
    assert any("fullscreen" in reason for reason in r["reasons"]), "it names what the script does have"
    assert not (tmp_path / "artifacts").exists(), "a refused selector must not launch the application"
    code, r = _run_cmd(monkeypatch, tmp_path, path, "--variant", "screen.size=large")
    assert code != 0 and any("screen.size" in reason for reason in r["reasons"]), r["reasons"]
    assert not (tmp_path / "artifacts").exists()


def test_1243_a_variant_that_is_not_an_axis_value_pair_is_refused(tmp_path, monkeypatch):
    code, r = _run_cmd(monkeypatch, tmp_path, _variant_file(tmp_path), "--variant", "fullscreen")
    assert code != 0 and any("<axis>=<value>" in reason for reason in r["reasons"]), r["reasons"]
    assert not (tmp_path / "artifacts").exists()


def test_1243_a_variant_the_adapter_cannot_switch_to_is_refused_before_any_launch(tmp_path, monkeypatch):
    """The shape fake advertises both window modes and has no `set_variant` hook: nothing would switch the window, so
    running its fullscreen cases anyway would report a mode that was never entered."""
    code, r = _run_cmd(monkeypatch, tmp_path, _variant_file(tmp_path), "--variant", "window.mode=fullscreen")
    assert code != 0 and r["status"] == "failed" and r["steps"] == [] and r["batches"] == []
    assert any("window.mode=fullscreen" in reason for reason in r["reasons"]), r["reasons"]
    assert r["selection"] == {"kind": "variant", "value": "window.mode=fullscreen"}


def test_1243_a_variant_no_case_applies_to_executes_nothing_and_is_red(tmp_path, monkeypatch):
    """A run that executed no step is a failure, and a variant selection does not change that (#1222's rule)."""
    d = script(step("b1.win", variants={"window.mode": "windowed"}))  # one batch, and no case runs in fullscreen
    path = tmp_path / "one-mode.json"
    path.write_text(json.dumps(d))
    code, r = _run_cmd(monkeypatch, tmp_path, path, "--variant", "window.mode=fullscreen", spec=VARIANT_SPEC)
    assert code == 1 and r["status"] == "failed"
    assert r["reasons"] == ["no step executed"] and r["steps"] == []
    assert r["selection"] == {"kind": "variant", "value": "window.mode=fullscreen"}


def test_1243_the_three_selectors_stay_mutually_exclusive(tmp_path, monkeypatch):
    import importlib.util
    monkeypatch.chdir(FORK)
    loaded = importlib.util.spec_from_file_location("gui_test_run_script_1243", RUN_SCRIPT)
    mod = importlib.util.module_from_spec(loaded)
    loaded.loader.exec_module(mod)
    with pytest.raises(SystemExit):
        mod.main([str(SMOKE), "--adapter", SHAPE_SPEC, "--out", str(tmp_path / "never.json"),
                  "--batch", "b1", "--variant", "window.mode=windowed"])



# --- to_test_scenario (#1225): publishing the plan is idempotent, resumable and paced -------------

# What a published body must never carry: a Portuguese sentence over English prose, or a tool's signature line.
_ATTRIBUTION = re.compile(r"co-authored-by|generated with|claude\b|🤖", re.I)
_PT_BR = re.compile(r"\b(não|uma|para|com|sem|caso|casos|jogo|tela|janela|botão|arquivo|quando|então|também|deve|"
                    r"executa|verifica)\b", re.I)


def _assert_en_us_with_no_attribution(bodies):
    assert bodies
    for body in bodies:
        assert not _ATTRIBUTION.search(body), body[:200]
        assert not _PT_BR.search(body), body[:200]


class _PublishGitHub:
    """A recorded-call stand-in for the real REST runner: no network, no `gh`, and a failing create."""

    def __init__(self, fail_on_create=None, start=100):
        self.calls, self.made = [], []
        self.next, self.creates = start, 0
        self.fail_on_create = fail_on_create

    def __call__(self, op, **kw):
        self.calls.append((op, kw))
        if op == "item_status":
            return "Backlog"           # the parent is boarded in Backlog (#895)
        if op == "create_issue":
            self.creates += 1
            if self.fail_on_create and self.creates == self.fail_on_create:
                raise RuntimeError("the connection dropped")
            self.next += 1
            made = {"number": self.next, "id": 900 + self.next}
            self.made.append((kw, made))
            return made
        return {}

    def of(self, op):
        return [kw for o, kw in self.calls if o == op]

    def ops(self):
        return [o for o, _ in self.calls]


def _pilot_1225():
    """The committed pilot as a plan: its scenario, its script with the content hashes a conversion records."""
    from squad.to_test_scenario import build_plan, read_scenario, read_script, record_hashes

    docs = _mesenai() / "docs" / "validation" / "process"
    script_path, md_path = docs / "play-pad-only.gui-test.json", docs / "play-pad-only.gui-test.md"
    if not (script_path.exists() and md_path.exists()):
        pytest.skip("MESENAI_REPO holds no committed pilot script and its rendered Markdown")
    scenario = read_scenario(md_path)
    plan = build_plan(scenario, record_hashes(scenario, read_script(script_path)))
    return script_path, plan


def _nodes(plan):
    """The issues a tree publish makes besides the scenario and the cases: each area, and each area's containers."""
    return len(plan["areas"]) + sum(len(area["containers"]) for area in plan["areas"])


@pytest.mark.host_bound(reason="needs the committed MesenAI pilot to publish its parent (MESENAI_REPO)")
def test_1225_the_committed_pilot_publishes_its_parent_and_links_every_case_natively(tmp_path):
    from squad import to_test_scenario as tts

    script_path, plan = _pilot_1225()
    map_path = tmp_path / tts.issues_path(script_path).name
    gh = _PublishGitHub()
    out = tts.publish(plan, gh, map_path=map_path, board=True)

    assert len(plan["issues"]) >= 67
    labels = {kw["name"] for kw in gh.of("ensure_label")}
    assert {"Test-Case", "ready-for-agent"} <= labels
    assert [kw["title"] for kw in gh.of("create_issue")][0] == plan["parent"]["title"]
    assert len(gh.of("create_issue")) == len(plan["issues"]) + _nodes(plan) + 1
    # The relation names each issue's numeric id, and only the parent reaches the board.
    assert {kw["sub"] for kw in gh.of("add_sub_issue")} == {made["id"] for _, made in gh.made[1:]}
    assert gh.of("board") == [{"number": out["parent"]}]
    # The map beside the script is what a second machine resumes from.
    saved = tts.read_issues(map_path)
    assert saved == out["map"] and saved["parent"] == out["parent"] and saved["layout"] == "areas"
    assert all(case["hash"] for case in out["cases"].values())
    _assert_en_us_with_no_attribution([kw["body"] for kw in gh.of("create_issue")])


@pytest.mark.host_bound(reason="needs the committed MesenAI pilot to resume an interrupted publish (MESENAI_REPO)")
def test_1225_an_interrupted_pilot_publish_resumes_from_the_map_and_a_second_publish_updates(tmp_path):
    from squad import to_test_scenario as tts

    script_path, plan = _pilot_1225()
    map_path = tmp_path / tts.issues_path(script_path).name
    with pytest.raises(RuntimeError):
        tts.publish(plan, _PublishGitHub(fail_on_create=5), map_path=map_path, board=True)
    saved = tts.read_issues(map_path)
    assert saved["parent"] and len(saved["cases"]) == 1      # the parent, an area, its container and one case made it

    resumed = _PublishGitHub(start=5000)
    out = tts.publish(plan, resumed, map_path=map_path, board=True)
    assert len(resumed.of("create_issue")) == len(plan["issues"]) + _nodes(plan) + 1 - 4
    assert len(out["cases"]) == len(plan["issues"]) and out["removed"] == []
    assert len({case["number"] for case in out["cases"].values()}) == len(plan["issues"])
    assert resumed.of("board") == []                          # the parent is already on the board

    smaller = copy.deepcopy(plan)
    last = smaller["issues"].pop()["id"]
    for area in smaller["areas"]:
        for container in area["containers"]:
            container["cases"] = [i for i in container["cases"] if i["id"] != last]
    again = _PublishGitHub(start=9000)
    dropped = tts.publish(smaller, again, map_path=map_path, board=True)
    assert again.of("create_issue") == []                     # the stub lists no sub-issues, so links are not asserted
    assert dropped["removed"] == [plan["issues"][-1]["id"]]
    assert "close" not in again.ops()                         # a removed case is reported, never closed


# --- to_test_scenario close-out (#1227): the pilot publishes through the command, within GitHub's sub-issue cap ----

@pytest.mark.host_bound(reason="needs the committed MesenAI pilot to publish per batch (MESENAI_REPO)")
def test_1227_the_pilot_publishes_per_batch_and_per_case_as_a_tree_within_the_sub_issue_cap(tmp_path):
    import shutil

    from squad import to_test_scenario as tts

    docs = _mesenai() / "docs" / "validation" / "process"
    if not (docs / "play-pad-only.gui-test.json").exists():
        pytest.skip("MESENAI_REPO holds no committed pilot script")
    md, script = tmp_path / "pilot.md", tmp_path / "pilot.gui-test.json"
    shutil.copy(docs / "play-pad-only.gui-test.md", md)
    shutil.copy(docs / "play-pad-only.gui-test.json", script)
    cases = len(tts.read_scenario(md)["cases"])

    class Fake:
        def __init__(self):
            self.n, self.creates = 100, 0

        def __call__(self, op, **kw):
            if op != "create_issue":
                return {}
            self.n, self.creates = self.n + 1, self.creates + 1
            return {"number": self.n, "id": 9000 + self.n}

    per_case = Fake()
    if cases > tts.SUB_ISSUE_CAP:
        # The area tree (ADR-0275) holds the cap per node, so a pilot over 100 cases publishes per case instead of being refused.
        assert tts.main(["publish", str(md), str(script), "--stamp"], github=per_case) == 0
        plan = tts.build_plan(tts.read_scenario(md), tts.read_script(script))
        assert per_case.creates == len(plan["issues"]) + len(plan["areas"]) + sum(len(a["containers"]) for a in plan["areas"]) + 1
        tts.issues_path(script).unlink()
    per_batch = Fake()
    assert tts.main(["publish", str(md), str(script), "--granularity", "batch"], github=per_batch) == 0
    plan = tts.build_plan(tts.read_scenario(md), tts.read_script(script), granularity="batch")
    assert per_batch.creates == len(plan["issues"]) + len(plan["areas"]) + sum(len(a["containers"]) for a in plan["areas"]) + 1
    assert len(plan["issues"]) == len(tts.read_script(script)["batches"])
    assert tts.read_issues(tts.issues_path(script))["parent"] is not None
# --- #1286: every case of the committed pad-only pilot, run on the squad's GUI test infrastructure ---

from squad.gui_test_scenario import cases as scenario_cases  # noqa: E402
from squad.gui_test_scenario import run_scenario  # noqa: E402
from tests.gui_test_pilot_fake_1286 import ADAPTERS as PILOT_ADAPTERS  # noqa: E402
from tests.gui_test_pilot_fake_1286 import MANUAL_ONLY_BATCHES  # noqa: E402


def _automated_1286(script):
    """The ids of the script's automated cases (#1399): every case whose Mode is not `manual`, in script order.

    Derived from the script instead of frozen: the pilot is read from MesenAI's checkout, so a hardcoded list went red
    on the fork base whenever MesenAI added a case (#1203) with no fork commit to blame."""
    return [c["id"] for c in scenario_cases(script) if c["mode"] != "manual"]


HOLDS_1286 = ["home.focus-holds-up", "home.focus-holds-down", "home.focus-holds-left", "home.focus-holds-right"]


def _pilot_1286():
    return _pilot_1223()


@pytest.mark.host_bound(reason="needs the committed MesenAI pilot script to list its automated ids (MESENAI_REPO)")
def test_1286_the_expected_automated_ids_follow_the_script_not_a_frozen_list():
    """#1399: the fork reads the pilot from MesenAI's checkout, so a MesenAI-only change (a case added in #1203) must not
    redden the fork base. The expectation is the script's own non-manual cases, so flipping one case's mode moves it."""
    d = json.loads(json.dumps(_pilot_1286()))
    before = _automated_1286(d)
    flipped = next(c["id"] for c in scenario_cases(d) if c["mode"] == "manual")
    next(s for b in d["batches"] for part in ("setup", "steps", "teardown") for s in b.get(part) or []
         if s["id"] == flipped)["mode"] = "auto"
    after = _automated_1286(d)
    assert flipped in after and flipped not in before
    assert [i for i in after if i != flipped] == before


def _home_1286(tmp_path):
    """The committed home batch alone, on the adapter the roster names for it: the seven automated cases, nothing else."""
    d = _pilot_1286()
    batch = next(b for b in d["batches"] if b["id"] == "home")
    return run_scenario({**d, "batches": [batch]}, PILOT_ADAPTERS, out_root=tmp_path)


@pytest.mark.host_bound(reason="needs the MesenAI Home batch on a fresh profile (MESENAI_REPO)")
def test_1286_the_home_keeps_its_ring_on_a_fresh_profile_so_the_hold_cases_run(tmp_path):
    """A fresh settings profile has no play history and no favorites, so the Home holds Open a ROM alone and a D-pad
    press stays on it - which is what the four `home.focus-holds-*` cases and the real emulator say (#1137, and the
    real-app case above). An adapter whose Home keeps four tiles whatever the profile lets Up escape to Recent, and
    that one escape is enough to make the whole home batch - six of the scenario's fifteen automated cases - unrunnable.
    """
    out = _home_1286(tmp_path)
    by_id = {r["id"]: r["verdict"] for r in out["rows"]}
    assert [by_id[i] for i in HOLDS_1286] == ["passed"] * 4, by_id


@pytest.mark.host_bound(reason="needs the MesenAI Home batch to run end to end (MESENAI_REPO)")
def test_1286_the_committed_home_batch_runs_end_to_end_on_the_roster(tmp_path):
    out = _home_1286(tmp_path)
    by_id = {r["id"]: r["verdict"] for r in out["rows"]}
    home = [c for c in _automated_1286(_pilot_1286()) if c.startswith("home.")]
    assert set(HOLDS_1286) <= set(home)
    assert [by_id[i] for i in home] == ["passed"] * len(home), by_id


@pytest.mark.host_bound(reason="needs the committed MesenAI pilot script to check its batch wiring (MESENAI_REPO)")
def test_1286_every_batch_of_the_committed_pilot_is_wired_to_an_adapter():
    """No batch of the committed script is left without a target, and the roster's `manual` claim holds: a batch it
    calls manual-only has no automated case the adapter would have to answer."""
    d = _pilot_1286()
    rows = {c["id"]: c for c in scenario_cases(d)}
    assert set(PILOT_ADAPTERS) == {b["id"] for b in d["batches"]}
    for batch in d["batches"]:
        modes = {rows[s["id"]]["mode"] for part in ("setup", "steps", "teardown") for s in batch.get(part) or []}
        if batch["id"] in MANUAL_ONLY_BATCHES:
            assert modes == {"manual"}, f"batch {batch['id']} is on a launch-only adapter and has an automated case"


@pytest.mark.host_bound(reason="needs the committed MesenAI pilot script to give every case a verdict (MESENAI_REPO)")
def test_1286_every_case_of_the_committed_pilot_gets_a_verdict_and_the_automated_ones_pass(tmp_path):
    """The whole scenario in one call: every case of the committed script, in script order, none dropped and none
    added; the automated cases (Mode != manual) pass on the roster and every other case is `pending` for a person."""
    d = _pilot_1286()
    out = run_scenario(d, PILOT_ADAPTERS, out_root=tmp_path)
    assert [r["id"] for r in out["rows"]] == [s["id"] for s in scenario_cases(d)]
    total = len(scenario_cases(d))
    automated = _automated_1286(d)
    assert automated and total > len(automated)
    assert out["summary"]["cases"] == total and len(out["rows"]) == total
    assert out["status"] == "passed" and out["reasons"] == []
    assert out["summary"]["failed"] == 0 and out["summary"]["not executed"] == 0
    assert [r["id"] for r in out["rows"] if r["verdict"] == "passed"] == automated
    assert {r["id"] for r in out["rows"] if r["verdict"] == "pending"} == \
        {c["id"] for c in scenario_cases(d)} - set(automated)
    assert out["summary"]["pending"] == total - len(automated)


@pytest.mark.host_bound(reason="needs the committed MesenAI pilot script to name the case that blocks it (MESENAI_REPO)")
def test_1286_a_case_names_the_case_that_blocks_it():
    """The Blocked-by column of the scenario's case issues: a step waits for its batch's last setup step."""
    rows = {c["id"]: c for c in scenario_cases(_pilot_1286())}
    assert rows["home.focus-holds-up"]["blocked_by"] == "home.first-focus"
    assert rows["lib.open-screen"]["blocked_by"] == "lib.reach-library"
    assert rows["launch.first-run-home"]["blocked_by"] is None
    assert rows["pause-pack-multi.pack-focus"]["blocked_by"] == "pause-pack-multi.chord-opens"


def test_1286_a_batch_the_roster_cannot_serve_is_not_executed_and_never_dropped(tmp_path):
    """A batch with no adapter is not skipped in silence: its cases keep their row, say `not executed` and name the
    batch, and the run is red - a scenario whose cases nothing ran is not a green scenario."""
    out = run_scenario(script(step("b1.auto"), step("b1.look", mode="manual")), {}, out_root=tmp_path)
    assert [r["id"] for r in out["rows"]] == ["b1.auto", "b1.look"]
    assert {r["verdict"] for r in out["rows"]} == {"not executed (no adapter for batch b1)"}
    assert out["batches"] == [{"id": "b1", "verdict": "not executed",
                               "reason": "the roster has no adapter for batch b1"}]
    assert out["status"] == "failed"
    assert out["reasons"] == ["no case executed", "batch b1 was not executed: the roster has no adapter for batch b1"]
    assert out["summary"] == {"passed": 0, "failed": 0, "pending": 0, "not executed": 2, "cases": 2, "run": 0}


def test_1286_a_failed_case_makes_the_scenario_red(tmp_path):
    """A deviation the roster's adapter cannot show: the step fails, and the run names the case."""
    d = script(step("b1.auto", check={"name": "ui.focused", "args": {"is": "play.ghost"}},
                    wait={"check": "ui.focused == play.ghost", "timeout_ticks": 2}))
    out = run_scenario(d, {"b1": TickingFake}, out_root=tmp_path)
    assert out["status"] == "failed" and out["reasons"] == ["1 case(s) failed: b1.auto"]
    assert out["rows"][0]["verdict"] == "failed (timeout waiting for ui.focused == play.ghost)"


# --- #1276: the runner hands MESEN_GUI_WINDOW, the emulator adapter's own window switch, on unchanged ---

# The adapter reads the switch out of its own environment (`mesen_gui_adapter.window_mode()`), so a run can only carry
# it if the runner starts the adapter's process with it set. The runner's `env` tuple names the environment, and the
# three preconditions are the only names it checks: the switch is a pass-through, unset meaning the adapter's default.
# The stand-in below is the real adapter's class under its real spec, in a throwaway MESENAI_ROOT, so the case reads
# what the command actually handed a worker without a build behind it.
_WINDOW_MODE_LOGGER = '''"""A stand-in for MesenAI's adapter that records the window switch it was started with (#1276)."""
import os
from pathlib import Path

from tests.gui_test_run_script_fake import ShapeAdapter


class MesenGuiAdapter(ShapeAdapter):
    def __init__(self, out_dir, **kw):
        super().__init__(out_dir)
        (Path(out_dir) / "window-mode.log").write_text(os.environ.get("MESEN_GUI_WINDOW", "(unset)") + "\\n")
'''


def test_1276_a_run_hands_the_window_switch_to_the_adapter_unchanged(tmp_path):
    """MESEN_GUI_WINDOW is part of the emulator adapter's environment: a run must reach the adapter with it set."""
    import ctypes
    import sysconfig

    mod = _run_script_module()
    assert "MESEN_GUI_WINDOW" in mod.ADAPTERS["mesen-gui"]["env"], \
        "the emulator adapter's environment must carry the window switch the adapter itself reads"

    pkg = tmp_path / "mesenai" / "scripts" / "gui_test"
    pkg.mkdir(parents=True)
    (pkg / "mesen_gui_adapter.py").write_text(_WINDOW_MODE_LOGGER)
    binary = tmp_path / "Mesen"
    binary.write_bytes(b"#!/bin/sh\n")
    # The gate loads the core, so the stand-in points it at a shared library that really loads: this interpreter's own.
    # INSTSONAME is not a file name on the macOS framework build -- it is `Python.framework/Versions/3.12/Python`, a path
    # under the framework root -- so the join names no file there and the library LIBDIR really holds is `libpython*.dylib`.
    # Both readings are tried, and the file has to be there.
    name = sysconfig.get_config_var("INSTSONAME") or sysconfig.get_config_var("LDLIBRARY")
    libdir = Path(sysconfig.get_config_var("LIBDIR") or "")
    cands = [libdir / str(name), *(sorted(libdir.glob("libpython*.dylib")) + sorted(libdir.glob("libpython*.so")))]
    core = next((c for c in cands if c.is_file()), None)
    assert core is not None, f"no shared library of this interpreter to stand in for the core; tried {cands}"
    assert ctypes.CDLL(str(core)), core

    clean = tmp_path / "clean.json"
    clean.write_text(json.dumps(script(step("b1.one"))))
    # `any` is the escape hatch a runner without a primary display sets; `""` is the value the adapter reads as its own
    # default. Both reach the adapter byte for byte, and neither makes the run red: the switch is handed on, not checked.
    for value in ("any", ""):
        out = tmp_path / f"results-{value or 'default'}.json"
        p = _run_script_cmd(clean, out, spec="mesen-gui",
                            env={"MESENAI_ROOT": str(tmp_path / "mesenai"), "MESEN_GUI_BINARY": str(binary),
                                 "MESEN_CORE_LIB": str(core), "MESEN_GUI_WINDOW": value})
        assert p.returncode == 0, p.stdout + p.stderr
        assert json.loads(out.read_text())["status"] == "passed"
        seen = sorted(Path(str(out) + ".artifacts").rglob("window-mode.log"))
        assert seen, "the run started no adapter process to record the switch"
        assert {f.read_text().strip() for f in seen} == {value}, [f.read_text() for f in seen]


def test_1392_the_gui_suite_command_goes_through_the_serial_lock_and_the_shell_suites_beside_it(tmp_path):
    """#1392/#1410 where they live now, with the local gate retired (#115): the profile's GUI command is handed through
    `serial.sh gui-suite` unchanged (quotes and env intact) and takes that one lock, and the squad's own shell suites are
    the other half of the gate -- they take no lock and every red one is named."""
    scripts = Path(__file__).resolve().parent.parent / "contrib" / "squad-goal" / "scripts"
    d = tmp_path / "contrib" / "squad-goal" / "scripts"
    d.mkdir(parents=True)
    for name in ("shell-suites.sh", "serial.sh", "caps.sh"):   # caps.sh holds the git scrub shell-suites.sh requires (#2306)
        (d / name).write_text((scripts / name).read_text())
    suite = d / "test-x.sh"
    suite.write_text("#!/bin/bash\nexit 0\n")
    # Its own lock directory, so this run's gui-suite lock is not the one a suite run around this test already holds.
    env = {**os.environ, "SG_STATE": str(tmp_path / "state")}
    env.pop("SG_SERIAL_HELD", None)
    seen = tmp_path / "seen"
    gui = f"MESENAI_ROOT=/r MESEN_GUI_BINARY='/b b' sh -c 'echo \"$MESENAI_ROOT|$MESEN_GUI_BINARY\" > {seen}'"
    p = subprocess.run(["bash", str(d / "serial.sh"), "gui-suite", "bash", "-c", gui], cwd=tmp_path,
                       capture_output=True, text=True, timeout=15, env=env)
    assert p.returncode == 0, p.stdout + p.stderr
    assert seen.read_text().strip() == "/r|/b b"
    assert not (tmp_path / "state" / "locks" / "gui-suite").exists(), "the lock outlived the command it guarded"
    suite.write_text("#!/bin/bash\nexit 1\n")
    p = subprocess.run(["bash", str(d / "shell-suites.sh")], cwd=tmp_path, capture_output=True, text=True, timeout=60, env=env)
    assert p.returncode != 0 and "FAILED: test-x.sh" in p.stdout, p.stdout


def test_debt_filing_command_files_once_through_a_stub_gh(tmp_path):
    """#1394: the command files each DEBT finding with the debt labels, and a second run files nothing."""
    log = tmp_path / "issues.jsonl"
    gh = tmp_path / "bin" / "gh"
    gh.parent.mkdir()
    gh.write_text(
        "#!/bin/sh\n"
        f'LOG="{log}"; touch "$LOG"\n'
        'case "$*" in\n'
        '  *"/labels/debt"*) echo "{}" ;;\n'
        '  *"--method POST"*"/issues"*) cat >> "$LOG"; echo >> "$LOG"; echo \'{"number":1,"html_url":"https://x/1"}\' ;;\n'
        '  *"--paginate"*) awk \'BEGIN{printf "["; q=sprintf("%c",34)} length($0)>0 {k++; printf "%s{%snumber%s:%d,%s", (k>1?",":""), q, q, k, substr($0,2)} END{printf "]"}\' "$LOG" ;;\n'
        'esac\n')
    gh.chmod(0o755)
    review = tmp_path / "review.md"
    review.write_text("- BLOCKING a.py:1 — bad. Acceptance: fixed.\n- DEBT b.py:2 — no backoff. Acceptance: backs off.\n")
    # The environment is pinned, not inherited: `--owner`/`--project` default to $SG_OWNER/$SG_PROJECT, and the fork gate
    # exports both. With them set the filer also reads the project board (`gh api users/<owner>`), which this stub does not
    # answer, so the run died in `_base()` with a JSONDecodeError -- red in the gate, green from a bare shell, with nothing
    # in the failure about the filer. Same trap as FG_SUITES/SG_STATE in a fixture that runs the gate.
    env = {**os.environ, "PATH": f"{gh.parent}{os.pathsep}{os.environ['PATH']}", "SG_OWNER": "", "SG_PROJECT": ""}
    cmd = ["python", "-m", "squad.debt_filing", "--repo", "o/r", "--pr", "5", "--issue", "4", "--head", "abc",
           "--review", str(review)]
    first = subprocess.run(cmd, cwd=FORK, env=env, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    filed = [json.loads(line) for line in log.read_text().splitlines() if line]
    # The filer labels a debt issue `debt` only (#1494): debts are worked like bugs, after the bugs,
    # so `ready-for-agent` would put them back in the frontier's pick.
    assert len(filed) == 1 and set(filed[0]["labels"]) == {"debt", "enhancement"}
    assert "b.py:2" in filed[0]["body"] and "backs off" in filed[0]["body"]
    again = subprocess.run(cmd, cwd=FORK, env=env, capture_output=True, text=True)
    assert again.returncode == 0 and "no new DEBT findings" in again.stdout
