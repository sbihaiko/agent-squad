#!/usr/bin/env python3
"""Prove the operator session's tool set and kernel bound with the real `claude` CLI (#1619, #1656).

Two phases, one command. Every call runs through contrib/squad-goal/scripts/claude-sandboxed.sh with the profile this
probe writes. The denials come first: a write outside the scope's outbox, a read of another scope's inbox and a foreign
host on port 443 must each fail. Then one bounded `claude -p` call runs with the operator's tool set and `dontAsk`. Its
HTTPS goes only through a loopback proxy that relays the model host and refuses every other target. A denial that does
not happen, or a session that starts with another tool set, fails the probe.

Network bound: the profile allows network out to localhost only, so the kernel refuses every foreign host, port 443
included. How the call reaches the model follows the CLI's endpoint (`ANTHROPIC_BASE_URL`): a loopback gateway is reached
under that same loopback rule, and any other host is reached through contrib/voice/model_proxy.py, which relays that one
target and refuses every other CONNECT, recording each refusal. The probe proves the kernel refusal, the refusal the proxy
records, and a model call that succeeds.

usage: operator_probe.py
Exit 0: every denial held and the session started with the tool set. Exit 1: a denial did not hold, or the CLI failed.
"""
import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from model_proxy import ModelProxy  # noqa: E402
from operators import TOOLS, profile_text  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
WRAPPER = str(ROOT / "contrib" / "squad-goal" / "scripts" / "claude-sandboxed.sh")
MODEL = "haiku"
MODEL_HOST = "api.anthropic.com"
MODEL_PORT = 443
SPEND = "0.50"                         # USD, the one CLI call's cap
DEADLINE = 180.0                       # seconds, the one CLI call
DENIAL_DEADLINE = 30.0                 # seconds, one denial command
SENTINEL = "PROBE-OTHER-INBOX-LINE"
PROMPT = "Reply with the single word ok. Use no tool."
DENIED = "Operation not permitted"      # the kernel's own refusal, the only reason a denial counts
LOOPBACK = "127.0.0.1"
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
NETWORK_HOST = "93.184.216.34"          # a literal address: the rule is a port rule, never a name-resolution result
NETWORK_DENIED = 443                    # a foreign host on the port the model call uses: refused by the kernel
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
        raise ProbeFailed("a denial did not happen: a foreign host on port 443 was reached")


def check_loopback_open(loopback):
    """The control for the network denial: loopback must pass the kernel, or the proxy path is unproven."""
    if loopback.returncode != 0:
        raise ProbeFailed(f"the network rule refuses loopback, so the model proxy path is unproven: "
                          f"{loopback.stderr.strip() or 'no reason'}")


def check_proxy_refuses(proxy):
    """A foreign target fails at the proxy, and the proxy records it: a refusal the owner can read, not a silent one."""
    target = f"{NETWORK_HOST}:{NETWORK_DENIED}"
    with socket.create_connection((LOOPBACK, proxy.port), 5) as s:
        s.sendall(f"CONNECT {target} HTTP/1.1\r\n\r\n".encode())
        status = s.makefile("rb").readline().decode(errors="replace").strip()
    if not status.startswith("HTTP/1.1 403"):
        raise ProbeFailed(f"the proxy answered a foreign target with {status!r}, not a refusal")
    if target not in proxy.refusals:
        raise ProbeFailed("the proxy refused a foreign target and did not record it")


