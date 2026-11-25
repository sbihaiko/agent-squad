"""Guard: issue #2925. One operator session per project, never per channel (#1616 is the older per-scope shape).

Every case drives the module production runs, with a fake `claude` on the path: the fake answers `agents --json` from
a scenario file and records every call it gets, so no live session, no network and no key is touched. The hub seam is
a fixture, so no case reads the owner's profiles. One test per acceptance criterion of #2925.
"""
import hashlib
import importlib.util
import json
import os
import re
import socket
import sys
import time
from pathlib import Path

from tests.proxystop import track_starts

ROOT = Path(__file__).resolve().parent.parent
VOICE = ROOT / "contrib" / "voice"

FAKE = """#!/bin/sh
printf -- '---CALL---\\n%s\\n' "$*" >> "$FAKE_LOG"
case "$1 $2" in
  "agents --json")
    if [ -s "$FAKE_STOPPED" ]; then
      "$FAKE_PY" -c 'import json,sys
ids=set(open(sys.argv[2]).read().split())
print(json.dumps([a for a in json.load(open(sys.argv[1])) if a["sessionId"] not in ids]))' "$FAKE_SCENARIO" "$FAKE_STOPPED"
    else cat "$FAKE_SCENARIO"; fi
    exit 0 ;;
  "stop "*|"stop")
    if [ -s "$FAKE_FAIL_STOP" ]; then printf 'stop refused\\n' >&2; exit 1; fi
    printf '%s\\n' "$2" >> "$FAKE_STOPPED"
    exit 0 ;;
esac
case "$1" in
  --bg) printf '%s\\n' "$FAKE_NEW"; exit 0 ;;
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
    monkeypatch.setenv("FAKE_STOPPED", str(tmp_path / "stopped-ids"))
    monkeypatch.setenv("FAKE_FAIL_STOP", str(tmp_path / "fail-stop"))
    monkeypatch.setenv("FAKE_NEW", "aaaaaaaa-0000-0000-0000-000000000001")
    monkeypatch.setenv("FAKE_PY", sys.executable)
    module.PROJECTS = str(tmp_path / "projects")   # the transcript seam: no test reads the owner's transcripts
    passthrough = bin_dir / "passthrough.sh"
    passthrough.write_text('#!/bin/sh\nexec "$SQUAD_CLAUDE_CLI" "$@"\n')
    passthrough.chmod(0o755)
    module.WRAPPER = str(passthrough)
    module.CLI = "claude"
    module.DEADLINE = 5.0
    module.LOCK_WAIT = 0.2        # a contended lock stands aside at once, so a case never spends the 1 s line on it
    (tmp_path / "repo").mkdir(exist_ok=True)       # the DM session's own checkout: the agent-squad repository
    (tmp_path / "state").mkdir(exist_ok=True)      # the state folder the daemon keeps its record and queues under
    return module


def hub(tmp_path, monkeypatch, op, projects):
    """The hub seam: each project's repository and the checkout its profile names. Returns repository -> checkout."""
    rows, found = [], {}
    for repo in projects:
        checkout = tmp_path / "checkouts" / op.project_key(repo)
        checkout.mkdir(parents=True, exist_ok=True)
        rows.append({"repo": repo, "checkout": str(checkout), "local": ""})
        found[repo] = str(checkout)
    monkeypatch.setattr(op, "hub_projects", lambda *a, **k: rows)
    return found


def registry(tmp_path, entries):
    """The channel registry: a list of (repository, channel id, name) rows, written the way the daemon writes it."""
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    chan = {}
    for repo, cid, name in entries:
        chan[repo] = {"name": name, "id": cid}
    (state / "channels.json").write_text(json.dumps(chan))


def revision(tmp_path, repo, channels):
    """One project's active Slack revision, naming extra channels the project owns (#2925)."""
    state = tmp_path / "state"
    (state / "slack").mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(repo.encode()).hexdigest()[:16]
    doc = {"workspaces": [{"id": "T0TEAM0001", "channels": [{"id": cid, "name": f"proj-{cid.lower()}"}
                                                           for cid in channels]}]}
    (state / "slack" / f"{digest}.json").write_text(json.dumps({"repo": repo, "sha": "a" * 40, "revision": doc}))


def scenario(tmp_path, sessions):
    (tmp_path / "scenario.json").write_text(json.dumps(sessions))


def session(checkout, key, sid, started=1000, pid=None):
    """One live session carrying the operator token for `key`: its name carries it, whatever its working directory."""
    return {"sessionId": sid, "pid": pid or abs(hash(sid)) % 60000, "startedAt": started,
            "name": f"SQUAD-OPERATOR-{key}", "cwd": str(checkout)}


