"""Guard: issue #1616. One supervisor keeps one operator session alive per scope, owns the sessions it started by the
`SQUAD-OPERATOR` marker, and never touches a session that carries none.

Every case drives the module production runs, with a fake `claude` on the path: the fake answers `agents --json` from
a scenario file and records every call it gets, so no live session, no network and no key is touched. One test per
acceptance criterion.
"""
import hashlib
import importlib.util
import json
import os
import socket
import time
from pathlib import Path

from tests.proxystop import track_starts

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
    track_starts(module)
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
    passthrough = bin_dir / "passthrough.sh"
    passthrough.write_text('#!/bin/sh\nexec "$SQUAD_CLAUDE_CLI" "$@"\n')
    passthrough.chmod(0o755)
    module.WRAPPER = str(passthrough)
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


def test_an_adopted_scope_keeps_the_profile_and_proxy_it_ran_under(tmp_path, monkeypatch):
    """#1658: adoption replaces the session id, never the profile or the proxy the record holds for the scope."""
    op = operators(tmp_path, monkeypatch)
    registry(tmp_path, [])
    live = session(tmp_path, "dm", "dddddddd-0000-0000-0000-000000000001")
    scenario(tmp_path, [live])
    profile = {"path": "/state/operators/profiles/dm.sb", "sha256": "ab" * 32}
    proxy = {"pid": 999999, "port": 1}
    held = {"id": "eeeeeeee-0000-0000-0000-00000000000a", "pid": 1, "started": 100, "profile": profile, "proxy": proxy}
    (tmp_path / "state" / "operators.json").write_text(json.dumps({"dm": held}))
    assert op.run_pass(str(tmp_path / "state"), str(ROOT)) == 0
    assert op.run_pass(str(tmp_path / "state"), str(ROOT)) == 0
    entry = record(tmp_path)["dm"]
    assert entry["id"] == live["sessionId"]
    assert entry["profile"] == profile
    assert entry["proxy"] == proxy


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
    monkeypatch.setattr(op, "call", lambda argv, cwd=None, timeout=op.DEADLINE, env=None: (timeouts.append(timeout), real(argv, cwd, timeout, env))[1])
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


WRAP = """#!/bin/sh
{
  printf 'profile=%s\\n' "$SQUAD_SIGNAL_PROFILE"
  printf 'proxy=%s\\n' "${HTTPS_PROXY-unset}"
  printf 'sandboxed=%s\\n' "${CLAUDE_CODE_SANDBOXED-unset}"
  printf 'argv1=%s\\n' "$1"
} >> "$FAKE_WRAP_LOG"
exec "$SQUAD_CLAUDE_CLI" "$@"
"""


def wrapped(tmp_path, monkeypatch, base_url=None):
    """The module with its sandbox wrapper bound to a fake that logs the profile, the proxy env and the argv."""
    op = operators(tmp_path, monkeypatch)
    wrap = tmp_path / "wrap.sh"
    wrap.write_text(WRAP)
    wrap.chmod(0o755)
    monkeypatch.setattr(op, "WRAPPER", str(wrap))
    monkeypatch.setenv("FAKE_WRAP_LOG", str(tmp_path / "wrap.log"))
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    if base_url is None:
        monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    else:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", base_url)
    registry(tmp_path, [])
    scenario(tmp_path, [])
    return op


def wrap_log(tmp_path):
    return (tmp_path / "wrap.log").read_text() if (tmp_path / "wrap.log").exists() else ""


def test_a_session_starts_through_the_sandbox_wrapper_under_the_operator_profile(tmp_path, monkeypatch):
    """#1658: each operator session starts through claude-sandboxed.sh, and the profile it runs under is recorded."""
    op = wrapped(tmp_path, monkeypatch)
    assert op.run_pass(str(tmp_path / "state"), str(ROOT)) == 0
    log = wrap_log(tmp_path)
    assert "argv1=--bg" in log, f"the session did not start through the wrapper: {log!r}"
    state = os.path.realpath(tmp_path / "state")
    assert f'(literal "{state}/outboxes/dm.jsonl")' in log
    assert "(deny network-outbound)" in log
    entry = record(tmp_path)["dm"]
    profile = (tmp_path / "state" / "operators" / "profiles" / "dm.sb").read_text()
    assert entry["profile"]["path"].endswith("profiles/dm.sb")
    assert entry["profile"]["sha256"] == hashlib.sha256(profile.encode()).hexdigest()


def test_the_session_tells_the_cli_it_already_runs_under_a_sandbox(tmp_path, monkeypatch):
    """#2768: the session runs under the kernel wall already, so the CLI must not ask for folder trust or wrap its own
    child processes in a second sandbox, which the kernel refuses as a nested apply."""
    op = wrapped(tmp_path, monkeypatch)
    assert op.run_pass(str(tmp_path / "state"), str(ROOT)) == 0
    assert "sandboxed=1" in wrap_log(tmp_path)


