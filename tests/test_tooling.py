"""Tooling guards. Guard for the mypy rule: `mypy squad` finds no error."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.slow(reason="mypy builds its cache from nothing in a fresh tree: about 9 s alone, and it waits for a CPU under load")
@pytest.mark.heavy
def test_mypy_squad_is_clean():
    """Guard for the mypy rule: `mypy squad` finds no error.

    The call carries `heavy` and a reason: a tree with no `.mypy_cache` makes mypy analyze the package from nothing,
    and the suite's own line for an unmarked call (2 s) is measured on a warm cache only. The CI check job runs
    `mypy squad` as its own step, which `test_ci_check_job_runs_mypy_squad_as_its_own_step` proves in the default run.
    """
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


def test_every_gate_lints_the_scope_this_repository_owns():
    """Guard for the lint scope: the CI workflow and every local gate lint `squad tests`.

    `ruff check .` also lints the delivery loop's own scripts under contrib/squad-goal/scripts, which the repository never
    lints, and it is red at every tip: 1378 findings at ebb70f8, all of them there. The fast gate's default battery drifted
    to `.` while branchland.sh, wave.sh and ci.yml keep the repository's own scope, so a child entering a wave could never
    pass its gate and no wave could land (#447).

    The battery is one definition, `base-battery.sh`: a gate that runs it (`branchland.sh`, `wave.sh`, `fast-gate.sh`)
    keeps no copy, so the scope is read from the definition the gate names.
    """
    battery = ROOT / "contrib/squad-goal/scripts/base-battery.sh"
    for f in ("contrib/squad-goal/scripts/branchland.sh", "contrib/squad-goal/scripts/wave.sh",
              "contrib/squad-goal/scripts/fast-gate.sh", ".github/workflows/ci.yml"):
        # The command lines only: the comment above a gate may name the wrong scope it was warned about, and does.
        body = "\n".join(line for line in (ROOT / f).read_text().splitlines() if not line.lstrip().startswith("#"))
        if "base-battery.sh" in body:
            body += "\n" + "\n".join(line for line in battery.read_text().splitlines() if not line.lstrip().startswith("#"))
        assert "ruff check squad tests" in body, f"{f} does not lint the scope this repository owns"
        assert "ruff check ." not in body, f"{f} lints a wider scope than the repository owns"


def test_the_wave_check_keeps_the_output_of_a_red_tip():
    """Guard for the diagnosis path: a red tip check writes what it printed to a named log, never `/dev/null` (#447)."""
    txt = (ROOT / "contrib/squad-goal/scripts/wave.sh").read_text()
    assert 'clog="$SG_STATE/wave-check-' in txt and 'eval "$tipgate") >"$clog" 2>&1' in txt, \
        "the wave's tip check throws its output away, so a red one names no cause"
    assert "the check's own output is $clog" in txt, "the WAVE CHECK RED line does not name the log"


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


def test_ci_check_job_runs_mypy_squad_as_its_own_step():
    """Guard for the mypy rule: the CI check job runs `mypy squad` as a step of its own, once for each supported
    Python. The tree's own mypy call carries `heavy`, so the rule keeps this fast gate in the default run (ADR-0018)."""
    runs = _ci_run_lines()
    assert runs.count("mypy squad") == 2, (
        f"the CI check job runs `mypy squad` as its own step, once per supported Python; found {runs}"
    )


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
    """Guard for the CI rule: the one pull request that carries dev to stg starts the run, and nothing else.

    Python 3.11 and 3.13 run inside one job, with a read-only token. No branch push starts a run: a work branch is
    proven by the local fast gate, and the landing by the promotion proof. A pull request to any other branch starts
    none, and neither does a pull request to stg from a branch other than dev.
    """
    import yaml

    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    on = wf.get("on", wf.get(True))  # PyYAML reads a bare `on` as True
    assert on["pull_request"]["branches"] == ["stg"], "CI runs only on the pull request that carries dev to stg"
    assert set(on) == {"pull_request"}, f"only that pull request starts the run; found {sorted(on)}"
    assert "push" not in on and "pull_request_target" not in on, "a branch push starts no run"
    assert wf["permissions"] == {"contents": "read"}
    job = next(iter(wf["jobs"].values()))
    assert "dev" in str(job.get("if", "")), f"the job refuses a pull request from any branch but dev: {job.get('if')!r}"
    assert job["timeout-minutes"] == 20, "the job keeps the twenty minutes it gives itself"
    versions = [s["with"]["python-version"] for s in job["steps"] if s.get("uses", "").startswith("actions/setup-python")]
    assert versions == ["3.11", "3.13"], f"one job runs both supported versions in order; found {versions}"
    uses = [s["uses"] for j in wf["jobs"].values() for s in j["steps"] if "uses" in s]
    assert uses and all("@v" in u and u.rsplit("@v", 1)[1].isdigit() for u in uses)


# The sentence the workflow states for its landing group. A group that names the commit holds one run and nothing
# pending, so no queue cap applies and GitHub cancels no landing.
LANDING_GROUP_RULE = "The group names the commit, so no queue holds a pull request behind another and GitHub cancels none."


def test_one_commit_starts_one_job_and_a_landing_waits_in_no_queue():
    """Guard for the CI rule: no matrix (one commit, one job), a landing gets a concurrency group of its own, and a
    landing on dev, stg or main is never cancelled (#149, #398).

    A matrix made one commit start two jobs that queued behind each other for two runners, and a check no runner picks
    up answers nothing.

    A group shared by one branch's pushes cannot keep that promise: GitHub queues the group's pending runs and cancels
    the runs past the queue's cap (`queue: single` holds one pending run, `queue: max` holds 100), so a burst of
    landings on one branch cancels a landing. A group that names the commit holds one run and nothing pending, so no
    cap applies and GitHub cancels none. The guard checks the group, the cancel semantics, and the workflow's own
    sentence for the rule.
    """
    import yaml

    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    wf = yaml.safe_load(text)
    assert len(wf["jobs"]) == 1, "one commit starts one job"
    job = next(iter(wf["jobs"].values()))
    assert "matrix" not in (job.get("strategy") or {}), "a matrix splits one commit into two jobs waiting for two runners"
    con = wf["concurrency"]
    group = con["group"]
    assert "github.sha" in group, (
        f"{group}: a group shared by a branch's pushes queues them, and GitHub cancels a run past the queue's cap, so a "
        "landing on dev, stg or main can be cancelled; the group names the commit instead"
    )
    assert "github.head_ref" in group, f"{group}: the group names the pull request's own branch, never its base"
    assert not re.search(r"github\.ref\b", group), f"{group}: the group names the branch, not the full ref"
    assert con["cancel-in-progress"] is False, "a pull request's run is never cancelled, and no other event starts one"
    assert "queue" not in con, (
        f"the group holds nothing pending, so no queue cap can cancel a landing; found queue={con.get('queue')!r}"
    )
    stated = " ".join(
        line.lstrip("#").strip() for line in text.split("concurrency:", 1)[0].splitlines() if line.startswith("#")
    )
    assert LANDING_GROUP_RULE in stated, f"the workflow states the rule its group carries: {LANDING_GROUP_RULE}"


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


# #793: a fixture that runs the real autobug.sh or autofrontier.sh pins its own disk floor. Without the pin the
# dispatcher reads the machine's free space, so a shared runner or a full volume decides the test (#615).
# A run is the dispatcher as a command: first word, after `bash`, `exec`, or a `;`, `&&`, `|` or `(`. A path named as an
# argument (cp, chmod, grep -l, a printf stub) is a file operation, not a run.
_DISPATCHER_RUN = re.compile(r"(?:^|[;&|(]\s*|\bexec\s+|\bbash\s+)\"?[^\s\"]*/?(?:autobug|autofrontier)\.sh\"?(?=\s|$)")
_NOT_A_RUN = re.compile(r"^\s*#|\bprintf\b|\becho\b|\bgrep\b|>\s*\"?\$?\S*(?:autobug|autofrontier)\.sh")


def _runs_a_dispatcher_without_pin(text):
    """True when a non-comment line runs the real dispatcher and no SG_MIN_FREE_GB assignment is in the file."""
    runs = any(_DISPATCHER_RUN.search(line) and not _NOT_A_RUN.search(line) for line in text.splitlines())
    return runs and not re.search(r"(?m)^\s*(?:export\s+)?SG_MIN_FREE_GB=", text)


def test_a_dispatcher_fixture_pins_its_disk_floor_in_the_text():
    """The guard itself: a fixture that runs the dispatcher with no pin is caught; a stub or a pinned fixture is not."""
    assert _runs_a_dispatcher_without_pin('bash "$T/s/autobug.sh"\n')
    assert _runs_a_dispatcher_without_pin('bash "$S/autofrontier.sh" 2>&1\n')
    assert _runs_a_dispatcher_without_pin('x=1; "$S/autobug.sh"\n')
    assert not _runs_a_dispatcher_without_pin('chmod +x "$F/autobug.sh"\ncp "$S/autobug.sh" "$F2/"\n')
    assert not _runs_a_dispatcher_without_pin('export SG_MIN_FREE_GB=0\nbash "$T/s/autobug.sh"\n')
    assert not _runs_a_dispatcher_without_pin('printf "#!/bin/bash\\nexit 0\\n" > "$F/autobug.sh"\n')
    assert not _runs_a_dispatcher_without_pin("# bash autobug.sh is run by the loop\n")


def test_every_shell_fixture_that_runs_a_dispatcher_pins_its_disk_floor():
    scripts = ROOT / "contrib/squad-goal/scripts"
    unpinned = [p.name for p in sorted(scripts.glob("test-*.sh")) if _runs_a_dispatcher_without_pin(p.read_text(errors="replace"))]
    assert unpinned == [], f"these fixtures run autobug.sh or autofrontier.sh with no SG_MIN_FREE_GB pin: {unpinned}"
