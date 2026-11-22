"""The walls of a child session: no write outside its worktree, no touch of the live skill copy.

Children run with `permission_mode: bypassPermissions`, which approves every tool call before a `can_use_tool` callback
is asked (claude_agent_sdk: `_get_can_use_tool_shadowed_warning`). A PreToolUse hook still runs under bypass and its
`deny` stands, so the wall is a hook. The adapter puts it on every session it builds; the operator's own sessions never
pass through the adapter.

A denial returns a reason the agent reads, so it can continue another way, and the adapter writes a `sandbox_deny` event.
"""
import importlib.util
import os
import re
import shlex
import shutil
from dataclasses import dataclass, field

from squad.project_settings import REL as SETTINGS_REL

WRITE_TOOLS = {"Edit": "file_path", "MultiEdit": "file_path", "Write": "file_path", "NotebookEdit": "notebook_path"}
MATCHER = "|".join([*WRITE_TOOLS, "Bash"])
MIGRATE = re.compile(r"modelreg(?:\.py)?\s+migrate\b")
_SHELL_OP = re.compile(r"\d*(?:>>?|<|\||&|;)")
# #1469: the kernel refuses a signal to a process outside the session, whatever spells it; the two allows keep the session's own.
SIGNAL_PROFILE = "(version 1)(allow default)(deny signal)(allow signal (target self))(allow signal (target same-sandbox))"
WRAPPER = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "contrib", "squad-goal", "scripts", "claude-sandboxed.sh")


@dataclass
class Matcher:
    """The shape of the SDK's HookMatcher, for a session built with no SDK installed (tests)."""
    matcher: str | None = None
    hooks: list = field(default_factory=list)
    timeout: float | None = None


def bundled_cli():
    """The CLI the SDK starts when no path is given: its bundled copy, else `claude` on PATH. Found without importing the SDK."""
    spec = importlib.util.find_spec("claude_agent_sdk")
    for loc in (spec.submodule_search_locations or []) if spec else []:
        bundled = os.path.join(loc, "_bundled", "claude")
        if os.path.isfile(bundled):
            return bundled
    return shutil.which("claude") or "claude"


def sdk_cli_env():
    """The env the wrapper reads to start the CLI under the signal profile."""
    return {"SQUAD_CLAUDE_CLI": bundled_cli(), "SQUAD_SIGNAL_PROFILE": SIGNAL_PROFILE}


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


# #1762: a child ran its suites outside the gates ceiling, three copies at once, and the load reached 67 on 8 cores. A suite
# named as a command goes through child-gate.sh, which takes a gates slot and refuses a second copy of the same suite. The
# aggregate runner counts as a suite: it starts every test-*.sh the same way.
_SUITE = re.compile(r"(?:test-[\w.-]+|shell-suites)\.sh")
_SHELLS = ("sh", "bash", "zsh", "dash")
_NOEXEC = ("-n", "--noexec")   # a syntax read runs nothing, so it is a read and passes


def _shell_script(args):
    """The script word a shell command runs: its first word that is no flag. None for a `-c` body, which is a command."""
    for i, a in enumerate(args):
        if a == "--":
            return os.path.basename(args[i + 1]) if i + 1 < len(args) else None
        if a in _NOEXEC:
            return None
        if a == "-c" or (a.startswith("-") and not a.startswith("--") and "c" in a[1:]):
            return None
        if a.startswith("-"):
            continue
        return os.path.basename(a)
    return None


def _shell_body(args):
    """The `-c` body a shell command runs, or None."""
    for i, a in enumerate(args):
        if a == "-c" or (a.startswith("-") and not a.startswith("--") and "c" in a[1:]):
            return args[i + 1] if i + 1 < len(args) else ""
    return None


