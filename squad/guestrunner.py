"""The guest poller runner: one fresh `claude -p` session for each due `guest` channel (SPEC 2682, #2830).

A tick fires every minute. It reads the profile's Slack files, asks the poll schedule which channels are due, and starts
one session per due channel. Each session runs under `claude-sandboxed.sh`, with the read hook (`slackpoller.
pre_read_tool_use`) and the checkpoint hook (`slackpoller.checkpoint_hook`) as command hooks. The session reads one page
of its channel in the workspace named by `team_id`, and the runner takes that page into one `Poller` poll.

A shadow poll (the default) records each decision in the shadow record and makes no write call. A session that passes
its deadline is killed, its channel stays due, and the next tick polls it again. The runner logs one line per poll: the
channel id and the decision count. It never logs message text.

The runner starts and stops by profile: `start` spawns `run` as a detached process and records its pid in the state
folder, and `stop` signals that pid.

Guards: `tests/test_guest_runner.py`.
"""
import argparse
import asyncio
import hashlib
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from . import gueststate, sandbox, slackpolicy
from . import slackpoller as P
from .record import atomic_write

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
WRAPPER = os.path.join(ROOT, "contrib", "squad-goal", "scripts", "claude-sandboxed.sh")
TICK_SECONDS = 60
DEADLINE = 300.0            # seconds, one session: a deadline is a bound, never a target
HOOK_TIMEOUT = 30.0         # seconds, one hook call
LIMIT = 100                 # messages per read
STOP_WAIT = 5.0             # seconds the runner gets to exit after its signal
LOG_NAME = "guest-runner.log"


class RunnerError(Exception):
    """A start refused: a runner already runs for the profile."""


class SessionFailed(Exception):
    """A session exited non-zero, or its result holds no page. The reason names no message text."""


class _CursorBeforeSession(P.Checkpoint):
    """The cursor as the poll found it. The session's checkpoint hook has already moved the file, so the poll reads the
    value stored before the session, and the page is not delivered twice."""

    def __init__(self, path, channel, oldest):
        super().__init__(path)
        self._channel, self._oldest = str(channel), oldest

    def get(self, channel):
        return self._oldest if str(channel) == self._channel else super().get(channel)

    def record_page(self, channel, page, own=()):
        return self.get(channel)


def poll_prompt(request, team_id):
    cursor = f", oldest {request['oldest']}" if request.get("oldest") else ""
    return (f"Read one page of Slack channel {request['channel']}{cursor}, limit {request['limit']}. "
            f"Pass team_id {team_id} to the connector so it selects that workspace. "
            "Use the Slack connector's read tool and no other tool. "
            'Reply with JSON only: {"messages": [...]}, holding the messages of the page as the tool returned them.')


def hook_settings(checkpoint_path):
    base = [sys.executable, "-m", "squad.guestrunner", "hook"]
    pre = shlex.join([*base, "pre"])
    post = shlex.join([*base, "post", str(checkpoint_path)])
    return json.dumps({"hooks": {
        "PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": pre, "timeout": HOOK_TIMEOUT}]}],
        "PostToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": post, "timeout": HOOK_TIMEOUT}]}],
    }})


def session_argv(request, team_id, repo, checkpoint_path, wrapper=WRAPPER):
    """The argv of one poll's session: one print-mode call with no persisted session, no resume and no continue. Its
    settings sources are the user's, because the CLI loads the claude.ai Slack connector only from them (#2854)."""
    return [wrapper, "-p", poll_prompt(request, team_id),
            "--permission-mode", "bypassPermissions",
            "--no-session-persistence",
            "--settings", hook_settings(checkpoint_path),
            "--setting-sources", "user",
            "--output-format", "json",
            "--add-dir", str(repo)]


def _env():
    path = os.pathsep.join(filter(None, [ROOT, os.environ.get("PYTHONPATH")]))
    return {**os.environ, **sandbox.sdk_cli_env(), "PYTHONPATH": path}


FENCE = re.compile(r"\A\s*```[a-zA-Z]*\s*\n?(.*?)\n?\s*```\s*\Z", re.S)


