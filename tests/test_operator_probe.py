"""Guard: issue #1619. One bounded probe with the real `claude` CLI proves the operator session's tool set and its kernel
bound. A fake CLI never stands in for the permission boundary, so the denial cases run the real sandbox-exec profile.
"""
import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest

from tests.proxystop import track_starts

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
    track_starts(operators)
    argvs = []

    def fake_call(argv, cwd=None, timeout=None, env=None):
        argvs.append(argv)
        return subprocess.CompletedProcess(argv, 0, "aaaaaaaa-0000-0000-0000-000000000001\n", "")

    monkeypatch.setattr(operators, "call", fake_call)
    (tmp_path / "repo").mkdir()      # a session runs in a checkout that exists; a missing one refuses the start
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


def test_two_probe_runs_work_in_one_fixed_session_folder(tmp_path, monkeypatch):
    """#2775: the CLI remembers a trust prompt by folder path, so every run must work in the same folder and that folder
    must stay after the run. A fresh folder per run made the owner's accepted prompt hold for no run."""
    probe = load("operator_probe")
    monkeypatch.setattr(probe, "work_root", lambda: tmp_path / "operator-probe")
    monkeypatch.setattr(probe.shutil, "which", lambda name: "/bin/true")
    works = []

    def fake_probe(work, cli):
        works.append(work)
        return 0

    monkeypatch.setattr(probe, "run_probe", fake_probe)
    assert probe.main([]) == 0
    assert probe.main([]) == 0
    assert works[0] == works[1]
    assert works[0] == probe.session_dir()
    assert works[0].is_relative_to(tmp_path / "operator-probe")
    assert works[0].is_dir()


def test_a_leak_marker_of_an_earlier_run_never_counts_as_a_denial(tmp_path, monkeypatch):
    """#2775: the session folder is fixed, so a marker an earlier run left behind must never read as a write that landed."""
    probe = load("operator_probe")
    leak = tmp_path / "elsewhere" / "leak.txt"
    leak.parent.mkdir()
    leak.write_text("leaked\n")
    seen = {}

    def fake_denials(work, profile, leak_path, other_inbox, port, **controls):
        seen["stale"] = leak_path.exists()
        return False, False, False

    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8016")
    monkeypatch.setattr(probe, "denials", fake_denials)
    monkeypatch.setattr(probe, "config_denials", lambda *a, **k: [], raising=False)
    monkeypatch.setattr(probe, "session", lambda *a, **k: (
        {"tools": probe.TOOLS.split(","), "permissionMode": "dontAsk"}, {"subtype": "success", "is_error": False}))
    monkeypatch.setattr(probe, "bg_session", lambda *a, **k: None)
    monkeypatch.setattr(probe, "guest_sessions", lambda *a, **k: None)
    monkeypatch.setattr(probe, "service_denial", lambda *a, **k: None)   # the service root has its own case
    probe.run_probe(tmp_path, "/bin/true")
    assert seen["stale"] is False


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


def test_the_profile_allows_loopback_only_on_the_model_endpoint_port(monkeypatch):
    """#2951 criterion 2: the one loopback port the profile opens is the model route's, read from the endpoint."""
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8016")
    text = load("operators").profile_text("/work/state/outboxes/dm.jsonl", "/work/state/inboxes",
                                          "/work/state/inboxes/dm.jsonl")
    assert '(allow network-outbound (remote ip "localhost:8016"))' in text.splitlines(), text
    assert "localhost:*" not in text
    assert "*:443" not in text


def test_the_profile_grants_the_cli_background_service_its_own_runtime():
    """#2768, #2943: `claude --bg` reaches or starts the background service under the project's own root, so the
    profile grants that root: the `/dev/null` device of an ignored stdio, the root's write and its unix socket. The
    owner's shared root, which serves every unsandboxed session, stays outside the profile."""
    operators = load("operators")
    service = os.path.realpath("/work/state/operators/services/probe-project")
    text = operators.profile_text("/work/state/outboxes/dm.jsonl", "/work/state/inboxes",
                                  "/work/state/inboxes/dm.jsonl", service=service)
    write = next(line for line in text.splitlines() if line.startswith("(allow file-write*"))
    # The unmonkeypatched device: the kernel opens the resolved spelling of `/dev/null`, so the grant must name it, the
    # way the service root names the resolved path. A host where `/dev/null` resolves to itself keeps the one literal.
    resolved_null = os.path.realpath("/dev/null")
    assert f'(literal "{resolved_null}")' in write, write
    assert f'(literal "{os.path.realpath(os.devnull)}")' in write
    assert f'(subpath "{service}")' in write
    assert f'(allow network-outbound (remote unix-socket (subpath "{service}")))' in text.splitlines(), text
    assert operators.daemon_root() not in text, "the owner's shared service is outside the profile (#2943)"


def test_the_profile_writes_the_resolved_device_path(tmp_path, monkeypatch):
    """#2768: the write grant must name the device path the kernel resolves, never a symlink spelling that never
    appears at open time. `os.devnull` is resolved the way `daemon_root()` resolves `/tmp`."""
    operators = load("operators")
    # The link and its target carry different names, so the two literals the grant emits are distinct strings: the
    # symlink spelling the CLI names and the resolved target the kernel opens. `tmp_path` is already resolved, so a
    # truthful grant must list both, never the resolved form alone.
    target = tmp_path / "real-dev-null"
    target.write_text("")
    link = tmp_path / "dev-null-link"
    link.symlink_to(target)
    assert os.path.realpath(str(link)) == str(target) and str(link) != str(target)
    monkeypatch.setattr(operators.os, "devnull", str(link))
    text = operators.profile_text("/w/state/outboxes/dm.jsonl", "/w/state/inboxes", "/w/state/inboxes/dm.jsonl")
    write = next(line for line in text.splitlines() if line.startswith("(allow file-write*"))
    assert f'(literal "{link}")' in write, write
    assert f'(literal "{target}")' in write, write


