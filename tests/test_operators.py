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
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.proxystop import track_starts

ROOT = Path(__file__).resolve().parent.parent
VOICE = ROOT / "contrib" / "voice"

FAKE = """#!/bin/sh
printf -- '---CALL---\\n%s\\n' "$*" >> "$FAKE_LOG"
printf '%s|%s|%s\\n' "${TERMUX_VERSION-unset}" "${PREFIX-unset}" "$(printf '%s' "$*" | tr '\\n' ' ')" >> "$FAKE_ENV_LOG"
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
    monkeypatch.setenv("FAKE_ENV_LOG", str(tmp_path / "env.log"))
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
    pinned_dm_record(tmp_path / "state", "D0AAAAAA1")   # the daemon pinned the owner's DM channel
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


def envs(tmp_path):
    """Each CLI call the fake got, with the two variables that must name the project's own service: (termux, prefix,
    argv)."""
    path = tmp_path / "env.log"
    if not path.exists():
        return []
    return [tuple(line.split("|", 2)) for line in path.read_text().splitlines() if line.count("|") >= 2]


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
    """Criterion 10: the brief sends each answer through the helper with the inbox line's own channel, and it names no
    outbox path for a free append (#2949)."""
    op = operators(tmp_path, monkeypatch)
    hub(tmp_path, monkeypatch, op, ["acme/widget"])
    registry(tmp_path, [("acme/widget", "C0AAAAAA1", "proj-widget")])
    revision(tmp_path, "acme/widget", ["C0AAAAAA1", "C0AAAAAA2"])
    scenario(tmp_path, [])
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo")) == 0
    text = brief(tmp_path, "acme/widget")
    assert op.ANSWER in text and "--scope" in text, text
    for scope in ("C0AAAAAA1", "C0AAAAAA2"):        # one helper line per channel, each with its own scope
        assert f"{op.ANSWER} --scope {scope}" in text, text
    assert "outboxes/" not in text, "the brief names an outbox path for a free append"
    assert "C0AAAAAA9" not in text


@pytest.mark.heavy
def test_the_brief_names_the_answer_helper_as_the_only_answer_path(tmp_path, monkeypatch):
    """#2949 criterion (5): every answer leaves through the one shipped helper, and the brief names no other path."""
    op = operators(tmp_path, monkeypatch)
    hub(tmp_path, monkeypatch, op, ["acme/widget"])
    registry(tmp_path, [("acme/widget", "C0AAAAAA1", "proj-widget")])
    scenario(tmp_path, [])
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo")) == 0
    text = brief(tmp_path, "acme/widget")
    state = str(tmp_path / "state")
    assert f"{op.ANSWER} --scope" in text, text
    for flag in ("--thread", "--text", "--speech"):
        assert flag in text, text
    assert "appending ONE JSON line to the outbox" not in text, text
    assert f"{state}/outboxes/C0AAAAAA1.jsonl" not in text, "the brief names an outbox for a free append"


def test_a_start_binds_the_session_to_its_project_for_the_answer_helper(tmp_path, monkeypatch):
    """#2949: the supervisor writes the binding and names it in the session's env, so the helper answers only the
    channels of this project and nothing else."""
    op = operators(tmp_path, monkeypatch)
    seen = {}

    def fake_call(argv, cwd=None, timeout=None, env=None):
        seen.update(env or {})
        return subprocess.CompletedProcess(argv, 0, "aaaaaaaa-0000-0000-0000-000000000001\n", "")

    monkeypatch.setattr(op, "call", fake_call)
    (tmp_path / "repo").mkdir(exist_ok=True)
    state = str(tmp_path / "state")
    (tmp_path / "state").mkdir(exist_ok=True)
    op.start({"key": "acme-widget", "repo": "acme/widget", "checkout": str(tmp_path / "repo"),
              "channels": ["C0AAAAAA1", "C0AAAAAA2"]}, state, str(tmp_path / "repo"))
    path = seen["VOICE_ANSWER_BINDING"]
    binding = json.loads(Path(path).read_text())
    assert binding["channels"] == ["C0AAAAAA1", "C0AAAAAA2"], binding
    assert binding["state"] == state, binding


def test_a_dm_start_binds_the_session_to_the_one_dm_channel(tmp_path, monkeypatch):
    """#2949 criterion (2): the DM session's binding names the pinned DM channel, so a dm inbox line that names another
    channel cannot carry that channel into the dm outbox."""
    op = operators(tmp_path, monkeypatch)
    seen = {}

    def fake_call(argv, cwd=None, timeout=None, env=None):
        seen.update(env or {})
        return subprocess.CompletedProcess(argv, 0, "aaaaaaaa-0000-0000-0000-000000000001\n", "")

    monkeypatch.setattr(op, "call", fake_call)
    state = tmp_path / "state"
    (state / "inboxes").mkdir(parents=True)
    (tmp_path / "repo").mkdir(exist_ok=True)
    pinned_dm_record(state, "D0AAAAAA1")           # the supervisor's own record, never an inbox line
    line = {"id": "dm:1", "ts": "1762500000.000100", "user": "UPROBE0001", "channel": "D0ZZZZZZ", "text": "hi"}
    (state / "inboxes" / "dm.jsonl").write_text(json.dumps(line) + "\n")
    op.start("dm", str(state), str(tmp_path / "repo"))
    binding = json.loads(Path(seen["VOICE_ANSWER_BINDING"]).read_text())
    assert binding["channels"] == ["dm"], binding
    assert binding["dm"] == "D0AAAAAA1", binding


def pinned_dm_record(state, channel):
    """The supervisor's pinned DM record at `<state>/operators/sessions/dm.json` (#2949): the one channel the daemon
    may post a DM answer to."""
    folder = state / "operators" / "sessions"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "dm.json").write_text(json.dumps({"dm": channel}))


def test_a_dm_binding_refuses_with_no_pinned_record(tmp_path, monkeypatch):
    """Blocking 2 (#2949): the binding path never takes an inbox line's channel as the DM channel; with no pinned
    record it refuses to bind a DM, and the refusal names the missing record."""
    op = operators(tmp_path, monkeypatch)
    state = tmp_path / "state"
    (state / "inboxes").mkdir(parents=True)
    (state / "operators" / "sessions" / "dm.json").unlink()    # no pinned record was written yet
    (state / "inboxes" / "dm.jsonl").write_text(json.dumps({"channel": "D0AAAAAA1"}) + "\n")

    with pytest.raises(op.Refused, match="pinned DM channel"):
        op.binding(str(state), {"key": "dm", "repo": "/repo", "checkout": "/repo", "channels": []})


def test_the_brief_names_the_helper_as_the_one_command_a_session_runs(tmp_path, monkeypatch):
    """#2949 criterion (5): the read-only limit and the one answer path agree, so a session that follows the brief
    runs the helper and no other command."""
    op = operators(tmp_path, monkeypatch)
    for key in ("C0AAAAAA1", "dm"):
        text = op.prompt_for(key, str(tmp_path / "state"), "/repo")
        assert "runs no command" not in text.lower(), text
        assert "the one command" in text.lower() and op.ANSWER in text, text


def test_a_project_brief_writes_the_action_request_through_the_helper(tmp_path, monkeypatch):
    """ADR-0179 (review of #2950): a project session writes the request line through the helper, and the DM session
    never does."""
    op = operators(tmp_path, monkeypatch)
    project = op.prompt_for("C0AAAAAA1", str(tmp_path / "state"), "/repo")
    dm = op.prompt_for("dm", str(tmp_path / "state"), "/repo")
    assert "--kind action-request" in project and op.ANSWER in project, project
    assert "action-request" not in dm, dm


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


