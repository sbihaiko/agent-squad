"""The guest poller runner: one fresh `claude -p` session per due guest channel (SPEC 2682, #2830).

A fake connector stands for the claude.ai Slack connector, and a fake session runner stands for the sandboxed CLI. Every
id, `ts` and text here is a placeholder.
"""
import io
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest
import yaml

from squad import guestrunner as G
from squad import gueststate
from squad import slackpoller as P

CLIENT = "U0CLIENT01"
WS = "T0CLIENT01"
CH = "C0CLISUP01"
OTHER = "C0CLIGEN01"
NOW = datetime(2026, 11, 24, 12, 0, 0, tzinfo=UTC)
SECRET = "SECRET-GUEST-TEXT-2830"

COMMITTED = {
    "version": 1,
    "project": {"language": "en-US", "persona": "iara"},
    "personas": [{"id": "iara", "name": "Iara", "voice": "Sulafat", "tone": "Warm and patient."}],
    "roles": [{"id": "colleague", "actions": ["status"]}],
    "workspaces": [],
}

LOCAL = {
    "version": 1,
    "workspaces": [
        {"id": WS, "label": "client-a", "class": "guest", "persona": "iara",
         "channels": [
             {"id": CH, "name": "support", "mode": "listen+speak", "speak": "draft",
              "roles": ["colleague"],
              "poll": {"mode": "two_state", "fast": "1m", "slow": "10m", "quiet_after": "10m",
                       "thread_open": "30m"}},
             {"id": OTHER, "name": "general", "mode": "listen",
              "poll": {"mode": "exponential", "floor": "5m", "ceiling": "1h", "factor": 2,
                       "idle_window": "15m", "thread_open": "30m"}},
         ]},
    ],
}


class FakeConnector:
    """A read returns the messages of a channel from `oldest`. A post counts as a write call, so a test can prove none
    was made."""

    def __init__(self, messages):
        self.messages = list(messages)
        self.reads = 0
        self.writes = 0

    def read(self, channel=None, oldest=None, limit=100):
        self.reads += 1
        page = [m for m in self.messages if oldest is None or float(m["ts"]) >= float(oldest)]
        return {"ok": True, "messages": page[:limit]}

    def post(self, channel, text):
        self.writes += 1
        return {"ok": True}


def message(ts, text=SECRET, user=CLIENT):
    return {"ts": ts, "user": user, "text": text}


def fake_session(connector, log=None):
    """A session runner: one read through the connector, returned as the CLI's JSON result."""
    def run(argv, **kwargs):
        if log is not None:
            log.append((argv, kwargs))
        page = connector.read(channel=CH)
        stdout = json.dumps({"type": "result", "result": json.dumps({"messages": page["messages"]})})
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")
    return run


def make_docs(tmp_path, local=LOCAL):
    root = tmp_path / "repo"
    (root / "docs" / "squad").mkdir(parents=True)
    (root / "docs" / "squad" / "slack.yaml").write_text(yaml.safe_dump(COMMITTED))
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "slack.local.yaml").write_text(yaml.safe_dump(local))
    return root, profile


def make_runner(tmp_path, runner, shadow=True, log=None, local=LOCAL, deliver=None):
    root, profile = make_docs(tmp_path, local)
    state = tmp_path / "state"
    return G.GuestRunner(profile, root, state, shadow=shadow, runner=runner, log=(log or io.StringIO()).write,
                         deliver=deliver)


def deliver_to(connector):
    """The delivery path of a live runner: each delivered decision becomes one post through the connector."""
    def deliver(workspace, channel, message, decision):
        connector.post(channel, message.get("text"))
    return deliver


def prime(state, channel, at, local=LOCAL):
    """Poll one channel at `at`, so its next due time is one interval after `at`."""
    schedule = P.PollSchedule(state / gueststate.SCHEDULE_NAME, COMMITTED, local)
    schedule.polled(WS, channel, at)


