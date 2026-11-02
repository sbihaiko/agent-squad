"""The walls of a child session: no write outside its worktree, no touch of the live skill copy.

Children run with `permission_mode: bypassPermissions`, which approves every tool call before a `can_use_tool` callback
is asked (claude_agent_sdk: `_get_can_use_tool_shadowed_warning`). A PreToolUse hook still runs under bypass and its
`deny` stands, so the wall is a hook. The adapter puts it on every session it builds; the operator's own sessions never
pass through the adapter.

A denial returns a reason the agent reads, so it can continue another way, and the adapter writes a `sandbox_deny` event.
"""
import os
import re
import shlex
from dataclasses import dataclass, field

WRITE_TOOLS = {"Edit": "file_path", "MultiEdit": "file_path", "Write": "file_path", "NotebookEdit": "notebook_path"}
MATCHER = "|".join([*WRITE_TOOLS, "Bash"])
MIGRATE = re.compile(r"modelreg(?:\.py)?\s+migrate\b")
_SHELL_OP = re.compile(r"\d*(?:>>?|<|\||&|;)")


@dataclass
class Matcher:
    """The shape of the SDK's HookMatcher, for a session built with no SDK installed (tests)."""
    matcher: str | None = None
    hooks: list = field(default_factory=list)
    timeout: float | None = None


def _inside(path, root):
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _live_roots(env, home):
    """Every live skill directory, symlinks resolved: `~/.claude/skills` and SG_LIVE_DIR. The operator's copy."""
    roots = []
    if home:
        roots += [os.path.join(home, ".claude", "skills"), os.path.join(os.path.realpath(home), ".claude", "skills")]
    if val := env.get("SG_LIVE_DIR"):
        roots += [val, os.path.realpath(val)]
    out = []
    for root in roots:
        real = os.path.realpath(root)
        if real not in out:
            out.append(real)
    return out


def _migrate_target(cmd):
    """The path a `modelreg migrate` names, or None when it names none: then the default target is the live file."""
    match = MIGRATE.search(cmd)
    if match is None:
        return None
    rest = cmd[match.end():]
    try:
        tokens = shlex.split(rest, posix=True)
    except ValueError:
        tokens = rest.split()
    for token in tokens:
        if _SHELL_OP.fullmatch(token):
            break
        if token.startswith("-"):
            continue
        return token
    return None


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
        if MIGRATE.search(cmd):
            raw = _migrate_target(cmd)
            live = _live_roots(env, home)
            if raw is None:
                target = live[0] if live else os.path.join(home or "", ".claude", "skills")
                return (f"sandbox: modelreg migrate with no path is denied; its default target is the live skill copy "
                        f"{target}, which only the operator migrates after landing")
            raw = home + raw[1:] if home and (raw == "~" or raw.startswith("~/")) else raw
            path = os.path.realpath(os.path.join(root, raw))
            for target in live:
                if _inside(path, target):
                    return (f"sandbox: modelreg migrate of {raw} is denied; {target} is the live skill copy, "
                            f"which only the operator migrates after landing")
        for needle in bash_needles(env, home):
            if needle in cmd:
                return f"sandbox: a command that names {needle} is denied; the live skill copy and the loop state are not this session's to touch"
    return None


def _tokens(cmd):
    try:
        return shlex.split(cmd, posix=True)
    except ValueError:
        return cmd.split()


def _names_outside(token, top, home):
    raw = token.lstrip("0123456789<>&|=")
    if raw.startswith("~"):
        raw = home + raw[1:] if home else raw
    if not (raw.startswith(("/", "..")) or "/../" in raw) or raw == "/dev/null":
        return False
    return not _inside(os.path.realpath(os.path.join(top, raw)), top)


def bang_reason(command, root, env=None, home=None):
    """Why a bang command typed into a hosted session is refused, or None: the Bash wall, and no path outside `root`."""
    env = os.environ if env is None else env
    home = home if home is not None else env.get("HOME") or os.path.expanduser("~")
    reason = deny_reason("Bash", {"command": command}, root, env, home)
    if reason is not None:
        return reason
    top = os.path.realpath(root)
    for token in _tokens(command):
        if _names_outside(token, top, home):
            return f"sandbox: a command that names {token} outside its working directory {top} is denied"
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
