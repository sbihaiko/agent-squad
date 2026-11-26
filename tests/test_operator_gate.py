"""Guard: issue #2950. One `PreToolUse` hook decides every tool call of a project operator session.

Every case drives the hook as a script (`contrib/voice/operator_gate.py`) with the CLI's event JSON on stdin and the
policy file as its argument, so the test runs the one path production runs: no fake decision function stands between the
event and the answer. The policy is one JSON file the supervisor writes at the session start. One test per acceptance
criterion of #2950.
"""
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VOICE = ROOT / "contrib" / "voice"
GATE = VOICE / "operator_gate.py"
ANSWER = VOICE / "answer.py"
INBOX = "C0AAAAAA1"
OTHER_INBOX = "C0BBBBBB2"
CHECKOUT = "checkout"
OTHER_CHECKOUT = "other-checkout"


def tree(tmp_path):
    """One session's folders: its checkout, another project's checkout, one inbox per channel, and the audit file."""
    checkout = tmp_path / CHECKOUT
    other = tmp_path / OTHER_CHECKOUT
    inboxes = tmp_path / "state" / "inboxes"
    for folder in (checkout, other, inboxes):
        folder.mkdir(parents=True)
    own = inboxes / f"{INBOX}.jsonl"
    own.write_text("")
    (inboxes / f"{OTHER_INBOX}.jsonl").write_text("")
    return {"checkout": str(checkout), "other": str(other), "inboxes": [str(own)],
            "audit": str(tmp_path / "state" / "operators" / "denials.jsonl")}


def policy(tree, wrappers=()):
    """The policy document the supervisor writes for one session: its checkout, its own inboxes and nothing else."""
    return {"checkout": tree["checkout"], "inboxes": tree["inboxes"], "wrappers": list(wrappers), "answer": str(ANSWER),
            "others": [tree["other"]], "audit": tree["audit"]}


def run(tmp_path, tree, event, doc=None, name="policy.json"):
    """The hook as a script: the policy as a file, the event JSON on stdin. Returns the process."""
    path = tmp_path / name
    path.write_text(json.dumps(policy(tree) if doc is None else doc))
    return subprocess.run([sys.executable, str(GATE), str(path)], input=json.dumps(event),
                          capture_output=True, text=True, timeout=30, check=False)


def answer(done):
    """The hook's answer, as the CLI reads it: its exit code and the `hookSpecificOutput` it printed."""
    assert done.returncode == 0, f"the hook exited {done.returncode}: {done.stderr}"
    return json.loads(done.stdout)["hookSpecificOutput"]


def call(done):
    """One decision as `(allowed, reason, input)`, whatever shape the hook answers with."""
    out = answer(done)
    return out["permissionDecision"] == "allow", out.get("permissionDecisionReason") or "", out.get("updatedInput") or {}


def read_event(path):
    return {"tool_name": "Read", "tool_input": {"file_path": str(path)}}


def bash_event(command):
    return {"tool_name": "Bash", "tool_input": {"command": command, "description": "probe"}}


def monitor_event(command):
    return {"tool_name": "Monitor", "tool_input": {"command": command, "description": "inbox", "timeout_ms": 1000}}


def audit_lines(tree):
    path = Path(tree["audit"])
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


# Criterion 1: the hook attaches to the `--bg` project session.

def operators_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("operators", VOICE / "operators.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_gate_attaches_to_the_bg_session(tmp_path, monkeypatch):
    op = operators_module()
    argvs = []

    def fake_call(argv, cwd=None, timeout=None, env=None):
        argvs.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "aaaaaaaa-0000-0000-0000-000000000001\n", "")

    monkeypatch.setattr(op, "call", fake_call)
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    pinned = state / "operators" / "sessions"
    pinned.mkdir(parents=True)
    (pinned / "dm.json").write_text(json.dumps({"dm": "D0AAAAAA1"}))   # a DM start binds the pinned record
    op.start("dm", str(state), str(repo))
    argv = argvs[0]
    assert argv[0] == op.WRAPPER and argv[1] == "--bg", argv[:3]
    settings = json.loads(argv[argv.index("--settings") + 1])
    command = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert str(GATE) in command, command
    policy_path = Path(command.split()[-1])
    assert policy_path.is_file(), f"the gate names no policy file: {command}"
    doc = json.loads(policy_path.read_text())
    assert doc["checkout"] == os.path.realpath(repo), doc
    assert doc["inboxes"] == [os.path.realpath(str(state / "inboxes" / "dm.jsonl"))], doc
    assert doc["audit"].startswith(str(state)), doc
    assert doc["answer"] == str(ANSWER), doc


