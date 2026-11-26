"""Guard: issue #1619. One bounded probe with the real `claude` CLI proves the operator session's tool set and its kernel
bound. A fake CLI never stands in for the permission boundary, so the denial cases run the real sandbox-exec profile.
"""
import importlib.util
import json
import os
import shlex
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
    pinned = tmp_path / "state" / "operators" / "sessions"
    pinned.mkdir(parents=True)
    (pinned / "dm.json").write_text(json.dumps({"dm": "D0AAAAAA1"}))   # a DM start binds the pinned record alone
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
    monkeypatch.setattr(probe, "prepare_config", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(probe, "session", lambda *a, **k: (
        {"tools": probe.TOOLS.split(","), "permissionMode": "dontAsk"}, {"subtype": "success", "is_error": False}))
    monkeypatch.setattr(probe, "bg_session", lambda *a, **k: None)
    monkeypatch.setattr(probe, "guest_sessions", lambda *a, **k: None)
    monkeypatch.setattr(probe, "answer_phase", lambda *a, **k: None)
    monkeypatch.setattr(probe, "full_turn_phase", lambda *a, **k: None)   # a full turn makes live model calls (ADR-0180)
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
                                          "/work/state/inboxes/dm.jsonl", config="/work/config")
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
                                  "/work/state/inboxes/dm.jsonl", config="/work/config", service=service)
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
    text = operators.profile_text("/w/state/outboxes/dm.jsonl", "/w/state/inboxes", "/w/state/inboxes/dm.jsonl",
                                  config="/w/config")
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
                                              ports=[port], config=str(tmp_path / "config"))
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
                                             ports=[1], config=str(tmp_path / "config"))
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
    monkeypatch.setattr(probe, "prepare_config", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(probe, "check_denials", lambda *a, **k: None)

    def fake_session(work, profile, cli, proxy_url=None):
        seen["proxy_url"] = proxy_url
        return ({"tools": probe.TOOLS.split(","), "permissionMode": "dontAsk"}, {"subtype": "success", "is_error": False})

    def fake_guest(work, profile, cli, role, proxy_url=None):
        seen[role] = proxy_url
        init = {"tools": list(probe.GUEST_TOOLS), "permissionMode": "dontAsk"}
        return init, {"subtype": "success", "is_error": False}, probe.tool_calls(events_of(held_calls(probe, role)))

    def fake_bg(work, profile, cli, proxy_url=None, **kwargs):
        seen["bg"] = proxy_url

    monkeypatch.setattr(probe, "session", fake_session)
    monkeypatch.setattr(probe, "bg_session", fake_bg)
    monkeypatch.setattr(probe, "guest_session", fake_guest)
    monkeypatch.setattr(probe, "answer_phase", lambda *a, **k: None)
    monkeypatch.setattr(probe, "full_turn_phase", lambda *a, **k: None)   # a full turn makes live model calls (ADR-0180)
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
    """#2768, #2963, #2943 keeps every write grant a literal or a named subpath: the outbox file, the Claude home, the
    CLI's own config root, `/dev/null` and the project's own service root. No grant names a whole root, the owner's global
    config file is granted to no one, and the owner's shared service root is a write root for no session."""
    operators = load("operators")
    service = os.path.realpath("/work/state/operators/services/probe-project")
    text = operators.profile_text("/work/state/outboxes/dm.jsonl", "/work/state/inboxes",
                                  "/work/state/inboxes/dm.jsonl", config="/work/config", service=service)
    write = next(line for line in text.splitlines() if line.startswith("(allow file-write*"))
    for root in ("/dev", "/tmp", "/private/tmp", "/private/var/folders"):
        assert f'(literal "{root}")' not in write and f'(subpath "{root}")' not in write
    assert write.count("(subpath") == 3, write
    assert '(subpath "/work/config")' in write, write
    assert f'(subpath "{service}")' in write
    assert ".claude.json" not in write, "the owner's global config file is granted to no one"
    assert f'(subpath "{operators.daemon_root()}")' not in write, "the shared service root is never a write root"


@pytest.mark.sandbox_bound
def test_the_profile_refuses_a_write_to_the_temp_folder(tmp_path):
    probe = load("operator_probe")
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    profile = load("operators").profile_text(str(outbox), str(tmp_path), str(tmp_path / "dm.jsonl"),
                                             config=str(tmp_path / "config"))
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
                                             str(tmp_path / "in" / "dm.jsonl"), config=str(tmp_path / "config"),
                                             service=str(service))
    assert probe.service_denial(tmp_path, profile, str(service))


@pytest.mark.claude_bound
@pytest.mark.heavy     # a live `claude -p` call takes seconds: over the 1 s line, so the fast battery deselects it (ADR-0139)
@pytest.mark.slow(reason="the probe makes one live model call per phase in sequence: the tool set, the answer helper, "
                         "a --bg session and two guest sessions; their sum passes 20 s once every phase runs (#2982)")
def test_the_real_cli_probe_passes():
    probe = load("operator_probe")
    assert probe.main([]) == 0


FULL_STAND_IN = """#!{python}
import json, os, sys
argv = sys.argv[1:]
stdin = sys.stdin.read()
store = os.environ["STUB_STORE"]
if "--resume" in argv:
    session = argv[argv.index("--resume") + 1]
    answer = open(store, encoding="utf-8").read() if os.environ.get("STUB_REMEMBERS") else "I do not know."
else:
    session = "sess-probe-0001"
    with open(store, "w", encoding="utf-8") as f:
        f.write(stdin)
    answer = "ok"
print(json.dumps({{"type": "result", "is_error": False, "result": answer, "session_id": session}}))
"""


