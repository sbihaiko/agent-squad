#!/usr/bin/env python3
"""Prove the operator session's tool set and kernel bound with the real `claude` CLI (#1619).

Two phases, one command. Every call runs through contrib/squad-goal/scripts/claude-sandboxed.sh with the profile this
probe writes. The denials come first: a write outside the scope's outbox, a read of another scope's inbox and a network
call on a port the model call does not use must each fail. Then one bounded `claude -p` call runs with the operator's
tool set and `dontAsk`. A denial that does not happen, or a session that starts with another tool set, fails the probe.

Network bound: proven at port level only. The profile allows outbound TCP port 443 to every host, and loopback. It denies
port 80 to foreign hosts. Seatbelt accepts only `*` or `localhost` as a network host, so the profile cannot name the model
endpoint. UNPROVEN: host-level isolation. An operator session can reach any host on port 443. This probe does not claim
otherwise. The stronger bound, a loopback proxy that allows only the model host, is a follow-up.

usage: operator_probe.py
Exit 0: every denial held and the session started with the tool set. Exit 1: a denial did not hold, or the CLI failed.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from operators import TOOLS  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
WRAPPER = str(ROOT / "contrib" / "squad-goal" / "scripts" / "claude-sandboxed.sh")
MODEL = "haiku"
SPEND = "0.50"                         # USD, the one CLI call's cap
DEADLINE = 180.0                       # seconds, the one CLI call
DENIAL_DEADLINE = 30.0                 # seconds, one denial command
SENTINEL = "PROBE-OTHER-INBOX-LINE"
PROMPT = "Reply with the single word ok. Use no tool."
DENIED = "Operation not permitted"      # the kernel's own refusal, the only reason a denial counts
NETWORK_HOST = "93.184.216.34"          # a literal address: the rule is a port rule, never a name-resolution result
NETWORK_DENIED = 80                     # a port the profile denies to a foreign host
NETWORK_OPEN = 443                      # the port the model call uses, open to every host
CONNECT = "import socket, sys; socket.create_connection((sys.argv[1], int(sys.argv[2])), 2)"


class ProbeFailed(Exception):
    """A denial did not hold, or the session did not start with the tool set."""


def work_root():
    """The folder each probe run works in: under the home directory, outside the profile's writable roots."""
    return Path.home() / ".local" / "state" / "agent-squad" / "operator-probe"


def check_denials(write_landed, read_leaked, network_reached):
    if write_landed:
        raise ProbeFailed("a denial did not happen: write outside the outbox landed")
    if read_leaked:
        raise ProbeFailed("a denial did not happen: read of another scope's inbox returned its lines")
    if network_reached:
        raise ProbeFailed("a denial did not happen: a port the network rule denies was reached")


def check_port_bound(open_port):
    """The control for the network denial: the port the model call uses must pass the kernel, or the rule bounds nothing.

    A refusal of port 443 is unproven as well as useless, since a session that cannot open it cannot reach the model.
    A timeout or a refused connection is the network's answer and passes: the kernel let the connect through.
    """
    if open_port.returncode != 0 and DENIED in open_port.stderr:
        raise ProbeFailed(f"the network rule refuses port {NETWORK_OPEN}, so no port bound is proven")


def profile_text(outbox_dir, other_inbox):
    home = os.path.realpath(os.path.expanduser("~"))
    writable = [outbox_dir, os.path.join(home, ".claude")]
    subpaths = " ".join(f'(subpath "{p}")' for p in writable)
    return "\n".join([
        "(version 1)",
        "(allow default)",
        "(deny file-write*)",
        f"(allow file-write* {subpaths} (literal \"{home}/.claude.json\"))",
        f'(deny file-read* (subpath "{other_inbox}"))',
        "(deny network-outbound)",
        # Seatbelt accepts only `*` or `localhost` as a host, so 443 stays open to every host.
        '(allow network-outbound (remote ip "localhost:*") (remote tcp "*:443") (remote udp "*:53"))',
    ]) + "\n"


