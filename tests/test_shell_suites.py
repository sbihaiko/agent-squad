"""One test per shell suite beside the scripts, so the gate runs them (#394). A red suite fails its test with its FAIL lines.

A suite is found by `tests/shell_suites.py`: a new `test-*.sh` needs no edit here. The suites named in
`tests/shell_suites_seconds.txt` as 0.5 s or more carry `heavy`, so the fast battery leaves them out and the full run keeps all.
One over the slow line also carries a `slow` reason, since the 20 s cap guards a unit test, not a script suite.
"""
import pytest

from tests import shell_suites


def _param(suite):
    marks = [pytest.mark.heavy] if shell_suites.is_heavy(suite) else []
    if shell_suites.is_slow(suite):
        marks.append(pytest.mark.slow(reason=f"runs a real script suite that takes over {shell_suites.SLOW_SECONDS:g} s"))
    return pytest.param(suite, id=suite.name, marks=marks)


@pytest.mark.parametrize("suite", [_param(s) for s in shell_suites.discover()])
def test_shell_suite(suite):
    message = shell_suites.problem(suite)
    assert message is None, message
