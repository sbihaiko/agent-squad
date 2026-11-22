"""A child session cannot write outside its worktree or touch the live skill copy (#659).

Children run with bypassPermissions, so the wall is a PreToolUse hook on every session the adapter builds. A scripted
session drives the real hook through the adapter's own options: no stub of the hook itself, no SDK, no spend.
"""
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from squad import record, sandbox
from squad.adapter import CallRequest
from squad.sdk_adapter import SdkAdapter

pytestmark = pytest.mark.anyio


@dataclass
class ResultMessage:
    subtype: str = "success"
    is_error: bool = False
    num_turns: int = 1
    session_id: str = "sdk-1"
    total_cost_usd: float | None = 0.01
    result: str | None = "{}"
    origin: object = None
    usage: dict | None = None


@dataclass
class Matcher:
    matcher: str | None = None
    hooks: list = field(default_factory=list)
    timeout: float | None = None


class Options:
    def __init__(self, **kw):
        self.kw = kw


def scripted(steps, decisions):
    """A session that asks the PreToolUse hooks of its options before each scripted tool call, and runs an allowed write."""
    async def query(*, prompt, options):
        for tool, tool_input in steps:
            out = {}
            for m in options.kw["hooks"]["PreToolUse"]:
                if m.matcher is None or tool in m.matcher.split("|"):
                    for h in m.hooks:
                        out = await h({"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input}, "tu-1", {"signal": None}) or out
            spec = out.get("hookSpecificOutput") or {}
            decisions.append((tool, spec.get("permissionDecision"), spec.get("permissionDecisionReason")))
            if spec.get("permissionDecision") != "deny" and tool == "Write":
                p = tool_input["file_path"]
                p = p if os.path.isabs(p) else os.path.join(options.kw["cwd"], p)
                with open(p, "w") as f:
                    f.write(tool_input["content"])
        yield ResultMessage()
    return query


def request(ws):
    return CallRequest(node="actor", entry=1, item=None, prompt="do it", model="claude-sonnet-5-5", effort="medium",
                       tools=["Read", "Edit", "Write", "Bash"], max_stalls=12, max_spend=2.0, workspace=str(ws), deadline=30.0,
                       session_id="run-s1")


@pytest.fixture
def site(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude" / "skills" / "squad-goal").mkdir(parents=True)
    ws = tmp_path / "wt"
    ws.mkdir()
    (ws / "escape").symlink_to(tmp_path)          # a link inside the worktree that leads out of it
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SG_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("SG_LIVE_DIR", str(home / ".claude" / "skills" / "squad-goal"))
    return tmp_path, home, ws


async def test_a_scripted_session_meets_each_wall_and_writes_in_its_worktree(site):
    tmp, home, ws = site
    live = home / ".claude" / "skills" / "squad-goal"
    steps = [
        ("Write", {"file_path": "ok.txt", "content": "fine"}),
        ("Write", {"file_path": str(tmp / "outside.txt"), "content": "x"}),
        ("Edit", {"file_path": str(ws / ".." / "outside.txt"), "old_string": "a", "new_string": "b"}),
        ("Edit", {"file_path": "escape/outside.txt", "old_string": "a", "new_string": "b"}),
        ("NotebookEdit", {"notebook_path": str(live / "n.ipynb"), "new_source": ""}),
        ("Write", {"file_path": str(live / "SKILL.md"), "content": "x"}),
        ("Bash", {"command": "cp a ~/.claude/skills/squad-goal/x"}),
        ("Bash", {"command": "ls $HOME/.claude"}),
        ("Bash", {"command": "ls ${HOME}/.claude/skills"}),
        ("Bash", {"command": f"cat {home}/.claude/settings.json"}),
        ("Bash", {"command": "rm -rf $SG_STATE/auto"}),
        ("Bash", {"command": f"touch {tmp / 'state'}/x"}),
        ("Bash", {"command": "echo > $SG_LIVE_DIR/models.json"}),
        ("Bash", {"command": "bash contrib/squad-goal/scripts/publish-live.sh"}),
        ("Bash", {"command": "python3 -m squad.modelreg migrate"}),
        ("Bash", {"command": "python3 -m squad.modelreg migrate --dry-run"}),
        ("Bash", {"command": "python3 -m pytest -q tests"}),
    ]
    decisions, events = [], []
    a = SdkAdapter(query=scripted(steps, decisions), options_cls=Options, hook_cls=Matcher)
    a.bind(lambda kind, **f: events.append((kind, f)))
    await a.call(request(ws))
    got = [d[1] for d in decisions]
    assert got == [None, "deny", "deny", "deny", "deny", "deny", "deny", "deny", "deny", "deny", "deny", "deny", "deny",
                   "deny", "deny", "deny", None]
    assert (ws / "ok.txt").read_text() == "fine" and not (tmp / "outside.txt").exists()
    assert all(r for _, d, r in decisions if d == "deny")          # the agent gets a reason, so it can continue
    deny = [f for k, f in events if k == "sandbox_deny"]
    assert len(deny) == 15
    assert deny[0]["node"] == "actor" and deny[0]["session"] == "run-s1" and deny[0]["tool"] == "Write" and deny[0]["reason"]
    for f in deny:
        record.validate_event({"event": "sandbox_deny", **f})


async def test_the_wall_is_on_every_session_even_with_compaction_logging_off(tmp_path):
    a = SdkAdapter(query=object(), options_cls=Options, hook_cls=Matcher, log_compaction=False)
    hooks = a.options(request(tmp_path)).kw["hooks"]
    assert "PreToolUse" in hooks and "PreCompact" not in hooks


def test_a_sandbox_deny_event_needs_its_fields():
    with pytest.raises(record.RecordError):
        record.validate_event({"event": "sandbox_deny", "node": "a"})


def test_a_path_is_resolved_before_it_is_judged(tmp_path):
    ws = tmp_path / "wt"
    (ws / "sub").mkdir(parents=True)
    assert sandbox.deny_reason("Write", {"file_path": str(ws / "sub" / ".." / "f")}, str(ws), env={}, home=str(tmp_path / "h")) is None
    assert sandbox.deny_reason("Write", {"file_path": str(ws / "sub" / ".." / ".." / "f")}, str(ws), env={}, home=str(tmp_path / "h"))
    assert sandbox.deny_reason("Write", {"file_path": str(tmp_path / "wt2" / "f")}, str(ws), env={}, home=str(tmp_path / "h"))


def _migrate_reason(ws, cmd, env, home):
    return sandbox.deny_reason("Bash", {"command": cmd}, str(ws), env=env, home=str(home))


def test_a_repo_modelreg_migrate_is_allowed(tmp_path):
    """#662: a child migrates the repo file in its own worktree; only the live file is the operator's."""
    home, ws = tmp_path / "h", tmp_path / "wt"
    (ws / "contrib" / "squad-goal" / "scripts").mkdir(parents=True)
    env = {"HOME": str(home)}
    commands = [
        "python3 -m squad.modelreg migrate contrib/squad-goal/scripts/models.json",
        f"python3 {ws}/contrib/squad-goal/scripts/modelreg.py migrate {ws}/contrib/squad-goal/scripts/models.json --dry-run",
        "uv run python -m squad.modelreg migrate --dry-run contrib/squad-goal/scripts/models.json",
        "cd contrib/squad-goal/scripts && python3 modelreg.py migrate models.json",
    ]
    for cmd in commands:
        assert _migrate_reason(ws, cmd, env, home) is None, cmd


def test_a_live_modelreg_migrate_is_denied_with_and_without_a_dry_run(tmp_path):
    home, ws = tmp_path / "h", tmp_path / "wt"
    live = home / ".claude" / "skills" / "squad-goal"
    (live / "scripts").mkdir(parents=True)
    ws.mkdir()
    env = {"HOME": str(home)}
    for dry in ("", " --dry-run"):
        reason = _migrate_reason(ws, f"python3 -m squad.modelreg migrate {live}/scripts/models.json{dry}", env, home)
        assert reason and str((home / ".claude" / "skills").resolve()) in reason, (dry, reason)


def test_a_symlink_into_the_live_skill_directory_is_denied(tmp_path):
    home, ws = tmp_path / "h", tmp_path / "wt"
    live = home / ".claude" / "skills" / "squad-goal"
    (live / "scripts").mkdir(parents=True)
    ws.mkdir()
    (ws / "copy").symlink_to(live)
    reason = _migrate_reason(ws, "python3 -m squad.modelreg migrate copy/scripts/models.json", {"HOME": str(home)}, home)
    assert reason and str((home / ".claude" / "skills").resolve()) in reason, reason


def test_sg_live_dir_is_a_live_root(tmp_path):
    live, ws = tmp_path / "live", tmp_path / "wt"
    live.mkdir()
    ws.mkdir()
    env = {"HOME": str(tmp_path / "h"), "SG_LIVE_DIR": str(live)}
    reason = _migrate_reason(ws, f"python3 -m squad.modelreg migrate {live}/models.json", env, tmp_path / "h")
    assert reason and str(live.resolve()) in reason, reason


def test_a_migrate_with_no_path_names_the_live_file_as_its_reason(tmp_path):
    home, ws = tmp_path / "h", tmp_path / "wt"
    ws.mkdir()
    reason = _migrate_reason(ws, "python3 -m squad.modelreg migrate --dry-run", {"HOME": str(home)}, home)
    assert reason and str((home / ".claude" / "skills").resolve()) in reason, reason


@pytest.mark.heavy     # a git repository, a gate pass and its log: over 1 s whatever the reader costs (#1511)
def test_a_child_can_read_the_fast_gate_result_without_naming_the_state_dir(tmp_path):
    """#945: the gate kept its only evidence in SG_STATE, and the wall denies that name to the child that ran the gate, so
    the child could not verify its own gate. With no SG_FAST_LOG_DIR set, the gate now prints its verdict and names a log in
    the default directory outside the state dir, and the child may read exactly that log; the wall still denies the state."""
    scripts = Path(__file__).resolve().parent.parent / "contrib" / "squad-goal" / "scripts"
    home, state, repo, tmp = tmp_path / "h", tmp_path / "state", tmp_path / "wt", tmp_path / "tmp"
    repo.mkdir()
    tmp.mkdir()
    git = ["git", "-c", "user.email=a@b", "-c", "user.name=t"]
    subprocess.run([*git, "init", "-q", "-b", "dev"], cwd=repo, check=True)
    (repo / "a.txt").write_text("base\n")
    subprocess.run([*git, "add", "-A"], cwd=repo, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "base"], cwd=repo, check=True)
    env = {**os.environ, "HOME": str(home), "SG_STATE": str(state), "SQUAD_CAPS_DIR": str(tmp_path / "caps"),
           "TMPDIR": str(tmp), "SG_FAST_BASE": "dev", "SG_FAST_CMD": "true", "SG_FAST_RETRY_CMD": "false",
           "SG_SERIAL_HELD": ""}
    env.pop("SG_FAST_LOG_DIR", None)                                 # the production default, not a test override
    wall = sandbox.deny_reason("Bash", {"command": "cat $SG_STATE/gate-status"}, str(repo), env=env, home=str(home))
    assert wall and "$SG_STATE" in wall, wall                        # the wall stands: the state dir is not the child's
    out = subprocess.run(["bash", str(scripts / "fast-gate.sh")], cwd=repo, env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr
    last = [line for line in out.stdout.splitlines() if line.strip()][-1]
    assert last.startswith("fast-gate: pass "), out.stdout          # the verdict is a line the child reads, not a file it may not open
    log = Path(last.rsplit("(log ", 1)[1].removesuffix(")"))
    assert log.is_file() and not log.resolve().is_relative_to(state.resolve()), last
    assert sandbox.deny_reason("Bash", {"command": f"cat {log}"}, str(repo), env=env, home=str(home)) is None


@pytest.mark.parametrize("command", ["cat /etc/hosts", "cat ../outside.txt", "tail -1 >/etc/x", "ls ~/.claude/skills"])
def test_bang_command_outside_the_working_directory_is_refused(tmp_path, command):
    reason = sandbox.bang_reason(command, str(tmp_path))
    assert reason and reason.startswith("sandbox:"), reason


@pytest.mark.parametrize("command", ["cat ./notes.txt", "echo hi >/dev/null", "ls -la", "echo done-42"])
def test_bang_command_inside_the_working_directory_is_allowed(tmp_path, command):
    assert sandbox.bang_reason(command, str(tmp_path)) is None


# #1855: the kernel wall hides the process table from a child session -- /bin/ps answers a bare "operation not permitted"
# at exec, so a child that ran a suite reading the process table read the wall as its own defect. The Bash wall names the
# tool and the rule instead, which is the same reading ADR-0139 gives a pytest case it deselects for the missing binary.
@pytest.mark.parametrize("command", ["ps -p $$", "/bin/ps -axwwo pid=,command=", "ps aux | grep watch.sh",
                                     "bash -c 'ps -ax'", "/usr/bin/env SG_A=1 ps -o pid=", "cd /tmp && ps"])
def test_a_process_table_read_is_denied_by_name(tmp_path, command):
    reason = sandbox.deny_reason("Bash", {"command": command}, str(tmp_path), env={}, home=str(tmp_path / "h"))
    assert reason and reason.startswith("sandbox: ps ") and "process table" in reason, reason


@pytest.mark.parametrize("command", ["pgrep -f watch.sh", "/usr/bin/pgrep -fl bash", "grep -c ps steps.log",
                                     "echo 'ps -ax' >> note", "cat ps.txt", "xargs -n1 echo < ps.txt"])
def test_a_read_that_is_not_the_process_table_is_allowed(tmp_path, command):
    assert sandbox.deny_reason("Bash", {"command": command}, str(tmp_path), env={}, home=str(tmp_path / "h")) is None, command


SIGNAL_WRAPPER = Path(__file__).resolve().parent.parent / "contrib" / "squad-goal" / "scripts" / "claude-sandboxed.sh"


def _wrapped_env(executable):
    """The env the adapter sets, with the CLI swapped for a probe executable (#1469)."""
    return {**os.environ, **sandbox.sdk_cli_env(), "SQUAD_CLAUDE_CLI": executable}


def _wrapped(executable, *args):
    return subprocess.run([str(SIGNAL_WRAPPER), *args], capture_output=True, text=True, env=_wrapped_env(executable), timeout=30)


@pytest.fixture
def supervisor():
    proc = subprocess.Popen(["sleep", "30"], start_new_session=True)     # a loop lane: its own process group, not the child's
    yield proc
    proc.kill()
    proc.wait()


@pytest.mark.sandbox_bound
def test_a_child_python_cannot_signal_the_loop_supervisor(supervisor):
    code = f"import os,signal; os.kill({supervisor.pid}, signal.SIGTERM)"
    out = _wrapped(sys.executable, "-c", code)
    assert out.returncode != 0 and "Operation not permitted" in out.stderr, out.stderr
    assert supervisor.poll() is None


@pytest.mark.sandbox_bound
@pytest.mark.parametrize("spelling", ["kill -TERM {pid}", "kill -TERM -- -{pid}", "eval 'kill -TERM {pid}'", "$(echo kill) -TERM {pid}"])
def test_a_shell_spelling_cannot_signal_a_process_the_session_did_not_start(supervisor, spelling):
    out = _wrapped("/bin/sh", "-c", spelling.format(pid=supervisor.pid))
    assert out.returncode != 0, out
    assert supervisor.poll() is None


@pytest.mark.sandbox_bound
def test_a_child_cannot_signal_a_sibling_child_it_did_not_start():
    code = "import time; print('ready', flush=True); time.sleep(30)"
    sibling = subprocess.Popen([str(SIGNAL_WRAPPER), "-c", code], env=_wrapped_env(sys.executable), stdout=subprocess.PIPE,
                               text=True, start_new_session=True)
    try:
        assert sibling.stdout.readline().strip() == "ready"       # sandboxed from here on: the exec has run
        out = _wrapped(sys.executable, "-c", f"import os,signal; os.kill({sibling.pid}, signal.SIGTERM)")
        assert out.returncode != 0 and "Operation not permitted" in out.stderr, out.stderr
        assert sibling.poll() is None
    finally:
        sibling.kill()
        sibling.wait()


@pytest.mark.sandbox_bound
def test_a_child_can_still_signal_its_own_process():
    code = ("import os,signal,subprocess,time\n"
            "p = subprocess.Popen(['sleep', '30'])\n"
            "os.kill(p.pid, signal.SIGTERM)\n"
            "p.wait()\n"
            "print('own child signalled')\n")
    out = _wrapped(sys.executable, "-c", code)
    assert out.returncode == 0 and "own child signalled" in out.stdout, out.stderr


def test_the_sdk_child_is_started_through_the_signal_wall(tmp_path):
    a = SdkAdapter(query=object(), options_cls=Options, hook_cls=Matcher)
    kw = a.options(request(tmp_path)).kw
    assert kw["cli_path"] == str(SIGNAL_WRAPPER)
    assert kw["env"]["SQUAD_SIGNAL_PROFILE"] == sandbox.SIGNAL_PROFILE
    assert Path(kw["env"]["SQUAD_CLAUDE_CLI"]).name == "claude"


@pytest.mark.parametrize("command", [
    "bash contrib/squad-goal/scripts/test-wave.sh",
    "./contrib/squad-goal/scripts/test-fast-gate.sh",
    "bash contrib/squad-goal/scripts/test-wave.sh && echo done",
    "bash -x contrib/squad-goal/scripts/test-wave.sh",
    "bash -eu contrib/squad-goal/scripts/test-wave.sh",
    "bash -c 'bash contrib/squad-goal/scripts/test-wave.sh'",
    "bash contrib/squad-goal/scripts/shell-suites.sh",
])
def test_a_child_runs_a_suite_only_through_the_gate_wrapper(tmp_path, command):
    """#1762: a suite a child ran outside the gates ceiling loaded the machine to 67 on 8 cores; the wall names the wrapper."""
    reason = sandbox.deny_reason("Bash", {"command": command}, str(tmp_path))
    assert reason and "child-gate.sh" in reason, reason


@pytest.mark.parametrize("command", [
    "bash contrib/squad-goal/scripts/child-gate.sh contrib/squad-goal/scripts/test-wave.sh",
    "bash contrib/squad-goal/scripts/fast-gate.sh",
    "grep test-wave.sh contrib/squad-goal/scripts/gate.sh",
    "ls contrib/squad-goal/scripts/test-wave.sh",
    "bash -c 'echo contrib/squad-goal/scripts/test-wave.sh'",
    "bash -n contrib/squad-goal/scripts/test-wave.sh",
])
def test_a_child_may_read_a_suite_or_gate_through_the_wrapper(tmp_path, command):
    assert sandbox.deny_reason("Bash", {"command": command}, str(tmp_path)) is None


@pytest.mark.parametrize("command", ["gh issue close 2151", "gh issue close 2151 --reason completed",
                                     "gh issue edit 2151 --state closed", "bash -c 'gh issue close 2151'",
                                     "gh api -X PATCH repos/sbihaiko/agent-squad/issues/2151 -f state=closed"])
def test_a_child_cannot_close_an_issue(tmp_path, command):
    """#2178: an issue closed with nothing in the base. Only the landing closes an issue; a child that closes its own
    claims a delivery the base does not carry."""
    reason = sandbox.deny_reason("Bash", {"command": command}, str(tmp_path), env={}, home=str(tmp_path / "h"))
    assert reason and "only the landing closes an issue" in reason, reason


@pytest.mark.parametrize("command", ["gh issue comment 2151 --body 'progress'", "gh issue view 2151",
                                     "gh api repos/sbihaiko/agent-squad/issues/2151/timeline",
                                     "gh issue list --state open"])
def test_a_child_may_still_comment_on_or_read_an_issue(tmp_path, command):
    assert sandbox.deny_reason("Bash", {"command": command}, str(tmp_path), env={}, home=str(tmp_path / "h")) is None, command


def test_the_session_contract_states_every_rule_the_sandbox_enforces():
    """A session reads the sandbox rules before its first call, never only from a denial (#2022): one day held 131 denials,
    up to five of one kind in one session. Each rule `deny_reason` enforces has its line in the contract every session gets."""
    from pathlib import Path
    text = (Path(__file__).resolve().parent.parent / "prompts" / "_contract.md").read_text()
    assert "## Sandbox" in text
    sect = text[text.index("## Sandbox"):]
    for needle in ("contrib/squad-goal/scripts/child-gate.sh", "inside your worktree", "/tmp", "$SG_STATE", "live skill copy", "`ps`",
                   "Do not close an issue", "docs/squad/slack.yaml"):
        assert needle in sect, needle


# #2692, story 66: the committed `slack.yaml` beside the project settings file is the owner's written grant. Only the hub
# writes it, and only after the owner approves the diff. A run or a child that edits it would create authority from
# ordinary code, so the wall denies every write at that one path and leaves fixtures elsewhere alone.
SETTINGS_SLACK = "docs/squad/slack.yaml"


def test_a_child_cannot_edit_the_slack_file_at_the_settings_path(tmp_path):
    ws = tmp_path / "wt"
    (ws / "docs" / "squad").mkdir(parents=True)
    for tool in ("Write", "Edit", "MultiEdit"):
        for rel in (SETTINGS_SLACK, f"./{SETTINGS_SLACK}", "docs/squad/../squad/slack.yaml", str(ws / SETTINGS_SLACK)):
            reason = sandbox.deny_reason(tool, {"file_path": rel, "content": "x", "old_string": "a", "new_string": "b"},
                                         str(ws), env={}, home=str(tmp_path / "h"))
            assert reason, (tool, rel)


def test_the_settings_path_denial_names_the_rule(tmp_path):
    ws = tmp_path / "wt"
    reason = sandbox.deny_reason("Write", {"file_path": SETTINGS_SLACK, "content": "x"}, str(ws), env={}, home=str(tmp_path / "h"))
    assert reason and reason.startswith("sandbox:") and "slack.yaml" in reason and "hub" in reason, reason


def test_a_slack_fixture_under_a_temporary_folder_is_allowed(tmp_path):
    ws = tmp_path / "wt"
    (ws / "tests" / "fixtures" / "docs" / "squad").mkdir(parents=True)
    for rel in ("tests/fixtures/docs/squad/slack.yaml", "tests/fixtures/slack.yaml",
                str(ws / "tests" / "fixtures" / "docs" / "squad" / "slack.yaml")):
        assert sandbox.deny_reason("Write", {"file_path": rel, "content": "version: 1"}, str(ws),
                                   env={}, home=str(tmp_path / "h")) is None, rel


@pytest.mark.parametrize("command", [
    f"echo 'x: 1' > {SETTINGS_SLACK}",
    f"cp /tmp/x {SETTINGS_SLACK}",
    f"rm -f {SETTINGS_SLACK}",
    f"sed -i 's/a/b/' {SETTINGS_SLACK}",
    f"bash -c 'echo x >> {SETTINGS_SLACK}'",
])
def test_a_bash_write_to_the_settings_path_is_denied(tmp_path, command):
    reason = sandbox.deny_reason("Bash", {"command": command}, str(tmp_path), env={}, home=str(tmp_path / "h"))
    assert reason and "slack.yaml" in reason, reason


@pytest.mark.parametrize("command", [f"cat {SETTINGS_SLACK}", f"grep -n roles {SETTINGS_SLACK}",
                                     f"cat {SETTINGS_SLACK} > notes.txt", "ls docs/squad"])
def test_a_read_of_the_settings_path_is_allowed(tmp_path, command):
    assert sandbox.deny_reason("Bash", {"command": command}, str(tmp_path), env={}, home=str(tmp_path / "h")) is None, command


async def test_the_settings_path_denial_holds_under_bypass_permissions(site):
    tmp, home, ws = site
    (ws / "docs" / "squad").mkdir(parents=True)
    steps = [("Write", {"file_path": SETTINGS_SLACK, "content": "roles: []"})]
    decisions, events = [], []
    a = SdkAdapter(query=scripted(steps, decisions), options_cls=Options, hook_cls=Matcher)
    a.bind(lambda kind, **f: events.append((kind, f)))
    assert a.options(request(ws)).kw["permission_mode"] == "bypassPermissions"
    await a.call(request(ws))
    assert decisions == [("Write", "deny", decisions[0][2])] and decisions[0][2], decisions
    assert "slack.yaml" in decisions[0][2], decisions
    assert not (ws / SETTINGS_SLACK).exists()
    deny = [f for k, f in events if k == "sandbox_deny"]
    assert len(deny) == 1 and deny[0]["tool"] == "Write" and "slack.yaml" in deny[0]["reason"], deny
    record.validate_event({"event": "sandbox_deny", **deny[0]})
