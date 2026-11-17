"""A run whose wave the age cap requeued is retired before its issue goes back to the queue (#1970).

The retirement ends the run's engine by proven ownership (the pid its record names, with its start time), verifies that no
process of the run survives, keeps its work, and only then writes the terminal verdict. A survivor or an unknown read keeps
the claim: nothing is written and the caller does not requeue. Every process here is a stub; no real process is signalled.
"""
import importlib.util
import json
import signal
import time
from pathlib import Path

from squad import procs as P
from squad import record
from squad import retire as R

NOW = 1_800_000_000.0
ENGINE_START = NOW - 4000
HUB_PY = Path(__file__).resolve().parent.parent / "contrib/squad-goal/scripts/hub.py"


class World:
    """A stub process table: snapshot, cwd read and kill act on it, and kill records each signal it is sent."""

    def __init__(self):
        self.procs = {}
        self.sent = []

    def add(self, pid, ppid, cmd, cwd="/elsewhere", start=ENGINE_START, ignores=()):
        self.procs[pid] = {"ppid": ppid, "cmd": cmd, "cwd": cwd, "start": start, "ignores": set(ignores)}

    def snapshot(self):
        return {pid: P.Proc(pid=pid, ppid=p["ppid"], pgid=pid, start=p["start"], command=p["cmd"]) for pid, p in self.procs.items()}

    def cwds(self):
        return [(pid, p["cwd"], p["cmd"]) for pid, p in self.procs.items()]

    def kill(self, pid, sig):
        self.sent.append((pid, sig))
        p = self.procs.get(pid)
        if p is None:
            raise ProcessLookupError(pid)
        if sig in p["ignores"]:
            return
        del self.procs[pid]
        for q in self.procs.values():
            if q["ppid"] == pid:
                q["ppid"] = 1


def run_folder(tmp_path, events):
    f = tmp_path / "runs" / "run-x"
    f.mkdir(parents=True)
    rec = record.Record(f / "record.jsonl")
    for e in events:
        rec.append(e["event"], **{k: v for k, v in e.items() if k not in ("event", "ts")})
    return f


START = {"event": "run_start", "run_id": "run-x", "graph_name": "g", "graph_hash": "h", "engine_options": {}, "root": "root",
         "plan": [], "ts": ENGINE_START}


def engine_events(pid=4242, since=ENGINE_START + 10):
    return [START,
            {"event": "call_start", "node": "root", "entry": 1, "session": "s", "pid": pid, "limit": 600, "started_at": since, "ts": since}]


def go(folder, world, saved=None, wip_ok=True):
    saved = [] if saved is None else saved

    def keep(f, run_id):
        saved.append((str(f), run_id))
        return ["WIP clean own"], wip_ok
    return R.retire(folder, "killed/age-cap-requeue", snapshot=world.snapshot, cwds=world.cwds, kill=world.kill,
                    sleep=lambda s: None, now=NOW, keep_work=keep, grace_s=1.0, poll_s=0.5)


def verdicts(folder):
    return [json.loads(line) for line in (folder / "record.jsonl").read_text().splitlines()
            if json.loads(line).get("event") == "verdict"]


def hub_state(folder, tmp_path, monkeypatch):
    for key, value in {"SG_REPO": "x/y", "SG_OWNER": "x", "SG_PROJECT": "4", "SG_CHECKOUT": str(tmp_path / "co"),
                       "SG_HUB_DIR": str(tmp_path / "hub"), "SG_STATE": str(tmp_path / "st")}.items():
        monkeypatch.setenv(key, value)
    (tmp_path / "hub").mkdir()
    spec = importlib.util.spec_from_file_location("hub_retire_state", HUB_PY)
    hub = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hub)
    return hub.state_of(hub.scan(folder / "record.jsonl"), 0)


def test_an_age_cap_retirement_reads_as_retired_in_the_hub(tmp_path, monkeypatch):
    f = run_folder(tmp_path, engine_events())
    out = go(f, World())
    assert out.ended, out.lines
    assert [v["reasons"] for v in verdicts(f)] == [["killed/age-cap-requeue"]]
    assert hub_state(f, tmp_path, monkeypatch) == "retired-age-cap"


def test_the_engine_tree_ends_and_the_verdict_follows(tmp_path):
    f = run_folder(tmp_path, engine_events())
    w = World()
    w.add(4242, 900, "/repo/.venv/bin/python /repo/.venv/bin/squad start --run x")
    w.add(4300, 4242, "/x/_bundled/claude --session", cwd=str(f / "ws"))
    w.add(4301, 4300, "/bin/zsh -c pytest", cwd=str(f / "ws"))
    saved = []
    out = go(f, w, saved)
    assert out.ended, out.lines
    assert not w.procs
    assert {p for p, s in w.sent if s == signal.SIGTERM} == {4242, 4300, 4301}
    assert [v["reasons"] for v in verdicts(f)] == [["killed/age-cap-requeue"]]
    assert saved == [(str(f), "run-x")]


