"""A runner whose sandbox denies the process table deselects the process-bound tests, and never skips them (#1470).

A nested seatbelt can deny the setuid-root `/bin/ps`, so a test that starts or observes a live process fails by
construction with `operation not permitted: /bin/ps`, not by a defect. The suite asks the runner first: when the
process table cannot be read, every `process_bound` test is deselected by id and printed with its reason (ADR-0139).
"""

from tests import conftest


class _Mark:
    kwargs = {}


class _Item:
    def __init__(self, name, mark=None):
        self.name = name
        self.nodeid = f"tests/fake.py::{name}"
        self._mark = mark

    def get_closest_marker(self, name):
        return _Mark() if name == self._mark else None


class _Hook:
    def __init__(self):
        self.deselected = []

    def pytest_deselected(self, items):
        self.deselected.extend(items)


class _Config:
    def __init__(self):
        self._absent_deselected = []
        self.hook = _Hook()


def _collect(monkeypatch, present):
    monkeypatch.setattr(conftest, "process_table_present", lambda: present)
    config = _Config()
    items = [_Item("a", conftest.PROCESS_BOUND_MARKER), _Item("b")]
    conftest.pytest_collection_modifyitems(config, items)
    return config, [it.name for it in items]


def test_a_runner_that_cannot_read_the_process_table_deselects_the_process_bound_tests(monkeypatch):
    config, kept = _collect(monkeypatch, present=False)
    assert kept == ["b"]
    assert config._absent_deselected == [("tests/fake.py::a", conftest.PROCESS_ABSENT_REASON)]
    assert [it.name for it in config.hook.deselected] == ["a"]


def test_a_runner_that_reads_the_process_table_keeps_the_process_bound_tests(monkeypatch):
    config, kept = _collect(monkeypatch, present=True)
    assert kept == ["a", "b"]
    assert config._absent_deselected == []


def test_the_probe_reads_a_denied_ps_as_no_process_table(monkeypatch):
    def denied(*a, **kw):
        raise PermissionError(1, "Operation not permitted", "/bin/ps")
    monkeypatch.setattr(conftest.subprocess, "run", denied)
    assert conftest.process_table_present() is False


def test_the_deselection_reason_names_the_binary_that_is_denied():
    assert "/bin/ps" in conftest.PROCESS_ABSENT_REASON
