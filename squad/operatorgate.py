"""The tool gate of a project operator session (#2950, SPEC 2946).

One `PreToolUse` hook decides every tool call of a project's operator session. It names four tools, and a tool it does
not name is denied:

- **Read** only for a path inside the project's own checkout, and never for the secret list: `.env`, a
  `*.slack.local.yaml`, the owner's `~/.ssh`, `~/.config/gh` and `~/.aws`, and every other project checkout.
- **Monitor** only as the exact command `tail -n 0 -F <inbox>`, with `<inbox>` one of the session's own inboxes. The
  Monitor tool's input carries the command under `command`; that shape was measured on CLI 2.1.332, never guessed.
- **TaskStop**, the one call that ends a Monitor.
- **Bash** only for the shipped `answer` helper and for the command lines the project's committed Slack file declares
  under `guard.read_wrappers`, each as one fixed argv. The hook refuses any command with a shell control operator, a
  command substitution or a redirection, whether or not it is quoted where the shell keeps it literal (`$` and a
  backtick are refused outside single quotes, so a quoted answer never hides one).

**The judged command is the executed command.** Every allowed call is answered with `updatedInput` holding the exact
argv the hook judged. The CLI gives that input to the tool, and a measurement on CLI 2.1.332 shows the `--settings`
hook's input wins over an ambient hook's rewrite, so a tampering hook never changes the command the guard approved.
The live phases of `contrib/voice/operator_probe.py` hold that measurement.

**Fail closed.** The hook is a script (`contrib/voice/operator_gate.py`): any error exits 2, which the CLI reads as a
block. The decision function raises on a policy it cannot read, and the script turns that into the block.

**The audit.** Every denial appends one JSON line to the audit file the policy names: the time, the tool, the decision,
the reason and a bounded, redacted excerpt of the call. The line is best effort: a failed write never turns a denial
into an allow.

The policy is one JSON document the supervisor writes at the session start (`contrib/voice/operators.py`), never a value
the model can reach: `checkout`, `inboxes`, `wrappers`, `answer`, `others` (every other project checkout) and `audit`.

Guards: `tests/test_operator_gate.py`, and the gated live phases of `contrib/voice/operator_probe.py`.
"""
import datetime
import json
import os
import shlex
import sys
from typing import NamedTuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from squad.redact import redact  # noqa: E402  the shared redaction of a value that may carry a secret

TOOLS = ("Read", "Monitor", "TaskStop", "Bash")
TAIL = ("tail", "-n", "0", "-F")          # the one Monitor command, as `OPERATOR.md` prints it
HELPER_FLAGS = ("--scope", "--thread", "--text", "--speech", "--persona", "--kind")
HELPER_REQUIRED = ("--scope", "--text")
HELPER_KINDS = ("answer", "proposal")
SECRET_DIRS = (".ssh", os.path.join(".config", "gh"), ".aws")
AUDIT_CHARS = 120                          # the most of a call an audit line keeps
# Characters a shell reads as a control operator or a redirection, and the ones that expand or substitute.
UNQUOTED = set(";|&<>(){}[]*?~\n\r")
EXPANSION = set("$`")
DENIED_SUFFIX = ".slack.local.yaml"
EXCERPT_KEYS = ("command", "file_path", "path", "task_id")


class Outcome(NamedTuple):
    """One decision: whether the call passes, the reason when it does not, and the exact input the tool runs."""
    allow: bool
    reason: str
    given: dict


class Policy(NamedTuple):
    """What one session's gate holds: its checkout, its own inboxes, the declared wrappers, the answer helper, every
    other project checkout, and the audit file."""
    checkout: str
    inboxes: tuple
    wrappers: tuple
    answer: str
    others: tuple
    audit: str


def _path(doc, field, required=False):
    """One path field of a policy document, resolved. A field of another type is an error, so a malformed policy blocks
    every call (fail closed) instead of resolving to a folder the gate never meant."""
    value = doc.get(field)
    if value is None or value == "":
        if required:
            raise ValueError(f"the policy names no {field}")
        return ""
    if not isinstance(value, str):
        raise ValueError(f"the policy {field} is not a path: {value!r}")
    return os.path.realpath(value)


def _paths(doc, field):
    value = doc.get(field)
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"the policy {field} is not a list of paths: {value!r}")
    return tuple(os.path.realpath(item) for item in value)


