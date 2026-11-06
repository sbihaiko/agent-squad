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
        return signal_reason(cmd)
    return None


# #1468: a child ran `pkill -P 1` to time pkill. The loop supervisor's parent is pid 1 (watchd starts it in a new session),
# so it died and the loop stayed down for three hours. The wall is a deny list on the command text: each segment is read,
# and a segment split is crude on purpose -- it also cuts a quoted `bash -c '...'` body and a loop body into segments, so a
# signal hidden there is read the same. A kill of the session's own processes ($!, %job, a pid, its own group) passes.
_SEGMENT = re.compile(r"\|\||&&|(?<![<>])&|[;|\n()`]|\$\(")
_PREFIX = {"time", "nohup", "sudo", "command", "exec", "builtin", "env", "nice", "do", "then", "else", "{", "!"}
_KILLERS = {"kill", "pkill", "killall"}
_LAUNCHCTL_STOPS = {"stop", "kill", "bootout", "remove", "unload", "disable", "kickstart"}
_LOOP_NAMES = re.compile(r"(?<![\w.-])(watchd|watch\.sh|fastloop|cqflush|hubctl|hub|launchd)(?:\.sh|\.pid|\.lock)?(?![\w.-])")


def _segment_words(seg):
    try:
        words = shlex.split(seg, posix=True)
    except ValueError:
        words = [w.strip("'\"") for w in seg.split()]
    while words and (words[0] in _PREFIX or re.fullmatch(r"[A-Za-z_]\w*=.*", words[0])):
        words = words[1:]
    return words


def _pkill_parent_one(args):
    for i, a in enumerate(args):
        val = None
        if a in ("-P", "--parent"):
            val = args[i + 1] if i + 1 < len(args) else ""
        elif a.startswith("--parent="):
            val = a.split("=", 1)[1]
        elif a.startswith("-P") and len(a) > 2:
            val = a[2:]
        if val is not None and "1" in val.split(","):
            return True
    return False


def _kill_everyone(args):
    signal, targets, i = None, [], 0
    while i < len(args):
        a = args[i]
        if a == "--":
            targets += args[i + 1:]
            break
        if a in ("-s", "-n") and signal is None:
            signal, i = args[i + 1] if i + 1 < len(args) else "", i + 2
            continue
        if a.startswith("-") and signal is None:
            signal = a
        else:
            targets.append(a)
        i += 1
    return any(t in ("1", "-1") for t in targets) or (not targets and signal == "-1")


def signal_reason(cmd, depth=0):
    """Why a Bash command that signals outside the session is denied, or None (#1468)."""
    killing = False
    for seg in _SEGMENT.split(cmd):
        words = _segment_words(seg)
        if not words:
            continue
        verb, args = os.path.basename(words[0]), words[1:]
        if verb in ("sh", "bash", "zsh", "dash") and "-c" in args and depth < 3:
            body = args[args.index("-c") + 1] if args.index("-c") + 1 < len(args) else ""
            if inner := signal_reason(body, depth + 1):
                return inner
            continue
        if verb == "xargs":
            fed = next((os.path.basename(w) for w in args if not w.startswith("-")), "")
            if fed in _KILLERS:
                return (f"sandbox: a {fed} fed by xargs is denied; this session cannot prove the pid list is its own, "
                        f"and a signal outside it can stop the loop")
            continue
        if verb == "launchctl" and args and args[0] in _LAUNCHCTL_STOPS:
            return f"sandbox: launchctl {args[0]} is denied; a launchd job is the loop's, not this session's"
        if verb not in _KILLERS:
            continue
        killing = True
        if verb == "killall":
            return "sandbox: killall is denied; a signal by name reaches processes outside this session, the loop's among them"
        if verb == "pkill" and _pkill_parent_one(args):
            return ("sandbox: pkill by parent 1 is denied; that signal reaches every process launchd started, "
                    "the loop supervisor among them")
        if verb == "kill" and _kill_everyone(args):
            return "sandbox: kill of pid 1 or -1 is denied; it signals every process of this user, the loop supervisor among them"
    if killing and (m := _LOOP_NAMES.search(cmd)):
        return f"sandbox: a signal to {m.group(1)} is denied; that process is the loop's, not this session's"
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