def test_tick_starts_one_session_for_each_due_channel_only(tmp_path):
    launches = []
    runner = make_runner(tmp_path, fake_session(FakeConnector([]), launches))
    prime(runner.state_root, CH, NOW - timedelta(seconds=30))
    runner.tick(NOW)
    assert len(launches) == 1
    assert OTHER in "".join(launches[0][0])
    assert CH not in "".join(launches[0][0])


def test_channel_before_next_due_starts_no_session(tmp_path):
    launches = []
    runner = make_runner(tmp_path, fake_session(FakeConnector([]), launches))
    prime(runner.state_root, CH, NOW - timedelta(seconds=30))
    prime(runner.state_root, OTHER, NOW - timedelta(seconds=30))
    assert runner.tick(NOW) == []
    assert launches == []


def test_session_is_one_fresh_claude_print_call_under_the_sandbox(tmp_path):
    launches = []
    runner = make_runner(tmp_path, fake_session(FakeConnector([]), launches))
    runner.tick(NOW)
    argv = launches[0][0]
    assert argv[0] == G.WRAPPER
    assert argv[1] == "-p"
    assert "--no-session-persistence" in argv
    assert "--resume" not in argv and "--continue" not in argv
    settings = json.loads(argv[argv.index("--settings") + 1])
    pre = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    post = settings["hooks"]["PostToolUse"][0]["hooks"][0]["command"]
    assert "guestrunner" in pre and " pre" in pre
    assert "guestrunner" in post and " post " in post and str(runner.state_root) in post


def test_session_selects_the_workspace_by_team_id(tmp_path):
    launches = []
    runner = make_runner(tmp_path, fake_session(FakeConnector([]), launches))
    runner.tick(NOW)
    prompt = launches[0][0][2]
    assert WS in prompt


def test_shadow_tick_records_decisions_and_makes_no_write_call(tmp_path):
    connector = FakeConnector([message("1900000000.000100"), message("1900000001.000100")])
    log = io.StringIO()
    runner = make_runner(tmp_path, fake_session(connector), shadow=True, log=log, deliver=deliver_to(connector))
    prime(runner.state_root, OTHER, NOW - timedelta(seconds=30))
    runner.tick(NOW)
    assert connector.writes == 0
    shadow = P.ShadowLog(runner.state_root / P.SHADOW_NAME)
    assert len(shadow.entries(CH)) == 2
    assert "poll " + CH + " decisions=2" in log.getvalue()


def test_shadow_tick_never_calls_deliver_even_when_the_poll_hands_out_a_decision(tmp_path, monkeypatch):
    delivered = []
    runner = make_runner(tmp_path, fake_session(FakeConnector([])), shadow=True,
                         deliver=lambda *args: delivered.append(args))
    prime(runner.state_root, OTHER, NOW - timedelta(seconds=30))
    handed = [({"ts": "1900000000.000100", "text": SECRET}, object())]
    monkeypatch.setattr(G.P.Poller, "poll", lambda self, workspace, channel: handed)
    runner.tick(NOW)
    assert delivered == []


def test_live_tick_delivers_each_decision_through_the_connector(tmp_path):
    connector = FakeConnector([message("1900000000.000100"), message("1900000001.000100")])
    runner = make_runner(tmp_path, fake_session(connector), shadow=False, deliver=deliver_to(connector))
    prime(runner.state_root, OTHER, NOW - timedelta(seconds=30))
    runner.tick(NOW)
    assert connector.writes == 2


def test_log_line_per_poll_names_channel_and_count_never_text(tmp_path):
    connector = FakeConnector([message("1900000000.000100")])
    log = io.StringIO()
    runner = make_runner(tmp_path, fake_session(connector), log=log)
    prime(runner.state_root, OTHER, NOW - timedelta(seconds=30))
    runner.tick(NOW)
    lines = [line for line in log.getvalue().splitlines() if line.strip()]
    assert len(lines) == 1
    assert CH in lines[0] and "decisions=1" in lines[0]
    assert SECRET not in log.getvalue()


