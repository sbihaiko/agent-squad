"""A test that starts a dashboard server leaves none when it ends: the tests' own conftest reaps it per test, not at session end."""
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

ROOT = Path(__file__).resolve().parent.parent

INNER_TEST = '''
import subprocess
from pathlib import Path
from squad import dashboard_server


def test_starts_a_dashboard(tmp_path):
    folder = tmp_path / "run"
    folder.mkdir()
    port = dashboard_server.start(folder, idle=7200, watch=0.2)
    assert port
    Path("{marker}").write_text(str(folder.resolve()))


def test_the_next_test_finds_it_gone():
    folder = Path("{marker}").read_text()
    r = subprocess.run(["pgrep", "-f", "squad.dashboard_server " + folder], capture_output=True, text=True)
    assert not r.stdout.strip(), "a dashboard server outlived the test that started it"
'''


@pytest.mark.heavy
def test_a_test_that_starts_a_dashboard_leaves_no_server_when_it_ends(pytester, monkeypatch, tmp_path):
    marker = tmp_path / "folder.txt"
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    monkeypatch.setenv("SQUAD_DASHBOARD_STATE_DIR", str(tmp_path / "state"))
    pytester.makeconftest('pytest_plugins = ["tests.conftest"]')
    pytester.makepyfile(test_inner=INNER_TEST.format(marker=marker))
    result = pytester.runpytest_subprocess("-p", "no:deepeval", "-p", "no:cacheprovider", "-p", "no:xdist")
    result.assert_outcomes(passed=2)