def full_stand_in(tmp_path, monkeypatch, remembers):
    path = tmp_path / "claude-full-stand-in"
    path.write_text(FULL_STAND_IN.format(python=sys.executable))
    path.chmod(path.stat().st_mode | 0o100)
    monkeypatch.setenv("STUB_STORE", str(tmp_path / "said.txt"))
    monkeypatch.setenv("STUB_REMEMBERS", "1" if remembers else "")
    return str(path)


def holder_listing(session):
    """The process table's seam: it names the session on the first call, as a live holder does, then reports it gone."""
    calls = []

    def listing():
        calls.append(1)
        return [["claude", "--resume", session]] if len(calls) == 1 else []

    return listing


def test_a_full_turn_returns_on_resume_the_word_it_was_given_on_stdin(tmp_path, monkeypatch):
    """#2965 criteria (4) to (6) and (13): the second full turn resumes the first session and returns its stdin word.
    #2969: the phase then proves a real `--resume` holder is seen, and a turn runs after it closes."""
    probe = load("operator_probe")
    cli = full_stand_in(tmp_path, monkeypatch, remembers=True)

    def no_sandbox(*args, **kwargs):
        raise AssertionError("a full call ran through the sandbox wrapper")

    monkeypatch.setattr(probe, "sandboxed", no_sandbox)
    monkeypatch.setattr(probe, "cli_call", no_sandbox)
    probe.full_turn_phase(tmp_path, cli, listing=holder_listing("sess-probe-0001"))


HOLDER_STAND_IN = """#!{python}
import json, os, sys, time
with open(os.environ["HOLDER_ARGV"], "w", encoding="utf-8") as f:
    json.dump([os.path.realpath(sys.argv[0])] + sys.argv[1:], f)
time.sleep(30)
"""


def holder_stand_in(tmp_path, monkeypatch):
    """A stand-in CLI whose file name is exactly `claude`, so the process table's parse takes it for the CLI. It
    records the argv it started with and stays alive, as an interactive terminal does."""
    folder = tmp_path / "bin"
    folder.mkdir()
    path = folder / "claude"
    path.write_text(HOLDER_STAND_IN.format(python=sys.executable))
    path.chmod(path.stat().st_mode | 0o100)
    record = tmp_path / "holder-argv.json"
    monkeypatch.setenv("HOLDER_ARGV", str(record))
    return str(path), record


def recorded_listing(record):
    """The process table's seam for a test: it answers the argv the holder recorded, once the holder has written it."""
    def listing():
        try:
            return [json.loads(record.read_text(encoding="utf-8"))]
        except (OSError, ValueError):
            return []
    return listing


def test_the_probe_holds_the_session_with_an_interactive_terminal(tmp_path, monkeypatch):
    """AC4: the holder the probe proves the refusal with is an interactive `claude --resume` terminal, never the
    daemon's own `-p` path, so the refusal it proves is the one a terminal causes."""
    probe = load("operator_probe")
    cli, record = holder_stand_in(tmp_path, monkeypatch)
    probe.hold_session(cli, tmp_path, "sess-probe-0001", listing=recorded_listing(record))
    argv = json.loads(record.read_text(encoding="utf-8"))
    assert "-p" not in argv and "--print" not in argv, argv
    assert argv[1:] == ["--resume", "sess-probe-0001"], argv


def test_the_probe_fails_when_the_listed_holder_is_not_a_terminal(tmp_path, monkeypatch):
    """A listed holder that carries the print flag is the daemon's own path, so it proves no terminal refusal."""
    probe = load("operator_probe")
    cli, _record = holder_stand_in(tmp_path, monkeypatch)
    with pytest.raises(probe.ProbeFailed, match="terminal"):
        probe.hold_session(cli, tmp_path, "sess-probe-0001",
                           listing=lambda: [["claude", "--resume", "sess-probe-0001", "-p"]])


def test_a_full_turn_phase_fails_when_the_process_table_never_names_the_session(tmp_path, monkeypatch):
    """#2969 AC4: the probe fails unless the live listing shows the holder, so a refusal it never saw proves nothing."""
    probe = load("operator_probe")
    cli = full_stand_in(tmp_path, monkeypatch, remembers=True)
    monkeypatch.setattr(probe, "HOLD_DEADLINE", 0.5)   # the holder stays alive, so the deadline decides, not an exit
    with pytest.raises(probe.ProbeFailed, match="process table"):
        probe.full_turn_phase(tmp_path, cli, listing=lambda: [])


def test_a_full_turn_phase_is_unproven_when_the_process_table_is_unreadable(tmp_path, monkeypatch):
    """An unreadable listing is unproven, so the phase never passes on it silently."""
    probe = load("operator_probe")
    cli = full_stand_in(tmp_path, monkeypatch, remembers=True)
    with pytest.raises(probe.ProbeFailed, match="unreadable"):
        probe.full_turn_phase(tmp_path, cli, listing=lambda: None)


