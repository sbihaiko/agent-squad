"""The token ledger: review.sh writes one line per model call (`test-token-ledger.sh`, run by the shell-suite runner), `tokens.py burners` names the biggest consumers.

Ledger line: `<iso-ts>\\t<consumer>\\t<model>\\t<tokens_in>\\t<tokens_out>\\t<cache_read>`, no header.
The hub reads the reader; it never reads the ledger. Host-free: throwaway state and checkout folders.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "contrib/squad-goal/scripts"


def _run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPTS / "tokens.py"), "burners", *args],
                          capture_output=True, text=True, timeout=30, env=env)


def _ts(hours_ago: float) -> str:
    return (datetime.now() - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%S")


def _ledger(state: Path, rows: list[tuple[float, str, str, int, int, int]]) -> None:
    state.mkdir(exist_ok=True)
    text = "".join(f"{_ts(h)}\t{c}\t{m}\t{i}\t{o}\t{r}\n" for h, c, m, i, o, r in rows)
    (state / "token-usage.tsv").write_text(text + "garbage line\nx\ty\n")


def test_burners_sorted_by_total_with_window(tmp_path: Path) -> None:
    _ledger(tmp_path, [(1, "review:PR#1", "a", 100, 50, 999), (2, "review:PR#1", "b", 10, 10, 0),
                       (1, "dispatch:d1", "a", 1000, 0, 0), (30, "janitor:board", "a", 9999, 0, 0)])
    r = _run(str(tmp_path))
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines() == ["1169\treview:PR#1\ta,b\t2\t0", "1000\tdispatch:d1\ta\t1\t0"]


def test_empty_window_prints_dash(tmp_path: Path) -> None:
    assert _run(str(tmp_path)).stdout.strip() == "-"


def test_runs_outcome_events_are_consumers(tmp_path: Path) -> None:
    run = tmp_path / "co/runs/run-1"
    run.mkdir(parents=True)
    ev = [{"event": "outcome", "ts": _ts(1), "model": "m", "tokens_in": 5, "tokens_out": 5, "cache_read": 0, "node": "scout"},
          {"event": "outcome", "ts": _ts(1), "model": "m", "tokens_in": 7, "tokens_out": 0},
          {"event": "other", "ts": _ts(1), "tokens_in": 99}]
    (run / "record.jsonl").write_text("\n".join(json.dumps(e) for e in ev) + "\nnot json\n")
    r = _run(str(tmp_path), "--checkout", str(tmp_path / "co"))
    assert r.stdout.splitlines() == ["10\trun:run-1:scout\tm\t1\t0", "7\trun:run-1\tm\t1\t0"]


def test_a_run_in_a_run_or_child_worktree_is_a_consumer(tmp_path: Path) -> None:
    # #580 keeps a loop run's folder in its own worktree, and a fork route's in its child tree (#585)
    for rel, n in (("co/worktrees/run/k1/runs/run-a", 3), ("co/worktrees/child/gui-9/runs/run-b", 2)):
        run = tmp_path / rel
        run.mkdir(parents=True)
        ev = {"event": "outcome", "ts": _ts(1), "model": "m", "tokens_in": n, "tokens_out": 0, "node": "c"}
        (run / "record.jsonl").write_text(json.dumps(ev) + "\n")
    r = _run(str(tmp_path), "--checkout", str(tmp_path / "co"))
    assert r.stdout.splitlines() == ["3\trun:run-a:c\tm\t1\t0", "2\trun:run-b:c\tm\t1\t0"]


def test_a_run_outcome_counts_its_cache_reads_once_at_their_weight(tmp_path: Path) -> None:
    # an outcome's tokens_in holds every prompt token, cache reads and writes included (squad/sdk_adapter.py), unlike a
    # ledger line, whose cache_read is beside tokens_in: 600 in of which 550 cache reads and 40 cache writes, 10 out
    run = tmp_path / "co/runs/run-1"
    run.mkdir(parents=True)
    ev = {"event": "outcome", "ts": _ts(1), "model": "m", "tokens_in": 600, "tokens_out": 10, "cache_read": 550,
          "cache_write": 40, "node": "c"}
    (run / "record.jsonl").write_text(json.dumps(ev) + "\n")
    for weight, total in (("0", 60), ("1", 610)):
        r = _run(str(tmp_path), "--checkout", str(tmp_path / "co"), "--cache-weight", weight)
        assert r.stdout.splitlines() == [f"{total}\trun:run-1:c\tm\t1\t0"], weight


def test_json_and_threshold(tmp_path: Path) -> None:
    _ledger(tmp_path, [(1, "review:PR#1", "a", 600, 0, 0), (1, "janitor:board", "a", 300, 0, 0)])
    j = json.loads(_run(str(tmp_path), "--json", "--threshold", "500").stdout)
    assert (j["window_hours"], j["threshold"], j["total"], j["over"]) == (24, 500, 900, True)
    assert j["uncounted"] == 0
    assert j["burners"][0] == {"consumer": "review:PR#1", "tokens": 600, "calls": 1, "uncounted": 0, "models": ["a"]}
    assert "review:PR#1" in j["brief"]
    j = json.loads(_run(str(tmp_path), "--json", "--threshold", "5000").stdout)
    assert j["over"] is False and j["brief"] == ""


def test_a_call_without_a_count_is_named_in_both_readings(tmp_path: Path) -> None:
    # A claude line that reported no usage carries the mark `no-usage` in a 7th field (review.sh writes it). It weighs 0,
    # so the total is a floor; the reading must say so rather than let a review pass that reviewed nothing look healthy.
    _ledger(tmp_path, [(1, "review:PR#1", "claude:claude-opus-5-5", 500, 0, 0)])
    with open(tmp_path / "token-usage.tsv", "a") as f:
        f.write(f"{_ts(1)}\treview:PR#2\tclaude:claude-opus-5-5\t0\t0\t0\tno-usage\n")
        f.write(f"{_ts(1)}\treview:PR#2\tclaude:claude-opus-5-5\t0\t0\t0\tno-usage\n")
    assert _run(str(tmp_path)).stdout.splitlines() == [
        "500\treview:PR#1\tclaude:claude-opus-5-5\t1\t0",
        "0\treview:PR#2\tclaude:claude-opus-5-5\t2\t2",
        "# floor, not a measure: 2 calls carried no token count",
    ]
    j = json.loads(_run(str(tmp_path), "--json").stdout)
    assert j["uncounted"] == 2
    assert j["burners"][1]["uncounted"] == 2 and j["burners"][1]["tokens"] == 0


def test_threshold_from_env(tmp_path: Path) -> None:
    import os
    _ledger(tmp_path, [(1, "review:PR#1", "a", 600, 0, 0)])
    j = json.loads(_run(str(tmp_path), "--json", env={**os.environ, "SG_TOKEN_ALERT": "100"}).stdout)
    assert j["threshold"] == 100 and j["over"] is True


def _append(state: Path, *lines: str) -> None:
    with open(state / "token-usage.tsv", "a") as f:
        f.write("".join(f"{_ts(1)}\t{ln}\n" for ln in lines))


def test_a_non_claude_call_with_no_count_makes_the_total_a_floor(tmp_path: Path) -> None:
    # grok, agy and codex print no count on the plain path: 0 0 0 is logged, the call is still counted. That is a floor.
    _ledger(tmp_path, [(1, "review:PR#1", "claude:claude-opus-5-5", 500, 0, 0)])
    _append(tmp_path, "review:PR#2\tgrok-4\t0\t0\t0", "review:PR#3\tgemini-3.8-flash-high\t0\t0\t0")
    j = json.loads(_run(str(tmp_path), "--json").stdout)
    assert j["unreported"] == 2 and j["floor"] is True
    out = _run(str(tmp_path)).stdout.splitlines()
    assert out[-1].startswith("# floor, not a measure") and "2 calls" in out[-1]


def test_a_measured_ledger_is_not_a_floor(tmp_path: Path) -> None:
    _ledger(tmp_path, [(1, "review:PR#1", "claude:claude-opus-5-5", 500, 0, 0)])
    _append(tmp_path, "review:PR#2\tgrok-4\t10\t5\t0")   # a non-claude call that did carry a count is a measure
    j = json.loads(_run(str(tmp_path), "--json").stdout)
    assert (j["unreported"], j["floor"]) == (0, False)
    assert not any(ln.startswith("#") for ln in _run(str(tmp_path)).stdout.splitlines())


def _verdict(state: Path, *args: str) -> tuple[int, dict]:
    r = subprocess.run([sys.executable, str(SCRIPTS / "tokens.py"), "verdict", str(state), *args],
                       capture_output=True, text=True, timeout=30)
    return r.returncode, json.loads(r.stdout)


def test_verdict_passes_under_the_cap(tmp_path: Path) -> None:
    _ledger(tmp_path, [(1, "review:PR#1", "claude:x", 1000, 0, 0)])
    code, v = _verdict(tmp_path)
    assert code == 0 and v["verdict"] == "pass" and v["total"] == 1000 and v["cap"] == 1500000


def test_verdict_passes_when_the_top_burner_is_not_a_review(tmp_path: Path) -> None:
    _ledger(tmp_path, [(1, "dispatch:d1", "claude:x", 2000000, 0, 0), (1, "review:PR#1", "claude:x", 100, 0, 0)])
    code, v = _verdict(tmp_path)
    assert code == 0 and v["verdict"] == "pass" and v["top"] == "dispatch:d1"


def test_verdict_fails_over_the_cap_with_a_review_on_top(tmp_path: Path) -> None:
    _ledger(tmp_path, [(1, "review:PR#1", "claude:x", 2000000, 0, 0), (1, "dispatch:d1", "claude:x", 100, 0, 0)])
    code, v = _verdict(tmp_path)
    assert code == 1 and v["verdict"] == "fail" and v["top"] == "review:PR#1"
    assert _verdict(tmp_path, "--cap", "3000000")[1]["verdict"] == "pass"


def test_verdict_of_an_empty_ledger_is_not_proven(tmp_path: Path) -> None:
    code, v = _verdict(tmp_path)
    assert code == 2 and v["verdict"] == "not-proven" and v["total"] == 0
    _ledger(tmp_path, [(30, "review:PR#1", "claude:x", 9, 0, 0)])   # only a line outside the window
    assert _verdict(tmp_path)[1]["verdict"] == "not-proven"


def test_verdict_names_a_floor(tmp_path: Path) -> None:
    _ledger(tmp_path, [(1, "review:PR#1", "grok-4", 0, 0, 0)])
    _, v = _verdict(tmp_path)
    assert v["floor"] is True and v["verdict"] == "pass"
