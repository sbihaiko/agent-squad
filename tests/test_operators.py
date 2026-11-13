"""Guard: issue #1616. One supervisor keeps one operator session alive per scope, owns the sessions it started by the
`SQUAD-OPERATOR` marker, and never touches a session that carries none.

Every case drives the module production runs, with a fake `claude` on the path: the fake answers `agents --json` from
a scenario file and records every call it gets, so no live session, no network and no key is touched. One test per
acceptance criterion.
"""
import importlib.util
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VOICE = ROOT / "contrib" / "voice"

FAKE = """#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_LOG"
case "$1 $2" in
  "agents --json") cat "$FAKE_SCENARIO"; exit 0 ;;
  "stop "*|"stop")
    if [ -s "$FAKE_FAIL_STOP" ]; then printf 'stop refused\\n' >&2; exit 1; fi
    printf '' > "$FAKE_SCENARIO.stop"
    exit 0 ;;
esac
case "$1" in
  --bg) printf '{"sessionId":"%s"}\\n' "$FAKE_NEW" | cut -c1-0; printf '%s\\n' "$FAKE_NEW"; exit 0 ;;
esac
printf 'claude: unknown call %s\\n' "$*" >&2
exit 2
"""


def operators(tmp_path, monkeypatch):
    """The module, with its CLI name bound to the fake on the path."""
    spec = importlib.util.spec_from_file_location("operators", VOICE / "operators.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "claude"
    fake.write_text(FAKE)
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "calls.log"))
    monkeypatch.setenv("FAKE_SCENARIO", str(tmp_path / "scenario.json"))
    monkeypatch.setenv("FAKE_FAIL_STOP", str(tmp_path / "fail-stop"))
    monkeypatch.setenv("FAKE_NEW", "aaaaaaaa-0000-0000-0000-000000000001")
    module.CLI = "claude"
    module.DEADLINE = 5.0
    module.LOCK_WAIT = 1.0
    return module


def scenario(tmp_path, sessions):
    (tmp_path / "scenario.json").write_text(json.dumps(sessions))


def session(tmp_path, scope, sid, started=1000, pid=None):
    """One live session carrying the marker for `scope`: its cwd is the scope's marked folder."""
    return {"sessionId": sid, "pid": pid or abs(hash(sid)) % 60000, "startedAt": started,
            "cwd": str(tmp_path / "state" / "operators" / "SQUAD-OPERATOR" / scope)}


def registry(tmp_path, channel_ids=()):
    chan = {f"repo{i}": {"name": f"chan{i}", "id": cid} for i, cid in enumerate(channel_ids)}
    (tmp_path / "state").mkdir(exist_ok=True)
    (tmp_path / "state" / "channels.json").write_text(json.dumps(chan))


def calls(tmp_path):
    return [line for line in (tmp_path / "calls.log").read_text().splitlines() if line] if (tmp_path / "calls.log").exists() else []


def record(tmp_path):
    path = tmp_path / "state" / "operators.json"
    return json.loads(path.read_text()) if path.exists() else {}


def test_a_marked_session_is_the_only_one_stopped(tmp_path, monkeypatch):
    """Criterion 1: `SQUAD-OPERATOR <scope-id>` is the only ownership token; an unmarked session is never stopped."""
    op = operators(tmp_path, monkeypatch)
    registry(tmp_path, ["C0AAAAAA1"])
    unmarked = {"sessionId": "bbbbbbbb-0000-0000-0000-000000000009", "pid": 9, "startedAt": 10, "cwd": "/elsewhere"}
    scenario(tmp_path, [unmarked])
    (tmp_path / "state" / "stopped.json").write_text(json.dumps({"dm": 1}))
    assert op.run_pass(str(tmp_path / "state"), str(ROOT)) == 0
    assert not [c for c in calls(tmp_path) if c.startswith("stop")], "an unmarked session was stopped"


