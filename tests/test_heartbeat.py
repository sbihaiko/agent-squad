"""Guards for the run-level heartbeat watchdog: a run is killed only when its process tree is flat AND its record stopped.

Both signals are proven, and each one alone is proven not to be enough: a long Bash call works with a still record, and a
run between two calls writes while its tree spends nothing.
"""
from squad import heartbeat, record

NOW = 2_000_000_000.0
FIVE_MIN = 300.0


def run(tmp_path, name="run-a", pid=111, node="router", age=600.0):
    """A stub run whose one open call is held by `pid` and whose last event is `age` seconds old."""
    folder = tmp_path / "runs" / name
    ts = NOW - age
    r = record.Record(folder / "record.jsonl")
    r.append("run_start", run_id=name, graph_name="g", graph_hash="h", engine_options={}, root="main", plan=["main"], ts=ts - 1)
    r.append("call_start", node=node, entry=1, item=None, session="s1", pid=pid, limit=1200.0, started_at=ts, ts=ts)
    return folder


def events(folder):
    return record.read(folder / "record.jsonl")


def verdicts(folder):
    return [e for e in events(folder) if e["event"] == "verdict"]


def sweep(tmp_path, *folders, table, now, **kw):
    """One pass over the given runs. Return (lines, the pids it killed)."""
    active = tmp_path / "active-runs.txt"
    active.write_text("".join(str(f) + "\n" for f in folders))
    killed = []
    lines = heartbeat.sweep(active, tmp_path, tmp_path / "state", now=now, table=table, minutes=(5, 5), kill=killed.extend, **kw)
    return lines, killed


def test_a_flat_tree_and_a_frozen_record_is_killed_and_its_record_names_the_reason(tmp_path):
    f = run(tmp_path)
    table = {111: (1, 10.0), 112: (111, 0.0)}       # the call's process and one agent child, neither moving

    assert sweep(tmp_path, f, table=table, now=NOW) == ([], [])          # the first pass only stamps the flat CPU
    lines, killed = sweep(tmp_path, f, table=table, now=NOW + FIVE_MIN)  # five minutes later, nothing has moved

    assert killed == [112, 111], "the whole tree dies, the child first"
    assert lines == ["STALE HEART run=run-a cpu=10s last node router, killed: heartbeat_stale"]
    assert [v["reasons"] for v in verdicts(f)] == [["killed: heartbeat_stale"]]
    assert verdicts(f)[-1]["verdict"] == "failed" and verdicts(f)[-1]["exit_code"] == 1


def test_a_run_inside_one_long_bash_call_whose_cpu_moves_is_not_touched(tmp_path):
    f = run(tmp_path, name="run-bash", pid=211)
    before = events(f)

    sweep(tmp_path, f, table={211: (1, 5.0), 212: (211, 20.0)}, now=NOW)
    lines, killed = sweep(tmp_path, f, table={211: (1, 5.0), 212: (211, 26.0)}, now=NOW + FIVE_MIN)

    assert (lines, killed) == ([], []), "a full test suite works for minutes without writing a record event"
    assert events(f) == before, "the record of a working run is left exactly as it was"


def test_a_frozen_record_whose_cpu_moves_is_not_touched(tmp_path):
    f = run(tmp_path, name="run-moving", pid=311)

    sweep(tmp_path, f, table={311: (1, 10.0)}, now=NOW)
    lines, killed = sweep(tmp_path, f, table={311: (1, 13.0)}, now=NOW + FIVE_MIN)

    assert (lines, killed) == ([], []) and verdicts(f) == []


def test_a_record_that_still_grows_is_not_touched(tmp_path):
    f = run(tmp_path, name="run-writing", pid=411, age=-299.0)     # the record moved a second ago

    sweep(tmp_path, f, table={411: (1, 10.0)}, now=NOW)
    lines, killed = sweep(tmp_path, f, table={411: (1, 10.0)}, now=NOW + FIVE_MIN)

    assert (lines, killed) == ([], []) and verdicts(f) == []


