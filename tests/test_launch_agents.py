"""Guard: issue #1617. The daemon and the supervisor run from launchd, installed and rolled back by three named scripts, each
with its own test-*.sh beside it. The shell suites run against a fake launchctl and a fixture repository, so no job is
loaded into the owner's launchd and the owner's working checkout is never the deployment source.
"""
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
VOICE = ROOT / "contrib" / "voice"
AGENTS = ("install-agents", "rollback-agents", "uninstall-agents")
# A named behaviour of install-agents.sh: it waits for launchd to release the old job before the bootstrap (#2954).
SUITES = AGENTS + ("install-agents-bootstrap",)


def test_each_agent_script_is_named_and_has_its_test_beside_it():
    for name in AGENTS:
        assert (VOICE / f"{name}.sh").is_file(), f"{name}.sh is missing"
        assert (VOICE / f"test-{name}.sh").is_file(), f"test-{name}.sh is missing beside {name}.sh"


@pytest.mark.macos_bound   # launchctl and plists: the suites need the macOS userland the loop runs on (ADR-0139)
@pytest.mark.heavy
def test_the_launch_job_suites_pass_against_a_fake_launchctl():
    for name in SUITES:
        done = subprocess.run(["bash", str(VOICE / f"test-{name}.sh")], capture_output=True, text=True, timeout=120,
                              check=False)
        assert done.returncode == 0, f"test-{name}.sh failed: {done.stdout}{done.stderr}"