def test_a_full_turn_that_forgets_the_stdin_word_fails_the_probe(tmp_path, monkeypatch):
    probe = load("operator_probe")
    cli = full_stand_in(tmp_path, monkeypatch, remembers=False)
    with pytest.raises(probe.ProbeFailed, match="stdin"):
        probe.full_turn_phase(tmp_path, cli)


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
        assert probe.OWN_SENTINEL in inbox.read_text()
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
    monkeypatch.setattr(probe, "prepare_config", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(probe, "check_denials", lambda *a, **k: None)

    def fake_session(work, profile, cli, proxy_url=None):
        seen.append(work)
        return ({"tools": probe.TOOLS.split(","), "permissionMode": "dontAsk"}, {"subtype": "success", "is_error": False})

    monkeypatch.setattr(probe, "session", fake_session)
    monkeypatch.setattr(probe, "bg_session", lambda *a, **k: None)
    monkeypatch.setattr(probe, "guest_sessions", lambda *a, **k: None)
    monkeypatch.setattr(probe, "answer_phase", lambda *a, **k: None)
    monkeypatch.setattr(probe, "full_turn_phase", lambda *a, **k: None)   # a full turn makes live model calls (ADR-0180)
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
    monkeypatch.setattr(probe, "prepare_config", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(probe, "session", lambda *a, **k: (
        {"tools": probe.TOOLS.split(","), "permissionMode": "dontAsk"}, {"subtype": "success", "is_error": False}))

    def fake_guest(work, profile, cli, role, proxy_url=None):
        roles.append(role)
        init = {"tools": list(probe.GUEST_TOOLS), "permissionMode": "dontAsk"}
        return init, {"subtype": "success", "is_error": False}, probe.tool_calls(events_of(held_calls(probe, role)))

    monkeypatch.setattr(probe, "guest_session", fake_guest)
    monkeypatch.setattr(probe, "bg_session", lambda *a, **k: None)
    monkeypatch.setattr(probe, "answer_phase", lambda *a, **k: None)
    monkeypatch.setattr(probe, "full_turn_phase", lambda *a, **k: None)   # a full turn makes live model calls (ADR-0180)
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
    (home / ".claude.json").write_text("{}\n")
    return home


def test_the_probe_proves_the_owners_claude_files_are_refused(tmp_path, monkeypatch):
    """#2925, #2963: the probe tries a write to the owner's settings, CLAUDE.md and global config file under the
    profile, and each is refused."""
    probe = load("operator_probe")
    home = owner_home(tmp_path)
    monkeypatch.setattr(probe, "sandboxed", scripted(probe))
    tried = probe.config_denials(tmp_path, "(version 1)(allow default)", home=home)
    assert sorted(os.path.basename(p) for p in tried) == [".claude.json", "CLAUDE.md", "settings.json"], tried
    assert (home / ".claude" / "settings.json").read_text() == "owner file\n"


def test_a_profile_that_lets_the_owners_settings_be_written_fails_the_probe(tmp_path, monkeypatch):
    probe = load("operator_probe")
    home = owner_home(tmp_path)
    monkeypatch.setattr(probe, "sandboxed", scripted(probe, config_open=True))
    with pytest.raises(probe.ProbeFailed, match="may write"):
        probe.config_denials(tmp_path, "(version 1)(allow default)", home=home)


def test_the_probe_denies_a_write_to_the_owner_global_config_file(tmp_path, monkeypatch):
    """#2963: the owner's global config file holds the MCP servers the owner's next unsandboxed session starts, so the
    probe proves the profile refuses a write to it, beside the settings files under the Claude home."""
    probe = load("operator_probe")
    home = owner_home(tmp_path)
    (home / ".claude.json").write_text("{}\n")
    monkeypatch.setattr(probe, "sandboxed", scripted(probe))
    tried = probe.config_denials(tmp_path, "(version 1)(allow default)", home=home)
    assert str(home / ".claude.json") in tried, tried


def test_the_probe_runs_its_cli_calls_under_the_operator_config_root(tmp_path, monkeypatch):
    """#2963: the CLI the probe starts keeps its runtime state under the operator's config root, and an empty
    secure-storage dir keeps the owner's keychain name, so the probe measures the bound a live session runs under."""
    probe = load("operator_probe")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    seen = {}

    def fake(profile, cli, args, cwd, timeout, extra_env=None):
        seen["env"] = extra_env or {}
        out = "\n".join([json.dumps({"type": "system", "subtype": "init", "tools": probe.TOOLS.split(","),
                                     "permissionMode": "dontAsk"}),
                         json.dumps({"type": "result", "subtype": "success", "is_error": False})]) + "\n"
        return subprocess.CompletedProcess(args, 0, out, "")

    monkeypatch.setattr(probe, "sandboxed", fake)
    probe.session(tmp_path / "checkout", "(version 1)(allow default)", "claude")
    assert seen["env"]["CLAUDE_CONFIG_DIR"] == str(probe.config_root()), seen["env"]
    assert seen["env"]["CLAUDE_SECURESTORAGE_CONFIG_DIR"] == "", seen["env"]


def test_the_probe_reads_its_roster_under_the_operator_config_root(tmp_path, monkeypatch):
    """#2963: the roster the probe lists, and the stop it sends, run under the operator's config root, the way the
    supervisor reads the same service."""
    probe = load("operator_probe")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    seen = {}

    def fake_run(argv, **kwargs):
        seen["env"] = kwargs.get("env") or {}
        return subprocess.CompletedProcess(argv, 0, "[]", "")

    monkeypatch.setattr(probe.subprocess, "run", fake_run)
    assert not probe.bg_listed("claude", "b5ccaeeb", str(tmp_path), "SQUAD-OPERATOR-x")
    assert seen["env"]["CLAUDE_CONFIG_DIR"] == str(probe.config_root()), seen["env"]


def test_the_real_profile_refuses_the_owners_claude_files(tmp_path, monkeypatch):
    """The operator's own profile, applied by the kernel, refuses both writes on this machine."""
    probe = load("operator_probe")
    if sys.platform != "darwin" or not os.path.exists("/usr/bin/sandbox-exec"):
        pytest.skip("the kernel bound is macOS only")
    profile = probe.profile_text([str(tmp_path / "out.jsonl")], str(tmp_path / "inboxes"), [],
                                 config=str(tmp_path / "config"))
    home = os.path.realpath(os.path.expanduser("~"))
    tried = probe.config_denials(tmp_path, profile, home=home)
    assert tried, "this machine holds no owner Claude file to try"


# #2949: the fixed answer path. One live session answers one pending inbox line through the shipped helper, and no
# other channel's outbox takes a line.

def answer_folders(tmp_path, probe):
    """The queues one answer phase works over: one inbox and one outbox per channel of the probe's project."""
    outboxes = [tmp_path / "state" / "outboxes" / f"{c}.jsonl" for c in probe.PROBE_CHANNELS]
    own = [tmp_path / "state" / "inboxes" / f"{c}.jsonl" for c in probe.PROBE_CHANNELS]
    for path in outboxes + own:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
    return outboxes, own


def test_the_answer_prompt_names_the_shipped_helper():
    """#2949 criterion (6): the live prompt runs the helper exactly, with the scope, the thread and the text."""
    probe = load("operator_probe")
    command = [sys.executable, str(probe.HELPER), "--scope", probe.PROBE_CHANNELS[0],
               "--thread", probe.PROBE_THREAD, "--text", probe.ANSWER_TEXT]
    text = probe.answer_prompt(command)
    assert str(probe.HELPER) in text and "--scope C0PROBE01" in text, text
    assert "Bash tool" in text and "no change to any argument" in text, text


def helper_command(probe):
    """The one command the live session is asked to run."""
    return f'{sys.executable} {probe.HELPER} --scope {probe.PROBE_CHANNELS[0]} --thread {probe.PROBE_THREAD} --text "{probe.ANSWER_TEXT}"'


def answer_event(probe, command=None):
    """The stream-json events of a session whose one Bash call runs the shipped helper."""
    return events_of([("Bash", {"command": command or helper_command(probe)}, "answer: wrote one line to the outbox")])


def test_the_answer_phase_proves_a_live_session_answered_through_the_helper(tmp_path, monkeypatch):
    """#2949 criterion (6): the live session's one Bash call runs the shipped helper, and its line is the one answer."""
    probe = load("operator_probe")
    outboxes, own = answer_folders(tmp_path, probe)
    seen = {}

    def fake_cli(work, profile, cli, args, proxy_url=None, extra_env=None):
        seen["env"] = extra_env
        seen["question"] = own[0].read_text()
        with open(outboxes[0], "a", encoding="utf-8") as f:
            f.write(json.dumps({"thread_ts": probe.PROBE_THREAD, "text": probe.ANSWER_TEXT}) + "\n")
        return {}, {}, answer_event(probe)

    monkeypatch.setattr(probe, "cli_call", fake_cli)
    probe.answer_phase(tmp_path, "(version 1)", "/bin/true", outboxes, own)

    binding = json.loads(Path(seen["env"]["VOICE_ANSWER_BINDING"]).read_text())
    assert binding["channels"] == list(probe.PROBE_CHANNELS), binding
    assert probe.PROBE_THREAD in seen["question"], seen["question"]


def test_a_session_that_writes_the_line_without_the_helper_fails_the_probe(tmp_path, monkeypatch):
    """#2949 criterion (6): a line the session appends itself, with no helper call, proves nothing."""
    probe = load("operator_probe")
    outboxes, own = answer_folders(tmp_path, probe)

    def fake_cli(work, profile, cli, args, proxy_url=None, extra_env=None):
        with open(outboxes[0], "a", encoding="utf-8") as f:
            f.write(json.dumps({"thread_ts": probe.PROBE_THREAD, "text": probe.ANSWER_TEXT}) + "\n")
        return {}, {}, []

    monkeypatch.setattr(probe, "cli_call", fake_cli)
    with pytest.raises(probe.ProbeFailed, match="Bash"):
        probe.answer_phase(tmp_path, "(version 1)", "/bin/true", outboxes, own)


def test_a_session_whose_own_command_writes_the_outbox_fails_the_probe(tmp_path, monkeypatch):
    """#2949 criterion (6): the one Bash call must run the helper, never append to the outbox by hand."""
    probe = load("operator_probe")
    outboxes, own = answer_folders(tmp_path, probe)

    def fake_cli(work, profile, cli, args, proxy_url=None, extra_env=None):
        with open(outboxes[0], "a", encoding="utf-8") as f:
            f.write(json.dumps({"thread_ts": probe.PROBE_THREAD, "text": probe.ANSWER_TEXT}) + "\n")
        return {}, {}, answer_event(probe, f'echo "{probe.ANSWER_TEXT}" >> "{outboxes[0]}"')

    monkeypatch.setattr(probe, "cli_call", fake_cli)
    with pytest.raises(probe.ProbeFailed, match="does not run the helper"):
        probe.answer_phase(tmp_path, "(version 1)", "/bin/true", outboxes, own)


def test_a_session_that_runs_two_bash_calls_fails_the_probe(tmp_path, monkeypatch):
    """#2949 criterion (6): one answer is one helper call, so a second Bash call is unproven."""
    probe = load("operator_probe")
    outboxes, own = answer_folders(tmp_path, probe)
    events = answer_event(probe) + answer_event(probe)

    def fake_cli(work, profile, cli, args, proxy_url=None, extra_env=None):
        return {}, {}, events

    monkeypatch.setattr(probe, "cli_call", fake_cli)
    with pytest.raises(probe.ProbeFailed, match="2 Bash calls"):
        probe.answer_phase(tmp_path, "(version 1)", "/bin/true", outboxes, own)


def test_the_answer_phase_keeps_the_denial_control_lines(tmp_path, monkeypatch):
    """#2949 criterion (6): the answer phase never erases a line an earlier phase's verdict rests on."""
    probe = load("operator_probe")
    outboxes, own = answer_folders(tmp_path, probe)
    for outbox in outboxes:
        outbox.write_text("answered\n")          # each denial phase's own control line

    def fake_cli(work, profile, cli, args, proxy_url=None, extra_env=None):
        with open(outboxes[0], "a", encoding="utf-8") as f:
            f.write(json.dumps({"thread_ts": probe.PROBE_THREAD, "text": probe.ANSWER_TEXT}) + "\n")
        return {}, {}, answer_event(probe)

    monkeypatch.setattr(probe, "cli_call", fake_cli)
    probe.answer_phase(tmp_path, "(version 1)", "/bin/true", outboxes, own)

    assert outboxes[0].read_text().startswith("answered\n")
    assert outboxes[1].read_text() == "answered\n"


def test_a_live_session_that_answers_no_pending_line_fails_the_probe(tmp_path, monkeypatch):
    """#2949 criterion (6): a session that writes nothing leaves the probe unproven, never passed."""
    probe = load("operator_probe")
    outboxes, own = answer_folders(tmp_path, probe)

    def fake_cli(work, profile, cli, args, proxy_url=None, extra_env=None):
        return {}, {}, answer_event(probe)

    monkeypatch.setattr(probe, "cli_call", fake_cli)
    with pytest.raises(probe.ProbeFailed, match="did not answer through the helper"):
        probe.answer_phase(tmp_path, "(version 1)", "/bin/true", outboxes, own)


def test_an_answer_in_another_channels_outbox_fails_the_probe(tmp_path, monkeypatch):
    """#2949 criterion (6): a line outside the question's channel is a refusal the probe names."""
    probe = load("operator_probe")
    outboxes, own = answer_folders(tmp_path, probe)

    def fake_cli(work, profile, cli, args, proxy_url=None, extra_env=None):
        for outbox in outboxes:
            with open(outbox, "a", encoding="utf-8") as f:
                f.write(json.dumps({"thread_ts": probe.PROBE_THREAD, "text": probe.ANSWER_TEXT}) + "\n")
        return {}, {}, answer_event(probe)

    monkeypatch.setattr(probe, "cli_call", fake_cli)
    with pytest.raises(probe.ProbeFailed, match="outside the question's channel"):
        probe.answer_phase(tmp_path, "(version 1)", "/bin/true", outboxes, own)


def test_the_answer_binding_is_written_before_the_profile_is_built(tmp_path, monkeypatch):
    """#2949 criterion (6): the live session's answer binding exists before the profile it runs under is built, so the
    helper's own read never meets a file the probe created outside the profile's own scaffolding."""
    probe = load("operator_probe")
    seen = {}
    real = probe.project_shape

    def spy(work, ports):
        seen["binding"] = (Path(work) / "answer" / "binding.json").is_file()
        return real(work, ports)

    monkeypatch.setattr(probe, "project_shape", spy)
    _, _, _, _, _, _, _, profile = probe.profile_for(tmp_path, [8016])

    assert seen["binding"] is True, "the answer binding was written after the profile was built"
    binding = json.loads((tmp_path / "answer" / "binding.json").read_text())
    assert binding["channels"] == list(probe.PROBE_CHANNELS), binding
    assert binding["state"] == str(tmp_path / "state"), binding
    assert profile.startswith("(version 1)")


# The answer phase's own proof (#2949 review): the one Bash call is exactly the helper's argv and exits 0, and an
# earlier phase's JSON line in another outbox never reads as this session's stray answer.

def expected_helper_argv(probe):
    return [sys.executable, str(probe.HELPER), "--scope", probe.PROBE_CHANNELS[0], "--thread", probe.PROBE_THREAD,
            "--text", probe.ANSWER_TEXT]


def test_the_answer_call_must_be_exactly_the_helper_argv():
    """A command that carries the helper's substrings plus another command proves nothing: the argv is parsed and
    compared whole."""
    probe = load("operator_probe")
    command = f'{helper_command(probe)} ; rm -rf "/tmp/leak"'
    calls = probe.tool_calls(events_of([("Bash", {"command": command}, "answer: wrote one line")]))

    with pytest.raises(probe.ProbeFailed, match="exactly"):
        probe.check_answer_call(calls, expected_helper_argv(probe))


def test_the_answer_call_must_run_the_helper_with_its_exact_arguments():
    """The exact argv passes; one changed argument is refused."""
    probe = load("operator_probe")
    good = probe.tool_calls(events_of([("Bash", {"command": helper_command(probe)}, "answer: wrote one line")]))
    assert probe.check_answer_call(good, expected_helper_argv(probe))

    changed = f'{helper_command(probe).replace(probe.PROBE_THREAD, "1.1")}'
    calls = probe.tool_calls(events_of([("Bash", {"command": changed}, "answer: wrote one line")]))
    with pytest.raises(probe.ProbeFailed, match="exactly"):
        probe.check_answer_call(calls, expected_helper_argv(probe))


def test_a_helper_call_that_exits_nonzero_fails_the_probe():
    """The Bash tool_result carries the call's own failure, so a helper that refused its line proves nothing."""
    probe = load("operator_probe")
    events = [
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "toolu_0", "name": "Bash", "input": {"command": helper_command(probe)}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "toolu_0", "is_error": True,
             "content": [{"type": "text", "text": "answer: refused: no pending inbox line"}]}]}},
    ]
    with pytest.raises(probe.ProbeFailed, match="exit"):
        probe.check_answer_call(probe.tool_calls(events), expected_helper_argv(probe))


