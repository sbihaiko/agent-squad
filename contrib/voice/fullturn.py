"""One owner message as one user turn of a project's own Claude session (ADR-0180).

The turn runs `claude -p` in the project checkout with the owner's own settings, MCP servers, skills and permission mode:
no tool bound, no MCP bound and no operator brief. The message goes to the CLI on stdin. The first turn of a project
creates its session, and the state folder keeps the session id, so each later turn resumes it with `--resume`.
"""
import fcntl
import json
import os
import signal
import subprocess
import threading
from typing import NamedTuple

TURN_SECONDS = 30 * 60
MAX_BUDGET_USD = 5
SESSIONS_FILE = "full-sessions.json"
LOCK_FILE = "full-sessions.lock"
OPERATOR_OVERRIDES = ("CLAUDE_CONFIG_DIR", "CLAUDE_SECURESTORAGE_CONFIG_DIR", "CLAUDE_CODE_TMPDIR")


class Turn(NamedTuple):
    session: str | None
    text: str
    exit: int | None
    failed: bool
    reason: str | None = None

    def audit(self):
        return {"session": self.session, "exit": self.exit, "failed": self.failed, "reason": self.reason,
                "chars": len(self.text)}


class TurnOrder:
    """The turns of one project run one at a time, in the order they arrived."""

    def __init__(self):
        self.cond = threading.Condition()
        self.next_ticket = {}
        self.serving = {}

    def arrive(self, repo):
        with self.cond:
            ticket = self.next_ticket.get(repo, 0)
            self.next_ticket[repo] = ticket + 1
            return ticket

    def wait(self, repo, ticket):
        with self.cond:
            while self.serving.get(repo, 0) != ticket:
                self.cond.wait()

    def leave(self, repo):
        with self.cond:
            self.serving[repo] = self.serving.get(repo, 0) + 1
            self.cond.notify_all()


def owner_env(environ):
    return {key: value for key, value in environ.items() if key not in OPERATOR_OVERRIDES}


def argv(claude, session):
    command = [claude, "-p"]
    if session:
        command += ["--resume", session]
    return command + ["--output-format", "json", "--max-budget-usd", str(MAX_BUDGET_USD)]


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


def end_tree(proc):
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.communicate()


def run_turn(claude, checkout, state_dir, repo, message, environ=None, timeout=TURN_SECONDS):
    session = saved_sessions(state_dir).get(repo)
    try:   # a missing CLI or checkout is a failed turn, never a raise (#2965 review)
        proc = subprocess.Popen(argv(claude, session), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, cwd=checkout,
                                env=owner_env(os.environ if environ is None else environ), start_new_session=True)
    except OSError:
        return Turn(session, "", None, True)
    try:
        stdout, _ = proc.communicate(message, timeout=timeout)
    except subprocess.TimeoutExpired:
        end_tree(proc)
        return Turn(session, "", None, True, "deadline")
    row = result_row(stdout)
    failed = proc.returncode != 0 or bool(row.get("is_error")) or "result" not in row
    reason = "spend_cap" if row.get("subtype") == "error_max_budget_usd" else None
    session = row.get("session_id") or session
    if not failed and session:
        remember_session(state_dir, repo, session)
    return Turn(session, "" if failed else str(row["result"]), proc.returncode, failed, reason)
