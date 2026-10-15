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
    assert r.stdout.splitlines() == ["1169\treview:PR#1\ta,b\t2", "1000\tdispatch:d1\ta\t1"]


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
    assert r.stdout.splitlines() == ["10\trun:run-1:scout\tm\t1", "7\trun:run-1\tm\t1"]


def test_json_and_threshold(tmp_path: Path) -> None:
    _ledger(tmp_path, [(1, "review:PR#1", "a", 600, 0, 0), (1, "janitor:board", "a", 300, 0, 0)])
    j = json.loads(_run(str(tmp_path), "--json", "--threshold", "500").stdout)
    assert (j["window_hours"], j["threshold"], j["total"], j["over"]) == (24, 500, 900, True)
    assert j["burners"][0] == {"consumer": "review:PR#1", "tokens": 600, "calls": 1, "models": ["a"]}
    assert "review:PR#1" in j["brief"]
    j = json.loads(_run(str(tmp_path), "--json", "--threshold", "5000").stdout)
    assert j["over"] is False and j["brief"] == ""


def test_threshold_from_env(tmp_path: Path) -> None:
    import os
    _ledger(tmp_path, [(1, "review:PR#1", "a", 600, 0, 0)])
    j = json.loads(_run(str(tmp_path), "--json", env={**os.environ, "SG_TOKEN_ALERT": "100"}).stdout)
    assert j["threshold"] == 100 and j["over"] is True
