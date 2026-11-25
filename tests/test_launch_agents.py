"""Guard: issue #1617. The daemon and the supervisor run from launchd, installed and rolled back by three named scripts, each
with its own test-*.sh beside it. The shell suites run against a fake launchctl and a fixture repository, so no job is
loaded into the owner's launchd and the owner's working checkout is never the deployment source.

The paths are spelled out as literals, never built from a name: the fast gate's selector names a script by a path literal,
so only a literal here makes a change to one of them run this guard (#2954).
"""
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = (
    "contrib/voice/install-agents.sh",
    "contrib/voice/rollback-agents.sh",
    "contrib/voice/uninstall-agents.sh",
)
# A named behaviour of install-agents.sh: it waits for launchd to release the old job before the bootstrap (#2954).
SUITES = (
    "contrib/voice/test-install-agents.sh",
    "contrib/voice/test-rollback-agents.sh",
    "contrib/voice/test-uninstall-agents.sh",
    "contrib/voice/test-install-agents-bootstrap.sh",
)


def test_each_agent_script_is_named_and_has_its_test_beside_it():
    for rel in SCRIPTS:
        assert (ROOT / rel).is_file(), f"{rel} is missing"
        name = Path(rel).name
        spelled = f"contrib/voice/test-{name}"
        assert spelled in SUITES, f"no suite beside {rel}"
        assert (ROOT / spelled).is_file(), f"{spelled} is missing beside {rel}"


@pytest.mark.macos_bound   # launchctl and plists: the suites need the macOS userland the loop runs on (ADR-0139)
@pytest.mark.heavy
def test_the_launch_job_suites_pass_against_a_fake_launchctl():
    for rel in SUITES:
        done = subprocess.run(["bash", str(ROOT / rel)], capture_output=True, text=True, timeout=120, check=False)
        assert done.returncode == 0, f"{rel} failed: {done.stdout}{done.stderr}"