def test_an_earlier_json_line_in_another_outbox_does_not_fail_the_phase(tmp_path, monkeypatch):
    """The phase compares each other outbox against its own earlier content, so a JSON control line an earlier phase
    wrote is no answer of this session."""
    probe = load("operator_probe")
    outboxes, own = answer_folders(tmp_path, probe)
    outboxes[1].write_text(json.dumps({"control": "denial phase"}) + "\n")

    def fake_cli(work, profile, cli, args, proxy_url=None, extra_env=None):
        with open(outboxes[0], "a", encoding="utf-8") as f:
            f.write(json.dumps({"thread_ts": probe.PROBE_THREAD, "text": probe.ANSWER_TEXT}) + "\n")
        return {}, {}, answer_event(probe)

    monkeypatch.setattr(probe, "cli_call", fake_cli)
    probe.answer_phase(tmp_path, "(version 1)", "/bin/true", outboxes, own)


def test_the_answer_phase_leaves_the_inbox_as_it_found_it(tmp_path, monkeypatch):
    """Blocking 1 (#2949): `bg_session` and `guest_sessions` reuse this state folder, so the synthetic question the
    phase appends must be gone when the phase returns: a later phase reads the queue it created, never this one."""
    probe = load("operator_probe")
    outboxes, own = answer_folders(tmp_path, probe)
    own[0].write_text(json.dumps({"id": "c:x", "ts": "1.0", "channel": probe.PROBE_CHANNELS[0], "text": "earlier"}) + "\n")
    before = own[0].read_bytes()

    def fake_cli(work, profile, cli, args, proxy_url=None, extra_env=None):
        with open(outboxes[0], "a", encoding="utf-8") as f:
            f.write(json.dumps({"thread_ts": probe.PROBE_THREAD, "text": probe.ANSWER_TEXT}) + "\n")
        return {}, {}, answer_event(probe)

    monkeypatch.setattr(probe, "cli_call", fake_cli)
    probe.answer_phase(tmp_path, "(version 1)", "/bin/true", outboxes, own)

    assert own[0].read_bytes() == before