def test_ensure_names_the_session_it_started_and_the_one_it_kept(tmp_path, monkeypatch, capsys):
    """Criterion 4: the ensure says whether it started a session or kept a live one, so the caller never reads a pass
    that started nothing as a success. The daemon's watcher tells the two apart by this one line (#2925)."""
    op = operators(tmp_path, monkeypatch)
    found = hub(tmp_path, monkeypatch, op, ["acme/widget"])
    registry(tmp_path, [("acme/widget", "C0AAAAAA1", "proj-widget")])
    scenario(tmp_path, [])
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo"), only="acme/widget") == 0
    out = capsys.readouterr().out
    assert "operators: ensure acme/widget started " in out, out

    live = session(found["acme/widget"], "acme-widget", "cccccccc-0000-0000-0000-000000000001",
                   started=int(time.time()))
    scenario(tmp_path, [live])
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo"), only="acme/widget") == 0
    out = capsys.readouterr().out
    assert f"operators: ensure acme/widget live {live['sessionId']}" in out, out
    assert len(started(tmp_path)) == 1, f"the live project was started again: {started(tmp_path)}"


def test_ensure_that_started_no_session_exits_non_zero_and_names_the_project(tmp_path, monkeypatch, capsys):
    """Criterion 4: no live session and no start is a failure the caller acts on, never a silent no-op. Re-anchored
    (#2925): an ensure now replaces a dead session at once, so the case that starts nothing is a project whose last stop
    failed and still waits for its retry."""
    op = operators(tmp_path, monkeypatch)
    hub(tmp_path, monkeypatch, op, ["acme/widget"])
    registry(tmp_path, [("acme/widget", "C0AAAAAA1", "proj-widget")])
    (tmp_path / "state" / "operators.json").write_text(json.dumps(
        {"acme-widget": {"id": "dddddddd-0000-0000-0000-000000000001", "pid": 9, "started": 1,
                         "stop": {"tries": 1, "next": time.time() + 600}}}))
    scenario(tmp_path, [])
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo"), only="acme/widget") == 2
    assert not started(tmp_path), f"a project with no listed session was started: {started(tmp_path)}"
    err = capsys.readouterr().err
    assert "acme/widget" in err and "no session" in err, err


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


@pytest.mark.heavy
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


def test_a_project_whose_checkout_is_missing_starts_no_session(tmp_path, monkeypatch, capsys):
    """Criterion 6: a project whose profile names no checkout on this machine starts nothing, and the ensure says so.

    The message that asked for the session is still waiting, so the daemon's fire-and-forget start must not look like a
    success: `--ensure` exits non-zero and names the missing checkout, and the daemon logs that outcome (#2925)."""
    op = operators(tmp_path, monkeypatch)
    monkeypatch.setattr(op, "hub_projects", lambda *a, **k: [{"repo": "acme/widget", "checkout": "/nonexistent/wt"}])
    registry(tmp_path, [("acme/widget", "C0AAAAAA1", "proj-widget")])
    scenario(tmp_path, [])
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo"), only="acme/widget") == 2
    assert not started(tmp_path), f"a project with no checkout was started: {started(tmp_path)}"
    err = capsys.readouterr().err
    assert "acme/widget" in err and "no checkout" in err, err


def test_ensure_of_a_project_the_registry_does_not_name_exits_non_zero(tmp_path, monkeypatch, capsys):
    """Criterion 4: an ensure for a project no registry row names is an error, never a silent no-op."""
    op = operators(tmp_path, monkeypatch)
    hub(tmp_path, monkeypatch, op, ["acme/widget"])
    registry(tmp_path, [("acme/widget", "C0AAAAAA1", "proj-widget")])
    scenario(tmp_path, [])
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo"), only="acme/ghost") == 2
    assert not started(tmp_path), f"an unknown project started a session: {started(tmp_path)}"
    assert "acme/ghost" in capsys.readouterr().err


def test_the_full_pass_never_fails_for_a_project_with_no_checkout(tmp_path, monkeypatch):
    """Criterion 3: a machine that lacks one checkout still runs the launchd pass, and starts every other session."""
    op = operators(tmp_path, monkeypatch)
    found = hub(tmp_path, monkeypatch, op, ["acme/widget"])
    monkeypatch.setattr(op, "hub_projects", lambda *a, **k: [{"repo": "acme/widget", "checkout": found["acme/widget"]},
                                                            {"repo": "acme/api", "checkout": "/nonexistent/wt"}])
    registry(tmp_path, [("acme/widget", "C0AAAAAA1", "proj-widget"), ("acme/api", "C0BBBBBB1", "proj-api")])
    scenario(tmp_path, [])
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo")) == 0
    assert set(record(tmp_path)) == {"dm", "acme-widget"}, record(tmp_path)


def profiles_folder(tmp_path, monkeypatch, *projects):
    """The profiles folder the seam reads, with one project profile per name and always the template."""
    folder = tmp_path / "profiles"
    folder.mkdir(exist_ok=True)
    (folder / "example.sh").write_text(': "${SG_REPO:=owner/repo}"\n')
    for name in projects:
        checkout = tmp_path / "checkouts" / name
        checkout.mkdir(parents=True, exist_ok=True)
        (folder / f"{name}.sh").write_text(f': "${{SG_REPO:=acme/{name}}}"\n: "${{SG_CHECKOUT:={checkout}}}"\n')
    monkeypatch.setenv("SG_PROFILES_DIR", str(folder))
    return folder


def test_a_pass_that_finds_no_project_names_the_channel_maps_mismatch(tmp_path, monkeypatch, capsys):
    """#2957: the hub lists no project while the channel map names one, as a deploy whose profiles folder holds only
    example.sh does. The pass starts nothing, and the error log names the mismatch instead of staying silent, with the
    folder the pass read and its state as the hint."""
    op = operators(tmp_path, monkeypatch)
    folder = profiles_folder(tmp_path, monkeypatch)   # the documented cause: the folder holds only example.sh
    hub(tmp_path, monkeypatch, op, [])                # the hub seam answers: no profile names a repository
    registry(tmp_path, [("acme/widget", "C0AAAAAA1", "proj-widget")])
    scenario(tmp_path, [])
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo")) == 0
    assert set(record(tmp_path)) == {"dm"}, record(tmp_path)   # only the owner's own session starts
    err = capsys.readouterr().err
    assert "acme/widget" in err and "no project" in err, err
    assert str(folder) in err and "example.sh" in err, err


def test_a_pass_with_no_project_and_no_channel_map_reports_no_fault(tmp_path, monkeypatch, capsys):
    """#2957: a configuration with no project is valid, so a pass with no channel map and no hub project names no
    mismatch: the error log stays empty."""
    op = operators(tmp_path, monkeypatch)
    profiles_folder(tmp_path, monkeypatch)            # the template alone is a valid configuration here
    hub(tmp_path, monkeypatch, op, [])
    scenario(tmp_path, [])
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo")) == 0
    assert capsys.readouterr().err == ""


def test_a_pass_the_hub_answers_for_reports_no_fault(tmp_path, monkeypatch, capsys):
    """#2957: when the hub names a project there is no mismatch, so the error log stays empty."""
    op = operators(tmp_path, monkeypatch)
    profiles_folder(tmp_path, monkeypatch, "widget")
    hub(tmp_path, monkeypatch, op, ["acme/widget"])
    registry(tmp_path, [("acme/widget", "C0AAAAAA1", "proj-widget")])
    scenario(tmp_path, [])
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo")) == 0
    assert capsys.readouterr().err == ""


def test_a_profiles_folder_with_a_project_still_names_the_mismatch(tmp_path, monkeypatch, capsys):
    """#2957: the report follows the hub seam alone. The channel map names a project and the hub lists none, so the pass
    names the mismatch even when the profiles folder holds a project: the folder hint is a suffix, never a gate."""
    op = operators(tmp_path, monkeypatch)
    folder = profiles_folder(tmp_path, monkeypatch, "widget")   # a project profile the owner wrote on purpose
    hub(tmp_path, monkeypatch, op, [])                # the hub seam answers no project this pass
    registry(tmp_path, [("acme/widget", "C0AAAAAA1", "proj-widget")])
    scenario(tmp_path, [])
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo")) == 0
    err = capsys.readouterr().err
    assert "acme/widget" in err and "no project" in err, err
    assert str(folder) in err and "example.sh" not in err, err


