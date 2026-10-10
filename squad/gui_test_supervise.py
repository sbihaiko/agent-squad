"""Supervised gui-test/1 run: one fresh adapter process per batch, crash and hang ends that batch only."""
import json
import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

FORK = Path(__file__).resolve().parent.parent
REPORT_GLOBS = ("*.ips", "*.crash")


class Session:
    """A worker process group hosting one adapter; every call has a deadline, a miss kills the tree."""

    def __init__(self, spec, kwargs, step_timeout_s, log_path):
        self.timeout, self.log_path, self.buf = step_timeout_s, Path(log_path), b""
        self.log = open(self.log_path, "wb")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "squad.gui_test_worker", spec, json.dumps(kwargs)], cwd=FORK,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log, start_new_session=True)

    def call(self, name, *args):
        """Return the adapter's answer; raise Crash or Hang (tree already killed)."""
        try:
            self.proc.stdin.write((json.dumps({"call": name, "args": list(args)}) + "\n").encode())
            self.proc.stdin.flush()
        except OSError:
            raise self._end(Crash)
        deadline = time.monotonic() + self.timeout
        fd = self.proc.stdout.fileno()
        while b"\n" not in self.buf:
            left = deadline - time.monotonic()
            if left <= 0:
                raise self._end(Hang)
            if select.select([fd], [], [], left)[0]:
                chunk = os.read(fd, 65536)
                if not chunk:
                    raise self._end(Crash)
                self.buf += chunk
        line, _, self.buf = self.buf.partition(b"\n")
        msg = json.loads(line)
        if "error" in msg:
            raise AdapterError(msg["error"])
        return msg["ok"]

    def _end(self, kind):
        self.kill()
        return kind(self.output())

    def kill(self):
        for attempt in (0, 1):
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                break
            except PermissionError:  # macOS: a group whose leader is an unreaped zombie; reap, then sweep it
                self.proc.wait()
        self.proc.wait()
        self.log.close()

    def close(self):
        """Graceful end after teardown: closing stdin ends the worker loop; the group is killed regardless."""
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        self.kill()

    def output(self):
        return self.log_path.read_text(errors="replace")[-4000:]


class Crash(Exception):
    pass


class Hang(Exception):
    pass


class AdapterError(Exception):
    pass


def _snapshot(paths):
    return {p: (Path(p).read_bytes() if Path(p).exists() else None) for p in paths}


def _restore(snap):
    for p, data in snap.items():
        if data is None:
            Path(p).unlink(missing_ok=True)
        else:
            Path(p).write_bytes(data)


def _new_reports(report_dir, since):
    if not report_dir:
        return []
    return sorted(str(f) for g in REPORT_GLOBS for f in Path(report_dir).glob(g) if f.stat().st_mtime >= since)


def _precondition(session, text):
    key, sep, want = (text or "").partition(" == ")
    return True if not sep else bool(session.call("check", key, {"is": want}))


def _known_id(ex):
    """True when an adapter error is the adapter saying the check named an id the application does not have.

    mesen-gui raises UnknownId; the worker reports it as `<Type>: <message>` and the step fails, never the batch.
    """
    return isinstance(ex, AdapterError) and "UnknownId" in str(ex)


def _fail(sid, verdict, emit):
    emit("gui_verdict", sid, verdict=verdict)
    return {"id": sid, "verdict": verdict, "capture": None}, False


def _check_verdict(got, check):
    """The verdict of one check: the adapter may answer a bool (the reference fake) or, like mesen-gui, the
    observed value with it — `{"passed": ..., "observed": ...}` — which is what lets a failed step name both."""
    if isinstance(got, dict) and "passed" in got:
        if got["passed"]:
            return "passed"
        expected = True if check["name"] == "ui.visible" else (check.get("args") or {}).get("is")
        if expected is None:
            return f"failed (observed {got.get('observed')})"
        return f"failed (expected {expected}, observed {got.get('observed')})"
    if got == "unknown id":
        return "failed (unknown id)"
    return "passed" if got else "failed"