def policy(doc):
    """The policy one document names, each path resolved. A malformed field raises, so the script blocks every call."""
    if not isinstance(doc, dict):
        raise ValueError(f"the policy is not a document: {doc!r}")
    wrappers = doc.get("wrappers")
    if wrappers is not None and (not isinstance(wrappers, list)
                                 or not all(isinstance(entry, str) and entry for entry in wrappers)):
        raise ValueError(f"the policy wrappers is not a list of command lines: {wrappers!r}")
    return Policy(
        _path(doc, "checkout", required=True),
        _paths(doc, "inboxes"),
        tuple(wrappers or ()),
        _path(doc, "answer", required=True),
        _paths(doc, "others"),
        _path(doc, "audit"),
    )


def _allow(given):
    return Outcome(True, "", given)


def _deny(reason, given=None):
    return Outcome(False, reason, given or {})


def _inside(path, folder):
    """True when a resolved path sits inside a resolved folder, the folder itself included."""
    if not folder:
        return False
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


def decide(pol, event):
    """The decision of one `PreToolUse` event under one policy. A tool the gate does not name is denied, and so is an
    event with no tool name."""
    event = event if isinstance(event, dict) else {}
    name = event.get("tool_name")
    given = event.get("tool_input")
    given = given if isinstance(given, dict) else {}
    if name == "Read":
        return _read(pol, given)
    if name == "Monitor":
        return _monitor(pol, given)
    if name == "TaskStop":
        return _allow(given)
    if name == "Bash":
        return _bash(pol, given)
    return _deny(f"{name} is not a tool this session names: it allows only {', '.join(TOOLS[:-1])} and {TOOLS[-1]}")


def _secret_reason(pol, path):
    """The reason this resolved path is on the secret list, else None."""
    name = os.path.basename(path)
    home = os.path.realpath(os.path.expanduser("~"))
    for folder in SECRET_DIRS:
        if _inside(path, os.path.realpath(os.path.join(home, folder))):
            return f"the secret list refuses a Read of {path}: the owner's ~/{folder} is never this session's"
    for folder in pol.others:
        if _inside(path, folder):
            return f"{path} is another project checkout, not this session's {pol.checkout}"
    if name == ".env" or name.startswith(".env."):
        return f"the secret list refuses a Read of {path}: a project's .env carries its secrets"
    if name.endswith(DENIED_SUFFIX):
        return f"the secret list refuses a Read of {path}: the profile's local Slack file carries its secrets"
    return None


def _read(pol, given):
    path = given.get("file_path")
    if not isinstance(path, str) or not path.strip():
        return _deny("a Read with no file_path names no path this session may read")
    resolved = os.path.realpath(os.path.expanduser(path))
    reason = _secret_reason(pol, resolved)
    if reason:
        return _deny(reason)
    if not _inside(resolved, pol.checkout):
        return _deny(f"{path} is outside the project checkout {pol.checkout or '(none)'}, so this session never reads it")
    return _allow({**given, "file_path": path})


def _monitor(pol, given):
    command = given.get("command")
    if not isinstance(command, str):
        return _deny("a Monitor with no command is not the exact tail of an inbox of this session")
    argv = _words(command)
    if argv is None or tuple(argv[:len(TAIL)]) != TAIL or len(argv) != len(TAIL) + 1:
        return _deny(f"Monitor runs only the exact tail command `tail -n 0 -F <inbox>` of an own inbox, not {command!r}")
    path = argv[-1]
    if os.path.realpath(os.path.expanduser(path)) not in pol.inboxes:
        return _deny(f"{path} is not an inbox of this session: Monitor follows only {', '.join(pol.inboxes) or 'none'}")
    return _allow({**given, "command": shlex.join(argv)})


def _words(command):
    """One command as its argv, or None when the hook cannot split it exactly (an unbalanced quote)."""
    try:
        return shlex.split(command, comments=False, posix=True)
    except ValueError:
        return None


def _shell_reason(command):
    """Why a command holds a shell control operator, a substitution or a redirection, else None.

    The scan reads the raw text with the shell's own quoting: inside single quotes every character is literal, inside
    double quotes `$` and a backtick still substitute, and outside quotes every operator, redirection, glob and tilde
    is refused. A command the hook cannot state exactly is one the shell could run differently, so it is refused."""
    quote = ""
    word_start = True
    i = 0
    while i < len(command):
        char = command[i]
        if quote == "'":
            if char == "'":
                quote = ""
            i += 1
            continue
        if char == "\\":
            i += 2                                   # the next character is escaped, whatever it is
            word_start = False
            continue
        if quote == '"':
            if char == '"':
                quote = ""
            elif char in EXPANSION:
                return f"the command holds a substitution ({char}) inside double quotes: {command!r}"
            i += 1
            continue
        if char == "'" or char == '"':
            quote = char
            word_start = False
            i += 1
            continue
        if char in EXPANSION:
            return f"the command holds a substitution ({char}): {command!r}"
        if char in UNQUOTED:
            return f"the command holds a shell control operator or a redirection ({char!r}): {command!r}"
        if char == "#" and word_start:
            return f"the command holds a comment (#), so the shell runs less than the hook read: {command!r}"
        word_start = char.isspace()
        i += 1
    if quote:
        return f"the command holds an unbalanced quote, so the hook cannot state it: {command!r}"
    return None


