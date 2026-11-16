"""The promotion's tip gate runs only the local half on a green fast-tier CI result (spec 1782, #1927).

The proof of the dev tip is a hybrid. The CI half is the fast tier of the dev-to-stg pull request, read by
`contrib/squad-goal/scripts/ghread.py --fast-tier`. The local half is the shell suites of the scripts the tip changes
against stg and each pytest test a Linux runner deselects. On a `pass` the gate runs only the local half; any other
verdict keeps today's gate, and `SG_PROOF_RUNNER=local` keeps it too. The gate history line names the runner and the CI
run id. Every case drives a fixture repository and a stub reader, so no case touches the network.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
TIP_GATE = SCRIPTS / "tip-gate.sh"
PROMOTE_PASS = SCRIPTS / "promote-pass.sh"


def _fields() -> tuple[str, ...]:
    """The gate-history field names from the one grammar's rule layer (hub_profiler), never a second copy here."""
    spec = importlib.util.spec_from_file_location("hub_profiler_under_test", SCRIPTS / "hub_profiler.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.GATE_HISTORY_FIELDS


# The fields of one gate-history line, in the order `gate.sh` writes them.
FIELDS = _fields()

# The markers a Linux runner deselects: each test that carries one is in the local half (tests/conftest.py, ADR-0139).
LINUX_DESELECT_MARKERS = (
    "host_bound", "node_bound", "sandbox_bound", "process_bound",
    "cc_bound", "sdk_bound", "macos_bound", "claude_bound",
)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def fixture_repo(tmp_path: Path) -> tuple[Path, str]:
    """A repository whose tip changes one script, with a shell suite beside it and an unchanged suite beside another.
    Answers the path and the tip sha."""
    repo = tmp_path / "repo"
    (repo / "contrib" / "squad-goal" / "scripts").mkdir(parents=True)
    git(repo, "init", "-q", "-b", "dev")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "test")
    scripts = repo / "contrib" / "squad-goal" / "scripts"
    log = tmp_path / "suite.log"
    other = tmp_path / "other.log"
    (scripts / "foo.sh").write_text("#!/bin/bash\nexit 0\n")
    (scripts / "bar.sh").write_text("#!/bin/bash\nexit 0\n")
    # An unchanged script keeps its suite beside it: the local half must leave that suite alone.
    (scripts / "test-bar.sh").write_text(
        textwrap.dedent(
            f"""\
            #!/bin/bash
            printf 'ran\\n' >> {other}
            exit 0
            """
        )
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    git(repo, "branch", "stg")
    (scripts / "foo.sh").write_text("#!/bin/bash\necho changed\n")
    (scripts / "test-foo.sh").write_text(
        textwrap.dedent(
            f"""\
            #!/bin/bash
            printf 'ran\\n' >> {log}
            exit "${{STUB_SUITE_RC:-0}}"
            """
        )
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "the change")
    return repo, git(repo, "rev-parse", "HEAD")


def stub_python(tmp_path: Path) -> tuple[Path, Path]:
    """A stand-in for the gate interpreter: it records each call's argv and passes."""
    log = tmp_path / "python.log"
    py = tmp_path / "py"
    py.write_text(f"#!/bin/bash\nprintf '%s\\n' \"$*\" >> {log}\nexit 0\n")
    py.chmod(0o755)
    return py, log


def stub_reader(tmp_path: Path, line: str) -> Path:
    """A stand-in for `ghread.py --fast-tier <sha>`: it answers the one line a case names."""
    cmd = tmp_path / "ci-reader"
    quoted = "'" + line.replace("'", "'\\''") + "'"
    cmd.write_text(f"#!/bin/bash\nprintf '%b\\n' {quoted}\nexit 0\n")   # %b: the tab the reader's line carries
    cmd.chmod(0o755)
    return cmd


def run_gate(tmp_path: Path, repo: Path, *args: str, ci: str = "pass", runner: str | None = None,
             suite_rc: str = "0") -> subprocess.CompletedProcess:
    py, _ = stub_python(tmp_path)
    env = {k: v for k, v in os.environ.items() if not k.startswith("SG_")}
    env.update(
        SG_STATE=str(tmp_path / "state"),
        SG_GATE_PY=str(py),
        SG_CI_FAST_TIER_CMD=str(stub_reader(tmp_path, ci)),
        SG_TIP_GATE_BASE="stg",
        STUB_SUITE_RC=suite_rc,
    )
    if runner is not None:
        env["SG_PROOF_RUNNER"] = runner
    return subprocess.run(["bash", str(TIP_GATE), *args], cwd=repo, env=env, capture_output=True, text=True)


def history_row(tmp_path: Path) -> list[str]:
    path = tmp_path / "state" / "gate-history"
    rows = [line for line in path.read_text().splitlines() if line.strip()]
    assert rows, "the gate wrote no history line"
    return rows[-1].split("\t")


def test_a_green_ci_result_runs_only_the_local_half_of_the_tip_gate(tmp_path):
    """(a) On a pass from the reader the gate runs the shell suites of the changed scripts and no battery."""
    repo, tip = fixture_repo(tmp_path)
    _, pylog = stub_python(tmp_path)
    out = run_gate(tmp_path, repo, "--local-half", tip, "4242", ci="pass\t4242")
    assert out.returncode == 0, out.stdout + out.stderr
    assert (tmp_path / "suite.log").read_text().split() == ["ran"], "the changed script's suite did not run"
    assert not (tmp_path / "other.log").exists(), "the gate ran the suite of a script the tip never changed"
    calls = pylog.read_text() if pylog.exists() else ""
    assert "-m ruff" not in calls, f"the CI half ran ruff again: {calls}"
    assert "-m mypy" not in calls, f"the CI half ran mypy again: {calls}"


def test_the_local_half_runs_each_test_a_linux_runner_deselects(tmp_path):
    """(e) The local half selects every marker a Linux runner deselects, so no macOS-bound red reaches the promotion."""
    repo, tip = fixture_repo(tmp_path)
    _, pylog = stub_python(tmp_path)
    out = run_gate(tmp_path, repo, "--local-half", tip, "4242", ci="pass\t4242")
    assert out.returncode == 0, out.stdout + out.stderr
    calls = pylog.read_text()
    pytest_call = next((line for line in calls.splitlines() if "-m pytest" in line), "")
    assert pytest_call, f"the local half ran no pytest for the deselected tests: {calls}"
    for marker in LINUX_DESELECT_MARKERS:
        assert marker in pytest_call, f"the local half leaves {marker} to the Linux runner: {pytest_call}"


def test_a_red_shell_suite_fails_the_gate_under_a_green_ci_result(tmp_path):
    """(c) A measured red in the local half is a fail, whatever the CI half answered."""
    repo, tip = fixture_repo(tmp_path)
    out = run_gate(tmp_path, repo, "--local-half", tip, "4242", ci="pass\t4242", suite_rc="1")
    assert out.returncode == 1, out.stdout + out.stderr
    row = history_row(tmp_path)
    assert row[2] == "fail", f"the gate history does not record the red: {row}"


def test_the_gate_history_records_the_runner_and_the_ci_run_id(tmp_path):
    """(d) A CI-half gate records runner=ci and the run id; the row keeps the fields the loop already reads."""
    repo, tip = fixture_repo(tmp_path)
    out = run_gate(tmp_path, repo, "--local-half", tip, "4242", ci="pass\t4242")
    assert out.returncode == 0, out.stdout + out.stderr
    row = history_row(tmp_path)
    assert len(row) == len(FIELDS), f"the history row keeps the grammar's field count: {row}"
    assert (row[FIELDS.index("runner")], row[FIELDS.index("run_id")]) == ("ci", "4242"), \
        f"the runner and the run id: {row}"
    assert row[FIELDS.index("caller")] == "tip", f"the caller field: {row}"


@pytest.mark.parametrize("verdict", ["fail\t77", "unproven\t-", "garbage", ""])
def test_any_other_verdict_keeps_todays_tip_gate(tmp_path, verdict):
    """(b) Only a pass takes the local half; every other verdict keeps today's gate."""
    repo, tip = fixture_repo(tmp_path)
    out = run_gate(tmp_path, repo, "--choose", tip, ci=verdict)
    assert out.returncode == 0, out.stdout + out.stderr
    assert out.stdout.strip() == "local", f"{verdict!r} did not keep today's gate: {out.stdout!r}"


def test_a_green_ci_result_names_the_runner_and_the_run_id(tmp_path):
    """(a,d) The pass answers the half and the run the history line records."""
    repo, tip = fixture_repo(tmp_path)
    out = run_gate(tmp_path, repo, "--choose", tip, ci="pass\t4242")
    assert out.returncode == 0, out.stdout + out.stderr
    assert out.stdout.strip() == "ci 4242", out.stdout


def test_SG_PROOF_RUNNER_local_restores_todays_tip_gate(tmp_path):
    """(g) The rollback: a profile that names the local runner never reads CI."""
    repo, tip = fixture_repo(tmp_path)
    out = run_gate(tmp_path, repo, "--choose", tip, ci="pass\t4242", runner="local")
    assert out.returncode == 0, out.stdout + out.stderr
    assert out.stdout.strip() == "local", out.stdout


def test_the_promotion_pass_chooses_the_half_before_the_delta_proof():
    """(b,g) promote-pass.sh asks the tip gate which half to run, and it asks before the delta spends the budget."""
    body = PROMOTE_PASS.read_text()
    assert 'tip-gate.sh" --choose' in body, "promote-pass.sh never asks the tip gate which half to run"
    assert body.index('tip-gate.sh" --choose') < body.index('bash "$d/delta-pass.sh"'), \
        "the promotion reads the CI half only after the delta proof already spent the budget"