def bare_session(cwd, sid, started=1000):
    """A live session that carries no token: a person's own session, or one an older build started."""
    return {"sessionId": sid, "pid": 7, "startedAt": started, "name": "", "cwd": str(cwd)}


def calls(tmp_path):
    """Every CLI call the fake got, as one block of its whole argv: a brief carries newlines, so the blocks split on the
    fake's own separator, never on a line."""
    log = tmp_path / "calls.log"
    if not log.exists():
        return []
    return [block.strip("\n") for block in log.read_text().split("---CALL---\n") if block.strip()]


def started(tmp_path):
    """The `--bg` calls the fake CLI got: one per session started."""
    return [c for c in calls(tmp_path) if c.startswith("--bg")]


def record(tmp_path):
    path = tmp_path / "state" / "operators.json"
    return json.loads(path.read_text()) if path.exists() else {}


def brief(tmp_path, marker):
    """The brief of the one start whose text names `marker`."""
    return next(c for c in started(tmp_path) if marker in c)


def test_one_session_serves_every_channel_of_a_project(tmp_path, monkeypatch):
    """Criterion 1: one session per project that owns at least one channel, not one per channel."""
    op = operators(tmp_path, monkeypatch)
    hub(tmp_path, monkeypatch, op, ["acme/widget"])
    registry(tmp_path, [("acme/widget", "C0AAAAAA1", "proj-widget")])
    revision(tmp_path, "acme/widget", ["C0AAAAAA1", "C0AAAAAA2"])   # the project owns two channels
    scenario(tmp_path, [])
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo")) == 0
    widget = [c for c in started(tmp_path) if "acme/widget" in c]
    assert len(widget) == 1, f"the project got {len(widget)} sessions, not one: {started(tmp_path)}"
    assert "C0AAAAAA1.jsonl" in widget[0] and "C0AAAAAA2.jsonl" in widget[0], "the brief names one inbox only"


def test_each_answer_goes_to_the_outbox_of_its_channels_line(tmp_path, monkeypatch):
    """Criterion 10: the brief names one outbox per channel of the project, and no other outbox."""
    op = operators(tmp_path, monkeypatch)
    hub(tmp_path, monkeypatch, op, ["acme/widget"])
    registry(tmp_path, [("acme/widget", "C0AAAAAA1", "proj-widget")])
    revision(tmp_path, "acme/widget", ["C0AAAAAA1", "C0AAAAAA2"])
    scenario(tmp_path, [])
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo")) == 0
    text = brief(tmp_path, "acme/widget")
    state = str(tmp_path / "state")
    assert f"{state}/outboxes/C0AAAAAA1.jsonl" in text and f"{state}/outboxes/C0AAAAAA2.jsonl" in text, text
    assert "C0AAAAAA9" not in text


def test_the_brief_arms_one_monitor_per_inbox_of_the_project(tmp_path, monkeypatch):
    """Criterion 9: one Monitor per inbox of the project's channels, each on its own call."""
    op = operators(tmp_path, monkeypatch)
    hub(tmp_path, monkeypatch, op, ["acme/widget"])
    registry(tmp_path, [("acme/widget", "C0AAAAAA1", "proj-widget")])
    revision(tmp_path, "acme/widget", ["C0AAAAAA1", "C0AAAAAA2"])
    scenario(tmp_path, [])
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo")) == 0
    text = brief(tmp_path, "acme/widget")
    state = str(tmp_path / "state")
    assert f"tail -n 0 -F {state}/inboxes/C0AAAAAA1.jsonl" in text, text
    assert f"tail -n 0 -F {state}/inboxes/C0AAAAAA2.jsonl" in text, text
    assert "Start one Monitor per inbox" in text


def test_two_sessions_on_one_project_keep_the_older(tmp_path, monkeypatch):
    """Criterion 2: two sessions on one project keep the older and stop the newer."""
    op = operators(tmp_path, monkeypatch)
    found = hub(tmp_path, monkeypatch, op, ["acme/widget"])
    registry(tmp_path, [("acme/widget", "C0AAAAAA1", "proj-widget")])
    old = session(found["acme/widget"], "acme-widget", "eeeeeeee-0000-0000-0000-00000000000a", started=100)
    new = session(found["acme/widget"], "acme-widget", "eeeeeeee-0000-0000-0000-00000000000b", started=200)
    (tmp_path / "state" / "operators.json").write_text(json.dumps(
        {"acme-widget": {"id": old["sessionId"], "pid": old["pid"], "started": 100}}))
    scenario(tmp_path, [new, old])
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo")) == 0
    stopped = [c for c in calls(tmp_path) if c.startswith("stop")]
    assert stopped == [f"stop {new['sessionId']}"], f"stopped {stopped}, not only the newer session"


