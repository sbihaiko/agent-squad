"""The macOS probe workflow runs by hand, on a GitHub macOS runner, with the read-only token and no secret (#1976)."""
import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/macos-probe.yml"


def _doc():
    return yaml.safe_load(WORKFLOW.read_text())


def _step(step_id):
    return next(s for s in _doc()["jobs"]["probe"]["steps"] if s.get("id") == step_id)


def _run_step(step_id, tmp_path, fake_cc):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    cc = bin_dir / "cc"
    cc.write_text(fake_cc)
    cc.chmod(0o755)
    script = tmp_path / "step.sh"
    script.write_text(_step(step_id)["run"])
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path), "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md")}
    return subprocess.run(["bash", "-e", str(script)], cwd=ROOT, env=env, capture_output=True, text=True)


FAILING_CC = "#!/bin/sh\nexit 1\n"
WORKING_CC = (
    "#!/bin/sh\n"
    "while [ $# -gt 0 ]; do if [ \"$1\" = \"-o\" ]; then out=\"$2\"; fi; shift; done\n"
    "printf '#!/bin/sh\\nexit 0\\n' > \"$out\"\n"
    "chmod +x \"$out\"\n"
)


def _events(doc):
    on = doc.get(True, doc.get("on"))
    return {on} if isinstance(on, str) else set(on)


def test_the_probe_starts_by_hand_only():
    assert _events(_doc()) == {"workflow_dispatch"}


def test_the_probe_holds_the_read_only_token_at_every_level():
    doc = _doc()
    assert doc.get("permissions") == {"contents": "read"}
    for name, job in doc["jobs"].items():
        assert job.get("permissions", {"contents": "read"}) == {"contents": "read"}, name


def test_every_job_runs_on_a_github_macos_runner():
    for name, job in _doc()["jobs"].items():
        assert str(job["runs-on"]).startswith("macos-"), name


def test_the_probe_names_no_secret():
    assert not re.search(r"secrets\s*[.\[]", WORKFLOW.read_text())


def test_a_failed_cc_build_fails_its_step(tmp_path):
    result = _run_step("cc", tmp_path, FAILING_CC)
    assert result.returncode != 0, result.stderr
    assert "exit code: 1" in (tmp_path / "summary.md").read_text()


def test_a_working_cc_build_passes_its_step(tmp_path):
    result = _run_step("cc", tmp_path, WORKING_CC)
    assert result.returncode == 0, result.stderr


def test_the_suites_step_selects_the_macos_bound_marker():
    run = _step("suites")["run"]
    assert "tests/test_shell_suites.py" in run
    assert "-m macos_bound" in run
    assert "test-*.sh" not in run
