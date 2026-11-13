"""Guard: issue #1619. One bounded probe with the real `claude` CLI proves the operator session's tool set and its kernel
bound. A fake CLI never stands in for the permission boundary, so the denial cases run the real sandbox-exec profile.
"""
import importlib.util
import os
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
VOICE = ROOT / "contrib" / "voice"
PROBE_SCRIPT = VOICE / "operator_probe.py"


def load(name):
    spec = importlib.util.spec_from_file_location(name, VOICE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_operator_tool_set_is_read_monitor_taskstop_bash():
    operators = load("operators")
    assert operators.TOOLS.split(",") == ["Read", "Monitor", "TaskStop", "Bash"]


def test_a_session_starts_with_its_tool_set_restricted(tmp_path, monkeypatch):
    operators = load("operators")
    argvs = []

    def fake_call(argv, cwd=None, timeout=None):
        argvs.append(argv)
        return subprocess.CompletedProcess(argv, 0, "aaaaaaaa-0000-0000-0000-000000000001\n", "")

    monkeypatch.setattr(operators, "call", fake_call)
    operators.start("dm", str(tmp_path / "state"), str(tmp_path / "repo"))
    argv = argvs[0]
    assert argv[argv.index("--tools") + 1] == operators.TOOLS
    assert argv[argv.index("--allowedTools") + 1] == operators.TOOLS


def collect_probe(path_dir):
    env = {"PATH": str(path_dir), "HOME": str(Path.home()), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    return subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:deepeval",
                           "tests/test_operator_probe.py"], cwd=ROOT, env=env, capture_output=True, text=True,
                          check=False)


@pytest.mark.heavy
def test_the_real_cli_probe_is_deselected_when_the_cli_is_absent(tmp_path):
    done = collect_probe(tmp_path)
    assert "deselected: tests/test_operator_probe.py::test_the_real_cli_probe_passes" in done.stdout
    assert "skipped" not in done.stdout


def test_every_probe_call_runs_through_the_sandbox_wrapper(tmp_path, monkeypatch):
    probe = load("operator_probe")
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["env"] = kwargs["env"]
        return subprocess.CompletedProcess(argv, 0, "control\n", "")

    monkeypatch.setattr(probe.subprocess, "run", fake_run)
    probe.sandboxed("(version 1)(allow default)", "/bin/sh", ["-c", "echo control"], tmp_path, 5)
    assert seen["argv"][0] == probe.WRAPPER
    assert seen["env"]["SQUAD_SIGNAL_PROFILE"] == "(version 1)(allow default)"
    assert seen["env"]["SQUAD_CLAUDE_CLI"] == "/bin/sh"


def test_the_default_work_root_is_outside_every_writable_root():
    probe = load("operator_probe")
    work = probe.work_root()
    assert work.is_relative_to(Path.home())
    for temp in ("/private/var/folders", "/var/folders", "/private/tmp", "/tmp", tempfile.gettempdir()):
        assert not work.is_relative_to(Path(temp).resolve())


@pytest.mark.parametrize("denial,kwargs", [
    ("write outside the outbox", {"write_landed": True}),
    ("read of another scope's inbox", {"read_leaked": True}),
    ("a foreign host on port 443", {"network_reached": True}),
])
def test_a_denial_that_does_not_happen_fails_the_probe(denial, kwargs):
    probe = load("operator_probe")
    flags = {"write_landed": False, "read_leaked": False, "network_reached": False, **kwargs}
    with pytest.raises(probe.ProbeFailed, match=denial):
        probe.check_denials(**flags)


def test_the_profile_allows_network_out_to_loopback_only():
    probe = load("operator_probe")
    text = probe.profile_text("/work/state/outboxes", "/work/state/inboxes/proj-other.jsonl")
    assert '(allow network-outbound (remote ip "localhost:*"))' in text.splitlines()
    assert "*:443" not in text


