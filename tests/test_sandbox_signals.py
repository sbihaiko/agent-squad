"""A child session never signals the loop: no pkill by parent 1, no kill of every process, no stop of a launchd job (#1468).

A child ran `time bash -c 'for i in $(seq 10); do pkill -P 1; done'` to time pkill. The loop supervisor's parent is pid 1
(watchd starts it in a new session), so the supervisor died and the loop stayed down for three hours. The wall is a deny
list on the Bash command text: it reads every segment, inside quoted `-c` strings and loop bodies too, and lets a kill
of the session's own processes through.
"""
import pytest

from squad import sandbox

ROOT = "/tmp/wt/child"


def reason(cmd):
    return sandbox.deny_reason("Bash", {"command": cmd}, ROOT, env={}, home="/Users/x")


@pytest.mark.parametrize("cmd", [
    "pkill -P 1",
    "pkill -P1",
    "pkill --parent 1",
    "pkill --parent=1",
    "/usr/bin/pkill -P 1",
    "pkill -TERM -P 1 >/dev/null 2>&1",
    "time bash -c 'for i in $(seq 10); do pkill -P 1 >/dev/null 2>&1; done'",
    "sh -c \"pkill -P 1\"",
    "for i in 1 2; do pkill -P 1; done",
    "killall bash",
    "/usr/bin/killall -9 python3",
    "kill -1",
    "kill -TERM -1",
    "kill -9 -- -1",
    "kill -s TERM -1",
    "kill 1",
    "kill -s TERM 1",
    "kill -9 1",
    "pkill -f watchd.sh",
    "pkill -f 'watchd.sh run'",
    "pkill -f /Users/x/.local/scripts/watch.sh",
    "pkill fastloop.sh",
    "pkill -f cqflush",
    "kill $(cat state/watchd.pid)",
    "pgrep -f watchd | xargs kill",
    "pgrep -f sleep | xargs kill -9",
    "ps ax | awk '{print $1}' | xargs pkill",
    "launchctl bootout gui/501/com.squad-goal.health.agent-squad",
    "launchctl unload ~/Library/LaunchAgents/x.plist",
    "launchctl stop com.squad-goal.health.agent-squad",
    "launchctl kill TERM gui/501/x",
    "sudo pkill -P 1",
    "env FOO=1 pkill -P 1",
])
def test_a_signal_outside_the_session_is_denied(cmd):
    r = reason(cmd)
    assert r is not None and r.startswith("sandbox: "), cmd
    assert "signal" in r or "launchd" in r, r


@pytest.mark.parametrize("cmd", [
    "kill $!",
    "kill %1",
    "kill -TERM -- -$$",
    "kill -TERM -$pgid",
    "kill 12345",
    "kill -9 $pid 2>/dev/null; wait $pid",
    "sleep 30 & sp=$!; kill $sp",
    "pkill -f github-mcp-probe",
    "pkill -f 'sleep 2718'",
    "pkill -f \"$PWD/scripts/test-gate.sh\"",
    "kill %watchdog-test",
    "pgrep -f watchd",
    "ps -p 1 -o command=",
    "launchctl list | grep squad",
    "echo done; git status",
    "python3 -m pytest -q tests/test_sandbox.py",
    "grep -n hub dashboard/hub.py",
])
def test_a_signal_inside_the_session_or_no_signal_passes(cmd):
    assert reason(cmd) is None, cmd


def test_the_denial_names_the_rule():
    assert "parent 1" in reason("pkill -P 1")
    assert "killall" in reason("killall bash")
    assert "every process" in reason("kill -9 -1")
    assert "watchd" in reason("pkill -f watchd.sh")
    assert "xargs" in reason("pgrep -f x | xargs kill")
