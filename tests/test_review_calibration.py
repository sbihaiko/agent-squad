"""The reviewer calibration report reads its committed labeled sample."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "contrib/squad-goal/scripts/review-calibration.py"
FIXTURE = ROOT / "contrib/squad-goal/scripts/fixtures/review-calibration.json"


def test_report_counts_debt_only_changes_in_the_labeled_sample() -> None:
    result = subprocess.run(
        ["python3", str(SCRIPT), str(FIXTURE)],
        capture_output=True,
        check=False,
        cwd=ROOT,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "labeled=10 debt_nit_only=1 rate=10.0%\n"