def test_session_past_deadline_is_killed_and_next_tick_polls_again(tmp_path):
    connector = FakeConnector([message("1900000000.000100")])
    calls = []
    good = fake_session(connector)

    def runner_fn(argv, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))
        return good(argv, **kwargs)

    log = io.StringIO()
    runner = make_runner(tmp_path, runner_fn, log=log)
    prime(runner.state_root, OTHER, NOW - timedelta(seconds=30))
    runner.tick(NOW)
    assert calls[0]["timeout"] == G.DEADLINE
    assert "killed" in log.getvalue()
    runner.tick(NOW + timedelta(seconds=60))
    assert len(calls) == 2
    assert "decisions=1" in log.getvalue()


def test_runner_ticks_once_a_minute(tmp_path):
    slept = []
    clock = iter([0.0, 5.0, 100.0, 103.0])
    runner = make_runner(tmp_path, fake_session(FakeConnector([])))
    runner.run(clock=lambda: next(clock), sleep=slept.append, ticks=2)
    assert G.TICK_SECONDS == 60
    assert slept == [55.0]


def test_read_hook_denies_a_write_and_checkpoint_hook_moves_the_cursor(tmp_path):
    checkpoint = tmp_path / "checkpoint.json"
    deny = io.StringIO()
    code = G.hook_main(["pre"], stdin=io.StringIO(json.dumps({"tool_name": "mcp__slack__slack_send_message"})),
                       stdout=deny, stderr=io.StringIO())
    assert code == 0
    assert "deny" in deny.getvalue()
    read = {"tool_name": "mcp__slack__slack_read_channel", "tool_input": {"channel_id": CH},
            "tool_response": {"messages": [message("1900000002.000100")]}}
    assert G.hook_main(["post", str(checkpoint)], stdin=io.StringIO(json.dumps(read)),
                       stdout=io.StringIO(), stderr=io.StringIO()) == 0
    assert P.Checkpoint(checkpoint).get(CH) == "1900000002.000100"


def test_start_and_stop_run_one_runner_per_profile(tmp_path):
    root, profile = make_docs(tmp_path, local={"version": 1, "workspaces": []})
    state = tmp_path / "state"
    pid = G.start(profile, root, state)
    try:
        assert pid > 0
        with pytest.raises(G.RunnerError):
            G.start(profile, root, state)
    finally:
        assert G.stop(profile, state) is True
    assert G.stop(profile, state) is False


def test_start_passes_its_state_folder_to_the_run_command(tmp_path, monkeypatch):
    root, profile = make_docs(tmp_path, local={"version": 1, "workspaces": []})
    state = tmp_path / "custom-state"
    launched = []

    class FakeProcess:
        pid = 4242

    def popen(command, **kwargs):
        launched.append(command)
        return FakeProcess()

    monkeypatch.setattr(G.subprocess, "Popen", popen)
    G.start(profile, root, state)
    command = launched[0]
    assert command[command.index("--state") + 1] == str(state)


def test_stop_keeps_the_pidfile_when_the_runner_ignores_sigterm(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "STOP_WAIT", 0.3)
    profile = tmp_path / "profile"
    profile.mkdir()
    state = tmp_path / "state"
    child = subprocess.Popen(
        [sys.executable, "-c",
         "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('ready', flush=True); time.sleep(60)"],
        stdout=subprocess.PIPE, text=True)
    child.stdout.readline()
    pidfile = G.pid_path(state, profile)
    pidfile.parent.mkdir(parents=True)
    pidfile.write_text(f"{child.pid}\n")
    try:
        with pytest.raises(G.RunnerError):
            G.stop(profile, state)
        assert pidfile.exists()
    finally:
        child.kill()
        child.wait()
