"""Suite-time guards: a single test call over the cap fails, and one that never returns is cut at its deadline.

The cap is read from the report, so it only judges a test that ended. A test that hangs walks past it: one xdist
worker spinning in a subprocess loop parks the whole suite and the machine with it (#130). The deadline is the cap's
own rule read while the test runs -- it carries the same exemption, a `slow(reason=...)` marker -- and it fails the
test by name instead of waiting for a return that never comes.
"""
import signal

import pytest

DEFAULT_CAP_SECONDS = 20.0
DEFAULT_DEADLINE_SECONDS = 60.0


def pytest_addoption(parser):
    parser.addini("slow_cap_seconds", "cap for one test call, in seconds", default=str(DEFAULT_CAP_SECONDS))
    parser.addini("test_deadline_seconds", "deadline for one test call, in seconds; 0 turns the deadline off",
                  default=str(DEFAULT_DEADLINE_SECONDS))


def pytest_configure(config):
    config.addinivalue_line("markers", "slow(reason): this test may run past the cap; the reason is required")


def _slow_reason(item):
    mark = item.get_closest_marker("slow")
    if mark is None:
        return None
    return mark.kwargs.get("reason") or (mark.args[0] if mark.args else None) or ""


def pytest_collection_modifyitems(items):
    for item in items:
        if _slow_reason(item) == "":
            raise pytest.UsageError(f"{item.nodeid}: the slow marker needs a reason")


def _deadline_seconds(item):
    """The bound for this test call, or 0 for none. A test that declares why it runs long is exempt, as it is from the cap."""
    seconds = float(item.config.getini("test_deadline_seconds"))
    if seconds <= 0 or not hasattr(signal, "SIGALRM") or _slow_reason(item) is not None:
        return 0.0
    return seconds


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    """Cut this one test call at its deadline: the alarm interrupts whatever it is waiting on and fails it by name."""
    seconds = _deadline_seconds(item)
    if not seconds:
        yield
        return
    previous = signal.signal(signal.SIGALRM, lambda *_: _over(item, seconds))
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _over(item, seconds):
    raise AssertionError(
        f"{item.nodeid} ran past its deadline of {seconds:g} s and was stopped. "
        "A test that never returns parks the whole suite and the machine with it; find what it waits on instead of "
        "raising the deadline."
    )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if call.when != "call" or report.failed or report.skipped:
        return
    cap = float(item.config.getini("slow_cap_seconds"))
    if report.duration > cap and _slow_reason(item) is None:
        report.outcome = "failed"
        report.longrepr = (
            f"{item.nodeid} took {report.duration:.1f} s, over the {cap:g} s cap. "
            "Investigate the cause; do not tune the test to sit under the cap."
        )