def suite_reason(cmd, root="", depth=0):
    """Why a Bash command that runs a test-*.sh suite directly is denied, or None (#1762). A shell's flags are read, so
    `bash -x test-a.sh` is the same run as `bash test-a.sh`, and a `-c` body is read as the command it is."""
    for seg in _SEGMENT.split(cmd):
        words = _segment_words(seg)
        if not words:
            continue
        verb, args = os.path.basename(words[0]), words[1:]
        if verb in _SHELLS:
            body = _shell_body(args)
            if body is not None:
                if depth < 3 and (inner := suite_reason(body, root, depth + 1)):
                    return inner
                continue
            target = _shell_script(args)
        else:
            target = verb
        if target and _SUITE.fullmatch(target):
            wrapper = os.path.join(root, "contrib", "squad-goal", "scripts", "child-gate.sh") if root else "contrib/squad-goal/scripts/child-gate.sh"
            return (f"sandbox: a test suite run directly is denied; run it as "
                    f"`bash {wrapper} <suite>`, which takes the machine's gates slot (caps.gates) and refuses a second "
                    f"copy of the same suite")
    return None


# #2178: an issue closed while the base carried none of its work. A child session read its own tree as a delivery and
# closed the issue; the loop then read the spec chain as delivered. One writer closes an issue: the landing, which holds
# the proof. The wall is the command text, so every spelling of the write is read: `gh issue close`, a `gh issue edit`
# that sets the state, and the API route a session reaches for instead. A comment and a read stay open to the child.
_ISSUE_CLOSE_DENIAL = ("sandbox: closing an issue is denied; only the landing closes an issue, because it holds the proof "
                       "that the base carries the work. Report the state in a comment, and leave the close to the landing")


def _issue_close(verb, args):
    """True when this command word writes an issue's state closed."""
    if verb != "gh":
        return False
    words = [a for a in args if not a.startswith("-")]
    if words[:2] == ["issue", "close"]:
        return True
    if words[:2] == ["issue", "edit"]:
        for i, a in enumerate(args):
            if a in ("--state", "-s") and i + 1 < len(args) and args[i + 1] == "closed":
                return True
            if a.startswith("--state=") and a.split("=", 1)[1] == "closed":
                return True
        return False
    if words[:1] == ["api"]:
        route = next((a for a in args if "issues/" in a), "")
        method = "PATCH"
        for i, a in enumerate(args):
            if a in ("-X", "--method") and i + 1 < len(args):
                method = args[i + 1].upper()
            elif a.startswith("--method="):
                method = a.split("=", 1)[1].upper()
        closed = any(a.replace('"', "").replace("'", "") in ("state=closed", "state_reason=completed") for a in args)
        return bool(route) and method == "PATCH" and closed
    return False


def issue_close_reason(cmd, depth=0):
    """Why a Bash command that closes an issue is denied, or None (#2178). A shell's `-c` body is read as the command it is."""
    for seg in _SEGMENT.split(cmd):
        words = _segment_words(seg)
        if not words:
            continue
        verb, args = os.path.basename(words[0]), words[1:]
        if verb in _SHELLS:
            body = _shell_body(args)
            if body is not None and depth < 3 and (inner := issue_close_reason(body, depth + 1)):
                return inner
            continue
        if _issue_close(verb, args):
            return _ISSUE_CLOSE_DENIAL
    return None


# #2692, story 66: the committed `slack.yaml` sits beside the project settings file, and it is the owner's written grant.
# Only the hub writes it, after the owner approves the diff. A run or a child that edits it would create authority from
# ordinary code, so the wall denies every write at that one path and leaves a fixture named `slack.yaml` elsewhere alone.
SETTINGS_SLACK = SETTINGS_REL.with_name("slack.yaml")
_SETTINGS_SLACK_DENIAL = ("sandbox: a write to the settings-path slack.yaml is denied; that file is the owner's grant, "
                          "and only the hub writes it after the owner's approval, so no ordinary code creates authority")
_SLACK_WRITERS = {"cp", "mv", "rm", "tee", "touch", "truncate", "dd", "install"}
_SLACK_EDITORS = {"sed", "perl"}
_REDIRECT = (">", ">>", ">|", "&>", "&>>")


