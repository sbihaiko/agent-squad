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


FAST_TIER_JOBS = ("ruff", "mypy", "fast-battery")
# The full suite's two modes run in shard jobs by test path (#1518): one sibling job per shard per mode, each with its own
# check-run name, never a matrix leg (ADR-0163). The partition is tests/ci_shards.py, and the guard below proves that every
# test file of the tree is in exactly one shard.
FULL_SHARDS = 6
SHARD_JOBS = tuple(f"full-xdist-{k}" for k in range(1, FULL_SHARDS + 1)) + tuple(
    f"full-serial-{k}" for k in range(1, FULL_SHARDS + 1))
COMPLETE_TIER_JOBS = SHARD_JOBS + ("fast-battery-311", "gui-e2e")
# The fast tier runs on every event of the dev-to-stg pull request but the label, a push included. The complete tier runs on
# the stg-to-main pull request alone, on the `promote` label, and the guard job starts it once per tip (ADR-0173).
GUARD_JOB = "complete-guard"
TIER_JOBS = FAST_TIER_JOBS + (GUARD_JOB,) + COMPLETE_TIER_JOBS
# A job runs only for a pull request whose head is a branch of this repository: in a public repository a fork can name
# its branch dev or stg, and its request must start no job.
SAME_REPO = "github.event.pull_request.head.repo.full_name == github.repository"
DEV_TO_STG = f"github.head_ref == 'dev' && {SAME_REPO}"
STG_TO_MAIN = f"github.head_ref == 'stg' && {SAME_REPO}"
FAST_TIER_IF = f"{DEV_TO_STG} && github.event.action != 'labeled'"
GUARD_IF = f"{STG_TO_MAIN} && github.event.action == 'labeled' && github.event.label.name == 'promote'"
COMPLETE_TIER_IF = f"{GUARD_IF} && needs.{GUARD_JOB}.outputs.run == 'true'"
PULL_REQUEST_TYPES = ["opened", "synchronize", "reopened", "labeled"]
PULL_REQUEST_BRANCHES = ["stg", "main"]
# The sentence the workflow states for its two pull requests.
TWO_PULL_REQUESTS_RULE = "The fast tier runs on the dev-to-stg pull request, and the complete tier on the stg-to-main pull request"
# The sentence the workflow states for the branch that supplies its file to the stg-to-main pull request.
TRANSITION_RULE = (
    "the head branch supplies it: stg carries the complete tier before the first stg-to-main request")
PROMOTE_PASS = ROOT / "contrib" / "squad-goal" / "scripts" / "promote-pass.sh"


def _ci_workflow() -> dict:
    import yaml

    return yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())


def _job_python(job: dict) -> str:
    return next(s["with"]["python-version"] for s in job["steps"] if s.get("uses", "").startswith("actions/setup-python"))


def _ci_run_lines() -> list[str]:
    return [step["run"] for job in _ci_workflow()["jobs"].values() for step in job["steps"] if "run" in step]


def _shard_arg(shard: int) -> str:
    """The one way a shard job names its files: the partition module's answer for that shard, never a hand-kept list."""
    return f"$(python3 tests/ci_shards.py {shard})"