def test_a_missing_profiles_folder_names_the_path_and_the_unset_variable(tmp_path, monkeypatch, capsys):
    """#2957: with SG_PROFILES_DIR unset the pass reads the fallback beside address.sh. When that folder is not there,
    the hint names the fallback path and the unset variable, so the operator never blames a folder that holds no
    project when the pass read the wrong path."""
    op = operators(tmp_path, monkeypatch)
    monkeypatch.delenv("SG_PROFILES_DIR", raising=False)
    missing = tmp_path / "there-is-no-such-profiles-folder"
    monkeypatch.setattr(op, "PROFILES_FALLBACK", str(missing))
    hub(tmp_path, monkeypatch, op, [])
    registry(tmp_path, [("acme/widget", "C0AAAAAA1", "proj-widget")])
    scenario(tmp_path, [])
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo")) == 0
    err = capsys.readouterr().err
    assert "acme/widget" in err and "no project" in err, err
    assert str(missing) in err and "is not there" in err and "SG_PROFILES_DIR is unset" in err, err


def test_a_named_profiles_folder_that_is_missing_blames_no_unset_variable(tmp_path, monkeypatch, capsys):
    """#2957: a folder SG_PROFILES_DIR names and the filesystem lacks is named by its own path, and the hint says the
    variable is set, so the operator reads which path the pass read and why it holds no project."""
    op = operators(tmp_path, monkeypatch)
    missing = tmp_path / "named-but-missing"
    monkeypatch.setenv("SG_PROFILES_DIR", str(missing))
    hub(tmp_path, monkeypatch, op, [])
    registry(tmp_path, [("acme/widget", "C0AAAAAA1", "proj-widget")])
    scenario(tmp_path, [])
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo")) == 0
    err = capsys.readouterr().err
    assert str(missing) in err and "is not there" in err, err
    assert "SG_PROFILES_DIR is unset" not in err, err


def test_a_profiles_folder_with_a_project_starts_its_session(tmp_path, monkeypatch):
    """Criterion 2 (#2957): the agents read their projects from SG_PROFILES_DIR, so a folder with a project profile
    makes the pass start one session for that project, through the hub seam production reads."""
    op = operators(tmp_path, monkeypatch)
    profiles_folder(tmp_path, monkeypatch, "widget")   # acme/widget, checkout under the tmp tree
    registry(tmp_path, [("acme/widget", "C0AAAAAA1", "proj-widget")])
    scenario(tmp_path, [])
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo")) == 0
    assert "acme-widget" in record(tmp_path), record(tmp_path)
    assert any("SQUAD-OPERATOR-acme-widget" in c for c in started(tmp_path)), calls(tmp_path)


def test_the_profile_writes_only_the_projects_outboxes_and_the_claude_runtime(tmp_path, monkeypatch):
    """Criterion 11: the kernel bound refuses every write outside the session's outboxes, the Claude runtime, and the
    CLI's own config root (#2963). The owner's global config file is granted to no one."""
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
    home = os.path.realpath(os.path.expanduser("~"))
    assert f'(subpath "{home}/.claude")' in write, "the CLI's own state under the Claude home stays writable"
    assert f'(subpath "{op.config_root(state)}")' in write, write
    assert ".claude.json" not in write, "the session may write the owner's global config file"
    assert "/checkouts/" not in write, "the project checkout must stay read-only"


def test_the_profile_refuses_network_outside_loopback(tmp_path, monkeypatch):
    """Criterion 12: the kernel bound refuses every network connection outside loopback."""
    op = operators(tmp_path, monkeypatch)
    text = op.profile_text(["/state/outboxes/C1.jsonl"], "/state/inboxes", ["/state/inboxes/C1.jsonl"],
                           config=str(tmp_path / "config"))
    lines = text.splitlines()
    assert "(deny network-outbound)" in lines and "localhost:*" not in text
    assert "*:443" not in text


def test_the_profile_allows_loopback_only_on_the_model_endpoint_port(tmp_path, monkeypatch):
    """#2951 criterion 2: a hook defect reaches no loopback service but the model route, so the port comes from the
    endpoint configuration and never from a guessed constant."""
    op = operators(tmp_path, monkeypatch)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8016")
    text = op.profile_text(["/state/outboxes/C1.jsonl"], "/state/inboxes", ["/state/inboxes/C1.jsonl"],
                           config=str(tmp_path / "config"))
    assert '(allow network-outbound (remote ip "localhost:8016"))' in text.splitlines(), text
    assert "localhost:*" not in text
    monkeypatch.delenv("ANTHROPIC_BASE_URL")
    text = op.profile_text(["/state/outboxes/C1.jsonl"], "/state/inboxes", ["/state/inboxes/C1.jsonl"],
                           ports=[9317], config=str(tmp_path / "config"))
    assert '(allow network-outbound (remote ip "localhost:9317"))' in text.splitlines(), text
    assert "localhost:8016" not in text, "a port the caller names replaces the endpoint port"


def test_the_profile_refuses_reads_of_the_secret_list_and_another_checkout(tmp_path, monkeypatch):
    """#2951 criterion 1: the kernel refuses every read of the owner's credentials, a project's secret files and another
    project's checkout, while the session's own checkout stays readable."""
    op = operators(tmp_path, monkeypatch)
    home = os.path.realpath(os.path.expanduser("~"))
    text = op.profile_text(["/state/outboxes/C1.jsonl"], "/state/inboxes", ["/state/inboxes/C1.jsonl"],
                           checkout="/work/acme", others=["/work/other", "/work/acme"],
                           config=str(tmp_path / "config"))
    lines = text.splitlines()
    creds = f'(deny file-read* (subpath "{home}/.ssh") (subpath "{home}/.config/gh") (subpath "{home}/.aws"))'
    assert creds in lines, text
    assert any(line.startswith("(deny file-read*") and '(regex #"^(.*/)?\\.env' in line for line in lines), text
    assert any(line.startswith("(deny file-read*") and "(.*/)?[^/]*\\.slack\\.local\\.yaml" in line for line in lines), text
    assert '(deny file-read* (subpath "/work/other"))' in lines, text
    allow = f'(allow file-read* (subpath "{os.path.realpath("/work/acme")}"))'
    assert allow in lines, "the session's own checkout must stay readable"
    after = max(i for i, line in enumerate(lines)
                if line.startswith("(deny file-read*") and ('\\.env' in line or "\\.slack\\.local" in line))
    assert lines.index(allow) > after, "the own checkout allow must follow every secret deny (last rule wins)"


def test_the_profile_reallows_the_own_checkout_secret_named_files(tmp_path, monkeypatch):
    """#2951 criteria 1 and 3: a project keeps its `.env` and `*.slack.local.yaml` in its own checkout, so the read
    wall must not swallow them: the own checkout is re-allowed after the secret denies, and another checkout is not."""
    op = operators(tmp_path, monkeypatch)
    own = os.path.realpath("/work/acme")
    text = op.profile_text(["/state/outboxes/C1.jsonl"], "/state/inboxes", ["/state/inboxes/C1.jsonl"],
                           checkout="/work/acme", others=["/work/other"], config=str(tmp_path / "config"))
    lines = text.splitlines()
    assert f'(allow file-read* (subpath "{own}"))' in lines, text
    assert f'(deny file-read* (subpath "{os.path.realpath("/work/other")}"))' in lines, text


