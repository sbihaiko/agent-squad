"""The hosted terminal, end to end: hubmux, the product's start path, brings up each profile's hub and its terminal
supervisor, and the test drives the routes a page drives. Real processes and a real pty. The hosted session runs a `!`
line as a shell command, so no model call is made and nothing is spent. A temporary HOME keeps the owner's session
registry out of the read, and a temporary socket folder keeps the daemon's socket off /tmp/squad-sup-<uid>.
"""
import http.client
import json
import os
import pathlib
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

import pytest

from squad import supervisor as sv

ROOT = pathlib.Path(__file__).resolve().parent.parent
HUBMUX = ROOT / "contrib/squad-goal/scripts/hubmux.py"
PREFIX = "/alpha"
SID = "e2e-terminal-1"
OTHER = "e2e-terminal-2"
CID = "client-e2e-0001"
MARKER = "e2e-marker-7f3a"
START_DEADLINE = 60.0
READ_DEADLINE = 30.0
ATTACH = {"sid": SID, "client_id": CID}


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def request(port: int, method: str, path: str, body: dict | None = None) -> tuple[int, str]:
    """One loopback request as the page sends it: the hub's own origin and a JSON body on a write."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Host": f"127.0.0.1:{port}"}
    payload = None
    if method == "POST":
        headers["Origin"] = f"http://127.0.0.1:{port}"
    if body is not None:
        payload = json.dumps(body)
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body=payload, headers=headers)
    reply = conn.getresponse()
    text = reply.read().decode()
    conn.close()
    return reply.status, text


def read_path(sid: str, offset: int = 0) -> str:
    return f"{PREFIX}/api/terminals/read?sid={sid}&client_id={CID}&offset={offset}"


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def stop_hub(hub: SimpleNamespace) -> None:
    """Kill the hosted session, the daemon and the hub, then wait for every process and the socket folder to be gone."""
    state, pids = hub.state, set()
    if state.is_dir():
        if sv._answers(state):
            try:
                sv.call(state, "stop", session=SID)
            except (OSError, ValueError, sv.Refused):
                pass
        ledger = state / "sessions.jsonl"
        if ledger.exists():
            for line in ledger.read_text().splitlines():
                event = json.loads(line)
                if event.get("event") == "start" and isinstance(event.get("pid"), int):
                    pids.add(event["pid"])
                    try:
                        os.killpg(event["pid"], signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        pass
        pid_file = state / sv.PIDFILE
        if pid_file.exists():
            try:
                pids.add(int(pid_file.read_text()))
                os.kill(int(pid_file.read_text()), signal.SIGKILL)
            except (ProcessLookupError, ValueError):
                pass
    hub.proc.terminate()
    try:
        hub.proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        hub.proc.kill()
        hub.proc.wait(timeout=10)
    hub.log.close()
    deadline = time.monotonic() + 10
    while any(alive(pid) for pid in pids):
        assert time.monotonic() < deadline, f"processes survived the test: {[pid for pid in pids if alive(pid)]}"
        time.sleep(0.05)


@pytest.fixture
def hub_up(tmp_path, monkeypatch):
    """Start hubmux as hubctl.sh does: one profile, its own temp checkout, state, HOME and supervisor socket folder."""
    sockets = pathlib.Path(tempfile.mkdtemp(prefix="sq1161-", dir="/tmp"))
    monkeypatch.setenv("SQUAD_SUPERVISOR_DIR", str(sockets))
    started = []

    def start(absent: bool = False) -> SimpleNamespace:
        profiles, checkout, base = tmp_path / "profiles", tmp_path / "checkout", tmp_path / "state"
        for folder in (profiles, checkout, base):
            folder.mkdir(exist_ok=True)
        (profiles / "alpha.sh").write_text(
            f': "${{SG_REPO:=x/alpha}}"; : "${{SG_OWNER:=x}}"; : "${{SG_PROJECT:=3}}"; : "${{SG_CHECKOUT:={checkout}}}"\n')
        index = len(started)
        home = tmp_path / "home" / str(index)
        home.mkdir(parents=True)
        port = free_port()
        env = {k: v for k, v in os.environ.items() if not k.startswith("SG_")}
        env.update(HOME=str(home), SG_PROFILES_DIR=str(profiles), SG_STATE_BASE=str(base), SG_HUB_DIR=str(tmp_path / "hub"))
        if absent:
            env["SQUAD_SUPERVISOR_CMD"] = f"{tmp_path}/no-supervisor {{state}}"   # a start that refuses at once, with its reason
        log = (tmp_path / f"hub-{index}.log").open("w")
        proc = subprocess.Popen([sys.executable, str(HUBMUX), str(port)], env=env, stdout=log, stderr=log,
                                stdin=subprocess.DEVNULL, start_new_session=True)
        hub = SimpleNamespace(proc=proc, state=base / "alpha", checkout=checkout, log=log)
        started.append(hub)
        deadline = time.monotonic() + START_DEADLINE
        while True:
            assert proc.poll() is None, f"the hub exited at start: {(tmp_path / f'hub-{index}.log').read_text()[-800:]}"
            assert time.monotonic() < deadline, "the hub did not answer /api/hub"
            try:
                if '"hub": "squad-hub"' in request(port, "GET", "/api/hub")[1]:
                    break
            except OSError:
                pass
            time.sleep(0.2)
        return SimpleNamespace(port=port, state=hub.state, checkout=checkout, log=log)

    yield start
    for hub in started:
        stop_hub(hub)
    shutil.rmtree(sockets, ignore_errors=True)


def wait_up(hub: SimpleNamespace) -> None:
    """The supervisor is up once a read reaches it: an unstarted session is refused as unknown, never as an absent daemon."""
    deadline = time.monotonic() + START_DEADLINE
    while True:
        try:
            status, body = request(hub.port, "GET", read_path(SID))
            if status == 403 and "not started by this daemon" in json.loads(body)["err"]:
                return
        except OSError:
            pass
        assert time.monotonic() < deadline, f"the supervisor never came up: {hub.log.name}"
        time.sleep(0.2)


def wait_refused(hub: SimpleNamespace) -> str:
    """A read of an unstarted session answers the supervisor's own refusal while the daemon is down."""
    deadline = time.monotonic() + START_DEADLINE
    while True:
        try:
            status, body = request(hub.port, "GET", read_path(SID))
            if status == 503:
                return json.loads(body)["err"]
        except OSError:
            pass
        assert time.monotonic() < deadline, f"the supervisor never refused: {hub.log.name}"
        time.sleep(0.2)