def test_the_launchd_pass_starts_one_session_per_project(tmp_path, monkeypatch):
    """Criterion 3: the pass starts a session for each project the registry names, and one for the DM."""
    op = operators(tmp_path, monkeypatch)
    hub(tmp_path, monkeypatch, op, ["acme/widget", "acme/api"])
    registry(tmp_path, [("acme/widget", "C0AAAAAA1", "proj-widget"), ("acme/api", "C0BBBBBB1", "proj-api")])
    scenario(tmp_path, [])
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo")) == 0
    assert len(started(tmp_path)) == 3, f"the pass started {len(started(tmp_path))} sessions, not dm and two projects"
    assert set(record(tmp_path)) == {"dm", "acme-widget", "acme-api"}


def test_ensure_starts_the_named_projects_session_and_no_other(tmp_path, monkeypatch):
    """Criterion 4: a message for a project with no live session starts that session at once, not at the next pass."""
    op = operators(tmp_path, monkeypatch)
    hub(tmp_path, monkeypatch, op, ["acme/widget", "acme/api"])
    registry(tmp_path, [("acme/widget", "C0AAAAAA1", "proj-widget"), ("acme/api", "C0BBBBBB1", "proj-api")])
    scenario(tmp_path, [])
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo"), only="acme/widget") == 0
    assert len(started(tmp_path)) == 1, f"ensure started {started(tmp_path)}"
    assert set(record(tmp_path)) == {"acme-widget"}


def test_ensure_does_nothing_when_the_project_already_has_a_live_session(tmp_path, monkeypatch):
    """Criterion 4, second half: a live session is never restarted by the message that follows it."""
    op = operators(tmp_path, monkeypatch)
    found = hub(tmp_path, monkeypatch, op, ["acme/widget"])
    registry(tmp_path, [("acme/widget", "C0AAAAAA1", "proj-widget")])
    live = session(found["acme/widget"], "acme-widget", "cccccccc-0000-0000-0000-000000000001", started=int(time.time()))
    (tmp_path / "state" / "operators.json").write_text(json.dumps(
        {"acme-widget": {"id": live["sessionId"], "pid": live["pid"], "started": live["startedAt"]}}))
    scenario(tmp_path, [live])
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo"), only="acme/widget") == 0
    assert not started(tmp_path), f"a live project was started again: {started(tmp_path)}"


def test_the_session_answers_the_message_that_started_it(tmp_path, monkeypatch):
    """Criterion 5: the line that paid for the start rides in the brief, and the brief says to answer it first."""
    op = operators(tmp_path, monkeypatch)
    hub(tmp_path, monkeypatch, op, ["acme/widget"])
    registry(tmp_path, [("acme/widget", "C0AAAAAA1", "proj-widget")])
    scenario(tmp_path, [])
    now = time.time()
    inbox(tmp_path, "C0AAAAAA1", mtime=now - 5, text="what changed in the release?")
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo"), only="acme/widget") == 0
    text = brief(tmp_path, "acme/widget")
    assert "what changed in the release?" in text, text
    assert "Answer it first" in text, text


def test_the_working_directory_is_the_projects_own_checkout(tmp_path, monkeypatch):
    """Criteria 6 and 7: the project and its checkout come from the hub profile, and the session runs there."""
    op = operators(tmp_path, monkeypatch)
    found = hub(tmp_path, monkeypatch, op, ["acme/widget"])
    registry(tmp_path, [("acme/widget", "C0AAAAAA1", "proj-widget")])
    scenario(tmp_path, [])
    wrap = tmp_path / "wrap.sh"
    wrap.write_text('#!/bin/sh\nprintf "cwd=%s\\n" "$PWD" >> "$FAKE_WRAP_LOG"\nexec "$SQUAD_CLAUDE_CLI" "$@"\n')
    wrap.chmod(0o755)
    monkeypatch.setattr(op, "WRAPPER", str(wrap))
    monkeypatch.setenv("FAKE_WRAP_LOG", str(tmp_path / "wrap.log"))
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo"), only="acme/widget") == 0
    log = (tmp_path / "wrap.log").read_text()
    assert f"cwd={os.path.realpath(found['acme/widget'])}" in log, log
    assert f"--add-dir {os.path.realpath(found['acme/widget'])}" in brief(tmp_path, "acme/widget")


def test_the_session_loads_the_projects_claude_md(tmp_path, monkeypatch):
    """Criterion 8: no `--bare`, so the CLI discovers the checkout's CLAUDE.md, and the brief points at it."""
    op = operators(tmp_path, monkeypatch)
    found = hub(tmp_path, monkeypatch, op, ["acme/widget"])
    registry(tmp_path, [("acme/widget", "C0AAAAAA1", "proj-widget")])
    scenario(tmp_path, [])
    (Path(found["acme/widget"]) / "CLAUDE.md").write_text("# widget rules\n")
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo"), only="acme/widget") == 0
    argv = next(c for c in started(tmp_path) if "acme/widget" in c)
    assert " --bare " not in f" {argv} ", "a bare session never loads the project CLAUDE.md"
    assert "CLAUDE.md" in argv, argv


