"""Suite-time guards: a single test call over the cap fails, and one that never returns is cut at its deadline.

The cap is read from the report, so it only judges a test that ended. A test that hangs walks past it: one xdist
worker spinning in a subprocess loop parks the whole suite and the machine with it (#130). The deadline is the cap's
own rule read while the test runs -- it carries the same exemption, a `slow(reason=...)` marker -- and it fails the
test by name instead of waiting for a return that never comes.

The fast battery (`-m "not heavy"`, the one CI runs) stays fast by one more rule: a call over 1 s carries `heavy`. The
guard fails an unmarked call only from `heavy_cap_seconds` (2 s), twice the line, so xdist and machine load do not
fail a test that sits near 1 s; a call that really drifts past it is named.

Both lines guard the work a test does, not the runner's load. A call over a line is read once more against the
scheduling slowdown measured right then (`contention_factor`): the measured wall time is divided by it, bounded, so a
call that only waited for a CPU is not named while a call that sleeps past the line still is. The measure runs only for
a call already over a line, so a passing suite pays nothing.

The 1 s line is a timing-only finding, and xdist load still turns a 0.4 s suite into 2.2 s past what the factor excuses
(#629). So the mode decides how it is read. Under xdist (pytest-xdist's own worker state: the call runs in a worker, whose
config carries `workerinput`), an unmarked call at or over `heavy_cap_seconds` does not fail; it is named in the summary
as `SLOW-UNMARKED (deferred to the serial run)`. In a serial run (`-p no:xdist`, or no `-n`) it fails, and the full suite
runs that mode on the same tip after the xdist run, so the line is still enforced. The mode is read from the runner, never
from a worker count. The 20 s cap and every functional failure are strict in both modes.
"""
import signal
import time

import pytest

DEFAULT_CAP_SECONDS = 20.0
DEFAULT_DEADLINE_SECONDS = 60.0
DEFAULT_HEAVY_CAP_SECONDS = 2.0
MAX_CONTENTION_FACTOR = 4.0
DEFERRED_KEY = "slow_unmarked_deferred"
DEFERRED_TITLE = "SLOW-UNMARKED (deferred to the serial run)"
CALIBRATION_SECONDS = 0.05


def pytest_addoption(parser):
    parser.addini("slow_cap_seconds", "cap for one test call, in seconds", default=str(DEFAULT_CAP_SECONDS))
    parser.addini("test_deadline_seconds", "deadline for one test call, in seconds; 0 turns the deadline off",
                  default=str(DEFAULT_DEADLINE_SECONDS))
    parser.addini("heavy_cap_seconds", "an unmarked test call at or over this fails a serial run; under xdist it is reported; mark it heavy", default=str(DEFAULT_HEAVY_CAP_SECONDS))


def pytest_configure(config):
    config.addinivalue_line("markers", "slow(reason): this test may run past the cap; the reason is required")
    config.addinivalue_line("markers", "heavy: this test call takes 1 s or more; the fast battery deselects it")


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


def contention_factor():
    """How much slower than its own CPU time a short spin runs right now: 1 on an idle machine, more when the process
    waits for a CPU. Bounded by `MAX_CONTENTION_FACTOR`, so load can excuse only so much of a call."""
    wall_start, cpu_start = time.perf_counter(), time.thread_time()
    while time.perf_counter() - wall_start < CALIBRATION_SECONDS:
        pass
    wall, cpu = time.perf_counter() - wall_start, time.thread_time() - cpu_start
    if cpu <= 0:
        return 1.0
    return min(MAX_CONTENTION_FACTOR, max(1.0, wall / cpu))


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
    heavy_cap = float(item.config.getini("heavy_cap_seconds"))
    duration = report.duration
    if duration > min(cap, heavy_cap):
        duration /= contention_factor()
    if duration > cap and _slow_reason(item) is None:
        report.outcome = "failed"
        report.longrepr = (
            f"{item.nodeid} took {report.duration:.1f} s, over the {cap:g} s cap. "
            "Investigate the cause; do not tune the test to sit under the cap."
        )
        return
    if duration >= heavy_cap and item.get_closest_marker("heavy") is None:
        if _under_xdist(item.config):
            report.user_properties.append((DEFERRED_KEY, f"{item.nodeid} took {report.duration:.1f} s"))
            return
        report.outcome = "failed"
        report.longrepr = (
            f"{item.nodeid} took {report.duration:.1f} s, at or over the {heavy_cap:g} s line for an unmarked call. "
            "Mark it `@pytest.mark.heavy` so the fast battery leaves it out, or find why it is slow."
        )


def _under_xdist(config):
    """True when this call runs in a pytest-xdist worker: xdist gives each worker's config its `workerinput`."""
    return hasattr(config, "workerinput")


_DEFERRED: list[str] = []


def pytest_runtest_logreport(report):
    """Collect the deferred findings where the reports arrive: in the xdist controller, which writes the summary."""
    for key, text in getattr(report, "user_properties", ()):
        if key == DEFERRED_KEY:
            _DEFERRED.append(text)


def pytest_terminal_summary(terminalreporter):
    """Name every unmarked call the xdist run deferred, so the finding is read even though the run stays green."""
    for text in _DEFERRED:
        terminalreporter.write_line(f"{DEFERRED_TITLE}: {text}, at or over the heavy line; the serial run enforces it")