def _step(session, s, emit, tick_args):
    emit("gui_step", s["id"])
    pre = s.get("precondition")
    if not _precondition(session, pre):
        return _fail(s["id"], f"failed (precondition {pre})", emit)
    if s["mode"] != "automated":
        return None, True
    if s.get("action"):
        session.call("inject", s["action"]["name"], s["action"].get("args", {}))
    cond = s["wait"].get("check")
    capture = None
    if session.call("wait", cond, s["wait"].get("timeout_ticks") or s["wait"]["timeout_frames"]) != "met":
        verdict = f"failed (timeout waiting for {cond})"
    else:
        capture = session.call("capture")
        emit("gui_capture", s["id"], **capture)
        if "check" not in s:
            verdict = "passed"
        else:
            try:
                got = session.call("check", s["check"]["name"], s["check"].get("args", {}))
            except AdapterError as ex:
                if not _known_id(ex):
                    raise
                verdict = "failed (unknown id)"
            else:
                verdict = _check_verdict(got, s["check"])
    emit("gui_verdict", s["id"], verdict=verdict)
    return {"id": s["id"], "verdict": verdict, "capture": capture}, verdict == "passed"


def run_supervised(script, spec, kwargs=None, *, step_timeout_s=10, out_dir, record=None, report_dir=None):
    """Run each batch in a fresh adapter process; return {"results": [...], "batches": [...], "restore": [...]}.

    `spec` is "module:Class", built in the worker with `kwargs` (+ out_dir). Files listed in
    script["fixtures"]["restore"] are put back byte for byte before every batch and after the run.
    """
    out_dir, kwargs, pos = Path(out_dir), dict(kwargs or {}), 0
    out_dir.mkdir(parents=True, exist_ok=True)
    fixtures = script.get("fixtures", {})
    snap = _snapshot(fixtures.get("restore", []))
    results, batches = [], []

    def emit(event, node, **more):
        nonlocal pos
        if record:
            record({"event": event, "node": node, "pos": pos, **more})
        pos += 1

    try:
        for batch in script["batches"]:
            _restore(snap)
            started = time.time() - 1
            steps = batch["setup"] + batch["steps"] + batch["teardown"]
            session = Session(spec, {"out_dir": str(out_dir), **kwargs}, step_timeout_s,
                              out_dir / f"{batch['id']}.log")
            info, done, current = {"id": batch["id"], "verdict": "passed"}, set(), None
            try:
                session.call("launch", fixtures)
                body = batch["setup"] + batch["steps"]
                for s in body:
                    current = s
                    r, ok = _step(session, s, emit, None)
                    if r:
                        results.append(r)
                        done.add(s["id"])
                    if not ok:
                        info["verdict"] = r["verdict"] if r else "failed"
                        break
                for s in batch["teardown"]:  # teardown steps run even after a failed step
                    current = s
                    r, _ = _step(session, s, emit, None)
                    if r:
                        results.append(r)
                        done.add(s["id"])
                current = None
                info["teardown"] = session.call("teardown")
            except (Crash, Hang) as e:
                kind = "process crash" if isinstance(e, Crash) else "hang"
                info.update(verdict=f"failed ({kind})", output=str(e), crash_reports=_new_reports(report_dir, started))
                if current is not None and current["id"] not in done:
                    emit("gui_verdict", current["id"], verdict=info["verdict"])
                    results.append({"id": current["id"], "verdict": info["verdict"], "capture": None})
                    done.add(current["id"])
            except AdapterError as e:
                info.update(verdict=f"failed ({e})")
            finally:
                session.close()
            for s in steps:
                if s["mode"] == "automated" and s["id"] not in done:
                    results.append({"id": s["id"], "verdict": "skipped (batch failed)", "capture": None})
            batches.append(info)
    finally:
        _restore(snap)
    return {"results": results, "batches": batches}