def test_a_project_whose_checkout_is_missing_starts_no_session(tmp_path, monkeypatch):
    """Criterion 6: a project whose profile names no checkout on this machine starts nothing, and says so."""
    op = operators(tmp_path, monkeypatch)
    monkeypatch.setattr(op, "hub_projects", lambda *a, **k: [{"repo": "acme/widget", "checkout": "/nonexistent/wt"}])
    registry(tmp_path, [("acme/widget", "C0AAAAAA1", "proj-widget")])
    scenario(tmp_path, [])
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo"), only="acme/widget") == 0
    assert not started(tmp_path), f"a project with no checkout was started: {started(tmp_path)}"


def test_the_profile_writes_only_the_projects_outboxes_and_the_claude_runtime(tmp_path, monkeypatch):
    """Criterion 11: the kernel bound refuses every write outside the session's outboxes and the Claude runtime."""
    op = operators(tmp_path, monkeypatch)
    hub(tmp_path, monkeypatch, op, ["acme/widget"])
    registry(tmp_path, [("acme/widget", "C0AAAAAA1", "proj-widget")])
    revision(tmp_path, "acme/widget", ["C0AAAAAA1", "C0AAAAAA2"])
    scenario(tmp_path, [])
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo"), only="acme/widget") == 0
    entry = record(tmp_path)["acme-widget"]
    text = Path(entry["profile"]["path"]).read_text()
    write = next(line for line in text.splitlines() if line.startswith("(allow file-write*"))
    state = os.path.realpath(tmp_path / "state")
    assert f'(literal "{state}/outboxes/C0AAAAAA1.jsonl")' in write
    assert f'(literal "{state}/outboxes/C0AAAAAA2.jsonl")' in write
    assert f'(literal "{state}/outboxes/dm.jsonl")' not in write, "the project session may write the owner's DM outbox"
    for granted in ("/.claude", "/.claude.json"):
        assert granted in write, write
    assert "/checkouts/" not in write, "the project checkout must stay read-only"


def test_the_profile_refuses_network_outside_loopback(tmp_path, monkeypatch):
    """Criterion 12: the kernel bound refuses every network connection outside loopback."""
    op = operators(tmp_path, monkeypatch)
    text = op.profile_text(["/state/outboxes/C1.jsonl"], "/state/inboxes", ["/state/inboxes/C1.jsonl"])
    lines = text.splitlines()
    assert "(deny network-outbound)" in lines and '(allow network-outbound (remote ip "localhost:*"))' in lines
    assert "*:443" not in text


def test_an_action_request_gets_a_read_only_answer_and_one_dm_proposal(tmp_path, monkeypatch):
    """Criterion 13: the brief answers an action request with the read-only limit and one proposal line to the owner."""
    op = operators(tmp_path, monkeypatch)
    text = op.prompt_for("dm", str(tmp_path / "state"), "/repo")
    assert "never acts" in text and "only reads" in text, text
    assert "Proposal:" in text and "outboxes/dm.jsonl" in text, text


def test_the_dm_session_keeps_its_own_scope_and_the_agent_squad_repository(tmp_path, monkeypatch):
    """Criterion 14: the DM is its own session, in the agent-squad repository, on its own inbox and outbox."""
    op = operators(tmp_path, monkeypatch)
    hub(tmp_path, monkeypatch, op, ["acme/widget"])
    registry(tmp_path, [("acme/widget", "C0AAAAAA1", "proj-widget")])
    scenario(tmp_path, [])
    wrap = tmp_path / "wrap.sh"
    wrap.write_text('#!/bin/sh\nprintf "cwd=%s\\n" "$PWD" >> "$FAKE_WRAP_LOG"\nexec "$SQUAD_CLAUDE_CLI" "$@"\n')
    wrap.chmod(0o755)
    monkeypatch.setattr(op, "WRAPPER", str(wrap))
    monkeypatch.setenv("FAKE_WRAP_LOG", str(tmp_path / "wrap.log"))
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo"), only="dm") == 0
    text = brief(tmp_path, "direct messages")
    state = str(tmp_path / "state")
    assert f"tail -n 0 -F {state}/inboxes/dm.jsonl" in text and f"{state}/outboxes/dm.jsonl" in text, text
    assert f"cwd={os.path.realpath(tmp_path / 'repo')}" in (tmp_path / "wrap.log").read_text()
    entry = record(tmp_path)["dm"]
    assert entry["name"] == "SQUAD-OPERATOR-dm" and entry["checkout"] == os.path.realpath(tmp_path / "repo")