def test_a_bg_session_that_cannot_start_fails_the_probe(tmp_path, monkeypatch):
    """#2768 expected (2): a `--bg` session the sandbox refuses must fail the probe, never pass it silently."""
    probe = load("operator_probe")
    stderr = ("Couldn't reach the background service (spawn background service: EPERM: operation not permitted, "
              "posix_spawn '/home/u/.local/bin/claude')")

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, "", stderr)

    monkeypatch.setattr(probe.subprocess, "run", fake_run)
    with pytest.raises(probe.ProbeFailed, match="--bg session did not start"):
        probe.bg_session(tmp_path, "(version 1)(allow default)", "claude")


def test_a_bg_session_that_starts_is_listed_under_its_marked_name_and_stopped(tmp_path, monkeypatch):
    """#2768 expected (1), #2925 criterion 15: the `--bg` session the CLI prints is live under the operator's marked
    name, and the probe stops it when the check ends."""
    probe = load("operator_probe")
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv[0] == "claude" and argv[1] == "agents":
            return subprocess.CompletedProcess(
                argv, 0, '[{"sessionId": "ba6e7d2e", "name": "SQUAD-OPERATOR-probe-project", "cwd": "/w"}]', "")
        if argv[0] == "claude" and argv[1] == "stop":
            return subprocess.CompletedProcess(argv, 0, "", "")
        return subprocess.CompletedProcess(argv, 0, "backgrounded · ba6e7d2e\n", "")

    monkeypatch.setattr(probe.subprocess, "run", fake_run)
    probe.bg_session(tmp_path, "(version 1)(allow default)", "claude")
    started = next(argv for argv in calls if argv[1] == "--bg")
    assert started[started.index("--name") + 1] == "SQUAD-OPERATOR-probe-project", started
    assert ["claude", "stop", "ba6e7d2e"] in calls


def test_a_bg_session_listed_without_the_marked_name_is_unproven(tmp_path, monkeypatch):
    """#2925 criterion 15: a name the roster does not report proves no ownership, so the supervisor could not tell the
    session from a person's own session on the same checkout."""
    probe = load("operator_probe")

    def fake_run(argv, **kwargs):
        if argv[0] == "claude" and argv[1] == "agents":
            return subprocess.CompletedProcess(argv, 0, '[{"sessionId": "ba6e7d2e", "name": "", "cwd": "/w"}]', "")
        return subprocess.CompletedProcess(argv, 0, "backgrounded · ba6e7d2e\n", "")

    monkeypatch.setattr(probe.subprocess, "run", fake_run)
    with pytest.raises(probe.ProbeFailed, match="SQUAD-OPERATOR-probe-project"):
        probe.bg_session(tmp_path, "(version 1)(allow default)", "claude")


def test_a_bg_session_the_cli_does_not_list_is_unproven(tmp_path, monkeypatch):
    """A `--bg` call that prints an id the CLI does not list never started a service: the probe fails."""
    probe = load("operator_probe")

    def fake_run(argv, **kwargs):
        if argv[0] == "claude" and argv[1] == "agents":
            return subprocess.CompletedProcess(argv, 0, "[]", "")
        return subprocess.CompletedProcess(argv, 0, "backgrounded · ba6e7d2e\n", "")

    monkeypatch.setattr(probe.subprocess, "run", fake_run)
    with pytest.raises(probe.ProbeFailed, match="does not list it"):
        probe.bg_session(tmp_path, "(version 1)(allow default)", "claude")


def test_a_bg_session_starts_in_the_projects_own_service(tmp_path, monkeypatch):
    """#2943: the `--bg` call and the roster read and stop that follow it carry the project's own service root, so the
    probe exercises the service the supervisor starts, never the owner's shared one."""
    probe = load("operator_probe")
    service = str(tmp_path / "service")
    seen = []

    def fake_run(argv, **kwargs):
        seen.append((argv, kwargs.get("env") or {}))
        if argv[0] == "claude" and argv[1] == "agents":
            return subprocess.CompletedProcess(
                argv, 0, '[{"sessionId": "ba6e7d2e", "name": "SQUAD-OPERATOR-probe-project"}]', "")
        if argv[0] == "claude" and argv[1] == "stop":
            return subprocess.CompletedProcess(argv, 0, "", "")
        return subprocess.CompletedProcess(argv, 0, "backgrounded · ba6e7d2e\n", "")

    monkeypatch.setattr(probe.subprocess, "run", fake_run)
    probe.bg_session(tmp_path, "(version 1)(allow default)", "claude", service=service)
    assert seen
    for argv, env in seen:
        assert env.get("TERMUX_VERSION") == "1" and env.get("PREFIX") == service, (argv, env)


def test_an_untrusted_workspace_names_the_one_folder_to_trust(tmp_path, monkeypatch):
    """#2775: a machine that never trusted the folder gets one folder to run `claude` in, and that folder is the one
    every run works in, so one accepted prompt holds for every later run."""
    probe = load("operator_probe")
    monkeypatch.setattr(probe, "work_root", lambda: tmp_path)
    folder = probe.session_dir()
    folder.mkdir(parents=True)
    stderr = "Workspace not trusted. Accept the trust prompt, then retry."

    monkeypatch.setattr(probe.subprocess, "run",
                        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 1, "", stderr))
    with pytest.raises(probe.ProbeFailed) as failed:
        probe.bg_session(folder, "(version 1)(allow default)", "claude")
    assert str(folder) in str(failed.value)


def test_a_loopback_refusal_fails_the_probe():
    probe = load("operator_probe")
    refused = subprocess.CompletedProcess([], 1, "", "ConnectionRefusedError: [Errno 61] Connection refused")
    with pytest.raises(probe.ProbeFailed, match="loopback"):
        probe.check_loopback_open(refused)
    probe.check_loopback_open(subprocess.CompletedProcess([], 0, "", ""))