def _settings_slack(root):
    """The one path the rule guards: the `slack.yaml` beside the project settings file, under this session's root."""
    return os.path.realpath(os.path.join(os.path.realpath(root), *SETTINGS_SLACK.parts))


def _named_path(token, root, home):
    """The real path a command word names, read from the root, or None for a shell operator."""
    raw = token.strip("'\"")
    if not raw or raw in ("<", "<<", "<<<"):
        return None
    raw = raw.lstrip("0123456789<>&|=")
    if raw.startswith("~"):
        raw = home + raw[1:] if home else raw
    return os.path.realpath(os.path.join(root, raw))


def _redirects_into(words, root, home, target):
    """True when a `>` or `>>` of this segment sends its output to the target, not only when the target is read."""
    for i, w in enumerate(words):
        if w in _REDIRECT:
            rest = words[i + 1] if i + 1 < len(words) else ""
        elif w.startswith(">") and len(w) > 1:
            rest = w
        else:
            continue
        if _named_path(rest.lstrip("<>&|"), root, home) == target:
            return True
    return False


def _in_place(verb, args):
    """True when a text editor writes its file in place: `sed -i`, `perl -pi`."""
    if verb not in _SLACK_EDITORS:
        return False
    return any(a == "-i" or (a.startswith("-") and not a.startswith("--") and "i" in a[1:]) for a in args)


def settings_slack_reason(cmd, root, home, depth=0):
    """Why a Bash command that writes the settings-path slack.yaml is denied, or None (#2692). A read stays open, and a
    shell's `-c` body is read as the command it is."""
    target = _settings_slack(root)
    for seg in _SEGMENT.split(cmd):
        words = _segment_words(seg)
        if not words:
            continue
        verb, args = os.path.basename(words[0]), words[1:]
        if verb in _SHELLS:
            body = _shell_body(args)
            if body is not None and depth < 3 and (inner := settings_slack_reason(body, root, home, depth + 1)):
                return inner
            continue
        if target not in {_named_path(w, root, home) for w in words}:
            continue
        if _redirects_into(words, root, home, target) or verb in _SLACK_WRITERS or _in_place(verb, args):
            return _SETTINGS_SLACK_DENIAL
    return None


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
        if path == _settings_slack(top):
            return _SETTINGS_SLACK_DENIAL
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
        if (reason := suite_reason(cmd, root)) is not None:
            return reason
        if (reason := settings_slack_reason(cmd, root, home)) is not None:
            return reason
        if (reason := issue_close_reason(cmd)) is not None:
            return reason
        return table_reason(cmd) or signal_reason(cmd)
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
    while words and (os.path.basename(words[0]) in _PREFIX or re.fullmatch(r"[A-Za-z_]\w*=.*", words[0])):
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


# #1855: the kernel wall hides the process table from a child session -- the ps binary answers a bare "operation not
# permitted" at exec, and a child that ran a suite reading it read the wall as its own defect. The Bash wall names the
# tool and the rule, so an unrunnable suite reads as an environment limit, as ADR-0139 reads the same denial for a pytest
# case. A read that must work sources the seam `$SG_PS` (squad/procs.py, #1508) instead of naming the binary.
_TABLE_READER = "ps"


def table_reason(cmd, depth=0):
    """Why a Bash command that reads the process table is denied, or None (#1855)."""
    for seg in _SEGMENT.split(cmd):
        words = _segment_words(seg)
        if not words:
            continue
        verb, args = os.path.basename(words[0]), words[1:]
        if verb in ("sh", "bash", "zsh", "dash") and "-c" in args and depth < 3:
            body = args[args.index("-c") + 1] if args.index("-c") + 1 < len(args) else ""
            if inner := table_reason(body, depth + 1):
                return inner
            continue
        if verb == _TABLE_READER:
            return ("sandbox: ps is denied; the kernel wall hides the process table from this session, so a suite that "
                    "reads it cannot run here -- a fixture ends its processes through the pids it recorded (ADR-0139)")
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
