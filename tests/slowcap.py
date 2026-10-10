"""Suite-time guard: a single test call over the cap fails, unless it carries `slow(reason=...)`."""
import pytest

DEFAULT_CAP_SECONDS = 20.0


def pytest_addoption(parser):
    parser.addini("slow_cap_seconds", "cap for one test call, in seconds", default=str(DEFAULT_CAP_SECONDS))


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