def sandboxed(profile, cli, args, cwd, timeout):
    """One call through the sandbox wrapper: the profile text, the program it wraps and its arguments."""
    env = {**os.environ, "SQUAD_SIGNAL_PROFILE": profile, "SQUAD_CLAUDE_CLI": cli}
    try:
        return subprocess.run([WRAPPER, *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout,
                              check=False)
    except subprocess.TimeoutExpired:
        raise ProbeFailed(f"a sandboxed call passed its {timeout:.0f}s deadline") from None


def denials(work, profile, leak, other_inbox):
    """Run each denial under the profile, with no model in the loop, and return what got through.

    A denial counts only when the profile applied (the control echo ran) and the command refused with its own
    `Operation not permitted`; a failure of any other kind is unproven, so the probe fails rather than passes.
    The network denial is a port rule, so it is measured as a pair on one literal address: port 80 refused, port 443
    open. The open port is the control: without it a refused port 80 says nothing about the bound.
    """
    def run(command):
        return sandboxed(profile, "/bin/sh", ["-c", command], work, DENIAL_DEADLINE)

    def connect(port):
        return sandboxed(profile, sys.executable, ["-c", CONNECT, NETWORK_HOST, str(port)], work, DENIAL_DEADLINE)

    control = run("echo control")
    if control.returncode != 0 or "control" not in control.stdout:
        raise ProbeFailed(f"sandbox-exec did not apply the profile: {control.stderr.strip() or 'no output'}")
    write = run(f'echo leaked > "{leak}"')
    read = run(f'cat "{other_inbox}"')
    network = connect(NETWORK_DENIED)
    for name, res in (("write", write), ("read", read), ("network", network)):
        if res.returncode != 0 and DENIED not in res.stderr:
            raise ProbeFailed(f"the {name} denial is unproven: {res.stderr.strip() or 'no reason'}")
    check_port_bound(connect(NETWORK_OPEN))
    return leak.exists(), SENTINEL in read.stdout, network.returncode == 0


def session(work, profile, cli):
    """One bounded `claude -p` call under the profile; return its init event and its result event."""
    args = ["-p", PROMPT, "--tools", TOOLS, "--allowedTools", TOOLS, "--permission-mode", "dontAsk",
            "--strict-mcp-config", "--output-format", "stream-json", "--verbose", "--max-budget-usd", SPEND,
            "--model", MODEL]
    done = sandboxed(profile, cli, args, work, DEADLINE)
    events = []
    for line in done.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    init = next((e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), None)
    result = next((e for e in events if e.get("type") == "result"), None)
    if init is None or result is None:
        reason = (done.stderr or done.stdout).strip().splitlines()
        raise ProbeFailed(f"the CLI call ended with no init or result event (exit {done.returncode}): "
                          f"{reason[-1] if reason else 'no output'}")
    return init, result


def check_session(init, result):
    builtin = sorted(t for t in init.get("tools", []) if not t.startswith("mcp__"))
    if builtin != sorted(TOOLS.split(",")):
        raise ProbeFailed(f"the session started with tools {builtin}, not {TOOLS}")
    if init.get("permissionMode") != "dontAsk":
        raise ProbeFailed(f"the session started in {init.get('permissionMode')}, not dontAsk")
    if result.get("is_error") or result.get("subtype") != "success":
        raise ProbeFailed(f"the CLI call did not succeed: {result.get('subtype')}")


def run_probe(work, cli):
    outbox = work / "state" / "outboxes" / "dm.jsonl"
    other_inbox = work / "state" / "inboxes" / "proj-other.jsonl"
    leak = work / "elsewhere" / "leak.txt"
    for folder in (outbox.parent, other_inbox.parent, leak.parent):
        folder.mkdir(parents=True, exist_ok=True)
    outbox.write_text("")
    other_inbox.write_text(SENTINEL + "\n")
    profile = profile_text(str(outbox.parent), str(other_inbox))
    check_denials(*denials(work, profile, leak, other_inbox))
    init, result = session(work, profile, cli)
    check_session(init, result)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.parse_args(argv)
    cli = shutil.which("claude")
    if cli is None:
        print("operator-probe: no claude CLI on the path", file=sys.stderr)
        return 1
    work_root().mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="run-", dir=work_root())).resolve()
    try:
        run_probe(work, cli)
    except ProbeFailed as e:
        print(f"operator-probe: fail: {e}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(f"operator-probe: pass: denials held (write, read, port {NETWORK_DENIED} refused, "
          f"port {NETWORK_OPEN} open); session tools {TOOLS}, dontAsk, cap ${SPEND}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
