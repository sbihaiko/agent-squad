"""Tooling guards. Guard for the mypy rule: `mypy squad` finds no error."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

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


def test_no_script_invokes_lsof_by_a_platform_specific_path():
    """A guard for the portability rule: `/usr/sbin/lsof` holds on macOS and is absent on the CI runner, where a
    hardcoded path turns a missing tool into a failed pass (ADR-0018, ADR-0133). Every call site reads the one
    resolution: `env.sh` exports `SG_LSOF`, and the two modules take it from there."""
    offenders = []
    for p in sorted((ROOT / "contrib" / "squad-goal" / "scripts").glob("*")):
        if not p.is_file() or p.suffix not in (".sh", ".py"):
            continue
        for n, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
            if "/usr/sbin/lsof" in line and "SG_LSOF" not in line and not line.lstrip().startswith("#"):
                offenders.append(f"{p.relative_to(ROOT)}:{n}: {line.strip()}")
    assert not offenders, "a call site hardcodes lsof's macOS path:\n" + "\n".join(offenders)


def test_every_process_table_read_of_a_command_asks_for_the_full_one():
    """A guard for the portability rule: GNU ps cuts the command column at the output width, so on the CI runner
    `python .../squad.dashboard_server <folder>` arrives without its folder, a match on the dashboard finds nothing,
    and the pass reads a process table it cannot see (ADR-0018, ADR-0133). A read that wants a command asks for the
    wide form, `-ww`."""
    offenders = []
    for p in sorted((ROOT / "contrib" / "squad-goal" / "scripts").glob("*")):
        if not p.is_file() or p.suffix not in (".sh", ".py"):
            continue
        for n, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
            if "/bin/ps" in line and ("command=" in line or "args=" in line) and "ww" not in line:
                offenders.append(f"{p.relative_to(ROOT)}:{n}: {line.strip()[:120]}")
    assert not offenders, "a ps read of a command is not asked for the wide form:\n" + "\n".join(offenders)


def test_ci_workflow_triggers_one_job_that_covers_both_pythons_and_keeps_the_token_read_only():
    """Guard for the CI rule: a landing push to dev, stg or main starts the run, and nothing else (#398).

    Python 3.11 and 3.13 run inside one job, with a read-only token. Only the three long-lived branches are named, so a
    work-branch push starts no run and a pull request starts none; a work branch is proven by the local fast gate.
    """
    import yaml

    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    on = wf.get("on", wf.get(True))  # PyYAML reads a bare `on` as True
    assert on["push"]["branches"] == ["dev", "stg", "main"], "CI runs only on a landing in dev, stg or main"
    assert set(on) == {"push"}, f"only a push to a long-lived branch starts the run; found {sorted(on)}"
    assert "pull_request" not in on and "pull_request_target" not in on, "a pull request starts no run"
    assert wf["permissions"] == {"contents": "read"}
    job = next(iter(wf["jobs"].values()))
    assert job["timeout-minutes"] == 20, "the job keeps the twenty minutes it gives itself"
    versions = [s["with"]["python-version"] for s in job["steps"] if s.get("uses", "").startswith("actions/setup-python")]
    assert versions == ["3.11", "3.13"], f"one job runs both supported versions in order; found {versions}"
    uses = [s["uses"] for j in wf["jobs"].values() for s in j["steps"] if "uses" in s]
    assert uses and all("@v" in u and u.rsplit("@v", 1)[1].isdigit() for u in uses)


def test_one_commit_starts_one_job_and_a_superseded_head_waits_for_no_runner():
    """Guard for the CI rule: no matrix (one commit, one job), and a landing is never cancelled (#149, #398).

    A matrix made one commit start two jobs that queued behind each other for two runners, and a check no runner picks
    up answers nothing. The group is the branch a push carries, so one branch's pushes share it. Only a landing pushes,
    and a landing keeps its evidence, so the group needs no `github.head_ref`.

    `cancel-in-progress: false` alone is not enough. The group's default queue holds one pending run, so the arrival of
    a third landing cancels the pending second one and the middle landing loses its evidence (GitHub Actions workflow
    syntax, `concurrency.queue`, default `single`). `queue: max` holds every pending landing until it runs, in order.
    """
    import yaml

    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    assert len(wf["jobs"]) == 1, "one commit starts one job"
    job = next(iter(wf["jobs"].values()))
    assert "matrix" not in (job.get("strategy") or {}), "a matrix splits one commit into two jobs waiting for two runners"
    con = wf["concurrency"]
    group = con["group"]
    assert "github.ref_name" in group and "github.head_ref" not in group, group
    assert not re.search(r"github\.ref\b", group), f"{group}: the group names the branch, not the full ref"
    assert con["cancel-in-progress"] is False, "a landing on dev, stg or main is never cancelled, and no other push starts a run"
    assert con.get("queue") == "max", (
        "the group's default queue holds one pending run, so the next landing cancels a pending one and the middle "
        "landing loses its evidence; `queue: max` holds every pending landing until it runs"
    )


E2E = ROOT / "tests" / "test_gui_test_e2e.py"
ADR_0139 = ROOT / "docs" / "adr" / "0139-a-test-without-its-fixture-repo-is-deselected.md"
HOST_NAMES = ("MESENAI_REPO", "MESENAI_ROOT")


def _host_bound_names() -> set[str]:
    """The tests that carry `@pytest.mark.host_bound` in the e2e file: the marker is the rule ADR-0139 states."""
    return set(re.findall(r"@pytest\.mark\.host_bound\s*\ndef (\w+)\(", E2E.read_text()))


def _no_host_env() -> dict:
    return {k: v for k, v in os.environ.items() if k not in HOST_NAMES}


def _collect_e2e():
    return subprocess.run([sys.executable, "-m", "pytest", str(E2E), "--collect-only", "-p", "no:cacheprovider"],
                          cwd=ROOT, env=_no_host_env(), capture_output=True, text=True)


@pytest.mark.heavy
def test_the_host_bound_tests_are_deselected_and_never_run_when_the_host_is_absent():
    """Guard for ADR-0139: with no MesenAI checkout the e2e module imports and collects, every host-bound test is
    deselected by name, none is collected to run, and none is skipped."""
    host = _host_bound_names()
    assert host, "the e2e file marks no host-bound test"
    r = _collect_e2e()
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout + r.stderr
    assert "tests collected" in out, out
    n = re.search(r"\((\d+) deselected\)", out)
    assert n, out
    assert int(n.group(1)) == len(host), out
    collected = set(re.findall(r"::(\w+)", out))
    assert host.isdisjoint(collected), sorted(host & collected)
    assert "skipped" not in out, "a host-bound test was skipped, not deselected"


@pytest.mark.heavy
def test_the_host_free_tests_in_the_e2e_file_still_run_without_the_host():
    """Guard for ADR-0139: the deselection removes only the host-bound tests; the rest of the file runs green."""
    collected = _collect_e2e().stdout
    assert "::test_three_step_script_runs_against_a_fake_adapter" in collected
    run = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider",
         f"{E2E}::test_three_step_script_runs_against_a_fake_adapter",
         f"{E2E}::test_rendering_is_deterministic_and_lists_every_step"],
        cwd=ROOT, env=_no_host_env(), capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "2 passed" in run.stdout, run.stdout


def test_the_deselection_adr_records_the_rule_and_is_indexed():
    """Guard for ADR-0139: the ADR exists, carries the rule and its guard, and the index lists it."""
    text = ADR_0139.read_text()
    low = text.lower()
    assert "## Guard" in text and "`tests/test_tooling.py`" in text
    assert "deselected" in low and "skip" in low and "its own ci step" in low and "by name" in low
    assert "[0139](0139-a-test-without-its-fixture-repo-is-deselected.md)" in (ROOT / "docs" / "adr" / "README.md").read_text()


def test_ci_e2e_step_names_no_mesenai_path_the_job_never_checks_out():
    """Guard for ADR-0139: the e2e step keeps its place but sets no MESENAI_REPO path the job never checks out, and
    the gate step keeps its own command."""
    import yaml

    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    steps = [s for j in wf["jobs"].values() for s in j["steps"]]
    e2e = [s for s in steps if "test_gui_test_e2e.py" in (s.get("run") or "")]
    assert len(e2e) == 1, steps
    assert "MESENAI_REPO" not in json.dumps(e2e[0]), e2e[0]
    assert e2e[0].get("env", {}) == {}, e2e[0]
    assert any("pytest -n auto" in r for r in _ci_run_lines()), "the gate step must keep its own command"


def test_ci_gate_steps_run_the_fast_battery_on_both_pythons():
    """Guard for the suite rule: each gate step runs the fast battery, which leaves the `heavy` tests out."""
    gates = [r for r in _ci_run_lines() if r.startswith("python3 -m pytest") and "test_gui_test_e2e.py" not in r]
    assert gates == ['python3 -m pytest -n auto -m "not heavy"'] * 2, gates