def test_the_record_is_written_after_each_start(tmp_path, monkeypatch):
    """Criterion 2: the record is written after each start, atomically, so a crash loses no earlier start."""
    op = operators(tmp_path, monkeypatch)
    registry(tmp_path, ["C0AAAAAA1"])           # two scopes: dm and the channel
    scenario(tmp_path, [])
    writes = []
    real = op.write_json
    monkeypatch.setattr(op, "write_json", lambda path, value: (writes.append(dict(value)), real(path, value))[1])
    assert op.run_pass(str(tmp_path / "state"), str(ROOT)) == 0
    assert len(writes) == 2, f"the record was written {len(writes)} times, not once per start"
    assert set(record(tmp_path)) == {"dm", "C0AAAAAA1"}
    assert not [n for n in os.listdir(tmp_path / "state") if n.endswith(".tmp")], "a half-written record was left"


def test_a_dead_session_is_replaced_only_on_a_second_pass(tmp_path, monkeypatch):
    """Criterion 3: a recorded session is replaced only when its id is dead and a second pass agrees."""
    op = operators(tmp_path, monkeypatch)
    registry(tmp_path, [])
    (tmp_path / "state" / "operators.json").write_text(json.dumps({"dm": {"id": "dead", "pid": 1, "started": 1}}))
    scenario(tmp_path, [])
    assert op.run_pass(str(tmp_path / "state"), str(ROOT)) == 0
    assert not [c for c in calls(tmp_path) if c.startswith("--bg")], "a replacement started on the first pass"
    assert record(tmp_path)["dm"]["missing"] == 1
    assert op.run_pass(str(tmp_path / "state"), str(ROOT)) == 0
    assert [c for c in calls(tmp_path) if c.startswith("--bg")], "the second pass started no replacement"


def test_a_session_handed_an_answer_is_never_replaced(tmp_path, monkeypatch):
    """Criterion 3, second half: a session working an answer (`handed`) is never replaced, since it is still listed."""
    op = operators(tmp_path, monkeypatch)
    registry(tmp_path, [])
    live = session(tmp_path, "dm", "cccccccc-0000-0000-0000-000000000001")
    (tmp_path / "state" / "operators.json").write_text(json.dumps({"dm": {"id": live["sessionId"], "pid": live["pid"], "started": 1}}))
    scenario(tmp_path, [live])
    for _ in range(2):
        assert op.run_pass(str(tmp_path / "state"), str(ROOT)) == 0
    assert not [c for c in calls(tmp_path) if c.startswith("--bg")], "a live session was replaced"


def test_a_marked_session_outside_the_record_is_adopted_after_two_passes(tmp_path, monkeypatch):
    """Criterion 4: a session carrying a marker but not in the record is adopted after two consecutive passes."""
    op = operators(tmp_path, monkeypatch)
    registry(tmp_path, [])
    live = session(tmp_path, "dm", "dddddddd-0000-0000-0000-000000000001")
    scenario(tmp_path, [live])
    assert op.run_pass(str(tmp_path / "state"), str(ROOT)) == 0
    assert not (record(tmp_path).get("dm") or {}).get("id"), "the first pass adopted the session"
    assert op.run_pass(str(tmp_path / "state"), str(ROOT)) == 0
    assert record(tmp_path)["dm"]["id"] == live["sessionId"]
    assert not [c for c in calls(tmp_path) if c.startswith("--bg")], "an adopted scope was started again"


def test_two_sessions_on_one_scope_keep_the_older(tmp_path, monkeypatch):
    """Criterion 5: two sessions on one scope keep the older and stop the newer."""
    op = operators(tmp_path, monkeypatch)
    registry(tmp_path, [])
    old = session(tmp_path, "dm", "eeeeeeee-0000-0000-0000-00000000000a", started=100)
    new = session(tmp_path, "dm", "eeeeeeee-0000-0000-0000-00000000000b", started=200)
    (tmp_path / "state" / "operators.json").write_text(json.dumps({"dm": {"id": old["sessionId"], "pid": old["pid"], "started": 100}}))
    scenario(tmp_path, [new, old])
    assert op.run_pass(str(tmp_path / "state"), str(ROOT)) == 0
    stopped = [c for c in calls(tmp_path) if c.startswith("stop")]
    assert stopped == [f"stop {new['sessionId']}"], f"stopped {stopped}, not only the newer session"