def test_the_gate_of_a_project_session_declares_its_wrapper_and_inbox(tmp_path, monkeypatch):
    op = operators_module()
    argvs = []

    def fake_call(argv, cwd=None, timeout=None, env=None):
        argvs.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "aaaaaaaa-0000-0000-0000-000000000002\n", "")

    monkeypatch.setattr(op, "call", fake_call)
    repo = tmp_path / "repo"
    checkout = tmp_path / "project"
    repo.mkdir()
    checkout.mkdir()
    state = tmp_path / "state"
    record = Path(op.voiced.slack_record_path(str(state), "sbihaiko/project"))
    record.parent.mkdir(parents=True)
    wrapper = f"{checkout / 'scripts' / 'read-status.sh'} --short"
    record.write_text(json.dumps({"revision": {"guard": {"read_wrappers": [wrapper]},
                                               "workspaces": [{"channels": [{"id": INBOX}]}]}}))
    unit = {"key": "project", "repo": "sbihaiko/project", "checkout": str(checkout), "channels": [INBOX]}
    op.start(unit, str(state), str(repo))
    argv = argvs[0]
    assert argv[0] == op.WRAPPER and argv[1] == "--bg", argv[:3]
    settings = json.loads(argv[argv.index("--settings") + 1])
    command = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    doc = json.loads(Path(command.split()[-1]).read_text())
    assert doc["wrappers"] == [wrapper], doc
    assert doc["inboxes"] == [os.path.realpath(str(state / "inboxes" / f"{INBOX}.jsonl"))], doc
    assert doc["checkout"] == os.path.realpath(checkout), doc


def test_the_kernel_profile_lets_the_gate_write_its_audit(tmp_path):
    op = operators_module()
    audit = str(tmp_path / "state" / "operators" / "denials.jsonl")
    profile = op.profile_text([str(tmp_path / "out.jsonl")], str(tmp_path / "inboxes"),
                              [str(tmp_path / "inboxes" / "dm.jsonl")], checkout=str(tmp_path / "checkout"),
                              ports=[80], config=str(tmp_path / "config"), audit=audit)
    write_rule = next(line for line in profile.splitlines() if line.startswith("(allow file-write*"))
    assert f'(literal "{audit}")' in write_rule, write_rule


# Criterion 2: the hook decides every tool call, and a tool it does not name is denied.

def test_a_tool_the_gate_does_not_name_is_denied(tmp_path):
    tree_ = tree(tmp_path)
    for name in ("WebFetch", "Grep", "Glob", "Write", "Edit", "Task", "mcp__slack__slack_send_message"):
        allowed, reason, _ = call(run(tmp_path, tree_, {"tool_name": name, "tool_input": {}}))
        assert not allowed, f"{name} passed the gate"
        assert "not a tool this session names" in reason, reason


def test_the_named_read_tools_are_allowed(tmp_path):
    tree_ = tree(tmp_path)
    for name in ("TaskStop",):
        allowed, reason, _ = call(run(tmp_path, tree_, {"tool_name": name, "tool_input": {"task_id": "t1"}}))
        assert allowed, f"{name} was refused: {reason}"


# Criterion 3: Monitor only as the exact tail command of the session's own inboxes.

def test_monitor_allows_the_exact_tail_of_an_own_inbox(tmp_path):
    tree_ = tree(tmp_path)
    command = f"tail -n 0 -F {tree_['inboxes'][0]}"
    allowed, reason, given = call(run(tmp_path, tree_, monitor_event(command)))
    assert allowed, reason
    assert given["command"] == command, given


def test_monitor_denies_any_other_command(tmp_path):
    tree_ = tree(tmp_path)
    other = str(Path(tree_["inboxes"][0]).with_name(f"{OTHER_INBOX}.jsonl"))
    for command in (f"tail -n 5 -F {tree_['inboxes'][0]}", f"cat {tree_['inboxes'][0]}",
                    f"tail -n 0 -F {other}", f"tail -n 0 -F {tree_['inboxes'][0]} {other}",
                    "tail -n 0 -F /etc/passwd", f"sh -c 'tail -n 0 -F {tree_['inboxes'][0]}'"):
        allowed, reason, _ = call(run(tmp_path, tree_, monitor_event(command)))
        assert not allowed, f"Monitor allowed {command!r}"
        assert "Monitor" in reason, reason


# Criteria 4 and 5: Read only inside the project checkout, minus the secret list.

def test_read_allows_a_path_inside_the_project_checkout(tmp_path):
    tree_ = tree(tmp_path)
    inside = Path(tree_["checkout"]) / "README.md"
    inside.write_text("hello")
    allowed, reason, given = call(run(tmp_path, tree_, read_event(inside)))
    assert allowed, reason
    assert given["file_path"] == str(inside), given