def test_a_loopback_endpoint_starts_its_session_with_no_proxy(tmp_path, monkeypatch):
    """#1658: a loopback model endpoint is reached under the profile's loopback rule, so no proxy starts."""
    op = wrapped(tmp_path, monkeypatch, base_url="http://localhost:8016")
    assert op.run_pass(str(tmp_path / "state"), str(ROOT)) == 0
    assert "proxy=unset" in wrap_log(tmp_path)
    assert "proxy" not in record(tmp_path)["dm"]


def test_a_foreign_endpoint_gets_a_proxy_that_ends_with_its_session(tmp_path, monkeypatch):
    """#1658: a foreign endpoint goes through model_proxy.py, HTTPS_PROXY names it, and retiring the session ends it."""
    op = wrapped(tmp_path, monkeypatch, base_url="https://api.anthropic.com")
    assert op.run_pass(str(tmp_path / "state"), str(ROOT)) == 0
    proxy_url = next(line.split("=", 1)[1] for line in wrap_log(tmp_path).splitlines() if line.startswith("proxy="))
    assert proxy_url.startswith("http://127.0.0.1:")
    port = int(proxy_url.rsplit(":", 1)[1])
    assert record(tmp_path)["dm"]["proxy"]["port"] == port
    op.retire_proxy(record(tmp_path)["dm"])
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close()
        except OSError:
            return
        time.sleep(0.05)
    raise AssertionError("the proxy still listens after its session was retired")


def test_a_proxy_refusal_is_reported_by_the_supervisor(tmp_path, monkeypatch, capsys):
    """#1658: a refusal the proxy records is printed by the next pass, so the owner reads it."""
    op = wrapped(tmp_path, monkeypatch)
    refusals = tmp_path / "state" / "operators" / "refusals.jsonl"
    refusals.parent.mkdir(parents=True, exist_ok=True)
    refusals.write_text('{"at": "2026-11-13T05:00:00", "scope": "dm", "target": "evil.example:443"}\n')
    assert op.run_pass(str(tmp_path / "state"), str(ROOT)) == 0
    assert "model proxy refused evil.example:443 for dm" in capsys.readouterr().err


def test_the_session_is_told_the_repository_is_read_only_under_its_profile(tmp_path, monkeypatch):
    """#1658: the profile writes only the outbox file and the Claude home, so the brief names the repository as read-only."""
    op = operators(tmp_path, monkeypatch)
    text = op.prompt_for("dm", str(tmp_path / "state"), "/repo")
    assert "Read /repo" in text
    assert "Work in /repo" not in text


def test_the_brief_carries_the_channel_language_and_the_persona_tone(tmp_path, monkeypatch):
    """#2688 criteria (5) and (6): the instruction for the text reply carries the channel language and the persona
    tone, read from the active revision of the project that owns the scope."""
    op = operators(tmp_path, monkeypatch)
    state = tmp_path / "state"
    state.mkdir()
    (state / "channels.json").write_text(json.dumps({"acme/app": {"name": "proj-acme-app", "id": "C0PROJ0001"}}))
    revision = {
        "project": {"language": "en-US", "persona": "iara"},
        "personas": [{"id": "iara", "name": "Iara", "voice": "Sulafat",
                      "tone": "Warm and patient. Confirms what was asked."}],
        "workspaces": [{"id": "T0TEAM0001", "label": "team", "class": "owned", "app": "hal9k",
                        "channels": [{"id": "C0PROJ0001", "name": "proj-acme-app", "mode": "listen+speak",
                                      "language": "pt-BR", "persona": "iara"}]}],
    }
    (state / "slack").mkdir()
    digest = hashlib.sha256(b"acme/app").hexdigest()[:16]
    (state / "slack" / f"{digest}.json").write_text(json.dumps(
        {"repo": "acme/app", "sha": "a" * 40, "revision": revision, "local": {}}))

    text = op.prompt_for("C0PROJ0001", str(state), "/repo")

    assert "pt-BR" in text, text
    assert "Warm and patient. Confirms what was asked." in text, text
    assert "even when the sender writes in another language" in text, text


def test_the_dm_brief_keeps_its_language_when_no_revision_names_a_persona(tmp_path, monkeypatch):
    """#2688: the DM has no channel persona, so the brief keeps the prototype's Portuguese answer and no tone."""
    op = operators(tmp_path, monkeypatch)
    text = op.prompt_for("dm", str(tmp_path / "state"), "/repo")
    assert "Brazilian Portuguese" in text, text