def test_a_loopback_refusal_fails_the_probe():
    probe = load("operator_probe")
    refused = subprocess.CompletedProcess([], 1, "", "ConnectionRefusedError: [Errno 61] Connection refused")
    with pytest.raises(probe.ProbeFailed, match="loopback"):
        probe.check_loopback_open(refused)
    probe.check_loopback_open(subprocess.CompletedProcess([], 0, "", ""))


@pytest.mark.sandbox_bound
@pytest.mark.heavy     # a foreign connect the kernel lets through waits out its 2 s timeout
def test_the_kernel_refuses_a_foreign_host_on_443_and_passes_loopback(tmp_path):
    """The real profile, the real kernel: a foreign host on 443 must fail with the kernel's own refusal, and loopback must
    pass, since the model call reaches its host only through the loopback proxy."""
    probe = load("operator_probe")
    (tmp_path / "outbox").mkdir()
    profile = probe.profile_text(str(tmp_path / "outbox"), str(tmp_path / "inbox.jsonl"))
    listener = socket.create_server(("127.0.0.1", 0))
    try:
        def connect(host, port):
            return probe.sandboxed(profile, sys.executable, ["-c", probe.CONNECT, host, str(port)], tmp_path, 30)
        foreign = connect(probe.NETWORK_HOST, probe.NETWORK_DENIED)
        loopback = connect("127.0.0.1", listener.getsockname()[1])
    finally:
        listener.close()
    assert foreign.returncode != 0 and probe.DENIED in foreign.stderr, foreign.stderr
    assert loopback.returncode == 0, loopback.stderr


def echo_once():
    """A loopback server that echoes the bytes of one connection; returns its listening socket."""
    server = socket.create_server(("127.0.0.1", 0))

    def serve():
        conn, _ = server.accept()
        with conn:
            while data := conn.recv(4096):
                conn.sendall(data)
    threading.Thread(target=serve, daemon=True).start()
    return server


def test_the_model_proxy_relays_its_target_and_refuses_every_other(tmp_path):
    proxy_module = load("model_proxy")
    echo = echo_once()
    target = echo.getsockname()[1]
    log = tmp_path / "proxy.log"
    try:
        with proxy_module.ModelProxy(("127.0.0.1", target), log) as proxy:
            with socket.create_connection(("127.0.0.1", proxy.port), 5) as s:
                s.sendall(f"CONNECT 127.0.0.1:{target} HTTP/1.1\r\n\r\n".encode())
                reader = s.makefile("rb")
                assert reader.readline().startswith(b"HTTP/1.1 200")
                assert reader.readline() == b"\r\n"
                s.sendall(b"ping")
                assert s.recv(4) == b"ping"
            for refused in ("93.184.216.34:443", "127.0.0.1:80"):
                with socket.create_connection(("127.0.0.1", proxy.port), 5) as s:
                    s.sendall(f"CONNECT {refused} HTTP/1.1\r\n\r\n".encode())
                    assert s.makefile("rb").readline().startswith(b"HTTP/1.1 403")
            assert proxy.refusals == ["93.184.216.34:443", "127.0.0.1:80"]
    finally:
        echo.close()
    assert "refused CONNECT 93.184.216.34:443" in log.read_text()


def test_the_model_endpoint_is_read_from_the_base_url(monkeypatch):
    """The CLI may send its model calls to a loopback gateway instead of the API host, so the probe reads the endpoint it
    must keep open and must not assume the public one (#1656)."""
    probe = load("operator_probe")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8016")
    assert probe.model_endpoint() == ("127.0.0.1", 8016, True)
    monkeypatch.delenv("ANTHROPIC_BASE_URL")
    assert probe.model_endpoint() == ("api.anthropic.com", 443, False)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://model.example:8080")
    assert probe.model_endpoint() == ("model.example", 8080, False)