def test_a_term_resistant_engine_gets_kill_and_is_verified_gone(tmp_path):
    f = run_folder(tmp_path, engine_events())
    w = World()
    w.add(4242, 900, "squad start --run x", ignores={signal.SIGTERM})
    out = go(f, w)
    assert out.ended, out.lines
    assert (4242, signal.SIGTERM) in w.sent and (4242, signal.SIGKILL) in w.sent
    assert len(verdicts(f)) == 1


def test_a_survivor_keeps_the_claim_and_writes_nothing(tmp_path):
    f = run_folder(tmp_path, engine_events())
    w = World()
    w.add(4242, 900, "squad start --run x", ignores={signal.SIGTERM, signal.SIGKILL})
    out = go(f, w)
    assert not out.ended
    assert verdicts(f) == []
    assert any("4242" in line for line in out.lines)


def test_a_reused_pid_is_never_signalled(tmp_path):
    f = run_folder(tmp_path, engine_events())
    w = World()
    w.add(4242, 900, "squad start --run other", start=NOW - 5)   # started after the record named the pid: another process
    out = go(f, w)
    assert out.ended, out.lines
    assert w.sent == []
    assert 4242 in w.procs
    assert len(verdicts(f)) == 1


def test_an_unrelated_process_in_the_run_folder_is_not_killed_and_holds_the_end(tmp_path):
    f = run_folder(tmp_path, engine_events())
    w = World()
    w.add(4242, 900, "squad start --run x")
    w.add(7000, 1, "/bin/zsh -c source /u/.claude/shell-snapshots/s.sh && /x/_bundled/claude", cwd=str(f / "ws"), start=NOW - 50)
    out = go(f, w)
    assert 4242 not in w.procs
    assert all(p != 7000 for p, _ in w.sent)   # outside the engine's tree: never killed by its cwd alone
    assert not out.ended   # a live process of the run's folder survives: the claim stays
    assert verdicts(f) == []


def test_a_helper_in_the_folder_that_is_no_run_process_does_not_hold(tmp_path):
    f = run_folder(tmp_path, engine_events())
    w = World()
    w.add(4242, 900, "squad start --run x")
    w.add(7001, 1, "caffeinate -i", cwd=str(f))
    out = go(f, w)
    assert out.ended, out.lines
    assert 7001 in w.procs and all(p != 7001 for p, _ in w.sent)


def test_a_run_with_a_verdict_is_untouched(tmp_path):
    f = run_folder(tmp_path, engine_events() + [{"event": "verdict", "verdict": "completed", "exit_code": 0, "reasons": [],
                                                    "remains": [], "folded": [], "ts": NOW - 100}])
    w = World()
    w.add(4242, 900, "squad start --run x")
    before = (f / "record.jsonl").read_text()
    saved = []
    out = go(f, w, saved)
    assert out.ended
    assert w.sent == [] and saved == []
    assert (f / "record.jsonl").read_text() == before


def test_a_failed_work_snapshot_keeps_the_claim(tmp_path):
    f = run_folder(tmp_path, engine_events())
    w = World()
    w.add(4242, 900, "squad start --run x")
    out = go(f, w, wip_ok=False)
    assert not out.ended
    assert verdicts(f) == []


def test_a_retry_after_an_interrupted_end_finishes_it(tmp_path):
    f = run_folder(tmp_path, engine_events())
    w = World()   # the first pass killed the engine and stopped before its verdict: the engine is gone now
    out = go(f, w)
    assert out.ended
    assert len(verdicts(f)) == 1
    again = go(f, w)
    assert again.ended and len(verdicts(f)) == 1


def test_an_unreadable_process_table_keeps_the_claim(tmp_path):
    f = run_folder(tmp_path, engine_events())

    def broken():
        raise P.ReadError("no table")
    out = R.retire(f, "killed/age-cap-requeue", snapshot=broken, cwds=lambda: [], kill=lambda p, s: None,
                   sleep=lambda s: None, now=NOW, keep_work=lambda f, r: ([], True))
    assert not out.ended and verdicts(f) == []


def test_a_run_that_named_no_process_and_moved_recently_keeps_the_claim(tmp_path):
    f = run_folder(tmp_path, [{**START, "ts": NOW - 10}])
    w = World()
    out = R.retire(f, "killed/age-cap-requeue", snapshot=w.snapshot, cwds=w.cwds, kill=w.kill, sleep=lambda s: None,
                   now=time.time(), keep_work=lambda f, r: ([], True))
    assert not out.ended and verdicts(f) == []
