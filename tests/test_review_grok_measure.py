"""The grok debt-only measurement reads the loop's own state files and invents no number."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "contrib/squad-goal/scripts/review-grok-measure.py"


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["python3", str(SCRIPT), *args], capture_output=True, check=False, cwd=ROOT, text=True)


def ledger(state: Path, rows: list[dict]) -> None:
    state.joinpath("review-ledger.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))


def row(key="fix_1-x", head="aaa111", model="grok-4.7", verdict="VERDICT: CHANGES", fix_round=False,
        blocking=0, debt=1, effort="low") -> dict:
    return {"ts": "2026-11-05T00:00:00Z", "key": key, "head": head, "model": model, "effort": effort,
            "verdict": verdict, "fix_round": fix_round, "blocking": blocking, "debt": debt}


def measure(state: Path, *extra: str) -> dict:
    result = run(str(state), *extra)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_a_missing_ledger_is_unavailable_never_zero(tmp_path: Path) -> None:
    out = measure(tmp_path)

    assert out["reviews"] is None
    assert out["grok_debt_only"] is None
    assert out["unavailable"] == ["review-ledger.jsonl", "review-attempts.tsv", "review-times.tsv"]
    assert out["decision"]["exemption"] == "pending"


def test_a_file_that_answered_names_no_unavailable_field(tmp_path: Path) -> None:
    ledger(tmp_path, [row()])
    tmp_path.joinpath("review-attempts.tsv").write_text("2026-11-05T00:00:00Z\tfix_1-x\tgrok-4.7\tlow\t40\t0\n")

    out = measure(tmp_path)

    assert out["unavailable"] == []


def test_counts_grok_debt_only_and_ignores_other_models(tmp_path: Path) -> None:
    ledger(tmp_path, [
        row(),
        row(head="bbb222", debt=2),
        row(model="claude-opus-5-5", head="ccc333", verdict="VERDICT: APPROVE", blocking=0, debt=0),
    ])

    out = measure(tmp_path)

    assert out["reviews"] == 3
    assert out["grok_reviews"] == 2
    assert out["grok_debt_only"] == 2
    assert out["debt_only_rate"] == "100.0%"
    assert out["debt_only_by_model"] == {
        "grok-4.7": {"reviews": 2, "debt_only": 2},
        "claude-opus-5-5": {"reviews": 1, "debt_only": 0},
    }


def test_a_grok_blocking_verdict_is_not_debt_only(tmp_path: Path) -> None:
    ledger(tmp_path, [row(blocking=1, debt=1)])

    out = measure(tmp_path)

    assert out["grok_debt_only"] == 0


def test_a_fix_round_after_grok_debt_only_counts_and_its_verdict_change_is_read(tmp_path: Path) -> None:
    ledger(tmp_path, [
        row(head="aaa111"),
        row(head="bbb222", verdict="VERDICT: APPROVE", fix_round=True, blocking=0, debt=0),
    ])

    out = measure(tmp_path)

    assert out["grok_fix_rounds"] == 1
    assert out["grok_fix_round_verdict_changes"] == 1
    assert out["grok_fix_round_kept_changes"] == 0


def test_a_full_review_after_grok_debt_only_is_not_a_fix_round(tmp_path: Path) -> None:
    ledger(tmp_path, [row(head="aaa111"), row(head="bbb222", fix_round=False)])

    out = measure(tmp_path)

    assert out["grok_fix_rounds"] == 0


def test_a_fix_round_on_the_same_head_is_not_counted(tmp_path: Path) -> None:
    ledger(tmp_path, [row(head="aaa111"), row(head="aaa111", fix_round=True)])

    out = measure(tmp_path)

    assert out["grok_fix_rounds"] == 0


def test_a_debt_only_changes_after_an_approval_is_not_a_fix_round(tmp_path: Path) -> None:
    ledger(tmp_path, [
        row(head="aaa111", verdict="VERDICT: APPROVE", blocking=0, debt=0),
        row(head="bbb222", fix_round=True),
    ])

    out = measure(tmp_path)

    assert out["grok_fix_rounds"] == 0


def test_a_row_with_no_readable_counts_is_unattributed_never_a_verdict(tmp_path: Path) -> None:
    ledger(tmp_path, [row(blocking=None, debt=None)])

    out = measure(tmp_path)

    assert out["unattributed_rows"] == 1
    assert out["grok_debt_only"] == 0


def test_grok_timeouts_and_fallthroughs_are_counted_by_kind(tmp_path: Path) -> None:
    tmp_path.joinpath("review-attempts.tsv").write_text(
        "2026-11-05T00:00:00Z\tfix_1-x\tgrok-4.7\tlow\t40\t0\n"
        "2026-11-05T00:01:00Z\tfix_1-x\tgrok-4.7\tlow\t480\t142\n"
        "2026-11-05T00:02:00Z\tfix_1-x\tgrok-4.5\tmedium\t5\t1\n"
        "2026-11-05T00:03:00Z\tfix_1-x\tclaude-opus-5-5\tmedium\t30\t142\n"
    )

    out = measure(tmp_path)

    assert out["grok_attempts"] == 3
    assert out["grok_timeouts"] == 1
    assert out["grok_fallthroughs"] == 2
    assert out["grok_low_effort_attempts"] == 2


def test_an_attempts_file_absent_falls_back_to_the_times_file_without_effort(tmp_path: Path) -> None:
    tmp_path.joinpath("review-times.tsv").write_text("2026-11-05T00:00:00Z\tfix_1-x\tgrok-4.7\t480\t142\n")

    out = measure(tmp_path)

    assert out["grok_attempts"] == 1
    assert out["grok_timeouts"] == 1
    assert out["grok_low_effort_attempts"] is None


def test_a_grok_blocking_that_another_model_approved_on_the_same_head_is_a_catch(tmp_path: Path) -> None:
    ledger(tmp_path, [
        row(head="aaa111", blocking=1, debt=0),
        row(head="aaa111", model="claude-opus-5-5", verdict="VERDICT: APPROVE"),
    ])

    out = measure(tmp_path)

    assert out["paired_heads"] == 1
    assert out["next_entry_catch"] == 1
    assert out["next_entry_approved_unflagged"] == 0


def test_a_pair_where_another_model_approved_and_grok_flagged_nothing_is_not_a_catch(tmp_path: Path) -> None:
    ledger(tmp_path, [
        row(verdict="VERDICT: APPROVE", blocking=0, debt=0),
        row(model="claude-opus-5-5", verdict="VERDICT: APPROVE"),
    ])

    out = measure(tmp_path)

    assert out["paired_heads"] == 1
    assert out["next_entry_catch"] == 0
    assert out["next_entry_approved_unflagged"] == 1


def test_no_paired_head_reports_unavailable_never_zero(tmp_path: Path) -> None:
    ledger(tmp_path, [row()])

    out = measure(tmp_path)

    assert out["paired_heads"] == 0
    assert out["next_entry_catch"] is None
    assert "no two models reviewed one head" in out["next_entry_catch_reason"]


def test_one_grok_model_with_a_debt_only_review_is_pending_a_second_model(tmp_path: Path) -> None:
    ledger(tmp_path, [row()])

    out = measure(tmp_path)

    assert out["decision"]["exemption"] == "pending"
    assert "grok-4.7" in out["decision"]["reason"]


def test_every_grok_model_with_a_review_showing_debt_only_names_the_vendor(tmp_path: Path) -> None:
    ledger(tmp_path, [row(), row(model="grok-4.5", head="bbb222")])

    out = measure(tmp_path)

    assert out["decision"]["exemption"] == "vendor-name"


def test_only_one_grok_model_showing_debt_only_names_that_model(tmp_path: Path) -> None:
    ledger(tmp_path, [
        row(model="grok-4.7"),
        row(model="grok-4.5", head="bbb222", verdict="VERDICT: APPROVE", blocking=0, debt=0),
    ])

    out = measure(tmp_path)

    assert out["decision"]["exemption"] == "per-model"
    assert "grok-4.7" in out["decision"]["reason"]
    assert out["decision"]["models"] == ["grok-4.7"]


def test_a_state_dir_is_required() -> None:
    result = run()

    assert result.returncode == 2
    assert "usage" in result.stderr