def test_a_loopback_endpoint_runs_the_session_with_no_proxy(tmp_path, monkeypatch):
    """A loopback gateway is reached under the profile's own loopback rule, so a proxy in front of it would prove nothing
    and break the call (#1656)."""
    probe = load("operator_probe")
    seen = {}
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8016")
    monkeypatch.setattr(probe, "denials", lambda *a: (False, False, False))
    monkeypatch.setattr(probe, "check_denials", lambda *a: None)

    def fake_session(work, profile, cli, proxy_url=None):
        seen["proxy_url"] = proxy_url
        return ({"tools": probe.TOOLS.split(","), "permissionMode": "dontAsk"}, {"subtype": "success", "is_error": False})

    monkeypatch.setattr(probe, "session", fake_session)
    probe.run_probe(tmp_path, "/bin/true")
    assert seen["proxy_url"] is None


def test_the_session_reaches_the_model_only_through_the_proxy(tmp_path, monkeypatch):
    probe = load("operator_probe")
    seen = {}

    def fake_run(argv, **kwargs):
        seen["env"] = kwargs["env"]
        out = '{"type": "system", "subtype": "init"}\n{"type": "result", "subtype": "success"}\n'
        return subprocess.CompletedProcess(argv, 0, out, "")

    monkeypatch.setattr(probe.subprocess, "run", fake_run)
    probe.session(tmp_path, "(version 1)(allow default)", "claude", "http://127.0.0.1:4242")
    assert seen["env"]["HTTPS_PROXY"] == "http://127.0.0.1:4242"
    assert seen["env"]["https_proxy"] == "http://127.0.0.1:4242"
    assert seen["env"]["NO_PROXY"] == ""


def test_a_session_with_a_write_tool_fails_the_probe():
    probe = load("operator_probe")
    init = {"tools": ["Read", "Write", "Monitor", "TaskStop", "Bash"], "permissionMode": "dontAsk"}
    with pytest.raises(probe.ProbeFailed, match="Write"):
        probe.check_session(init, {"subtype": "success", "is_error": False})


def test_the_session_is_bounded_in_time_and_spend():
    probe = load("operator_probe")
    assert probe.DEADLINE <= 300.0
    assert float(probe.SPEND) <= 1.0


def test_one_command_repeats_the_probe(tmp_path):
    probe = load("operator_probe")
    assert "usage: operator_probe.py" in probe.__doc__
    done = subprocess.run([sys.executable, str(PROBE_SCRIPT)], capture_output=True, text=True, check=False,
                          env={"PATH": str(tmp_path)})
    assert done.returncode == 1
    assert "no claude CLI on the path" in done.stderr


def test_the_profile_opens_no_write_root_beyond_the_outbox_and_the_claude_home():
    probe = load("operator_probe")
    text = probe.profile_text("/work/state/outboxes", "/work/state/inboxes/proj-other.jsonl")
    write = next(line for line in text.splitlines() if line.startswith("(allow file-write*"))
    for root in ("/private/tmp", "/private/var/folders", '"/dev"', '"/tmp"'):
        assert root not in write


@pytest.mark.sandbox_bound
def test_the_profile_refuses_a_write_to_the_temp_folder(tmp_path):
    probe = load("operator_probe")
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    inbox = tmp_path / "other-inbox.jsonl"
    inbox.write_text("")
    profile = probe.profile_text(str(outbox), str(inbox))
    leak = Path(tempfile.gettempdir()) / f"operator-probe-leak-{os.getpid()}.txt"
    try:
        done = probe.sandboxed(profile, "/bin/sh", ["-c", f'echo leaked > "{leak}"'], tmp_path, 30)
        landed = leak.exists()
    finally:
        leak.unlink(missing_ok=True)
    assert not landed
    assert "Operation not permitted" in done.stderr


@pytest.mark.claude_bound
@pytest.mark.heavy     # a live `claude -p` call takes seconds: over the 1 s line, so the fast battery deselects it (ADR-0139)
def test_the_real_cli_probe_passes():
    probe = load("operator_probe")
    assert probe.main([]) == 0