def test_read_denies_a_path_outside_the_project_checkout(tmp_path):
    tree_ = tree(tmp_path)
    outside = Path(tree_["checkout"]).parent / "elsewhere.txt"
    outside.write_text("x")
    for path in (outside, Path(tree_["other"]) / "README.md", Path("/etc/passwd")):
        allowed, reason, _ = call(run(tmp_path, tree_, read_event(path)))
        assert not allowed, f"Read allowed {path}"
        assert "outside the project checkout" in reason or "another project checkout" in reason, reason


def test_read_denies_a_secret_name_in_any_path_component(tmp_path):
    tree_ = tree(tmp_path)
    checkout = Path(tree_["checkout"])
    for path in (checkout / ".env" / "config.txt", checkout / "sub" / ".env.production" / "notes.txt",
                 checkout / "config" / "slack.local.yaml" / "notes.txt",
                 checkout / "docs" / "team.slack.local.yaml" / "notes.txt"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
        allowed, reason, _ = call(run(tmp_path, tree_, read_event(path)))
        assert not allowed, f"Read allowed {path}"
        assert "secret" in reason, reason
    plain = checkout / "docs" / "environment.md"
    plain.write_text("x")
    allowed, reason, _ = call(run(tmp_path, tree_, read_event(plain)))
    assert allowed, reason


def test_read_denies_the_secret_list(tmp_path, monkeypatch):
    home = tmp_path / "home"
    for name in (".ssh", ".config/gh", ".aws"):
        (home / name).mkdir(parents=True)
    secret = home / ".ssh" / "id_rsa"
    secret.write_text("key")
    monkeypatch.setenv("HOME", str(home))
    tree_ = tree(tmp_path)
    checkout = Path(tree_["checkout"])
    paths = [checkout / ".env", checkout / ".env.local", checkout / "docs.slack.local.yaml",
             checkout / "slack.local.yaml",
             home / ".ssh" / "id_rsa", home / ".config" / "gh" / "hosts.yml", home / ".aws" / "credentials"]
    for path in paths:
        allowed, reason, _ = call(run(tmp_path, tree_, read_event(path)))
        assert not allowed, f"Read allowed {path}"
        assert "secret" in reason, reason


# Criteria 6 and 7: Bash only for the answer helper and the declared wrappers, with no shell control operator.

def test_bash_allows_the_answer_helper_and_no_other_command(tmp_path):
    tree_ = tree(tmp_path)
    helper = (f"python3 {ANSWER} --scope {INBOX} --thread '100.000100' --text 'the answer'")
    allowed, reason, given = call(run(tmp_path, tree_, bash_event(helper)))
    assert allowed, reason
    # The allow hands the tool the judged words, in the one form the shell reads as exactly those words.
    assert shlex.split(given["command"]) == shlex.split(helper), given
    for command in ("ls", f"python3 {ANSWER} --scope {INBOX} --text x --outbox /tmp/elsewhere.jsonl",
                    "python3 scripts/other.py", f"bash {ANSWER}"):
        allowed, reason, _ = call(run(tmp_path, tree_, bash_event(command)))
        assert not allowed, f"Bash allowed {command!r}"
        assert "helper" in reason or "Bash" in reason, reason


def test_bash_denies_the_answer_helper_under_a_program_that_is_not_an_interpreter(tmp_path):
    tree_ = tree(tmp_path)
    for program in ("/evil/python3", "/evil/python", "./python3", "bin/python3"):
        command = f"{program} {ANSWER} --scope {INBOX} --text hi"
        allowed, reason, _ = call(run(tmp_path, tree_, bash_event(command)))
        assert not allowed, f"Bash allowed {command!r}"
        assert "helper" in reason, reason


def test_bash_allows_the_answer_helper_under_the_running_interpreter_by_its_path(tmp_path):
    tree_ = tree(tmp_path)
    command = f"{sys.executable} {ANSWER} --scope {INBOX} --text hi"
    allowed, reason, given = call(run(tmp_path, tree_, bash_event(command)))
    assert allowed, reason
    assert shlex.split(given["command"]) == shlex.split(command), given


def test_bash_allows_the_action_request_call_of_the_answer_helper(tmp_path):
    """ADR-0179 (review of #2950): the request line reaches the outbox through the one allowed helper call."""
    tree_ = tree(tmp_path)
    request = (f"python3 {ANSWER} --scope {INBOX} --thread '100.000100' --kind action-request --action status "
               "--arguments '{}'")
    allowed, reason, given = call(run(tmp_path, tree_, bash_event(request)))
    assert allowed, reason
    assert shlex.split(given["command"]) == shlex.split(request), given


def test_bash_denies_a_helper_flag_it_does_not_name(tmp_path):
    tree_ = tree(tmp_path)
    command = f"python3 {ANSWER} --scope {INBOX} --thread '100.000100' --kind action-request --action status --file x"
    allowed, reason, _ = call(run(tmp_path, tree_, bash_event(command)))
    assert not allowed, reason


def test_bash_allows_a_declared_read_wrapper(tmp_path):
    tree_ = tree(tmp_path)
    script = Path(tree_["checkout"]) / "scripts" / "read-status.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/sh\necho ok\n")
    script.chmod(0o755)
    command = f"{script} --short"
    allowed, reason, _ = call(run(tmp_path, tree_, bash_event(command), doc=policy(tree_, [command])))
    assert allowed, reason
    allowed, reason, _ = call(run(tmp_path, tree_, bash_event(f"{script} --short --write"),
                                  doc=policy(tree_, [command])))
    assert not allowed, "a wrapper call with another argument passed the gate"