def test_a_session_is_marked_by_its_name_never_by_its_working_directory(tmp_path, monkeypatch):
    """Criterion 15: a session of a project checkout with no token in its name is a person's own, and is never touched."""
    op = operators(tmp_path, monkeypatch)
    found = hub(tmp_path, monkeypatch, op, ["acme/widget"])
    registry(tmp_path, [("acme/widget", "C0AAAAAA1", "proj-widget")])
    scenario(tmp_path, [bare_session(found["acme/widget"], "bbbbbbbb-0000-0000-0000-000000000009")])
    (tmp_path / "state" / "stopped.json").write_text(json.dumps({"dm": 1}))
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo")) == 0
    assert not [c for c in calls(tmp_path) if c.startswith("stop")], "an unmarked session was stopped"
    assert op.carried("SQUAD-OPERATOR-acme-widget") == "acme-widget"
    assert op.carried("a person's own session") is None and op.carried("") is None


def test_the_record_is_written_after_each_start(tmp_path, monkeypatch):
    """The record is written after each start, atomically, so a crash loses no earlier start."""
    op = operators(tmp_path, monkeypatch)
    hub(tmp_path, monkeypatch, op, ["acme/widget"])
    registry(tmp_path, [("acme/widget", "C0AAAAAA1", "proj-widget")])    # two sessions: dm and the project
    scenario(tmp_path, [])
    writes = []
    real = op.write_json
    monkeypatch.setattr(op, "write_json", lambda path, value: (writes.append(dict(value)), real(path, value))[1])
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo")) == 0
    assert len(writes) == 2, f"the record was written {len(writes)} times, not once per start"
    assert set(record(tmp_path)) == {"dm", "acme-widget"}
    assert not [n for n in os.listdir(tmp_path / "state") if n.endswith(".tmp")], "a half-written record was left"


def test_a_dead_session_is_replaced_only_on_a_second_pass(tmp_path, monkeypatch):
    """A recorded session is replaced only when its id is dead and a second pass agrees."""
    op = operators(tmp_path, monkeypatch)
    hub(tmp_path, monkeypatch, op, [])
    scenario(tmp_path, [])
    (tmp_path / "state" / "operators.json").write_text(json.dumps({"dm": {"id": "dead", "pid": 1, "started": 1}}))
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo")) == 0
    assert not started(tmp_path), "a replacement started on the first pass"
    assert record(tmp_path)["dm"]["missing"] == 1
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo")) == 0
    assert started(tmp_path), "the second pass started no replacement"


def test_a_marked_session_outside_the_record_is_adopted_after_two_passes(tmp_path, monkeypatch):
    """A session carrying the token but not in the record is adopted after two consecutive passes."""
    op = operators(tmp_path, monkeypatch)
    hub(tmp_path, monkeypatch, op, [])
    live = session(tmp_path / "repo", "dm", "dddddddd-0000-0000-0000-000000000001")
    scenario(tmp_path, [live])
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo")) == 0
    assert not (record(tmp_path).get("dm") or {}).get("id"), "the first pass adopted the session"
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo")) == 0
    assert record(tmp_path)["dm"]["id"] == live["sessionId"]
    assert not started(tmp_path), "an adopted project was started again"


def test_an_adopted_project_keeps_the_profile_and_proxy_it_ran_under(tmp_path, monkeypatch):
    """#1658: adoption replaces the session id, never the profile or the proxy the record holds for the project."""
    op = operators(tmp_path, monkeypatch)
    hub(tmp_path, monkeypatch, op, [])
    live = session(tmp_path / "repo", "dm", "dddddddd-0000-0000-0000-000000000001")
    scenario(tmp_path, [live])
    profile = {"path": "/state/operators/profiles/dm.sb", "sha256": "ab" * 32}
    proxy = {"pid": 999999, "port": 1}
    held = {"id": "eeeeeeee-0000-0000-0000-00000000000a", "pid": 1, "started": 100, "profile": profile, "proxy": proxy}
    (tmp_path / "state" / "operators.json").write_text(json.dumps({"dm": held}))
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo")) == 0
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo")) == 0
    entry = record(tmp_path)["dm"]
    assert entry["id"] == live["sessionId"] and entry["profile"] == profile and entry["proxy"] == proxy