def _shard_files(shard: int) -> list[str]:
    """The test files of one shard, read through the command ci.yml runs, so the guard and the job read one seam."""
    r = subprocess.run([sys.executable, "tests/ci_shards.py", str(shard)], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout.split()


def test_each_fast_tier_job_runs_its_own_command():
    """Guard for the CI rule (#1322): each fast-tier job runs its own command and no other job's. The three jobs are
    independent, so they start together; their order in the file carries no behavior, and a reordered `jobs:` mapping
    fails nothing."""
    jobs = _ci_workflow()["jobs"]
    runs = {name: [s["run"] for s in job["steps"] if "run" in s] for name, job in jobs.items()}
    assert "ruff check squad tests" in runs["ruff"], f"the ruff job does not lint the repository's scope: {runs['ruff']}"
    assert "mypy squad" in runs["mypy"], f"the mypy job does not check squad: {runs['mypy']}"
    assert 'python3 -m pytest -n auto -m "not heavy"' in runs["fast-battery"], \
        f"the fast-battery job does not run the fast battery: {runs['fast-battery']}"
    for name, own in (("ruff", "ruff check squad tests"), ("mypy", "mypy squad")):
        others = [r for n, rs in runs.items() if n != name for r in rs]
        assert own not in others, f"{name}'s command also runs in another job: {own}"


def test_fast_tier_is_three_parallel_jobs_on_python_313_and_nothing_else():
    """Guard for the fast tier (#1322): the fast tier is exactly the ruff, mypy and fast-battery jobs, and every one starts
    on the dev-to-stg pull request. Each runs on Python 3.13 alone, with no `needs:` and no other job-level setting that
    serializes or softens it, so the three start together. Whether three runners actually start at once is measured by the
    first real dev-to-stg run; this guard refuses the settings that would hold them in a queue."""
    wf = _ci_workflow()
    assert set(FAST_TIER_JOBS) <= set(wf["jobs"]), f"the fast tier is exactly {FAST_TIER_JOBS}; found {list(wf['jobs'])}"
    assert tuple(wf["jobs"]) == TIER_JOBS, f"the workflow is the two tiers, {TIER_JOBS}; found {list(wf['jobs'])}"
    for name in FAST_TIER_JOBS:
        job = wf["jobs"][name]
        assert job.get("if") == FAST_TIER_IF, f"{name} starts on every event of the dev-to-stg pull request; found {job.get('if')!r}"
        for key in ("needs", "strategy", "environment", "concurrency"):
            assert key not in job, f"{name} carries `{key}:`, which can hold it behind another job or a gate"
        assert job.get("continue-on-error") in (None, False), f"{name} would report green on a red"
        assert _job_python(job) == "3.13", f"{name} runs on Python 3.13 alone"
    assert "ruff check squad tests" in [s.get("run") for s in wf["jobs"]["ruff"]["steps"]]
    assert 'python3 -m pytest -n auto -m "not heavy"' in [s.get("run") for s in wf["jobs"]["fast-battery"]["steps"]]


def test_every_complete_tier_job_runs_on_the_pull_request_whose_head_is_stg():
    """Guard for ADR-0173: every complete-tier job starts on the stg-to-main pull request, and no job is gated on a base
    branch.

    The dev-to-stg promotion reads the fast tier alone, so a promotion to stg never pays for the full suite. The complete
    tier proves the work stg gathered, on the pull request the loop opens from stg to main, and main moves only when it is
    green. A tier is told from the other by the head branch of its pull request: promote-pass.sh refuses a workflow whose
    job names `base_ref`, because such a job never runs on the pull request the dev tip is read on.
    """
    wf = _ci_workflow()
    gated_on_base = tuple(name for name, job in wf["jobs"].items() if "base_ref" in str(job.get("if", "")))
    assert gated_on_base == (), (
        f"a job gated on a base branch never runs on the dev-to-stg pull request: {gated_on_base}")
    for name in COMPLETE_TIER_JOBS:
        job = wf["jobs"][name]
        assert job.get("if") == COMPLETE_TIER_IF, (
            f"{name} starts on the stg-to-main pull request whose `promote` label asks for it; found {job.get('if')!r}")
        assert job.get("needs") == [GUARD_JOB], f"{name} waits on the guard that starts the tier once per tip"
        for key in ("strategy", "environment", "concurrency"):
            assert key not in job, f"{name} carries `{key}:`, which can hold it behind another job or a gate"
        assert job.get("continue-on-error") in (None, False), f"{name} would report green on a red"
    on = wf.get("on", wf.get(True))
    assert on["pull_request"]["branches"] == PULL_REQUEST_BRANCHES, (
        "the workflow runs on the two promotion pull requests, dev to stg and stg to main")
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    stated = " ".join(
        line.lstrip("#").strip() for line in text.split("concurrency:", 1)[0].splitlines() if line.startswith("#"))
    assert TWO_PULL_REQUESTS_RULE in stated, f"the workflow states which pull request carries each tier: {TWO_PULL_REQUESTS_RULE}"


def test_complete_tier_runs_both_full_suite_modes_the_311_battery_and_the_gui_e2e():
    """Guard for the complete tier (#1324, #1518): the full suite in both modes on Python 3.13, the fast battery on 3.11
    and the GUI e2e suite. The full suite runs in one shard job per shard per mode, so a job over fifteen minutes is split
    by test path into a sibling job, never by a matrix (ADR-0163). Each job sets SQUAD_CI_RUNNER=1, so the fixture-bound
    tests are deselected by id and the ids reach the job log (ADR-0139, #1323)."""
    wf = _ci_workflow()
    runs = {name: [s.get("run") for s in wf["jobs"][name]["steps"] if "run" in s] for name in COMPLETE_TIER_JOBS}
    for k in range(1, FULL_SHARDS + 1):
        xdist = [r for r in runs[f"full-xdist-{k}"] if "pytest" in r]
        serial = [r for r in runs[f"full-serial-{k}"] if "pytest" in r]
        assert xdist == [f"python3 -m pytest -n auto {_shard_arg(k)}"], xdist
        assert serial == [f"python3 -m pytest -p no:xdist {_shard_arg(k)}"], serial
        assert _job_python(wf["jobs"][f"full-xdist-{k}"]) == "3.13"
        assert _job_python(wf["jobs"][f"full-serial-{k}"]) == "3.13"
    assert [r for r in runs["fast-battery-311"] if "pytest" in r] == ['python3 -m pytest -n auto -m "not heavy"'], \
        runs["fast-battery-311"]
    assert [r for r in runs["gui-e2e"] if "pytest" in r] == ["python3 -m pytest tests/test_gui_test_e2e.py -q"], \
        runs["gui-e2e"]
    assert _job_python(wf["jobs"]["fast-battery-311"]) == "3.11"
    for name in COMPLETE_TIER_JOBS:
        job = wf["jobs"][name]
        assert (job.get("env") or {}).get("SQUAD_CI_RUNNER") == "1", (
            f"{name} does not set SQUAD_CI_RUNNER=1, so the deselected ids never reach its log (#1323)")
        assert "matrix" not in (job.get("strategy") or {}), f"{name} splits by matrix, never by test path"
        for r in runs[name]:
            assert "--ignore" not in r and "--deselect" not in r, f"{name} excludes a path instead of collecting it: {r}"
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "split by test path" in text, "the workflow names how a job over fifteen minutes is split"


def test_every_full_suite_job_installs_the_sdk_extra_the_hosted_terminal_case_needs():
    """Guard for #1538: the hosted terminal case (`tests/test_hub_terminal_e2e.py`) starts a real hosted session through
    `squad.sdk_adapter host`, and the supervisor refuses that start when its interpreter cannot import
    `claude_agent_sdk`. The full suite's shard jobs install `.[dev,sdk]`, so the case runs on the runner instead of
    failing as a dependency the install left out. The runner can carry this fixture, so nothing is deselected there
    (ADR-0139), and a missing one stays a failure, never a skip (ADR-0018 item 6)."""
    wf = _ci_workflow()
    for name in SHARD_JOBS:
        installs = [s["run"] for s in wf["jobs"][name]["steps"] if "pip install" in s.get("run", "")]
        assert installs == ["python3 -m pip install -e '.[dev,sdk]'"], (
            f"{name} installs the sdk extra the hosted terminal case imports; found {installs}")


def test_claude_md_ci_rule_names_the_fast_tier_jobs_its_guard_and_no_other_job():
    """Guard for the CI rule in CLAUDE.md (#1322, #1324): the rule names the two tiers, its guard, and exactly the jobs of
    ci.yml. A job the rule names but the workflow lacks, or the reverse, fails here (ADR-0018). The no-push rule beside
    it carries its own guard too, and the workflow starts no run on a push, so both halves of the rule are checked and
    an edit that drops either one fails."""
    claude = (ROOT / "CLAUDE.md").read_text().splitlines()
    rule = next(line for line in claude if line.startswith("- **CI "))
    assert "fast tier" in rule and "complete tier" in rule and "tests/test_tooling.py" in rule, rule
    assert "`dev` to `stg`" in rule and "`stg` to `main`" in rule, f"the rule names the pull request of each tier: {rule}"
    # `promote` is the label the complete tier starts on, never a job of the workflow (#1644).
    named = {n for n in re.findall(r"`([a-z0-9-]+)`", rule) if n not in ("dev", "stg", "main", "promote")}
    jobs = set(_ci_workflow()["jobs"])
    # A shard job is named by its family (`full-xdist`, `full-serial`): tests/test_setup.py caps a rule line at 300 characters,
    # and the twelve shard job ids do not fit one short rule (#1518). The guard expands each family from the workflow, so a
    # shard job the rule never covers, a gap in its numbering and a family the workflow lacks all fail here.
    families = {n for n in named if f"{n}-1" in jobs}
    assert {"full-xdist", "full-serial"} <= families, f"the rule names the full suite's two shard families; found {sorted(families)}"
    covered = named - families
    assert covered <= jobs, f"the rule names a job the workflow lacks: {sorted(covered - jobs)}"
    for fam in families:
        ks = {int(n[len(fam) + 1:]) for n in jobs if re.fullmatch(re.escape(fam) + r"-\d+", n)}
        assert ks == set(range(1, FULL_SHARDS + 1)), f"the rule names {fam}, and the workflow carries shards {sorted(ks)}"
        covered |= {f"{fam}-{k}" for k in ks}
    assert covered == jobs == set(TIER_JOBS), f"rule names {sorted(named)}; the workflow carries {sorted(jobs)}"
    assert "full-proof" not in rule, "the rule claims no job the workflow lacks"
    push_rule = next((line for line in claude if line.startswith("- **No branch push")), None)
    assert push_rule is not None, "CLAUDE.md carries no rule that a branch push starts no CI run"
    assert "No branch push starts CI" in push_rule and "tests/test_tooling.py" in push_rule, push_rule
    wf = _ci_workflow()
    triggers = wf.get("on", wf.get(True))
    assert "push" not in triggers, f"the workflow starts a run on a push: {triggers}"
    assert "pull_request" in triggers, f"the workflow starts no run on a pull request: {triggers}"


def test_ci_mypy_job_runs_mypy_squad_once_on_python_313():
    """Guard for the mypy rule: the fast tier's mypy job runs `mypy squad` once, on Python 3.13. The tree's own mypy
    call carries `heavy`, so the rule keeps this fast gate in the default run (ADR-0018)."""
    wf = _ci_workflow()
    runs = [s.get("run") for s in wf["jobs"]["mypy"]["steps"] if s.get("run") == "mypy squad"]
    assert runs == ["mypy squad"], f"the mypy job runs `mypy squad` once; found {runs}"
    assert _job_python(wf["jobs"]["mypy"]) == "3.13"


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
    """Guard for the CI rule: the two promotion pull requests start the run, and nothing else.

    The dev-to-stg pull request carries the fast tier, and the stg-to-main pull request carries the complete tier. The jobs
    run with a read-only token. No branch push starts a run: a work branch is proven by the local fast gate, and the landing
    by the promotion proof. A pull request to any other branch starts none, and a pull request whose head is neither dev nor
    stg starts no job, because each job's own condition names its head.
    """
    import yaml

    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    on = wf.get("on", wf.get(True))  # PyYAML reads a bare `on` as True
    assert on["pull_request"]["branches"] == PULL_REQUEST_BRANCHES, "CI runs on the two promotion pull requests, and no other"
    assert set(on) == {"pull_request"}, f"only a pull request starts the run; found {sorted(on)}"
    assert "push" not in on and "pull_request_target" not in on, "a branch push starts no run"
    assert wf["permissions"] == {"contents": "read"}
    for name, job in wf["jobs"].items():
        gate = str(job.get("if", ""))
        want = FAST_TIER_IF if name in FAST_TIER_JOBS else GUARD_IF if name == GUARD_JOB else COMPLETE_TIER_IF
        assert gate.startswith(want), f"{name} refuses every other pull request: {job.get('if')!r}"
        assert job["timeout-minutes"] == 20, "each job keeps the twenty minutes it gives itself"
    uses = [s["uses"] for j in wf["jobs"].values() for s in j["steps"] if "uses" in s]
    assert uses and all("@v" in u and u.rsplit("@v", 1)[1].isdigit() for u in uses)


# The sentence the workflow states for its landing group. A group that names the commit holds one run and nothing
# pending, so no queue cap applies and GitHub cancels no landing.
LANDING_GROUP_RULE = "A newer tip ends the fast tier of an older tip"


def test_one_pull_request_holds_one_fast_run_and_a_promotion_request_is_never_cancelled():
    """Guard for the CI rule: one commit starts its tier's jobs once each, none of them has a matrix, and one pull
    request holds one fast run, of its newest tip (#149, #398, #1518, owner): a new push cancels the fast run in
    progress, so a burst of landings never leaves two runs of one pull request side by side.

    The complete tier is the promotion request. Its group names the commit and it cancels nothing, so a push never
    cancels a requested proof. The guard checks the group, the cancel semantics, and the workflow's own sentence for
    the rule.
    """
    import yaml

    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    wf = yaml.safe_load(text)
    assert tuple(wf["jobs"]) == TIER_JOBS, "one commit starts its tier's jobs and no other (#1322, #1324, #1518)"
    for job in wf["jobs"].values():
        assert "matrix" not in (job.get("strategy") or {}), "a matrix splits a job into legs that wait for runners"
    con = wf["concurrency"]
    group = con["group"]
    assert group == "ci-${{ github.head_ref }}-${{ github.event.action == 'labeled' && github.sha || 'tip' }}", (
        f"{group}: the fast tier shares one group per pull request branch, and a labeled event names its commit")
    assert not re.search(r"github\.ref\b", group), f"{group}: the group names the branch, not the full ref"
    assert con["cancel-in-progress"] == "${{ github.event.action != 'labeled' }}", (
        "a new push cancels the fast run in progress, and a promotion request is never cancelled; found "
        f"{con['cancel-in-progress']!r}")
    assert "queue" not in con, f"no queue: the newest tip runs at once; found queue={con.get('queue')!r}"
    stated = " ".join(
        line.lstrip("#").strip() for line in text.split("concurrency:", 1)[0].splitlines() if line.startswith("#")
    )
    assert LANDING_GROUP_RULE in stated, f"the workflow states the rule its group carries: {LANDING_GROUP_RULE}"


def test_every_test_file_is_in_exactly_one_full_suite_shard():
    """Guard for #1518: the full suite is split into shards by test path, and the partition is exact.

    A test file the partition leaves out never runs in the complete tier, while the promotion reads green shard checks as
    the full proof; a file in two shards runs twice, and it carries a cost the other shard was balanced against. A new test
    file is assigned here before a green shard set can stand for it.
    """
    shards = [_shard_files(k) for k in range(1, FULL_SHARDS + 1)]
    listed = [p for shard in shards for p in shard]
    counted = {p: listed.count(p) for p in set(listed)}
    twice = sorted(p for p, n in counted.items() if n > 1)
    assert twice == [], f"a test file is in more than one shard, so the full proof runs it twice: {twice}"
    on_disk = {str(p.relative_to(ROOT)) for p in (ROOT / "tests").glob("test_*.py")}
    missing = sorted(on_disk - set(listed))
    assert missing == [], f"a test file is in no shard, so the full proof never runs it: {missing}"
    strays = sorted(set(listed) - on_disk)
    assert strays == [], f"a shard runs a path that is not a test file of this tree: {strays}"


def test_the_full_suite_runs_in_shard_jobs_of_both_modes():
    """Guard for #1518: the full suite is one sibling job per shard in each mode, `-n auto` and `-p no:xdist`, and every
    job names its files through the one partition module, so the two modes prove the same files.

    A shard job is a sibling by test path with its own check-run name, never a matrix leg (ADR-0163), and it carries no
    `--ignore` or `--deselect`: a path it excludes is a test the full proof would never run (ADR-0139). Each job starts on
    the stg-to-main pull request, sets SQUAD_CI_RUNNER=1, and gives itself no more than the twenty minutes of every job.
    """
    wf = _ci_workflow()
    assert tuple(n for n in wf["jobs"] if n in SHARD_JOBS) == SHARD_JOBS, \
        f"the full suite's shard jobs are {SHARD_JOBS}; found {list(wf['jobs'])}"
    for name in SHARD_JOBS:
        job = wf["jobs"][name]
        assert job.get("if") == COMPLETE_TIER_IF, f"{name} starts on the promote request of the stg-to-main pull request; found {job.get('if')!r}"
        assert "matrix" not in (job.get("strategy") or {}), f"{name} splits by matrix, never by test path"
        assert job.get("needs") == [GUARD_JOB], f"{name} waits on the once-per-tip guard, never behind a sibling"
        assert (job.get("env") or {}).get("SQUAD_CI_RUNNER") == "1", f"{name} does not set SQUAD_CI_RUNNER=1 (#1323)"
        assert job["timeout-minutes"] <= 20, f"{name} gives itself {job['timeout-minutes']} minutes, over the twenty"
        gates = [s.get("run") for s in job["steps"] if str(s.get("run", "")).startswith("python3 -m pytest")]
        assert len(gates) == 1, f"{name} runs one pytest call; found {gates}"
        assert "--ignore" not in gates[0] and "--deselect" not in gates[0], f"{name} excludes a path: {gates[0]}"
    for k in range(1, FULL_SHARDS + 1):
        assert _shard_files(k), f"shard {k} names no test file, so its job runs no proof"


def test_the_check_job_of_the_commit_is_part_of_the_ci_proof():
    """Guard for ADR-0164 (#1051, #1322, #1461): the promotion reads the check-run names of every job in ci.yml, so the
    fast tier and the complete tier are both part of the full pass, and no job name is a literal in the script.

    The names come from the job block alone (the script's `--ci-job-names` seam reads `ci.yml` structurally), and the
    answer is exactly the workflow's job names: a step name or a `with:` key at the same indentation never becomes a
    check run whose absence would hold every tip forever."""
    promote = (ROOT / "contrib" / "squad-goal" / "scripts" / "promote-pass.sh").read_text()
    assert "--ci-job-names" in promote and "ci_job_names" in promote, \
        "promote-pass.sh reads the job names through its own structural reader"
    assert '"check"' not in promote, "the check job is no longer a literal name in promote-pass.sh"
    wf = _ci_workflow()
    r = subprocess.run(["bash", str(ROOT / "contrib" / "squad-goal" / "scripts" / "promote-pass.sh"),
                        "--ci-job-names", str(ROOT / ".github" / "workflows" / "ci.yml")],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    job_names = [job.get("name", name) for name, job in wf["jobs"].items()]
    assert r.stdout.split() == job_names, \
        f"the check names are every job of jobs:, not a step, a with: key or a subset: {r.stdout!r}"
    assert set(SHARD_JOBS) <= set(job_names), (
        f"the promotion waits on every shard job of both modes; it reads {[n for n in job_names if n not in SHARD_JOBS]}")
    assert sorted(job_names) == sorted(TIER_JOBS), f"the jobs the promotion waits on: {job_names}"
    # #1518: one reader, the job names of ci.yml. The withdrawn matrix left a second one that read `shard: [0, 1, 2, 3]`
    # from the same file; a matrix returns with no job of its own, so that reader could only name checks no runner starts.
    assert "$shard_list" not in promote, "promote-pass.sh reads the shard checks from a matrix the workflow forbids"
    stg = (ROOT / "contrib" / "squad-goal" / "scripts" / "promote-stg.sh").read_text()
    assert "sg_ci_class" in stg, "promote-stg.sh reads every check run of the tip, so a red shard job refuses the merge"
    # #1723: the dev-to-stg promotion reads the fast tier alone and asks for no complete tier: promote-stg.sh neither reads the
    # complete tier's jobs nor sends the promote label, and the pass waits on the jobs that do not ask for the label.
    assert "--ci-complete-job-names" not in stg, "promote-stg.sh holds the dev-to-stg promotion on the complete tier"
    assert "labels[]=promote" not in stg, "promote-stg.sh asks for the complete tier on the dev-to-stg pull request"
    r = subprocess.run(["bash", str(ROOT / "contrib" / "squad-goal" / "scripts" / "promote-pass.sh"),
                        "--ci-fast-job-names", str(ROOT / ".github" / "workflows" / "ci.yml")],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert sorted(r.stdout.split()) == sorted(wf["jobs"][name].get("name", name) for name in FAST_TIER_JOBS), \
        f"the dev-to-stg promotion waits on the fast tier alone: {r.stdout!r}"


def test_the_complete_tier_starts_on_the_promote_label_alone():
    """Guard for ADR-0173: a push to the dev-to-stg pull request starts the fast tier alone, and the complete tier starts
    when the stg-to-main pull request carries the `promote` label, once per tip. The label on the dev-to-stg pull request
    starts nothing.

    The label event is the promotion request. A second label event on the same tip starts nothing: the guard job reads the
    tip's own check runs and answers once, so the twelve shard jobs, the 3.11 battery and the GUI e2e suite run once on a
    tip and never again. A run a job skipped is no evidence the tier ran: the guard reads `filter=all` and ignores a skipped
    conclusion, and the proof reads green runs the same way.
    """
    wf = _ci_workflow()
    on = wf.get("on", wf.get(True))
    assert on["pull_request"]["types"] == PULL_REQUEST_TYPES, \
        f"the label event starts the complete tier; found {on['pull_request'].get('types')!r}"
    for name in FAST_TIER_JOBS:
        assert wf["jobs"][name].get("if") == FAST_TIER_IF, f"{name} starts on a push: {wf['jobs'][name].get('if')!r}"
    guard = wf["jobs"][GUARD_JOB]
    assert guard.get("if") == GUARD_IF, f"the guard starts on a promote label alone; found {guard.get('if')!r}"
    assert "needs" not in guard, "the guard waits on no job: it is what the tier waits on"
    assert guard.get("permissions") == {"contents": "read", "checks": "read", "actions": "read"}, \
        f"the guard checks out the tree, reads the tip's check runs and the pull request's runs in flight (#2067); found {guard.get('permissions')!r}"
    assert guard.get("outputs") == {"run": "${{ steps.once.outputs.run }}"}, \
        f"the guard answers one output the tier reads: {guard.get('outputs')!r}"
    text = "\n".join(str(s.get("run", "")) for s in guard["steps"])
    assert "filter=all" in text, "the guard reads every check run of the tip, not the latest of each name"
    assert "skipped" in text, "the guard reads a skipped run as no run: another event's skip never starts the tier"
    for name in COMPLETE_TIER_JOBS:
        job = wf["jobs"][name]
        assert job.get("if") == COMPLETE_TIER_IF, f"{name} starts on the promote request alone; found {job.get('if')!r}"
        assert job.get("needs") == [GUARD_JOB], f"{name} reads the guard's answer, so a second label starts it not"


def test_the_head_branch_supplies_the_workflow_file_the_label_starts():
    """Guard for ADR-0173: which branch supplies the workflow file for the first stg-to-main pull request. A
    `pull_request` run executes the workflow of the pull request's merge commit, so the head branch supplies it: `stg`
    carries the complete tier before the first request, the label starts the tier from `stg`'s copy, and `main` takes
    the same file in the fast-forward. The case beside the statement: the stg-to-main pull request is a `pull_request`
    event on the `main` base, a push starts nothing, and the promote label starts the complete tier."""
    wf = _ci_workflow()
    on = wf.get("on", wf.get(True))
    triggers = set(on)
    assert triggers == {"pull_request"}, f"the tier starts on a pull request event alone; found {sorted(triggers)}"
    assert on["pull_request"]["types"] == PULL_REQUEST_TYPES, (
        f"the label event is one of the pull request events that start a run; found {on['pull_request'].get('types')!r}")
    assert "main" in on["pull_request"]["branches"], (
        "the base branch of the stg-to-main pull request is main, and the trigger reads the base branch")
    assert wf["jobs"][GUARD_JOB].get("if") == GUARD_IF, (
        f"the complete tier starts on the promote label of the stg-to-main pull request; found "
        f"{wf['jobs'][GUARD_JOB].get('if')!r}")
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    stated = " ".join(
        line.lstrip("#").strip() for line in text.split("concurrency:", 1)[0].splitlines() if line.startswith("#"))
    assert TRANSITION_RULE in stated, f"the workflow states which branch supplies its file: {TRANSITION_RULE}"


def _guard_answer(tmp_path, conclusion: str | None, status: str = "completed") -> str:
    """The guard step's own script, run on one check run of the first complete-tier job: the `run=` line it answers."""
    wf = _ci_workflow()
    script = next(s["run"] for s in wf["jobs"][GUARD_JOB]["steps"] if s.get("id") == "once")
    run = {"id": 1, "name": COMPLETE_TIER_JOBS[0], "status": status, "conclusion": conclusion}
    box = tmp_path / f"{status}-{conclusion}"
    (box / "bin").mkdir(parents=True)
    gh = box / "bin" / "gh"
    gh.write_text("#!/bin/bash\ncat <<'JSON'\n" + json.dumps({"check_runs": [run]}) + "\nJSON\n")
    gh.chmod(0o755)
    out = box / "out"
    env = {**os.environ, "PATH": f"{box / 'bin'}{os.pathsep}{os.environ['PATH']}", "SHA": "abc", "REPO": "o/r",
           "GITHUB_OUTPUT": str(out)}
    r = subprocess.run(["bash", "-c", script], cwd=ROOT, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return out.read_text().strip()


@pytest.mark.heavy
def test_the_guard_and_the_pass_read_the_same_unproven_set(tmp_path):
    """Guard for ADR-0173: a complete-tier run that ended `cancelled` or `timed_out` measured nothing, so the guard starts
    the tier again on that tip and the stg-to-main pass calls the tip unproven. Both read the one set, the answer of
    `promote-pass.sh --ci-unproven-conclusions`, so a conclusion the pass calls unproven is never one the guard reads as
    "the tier already ran": an unproven tip can always be proven again.
    """
    r = subprocess.run(["bash", str(PROMOTE_PASS), "--ci-unproven-conclusions"], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    unproven = r.stdout.split()
    assert unproven == ["cancelled", "timed_out"], f"the unproven set: {unproven}"
    guard = "\n".join(str(s.get("run", "")) for s in _ci_workflow()["jobs"][GUARD_JOB]["steps"])
    main_pass = (ROOT / "contrib" / "squad-goal" / "scripts" / "promote-main-pass.sh").read_text()
    for name, text in (("the guard", guard), ("promote-main-pass.sh", main_pass)):
        assert "--ci-unproven-conclusions" in text, f"{name} does not read the one unproven set"
        code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
        for c in unproven:
            assert c not in code, f"{name} keeps its own copy of the unproven set: {c}"
    for c in unproven:
        assert _guard_answer(tmp_path, c) == "run=true", f"a {c} run stops the guard, so the tip is never proven again"
    assert _guard_answer(tmp_path, "success") == "run=false", "a tip that carries a finished run starts the tier again"
    assert _guard_answer(tmp_path, None, "in_progress") == "run=false", "a tip whose tier is in flight starts it again"


def _guard_with_other_run(tmp_path, job_status: str, job_conclusion: str | None) -> tuple[str, str]:
    """The guard step's script on a tip with no run of its own, while another run of the pull request (id 22, sha def)
    carries a complete-tier job in the given state: the `run=` line and the step's log."""
    wf = _ci_workflow()
    script = next(s["run"] for s in wf["jobs"][GUARD_JOB]["steps"] if s.get("id") == "once")
    box = tmp_path / f"other-{job_status}-{job_conclusion}"
    (box / "bin").mkdir(parents=True)
    runs = {"workflow_runs": [{"id": 22, "head_sha": "def0000000", "status": job_status},
                              {"id": 7, "head_sha": "abc", "status": "in_progress"}]}
    jobs = {"jobs": [{"name": COMPLETE_TIER_JOBS[0], "status": job_status, "conclusion": job_conclusion}]}
    gh = box / "bin" / "gh"
    gh.write_text("#!/bin/bash\ncase \"$*\" in\n"
                  f"  *actions/runs/22/jobs*) cat <<'JSON'\n{json.dumps(jobs)}\nJSON\n;;\n"
                  f"  *actions/runs*) cat <<'JSON'\n{json.dumps(runs)}\nJSON\n;;\n"
                  "  *) echo '{\"check_runs\": []}';;\nesac\n")
    gh.chmod(0o755)
    out = box / "out"
    env = {**os.environ, "PATH": f"{box / 'bin'}{os.pathsep}{os.environ['PATH']}", "SHA": "abc", "REPO": "o/r",
           "GITHUB_OUTPUT": str(out), "GITHUB_RUN_ID": "7", "HEAD_REF": "stg"}
    r = subprocess.run(["bash", "-c", script], cwd=ROOT, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return out.read_text().strip(), r.stdout


@pytest.mark.heavy
def test_a_label_starts_no_complete_tier_while_another_runs(tmp_path):
    """Guard for #2067: one complete tier at a time. A label on a new stg tip starts nothing while a complete-tier job
    of another run of the pull request is in flight, and the log names that run and its sha; the run in flight is never
    cancelled. A run that ended, or ended cancelled or timed out, holds nothing."""
    answer, log = _guard_with_other_run(tmp_path, "in_progress", None)
    assert answer == "run=false", "a label started a second complete tier while one was in flight"
    assert "22" in log and "def0000" in log, f"the guard does not name the run in flight: {log!r}"
    for status, conclusion in (("completed", "success"), ("completed", "cancelled"), ("completed", "timed_out")):
        assert _guard_with_other_run(tmp_path, status, conclusion)[0] == "run=true", f"a {conclusion} run held the tier"
    wf = _ci_workflow()
    assert "cancel" not in json.dumps(wf["jobs"][GUARD_JOB]), "the guard cancels a run"


def test_the_quiet_window_is_one_value_in_the_adr_and_the_pass():
    """Guard for ADR-0173 (#1890): the ADR's Settings line and promote-main-pass.sh name one quiet-window value.

    The value is measured, not guessed: 300 s and 600 s start nearly the same number of complete tiers, and 300 s waits
    half as long; 3600 s starts far fewer but holds `main` for up to an hour on a tip that never quiets. The ADR is the
    decision and the pass is the code, so a value that drifts in one place fails here.
    """
    adr = next((ROOT / "docs" / "adr").glob("0173-*.md"))
    named = re.search(r"`SG_PROMOTE_MAIN_QUIET_S` \((\d+)\)", adr.read_text())
    assert named, "the ADR-0173 Settings line does not name SG_PROMOTE_MAIN_QUIET_S"
    script = (ROOT / "contrib" / "squad-goal" / "scripts" / "promote-main-pass.sh").read_text()
    default = re.search(r'SG_PROMOTE_MAIN_QUIET_S:-}\"\s+(\d+)', script)
    assert default, "promote-main-pass.sh does not default SG_PROMOTE_MAIN_QUIET_S"
    assert named.group(1) == default.group(1), (
        f"ADR-0173 names {named.group(1)} s and the pass defaults {default.group(1)} s: they are one measured value")
    assert named.group(1) == "300", "the measured quiet window is 300 s (#1890)"


E2E = ROOT / "tests" / "test_gui_test_e2e.py"
ADR_0139 = ROOT / "docs" / "adr" / "0139-a-test-without-its-fixture-repo-is-deselected.md"
HOST_NAMES = ("MESENAI_REPO", "MESENAI_ROOT")


def _host_bound_names() -> set[str]:
    """The tests that carry `@pytest.mark.host_bound` in the e2e file: the marker is the rule ADR-0139 states."""
    return set(re.findall(r"@pytest\.mark\.host_bound(?:\([^\n]*\))?\s*\ndef (\w+)\(", E2E.read_text()))


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
    collected = set(re.findall(r"::(\w+)", "\n".join(ln for ln in out.splitlines() if not ln.startswith("deselected:"))))
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


def test_ci_fast_tier_runs_the_gui_e2e_suite_from_one_step_and_names_no_mesenai_path():
    """Guard for ADR-0139 and #1322: the fast tier runs the GUI e2e suite from exactly one step, and names no
    MESENAI_REPO path the runner never checks out. The step is the rule, not a convenience: the battery above carries
    `-m "not heavy"`, and the file's `heavy` tests ran on every promotion before the fast tier, so a tier that leaves the
    file to the tree walk drops them. Neither pytest command carries `--ignore` or `--deselect`, so the host-bound tests
    are deselected by name when the host is absent (ADR-0139) and the host-free ones run."""
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert text.count("test_gui_test_e2e.py") == 2, \
        "the fast tier runs the GUI e2e suite from one step and the complete tier from its own job"
    assert "MESENAI_REPO" not in text
    for gate in (r for r in _ci_run_lines() if r.startswith("python3 -m pytest")):
        assert "--ignore" not in gate and "--deselect" not in gate, \
            f"the fast battery excludes a path instead of collecting the tree: {gate}"
    assert 'testpaths = ["tests"]' in (ROOT / "pyproject.toml").read_text(), \
        "the battery's tree holds the e2e file only while tests/ is its testpath"


def test_ci_gate_steps_run_the_fast_battery_once_on_python_313():
    """Guard for the suite rule: the fast-battery job runs the fast battery once, which leaves the `heavy` tests out, and
    one explicit GUI e2e run beside it, which is where that file's `heavy` tests come back."""
    job = _ci_workflow()["jobs"]["fast-battery"]
    gates = [s.get("run") for s in job["steps"] if str(s.get("run", "")).startswith("python3 -m pytest")]
    assert gates == ['python3 -m pytest -n auto -m "not heavy"', "python3 -m pytest tests/test_gui_test_e2e.py -q"], gates
    assert _job_python(job) == "3.13"


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


def test_no_shell_suite_probes_the_machine_for_a_sleep_it_did_not_start():
    """A suite that hangs a process to prove a deadline finds and ends that process by a name only its own run holds
    (#1735). `pgrep -f "sleep 2718"` reads every suite on the machine: two gates side by side then read each other's hang
    as a survivor, and one suite's `pkill` ends the hang another suite is still measuring."""
    scripts = ROOT / "contrib/squad-goal/scripts"
    wide = re.compile(r"p(?:grep|kill)\b[^\n]*-f\s+[\"']sleep [0-9]")
    def code(path):   # a comment may name the old probe: only a line the shell runs counts
        return "\n".join(ln for ln in path.read_text(errors="replace").splitlines() if not ln.lstrip().startswith("#"))

    found = [p.name for p in sorted(scripts.glob("test-*.sh")) if wide.search(code(p))]
    assert found == [], f"these suites probe the whole machine for a sleep: {found}"


def test_every_job_names_a_fixed_runner_image():
    """A `-latest` label moves to a new image on the vendor's date, so a job would change its system with no commit, and
    GitHub annotates every run with the migration notice. Each job names its image version."""
    wf = _ci_workflow()
    moving = {name: job.get("runs-on") for name, job in wf["jobs"].items() if str(job.get("runs-on")).endswith("-latest")}
    assert not moving, f"a job runs on a label that moves: {moving}"


def test_no_job_runs_for_a_pull_request_from_a_fork():
    """A fork that names its branch dev or stg starts no job: every job reads that the head is this repository."""
    jobs = _ci_workflow()["jobs"]
    missing = [name for name, job in jobs.items() if SAME_REPO not in str(job.get("if", ""))]
    assert missing == [], f"these jobs run for a fork's pull request: {missing}"


# The local half of the promotion's tip gate (spec 1782, #1927): the shell suites of the changed scripts and each test a
# Linux runner deselects. Two facts keep the two halves equivalent to today's gate, and each one is pinned here.
CONFTEST = ROOT / "tests" / "conftest.py"
BASE_BATTERY = ROOT / "contrib" / "squad-goal" / "scripts" / "base-battery.sh"
TIP_GATE = ROOT / "contrib" / "squad-goal" / "scripts" / "tip-gate.sh"


def _deselect_markers() -> set[str]:
    """The markers `tests/conftest.py` registers for a fixture a runner may lack: each one is deselected, never skipped,
    on a runner that does not hold it (ADR-0139). Read from the registration itself, so a new marker joins the set."""
    return set(re.findall(r'^\s+"(\w+?)(?:\(reason\))?:', CONFTEST.read_text(), re.M))


def test_the_local_half_runs_each_test_a_linux_runner_deselects():
    """(e) Every marker a Linux runner deselects is selected by the local half, so a macOS-bound red still refuses. The
    definition is one: `sg_linux_deselected_markers` in base-battery.sh, and the tip gate reads it."""
    markers = _deselect_markers()
    assert len(markers) == 8, f"the deselection markers of conftest.py changed: {sorted(markers)}"
    r = subprocess.run(["bash", "-c", f'. "{BASE_BATTERY}"; sg_linux_deselected_markers'],
                       capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip(), "base-battery.sh names no deselected-test selection"
    selected = set(re.findall(r"\b\w+_bound\b", r.stdout))
    assert markers <= selected, f"the local half leaves these deselected tests to no runner: {sorted(markers - selected)}"
    assert "sg_linux_deselected_markers" in TIP_GATE.read_text(), "the tip gate keeps its own copy of the marker selection"


def _mypy_scope(text: str) -> set[str]:
    """The paths one gate's mypy checks: the arguments of its `mypy` calls, comments left out."""
    scope: set[str] = set()
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        for call in re.findall(r"(?:^|\s)(?:-m )?mypy ([^|&;]+)", line):
            scope.update(call.split())
    return scope


def test_the_local_half_keeps_mypy_only_for_a_wider_scope_than_ci():
    """(f) The CI half's mypy scope and the local gate's are compared here. While they are the same, mypy stays in the CI
    half and never runs twice; a wider local scope fails this test, so mypy enters the local half on purpose, not by drift."""
    ci_scope = _mypy_scope("\n".join(_ci_run_lines()))
    local_scope = _mypy_scope(BASE_BATTERY.read_text())
    assert ci_scope == {"squad"}, f"the CI mypy scope moved: {sorted(ci_scope)}"
    assert local_scope == ci_scope, (
        f"the local mypy scope is {sorted(local_scope)}, not CI's {sorted(ci_scope)}: a wider local scope keeps mypy in "
        "the local half of the tip gate")
    local_half = "\n".join(ln for ln in TIP_GATE.read_text().splitlines() if not ln.lstrip().startswith("#"))
    assert "-m mypy" not in local_half, \
        "the local half runs mypy although the CI half already covers that scope"


def test_nightly_workflow_pins_its_schedule_dev_ref_and_read_only_token():
    """Guard for the nightly's GitHub half (#1975, ADR-0153): a schedule at 04:00 America/Sao_Paulo runs the complete Linux suite on dev.

    The trigger is a schedule only, so no branch push starts it. One job reads the dev tip once, and every shard checks out
    that one sha: dev moves between two checkouts, and twelve shards of twelve tips prove no tip. The sha job uploads an
    artifact whose name carries the sha, so the local half reads the tested sha with no download. Each shard runs the very
    command of its complete-tier sibling in ci.yml, heavy included, with SQUAD_CI_RUNNER=1, and the token is read-only.
    """
    import yaml

    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "nightly.yml").read_text())
    on = wf.get("on", wf.get(True))  # PyYAML reads a bare `on` as True
    assert set(on) == {"schedule"}, f"only the schedule starts the nightly, never a push; found {sorted(on)}"
    assert "push" not in on and "pull_request" not in on
    assert on["schedule"] == [{"cron": "0 7 * * *"}], "04:00 America/Sao_Paulo is 07:00 UTC"
    assert wf["permissions"] == {"contents": "read"}
    shards = {f"full-xdist-{n}" for n in range(1, FULL_SHARDS + 1)} | {f"full-serial-{n}" for n in range(1, FULL_SHARDS + 1)}
    assert set(wf["jobs"]) == shards | {"dev-sha"}
    sha_job = wf["jobs"]["dev-sha"]
    checkout = [s for s in sha_job["steps"] if str(s.get("uses", "")).startswith("actions/checkout@")]
    assert checkout and checkout[0].get("with", {}).get("ref") == "dev", "the sha job checks out dev"
    assert "steps.tip.outputs.sha" in str(sha_job.get("outputs", {}).get("sha")), "the sha job exports the dev sha it read"
    upload = [s for s in sha_job["steps"] if str(s.get("uses", "")).startswith("actions/upload-artifact@")]
    assert upload and upload[0]["with"]["name"] == "nightly-dev-sha-${{ steps.tip.outputs.sha }}", \
        "the tested dev sha is in the artifact name, so a reader needs no download"
    ci = _ci_workflow()["jobs"]
    for name in shards:
        job = wf["jobs"][name]
        assert job.get("needs") == ["dev-sha"], f"{name} waits for the one dev sha"
        checkout = [s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/checkout@")]
        assert checkout and checkout[0].get("with", {}).get("ref") == "${{ needs.dev-sha.outputs.sha }}", \
            f"{name} checks out the dev sha the sha job read"
        assert (job.get("env") or {}).get("SQUAD_CI_RUNNER") == "1", f"{name} deselects what a Linux runner lacks (ADR-0139)"
        runs = [s["run"] for s in job["steps"] if "pytest" in s.get("run", "")]
        assert runs == [s["run"] for s in ci[name]["steps"] if "pytest" in s.get("run", "")], \
            f"{name} runs the command of its complete-tier sibling in ci.yml"
        assert "not heavy" not in " ".join(runs), f"{name} deselects the heavy tests"
