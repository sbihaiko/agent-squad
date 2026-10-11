"""#1227: the scenario starts a run through the run command and returns; the write-back is a later, separate step."""
import json
import sys
import time
from pathlib import Path

from squad import to_test_scenario as tts

SLOW = """\
import json, sys, time
out = sys.argv[sys.argv.index("--out") + 1]
time.sleep(1.5)
Path = __import__("pathlib").Path
Path(out).write_text(json.dumps({"format": "gui-test-results/1", "args": sys.argv[1:]}))
"""


def _command(tmp_path, body=SLOW):
    path = tmp_path / "fake_run.py"
    path.write_text(body)
    return [sys.executable, str(path)]


def test_start_run_returns_before_the_run_ends_and_names_the_file_and_the_write_back(tmp_path):
    script = tmp_path / "demo.gui-test.json"
    script.write_text("{}")
    began = time.monotonic()
    out = tts.start_run(script, command=_command(tmp_path), out_dir=tmp_path / "results", batch="home")
    assert time.monotonic() - began < 1.0                      # it never waited on the run
    assert out["started"] is True and out["pid"] > 0
    results = Path(out["results"])
    assert results.parent == tmp_path / "results" and not results.exists()
    assert out["write_back"][:4] == [sys.executable, "-m", "squad.to_test_scenario", "write-back"]
    assert out["write_back"][4] == str(results)
    assert out["write_back"][out["write_back"].index("--map") + 1] == str(tts.issues_path(script))
    for _ in range(60):                                          # the run ends on its own; the file is its output
        if results.exists():
            break
        time.sleep(0.25)
    assert json.loads(results.read_text())["args"][-2:] == ["--batch", "home"]


def test_start_run_without_a_run_command_starts_nothing_and_says_so(tmp_path):
    script = tmp_path / "demo.gui-test.json"
    script.write_text("{}")
    out = tts.start_run(script, command=[sys.executable, str(tmp_path / "missing.py")], out_dir=tmp_path / "r")
    assert out["started"] is False and out["reason"] == "run command unavailable"
    assert "results" not in out and not (tmp_path / "r").exists()


# --- the publish command: the skill calls it instead of writing Python ---------------------------

def _pair(tmp_path, stamped):
    from tests.gui_test_helpers import script, step
    from tests.test_to_test_scenario_publish import SCENARIO, hashed
    md = tmp_path / "tiny.md"
    md.write_text(SCENARIO)
    ids = ["launch.s0", "launch.home", "launch.return"]
    extra = (lambda sid: {"content_hash": hashed(sid)}) if stamped else (lambda sid: {})
    setup = [step("launch.s0", role="setup", mode="manual", **extra("launch.s0"))]
    steps = [step(sid, **extra(sid)) for sid in ids[1:]]
    d = script(*steps)
    d["batches"] = [{"id": "launch", "setup": setup, "teardown": [], "steps": steps}]
    path = tmp_path / "tiny.gui-test.json"
    path.write_text(json.dumps(d))
    return md, path


def test_publish_command_stops_on_a_script_without_hashes_and_stamps_them_only_when_asked(tmp_path):
    from tests.test_to_test_scenario_publish import FakeGitHub
    md, path = _pair(tmp_path, stamped=False)
    gh = FakeGitHub()
    assert tts.main(["publish", str(md), str(path)], github=gh) == 1
    assert gh.calls == [] and "content_hash" not in path.read_text()
    assert tts.main(["publish", str(md), str(path), "--stamp"], github=gh) == 0
    assert "content_hash" in path.read_text() and len(gh.of("create_issue")) == 4   # parent plus three cases
    assert tts.read_issues(tts.issues_path(path))["parent"] is not None
    again = FakeGitHub()
    assert tts.main(["publish", str(md), str(path)], github=again) == 0
    assert again.of("create_issue") == []


# --- the skill text: what it promises is what the module does -----------------------------------

SKILL = Path(__file__).resolve().parent.parent / "vendor" / "skills" / "skills" / "to-test-scenario" / "SKILL.md"


def test_the_skill_never_waits_on_a_run_and_names_the_commands_the_module_really_has():
    text = SKILL.read_text(encoding="utf-8")
    for command in ("publish", "start-run", "write-back"):
        assert f"squad.to_test_scenario {command}" in text
        assert command in tts.main.__doc__ or command in Path(tts.__file__).read_text(encoding="utf-8")
    assert "never waits on it" in text and "results file" in text
    assert "run command unavailable" in text and "Never simulate a run" in text
    assert "run_command" not in text      # the module's synchronous hook is not how the skill runs anything


def test_nothing_in_the_skill_or_the_module_encodes_a_pilot_screen_button_or_case_count():
    for path in (SKILL, Path(tts.__file__)):
        text = path.read_text(encoding="utf-8")
        for pilot_only in ("play.home", "play.library", "HOME-0", "home.open-library", "281"):
            assert pilot_only not in text, f"{path.name} names {pilot_only}"