def test_a_started_session_refuses_the_checkout_of_another_project(tmp_path, monkeypatch):
    """#2951 criterion 1: a project's session never reads another project's checkout, and its own stays open."""
    op = operators(tmp_path, monkeypatch)
    found = hub(tmp_path, monkeypatch, op, ["acme/widget", "acme/gadget"])
    registry(tmp_path, [("acme/widget", "C0AAAAAA1", "proj-widget"), ("acme/gadget", "C0AAAAAA2", "proj-gadget")])
    scenario(tmp_path, [])
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo"), only="acme/widget") == 0
    text = Path(record(tmp_path)["acme-widget"]["profile"]["path"]).read_text()
    own, other = (os.path.realpath(found[name]) for name in ("acme/widget", "acme/gadget"))
    assert f'(deny file-read* (subpath "{other}"))' in text.splitlines(), text
    assert f'(allow file-read* (subpath "{own}"))' in text.splitlines(), text
    repo = os.path.realpath(tmp_path / "repo")
    assert f'(subpath "{repo}")' not in text, "the operator contract's checkout stays readable through --add-dir"


def test_an_action_request_gets_a_read_only_answer_and_one_dm_proposal(tmp_path, monkeypatch):
    """Criterion 13: the brief answers an action request with the read-only limit and one proposal line to the owner,
    and the proposal leaves through the helper like every other answer (#2949)."""
    op = operators(tmp_path, monkeypatch)
    text = op.prompt_for("dm", str(tmp_path / "state"), "/repo")
    assert "never acts" in text and "only reads" in text, text
    assert "Proposal:" in text and op.ANSWER in text, text
    assert "outboxes/dm.jsonl" not in text, text


def test_a_project_action_message_gets_a_read_only_answer(tmp_path, monkeypatch):
    """#2950 with ADR-0179: the project brief orders the request through the one helper call the gate allows, never a
    line the session writes itself, and it names no outbox path (#2939, #2949)."""
    op = operators(tmp_path, monkeypatch)
    text = op.prompt_for("C0AAAAAA1", str(tmp_path / "state"), "/repo")
    assert "only reads and answers" in text and "--kind action-request" in text and op.ANSWER in text, text
    assert '"kind"' not in text, "the brief orders a hand-written request line the gate would deny"
    assert op.outbox_path(str(tmp_path / "state"), "C0AAAAAA1") not in text, "the brief names an outbox for a free append"


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
    assert f"tail -n 0 -F {state}/inboxes/dm.jsonl" in text and op.ANSWER in text, text
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


def test_a_pass_with_no_pinned_dm_record_keeps_its_projects(tmp_path, monkeypatch, capsys):
    """Blocking 2 (#2949): a DM start refuses to bind with no pinned record, and that refusal never takes the pass
    down: the project still starts, and one log line names the missing record."""
    op = operators(tmp_path, monkeypatch)
    hub(tmp_path, monkeypatch, op, ["acme/widget"])
    registry(tmp_path, [("acme/widget", "C0AAAAAA1", "proj-widget")])
    scenario(tmp_path, [])
    (tmp_path / "state" / "operators" / "sessions" / "dm.json").unlink()    # the owner's DM was never pinned

    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo")) == 0

    assert list(record(tmp_path)) == ["acme-widget"], record(tmp_path)
    assert "pinned DM channel" in capsys.readouterr().err


def test_the_record_is_written_after_each_start(tmp_path, monkeypatch):
    """The record is written after each start, atomically, so a crash loses no earlier start."""
    op = operators(tmp_path, monkeypatch)
    hub(tmp_path, monkeypatch, op, ["acme/widget"])
    registry(tmp_path, [("acme/widget", "C0AAAAAA1", "proj-widget")])    # two sessions: dm and the project
    scenario(tmp_path, [])
    writes = []
    real = op.write_json

    def write_json(path, value):
        if str(path).endswith("operators.json"):     # the config root's own files are written too (#2963)
            writes.append(dict(value))
        return real(path, value)
    monkeypatch.setattr(op, "write_json", write_json)
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
  printf 'prefix=%s\\n' "${PREFIX-unset}"
  printf 'termux=%s\\n' "${TERMUX_VERSION-unset}"
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


def test_the_profile_reaches_only_the_projects_own_service(tmp_path, monkeypatch):
    """#2943: a session reaches the CLI background service of its own project. The owner's shared root, which serves
    every unsandboxed session, stays outside the profile."""
    op = operators(tmp_path, monkeypatch)
    service = os.path.realpath(tmp_path / "state" / "operators" / "services" / "acme-widget")
    text = op.profile_text(["/state/outboxes/C1.jsonl"], "/state/inboxes", ["/state/inboxes/C1.jsonl"],
                           config=str(tmp_path / "config"), service=service)
    write = next(line for line in text.splitlines() if line.startswith("(allow file-write*"))
    assert f'(subpath "{service}")' in write, write
    assert f'(allow network-outbound (remote unix-socket (subpath "{service}")))' in text.splitlines(), text
    assert op.daemon_root() not in text, "the owner's shared service is outside the profile (#2943)"


def test_a_started_session_gets_its_own_service_root(tmp_path, monkeypatch):
    """#2943: the start moves the CLI's background service under the project's own root and records that root, so the
    profile the session runs under grants that root and no other."""
    op = wrapped(tmp_path, monkeypatch)
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo")) == 0
    state = os.path.realpath(tmp_path / "state")
    entry = record(tmp_path)["dm"]
    service = os.path.join(state, "operators", "services", "dm")
    assert entry["service"] == service, entry
    log = wrap_log(tmp_path)
    assert f"prefix={service}" in log and "termux=1" in log, log
    profile = Path(entry["profile"]["path"]).read_text()
    assert f'(subpath "{service}")' in profile, profile
    assert op.daemon_root() not in profile, profile


def test_a_stop_reaches_the_projects_own_service(tmp_path, monkeypatch):
    """#2943: the stop carries the project's own service root, the one its session started under. The roster read is
    the CLI's own and names no root, as the real CLI lists every session whatever root it is given (measured)."""
    op = operators(tmp_path, monkeypatch)
    found = hub(tmp_path, monkeypatch, op, ["acme/widget"])
    registry(tmp_path, [("acme/widget", "C0AAAAAA1", "proj-widget")])
    revision(tmp_path, "acme/widget", ["C0AAAAAA1"])
    scenario(tmp_path, [])
    state = str(tmp_path / "state")
    assert op.run_pass(state, str(tmp_path / "repo"), only="acme/widget") == 0
    service = os.path.realpath(os.path.join(state, "operators", "services", "acme-widget"))
    held = record(tmp_path)["acme-widget"]
    assert held["service"] == service, held
    scenario(tmp_path, [session(found["acme/widget"], "acme-widget", held["id"], started=1000),
                        session(found["acme/widget"], "acme-widget", "bbbb0002-0000-0000-0000-000000000002",
                                started=2000)])
    assert op.run_pass(state, str(tmp_path / "repo")) == 0
    stops = [row for row in envs(tmp_path) if row[2].startswith("stop")]
    assert stops, envs(tmp_path)
    assert all(row[0] == "1" and row[1] == service for row in stops), stops
    listings = [row for row in envs(tmp_path) if row[2].startswith("agents")]
    assert listings and all(row[0] == "unset" and row[1] == "unset" for row in listings), listings


def test_a_stop_falls_back_to_the_cli_own_service_for_a_session_an_older_build_started(tmp_path, monkeypatch):
    """#2943: a session an older build started lives under the owner's shared service. The stop names the project's own
    root first, and falls back to the CLI's own service, so the restart still reaches that session."""
    op = operators(tmp_path, monkeypatch)
    seen = []

    def fake_call(argv, cwd=None, timeout=op.DEADLINE, env=None):
        seen.append(env)
        if env is not None:
            raise op.Refused("stop exited 1: No session matching 'aaaaaaaa'")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(op, "call", fake_call)
    op.stop("aaaaaaaa", service=str(tmp_path / "service"))
    assert len(seen) == 2, seen
    assert seen[0]["PREFIX"] == str(tmp_path / "service") and seen[0]["TERMUX_VERSION"] == "1"
    assert seen[1] is None


