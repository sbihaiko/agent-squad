"""A child session cannot write outside its worktree or touch the live skill copy (#659).

Children run with bypassPermissions, so the wall is a PreToolUse hook on every session the adapter builds. A scripted
session drives the real hook through the adapter's own options: no stub of the hook itself, no SDK, no spend.
"""
import os
from dataclasses import dataclass, field

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
                   "deny", "deny", None, None]
    assert (ws / "ok.txt").read_text() == "fine" and not (tmp / "outside.txt").exists()
    assert all(r for _, d, r in decisions if d == "deny")          # the agent gets a reason, so it can continue
    deny = [f for k, f in events if k == "sandbox_deny"]
    assert len(deny) == 14
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
