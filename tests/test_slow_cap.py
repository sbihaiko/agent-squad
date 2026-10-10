import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run_inner(tmp_path, body):
    (tmp_path / "test_inner.py").write_text(body)
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "tests.slowcap", "-o", "slow_cap_seconds=0.2",
         "-c", "/dev/null", "--rootdir", str(tmp_path), "test_inner.py"],
        cwd=tmp_path, env=env, capture_output=True, text=True,
    )


SLEEP = "import time\n"


def test_a_test_over_the_cap_fails_and_is_named(tmp_path):
    r = run_inner(tmp_path, SLEEP + "def test_slow():\n    time.sleep(0.5)\n")
    assert r.returncode == 1
    assert "test_inner.py::test_slow took" in r.stdout


def test_a_test_under_the_cap_passes(tmp_path):
    r = run_inner(tmp_path, "def test_fast():\n    pass\n")
    assert r.returncode == 0


def test_a_slow_marker_with_a_reason_lets_a_test_pass(tmp_path):
    body = SLEEP + "import pytest\n@pytest.mark.slow(reason='real subprocess')\ndef test_slow():\n    time.sleep(0.5)\n"
    assert run_inner(tmp_path, body).returncode == 0


def test_a_slow_marker_with_no_reason_refuses_the_run(tmp_path):
    body = "import pytest\n@pytest.mark.slow\ndef test_x():\n    pass\n"
    r = run_inner(tmp_path, body)
    assert r.returncode != 0 and "needs a reason" in r.stdout + r.stderr


def test_the_repo_default_cap_is_20_seconds_and_durations_print():
    text = (ROOT / "pyproject.toml").read_text()
    assert "--durations=15" in text and 'slow_cap_seconds = "20"' in text
