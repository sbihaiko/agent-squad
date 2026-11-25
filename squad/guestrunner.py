"""The guest poller runner: one fresh `claude -p` session for each due `guest` channel (SPEC 2682, #2830).

A tick fires every minute. It reads the profile's Slack files, asks the poll schedule which channels are due, and starts
one session per due channel. Each session runs under `claude-sandboxed.sh`, with the read hook (`slackpoller.
pre_read_tool_use`) and the checkpoint hook (`slackpoller.checkpoint_hook`) as command hooks. The session reads one page
of its channel in the workspace named by `team_id`, and the runner takes that page into one `Poller` poll. The
`PostToolUse` hook keeps the page its read tool returned, so the runner reads that page and the session's final answer is
only a fallback (#2855).

A shadow poll (the default) records each decision in the shadow record and makes no write call. A session that passes
its deadline is killed, its channel stays due, and the next tick polls it again. The runner logs one line per poll: the
channel id and the decision count, and a failed poll also names the status fields of the CLI answer (#2867). It never
logs message text or result text.

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
    """A session exited non-zero, or its result holds no page. The reason names the status fields of the CLI answer, so
    a failed poll names its cause, and it holds no message text and no result text (#2867)."""


def shape_of(value):
    """The shape of a page the runner could not read: keys, types and counts only, never a value (#2859). The runner
    log names why a page was not read, so the shape reaches the log and no message text ever does."""
    if isinstance(value, dict):
        for key in ("messages", "content"):
            if key in value:
                return f"{key}: {shape_of(value[key])}"
        return "mapping with keys " + (",".join(sorted(str(key) for key in value)) or "none")
    if isinstance(value, list):
        kinds = sorted({type(item).__name__ for item in value})
        return f"list of {len(value)} of " + (",".join(kinds) or "nothing")
    if isinstance(value, str):
        return "text"
    return type(value).__name__


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


PAGE_NAME = "page.json"     # the page a read tool returned, kept in the session folder until the poll reads it


def page_path(folder):
    """The file that holds the page of one session while the poll reads it (#2855)."""
    return Path(folder) / PAGE_NAME


def write_page(path, messages):
    """Keep the page one read tool returned, as one atomic write (ADR-0030)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps({"messages": list(messages)}) + "\n")


def read_page(path):
    """The page a read tool returned to one session, or None when the session kept none."""
    try:
        doc = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    page = doc.get("messages") if isinstance(doc, dict) else None
    return page if isinstance(page, list) else None


def discard_page(path):
    """Delete the kept page and every atomic-write temp of it, because both hold message text (story 42)."""
    path = Path(path)
    for kept in [path, *path.parent.glob(f".{path.name}.*")]:
        kept.unlink(missing_ok=True)


PAGE_TOOL = "read_channel"  # the connector's tool that reads one page of a channel


def page_hook(checkpoint, page=None, channel=None):
    """The `PostToolUse` hook of one poll: it moves the checkpoint, and it keeps the page the channel read returned, so
    the runner reads the page the connector sent and never the model's reformatted answer (#2855). Another tool, a read
    of another channel, or a result the reader cannot understand writes no page, and the answer stays the fallback."""
    record = P.checkpoint_hook(checkpoint)

    async def hook(input_data, tool_use_id, context):
        reply = await record(input_data, tool_use_id, context)
        if page is None or P.connector_word(input_data.get("tool_name")) != PAGE_TOOL:
            return reply
        args = input_data.get("tool_input") or {}
        found = args.get("channel_id") or args.get("channel")
        if channel is not None and str(found) != str(channel):
            return reply
        response = input_data.get("tool_response")
        if not P.read_failed(response):
            write_page(page, P.messages_of(response))
        return reply
    return hook


def hook_settings(checkpoint_path, page=None, channel=None):
    base = [sys.executable, "-m", "squad.guestrunner", "hook"]
    pre = shlex.join([*base, "pre"])
    extra = [str(page or ""), str(channel or "")] if page is not None or channel is not None else []
    post = shlex.join([*base, "post", str(checkpoint_path), *extra])
    return json.dumps({"hooks": {
        "PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": pre, "timeout": HOOK_TIMEOUT}]}],
        "PostToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": post, "timeout": HOOK_TIMEOUT}]}],
    }})


def session_argv(request, team_id, repo, checkpoint_path, page=None, wrapper=WRAPPER):
    """The argv of one poll's session: one print-mode call with no persisted session, no resume and no continue. Its
    settings sources are the user's, because the CLI loads the claude.ai Slack connector only from them (#2854)."""
    return [wrapper, "-p", poll_prompt(request, team_id),
            "--permission-mode", "bypassPermissions",
            "--no-session-persistence",
            "--settings", hook_settings(checkpoint_path, page, request.get("channel")),
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


CAUSE_FIELDS = ("subtype", "api_error_status", "terminal_reason", "stop_reason", "num_turns")
_NOT_JSON = object()    # the CLI's stdout is not JSON at all, so the answer carries no field to name


def answer_of(text):
    """The JSON value the CLI printed, with the parse error of a text that is not JSON. `_NOT_JSON` stands for that text,
    and a JSON value that is not an object is still an answer the session made. The caller keeps the error so the
    `SessionFailed` it raises from _NOT_JSON stays chained to the `JSONDecodeError` that caused it (#2867)."""
    try:
        return json.loads(text), None
    except (ValueError, TypeError) as e:
        return _NOT_JSON, e


def answer_cause(outer):
    """The metadata of one CLI answer that names why its session failed (#2867): its status fields, and no text. The
    result text, the answer's error text and every message stay out, so the runner log names a cause and never content.
    A field whose value is not scalar is named by its type, so a present-but-unprintable field still tells absent from
    present."""
    if not isinstance(outer, dict):
        return ""
    parts = []
    for key in CAUSE_FIELDS:
        value = outer.get(key)
        if isinstance(value, bool):
            parts.append(f"{key}={value}")
        elif isinstance(value, (str, int, float)):
            parts.append(f"{key}={value}")
        elif isinstance(value, (list, dict)):
            parts.append(f"{key}=<{type(value).__name__}>")
    return " ".join(parts)


def _failed(reason, outer=None):
    """One failure reason with the answer's metadata appended, so a failed poll names its cause (#2867)."""
    cause = answer_cause(outer)
    return f"{reason} ({cause})" if cause else reason


def run_session(argv, folder, runner=subprocess.run, env=None):
    """Run one session and return the page it read. The page the read tool returned, kept by the `PostToolUse` hook, wins
    over the session's final answer: the model reformats the page and the shape of its answer varies (#2855). A non-zero
    exit, an answer that carries an error, or an unreadable result raises `SessionFailed`, so a failed poll never reads as
    an empty page. A session past its deadline raises `subprocess.TimeoutExpired` after the runner kills it. The kept page
    holds message text, so it is deleted whatever the outcome (story 42)."""
    page = page_path(folder)
    discard_page(page)
    kept = None
    try:
        done = runner(argv, cwd=folder, capture_output=True, text=True, timeout=DEADLINE, env=env, check=False)
        outer, parse_error = answer_of(done.stdout)
        if done.returncode != 0:
            raise SessionFailed(_failed(f"exit {done.returncode}", outer))
        kept = read_page(page)
    finally:
        discard_page(page)
    if kept is not None:
        return kept
    if outer is _NOT_JSON:
        raise SessionFailed("no page in the result: the CLI answer is not JSON") from parse_error
    if not isinstance(outer, dict) or outer.get("is_error") or outer.get("error"):
        raise SessionFailed(_failed("the session answered an error", outer))
    try:
        result = session_answer(outer.get("result"))
    except (ValueError, TypeError) as e:
        raise SessionFailed(_failed(f"no page in the result: the answer is not JSON "
                                    f"({shape_of(outer.get('result'))})", outer)) from e
    if isinstance(result, dict) and (result.get("error") or result.get("ok") is False):
        raise SessionFailed(_failed("the session answered an error", outer))
    if P.read_failed(result):
        raise SessionFailed(_failed(f"no page in the result: {shape_of(result)}", outer))
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
            argv = session_argv(request, workspace, self.root, state.checkpoint_path(), page_path(folder))
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
            page = argv[2] if len(argv) > 2 and argv[2] else None
            channel = argv[3] if len(argv) > 3 and argv[3] else None
            hook = page_hook(P.Checkpoint(argv[1]), page, channel)
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
    hook.add_argument("page", nargs="?", default="")
    hook.add_argument("channel", nargs="?", default="")
    args = parser.parse_args(argv)
    if args.cmd == "hook":
        return hook_main([args.kind, args.checkpoint, args.page, args.channel])
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
