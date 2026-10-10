"""GUI test squad e2e suite (local-only): a 3-step script against a fake adapter,
the renderer, and the reverse-drift case against MesenAI's committed files."""
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
from tests.dashboard_helpers import run_node
from tests.gui_test_helpers import script, step

FORK = Path(__file__).resolve().parent.parent


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


def test_the_emulator_adapter_advertises_only_what_the_hook_does(tmp_path):
    caps = _emulator(tmp_path).capabilities()
    assert caps["actions"] == ["pad.press"]
    assert set(caps["checks"]) == {"ui.screen", "ui.focused", "ui.visible", "ui.dialogs"}
    assert caps["input_families"] == ["ticks"]


def _pilot_slice(ids):
    """The pilot's own steps by id, in their own batches, with `requires` trimmed to what the adapter advertises."""
    d = json.loads((_mesenai() / "docs" / "validation" / "process" / PILOT).read_text(encoding="utf-8"))
    for b in d["batches"]:
        b["steps"] = [s for s in b["steps"] if s["id"] in ids]
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
        assert json.loads((clone / "settings.json").read_text())["Preferences"] == {"UiMode": "Player", "Workspace": "Play"}
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


def _env_path(name, what):
    value = os.environ.get(name)
    assert value, f"set {name} to {what}"
    return Path(value).expanduser()


@pytest.mark.slow(reason="starts the real emulator three times (one process per batch) and waits on its window")
def test_three_pilot_steps_run_on_the_real_emulator_with_no_pad_attached(tmp_path):
    app = _env_path("MESENAI_APP", "the built emulator launcher, e.g. <repo>/bin/osx-arm64/Release/Mesen (a build of main with the test hook)")
    assert app.is_file(), f"MESENAI_APP {app} is not a file"
    d = _pilot_slice(THREE_PILOT_STEPS)
    adapter = EmulatorAdapter(tmp_path / "out", app_cmd=[str(app)], work_dir=None, startup_timeout=120, portable=True)
    validate(d, adapter.capabilities())
    events = []
    results = run_launched(d, adapter, record=events.append)
    assert [(r["id"], r["verdict"]) for r in results] == [(i, "passed") for i in THREE_PILOT_STEPS], results
    nodes = run_node([{"op": "guirun", "events": events}])[0]
    assert [(n["id"], n["status"], n["verdict"]) for n in nodes] == [(i, "executed", "passed") for i in THREE_PILOT_STEPS]
    for n in nodes:
        assert Path(n["capture"]["path"]).read_bytes().startswith(b"\x89PNG")
    assert len({n["capture"]["sha256"] for n in nodes}) >= 1


@pytest.mark.slow(reason="runs dotnet test on the headless suite, which builds the UI first")
def test_hook_e2e_case_of_1182_drives_the_home_over_the_socket():
    """#1182's GuiTestHook_e2e_a_runner_drives_the_Home_over_the_socket, run for real: one case passed, none skipped."""
    repo = _env_path("MESENAI_HEADLESS_REPO", "a MesenAI checkout with `make core` built (its own bin/, never the read-only MESENAI_REPO)")
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

from squad.gui_test_variants import (  # noqa: E402
    PermissionsMissing, RealWindowAdapter, RealWindowRefused, run_variants)


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
import time  # noqa: E402

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
    """The pilot's own steps, each batch keeping only `ids`; the launch/home steps need no rom or pack, so those fixtures are dropped."""
    d = json.loads((_mesenai_root() / "docs" / "validation" / "process" / PILOT).read_text(encoding="utf-8"))
    for b in d["batches"]:
        b["steps"] = [s for s in b["steps"] if s["id"] in ids]
    d["batches"] = [b for b in d["batches"] if b["steps"]]
    d["requires"] = {"actions": ["pad.press"], "checks": ["ui.screen", "ui.focused", "ui.visible", "ui.dialogs"]}
    d["variants"] = {"window.mode": ["windowed"]}
    # The pilot also declares fixtures for its other steps (rom-second, rom-zip, rom-fds, pack, ...); these three
    # need none of them, and the adapter refuses a kind it does not place, so only the profile and the note stay.
    d["fixtures"] = {k: v for k, v in d["fixtures"].items() if k in ("settings", "note")}
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
    seed.write_text(json.dumps({"Preferences": {"UiMode": "Player", "Workspace": "Play"}}))
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
            except Exception as e:  # what run_supervised does with an adapter error: the step in flight fails with it
                open_step = [ev["node"] for ev in events[mark:] if ev["event"] == "gui_step"][-1]
                verdict = f"failed ({e})"
                events.append({"event": "gui_verdict", "node": open_step, "pos": len(events), "verdict": verdict})
                results.append({"id": open_step, "verdict": verdict, "capture": None})
        finally:
            left = session.teardown()  # `quit` over the hook sometimes ends in a SIGSEGV (exit -11): reported, not asserted here
            assert all("quit not acknowledged" in x or "exited -11" in x for x in left), left
    return results, run_node([{"op": "guirun", "events": events}])[0]


@pytest.mark.slow(reason="starts the real emulator twice (one process per batch) and waits on its window")
def test_three_pilot_steps_are_executed_through_the_mesenai_adapter_with_a_capture_each(tmp_path, short_work):
    results, nodes = _run_pilot_on_the_real_app(tmp_path, short_work, THREE_PILOT_STEPS)
    assert [(n["id"], n["status"], n["verdict"]) for n in nodes] == [(i, "executed", "passed") for i in THREE_PILOT_STEPS], nodes
    assert [r["verdict"] for r in results] == ["passed"] * 3, results
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
def test_the_hook_e2e_case_passes_exactly_one_and_skips_none():
    """scripts/gui_test/run_headless_e2e.py: the skip=green trap closed, the verdict read off the summary line."""
    root = _mesenai_root()
    lib = _need("MESEN_CORE_LIB", "the MesenCore library built from the MesenAI checkout")
    assert lib.is_file(), f"MESEN_CORE_LIB {lib} is not a file"
    p = subprocess.run([sys.executable, str(root / "scripts" / "gui_test" / "run_headless_e2e.py")], cwd=root,
                       env={**os.environ, "MESEN_CORE_LIB": str(lib)}, capture_output=True, text=True, timeout=1500)
    out = p.stdout + p.stderr
    assert p.returncode == 0, out[-2000:]
    assert re.search(r"Failed:\s*0,\s*Passed:\s*1,\s*Skipped:\s*0,\s*Total:\s*1", out), out[-2000:]
    assert HEADLESS_CASE.split(".")[1] in out or "Passed!" in out


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