def test_a_failed_stop_keeps_ownership_and_backs_off(tmp_path, monkeypatch):
    """Ownership is dropped only after the stop is confirmed absent; a failed stop keeps it and backs off."""
    op = operators(tmp_path, monkeypatch)
    found = hub(tmp_path, monkeypatch, op, ["acme/widget"])
    registry(tmp_path, [("acme/widget", "C0AAAAAA1", "proj-widget")])
    (tmp_path / "fail-stop").write_text("1")
    scenario(tmp_path, [])
    (tmp_path / "state" / "operators.json").write_text(json.dumps(
        {"acme-widget": {"id": "ffffffff-0000-0000-0000-000000000001", "pid": 1, "started": 1, "stop": {"tries": 0, "next": 0}}}))
    scenario(tmp_path, [session(found["acme/widget"], "acme-widget", "ffffffff-0000-0000-0000-000000000002", started=300),
                        session(found["acme/widget"], "acme-widget", "ffffffff-0000-0000-0000-000000000001", started=100)])
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo")) == 0
    assert "acme-widget" in record(tmp_path), "ownership dropped after a refused stop"
    assert record(tmp_path)["acme-widget"]["stop"]["tries"] >= 1, "a failed stop recorded no retry"


def test_a_project_stopped_on_purpose_is_not_revived_and_a_dropped_project_loses_its_session(tmp_path, monkeypatch):
    """A project marked stopped is not revived; a project the registry dropped loses its session."""
    op = operators(tmp_path, monkeypatch)
    found = hub(tmp_path, monkeypatch, op, ["acme/widget"])
    registry(tmp_path, [("acme/widget", "C0AAAAAA1", "proj-widget")])
    widget = session(found["acme/widget"], "acme-widget", "11111111-0000-0000-0000-000000000001")
    gone = session(found["acme/widget"], "acme-old", "11111111-0000-0000-0000-000000000002")
    (tmp_path / "state" / "operators.json").write_text(json.dumps({
        "acme-widget": {"id": widget["sessionId"], "pid": widget["pid"], "started": 1},
        "acme-old": {"id": gone["sessionId"], "pid": gone["pid"], "started": 1}}))
    (tmp_path / "state" / "stopped.json").write_text(json.dumps({"acme-widget": 1}))
    scenario(tmp_path, [widget, gone])
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo")) == 0
    assert [c for c in calls(tmp_path) if c.startswith("stop")] == [f"stop {gone['sessionId']}"], \
        "the dropped project kept its session or the stopped one moved"
    assert not [c for c in started(tmp_path) if "acme/widget" in c], "a project stopped on purpose was revived"


def test_every_cli_call_has_a_deadline_and_one_lock_bounds_a_pass(tmp_path, monkeypatch):
    """Every CLI call has a deadline, and one lock bounds a pass."""
    op = operators(tmp_path, monkeypatch)
    hub(tmp_path, monkeypatch, op, [])
    scenario(tmp_path, [])
    timeouts = []
    real = op.call
    monkeypatch.setattr(op, "call", lambda argv, cwd=None, timeout=op.DEADLINE, env=None:
                        (timeouts.append(timeout), real(argv, cwd, timeout, env))[1])
    held = op.acquire(str(tmp_path / "state"))
    try:
        assert op.acquire(str(tmp_path / "state")) is None, "a second pass took the lock"
    finally:
        os.close(held)
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo")) == 0
    assert timeouts and all(t and t > 0 for t in timeouts), f"a CLI call ran with no deadline: {timeouts}"


def test_a_refused_start_is_named_and_exits_non_zero(tmp_path, monkeypatch):
    """The module's own rule: a CLI refusal is one named line on stderr and exit 1, never a silent empty pass."""
    op = operators(tmp_path, monkeypatch)
    hub(tmp_path, monkeypatch, op, [])
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
    hub(tmp_path, monkeypatch, op, [])
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
    scenario(tmp_path, [])
    return op


def wrap_log(tmp_path):
    return (tmp_path / "wrap.log").read_text() if (tmp_path / "wrap.log").exists() else ""


def test_a_session_starts_through_the_sandbox_wrapper_under_the_operator_profile(tmp_path, monkeypatch):
    """#1658: each operator session starts through claude-sandboxed.sh, and the profile it runs under is recorded."""
    op = wrapped(tmp_path, monkeypatch)
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo")) == 0
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
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo")) == 0
    assert "sandboxed=1" in wrap_log(tmp_path)


def test_a_loopback_endpoint_starts_its_session_with_no_proxy(tmp_path, monkeypatch):
    """#1658: a loopback model endpoint is reached under the profile's loopback rule, so no proxy starts."""
    op = wrapped(tmp_path, monkeypatch, base_url="http://localhost:8016")
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo")) == 0
    assert "proxy=unset" in wrap_log(tmp_path)
    assert "proxy" not in record(tmp_path)["dm"]


def test_a_foreign_endpoint_gets_a_proxy_that_ends_with_its_session(tmp_path, monkeypatch):
    """#1658: a foreign endpoint goes through model_proxy.py, HTTPS_PROXY names it, and retiring the session ends it."""
    op = wrapped(tmp_path, monkeypatch, base_url="https://api.anthropic.com")
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo")) == 0
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
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo")) == 0
    assert "model proxy refused evil.example:443 for dm" in capsys.readouterr().err


