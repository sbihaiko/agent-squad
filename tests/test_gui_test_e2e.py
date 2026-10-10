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
            assert s["expect"].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")[:30] in svg
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