def test_bash_denies_a_control_operator_a_substitution_or_a_redirection(tmp_path):
    tree_ = tree(tmp_path)
    script = Path(tree_["checkout"]) / "scripts" / "read-status.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/sh\necho ok\n")
    script.chmod(0o755)
    for command in (f"{script} --short; id", f"{script} --short | id", f"{script} --short && id",
                    f"{script} --short > /tmp/out", f"{script} < /etc/passwd", f"{script} $(id)",
                    f"{script} `id`", f"{script} --short & id", f"{script} --short\necho id",
                    f"{script} --write"):
        allowed, reason, _ = call(run(tmp_path, tree_, bash_event(command), doc=policy(tree_, [f"{script} --short"])))
        assert not allowed, f"Bash allowed {command!r}"
        assert "control operator" in reason or "substitution" in reason or "Bash" in reason, reason


# Criterion 9: an error in the hook blocks the call.

def test_an_error_in_the_hook_blocks_the_call(tmp_path):
    tree_ = tree(tmp_path)
    broken = run(tmp_path, tree_, bash_event("ls"), doc={"checkout": 5}, name="broken.json")
    assert broken.returncode == 2, f"a broken policy exited {broken.returncode}: {broken.stdout}"
    assert broken.stderr.strip(), "a blocked call named no reason"
    missing = subprocess.run([sys.executable, str(GATE), str(tmp_path / "absent.json")], input="{}",
                             capture_output=True, text=True, timeout=30, check=False)
    assert missing.returncode == 2
    garbage = subprocess.run([sys.executable, str(GATE)], input="not json", capture_output=True, text=True,
                             timeout=30, check=False)
    assert garbage.returncode == 2


# Criterion 10: each denial writes one audit line with its reason.

def test_each_denial_writes_one_audit_line_with_its_reason(tmp_path):
    tree_ = tree(tmp_path)
    done = run(tmp_path, tree_, bash_event("curl http://example.com"))
    allowed, reason, _ = call(done)
    assert not allowed
    lines = audit_lines(tree_)
    assert len(lines) == 1, lines
    assert lines[0]["decision"] == "deny" and lines[0]["tool"] == "Bash", lines[0]
    assert lines[0]["reason"] == reason, lines[0]
    assert "at" in lines[0], lines[0]
    run(tmp_path, tree_, bash_event("wget http://example.com/x"))
    assert len(audit_lines(tree_)) == 2


def test_a_denial_whose_audit_write_fails_blocks_the_call(tmp_path):
    tree_ = tree(tmp_path)
    (tmp_path / "state" / "operators").write_text("a file where the audit folder belongs")
    done = run(tmp_path, tree_, bash_event("curl http://example.com"))
    assert done.returncode == 2, f"a failed audit write exited {done.returncode}: {done.stdout}"
    assert done.stdout.strip() == "", f"a blocked call still answered: {done.stdout}"


def test_an_allowed_call_writes_no_audit_line(tmp_path):
    tree_ = tree(tmp_path)
    inside = Path(tree_["checkout"]) / "README.md"
    inside.write_text("hello")
    allowed, _, _ = call(run(tmp_path, tree_, read_event(inside)))
    assert allowed
    assert audit_lines(tree_) == []


# The judged command is the executed command: an allowed Bash call carries the exact command it judged.

def test_an_allowed_bash_call_carries_the_judged_command(tmp_path):
    """The tool runs the words the hook judged, never the model's spelling: the hook answers with the one form the
    shell reads as exactly that argv, and a quote inside an answer keeps its word (#2950 criterion 11)."""
    tree_ = tree(tmp_path)
    command = f"python3 {ANSWER} --scope {INBOX} --text \"the owner's answer\""
    allowed, reason, given = call(run(tmp_path, tree_, bash_event(command)))
    assert allowed, reason
    assert shlex.split(given["command"]) == shlex.split(command), given
    assert shlex.split(given["command"])[-1] == "the owner's answer", given


def test_the_operators_module_of_these_tests_ends_its_proxies_with_each_test():
    """#2989: a gate test that starts a session starts a model proxy where no loopback endpoint is set (the CI runner).
    The module these tests load records each start, so the test's teardown ends the proxy."""
    assert operators_module().start_proxy.__name__ == "tracked"