def test_the_answer_phase_restores_the_inbox_when_the_phase_fails(tmp_path, monkeypatch):
    """Blocking 1 (#2949): a failed phase restores the inbox too, so the phase after it never reads a stray question."""
    probe = load("operator_probe")
    outboxes, own = answer_folders(tmp_path, probe)
    before = own[0].read_bytes()

    def fake_cli(work, profile, cli, args, proxy_url=None, extra_env=None):
        return {}, {}, []

    monkeypatch.setattr(probe, "cli_call", fake_cli)
    with pytest.raises(probe.ProbeFailed, match="Bash"):
        probe.answer_phase(tmp_path, "(version 1)", "/bin/true", outboxes, own)

    assert own[0].read_bytes() == before


def test_a_line_the_daemon_appends_during_the_phase_is_never_truncated(tmp_path, monkeypatch):
    """Debt 2 (#2949): the phase deletes only the bytes it appended. When the daemon appends a real line during the
    probe, the inbox no longer ends at the phase's own question, so the phase fails and leaves that line in place."""
    probe = load("operator_probe")
    outboxes, own = answer_folders(tmp_path, probe)
    real = json.dumps({"id": "c:real", "ts": "9.0", "channel": probe.PROBE_CHANNELS[0], "text": "a real message"})

    def fake_cli(work, profile, cli, args, proxy_url=None, extra_env=None):
        with open(own[0], "a", encoding="utf-8") as f:
            f.write(real + "\n")          # the daemon's own line, appended while the session answered
        with open(outboxes[0], "a", encoding="utf-8") as f:
            f.write(json.dumps({"thread_ts": probe.PROBE_THREAD, "text": probe.ANSWER_TEXT}) + "\n")
        return {}, {}, answer_event(probe)

    monkeypatch.setattr(probe, "cli_call", fake_cli)
    with pytest.raises(probe.ProbeFailed, match="appended to the inbox"):
        probe.answer_phase(tmp_path, "(version 1)", "/bin/true", outboxes, own)

    assert real in own[0].read_text(), "the phase deleted a line the daemon wrote"