@pytest.mark.heavy
@pytest.mark.sdk_bound
@pytest.mark.slow(reason="starts hubmux, a real supervisor daemon and a hosted session")
def test_a_hosted_session_echoes_a_line_sent_through_the_hub(hub_up):
    hub = hub_up()
    wait_up(hub)   # hubmux's start path brought the terminal supervisor up

    sv.call(hub.state, "start", job="e2e-terminal", cwd=str(hub.checkout), session=SID)
    status, _ = request(hub.port, "GET", read_path(SID))
    assert status == 200

    assert request(hub.port, "POST", PREFIX + "/api/terminals/attach", ATTACH)[0] == 200
    sent = {"sid": SID, "text": f"!echo {MARKER}\n", "client_id": CID}
    assert request(hub.port, "POST", PREFIX + "/api/terminals/write", sent)[0] == 200

    deadline = time.monotonic() + READ_DEADLINE
    offset, scrollback = 0, ""
    while MARKER not in scrollback:
        assert time.monotonic() < deadline, f"the echoed line did not reach the scrollback: {scrollback[-300:]!r}"
        status, body = request(hub.port, "GET", read_path(SID, offset))
        assert status == 200, body
        chunk = json.loads(body)
        scrollback += chunk["data"]
        offset = chunk["offset"]
        time.sleep(0.1)

    # The read answered the session it attached to: the marker typed into SID came back from SID, and another session is refused.
    status, body = request(hub.port, "GET", read_path(OTHER))
    assert status == 403
    assert "not started by this daemon" in json.loads(body)["err"]

    assert request(hub.port, "POST", PREFIX + "/api/terminals/detach", ATTACH)[0] == 200
    status, body = request(hub.port, "POST", PREFIX + "/api/terminals/detach", ATTACH)
    assert status == 403
    assert "not attached" in json.loads(body)["err"]


@pytest.mark.heavy
@pytest.mark.slow(reason="starts hubmux and a refused supervisor start")
def test_with_the_supervisor_absent_a_read_answers_one_refusal(hub_up):
    hub = hub_up(absent=True)
    refusal = wait_refused(hub)
    assert refusal.startswith("the terminal supervisor is not running: ")
    assert "No such file or directory" in refusal
    assert "\n" not in refusal, "the refusal is one line"

    status, body = request(hub.port, "GET", read_path(SID))
    assert status == 503
    assert json.loads(body)["err"] == refusal, "a read answered a fresh refusal instead of the one reason"