def test_the_brief_carries_the_channel_language_and_the_persona_tone(tmp_path, monkeypatch):
    """#2688 criteria (5) and (6): the instruction for the text reply carries the channel language and the persona
    tone, read from the active revision of the project that owns the scope."""
    op = operators(tmp_path, monkeypatch)
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    (state / "channels.json").write_text(json.dumps({"acme/app": {"name": "proj-acme-app", "id": "C0PROJ0001"}}))
    slack_revision(state, "acme/app", "C0PROJ0001")
    text = op.prompt_for("C0PROJ0001", str(state), "/repo")
    assert "pt-BR" in text, text
    assert "Warm and patient. Confirms what was asked." in text, text
    assert "even when the sender writes in another language" in text, text


def slack_revision(state, repo, cid):
    revision = {
        "project": {"language": "en-US", "persona": "iara"},
        "personas": [{"id": "iara", "name": "Iara", "voice": "Sulafat",
                      "tone": "Warm and patient. Confirms what was asked."}],
        "workspaces": [{"id": "T0TEAM0001", "label": "team", "class": "owned", "app": "hal9k",
                        "channels": [{"id": cid, "name": "proj-acme-app", "mode": "listen+speak",
                                      "language": "pt-BR", "persona": "iara"}]}],
    }
    (state / "slack").mkdir(exist_ok=True)
    digest = hashlib.sha256(repo.encode()).hexdigest()[:16]
    (state / "slack" / f"{digest}.json").write_text(json.dumps(
        {"repo": repo, "sha": "a" * 40, "revision": revision, "local": {}}))


def test_the_brief_tells_the_session_to_name_the_channel_persona_on_its_answers(tmp_path, monkeypatch):
    """#2935: the brief carries the channel persona's id, so the session writes `persona` on each of its own answers."""
    op = operators(tmp_path, monkeypatch)
    state = tmp_path / "state"
    state.mkdir()
    (state / "channels.json").write_text(json.dumps({"acme/app": {"name": "proj-acme-app", "id": "C0PROJ0001"}}))
    revision = {
        "project": {"language": "en-US", "persona": "hal"},
        "personas": [{"id": "hal", "name": "HAL9K", "voice": "Charon", "tone": "Calm and precise."}],
        "workspaces": [{"id": "T0TEAM0001", "label": "team", "class": "owned", "app": "hal9k",
                        "channels": [{"id": "C0PROJ0001", "name": "proj-acme-app", "mode": "listen+speak",
                                      "language": "pt-BR", "persona": "hal"}]}],
    }
    (state / "slack").mkdir()
    digest = hashlib.sha256(b"acme/app").hexdigest()[:16]
    (state / "slack" / f"{digest}.json").write_text(json.dumps(
        {"repo": "acme/app", "sha": "a" * 40, "revision": revision, "local": {}}))

    text = op.prompt_for("C0PROJ0001", str(state), "/repo")

    assert '"persona":"hal"' in text, text


def test_the_dm_brief_keeps_its_language_when_no_revision_names_a_persona(tmp_path, monkeypatch):
    """#2688: the DM has no channel persona, so the brief keeps the prototype's Portuguese answer and no tone."""
    op = operators(tmp_path, monkeypatch)
    text = op.prompt_for("dm", str(tmp_path / "state"), "/repo")
    assert "Brazilian Portuguese" in text, text
    assert '"persona":' not in text, text


API_ERROR = {"type": "assistant", "isApiErrorMessage": True, "error": "Connection error",
             "message": {"id": "api-err", "model": "<synthetic>", "usage": {"input_tokens": 0, "output_tokens": 0},
                         "content": [{"type": "text",
                                      "text": "API Error: getaddrinfo ENOTFOUND api.anthropic.com"}]}}


def transcript(tmp_path, cwd, entries, mtime):
    """Write one session's transcript at the folder the CLI derives from the project checkout."""
    folder = tmp_path / "projects" / re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd))
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "session.jsonl"
    path.write_text("".join(json.dumps(e) + "\n" for e in entries))
    os.utime(path, (mtime, mtime))
    return path


def inbox(tmp_path, scope, mtime, text="hello"):
    path = tmp_path / "state" / "inboxes" / f"{scope}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"id": "m1", "ts": "1762500001.000200", "user": "U1", "channel": scope,
                                "text": text, "clip": False}) + "\n")
    os.utime(path, (mtime, mtime))
    return path