def test_the_operator_note_names_the_proposal_prefix_and_its_kind_token():
    """Debt 3 and Debt 5 (#2949): the DM proposal's second call passes `--kind proposal` and its text starts with
    `Proposal:`, so the operator note must name both the prefix and the token."""
    probe = load("operator_probe")
    note = Path(probe.__file__).with_name("OPERATOR.md").read_text()

    assert "Proposal:" in note and "--kind proposal" in note, note


def test_the_answer_phase_drives_the_real_helper_end_to_end(tmp_path, monkeypatch):
    """Debt 4 (#2949): `cli_call` is stubbed here, so this case proves the shipped helper under the phase's own
    binding (no model); it does not prove that the live session's Bash tool ran the helper. Only the live phase does."""
    probe = load("operator_probe")
    outboxes, own = answer_folders(tmp_path, probe)
    command = [sys.executable, str(probe.HELPER), "--scope", probe.PROBE_CHANNELS[0], "--thread", probe.PROBE_THREAD,
               "--text", probe.ANSWER_TEXT]
    seen = {}

    def stub_cli(work, profile, cli, args, proxy_url=None, extra_env=None):
        seen["binding"] = json.loads(Path(extra_env["VOICE_ANSWER_BINDING"]).read_text())
        done = subprocess.run(command, capture_output=True, text=True, timeout=30,
                              env={**os.environ, **extra_env}, check=False)
        assert done.returncode == 0, done.stderr
        text = done.stdout.strip() or "answer: wrote one line"
        return {}, {}, events_of([("Bash", {"command": shlex.join(command)}, text)])

    monkeypatch.setattr(probe, "cli_call", stub_cli)
    probe.answer_phase(tmp_path, "(version 1)", "/bin/true", outboxes, own)

    assert seen["binding"]["channels"] == list(probe.PROBE_CHANNELS), seen["binding"]
    got = [json.loads(raw) for raw in outboxes[0].read_text().splitlines() if raw.strip()]
    assert got == [{"thread_ts": probe.PROBE_THREAD, "text": probe.ANSWER_TEXT}], got