def _bash(pol, given):
    command = given.get("command")
    if not isinstance(command, str) or not command.strip():
        return _deny("a Bash call with no command is not the answer helper and not a declared read wrapper")
    reason = _shell_reason(command)
    if reason:
        return _deny(reason)
    argv = _words(command)
    if not argv:
        return _deny(f"the command {command!r} splits to no argv, so it runs nothing this session names")
    matched, reason = _helper(pol, argv)
    if not matched:
        matched, reason = _wrapper(pol, argv)
    if not matched or reason:
        return _deny(reason)
    judged = shlex.join(argv)
    if _words(judged) != argv:                       # the CLI runs `judged`: it must parse back to the judged argv
        return _deny(f"the command {command!r} has no exact form the hook can hand to the shell")
    return _allow({**given, "command": judged})


def _helper(pol, argv):
    """`(True, None)` for one allowed call of the shipped answer helper, `(False, "")` for an argv that is no helper
    call at all, and `(True, reason)` for a helper call the helper's own arguments refuse."""
    program = os.path.basename(argv[0])
    if program not in ("python3", "python") or len(argv) < 2:
        return False, ""
    if os.path.realpath(os.path.expanduser(argv[1])) != os.path.realpath(pol.answer):
        return False, ""
    rest = argv[2:]
    if len(rest) % 2:
        return True, f"the answer helper call {shlex.join(argv)} names a flag with no value"
    seen = set()
    for flag, value in zip(rest[::2], rest[1::2], strict=True):
        if flag not in HELPER_FLAGS:
            return True, f"the answer helper takes no {flag}: it takes {', '.join(HELPER_FLAGS)}"
        seen.add(flag)
        if flag == "--kind" and value not in HELPER_KINDS:
            return True, f"the answer helper's --kind is one of {', '.join(HELPER_KINDS)}, not {value!r}"
    missing = [flag for flag in HELPER_REQUIRED if flag not in seen]
    if missing:
        return True, f"the answer helper call names no {missing[0]}"
    return True, None


def _wrapper(pol, argv):
    """`(True, None)` for one command line the project declares, else a refusal.

    A declared wrapper is the whole fixed command line, and its program is a script file inside the project checkout,
    so a declared `bash -c ...`, `git ...` or `find ...` names no shipped wrapper and is refused."""
    for entry in pol.wrappers:
        words = _words(entry)
        if not words or tuple(words) != tuple(argv):
            continue
        program = argv[0]
        if os.sep not in program:
            return True, f"the declared wrapper {entry!r} names a bare program, not a shipped script in {pol.checkout}"
        path = program if os.path.isabs(program) else os.path.join(pol.checkout, program)
        path = os.path.realpath(path)
        if not _inside(path, pol.checkout) or not os.path.isfile(path) or not os.access(path, os.X_OK):
            return True, f"the declared wrapper {entry!r} is not an executable file inside {pol.checkout}"
        return True, None
    return True, (f"Bash runs only the shipped answer helper and the command lines the project declares under "
                  f"guard.read_wrappers, not {shlex.join(argv) if argv else '(none)'}")


def hook_output(outcome, judged=None):
    """The CLI's `hookSpecificOutput` for one decision. An allow carries the exact input the tool runs."""
    if not outcome.allow:
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": outcome.reason}}
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
                                   "updatedInput": outcome.given}}


def excerpt(event):
    """A bounded, redacted glimpse of one call for the audit: its command or its path, never the whole input."""
    event = event if isinstance(event, dict) else {}
    given = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
    for key in EXCERPT_KEYS:
        value = given.get(key)
        if isinstance(value, str) and value:
            return redact(value)[:AUDIT_CHARS]
    return ""


def audit_line(event, reason, now=None):
    """One audit line for one denial: the time, the tool, the decision, the reason and a bounded excerpt."""
    now = datetime.datetime.now(datetime.UTC) if now is None else now
    event = event if isinstance(event, dict) else {}
    return {"at": now.isoformat(timespec="seconds"), "tool": str(event.get("tool_name") or ""),
            "decision": "deny", "reason": reason, "call": excerpt(event)}


def audit(path, event, reason, now=None):
    """Append one audit line, best effort: a fill the hook cannot write never turns a denial into an allow."""
    if not path:
        return None
    line = audit_line(event, reason, now)
    try:
        folder = os.path.dirname(path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())
    except OSError:
        return line
    return line