@pytest.mark.sandbox_bound
@pytest.mark.heavy     # a foreign connect the kernel lets through waits out its 2 s timeout
def test_the_kernel_refuses_a_foreign_host_on_443_and_passes_the_endpoint_port(tmp_path):
    """The real profile, the real kernel: a foreign host on 443 must fail with the kernel's own refusal, the model
    route's loopback port must pass, and every other loopback port must fail (#2951 criterion 2)."""
    probe = load("operator_probe")
    (tmp_path / "outbox").mkdir()
    inboxes = tmp_path / "inboxes"
    inboxes.mkdir()   # a sibling of the session's cwd: denials of the inbox folder must never swallow the cwd
    listener = socket.create_server(("127.0.0.1", 0))
    elsewhere = socket.create_server(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    profile = load("operators").profile_text(str(tmp_path / "outbox"), str(inboxes), str(inboxes / "dm.jsonl"),
                                              ports=[port])
    try:
        def connect(host, port):
            return probe.sandboxed(profile, sys.executable, ["-c", probe.CONNECT, host, str(port)], tmp_path, 30)
        foreign = connect(probe.NETWORK_HOST, probe.NETWORK_DENIED)
        loopback = connect("127.0.0.1", port)
        other = connect("127.0.0.1", elsewhere.getsockname()[1])
    finally:
        listener.close()
        elsewhere.close()
    assert foreign.returncode != 0 and probe.DENIED in foreign.stderr, foreign.stderr
    assert loopback.returncode == 0, loopback.stderr
    assert other.returncode != 0 and probe.DENIED in other.stderr, "the profile reaches a loopback port that is not the model route"


def read_script(probe, allowed=()):
    """A sandbox stand-in for the read proof: every refusal is the kernel's own, and a command naming one of the
    `allowed` paths passes. A path is matched whole, so `a-outside` never counts as `a`."""
    allowed = (allowed,) if isinstance(allowed, str) else tuple(allowed)
    seen = []

    def fake(profile, cli, args, cwd, timeout, extra_env=None):
        cmd = args[-1]
        seen.append(cmd)
        if any(f'"{path}"' in cmd for path in allowed):
            return subprocess.CompletedProcess(args, 0, probe.OWN_READABLE + "\n", "")
        return subprocess.CompletedProcess(args, 1, "", probe.DENIED + "\n")

    fake.seen = seen
    return fake


def test_the_read_refusals_pass_when_each_secret_and_the_other_checkout_refuse(tmp_path, monkeypatch):
    """#2951 criteria 1 and 4: the probe proves .env, a *.slack.local.yaml, another project's checkout and each
    credential folder the home holds are refused, and its own checkout files are readable — including one named
    `.env` and one named `*.slack.local.yaml`, which a project keeps in its own checkout."""
    probe = load("operator_probe")
    home = tmp_path / "home"
    for name in probe.SECRET_DIRS:
        (home / name).mkdir(parents=True)
        (home / name / "credential.txt").write_text("secret\n")
    own = [tmp_path / "own-notes.txt", tmp_path / ".env", tmp_path / "probe.slack.local.yaml"]
    fake = read_script(probe, allowed=own)
    monkeypatch.setattr(probe, "sandboxed", fake)
    tried = probe.refuse_reads(tmp_path, "(version 1)(allow default)", tmp_path / "other-checkout", home=home)
    assert tried == sorted(str(home / name) for name in probe.SECRET_DIRS)
    joined = "\n".join(fake.seen)
    for name in (".env", "probe.slack.local.yaml", "notes.txt", "credential.txt", "own-notes.txt"):
        assert name in joined, (name, joined)


def test_refuse_reads_proves_a_secret_beside_the_checkout_and_keeps_the_checkouts_own_copies_readable(tmp_path, monkeypatch):
    """#2951 criteria 1 and 3: the `.env` and `*.slack.local.yaml` the probe must prove refused sit beside the own
    checkout, never inside it, where the same names stay readable (a project keeps its real ones there)."""
    probe = load("operator_probe")
    outside = tmp_path / "secrets"
    own = [tmp_path / "own-notes.txt", tmp_path / ".env", tmp_path / "probe.slack.local.yaml"]
    fake = read_script(probe, allowed=own)
    monkeypatch.setattr(probe, "sandboxed", fake)
    probe.refuse_reads(tmp_path, "(version 1)(allow default)", tmp_path / "other-checkout", home=tmp_path / "home",
                       outside=outside)
    assert (outside / ".env").read_text().strip() == probe.SECRET_LINE
    assert (outside / "probe.slack.local.yaml").read_text().strip() == probe.SECRET_LINE
    assert (tmp_path / ".env").read_text().strip() == probe.OWN_READABLE


def test_an_own_checkout_secret_that_the_profile_refuses_fails_the_probe(tmp_path, monkeypatch):
    """#2951 criteria 3 and 4: the own-checkout control names `.env` and `*.slack.local.yaml`, so a profile that
    refuses them everywhere — the session's own checkout included — fails the probe instead of passing it."""
    probe = load("operator_probe")
    monkeypatch.setattr(probe, "sandboxed", read_script(probe, allowed=(tmp_path / "own-notes.txt",)))
    with pytest.raises(probe.ProbeFailed, match="own checkout"):
        probe.refuse_reads(tmp_path, "(version 1)(allow default)", tmp_path / "other-checkout", home=tmp_path / "home")


def test_a_read_of_a_secret_or_another_checkout_that_lands_fails_the_probe(tmp_path, monkeypatch):
    """#2951 criterion 4: a denial that does not hold fails the probe, never passes it silently."""
    probe = load("operator_probe")
    own = [tmp_path / "own-notes.txt", tmp_path / ".env", tmp_path / "probe.slack.local.yaml"]
    # The secrets beside the checkout land: every target must be denied, and one that reads fails the probe.
    landed = own + [tmp_path.parent / f"{tmp_path.name}-outside" / ".env"]
    monkeypatch.setattr(probe, "sandboxed", read_script(probe, allowed=landed))
    with pytest.raises(probe.ProbeFailed, match="read denial did not hold"):
        probe.refuse_reads(tmp_path, "(version 1)(allow default)", tmp_path / "other-checkout", home=tmp_path / "home")


def test_a_credential_folder_with_only_subfolders_is_still_sampled(tmp_path, monkeypatch):
    """#2951 criterion 4: a credential folder that holds only subfolders is walked to its first file, so a home whose
    `~/.ssh` keeps no file at the top is still proven refused, never skipped."""
    probe = load("operator_probe")
    home = tmp_path / "home"
    nested = home / probe.SECRET_DIRS[0] / "known_hosts.d"
    nested.mkdir(parents=True)
    (nested / "host.txt").write_text("secret\n")
    own = [tmp_path / "own-notes.txt", tmp_path / ".env", tmp_path / "probe.slack.local.yaml"]
    fake = read_script(probe, allowed=own)
    monkeypatch.setattr(probe, "sandboxed", fake)
    tried = probe.refuse_reads(tmp_path, "(version 1)(allow default)", tmp_path / "other-checkout", home=home)
    assert str(home / probe.SECRET_DIRS[0]) in tried
    assert any("known_hosts.d/host.txt" in command for command in fake.seen), fake.seen


@pytest.mark.sandbox_bound
def test_the_kernel_refuses_each_read_and_keeps_the_own_checkout_readable(tmp_path):
    """#2951 criteria 1 and 4: the real profile under the real kernel refuses .env, a local Slack file and another
    project's checkout outside the session's own checkout, and keeps the checkout's own files readable — including
    its own `.env` and `*.slack.local.yaml`, which a project keeps there."""
    probe = load("operator_probe")
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    profile = load("operators").profile_text([str(tmp_path / "outbox")], str(tmp_path / "inboxes"), [],
                                             checkout=str(checkout), others=[str(tmp_path / "other-checkout")],
                                             ports=[1])
    refused = probe.refuse_reads(checkout, profile, tmp_path / "other-checkout")
    assert isinstance(refused, list)

    def run(command):
        return probe.sandboxed(profile, "/bin/sh", ["-c", command], checkout, 30)
    for name in (".env", "probe.slack.local.yaml", "own-notes.txt"):
        done = run(f'cat "{checkout / name}"')
        assert done.returncode == 0 and probe.OWN_READABLE in done.stdout, (name, done.stderr)
    outside = checkout.parent / f"{checkout.name}-outside"
    for name in (".env", "probe.slack.local.yaml"):
        done = run(f'cat "{outside / name}"')
        assert done.returncode != 0 and probe.DENIED in done.stderr, (name, done.stderr)


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
    assert '"target": "93.184.216.34:443"' in log.read_text()


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
    monkeypatch.setattr(probe, "denials", lambda *a, **k: (False, False, False))
    monkeypatch.setattr(probe, "config_denials", lambda *a, **k: [], raising=False)
    monkeypatch.setattr(probe, "check_denials", lambda *a, **k: None)

    def fake_session(work, profile, cli, proxy_url=None):
        seen["proxy_url"] = proxy_url
        return ({"tools": probe.TOOLS.split(","), "permissionMode": "dontAsk"}, {"subtype": "success", "is_error": False})

    def fake_guest(work, profile, cli, role, proxy_url=None):
        seen[role] = proxy_url
        init = {"tools": list(probe.GUEST_TOOLS), "permissionMode": "dontAsk"}
        return init, {"subtype": "success", "is_error": False}, probe.tool_calls(events_of(held_calls(probe, role)))

    def fake_bg(work, profile, cli, proxy_url=None, service=None):
        seen["bg"] = proxy_url

    monkeypatch.setattr(probe, "session", fake_session)
    monkeypatch.setattr(probe, "bg_session", fake_bg)
    monkeypatch.setattr(probe, "guest_session", fake_guest)
    monkeypatch.setattr(probe, "service_denial", lambda *a, **k: None)   # the service root has its own case
    probe.run_probe(tmp_path, "/bin/true")
    assert seen == {"proxy_url": None, "bg": None, "poller": None, "writer": None}


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


def test_the_profile_opens_no_write_root_beyond_the_outbox_the_claude_home_and_the_service():
    """#2768, #2943 keeps every write grant a literal or a named subpath: the outbox file, the Claude home, `/dev/null`
    and the project's own service root. No grant names a whole root."""
    operators = load("operators")
    service = os.path.realpath("/work/state/operators/services/probe-project")
    text = operators.profile_text("/work/state/outboxes/dm.jsonl", "/work/state/inboxes",
                                  "/work/state/inboxes/dm.jsonl", service=service)
    write = next(line for line in text.splitlines() if line.startswith("(allow file-write*"))
    for root in ("/dev", "/tmp", "/private/tmp", "/private/var/folders"):
        assert f'(literal "{root}")' not in write and f'(subpath "{root}")' not in write
    assert write.count("(subpath") == 2, write
    assert f'(subpath "{service}")' in write


@pytest.mark.sandbox_bound
def test_the_profile_refuses_a_write_to_the_temp_folder(tmp_path):
    probe = load("operator_probe")
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    profile = load("operators").profile_text(str(outbox), str(tmp_path), str(tmp_path / "dm.jsonl"))
    leak = Path(tempfile.gettempdir()) / f"operator-probe-leak-{os.getpid()}.txt"
    try:
        done = probe.sandboxed(profile, "/bin/sh", ["-c", f'echo leaked > "{leak}"'], tmp_path, 30)
        landed = leak.exists()
    finally:
        leak.unlink(missing_ok=True)
    assert not landed
    assert "Operation not permitted" in done.stderr


def test_the_probe_proves_the_shared_service_is_out_of_reach(tmp_path, monkeypatch):
    """#2943: a connect to the owner's shared background service, which sits outside the profile, is refused."""
    probe = load("operator_probe")
    monkeypatch.setattr(probe, "sandboxed", scripted(probe))
    assert probe.service_denial(tmp_path, "(version 1)(allow default)", str(tmp_path / "service"))
    monkeypatch.setattr(probe, "sandboxed", scripted(probe, service_open=True))
    with pytest.raises(probe.ProbeFailed, match="shared service"):
        probe.service_denial(tmp_path, "(version 1)(allow default)", str(tmp_path / "service"))


@pytest.mark.sandbox_bound
def test_the_shared_service_denial_probes_a_socket_that_exists(tmp_path, monkeypatch):
    """#2979: the kernel answers a missing path with "no such file", never a denial, so the probe binds the file it probes
    under the shared root and removes it after."""
    probe = load("operator_probe")
    inner, seen = scripted(probe), []

    def spy(profile, cli, args, cwd, timeout, extra_env=None):
        seen.append((args[-1], os.path.exists(args[-1])))
        return inner(profile, cli, args, cwd, timeout, extra_env)

    monkeypatch.setattr(probe, "sandboxed", spy)
    shared = probe.service_denial(tmp_path, "(version 1)(allow default)", str(tmp_path / "service"))
    assert seen[0] == (shared, True), f"the probe named a path that does not exist: {seen[0]}"
    assert not os.path.exists(shared), "the probe left its own socket under the shared root"


@pytest.mark.sandbox_bound
def test_the_kernel_refuses_a_connect_to_the_shared_service(tmp_path):
    """The real profile, the real kernel: the owner's shared service root is outside, the project's own root is inside."""
    probe = load("operator_probe")
    service = tmp_path / "service"
    profile = load("operators").profile_text(str(tmp_path / "out.jsonl"), str(tmp_path / "in"),
                                             str(tmp_path / "in" / "dm.jsonl"), service=str(service))
    assert probe.service_denial(tmp_path, profile, str(service))


@pytest.mark.claude_bound
@pytest.mark.heavy     # a live `claude -p` call takes seconds: over the 1 s line, so the fast battery deselects it (ADR-0139)
def test_the_real_cli_probe_passes():
    probe = load("operator_probe")
    assert probe.main([]) == 0


def test_the_probe_profile_names_one_outbox_and_inbox_per_channel_of_the_project(tmp_path):
    """#2925 criteria 9 to 11: the probe's profile is the live project shape, one outbox and one inbox per channel, and
    another project's inbox and checkout stay refused (#2951)."""
    probe = load("operator_probe")
    checkout, outboxes, own_inboxes, other_inbox, leak, other_checkout, service, profile = probe.project_shape(
        tmp_path, [8016])
    write = next(line for line in profile.splitlines() if line.startswith("(allow file-write*"))
    read = next(line for line in profile.splitlines() if line.startswith("(allow file-read*")
                and f'(literal "{own_inboxes[0]}")' in line)
    for outbox in outboxes:
        assert f'(literal "{outbox}")' in write, write
    for inbox in own_inboxes:
        assert f'(literal "{inbox}")' in read, read
        assert inbox.read_text().strip() == probe.OWN_SENTINEL
    assert other_inbox.read_text().strip() == probe.SENTINEL
    assert f'(deny file-read* (subpath "{other_inbox.parent}"))' in profile, profile
    assert f'(deny file-read* (subpath "{other_checkout}"))' in profile, profile
    assert '(allow network-outbound (remote ip "localhost:8016"))' in profile, profile
    assert checkout.is_dir() and other_checkout.is_dir() and not leak.exists()


def test_the_probe_builds_its_service_root_the_way_the_supervisor_does(tmp_path):
    """#2943: the probe's service root comes from `operators.service_root`, the one a live `start()` grants, so a change
    to that layout moves the probe with it instead of leaving it green on a shape no start produces."""
    probe = load("operator_probe")
    operators = load("operators")
    _, _, _, _, _, _, service, profile = probe.project_shape(tmp_path, [8016])
    expected = operators.service_root(str(tmp_path / "state"), probe.PROBE_KEY)
    assert os.path.realpath(service) == expected, (service, expected)
    assert f'(subpath "{expected}")' in profile, profile


def scripted(probe, *, outbox_refused=False, inbox_line=None, config_open=False, port=1, service_open=False):
    """A sandbox stand-in that answers the denial script: every refusal is the kernel's own, the controls pass unless a
    case says otherwise. `port` is the one loopback port the model route uses; every other port is refused (#2951)."""
    inbox_line = probe.OWN_SENTINEL if inbox_line is None else inbox_line

    def run_sandboxed(profile, cli, args, cwd, timeout, extra_env=None):
        cmd = args[-1] if cli == "/bin/sh" else ""
        if cmd == "echo control":
            return subprocess.CompletedProcess(args, 0, "control\n", "")
        if "leaked" in cmd or "other-project" in cmd:
            return subprocess.CompletedProcess(args, 1, "", probe.DENIED + "\n")
        if "answered >>" in cmd:
            if outbox_refused:
                return subprocess.CompletedProcess(args, 1, "", probe.DENIED + "\n")
            return subprocess.CompletedProcess(args, 0, "", "")
        if cmd.startswith("cat "):
            return subprocess.CompletedProcess(args, 0, inbox_line + "\n", "")
        if cli == sys.executable:
            if getattr(probe, "OPEN_FOR_WRITE", None) in args:
                if config_open:
                    return subprocess.CompletedProcess(args, 0, "", "")
                return subprocess.CompletedProcess(args, 1, "", probe.DENIED + "\n")
            if probe.CONNECT in args:
                if args[-2] == "127.0.0.1" and args[-1] == str(port):
                    return subprocess.CompletedProcess(args, 0, "", "")
                return subprocess.CompletedProcess(args, 1, "", probe.DENIED + "\n")
            if probe.NETWORK_HOST in args:
                return subprocess.CompletedProcess(args, 1, "", probe.DENIED + "\n")
            if getattr(probe, "SERVICE_CONNECT", None) in args:
                # The shared root is outside the profile; the project's own root is inside it, and the kernel there
                # answers "no such file" because the probe starts no socket.
                if service_open or probe.daemon_root() not in args[-1]:
                    return subprocess.CompletedProcess(args, 0, "", "")
                return subprocess.CompletedProcess(args, 1, "", probe.DENIED + "\n")
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(f"unexpected denial call: {args}")

    return run_sandboxed


def probe_paths(tmp_path):
    outbox = tmp_path / "outboxes" / "C1.jsonl"
    outbox.parent.mkdir(parents=True)
    inbox = tmp_path / "inboxes" / "C1.jsonl"
    inbox.parent.mkdir(parents=True)
    inbox.write_text("PROBE-OWN-INBOX-LINE\n")
    other = tmp_path / "inboxes" / "other-project.jsonl"
    other.write_text("PROBE-OTHER-INBOX-LINE\n")
    return outbox, other, inbox


def test_the_denials_pass_when_each_own_outbox_and_inbox_answers(tmp_path, monkeypatch):
    probe = load("operator_probe")
    outbox, other, inbox = probe_paths(tmp_path)
    monkeypatch.setattr(probe, "sandboxed", scripted(probe))
    assert probe.denials(tmp_path, "(version 1)(allow default)", tmp_path / "elsewhere" / "leak.txt", other, 1,
                         outboxes=[outbox], own_inboxes=[inbox]) == (False, False, False)


def test_a_profile_that_refuses_an_own_outbox_fails_the_probe(tmp_path, monkeypatch):
    probe = load("operator_probe")
    outbox, other, inbox = probe_paths(tmp_path)
    monkeypatch.setattr(probe, "sandboxed", scripted(probe, outbox_refused=True))
    with pytest.raises(probe.ProbeFailed, match="refuses an own outbox"):
        probe.denials(tmp_path, "(version 1)(allow default)", tmp_path / "elsewhere" / "leak.txt", other, 1,
                      outboxes=[outbox], own_inboxes=[inbox])


def test_a_profile_that_hides_an_own_inbox_fails_the_probe(tmp_path, monkeypatch):
    probe = load("operator_probe")
    outbox, other, inbox = probe_paths(tmp_path)
    monkeypatch.setattr(probe, "sandboxed", scripted(probe, inbox_line=""))
    with pytest.raises(probe.ProbeFailed, match="refuses an own inbox"):
        probe.denials(tmp_path, "(version 1)(allow default)", tmp_path / "elsewhere" / "leak.txt", other, 1,
                      outboxes=[outbox], own_inboxes=[inbox])


def test_the_probe_runs_its_session_in_the_project_checkout(tmp_path, monkeypatch):
    """#2925 criteria 7 and 8: the probe's real CLI session runs in the project checkout, so it loads that project's
    CLAUDE.md, never in the probe's own state folder."""
    probe = load("operator_probe")
    seen = []
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8016")
    monkeypatch.setattr(probe, "denials", lambda *a, **k: (False, False, False))
    monkeypatch.setattr(probe, "config_denials", lambda *a, **k: [], raising=False)
    monkeypatch.setattr(probe, "check_denials", lambda *a, **k: None)

    def fake_session(work, profile, cli, proxy_url=None):
        seen.append(work)
        return ({"tools": probe.TOOLS.split(","), "permissionMode": "dontAsk"}, {"subtype": "success", "is_error": False})

    monkeypatch.setattr(probe, "session", fake_session)
    monkeypatch.setattr(probe, "bg_session", lambda *a, **k: None)
    monkeypatch.setattr(probe, "guest_sessions", lambda *a, **k: None)
    monkeypatch.setattr(probe, "service_denial", lambda *a, **k: None)   # the service root has its own case
    probe.run_probe(tmp_path, "/bin/true")
    assert seen == [tmp_path / "checkout"], seen


# #2716: the guest poller and writer sessions run through the same real CLI and wrapper, each with its real PreToolUse
# hook (through contrib/voice/guest_probe_hook.py) and a stand-in connector (contrib/voice/fake_slack_mcp.py).

def events_of(calls):
    """Stream-json events for tool calls: (name, input, result text) each, in order."""
    events = []
    for n, (name, arguments, text) in enumerate(calls):
        tid = f"toolu_{n}"
        events.append({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": tid, "name": name, "input": arguments}]}})
        events.append({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": tid, "content": [{"type": "text", "text": text}]}]}})
    return events


def reached_text(probe, name, arguments):
    import json
    line = json.dumps({"tool": name.split("__")[-1], "arguments": arguments}, sort_keys=True)
    return f"{probe.CALLED} {line}"


def held_calls(probe, role):
    """The calls of a session whose every denial held and whose control call passed."""
    calls = []
    for _label, name, arguments, reason in probe.DENIALS[role]:
        calls.append((name, arguments, f"PreToolUse:{name} hook error: {reason} (probe)"))
    name, arguments = probe.CONTROLS[role]
    calls.append((name, arguments, reached_text(probe, name, probe.expected_control(role))))
    return calls


@pytest.mark.parametrize("role", ["poller", "writer"])
def test_a_session_whose_denials_held_passes_the_check(role):
    probe = load("operator_probe")
    probe.check_guest(role, probe.tool_calls(events_of(held_calls(probe, role))))


@pytest.mark.parametrize("role,index", [("poller", 0), ("writer", 0), ("writer", 1), ("writer", 2)])
def test_a_guest_denial_that_does_not_hold_fails_the_probe(role, index):
    probe = load("operator_probe")
    calls = held_calls(probe, role)
    name, arguments, _ = calls[index]
    calls[index] = (name, arguments, reached_text(probe, name, arguments))
    with pytest.raises(probe.ProbeFailed, match="did not hold"):
        probe.check_guest(role, probe.tool_calls(events_of(calls)))


@pytest.mark.parametrize("role,index", [("poller", 0), ("writer", 0), ("writer", 1), ("writer", 2)])
def test_a_guest_denial_the_session_never_tried_is_unproven(role, index):
    probe = load("operator_probe")
    calls = held_calls(probe, role)
    del calls[index]
    with pytest.raises(probe.ProbeFailed, match="unproven"):
        probe.check_guest(role, probe.tool_calls(events_of(calls)))


@pytest.mark.parametrize("role", ["poller", "writer"])
def test_a_refusal_without_the_hook_reason_is_unproven(role):
    probe = load("operator_probe")
    calls = held_calls(probe, role)
    name, arguments, _ = calls[0]
    calls[0] = (name, arguments, "some other refusal")
    with pytest.raises(probe.ProbeFailed, match="unproven"):
        probe.check_guest(role, probe.tool_calls(events_of(calls)))


@pytest.mark.parametrize("role", ["poller", "writer"])
def test_a_control_call_that_did_not_pass_fails_the_probe(role):
    probe = load("operator_probe")
    calls = held_calls(probe, role)
    name, arguments, _ = calls[-1]
    calls[-1] = (name, arguments, "denied")
    with pytest.raises(probe.ProbeFailed, match="control"):
        probe.check_guest(role, probe.tool_calls(events_of(calls)))


def test_a_draft_that_reaches_the_connector_with_the_model_text_fails_the_probe():
    """The writer hook returns updatedInput with the decided text: the model's own text must never reach the connector."""
    probe = load("operator_probe")
    calls = held_calls(probe, "writer")
    name, arguments, _ = calls[-1]
    calls[-1] = (name, arguments, reached_text(probe, name, arguments))
    with pytest.raises(probe.ProbeFailed, match="decided"):
        probe.check_guest("writer", probe.tool_calls(events_of(calls)))


def test_the_writer_control_reaches_the_connector_with_the_decided_text_only():
    probe = load("operator_probe")
    draft = probe.PROBE_DRAFT
    assert probe.expected_control("writer") == {"channel": draft.channel, "thread_ts": draft.thread_ts,
                                                "text": draft.text}
    assert probe.CONTROLS["writer"][1]["text"] != draft.text


@pytest.mark.parametrize("extra", ["mcp__claude_ai_Slack__slack_send_message", "Bash"])
def test_a_guest_session_with_a_tool_beyond_the_stand_in_fails_the_probe(extra):
    probe = load("operator_probe")
    init = {"tools": [*probe.GUEST_TOOLS, extra], "permissionMode": "dontAsk"}
    with pytest.raises(probe.ProbeFailed, match=extra):
        probe.check_guest_init(init)
    probe.check_guest_init({"tools": list(probe.GUEST_TOOLS), "permissionMode": "dontAsk"})


@pytest.mark.parametrize("role", ["poller", "writer"])
def test_a_guest_session_runs_through_the_wrapper_with_its_hook_and_the_stand_in(tmp_path, monkeypatch, role):
    import json
    probe = load("operator_probe")
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"], seen["env"] = argv, kwargs["env"]
        init = {"type": "system", "subtype": "init", "tools": list(probe.GUEST_TOOLS), "permissionMode": "dontAsk"}
        out = json.dumps(init) + '\n{"type": "result", "subtype": "success"}\n'
        return subprocess.CompletedProcess(argv, 0, out, "")

    monkeypatch.setattr(probe.subprocess, "run", fake_run)
    probe.guest_session(tmp_path, "(version 1)(allow default)", "claude", role, "http://127.0.0.1:4242")
    argv = seen["argv"]
    assert argv[0] == probe.WRAPPER
    assert seen["env"]["SQUAD_CLAUDE_CLI"] == "claude"
    assert seen["env"]["HTTPS_PROXY"] == "http://127.0.0.1:4242"
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--allowedTools") + 1] == "mcp__slack"
    assert argv[argv.index("--setting-sources") + 1] == ""
    assert argv[argv.index("--max-budget-usd") + 1] == probe.SPEND
    assert argv[argv.index("--model") + 1] == probe.MODEL
    assert "--strict-mcp-config" in argv
    servers = json.loads(argv[argv.index("--mcp-config") + 1])["mcpServers"]
    assert list(servers) == ["slack"]
    assert str(probe.FAKE_CONNECTOR) in servers["slack"]["args"]
    hooks = json.loads(argv[argv.index("--settings") + 1])["hooks"]["PreToolUse"]
    command = hooks[0]["hooks"][0]["command"]
    assert str(probe.GUEST_HOOK) in command and f" {role}" in command


def test_the_probe_runs_the_poller_and_the_writer_session(tmp_path, monkeypatch):
    probe = load("operator_probe")
    roles = []
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8016")
    monkeypatch.setattr(probe, "denials", lambda *a, **k: (False, False, False))
    monkeypatch.setattr(probe, "config_denials", lambda *a, **k: [], raising=False)
    monkeypatch.setattr(probe, "session", lambda *a, **k: (
        {"tools": probe.TOOLS.split(","), "permissionMode": "dontAsk"}, {"subtype": "success", "is_error": False}))

    def fake_guest(work, profile, cli, role, proxy_url=None):
        roles.append(role)
        init = {"tools": list(probe.GUEST_TOOLS), "permissionMode": "dontAsk"}
        return init, {"subtype": "success", "is_error": False}, probe.tool_calls(events_of(held_calls(probe, role)))

    monkeypatch.setattr(probe, "guest_session", fake_guest)
    monkeypatch.setattr(probe, "bg_session", lambda *a, **k: None)
    monkeypatch.setattr(probe, "service_denial", lambda *a, **k: None)   # the service root has its own case
    probe.run_probe(tmp_path, "/bin/true")
    assert roles == ["poller", "writer"]


def run_hook(role_args, tool_input):
    import json
    return subprocess.run([sys.executable, str(VOICE / "guest_probe_hook.py"), *role_args], input=json.dumps(tool_input),
                          capture_output=True, text=True, check=False, timeout=20)


@pytest.mark.heavy     # four Python starts that each import squad: load can push them over the 1 s line
def test_the_hook_shim_answers_with_the_real_guest_hooks():
    import json
    probe = load("operator_probe")
    draft = probe.PROBE_DRAFT
    write = run_hook(["poller"], {"tool_name": "mcp__slack__slack_send_message", "tool_input": {"text": "x"}})
    assert json.loads(write.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    read = run_hook(["poller"], {"tool_name": "mcp__slack__slack_read_channel", "tool_input": {}})
    assert json.loads(read.stdout) == {}
    draft_json = json.dumps(draft._asdict())
    other = run_hook(["writer", draft_json], {"tool_name": "mcp__slack__slack_send_message_draft",
                                              "tool_input": {"channel": "COTHER", "text": "x"}})
    assert json.loads(other.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    passed = run_hook(["writer", draft_json], {"tool_name": "mcp__slack__slack_send_message_draft",
                                               "tool_input": {"channel": draft.channel, "text": "x"}})
    assert json.loads(passed.stdout)["hookSpecificOutput"]["updatedInput"]["text"] == draft.text


def test_the_hook_shim_blocks_when_it_cannot_run_the_hook():
    done = run_hook(["nobody"], {"tool_name": "mcp__slack__slack_read_channel", "tool_input": {}})
    assert done.returncode == 2
    assert "blocked" in done.stderr


def test_the_stand_in_connector_reports_each_call_it_runs():
    import json
    lines = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
             {"jsonrpc": "2.0", "method": "notifications/initialized"},
             {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
             {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
              "params": {"name": "slack_send_message_draft", "arguments": {"channel": "C1", "text": "t"}}}]
    done = subprocess.run([sys.executable, str(VOICE / "fake_slack_mcp.py")], capture_output=True, text=True,
                          check=False, timeout=20, input="".join(json.dumps(line) + "\n" for line in lines))
    replies = [json.loads(line) for line in done.stdout.splitlines()]
    assert [r["id"] for r in replies] == [1, 2, 3]
    assert replies[0]["result"]["protocolVersion"] == "2025-06-18"
    names = [t["name"] for t in replies[1]["result"]["tools"]]
    assert names == ["slack_read_channel", "slack_send_message", "slack_send_message_draft"]
    text = replies[2]["result"]["content"][0]["text"]
    assert text.startswith("FAKE-SLACK-CALLED ")
    assert json.loads(text.split(" ", 1)[1]) == {"tool": "slack_send_message_draft",
                                                 "arguments": {"channel": "C1", "text": "t"}}


def test_the_roster_lists_the_session_under_the_short_id_it_printed(tmp_path):
    """`claude --bg` prints the short id, and the real roster carries it as `id` beside the full `sessionId`. The
    probe finds the session by the id it was printed, whichever field holds it."""
    probe = load("operator_probe")
    cli = tmp_path / "claude"
    row = {"id": "b5ccaeeb", "sessionId": "b5ccaeeb-b48a-44b5-818e-540ff3b0476a", "name": "SQUAD-OPERATOR-x"}
    cli.write_text("#!/bin/sh\nprintf '%s' '" + json.dumps([row]) + "'\n")
    cli.chmod(0o755)
    assert probe.bg_listed(str(cli), "b5ccaeeb", str(tmp_path), "SQUAD-OPERATOR-x")
    assert not probe.bg_listed(str(cli), "b5ccaeeb", str(tmp_path), "SQUAD-OPERATOR-y")


def owner_home(tmp_path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    for name in ("settings.json", "CLAUDE.md"):
        (home / ".claude" / name).write_text("owner file\n")
    return home


def test_the_probe_proves_the_owners_claude_files_are_refused(tmp_path, monkeypatch):
    """#2925: the probe tries a write to the owner's settings and CLAUDE.md under the profile, and each is refused."""
    probe = load("operator_probe")
    home = owner_home(tmp_path)
    monkeypatch.setattr(probe, "sandboxed", scripted(probe))
    tried = probe.config_denials(tmp_path, "(version 1)(allow default)", home=home)
    assert sorted(os.path.basename(p) for p in tried) == ["CLAUDE.md", "settings.json"], tried
    assert (home / ".claude" / "settings.json").read_text() == "owner file\n"


def test_a_profile_that_lets_the_owners_settings_be_written_fails_the_probe(tmp_path, monkeypatch):
    probe = load("operator_probe")
    home = owner_home(tmp_path)
    monkeypatch.setattr(probe, "sandboxed", scripted(probe, config_open=True))
    with pytest.raises(probe.ProbeFailed, match="may write"):
        probe.config_denials(tmp_path, "(version 1)(allow default)", home=home)


@pytest.mark.sandbox_bound   # a nested refusal says "Operation not permitted" too, so only a real apply proves this (ADR-0139)
def test_the_real_profile_refuses_the_owners_claude_files(tmp_path, monkeypatch):
    """The operator's own profile, applied by the kernel, refuses both writes on this machine."""
    probe = load("operator_probe")
    if sys.platform != "darwin" or not os.path.exists("/usr/bin/sandbox-exec"):
        pytest.skip("the kernel bound is macOS only")
    profile = probe.profile_text([str(tmp_path / "out.jsonl")], str(tmp_path / "inboxes"), [])
    home = os.path.realpath(os.path.expanduser("~"))
    tried = probe.config_denials(tmp_path, profile, home=home)
    assert tried, "this machine holds no owner Claude file to try"
