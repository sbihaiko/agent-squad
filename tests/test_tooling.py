"""Tooling guards. Guard for the mypy rule: `mypy squad` finds no error."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_mypy_squad_is_clean():
    r = subprocess.run(
        [sys.executable, "-m", "mypy", "squad"], cwd=ROOT, capture_output=True, text=True
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_ruff_check_squad_and_tests_is_clean():
    """Guard for the ruff rule: `ruff check squad tests` finds nothing. A finding is a defect."""
    r = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "squad", "tests"], cwd=ROOT, capture_output=True, text=True
    )
    assert r.returncode == 0, r.stdout + r.stderr


def _ci_run_lines() -> list[str]:
    import yaml

    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    return [step["run"] for job in wf["jobs"].values() for step in job["steps"] if "run" in step]


def test_ci_workflow_runs_ruff_mypy_and_pytest_in_order():
    """Guard for the CI rule: ci.yml runs ruff, then mypy, then pytest."""
    runs = _ci_run_lines()
    want = ["ruff check squad tests", "mypy squad", "python3 -m pytest"]
    idx = [next((i for i, r in enumerate(runs) if w in r), None) for w in want]
    assert None not in idx, f"ci.yml misses a step; found {runs}"
    assert idx == sorted(idx), "ci.yml must run ruff, mypy, pytest in that order"


def test_ci_workflow_triggers_python_matrix_and_permissions():
    """Guard for the CI rule: push to main, pull requests, Python 3.11 and 3.13, read-only token."""
    import yaml

    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    on = wf.get("on", wf.get(True))  # PyYAML reads a bare `on` as True
    assert on["push"]["branches"] == ["main"] and "pull_request" in on
    assert wf["permissions"] == {"contents": "read"}
    job = next(iter(wf["jobs"].values()))
    assert job["strategy"]["matrix"]["python-version"] == ["3.11", "3.13"]
    uses = [s["uses"] for j in wf["jobs"].values() for s in j["steps"] if "uses" in s]
    assert uses and all("@v" in u and u.rsplit("@v", 1)[1].isdigit() for u in uses)
