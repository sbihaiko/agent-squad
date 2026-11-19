"""One test per shell suite beside the scripts, so the gate runs them (#394). A red suite fails its test with its FAIL lines.

A suite is found by `tests/shell_suites.py`: a new `test-*.sh` needs no edit here. A suite whose own `# seconds:` header
is 0.5 s or more carries `heavy`, so the fast battery leaves it out and the full run keeps all.
One over the slow line also carries a `slow` reason, since neither the 20 s cap nor the call deadline guards a unit test's
work, and a real script suite's own wall time is the machine's.
"""
import pytest

from tests import shell_suites

# The fast lane's own beat reads the process table with ps to prove the lane it pid-files is alive (#513), and the
# fallback suite reads this shell's own command line through ps (#1647).
PROCESS_BOUND_SUITES = {"test-fastloop-pid.sh", "test-sg-ps-fallback.sh"}


def _param(suite):
    marks = [pytest.mark.macos_bound]   # the loop runs on macOS only; a Linux runner deselects the suite (ADR-0139)
    if shell_suites.is_heavy(suite):
        marks.append(pytest.mark.heavy)
    if suite.name in PROCESS_BOUND_SUITES:
        marks.append(pytest.mark.process_bound)
    if shell_suites.is_slow(suite):
        marks.append(pytest.mark.slow(reason=f"runs a real script suite that takes over {shell_suites.SLOW_SECONDS:g} s"))
    return pytest.param(suite, id=suite.name, marks=marks)


@pytest.mark.parametrize("suite", [_param(s) for s in shell_suites.discover()])
def test_shell_suite(suite):
    message = shell_suites.problem(suite)
    assert message is None, message
