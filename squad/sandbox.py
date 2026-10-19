"""The walls of a child session: no write outside its worktree, no touch of the live skill copy.

Children run with `permission_mode: bypassPermissions`, which approves every tool call before a `can_use_tool` callback
is asked (claude_agent_sdk: `_get_can_use_tool_shadowed_warning`). A PreToolUse hook still runs under bypass and its
`deny` stands, so the wall is a hook. The adapter puts it on every session it builds; the operator's own sessions never
pass through the adapter.

A denial returns a reason the agent reads, so it can continue another way, and the adapter writes a `sandbox_deny` event.
"""
import os
import re
from dataclasses import dataclass, field

WRITE_TOOLS = {"Edit": "file_path", "MultiEdit": "file_path", "Write": "file_path", "NotebookEdit": "notebook_path"}
MATCHER = "|".join([*WRITE_TOOLS, "Bash"])
MIGRATE = re.compile(r"modelreg(?:\.py)?\s+migrate\b")


@dataclass
class Matcher:
    """The shape of the SDK's HookMatcher, for a session built with no SDK installed (tests)."""
    matcher: str | None = None
    hooks: list = field(default_factory=list)
    timeout: float | None = None


def _inside(path, root):
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def bash_needles(env, home):
    """The text a Bash command may not hold: every spelling of the home `.claude` folder, the state and live folders."""
    out = ["~/.claude", "$HOME/.claude", "${HOME}/.claude", "publish-live.sh"]
    for h in {home, os.path.realpath(home)} if home else ():
        out.append(os.path.join(h, ".claude"))
    for var in ("SG_STATE", "SG_LIVE_DIR"):
        out += [f"${var}", f"${{{var}}}"]
        if val := env.get(var):
            out += sorted({val.rstrip("/"), os.path.realpath(val)})
    return out


def deny_reason(tool, tool_input, root, env=None, home=None):
    """Why the call is denied, or None when it may run. `root` is the session's worktree; a relative path is read from it."""
    env = os.environ if env is None else env
    home = home if home is not None else env.get("HOME") or os.path.expanduser("~")
    tool_input = tool_input or {}
    if tool in WRITE_TOOLS:
        raw = tool_input.get(WRITE_TOOLS[tool])
        if not isinstance(raw, str) or not raw:
            return None
        raw = home + raw[1:] if raw == "~" or raw.startswith("~/") else raw
        path = os.path.realpath(os.path.join(root, raw))
        top = os.path.realpath(root)
        if not _inside(path, top):
            return f"sandbox: {tool} of {raw} is denied; this session writes only inside its worktree {top}"
        return None
    if tool == "Bash":
        cmd = tool_input.get("command")
        if not isinstance(cmd, str):
            return None
        for needle in bash_needles(env, home):
            if needle in cmd:
                return f"sandbox: a command that names {needle} is denied; the live skill copy and the loop state are not this session's to touch"
        if MIGRATE.search(cmd) and "--dry-run" not in cmd:
            return "sandbox: modelreg migrate is denied in a child session; only --dry-run may run"
    return None


def pre_tool_use(root, on_deny, env=None, home=None):
    """The hook callback: deny with a reason, and tell `on_deny(tool, reason)` once per denial."""
    async def hook(input_data, tool_use_id, context):
        tool = input_data.get("tool_name")
        reason = deny_reason(tool, input_data.get("tool_input"), root, env, home)
        if reason is None:
            return {}
        on_deny(tool, reason)
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}
    return hook
