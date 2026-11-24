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
# A suite whose quiet-machine measure reads under the slow line, yet a loaded runner takes past the cap: its measure
# cannot see the load the cap excuses, so the case states its own reason.
LOADED_PAST_CAP = {"test-dispatch-mutex.sh": "runs a real script suite that a loaded runner takes past the cap"}


def _has_slow_reason(case):
    marks = case.marks if hasattr(case, "marks") else getattr(case, "pytestmark", ())
    return any(mark.name == "slow" and mark.kwargs.get("reason") for mark in marks)


def test_a_call_that_runs_a_whole_shell_suite_carries_a_slow_reason():
    """A call that runs a whole shell suite is judged by the 20 s cap, so it carries a stated reason, whether it runs the
    suite by hand or through the runner."""
    from tests.test_dispatch_mutex import test_the_two_dispatchers_share_one_claim_and_read_live_requests as dispatch
    from tests.test_live_check_orphans import test_the_live_check_suite_leaves_no_holder_behind as live_check
    from tests.test_retired_profile import test_the_address_suite_runs_without_the_loops_exported_defaults as address
    from tests.test_retired_profile import test_address_seam_accepts_a_retired_profile as address_seam
    from tests.test_retired_profile import test_retired_profile_migration_cycle as migration

    whole_suite_calls = [dispatch, live_check, address, address_seam, migration]
    missing = [test.__name__ for test in whole_suite_calls if not _has_slow_reason(test)]
    assert missing == [], f"these calls run a whole shell suite and carry no slow reason: {missing}"


def test_the_runner_case_of_a_suite_that_loads_past_the_cap_carries_a_slow_reason():
    """test-dispatch-mutex.sh measures 4.06 s on a quiet machine and 22.6 s under -n auto, past the 4x the cap excuses,
    so its runner case carries a reason even though its measure reads under the slow line."""
    case = _param(shell_suites.SUITES_DIR / "test-dispatch-mutex.sh")
    assert _has_slow_reason(case), "the runner case for test-dispatch-mutex.sh carries no slow reason"


def _param(suite):
    marks = [pytest.mark.macos_bound]   # the loop runs on macOS only; a Linux runner deselects the suite (ADR-0139)
    if shell_suites.is_heavy(suite):
        marks.append(pytest.mark.heavy)
    if suite.name in PROCESS_BOUND_SUITES:
        marks.append(pytest.mark.process_bound)
    if shell_suites.is_slow(suite):
        marks.append(pytest.mark.slow(reason=f"runs a real script suite that takes over {shell_suites.SLOW_SECONDS:g} s"))
    elif suite.name in LOADED_PAST_CAP:
        marks.append(pytest.mark.slow(reason=LOADED_PAST_CAP[suite.name]))
    return pytest.param(suite, id=suite.name, marks=marks)


@pytest.mark.parametrize("suite", [_param(s) for s in shell_suites.discover()])
def test_shell_suite(suite):
    message = shell_suites.problem(suite)
    assert message is None, message