def test_a_stop_that_fails_under_the_projects_own_service_is_never_retried_on_the_shared_one(tmp_path, monkeypatch):
    """#2943: only a session its own service never held may fall back. Any other refusal is the real answer, and a
    retry against the shared service could stop a foreign session whose id collides, or hide the failure."""
    op = operators(tmp_path, monkeypatch)
    seen = []

    def fake_call(argv, cwd=None, timeout=op.DEADLINE, env=None):
        seen.append(env)
        raise op.Refused("stop exited 1: the service is not running")

    monkeypatch.setattr(op, "call", fake_call)
    with pytest.raises(op.Refused):
        op.stop("aaaaaaaa", service=str(tmp_path / "service"))
    assert len(seen) == 1, seen


def test_a_stop_that_both_services_refuse_is_named(tmp_path, monkeypatch):
    """A stop no service confirms raises Refused, so the pass keeps ownership and retries later."""
    op = operators(tmp_path, monkeypatch)

    def fake_call(argv, cwd=None, timeout=op.DEADLINE, env=None):
        raise op.Refused("stop exited 1: No session matching 'aaaaaaaa'")

    monkeypatch.setattr(op, "call", fake_call)
    with pytest.raises(op.Refused):
        op.stop("aaaaaaaa", service=str(tmp_path / "service"))


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
    state.mkdir(exist_ok=True)
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

    assert "--persona hal" in text, text


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


def transcript(tmp_path, cwd, entries, mtime, sid="session"):
    """Write one session's transcript at the folder the CLI derives from the project checkout, named by its full id."""
    folder = tmp_path / "projects" / re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd))
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sid}.jsonl"
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
    transcript(tmp_path, found["acme/widget"], [API_ERROR], mtime=time.time() - 60, sid=live["sessionId"])
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
        "id": "m0", "model": "claude-sonnet-5-5", "usage": {"input_tokens": 1, "output_tokens": 1}}}], mtime=now - 3600,
        sid=live["sessionId"])
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
        "id": "m0", "model": "claude-sonnet-5-5", "usage": {"input_tokens": 1, "output_tokens": 1}}}], mtime=now - 600,
        sid=live["sessionId"])
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
    transcript(tmp_path, found["acme/widget"], [API_ERROR], mtime=now - 60, sid=live["sessionId"])
    inbox(tmp_path, "C0AAAAAA1", mtime=now - 600)
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo")) == 0
    err = capsys.readouterr().err
    assert "acme-widget" in err and "answer" in err, f"no alert named the project: {err!r}"
    assert err.count("alert") == 1, f"the restarted project raised {err.count('alert')} alerts: {err!r}"
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo")) == 0
    assert capsys.readouterr().err.count("alert") == 0, "the same unanswered line alerted twice"


def real_row(checkout, key, short, started=1000, pid=None):
    """A row in the shape the real CLI lists: `id` is the short id that `claude --bg` prints and `claude stop` takes,
    and `sessionId` is the full id that starts with it."""
    return {"id": short, "sessionId": f"{short}-0000-4000-8000-000000000001", "pid": pid or abs(hash(short)) % 60000,
            "startedAt": started, "name": f"SQUAD-OPERATOR-{key}", "cwd": str(checkout), "kind": "background"}


def test_a_session_the_record_names_by_its_short_id_is_kept(tmp_path, monkeypatch):
    """The record holds the short id `claude --bg` printed. A pass that finds the real CLI's row under that id keeps the
    session: it never counts it missing and never starts a second one."""
    op = operators(tmp_path, monkeypatch)
    found = hub(tmp_path, monkeypatch, op, ["acme/widget"])
    registry(tmp_path, [("acme/widget", "C0AAAAAA1", "proj-widget")])
    live = real_row(found["acme/widget"], "acme-widget", "cccccccc", started=int(time.time()))
    (tmp_path / "state" / "operators.json").write_text(json.dumps(
        {"acme-widget": {"id": "cccccccc", "pid": live["pid"], "started": live["startedAt"]}}))
    scenario(tmp_path, [live])
    for _ in range(3):
        assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo"), only="acme/widget") == 0
    assert not started(tmp_path), f"a live project was started again: {started(tmp_path)}"
    held = json.loads((tmp_path / "state" / "operators.json").read_text())["acme-widget"]
    assert held["id"] == "cccccccc" and not held.get("missing"), held


def test_the_newer_of_two_sessions_is_stopped_by_its_short_id(tmp_path, monkeypatch):
    """`claude stop` matches the short id alone; a full id answers "No session matching". The stop names the short id."""
    op = operators(tmp_path, monkeypatch)
    found = hub(tmp_path, monkeypatch, op, ["acme/widget"])
    registry(tmp_path, [("acme/widget", "C0AAAAAA1", "proj-widget")])
    old = real_row(found["acme/widget"], "acme-widget", "aaaaaaaa", started=100)
    new = real_row(found["acme/widget"], "acme-widget", "bbbbbbbb", started=200)
    scenario(tmp_path, [old, new])
    op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo"), only="acme/widget")
    stops = [c for c in calls(tmp_path) if c.startswith("stop ")]
    assert stops == ["stop bbbbbbbb"], stops


# ---------------------------------------------------------------- the review of the branch (#2925)

def test_stopping_the_newer_duplicate_keeps_the_older_sessions_record(tmp_path, monkeypatch):
    """The stop of the newer session of two leaves the record of the kept one: its id, its profile and its proxy."""
    import subprocess
    op = operators(tmp_path, monkeypatch)
    found = hub(tmp_path, monkeypatch, op, ["acme/widget"])
    registry(tmp_path, [("acme/widget", "C0AAAAAA1", "proj-widget")])
    old = session(found["acme/widget"], "acme-widget", "eeeeeeee-0000-0000-0000-00000000001a", started=100)
    new = session(found["acme/widget"], "acme-widget", "eeeeeeee-0000-0000-0000-00000000001b", started=200)
    proxy = subprocess.Popen(["sleep", "30"])
    try:
        held = {"id": old["sessionId"], "pid": old["pid"], "started": 100, "checkout": found["acme/widget"],
                "profile": {"path": "/p.sb", "sha256": "0" * 64}, "proxy": {"pid": proxy.pid, "port": 1}}
        (tmp_path / "state" / "operators.json").write_text(json.dumps({"acme-widget": held}))
        scenario(tmp_path, [new, old])
        assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo")) == 0
        entry = record(tmp_path).get("acme-widget") or {}
        assert entry.get("id") == old["sessionId"], entry
        assert entry.get("profile") == held["profile"] and entry.get("proxy") == held["proxy"], entry
        assert proxy.poll() is None, "the kept session's model proxy was killed"
    finally:
        proxy.kill()
        proxy.wait()


def test_one_ensure_replaces_a_recorded_session_that_died(tmp_path, monkeypatch):
    """A message for a project whose recorded session is gone starts a new one in that one pass, not at the second."""
    op = operators(tmp_path, monkeypatch)
    hub(tmp_path, monkeypatch, op, ["acme/widget"])
    registry(tmp_path, [("acme/widget", "C0AAAAAA1", "proj-widget")])
    (tmp_path / "state" / "operators.json").write_text(json.dumps(
        {"acme-widget": {"id": "dddddddd-0000-0000-0000-00000000000d", "pid": 9, "started": 1, "missing": 0}}))
    scenario(tmp_path, [])
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo"), only="acme/widget") == 0
    assert len(started(tmp_path)) == 1, "the message waited for a second pass"