def test_a_run_whose_process_is_gone_is_left_to_the_dead_run_pass(tmp_path):
    f = run(tmp_path, name="run-dead", pid=511)

    sweep(tmp_path, f, table={511: (1, 0.0)}, now=NOW)
    lines, killed = sweep(tmp_path, f, table={999: (1, 0.0)}, now=NOW + FIVE_MIN)

    assert (lines, killed) == ([], []), "a run with no process is the dead-run pass's, never this one"
    assert verdicts(f) == [] and not (tmp_path / "state" / "run-dead").exists()


def test_a_record_that_cannot_be_read_is_never_a_kill(tmp_path):
    f = run(tmp_path, name="run-torn", pid=611)
    (f / "record.jsonl").write_text('{"event": "run_start"\n')

    lines, killed = sweep(tmp_path, f, table={611: (1, 10.0)}, now=NOW)

    assert (lines, killed) == ([], [])


def test_a_run_that_has_a_verdict_is_left_alone(tmp_path):
    f = run(tmp_path, name="run-over", pid=711)
    r = record.Record(f / "record.jsonl")
    r.append("verdict", verdict="failed", exit_code=1, reasons=["boom"], remains=[], folded=[])
    before = events(f)

    sweep(tmp_path, f, table={711: (1, 10.0)}, now=NOW)
    lines, killed = sweep(tmp_path, f, table={711: (1, 10.0)}, now=NOW + FIVE_MIN)

    assert (lines, killed) == ([], []) and events(f) == before


def test_the_pass_prints_one_line_per_kill_and_each_line_names_its_run(tmp_path):
    a = run(tmp_path, name="run-one", pid=111)
    b = run(tmp_path, name="run-two", pid=121, node="final_gate")
    table = {111: (1, 4.0), 121: (1, 7.0), 122: (121, 1.0)}

    sweep(tmp_path, a, b, table=table, now=NOW)
    lines, killed = sweep(tmp_path, a, b, table=table, now=NOW + FIVE_MIN)

    assert len(lines) == 2 and killed == [111, 122, 121]
    assert lines[0] == "STALE HEART run=run-one cpu=4s last node router, killed: heartbeat_stale"
    assert lines[1] == "STALE HEART run=run-two cpu=8s last node final_gate, killed: heartbeat_stale"


def test_a_run_id_names_the_run_folder_under_the_checkout(tmp_path):
    f = run(tmp_path, name="run-byid", pid=811)
    active = tmp_path / "active-runs.txt"
    active.write_text("run-byid\n")

    heartbeat.sweep(active, tmp_path, tmp_path / "state", now=NOW, table={811: (1, 10.0)}, minutes=(5, 5))
    lines = heartbeat.sweep(active, tmp_path, tmp_path / "state", now=NOW + FIVE_MIN, table={811: (1, 10.0)}, minutes=(5, 5), kill=lambda pids: None)

    assert lines == ["STALE HEART run=run-byid cpu=10s last node router, killed: heartbeat_stale"]

    # an absolute folder is its own run folder, as launch.py writes it
    active.write_text(f"{f}\n")
    assert heartbeat.sweep(active, tmp_path / "elsewhere", tmp_path / "state2", now=NOW, table={811: (1, 10.0)}, minutes=(5, 5)) == []


def test_the_cpu_it_reports_is_the_whole_tree_read_from_ps():
    text = """\
  100     1   0:10.00 python -m squad.cli start
  101   100   0:00.50 claude
  102   101   1:02:03.25 bash sleep.sh
  103     1   0:01.00 unrelated
"""
    table = heartbeat.proc_table(text)

    assert heartbeat.tree(100, table) == [100, 101, 102]
    assert heartbeat.ps_seconds("1:02:03.25") == 3723.25 and heartbeat.ps_seconds("0:00.50") == 0.5
    assert heartbeat.ps_seconds("2-01:02:03") == 2 * 86400 + 3723.0