def test_a_session_whose_last_turn_failed_with_an_api_error_is_restarted(tmp_path, monkeypatch):
    """Criterion 16, issue #2923: a listed session whose last turn ended in an API error is stopped and replaced."""
    op = operators(tmp_path, monkeypatch)
    found = hub(tmp_path, monkeypatch, op, ["acme/widget"])
    registry(tmp_path, [("acme/widget", "C0AAAAAA1", "proj-widget")])
    live = session(found["acme/widget"], "acme-widget", "abababab-0000-0000-0000-000000000001", started=1)
    (tmp_path / "state" / "operators.json").write_text(json.dumps(
        {"acme-widget": {"id": live["sessionId"], "pid": live["pid"], "started": 1}}))
    scenario(tmp_path, [live])
    transcript(tmp_path, found["acme/widget"], [API_ERROR], mtime=time.time() - 60)
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo")) == 0
    assert f"stop {live['sessionId']}" in calls(tmp_path), "the dead session was never stopped"
    assert len([c for c in started(tmp_path) if "acme/widget" in c]) == 1, "no replacement started"
    assert record(tmp_path)["acme-widget"]["id"] == "aaaaaaaa-0000-0000-0000-000000000001"


def test_a_session_that_takes_no_turn_after_a_new_inbox_line_is_restarted(tmp_path, monkeypatch):
    """#2923 expected 2: an inbox line newer than the last turn, past the wait, restarts the session."""
    op = operators(tmp_path, monkeypatch)
    found = hub(tmp_path, monkeypatch, op, ["acme/widget"])
    registry(tmp_path, [("acme/widget", "C0AAAAAA1", "proj-widget")])
    now = time.time()
    live = session(found["acme/widget"], "acme-widget", "abababab-0000-0000-0000-000000000002", started=1)
    (tmp_path / "state" / "operators.json").write_text(json.dumps(
        {"acme-widget": {"id": live["sessionId"], "pid": live["pid"], "started": 1}}))
    scenario(tmp_path, [live])
    transcript(tmp_path, found["acme/widget"], [{"type": "assistant", "message": {
        "id": "m0", "model": "claude-sonnet-5-5", "usage": {"input_tokens": 1, "output_tokens": 1}}}], mtime=now - 3600)
    inbox(tmp_path, "C0AAAAAA1", mtime=now - 600)
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo")) == 0
    assert f"stop {live['sessionId']}" in calls(tmp_path), "a session that took no turn was never stopped"
    assert len([c for c in started(tmp_path) if "acme/widget" in c]) == 1, "no replacement started"


def test_a_session_that_answered_the_new_inbox_line_is_never_restarted(tmp_path, monkeypatch):
    """#2923: a turn newer than the inbox line is a live session, however long ago it answered."""
    op = operators(tmp_path, monkeypatch)
    found = hub(tmp_path, monkeypatch, op, ["acme/widget"])
    registry(tmp_path, [("acme/widget", "C0AAAAAA1", "proj-widget")])
    now = time.time()
    live = session(found["acme/widget"], "acme-widget", "abababab-0000-0000-0000-000000000003", started=1)
    (tmp_path / "state" / "operators.json").write_text(json.dumps(
        {"acme-widget": {"id": live["sessionId"], "pid": live["pid"], "started": 1}}))
    scenario(tmp_path, [live])
    inbox(tmp_path, "C0AAAAAA1", mtime=now - 3600)
    transcript(tmp_path, found["acme/widget"], [{"type": "assistant", "message": {
        "id": "m0", "model": "claude-sonnet-5-5", "usage": {"input_tokens": 1, "output_tokens": 1}}}], mtime=now - 600)
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo"), only="acme/widget") == 0
    assert not [c for c in calls(tmp_path) if c.startswith("stop")], "a live session was stopped"
    assert not started(tmp_path), "a live session was replaced"


def test_a_restart_with_no_answer_alerts_once_and_names_the_project(tmp_path, monkeypatch, capsys):
    """#2923 expected 3: the unanswered line raises one alert that names the project, and no second one."""
    op = operators(tmp_path, monkeypatch)
    found = hub(tmp_path, monkeypatch, op, ["acme/widget"])
    registry(tmp_path, [("acme/widget", "C0AAAAAA1", "proj-widget")])
    now = time.time()
    live = session(found["acme/widget"], "acme-widget", "abababab-0000-0000-0000-000000000004", started=1)
    (tmp_path / "state" / "operators.json").write_text(json.dumps(
        {"acme-widget": {"id": live["sessionId"], "pid": live["pid"], "started": 1}}))
    scenario(tmp_path, [live])
    transcript(tmp_path, found["acme/widget"], [API_ERROR], mtime=now - 60)
    inbox(tmp_path, "C0AAAAAA1", mtime=now - 600)
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo")) == 0
    err = capsys.readouterr().err
    assert "acme-widget" in err and "answer" in err, f"no alert named the project: {err!r}"
    assert err.count("alert") == 1, f"the restarted project raised {err.count('alert')} alerts: {err!r}"
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo")) == 0
    assert capsys.readouterr().err.count("alert") == 0, "the same unanswered line alerted twice"