def test_a_quoted_answer_renders_a_prompt_that_parses_back_to_its_argv():
    """Debt 3 (#2949): the probe asks the session to run the helper as one shell line, so an answer with a quote, a
    backtick or `$(...)` must survive the prompt unchanged."""
    probe = load("operator_probe")
    command = [sys.executable, str(probe.HELPER), "--scope", probe.PROBE_CHANNELS[0], "--thread", probe.PROBE_THREAD,
               "--text", "It's a \"quote\", a `tick` and $(rm -rf /)"]

    text = probe.answer_prompt(command)

    assert shlex.split(text.splitlines()[-1]) == command, text


# The answer phase without a model (#2949 review, Debt 4): the probe's question-append, outbox check and inbox restore
# run against the real shipped helper, so pytest proves the shape the live probe only proves with a model.

def test_the_answer_phase_runs_the_real_helper_without_a_model(tmp_path):
    """Debt 4 (#2949): `exercise_answer` appends its question under a lock, runs the real `answer` helper as the
    subprocess the session would run, and checks the one threaded outbox line, then restores the inbox — all with no
    model and no live session."""
    probe = load("operator_probe")
    checkout, outboxes, own_inboxes, *_ = probe.project_shape(tmp_path, [8000])
    binding = probe.write_binding(tmp_path)
    inbox_before = own_inboxes[0].read_text()

    def run():
        result = subprocess.run(
            [sys.executable, str(probe.HELPER), "--scope", probe.PROBE_CHANNELS[0], "--thread", probe.PROBE_THREAD,
             "--text", probe.ANSWER_TEXT],
            env={**os.environ, "VOICE_ANSWER_BINDING": str(binding)}, capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr

    answers = probe.exercise_answer(outboxes, own_inboxes[0], run)

    got = [json.loads(raw) for raw in outboxes[0].read_text().splitlines() if raw.strip()]
    assert got == [{"thread_ts": probe.PROBE_THREAD, "text": probe.ANSWER_TEXT}], got
    assert answers == got
    assert outboxes[1].read_text() == "", "the other channel's outbox took a line"
    assert own_inboxes[0].read_text() == inbox_before, "the phase left its question in the inbox"


def test_the_answer_phase_appends_its_question_under_an_exclusive_lock(tmp_path):
    """Blocking 3 (#2949): the synthetic question is appended through one helper that takes the same exclusive flock
    `restore_inbox` takes, never a bare unlocked `open(..., 'ab')`, so a concurrent appender never meets a torn line."""
    import inspect
    probe = load("operator_probe")
    body = inspect.getsource(probe.append_question)
    assert "flock" in body and "LOCK_EX" in body, body
    phase = inspect.getsource(probe.exercise_answer)
    assert "append_question(" in phase and "open(inbox" not in phase, phase

    inbox = tmp_path / "inbox.jsonl"
    inbox.write_text("before\n")
    probe.append_question(str(inbox), b"X\n")
    assert inbox.read_text() == "before\nX\n"


# The tool gate (#2950): the live phases run under the gate's `--settings`, and the gate decides the calls.

def test_the_gated_answer_phase_runs_past_an_ambient_hook_and_removes_it(tmp_path, monkeypatch):
    """#2950 criterion 11: the live call runs with the gate's `--settings` and an ambient project hook that rewrites
    every allowed Bash call; the answer line lands, the rewrite reaches no event, and the file leaves with the phase."""
    probe = load("operator_probe")
    outboxes, own = answer_folders(tmp_path, probe)
    seen = {}

    def fake_cli(work, profile, cli, args, proxy_url=None, extra_env=None):
        seen["args"] = list(args)
        ambient = tmp_path / ".claude" / "settings.json"
        seen["ambient"] = ambient.read_text() if ambient.exists() else ""
        with open(outboxes[0], "a", encoding="utf-8") as f:
            f.write(json.dumps({"thread_ts": probe.PROBE_THREAD, "text": probe.ANSWER_TEXT}) + "\n")
        return {}, {}, answer_event(probe)

    monkeypatch.setattr(probe, "cli_call", fake_cli)
    probe.answer_phase(tmp_path, "(version 1)", "/bin/true", outboxes, own, gate='{"hooks": {}}')

    assert seen["args"][seen["args"].index("--settings") + 1] == '{"hooks": {}}', seen["args"]
    assert "tamper.py" in seen["ambient"], seen["ambient"]
    assert probe.GATE_TAMPER in (tmp_path / "gate" / "tamper.py").read_text()
    assert not (tmp_path / ".claude" / "settings.json").exists(), "the phase left its ambient hook beside the cwd"


def test_an_ambient_rewrite_that_reached_the_tool_fails_the_probe(tmp_path, monkeypatch):
    """#2950 criterion 11: a rewrite that reached the tool means the executed command was not the judged one, so the
    probe fails even though an answer line landed."""
    probe = load("operator_probe")
    outboxes, own = answer_folders(tmp_path, probe)

    def fake_cli(work, profile, cli, args, proxy_url=None, extra_env=None):
        with open(outboxes[0], "a", encoding="utf-8") as f:
            f.write(json.dumps({"thread_ts": probe.PROBE_THREAD, "text": probe.ANSWER_TEXT}) + "\n")
        return {}, {}, events_of([("Bash", {"command": helper_command(probe)}, probe.GATE_TAMPER)])

    monkeypatch.setattr(probe, "cli_call", fake_cli)
    with pytest.raises(probe.ProbeFailed, match=probe.GATE_TAMPER):
        probe.answer_phase(tmp_path, "(version 1)", "/bin/true", outboxes, own, gate='{"hooks": {}}')


def bg_roster(argv):
    return subprocess.CompletedProcess(
        argv, 0, '[{"sessionId": "ba6e7d2e", "name": "SQUAD-OPERATOR-probe-project", "cwd": "/w"}]', "")


def bg_started(argv):
    return subprocess.CompletedProcess(argv, 0, "backgrounded · ba6e7d2e\n", "")


def test_a_gated_bg_session_runs_under_the_gate_and_needs_its_audit_line(tmp_path, monkeypatch):
    """#2950 criterion 1: the `--bg` session carries the gate's `--settings`, and the audit line of the one call the
    gate must refuse proves the hook decided a call inside that session itself."""
    probe = load("operator_probe")
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        if argv[0] == "claude" and len(argv) > 1 and argv[1] == "agents":
            return bg_roster(argv)
        if argv[0] == "claude" and len(argv) > 1 and argv[1] == "stop":
            return subprocess.CompletedProcess(argv, 0, "", "")
        audit = Path(probe.denials_path(str(tmp_path / "state")))
        audit.write_text(json.dumps({"at": "t", "tool": "Bash", "decision": "deny", "reason": "Bash runs only the helper",
                                     "call": probe.GATE_DENIED}) + "\n")
        return bg_started(argv)

    monkeypatch.setattr(probe.subprocess, "run", fake_run)
    probe.bg_session(tmp_path, "(version 1)(allow default)", "claude", gate='{"hooks": {}}')
    started = next(argv for argv in calls if len(argv) > 1 and argv[1] == "--bg")
    assert started[started.index("--settings") + 1] == '{"hooks": {}}', started
    assert "Bash" in started[started.index("--tools") + 1], started


def test_a_gated_bg_session_with_no_audit_line_is_unproven(tmp_path, monkeypatch):
    """#2950 criterion 1: a `--bg` session whose denied call left no audit line proves no hook ran there."""
    probe = load("operator_probe")
    monkeypatch.setattr(probe, "GATE_DEADLINE", 0)

    def fake_run(argv, **kwargs):
        if argv[0] == "claude" and len(argv) > 1 and argv[1] == "agents":
            return bg_roster(argv)
        return bg_started(argv)

    monkeypatch.setattr(probe.subprocess, "run", fake_run)
    with pytest.raises(probe.ProbeFailed, match="no audit line"):
        probe.bg_session(tmp_path, "(version 1)(allow default)", "claude", gate='{"hooks": {}}')


def test_a_gated_bg_session_reads_the_audit_file_the_gate_names_not_one_under_the_checkout(tmp_path, monkeypatch):
    """#2987: the session runs in the checkout, but the gate's policy names the audit file under the probe's state
    folder. A line in that file proves the denial; a path built from the checkout reads another, empty file."""
    probe = load("operator_probe")
    monkeypatch.setattr(probe, "GATE_DEADLINE", 0.5)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    audit = probe.gate_audit(tmp_path)

    def fake_run(argv, **kwargs):
        if argv[0] == "claude" and len(argv) > 1 and argv[1] == "agents":
            return bg_roster(argv)
        if argv[0] == "claude" and len(argv) > 1 and argv[1] == "stop":
            return subprocess.CompletedProcess(argv, 0, "", "")
        audit.parent.mkdir(parents=True, exist_ok=True)
        audit.write_text(json.dumps({"at": "t", "tool": "Bash", "decision": "deny", "reason": "Bash runs only the helper",
                                     "call": probe.GATE_DENIED}) + "\n")
        return bg_started(argv)

    monkeypatch.setattr(probe.subprocess, "run", fake_run)
    probe.bg_session(checkout, "(version 1)(allow default)", "claude", gate='{"hooks": {}}', audit=audit)
    assert not Path(probe.denials_path(str(checkout / "state"))).exists(), "the probe touched a file the gate never names"


def test_the_probe_hands_the_bg_session_the_audit_file_of_its_gate(tmp_path, monkeypatch):
    """#2987: `run_probe` passes the `--bg` phase the audit file its profile grants, under the probe's state folder,
    while the session itself runs in the checkout."""
    probe = load("operator_probe")
    seen = {}
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8016")
    monkeypatch.setattr(probe, "denials", lambda *a, **k: (False, False, False))
    monkeypatch.setattr(probe, "config_denials", lambda *a, **k: [], raising=False)
    monkeypatch.setattr(probe, "prepare_config", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(probe, "check_denials", lambda *a, **k: None)
    monkeypatch.setattr(probe, "session", lambda *a, **k: (
        {"tools": probe.TOOLS.split(","), "permissionMode": "dontAsk"}, {"subtype": "success", "is_error": False}))
    monkeypatch.setattr(probe, "check_session", lambda *a, **k: None)

    def fake_bg(work, profile, cli, proxy_url=None, **kwargs):
        seen["work"], seen["audit"] = Path(work), kwargs.get("audit")

    monkeypatch.setattr(probe, "bg_session", fake_bg)
    monkeypatch.setattr(probe, "guest_sessions", lambda *a, **k: None)
    monkeypatch.setattr(probe, "answer_phase", lambda *a, **k: None)
    monkeypatch.setattr(probe, "service_denial", lambda *a, **k: None)
    if hasattr(probe, "full_turn_phase"):
        monkeypatch.setattr(probe, "full_turn_phase", lambda *a, **k: None)
    probe.run_probe(tmp_path, "/bin/true")
    assert seen["audit"] is not None and Path(seen["audit"]) == Path(probe.denials_path(str(tmp_path / "state")))
    assert Path(seen["audit"]) != Path(probe.denials_path(str(seen["work"] / "state"))), seen