def sandboxed(profile, cli, args, cwd, timeout, extra_env=None):
    """One call through the sandbox wrapper: the profile text, the program it wraps and its arguments."""
    env = {**os.environ, "SQUAD_SIGNAL_PROFILE": profile, "SQUAD_CLAUDE_CLI": cli, **(extra_env or {})}
    try:
        return subprocess.run([WRAPPER, *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout,
                              check=False)
    except subprocess.TimeoutExpired:
        raise ProbeFailed(f"a sandboxed call passed its {timeout:.0f}s deadline") from None


def denials(work, profile, leak, other_inbox, proxy_port):
    """Run each denial under the profile, with no model in the loop, and return what got through.

    A denial counts only when the profile applied (the control echo ran) and the command refused with its own
    `Operation not permitted`; a failure of any other kind is unproven, so the probe fails rather than passes.
    The loopback control is the proxy's port: the model call needs it open, so a refused loopback proves nothing.
    """
    def run(command):
        return sandboxed(profile, "/bin/sh", ["-c", command], work, DENIAL_DEADLINE)

    def connect(host, port):
        return sandboxed(profile, sys.executable, ["-c", CONNECT, host, str(port)], work, DENIAL_DEADLINE)

    control = run("echo control")
    if control.returncode != 0 or "control" not in control.stdout:
        raise ProbeFailed(f"sandbox-exec did not apply the profile: {control.stderr.strip() or 'no output'}")
    write = run(f'echo leaked > "{leak}"')
    read = run(f'cat "{other_inbox}"')
    network = connect(NETWORK_HOST, NETWORK_DENIED)
    for name, res in (("write", write), ("read", read), ("network", network)):
        if res.returncode != 0 and DENIED not in res.stderr:
            raise ProbeFailed(f"the {name} denial is unproven: {res.stderr.strip() or 'no reason'}")
    check_loopback_open(connect(LOOPBACK, proxy_port))
    return leak.exists(), SENTINEL in read.stdout, network.returncode == 0


def model_endpoint(env=None):
    """The host, the port and whether it is loopback, for the endpoint the CLI sends its model calls to.

    The CLI takes its endpoint from `ANTHROPIC_BASE_URL`, which may name a loopback gateway, an HTTP host or nothing at
    all. A loopback endpoint is reached under the profile's own loopback rule, so it needs no proxy; any other endpoint is
    a foreign host the profile refuses, so it is reached through the proxy.
    """
    base = (env or os.environ).get("ANTHROPIC_BASE_URL") or f"https://{MODEL_HOST}"
    parts = urllib.parse.urlsplit(base)
    host = parts.hostname or MODEL_HOST
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return host, port, host in LOOPBACK_HOSTS


def session(work, profile, cli, proxy_url=None):
    """One bounded `claude -p` call under the profile, through the proxy when one is given; return its events."""
    args = ["-p", PROMPT, "--tools", TOOLS, "--allowedTools", TOOLS, "--permission-mode", "dontAsk",
            "--strict-mcp-config", "--output-format", "stream-json", "--verbose", "--max-budget-usd", SPEND,
            "--model", MODEL]
    proxy_env = {"HTTPS_PROXY": proxy_url, "https_proxy": proxy_url, "NO_PROXY": "", "no_proxy": ""} if proxy_url else {}
    done = sandboxed(profile, cli, args, work, DEADLINE, proxy_env)
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
    profile = profile_text(str(outbox), str(other_inbox.parent), str(other_inbox.parent / "dm.jsonl"))
    host, port, loopback = model_endpoint()
    if loopback:
        check_denials(*denials(work, profile, leak, other_inbox, port))
        init, result = session(work, profile, cli)
        check_session(init, result)
        return 0
    with ModelProxy((host, port), work / "proxy.log") as proxy:
        check_denials(*denials(work, profile, leak, other_inbox, proxy.port))
        check_proxy_refuses(proxy)
        init, result = session(work, profile, cli, proxy.url)
        check_session(init, result)
    return len(proxy.refusals)


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
        refused = run_probe(work, cli)
    except ProbeFailed as e:
        print(f"operator-probe: fail: {e}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(work, ignore_errors=True)
    host, port, loopback = model_endpoint()
    route = f"the loopback endpoint {host}:{port} carried the call" if loopback else \
        f"the proxy refused {refused} target(s) and recorded them"
    print(f"operator-probe: pass: denials held (write, read, foreign host on port {NETWORK_DENIED} refused, loopback "
          f"open); {route}; session tools {TOOLS}, dontAsk, cap ${SPEND}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