def test_an_ensure_waits_for_a_pass_that_holds_the_lock(tmp_path, monkeypatch):
    """An ensure that meets a pass in flight waits for it, so the message never waits for the launchd pass. A full
    pass still stands aside."""
    import fcntl
    import threading
    op = operators(tmp_path, monkeypatch)
    hub(tmp_path, monkeypatch, op, ["acme/widget"])
    registry(tmp_path, [("acme/widget", "C0AAAAAA1", "proj-widget")])
    scenario(tmp_path, [])
    monkeypatch.setattr(op, "LOCK_WAIT", 0.05)
    fd = os.open(str(tmp_path / "state" / "operators.lock"), os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo")) == 0
        assert not started(tmp_path), "a full pass ran beside the holder"
        timer = threading.Timer(0.3, lambda: fcntl.flock(fd, fcntl.LOCK_UN))
        timer.start()
        assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo"), only="acme/widget") == 0
        timer.join()
        assert len(started(tmp_path)) == 1, "the ensure stood aside and the message waits"
    finally:
        os.close(fd)


def test_the_stall_check_reads_the_sessions_own_transcript(tmp_path, monkeypatch):
    """The owner's own session in the same checkout writes beside the operator's. Its API error restarts nothing, and its
    turns hide no stalled operator: the check reads the transcript the session id names."""
    op = operators(tmp_path, monkeypatch)
    found = hub(tmp_path, monkeypatch, op, ["acme/widget"])
    registry(tmp_path, [("acme/widget", "C0AAAAAA1", "proj-widget")])
    now = time.time()
    live = session(found["acme/widget"], "acme-widget", "abababab-0000-0000-0000-0000000000a1", started=1)
    (tmp_path / "state" / "operators.json").write_text(json.dumps(
        {"acme-widget": {"id": live["sessionId"], "pid": live["pid"], "started": 1}}))
    scenario(tmp_path, [live])
    ok = {"type": "assistant", "message": {"id": "m0", "model": "claude-sonnet-5-5",
                                           "usage": {"input_tokens": 1, "output_tokens": 1}}}
    transcript(tmp_path, found["acme/widget"], [ok], mtime=now - 60, sid=live["sessionId"])
    transcript(tmp_path, found["acme/widget"], [API_ERROR], mtime=now - 5, sid="owner-own-session")
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo")) == 0
    assert not [c for c in calls(tmp_path) if c.startswith("stop")], "the owner's error restarted the operator"
    transcript(tmp_path, found["acme/widget"], [ok], mtime=now - 3600, sid=live["sessionId"])
    inbox(tmp_path, "C0AAAAAA1", mtime=now - 900)
    transcript(tmp_path, found["acme/widget"], [ok], mtime=now - 1, sid="owner-own-session")
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo")) == 0
    assert f"stop {live['sessionId']}" in calls(tmp_path), "the owner's turn hid a stalled operator"


def test_the_profile_refuses_writes_to_the_owners_claude_configuration(tmp_path, monkeypatch):
    """A session moved by a message never writes a file that the owner's next, unsandboxed session loads."""
    op = operators(tmp_path, monkeypatch)
    home = os.path.realpath(os.path.expanduser("~"))
    text = op.profile_text(["/state/outboxes/C1.jsonl"], "/state/inboxes", ["/state/inboxes/C1.jsonl"],
                           config=str(tmp_path / "config"))
    allow = text.index(f'(subpath "{home}/.claude")')
    for path in ("settings.json", "settings.local.json", "CLAUDE.md", "keybindings.json"):
        rule = f'(deny file-write* (literal "{home}/.claude/{path}"))'
        assert rule in text and text.index(rule) > allow, (path, text)
    for folder in ("skills", "agents", "commands", "hooks", "plugins", "output-styles"):
        rule = f'(subpath "{home}/.claude/{folder}")'
        assert rule in text and text.index(rule) > allow, (folder, text)
    assert "/memory" in text[allow:], "a project memory folder stays writable"


def test_a_project_session_never_writes_to_the_dm_outbox(tmp_path, monkeypatch):
    """The bound refuses the DM outbox to a project session, so its brief never asks for that write. The tool gate
    allows no append and no command but the answer helper, so the brief answers an action request read-only (#2939)."""
    op = operators(tmp_path, monkeypatch)
    found = hub(tmp_path, monkeypatch, op, ["acme/widget"])
    unit = {"key": "acme-widget", "repo": "acme/widget", "checkout": found["acme/widget"], "channels": ["C0AAAAAA1"]}
    text = op.prompt_for(unit, str(tmp_path / "state"), "/repo")
    assert "outboxes/dm.jsonl" not in text and "Proposal:" not in text, text
    assert "never acts" in text and "only reads and answers" in text, text


def test_a_short_id_of_letters_alone_is_read_as_the_session_id(tmp_path, monkeypatch):
    """`claude --bg` prints an 8-hex short id; one with no digit is still the id."""
    op = operators(tmp_path, monkeypatch)
    hub(tmp_path, monkeypatch, op, ["acme/widget"])
    registry(tmp_path, [("acme/widget", "C0AAAAAA1", "proj-widget")])
    scenario(tmp_path, [])
    monkeypatch.setenv("FAKE_NEW", "abcdefab")
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo"), only="acme/widget") == 0
    assert record(tmp_path)["acme-widget"]["id"] == "abcdefab", record(tmp_path)


# #2963: the CLI's runtime state belongs to the operator, never to the owner's home. The owner's global config file holds
# the MCP servers the owner's next unsandboxed session starts, so no rule may grant a write to it. The CLI takes its
# config home from CLAUDE_CONFIG_DIR, and an empty CLAUDE_SECURESTORAGE_CONFIG_DIR keeps the owner's keychain service
# name, so the login reads from the owner's keychain with no path change and no credential moves (measured on 2.1.332).

def test_the_profile_refuses_the_owner_global_config_file_and_grants_the_cli_its_own_root(tmp_path, monkeypatch):
    """The one write grant names the operator's own config root, never the owner's global config file."""
    op = operators(tmp_path, monkeypatch)
    home = os.path.realpath(os.path.expanduser("~"))
    root = op.config_root(str(tmp_path / "state"))
    text = op.profile_text(["/state/outboxes/C1.jsonl"], "/state/inboxes", ["/state/inboxes/C1.jsonl"], config=root)
    write = next(line for line in text.splitlines() if line.startswith("(allow file-write*"))
    assert f'(subpath "{root}")' in write, write
    assert f'(literal "{home}/.claude.json")' not in write, "no grant may let the session write the owner's global config file"
    assert f'(deny file-write* (literal "{home}/.claude.json"))' in text, "the owner's global config file is refused by name"


def test_the_operator_config_root_reads_the_owner_keychain_by_its_default_name(tmp_path, monkeypatch):
    """An empty secure-storage dir keeps the default keychain service name: the login stays the owner's, and no
    credential is carried into the sandbox."""
    op = operators(tmp_path, monkeypatch)
    env = op.cli_env(str(tmp_path / "state"))
    assert env["CLAUDE_CONFIG_DIR"] == op.config_root(str(tmp_path / "state"))
    assert env["CLAUDE_SECURESTORAGE_CONFIG_DIR"] == "", env


def test_a_started_session_keeps_its_state_in_the_operator_config_root(tmp_path, monkeypatch):
    """The CLI a start builds runs under the operator's own config root, and the start prepares that root first."""
    op = operators(tmp_path, monkeypatch)
    seen = {}

    def fake_call(argv, cwd=None, timeout=None, env=None):
        seen["env"] = env
        return subprocess.CompletedProcess(argv, 0, "aaaaaaaa-0000-0000-0000-000000000001\n", "")

    monkeypatch.setattr(op, "call", fake_call)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://localhost:8016")
    (tmp_path / "home").mkdir()
    pinned_dm_record(tmp_path / "state", "D0AAAAAA1")
    op.start("dm", str(tmp_path / "state"), str(tmp_path / "repo"))
    root = op.config_root(str(tmp_path / "state"))
    assert seen["env"]["CLAUDE_CONFIG_DIR"] == root, seen["env"]
    assert seen["env"]["CLAUDE_SECURESTORAGE_CONFIG_DIR"] == "", seen["env"]
    assert os.path.isdir(root), "the root is prepared before the CLI starts"


def test_the_cli_temp_folder_sits_inside_the_granted_config_root(tmp_path, monkeypatch):
    """The CLI writes its per-session temp folder and a cwd file after each Bash call under its temp root (#2982). The
    default root is `/tmp`, which the profile refuses, so every Bash call failed. The env names a root inside the
    operator's config root, which the profile grants, and never a folder that other sessions share."""
    op = operators(tmp_path, monkeypatch)
    state = str(tmp_path / "state")
    root = op.config_root(state)
    temp = op.cli_env(state)["CLAUDE_CODE_TMPDIR"]
    assert os.path.commonpath([temp, root]) == root, temp
    text = op.profile_text(["/state/outboxes/C1.jsonl"], "/state/inboxes", ["/state/inboxes/C1.jsonl"], config=root)
    write = next(line for line in text.splitlines() if line.startswith("(allow file-write*"))
    assert f'(subpath "{root}")' in write, write


def test_a_started_session_runs_with_its_temp_root_prepared(tmp_path, monkeypatch):
    """The start prepares the temp root before the CLI starts, so the first Bash call finds it (#2982)."""
    op = operators(tmp_path, monkeypatch)
    seen = {}

    def fake_call(argv, cwd=None, timeout=None, env=None):
        seen["env"] = env
        return subprocess.CompletedProcess(argv, 0, "aaaaaaaa-0000-0000-0000-000000000001\n", "")

    monkeypatch.setattr(op, "call", fake_call)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://localhost:8016")
    (tmp_path / "home").mkdir()
    pinned_dm_record(tmp_path / "state", "D0AAAAAA1")
    op.start("dm", str(tmp_path / "state"), str(tmp_path / "repo"))
    temp = seen["env"]["CLAUDE_CODE_TMPDIR"]
    assert os.path.isdir(temp), "the temp root is prepared before the CLI starts"


def test_the_operator_root_links_the_owner_user_files_a_session_loads(tmp_path, monkeypatch):
    """The session still loads the owner's user settings, memory, skills and transcripts: the root links each of them,
    and the global config file stays the session's own."""
    op = operators(tmp_path, monkeypatch)
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    for name in ("settings.json", "CLAUDE.md"):
        (home / ".claude" / name).write_text("{}")
    for name in ("skills", "projects"):
        (home / ".claude" / name).mkdir()
    monkeypatch.setenv("HOME", str(home))
    root = op.prepare_config(str(tmp_path / "state"))
    for name in ("settings.json", "CLAUDE.md", "skills", "projects"):
        link = os.path.join(root, name)
        assert os.path.islink(link), f"the operator root has no link for {name}: {os.listdir(root)}"
        assert os.path.realpath(link) == os.path.realpath(home / ".claude" / name), link
    assert not os.path.exists(os.path.join(root, ".claude.json")), "the global config file belongs to the session"


def test_the_operator_root_carries_only_the_trust_the_owner_recorded(tmp_path, monkeypatch):
    """`claude --bg` refuses a folder with no trust record, and the operator root is not the owner's file. The root
    copies the boolean the owner's config holds for that folder, and nothing else of that file moves."""
    op = operators(tmp_path, monkeypatch)
    home = tmp_path / "home"
    home.mkdir()
    trusted = os.path.realpath(tmp_path / "checkouts" / "trusted")
    untrusted = os.path.realpath(tmp_path / "checkouts" / "untrusted")
    for path in (trusted, untrusted):
        os.makedirs(path, exist_ok=True)
    (home / ".claude.json").write_text(json.dumps({
        "projects": {trusted: {"hasTrustDialogAccepted": True}, untrusted: {"hasTrustDialogAccepted": False}},
        "oauthAccount": {"emailAddress": "nobody@example.com"}}))
    monkeypatch.setenv("HOME", str(home))
    op.seed_trust(str(tmp_path / "state"), [trusted, untrusted])
    path = Path(op.config_root(str(tmp_path / "state"))) / ".claude.json"
    data = json.loads(path.read_text())
    assert data["projects"][trusted]["hasTrustDialogAccepted"] is True, data
    assert untrusted not in data["projects"], "a folder the owner never trusted is not trusted here"
    assert "oauthAccount" not in data, "nothing but the trust boolean moves into the operator's config"


def test_the_pass_reads_the_operator_roster_and_the_owner_roster(tmp_path, monkeypatch):
    """A session started before the root moved, or by hand, lives in the owner's roster. The pass still sees it, so it
    never starts a second session for the same project."""
    op = operators(tmp_path, monkeypatch)
    found = hub(tmp_path, monkeypatch, op, ["acme/widget"])
    registry(tmp_path, [("acme/widget", "C0AAAAAA1", "proj-widget")])
    scenario(tmp_path, [])
    live = session(found["acme/widget"], "acme-widget", "abababab-0000-0000-0000-0000000000b1", started=1)
    (tmp_path / "state" / "operators.json").write_text(json.dumps(
        {"dm": {"id": "dddddddd-0000-0000-0000-000000000001", "pid": 9, "started": 1}}))
    roots = []

    def fake_call(argv, cwd=None, timeout=None, env=None):
        assert argv[1:3] == ["agents", "--json"], argv
        roots.append(None if env is None else env.get("CLAUDE_CONFIG_DIR"))
        rows = [live] if env is None else []
        return subprocess.CompletedProcess(argv, 0, json.dumps(rows), "")

    monkeypatch.setattr(op, "call", fake_call)
    assert op.run_pass(str(tmp_path / "state"), str(tmp_path / "repo")) == 0
    assert roots == [op.config_root(str(tmp_path / "state")), None], "both rosters are read, the operator's first"
    assert record(tmp_path)["acme-widget"]["adopt"] == 1, record(tmp_path)


def test_a_stop_the_operator_root_refuses_is_retried_on_the_owner_roster(tmp_path, monkeypatch):
    """A session of the owner's roster carries a token of an earlier build: the stop reaches it after the operator's own
    roster refuses the id."""
    op = operators(tmp_path, monkeypatch)
    tried = []

    def fake_call(argv, cwd=None, timeout=None, env=None):
        tried.append(None if env is None else env.get("CLAUDE_CONFIG_DIR"))
        if env is not None:
            raise op.Refused("stop exited 1: No session matching 'abababab'")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(op, "call", fake_call)
    op.stop("abababab", str(tmp_path / "state"))
    assert tried == [op.config_root(str(tmp_path / "state")), None], tried


def test_a_corrupt_dm_line_is_skipped_with_one_log_line(tmp_path, monkeypatch, capsys):
    """#2949 criterion (2): `dm_channel` reads the inbox tail and reports one line for a corrupt line it skips, so a
    broken queue stays visible instead of silently dropping the DM channel."""
    op = operators(tmp_path, monkeypatch)
    state = tmp_path / "state"
    (state / "operators" / "sessions" / "dm.json").unlink()     # a state with no pinned record yet
    (state / "inboxes").mkdir(parents=True, exist_ok=True)
    (state / "inboxes" / "dm.jsonl").write_text('{"channel": "D0AAAAAA1"}\nnot json\n')

    assert op.dm_channel(str(state)) == "D0AAAAAA1"

    err = capsys.readouterr().err
    assert "dm.jsonl" in err and "corrupt" in err, err
    assert err.count("operators: skipped") == 1, err


def test_a_contradicting_dm_line_never_changes_the_pinned_channel(tmp_path, monkeypatch):
    """Debt 2 (#2949): the supervisor's own record pins the DM channel, so a newer inbox line that names another
    channel never moves the session's binding. `dm_channel` reads the record first and logs the fallback it took."""
    op = operators(tmp_path, monkeypatch)
    state = tmp_path / "state"
    (state / "operators" / "sessions" / "dm.json").write_text(json.dumps({"dm": "D0AAAAAA1"}))
    (state / "inboxes").mkdir(parents=True)
    (state / "inboxes" / "dm.jsonl").write_text(json.dumps({"channel": "D0ZZZZZZ"}) + "\n")

    assert op.dm_channel(str(state)) == "D0AAAAAA1"


def test_dm_channel_pinned_reads_no_inbox_line(tmp_path, monkeypatch):
    """Debt 2 and 6 (#2949): with no pinned record, a caller that needs an authoritative channel gets no answer, never
    the channel of an inbox line that anything may have written."""
    op = operators(tmp_path, monkeypatch)
    state = tmp_path / "state"
    (state / "operators" / "sessions" / "dm.json").unlink()     # a state with no pinned record yet
    (state / "inboxes").mkdir(parents=True)
    (state / "inboxes" / "dm.jsonl").write_text(json.dumps({"channel": "D0AAAAAA1"}) + "\n")

    assert op.dm_channel(str(state), pinned=True) == ""
    assert op.dm_channel(str(state)) == "D0AAAAAA1"


# The brief's helper line, per unit (#2949 review): the DM answers `dm`, a project answers the line's channel, and a
# channel persona's id rides inline. The DM binding always names the dm scope, so the helper's own binding check passes.

def persona_revision(tmp_path, repo, channel, persona_id):
    """One project's active Slack revision, naming a persona on one of its channels (#2935)."""
    state = tmp_path / "state"
    (state / "slack").mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(repo.encode()).hexdigest()[:16]
    doc = {"workspaces": [{"id": "T0TEAM0001", "channels": [{"id": channel, "name": "proj", "persona": persona_id}]}],
           "personas": [{"id": persona_id, "name": "HAL9K", "voice": "Charon", "tone": "calm"}]}
    (state / "slack" / f"{digest}.json").write_text(json.dumps({"repo": repo, "sha": "a" * 40, "revision": doc}))


def answer_module():
    """The shipped helper module, loaded from the tree under test."""
    spec = importlib.util.spec_from_file_location("answer", VOICE / "answer.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_dm_answer_line_names_the_dm_scope(tmp_path, monkeypatch):
    """The DM session's helper call answers its own scope, never a placeholder channel it does not have."""
    op = operators(tmp_path, monkeypatch)
    line = op.answer_lines(str(tmp_path / "state"),
                           {"key": "dm", "repo": "/repo", "checkout": "/repo", "channels": ["dm"]})

    assert f"{op.ANSWER} --scope dm" in line, line
    assert "the inbox line's channel" not in line, line


def test_the_answer_line_names_the_channel_persona_inline(tmp_path, monkeypatch):
    """A channel with a persona answers under it, so the brief's one helper line names the id (#2935)."""
    op = operators(tmp_path, monkeypatch)
    registry(tmp_path, [("acme/widget", "C0AAAAAA1", "proj-widget")])
    persona_revision(tmp_path, "acme/widget", "C0AAAAAA1", "hal")
    unit = {"key": "acme-widget", "repo": "acme/widget", "checkout": "/repo", "channels": ["C0AAAAAA1"]}

    line = op.answer_lines(str(tmp_path / "state"), unit)

    assert "--persona hal" in line, line


def test_the_answer_line_names_no_persona_for_a_channel_that_declares_none(tmp_path, monkeypatch):
    """A channel with no persona passes no `--persona`, so the daemon keeps the channel's own."""
    op = operators(tmp_path, monkeypatch)
    registry(tmp_path, [("acme/widget", "C0AAAAAA1", "proj-widget")])
    unit = {"key": "acme-widget", "repo": "acme/widget", "checkout": "/repo", "channels": ["C0AAAAAA1"]}

    assert "--persona" not in op.answer_lines(str(tmp_path / "state"), unit)


def test_the_answer_line_quotes_the_text_as_a_single_quoted_literal(tmp_path, monkeypatch):
    """Debt 3 (#2949): the answer text is an unescaped shell template, so a quote, a backtick or `$(...)` in an answer
    breaks the command the session runs. The line quotes it as a literal instead."""
    op = operators(tmp_path, monkeypatch)
    unit = {"key": "acme-widget", "repo": "acme/widget", "checkout": "/repo", "channels": ["C0AAAAAA1"]}

    line = op.answer_lines(str(tmp_path / "state"), unit)

    assert "--text '<the answer>'" in line, line
    assert '--text "<the answer>"' not in line, line
    assert "--thread '<the inbox line" in line, line


def test_the_brief_names_the_quoting_rule_for_the_answer_text(tmp_path, monkeypatch):
    """Debt 3 (#2949): the brief tells the session to pass the answer as a quoted literal, so an owner message with a
    quote never becomes extra shell."""
    op = operators(tmp_path, monkeypatch)
    unit = {"key": "acme-widget", "repo": "acme/widget", "checkout": "/repo", "channels": ["C0AAAAAA1"]}

    brief = op.prompt_for(unit, str(tmp_path / "state"), "/repo")

    assert "single-quoted literal" in brief, brief


def test_a_dm_binding_names_the_dm_scope_and_the_helper_accepts_it(tmp_path, monkeypatch):
    """The DM unit's channels may be empty; the binding still names `dm`, and the helper's own read accepts it, so a DM
    answer never exits 2 on a malformed binding."""
    op = operators(tmp_path, monkeypatch)
    state = str(tmp_path / "state")
    pinned_dm_record(Path(state), "D0AAAAAA1")

    path = op.binding(state, {"key": "dm", "repo": "/repo", "checkout": "/repo", "channels": []})

    data = json.loads(Path(path).read_text())
    assert data["channels"] == ["dm"], data
    got = answer_module().read_binding({"VOICE_ANSWER_BINDING": path})
    assert got["channels"] == ["dm"], got
    assert op.outbox_path(state, "dm") == os.path.join(state, "outboxes", "dm.jsonl")


# The pinned-DM record path (#2949 review, Debt 2): one helper names `<state>/operators/sessions/dm.json`, and the
# supervisor, the reader and the refusal all use it.

def test_the_pinned_dm_record_path_is_one_helper(tmp_path, monkeypatch):
    """Debt 2 (#2949): `dm_record_path` is the one name of the pinned record, and the refusal that names a missing
    record names that very file."""
    op = operators(tmp_path, monkeypatch)
    state = str(tmp_path / "fresh")
    assert op.dm_record_path(state) == os.path.join(state, "operators", "sessions", "dm.json")

    unit = {"key": "dm", "repo": "", "checkout": str(tmp_path), "channels": []}
    with pytest.raises(op.Refused) as caught:
        op.binding(state, unit)
    assert op.dm_record_path(state) in str(caught.value), str(caught.value)


# The persona read (#2949 review, Debt 3): a read or parse error keeps no persona and names the state and the scope;
# any other error is a real defect and is never swallowed.

def test_a_broken_slack_revision_names_the_state_and_scope(tmp_path, monkeypatch, capsys):
    """Debt 3 (#2949): a persona read that fails on a parse error returns no persona and prints one stderr line that
    names the state and the scope, as `dm_channel` names a skipped corrupt line."""
    op = operators(tmp_path, monkeypatch)
    state = str(tmp_path / "state")

    def broken(state_dir, scope):
        raise ValueError("bad revision")

    monkeypatch.setattr(op.voiced, "scope_identity", broken)

    assert op.scope_persona_id(state, "C0AAAAAA1") == ""
    err = capsys.readouterr().err
    assert "C0AAAAAA1" in err and state in err, err


def test_a_persona_defect_that_is_not_a_read_is_never_swallowed(tmp_path, monkeypatch):
    """Debt 3 (#2949): only a read or a parse error keeps the default; a defect of another kind propagates."""
    op = operators(tmp_path, monkeypatch)
    state = str(tmp_path / "state")

    def broken(state_dir, scope):
        raise RuntimeError("a defect")

    monkeypatch.setattr(op.voiced, "scope_identity", broken)

    with pytest.raises(RuntimeError):
        op.scope_persona_id(state, "C0AAAAAA1")
