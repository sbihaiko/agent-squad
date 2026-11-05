"""The shadow sample of a grok verdict (#1403): its sample rule, its daily cap and its ledger line."""

from __future__ import annotations

import datetime
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "contrib/squad-goal/scripts/review-shadow.py"


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["python3", str(SCRIPT), *args], capture_output=True, check=False, cwd=ROOT, text=True)


def utc(day: str) -> str:
    return f"{day}T00:00:00Z"


def today() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d")


def shadow_row(ts: str | None = None, blocking: int = 0, debt: int = 0) -> dict:
    return {"ts": ts or utc(today()), "key": "fix_1-x", "head": "aaa111", "model": "claude-opus-5-5",
            "effort": "medium", "verdict": "VERDICT: APPROVE", "shadow": True, "blocking": blocking, "debt": debt}


def ledger(state: Path, rows: list[dict]) -> None:
    state.joinpath("review-ledger.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))


def decide(state: Path, blocking: int) -> subprocess.CompletedProcess:
    return run("decide", str(state / "review-ledger.jsonl"), "--blocking", str(blocking))


def test_a_grok_verdict_with_no_blocking_finding_is_never_shadowed(tmp_path: Path) -> None:
    ledger(tmp_path, [])

    result = decide(tmp_path, 0)

    assert result.returncode == 1
    assert "Blocking" in result.stdout


def test_a_missing_ledger_starts_the_sample(tmp_path: Path) -> None:
    result = decide(tmp_path, 1)

    assert result.returncode == 0
    assert "run" in result.stdout


def test_every_blocking_verdict_is_shadowed_until_thirty_samples(tmp_path: Path) -> None:
    ledger(tmp_path, [shadow_row(ts=utc("2026-01-01")) for _ in range(29)])

    result = decide(tmp_path, 1)

    assert result.returncode == 0


def test_the_one_in_four_rate_starts_at_thirty_samples(tmp_path: Path) -> None:
    old = [shadow_row(ts=utc("2026-01-01")) for _ in range(30)]
    ledger(tmp_path, old)
    assert decide(tmp_path, 1).returncode == 0        # the first sample of the 25% phase

    ledger(tmp_path, old + [shadow_row(ts=utc("2026-01-01"))])
    assert decide(tmp_path, 1).returncode == 1        # then three are skipped

    ledger(tmp_path, old + [shadow_row(ts=utc("2026-01-01")) for _ in range(4)])
    assert decide(tmp_path, 1).returncode == 0        # and one in four is taken


def test_ten_shadow_runs_in_one_day_stop_the_sample(tmp_path: Path) -> None:
    ledger(tmp_path, [shadow_row() for _ in range(10)])

    result = decide(tmp_path, 1)

    assert result.returncode == 1
    assert "cap" in result.stdout


def test_the_daily_cap_counts_only_the_runs_of_today(tmp_path: Path) -> None:
    ledger(tmp_path, [shadow_row(ts=utc("2026-01-01")) for _ in range(10)])

    assert decide(tmp_path, 1).returncode == 0


def test_record_appends_one_shadow_line_with_its_model_verdict_and_counts(tmp_path: Path) -> None:
    path = tmp_path / "review-ledger.jsonl"
    path.write_text(json.dumps({"model": "grok-4.7", "verdict": "VERDICT: CHANGES"}) + "\n")

    result = run("record", str(path), "--key", "fix_1-x", "--head", "aaa111", "--model", "claude-opus-5-5",
                 "--verdict", "VERDICT: APPROVE", "--blocking", "0", "--debt", "2", "--effort", "low")

    assert result.returncode == 0, result.stderr
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(lines) == 2
    assert lines[0]["model"] == "grok-4.7"
    row = lines[1]
    assert row["shadow"] is True
    assert row["model"] == "claude-opus-5-5"
    assert row["verdict"] == "VERDICT: APPROVE"
    assert row["blocking"] == 0
    assert row["debt"] == 2
    assert row["key"] == "fix_1-x"
    assert row["head"] == "aaa111"
    assert row["effort"] == "low"
    assert row["ts"].endswith("Z")
