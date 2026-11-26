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


class Running:
    """One project's turn while its process runs, and whether a stop asked it to end.

    `user` is the requester who started the turn, so a stop answers only that identity: only a turn's own requester
    ends it, never another role holder who reaches the same project (#2968 review)."""

    def __init__(self, proc, user=None):
        self.proc = proc
        self.user = user
        self.stopped = threading.Event()


class TurnRegistry:
    """The turn each project runs now, so a `!stop` message ends it from outside the queue (#2968)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.running = {}

    def hold(self, repo, proc, user=None):
        running = Running(proc, user)
        with self.lock:
            self.running[repo] = running
        return running

    def release(self, repo, running):
        with self.lock:
            if self.running.get(repo) is running:
                del self.running[repo]

    def stop(self, repo, user=None):
        """End the turn one project runs now, with its whole process tree, and answer whether a turn ran.

        Only the requester who started the turn ends it: a stop from another identity clears nothing and answers
        False, so a full role handed to someone else can never kill the owner's work (#2968 review). The turn's own
        thread sits in `proc.communicate`, so this kills the group with `kill_tree` and leaves the collecting to that
        one reader; `run_turn` reads the same `Running` when its `communicate` returns, so the turn is a stopped turn
        and never a second turn."""
        with self.lock:
            running = self.running.get(repo)
        if running is None or running.user != user:
            return False
        running.stopped.set()
        kill_tree(running.proc)
        return True


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


def kill_tree(proc):
    """Signal the turn's whole process group and return at once: the thread that reads the turn's pipes collects it.

    `start_new_session` gives the turn its own process group, and every process the CLI starts stays in that group
    unless it asks for another one, so one signal to the group reaches the turn and its MCP servers alike. This signals
    only: `TurnRegistry.stop` calls it while the turn's own thread sits in `proc.communicate`, and that one thread
    reaps the process when the signal closes its pipes, so no second thread ever reads the pipes."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def end_tree(proc):
    """End the turn's whole process tree and collect it: the CLI, every process it started, the project's MCP servers
    included, and the process itself.

    This is the blocking primitive: it signals the group with `kill_tree` and then waits for the process, so a caller
    that holds no reader gets it drained and reaped before the call returns, and holds no zombie behind. A caller that
    is not the thread reading the pipes uses `kill_tree` instead, so only one thread ever reads them."""
    kill_tree(proc)
    proc.communicate()


def run_turn(claude, checkout, state_dir, repo, message, environ=None, timeout=TURN_SECONDS, registry=None,
             user=None):
    """Run one turn and answer its outcome. A `registry` holds the turn while its process runs, so a `!stop` from
    another thread ends it, and the turn reports itself stopped rather than failed by its own end (#2968). `user` is
    the requester who started the turn, so the registry ends it for that identity alone (#2968 review)."""
    session = saved_sessions(state_dir).get(repo)
    try:   # a missing CLI or checkout is a failed turn, never a raise (#2965 review)
        proc = subprocess.Popen(argv(claude, session), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, cwd=checkout,
                                env=owner_env(os.environ if environ is None else environ), start_new_session=True)
    except OSError:
        return Turn(session, "", None, True)
    running = registry.hold(repo, proc) if registry is not None else None
    if running is not None:
        running.user = user     # the requester, so only it ends this turn (#2968 review)
    try:
        stdout, _ = proc.communicate(message, timeout=timeout)
    except subprocess.TimeoutExpired:
        end_tree(proc)
        return Turn(session, "", None, True, "deadline")
    finally:
        if running is not None:
            registry.release(repo, running)
    if running is not None and running.stopped.is_set():
        return Turn(session, "", proc.returncode, True, "stopped")
    row = result_row(stdout)
    failed = proc.returncode != 0 or bool(row.get("is_error")) or "result" not in row
    reason = "spend_cap" if row.get("subtype") == "error_max_budget_usd" else None
    session = row.get("session_id") or session
    if not failed and session:
        remember_session(state_dir, repo, session)
    return Turn(session, "" if failed else str(row["result"]), proc.returncode, failed, reason)
