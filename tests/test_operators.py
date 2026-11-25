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
    assert ".claude.json" not in write, "the owner's MCP configuration is never a write grant (#2942)"
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
    op.stop("aaaaaaaa", None, str(tmp_path / "service"))
    assert len(seen) == 2, seen
    assert seen[0]["PREFIX"] == str(tmp_path / "service") and seen[0]["TERMUX_VERSION"] == "1"
    assert seen[1] is None


def test_a_stop_that_both_services_refuse_is_named(tmp_path, monkeypatch):
    """A stop no service confirms raises Refused, so the pass keeps ownership and retries later."""
    op = operators(tmp_path, monkeypatch)

    def fake_call(argv, cwd=None, timeout=op.DEADLINE, env=None):
        raise op.Refused("stop exited 1: No session matching 'aaaaaaaa'")

    monkeypatch.setattr(op, "call", fake_call)
    with pytest.raises(op.Refused):
        op.stop("aaaaaaaa", None, str(tmp_path / "service"))


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


def test_the_cli_keeps_its_own_state_in_the_config_root_and_the_owner_global_config_file_is_refused(tmp_path, monkeypatch):
    """#2942, #2963: the CLI writes its runtime state under the operator's own config root, so the owner's global
    config file needs no write grant, and the profile refuses it. The config root holds the CLI's login too: auth
    moves with `CLAUDE_CONFIG_DIR`, and the empty secure-storage dir keeps the owner's keychain entry (measured,
    CLI 2.1.332)."""
    op = operators(tmp_path, monkeypatch)
    home = os.path.realpath(os.path.expanduser("~"))
    config = str(tmp_path / "config")
    text = op.profile_text(["/state/outboxes/C1.jsonl"], "/state/inboxes", ["/state/inboxes/C1.jsonl"],
                           config=config)
    write = next(line for line in text.splitlines() if line.startswith("(allow file-write*"))
    assert f'(subpath "{os.path.realpath(config)}")' in write, write
    allow = text.index(f'(subpath "{home}/.claude")')
    rule = f'(deny file-write* (literal "{home}/.claude.json"))'
    assert rule in text.splitlines(), text
    assert text.index(rule) > allow, "the deny must follow the allow: Seatbelt applies the last matching rule"


def test_a_project_session_never_writes_to_the_dm_outbox(tmp_path, monkeypatch):
    """The bound refuses the DM outbox to a project session, so its brief never asks for that write. It answers in the
    channel that this session does not run actions yet."""
    op = operators(tmp_path, monkeypatch)
    found = hub(tmp_path, monkeypatch, op, ["acme/widget"])
    unit = {"key": "acme-widget", "repo": "acme/widget", "checkout": found["acme/widget"], "channels": ["C0AAAAAA1"]}
    text = op.prompt_for(unit, str(tmp_path / "state"), "/repo")
    assert "outboxes/dm.jsonl" not in text and "Proposal:" not in text, text
    assert "never acts" in text and "only reads" in text, text


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
    assert f'(literal "{home}/.claude.json")' not in write, "the session may write the owner's global config file"
    # #2942: the file sits beside the Claude home, so no grant ever covered it; it is refused by its own deny.
    assert f'(deny file-write* (literal "{home}/.claude.json"))' in text.splitlines(), text


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
    op.start("dm", str(tmp_path / "state"), str(tmp_path / "repo"))
    root = op.config_root(str(tmp_path / "state"))
    assert seen["env"]["CLAUDE_CONFIG_DIR"] == root, seen["env"]
    assert seen["env"]["CLAUDE_SECURESTORAGE_CONFIG_DIR"] == "", seen["env"]
    assert os.path.isdir(root), "the root is prepared before the CLI starts"


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
            raise op.Refused("no such session")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(op, "call", fake_call)
    op.stop("abababab", str(tmp_path / "state"))
    assert tried == [op.config_root(str(tmp_path / "state")), None], tried
