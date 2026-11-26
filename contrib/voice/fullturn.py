"""One owner message as one user turn of a project's own Claude session (ADR-0180).

The turn runs `claude -p` in the project checkout with the owner's own settings, MCP servers, skills and permission mode:
no tool bound, no MCP bound and no operator brief. The message goes to the CLI on stdin. The first turn of a project
creates its session, and the state folder keeps the session id, so each later turn resumes it with `--resume`.
"""
import fcntl
import json
import os
import subprocess
from typing import NamedTuple

TURN_SECONDS = 900
SESSIONS_FILE = "full-sessions.json"
LOCK_FILE = "full-sessions.lock"
OPERATOR_OVERRIDES = ("CLAUDE_CONFIG_DIR", "CLAUDE_SECURESTORAGE_CONFIG_DIR", "CLAUDE_CODE_TMPDIR")


class Turn(NamedTuple):
    session: str | None
    text: str
    exit: int | None
    failed: bool

    def audit(self):
        return {"session": self.session, "exit": self.exit, "failed": self.failed, "chars": len(self.text)}


def owner_env(environ):
    return {key: value for key, value in environ.items() if key not in OPERATOR_OVERRIDES}


def argv(claude, session, relay=None, extra=()):
    command = [claude, "-p"]
    if session:
        command += ["--resume", session]
    if relay:
        mcp_path, prompt_tool = relay
        command += ["--mcp-config", mcp_path, "--permission-prompt-tool", prompt_tool]
    return command + list(extra) + ["--output-format", "json"]


def result_row(stdout):
    for line in reversed((stdout or "").splitlines()):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and ("result" in row or "session_id" in row):
            return row
    return {}


def saved_sessions(state_dir):
    try:
        with open(os.path.join(state_dir, SESSIONS_FILE), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def remember_session(state_dir, repo, session):
    with open(os.path.join(state_dir, LOCK_FILE), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        sessions = saved_sessions(state_dir)
        sessions[repo] = session
        path = os.path.join(state_dir, SESSIONS_FILE)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(sessions, f, sort_keys=True)
        os.replace(path + ".tmp", path)


def run_turn(claude, checkout, state_dir, repo, message, environ=None, timeout=TURN_SECONDS, relay=None, extra=()):
    session = saved_sessions(state_dir).get(repo)
    try:
        done = subprocess.run(argv(claude, session, relay, extra), input=message, cwd=checkout, capture_output=True, text=True,
                              env=owner_env(os.environ if environ is None else environ), timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):   # a missing CLI or checkout is a failed turn, never a raise (#2965 review)
        return Turn(session, "", None, True)
    row = result_row(done.stdout)
    failed = done.returncode != 0 or bool(row.get("is_error")) or "result" not in row
    session = row.get("session_id") or session
    if not failed and session:
        remember_session(state_dir, repo, session)
    return Turn(session, "" if failed else str(row["result"]), done.returncode, failed)