def session_answer(answer):
    """The JSON answer of one session. An answer wrapped in a code fence reads as the bare one, so a model that fences
    its JSON still returns a page (#2854)."""
    text = answer if isinstance(answer, str) else ""
    fenced = FENCE.match(text)
    return json.loads(fenced.group(1) if fenced else text)


def run_session(argv, folder, runner=subprocess.run, env=None):
    """Run one session and return the page it read. A non-zero exit, an answer that carries an error, or an unreadable
    result raises `SessionFailed`, so a failed poll never reads as an empty page. A session past its deadline raises
    `subprocess.TimeoutExpired` after the runner kills it."""
    done = runner(argv, cwd=folder, capture_output=True, text=True, timeout=DEADLINE, env=env, check=False)
    if done.returncode != 0:
        raise SessionFailed(f"exit {done.returncode}")
    try:
        outer = json.loads(done.stdout)
    except (ValueError, TypeError) as e:
        raise SessionFailed("no page in the result") from e
    if not isinstance(outer, dict) or outer.get("is_error") or outer.get("error"):
        raise SessionFailed("the session answered an error")
    try:
        result = session_answer(outer.get("result"))
    except (ValueError, TypeError) as e:
        raise SessionFailed("no page in the result") from e
    if isinstance(result, dict) and (result.get("error") or result.get("ok") is False):
        raise SessionFailed("the session answered an error")
    if P.read_failed(result):
        raise SessionFailed("no page in the result")
    return P.messages_of(result)


class GuestRunner:
    """The runner of one profile: `tick` polls each due channel once, and `run` ticks once a minute."""

    def __init__(self, profile, root, state_root, shadow=True, runner=subprocess.run, log=None, deliver=None):
        if not shadow and deliver is None:
            raise ValueError("a live runner needs a deliver callable: a delivered decision has no write path otherwise")
        self.profile, self.root, self.state_root = Path(profile), Path(root), Path(state_root)
        self.shadow, self.runner, self.deliver = shadow, runner, deliver
        self.log = log or (lambda text: print(text, end="", flush=True))

    def _emit(self, line):
        self.log(f"{line}\n")

    def _docs(self):
        committed, local, _ = slackpolicy.documents(self.root / slackpolicy.COMMITTED,
                                                    slackpolicy.local_path(self.profile, self.root.name))
        return committed, local

    def tick(self, now):
        """Poll each due channel once and return the `(workspace, channel)` pairs that polled."""
        committed, local = self._docs()
        state = gueststate.GuestState(self.state_root, lambda: now.timestamp(), repo=self.root)
        schedule = P.PollSchedule(state.schedule_path(), committed, local)
        polled = []
        for workspace, channel in schedule.due(now):
            if self._poll(state, schedule, committed, local, workspace, channel, now):
                polled.append((workspace, channel))
        return polled

    def _poll(self, state, schedule, committed, local, workspace, channel, now):
        folder = self.state_root / "sessions" / str(channel)
        folder.mkdir(parents=True, exist_ok=True)
        cursor = _CursorBeforeSession(state.checkpoint_path(), channel, P.Checkpoint(state.checkpoint_path()).get(channel))

        def connect(**request):
            argv = session_argv(request, workspace, self.root, state.checkpoint_path())
            return {"ok": True, "messages": run_session(argv, folder, self.runner, _env())}

        poller = P.Poller(connect, cursor, committed, local, lambda: now, limit=LIMIT,
                          shadow=self.shadow, schedule=schedule)
        recorded = len(poller.shadow.entries(str(channel))) if self.shadow else 0
        try:
            pairs = poller.poll(workspace, channel)
        except subprocess.TimeoutExpired:
            self._emit(f"guest-runner: poll {channel} killed at its {DEADLINE:.0f}s deadline")
            return False
        except SessionFailed as e:
            self._emit(f"guest-runner: poll {channel} failed: {e}")
            return False
        if not self.shadow:
            for message, decision in pairs:
                self.deliver(workspace, channel, message, decision)
        count = len(poller.shadow.entries(str(channel))) - recorded if self.shadow else len(pairs)
        self._emit(f"guest-runner: poll {channel} decisions={count}")
        schedule.polled(workspace, channel, now)
        return True

    def run(self, clock=time.time, sleep=time.sleep, ticks=None):
        done = 0
        while ticks is None or done < ticks:
            began = clock()
            self.tick(datetime.fromtimestamp(began, UTC))
            done += 1
            if ticks is None or done < ticks:
                sleep(max(0.0, TICK_SECONDS - (clock() - began)))