def test_a_failed_stop_keeps_ownership_and_backs_off(tmp_path, monkeypatch):
    """Criterion 6: ownership is dropped only after the stop is confirmed absent; a failed stop keeps it and backs off."""
    op = operators(tmp_path, monkeypatch)
    registry(tmp_path, [])
    (tmp_path / "state" / "operators.json").write_text(json.dumps({"dm": {"id": "ffffffff-0000-0000-0000-000000000001", "pid": 1, "started": 1}}))
    (tmp_path / "fail-stop").write_text("1")
    scenario(tmp_path, [])
    op.run_pass(str(tmp_path / "state"), str(ROOT))   # pass 1: missing = 1
    op.run_pass(str(tmp_path / "state"), str(ROOT))   # pass 2: starts a replacement; the dead id is not stopped
    assert "dm" in record(tmp_path)
    (tmp_path / "state" / "stopped.json").write_text(json.dumps({"other": 1}))   # the dm scope is wanted again
    (tmp_path / "state" / "operators.json").write_text(json.dumps({"dm": {"id": "ffffffff-0000-0000-0000-000000000001", "pid": 1, "started": 1, "stop": {"tries": 0, "next": 0}}}))
    scenario(tmp_path, [session(tmp_path, "dm", "ffffffff-0000-0000-0000-000000000002", started=300),
                        session(tmp_path, "dm", "ffffffff-0000-0000-0000-000000000001", started=100)])
    assert op.run_pass(str(tmp_path / "state"), str(ROOT)) == 0
    assert "dm" in record(tmp_path), "ownership dropped after a refused stop"
    assert record(tmp_path)["dm"]["stop"]["tries"] >= 1, "a failed stop recorded no retry"


def test_a_scope_stopped_on_purpose_is_not_revived_and_a_dropped_scope_loses_its_session(tmp_path, monkeypatch):
    """Criterion 7: a scope marked stopped is not revived; a scope the registry dropped loses its session."""
    op = operators(tmp_path, monkeypatch)
    registry(tmp_path, ["C0AAAAAA1"])
    dm = session(tmp_path, "dm", "11111111-0000-0000-0000-000000000001")
    gone = session(tmp_path, "C0OLD0001", "11111111-0000-0000-0000-000000000002")
    (tmp_path / "state" / "operators.json").write_text(json.dumps({
        "dm": {"id": dm["sessionId"], "pid": dm["pid"], "started": 1},
        "C0OLD0001": {"id": gone["sessionId"], "pid": gone["pid"], "started": 1}}))
    (tmp_path / "state" / "stopped.json").write_text(json.dumps({"dm": 1}))
    scenario(tmp_path, [dm, gone])
    assert op.run_pass(str(tmp_path / "state"), str(ROOT)) == 0
    assert [c for c in calls(tmp_path) if c.startswith("stop")] == [f"stop {gone['sessionId']}"], "the dropped scope kept its session or the stopped one moved"
    started = [c for c in calls(tmp_path) if c.startswith("--bg")]
    assert not [c for c in started if "direct messages" in c], "a scope stopped on purpose was revived"


def test_every_cli_call_has_a_deadline_and_one_lock_bounds_a_pass(tmp_path, monkeypatch):
    """Criterion 8: every CLI call has a deadline, and one lock bounds a pass."""
    op = operators(tmp_path, monkeypatch)
    registry(tmp_path, [])
    scenario(tmp_path, [])
    timeouts = []
    real = op.call
    monkeypatch.setattr(op, "call", lambda argv, cwd=None, timeout=op.DEADLINE: (timeouts.append(timeout), real(argv, cwd, timeout))[1])
    held = op.acquire(str(tmp_path / "state"))
    try:
        assert op.acquire(str(tmp_path / "state")) is None, "a second pass took the lock"
    finally:
        os.close(held)
    assert op.run_pass(str(tmp_path / "state"), str(ROOT)) == 0
    assert timeouts and all(t and t > 0 for t in timeouts), f"a CLI call ran with no deadline: {timeouts}"


def test_a_refused_start_is_named_and_exits_non_zero(tmp_path, monkeypatch):
    """The module's own rule: a CLI refusal is one named line on stderr and exit 1, never a silent empty pass."""
    op = operators(tmp_path, monkeypatch)
    registry(tmp_path, [])
    (tmp_path / "scenario.json").write_text("not json")
    code = op.main(["--state", str(tmp_path / "state"), "--repo", str(ROOT)])
    assert code == 1