def hook_main(argv, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr):
    """The command hook of a poll session: `pre` runs the read hook, `post PATH` the checkpoint hook. It fails closed:
    any error exits 2, which the CLI reads as a block."""
    try:
        data = json.load(stdin)
        if argv[0] == "pre":
            hook = P.pre_read_tool_use()
        elif argv[0] == "post":
            hook = P.checkpoint_hook(P.Checkpoint(argv[1]))
        else:
            raise ValueError(f"no guest poll hook {argv[0]!r}")
        reply = asyncio.run(hook(data, data.get("tool_use_id"), None))
    except Exception as e:  # noqa: BLE001 - every failure must block the call
        stderr.write(f"guestrunner: blocked: {e}\n")
        return 2
    stdout.write(json.dumps(reply or {}))
    return 0


def pid_path(state_root, profile):
    digest = hashlib.sha256(str(Path(profile).resolve()).encode()).hexdigest()[:16]
    return Path(state_root) / "runners" / f"{digest}.pid"


def _read_pid(path):
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def start(profile, root, state_root, shadow=True):
    """Spawn the runner of one profile as a detached process and record its pid. Returns the pid."""
    pidfile = pid_path(state_root, profile)
    pid = _read_pid(pidfile)
    if pid is not None and _alive(pid):
        raise RunnerError(f"a runner already runs for {profile}")
    logs = Path(state_root) / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-m", "squad.guestrunner", "run", str(profile), "--root", str(root),
               "--state", str(state_root), "--shadow" if shadow else "--no-shadow"]
    with open(logs / LOG_NAME, "ab") as log:
        proc = subprocess.Popen(command, cwd=ROOT, env=_env(), stdout=log, stderr=subprocess.STDOUT,
                                start_new_session=True)
    pidfile.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(pidfile, f"{proc.pid}\n")
    return proc.pid


def _exited(pid):
    try:
        if os.waitpid(pid, os.WNOHANG) != (0, 0):
            return True
    except ChildProcessError:
        pass
    return not _alive(pid)


def stop(profile, state_root):
    """Signal the runner of one profile and wait for it to exit. Returns False when no runner was recorded. Raises
    `RunnerError` and keeps the pid file when the runner has not exited after `STOP_WAIT`."""
    pidfile = pid_path(state_root, profile)
    pid = _read_pid(pidfile)
    if pid is None:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + STOP_WAIT
    while not _exited(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    if not _exited(pid):
        raise RunnerError(f"the runner for {profile} did not exit after its signal; its pid file stays")
    pidfile.unlink(missing_ok=True)
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(prog="guestrunner")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("start", "run"):
        cmd = sub.add_parser(name)
        cmd.add_argument("profile")
        cmd.add_argument("--root", default=".")
        cmd.add_argument("--state", default=None)
        cmd.add_argument("--shadow", action=argparse.BooleanOptionalAction, default=True)
    stopper = sub.add_parser("stop")
    stopper.add_argument("profile")
    stopper.add_argument("--state", default=None)
    hook = sub.add_parser("hook")
    hook.add_argument("kind", choices=["pre", "post"])
    hook.add_argument("checkpoint", nargs="?", default="")
    args = parser.parse_args(argv)
    if args.cmd == "hook":
        return hook_main([args.kind, args.checkpoint])
    state = Path(args.state) if args.state else gueststate.default_root()
    if args.cmd == "stop":
        try:
            stopped = stop(args.profile, state)
        except RunnerError as e:
            print(f"guest-runner: {e}")
            return 1
        print("guest-runner: stopped" if stopped else "guest-runner: no runner for this profile")
        return 0 if stopped else 1
    if not args.shadow:
        parser.error("live delivery has no write path yet: start the runner in shadow mode")
    root = Path(args.root).resolve()
    if args.cmd == "start":
        print(f"guest-runner: started pid {start(args.profile, root, state, args.shadow)}")
        return 0
    GuestRunner(args.profile, root, state, shadow=args.shadow).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
