import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

pytest_plugins = ["tests.slowcap", "tests.real_app_guard_plugin"]

DESIGN = Path(__file__).resolve().parent.parent / "docs" / "design"


@pytest.fixture(autouse=True)
def _no_real_page(monkeypatch):
    """No test opens a page in the Owner's browser: `launch` opens the dashboard by default, so every test that starts a run without its own opener would. A test that checks the opening injects its opener or patches `launch.headless`."""
    monkeypatch.setenv("SQUAD_HEADLESS", "1")
    import webbrowser
    monkeypatch.setattr(webbrowser, "open", lambda *a, **k: False)


@pytest.fixture(autouse=True)
def _no_host_pytest_plugins(monkeypatch):
    """A gate command that runs `python3 -m pytest` in a fake subject must not load the plugins of the host's global python3: a broken one (deepeval on an old tenacity) crashes pytest at import, and the gate fails for a reason the test never set up."""
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")


@pytest.fixture(autouse=True)
def _own_memory_dir(monkeypatch, tmp_path_factory):
    """`squad setup` writes a memory index outside the project: no test touches the Owner's ~/.claude."""
    monkeypatch.setenv("SQUAD_MEMORY_DIR", str(tmp_path_factory.mktemp("memory")))


@pytest.fixture(scope="session", autouse=True)
def _stop_test_dashboard_servers():
    """A run a test starts may leave a dashboard server on its tmp folder; stop those when the session ends."""
    yield
    import subprocess
    subprocess.run(["pkill", "-f", r"squad\.dashboard_server .*pytest-of-"], check=False)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="session", autouse=True)
def _real_git_first_on_path():
    """On macOS `/usr/bin/git` is a shim that runs xcrun on every call, which doubles the cost of each fork. Put the real binary first: same git, one resolve per session.

    Guard: the git-running tests; a missing xcrun leaves PATH as it was."""
    import os
    import shutil
    import subprocess
    import tempfile
    if sys.platform != "darwin" or shutil.which("xcrun") is None:
        yield
        return
    r = subprocess.run(["xcrun", "-f", "git"], capture_output=True, text=True)
    old = os.environ["PATH"]
    link_dir = Path(tempfile.mkdtemp(prefix="squad-git-"))     # git alone: the tools dir also holds a python3 with no pytest
    if r.returncode == 0 and r.stdout.strip():
        (link_dir / "git").symlink_to(r.stdout.strip())
        os.environ["PATH"] = str(link_dir) + os.pathsep + old
    try:
        yield
    finally:
        os.environ["PATH"] = old
        shutil.rmtree(link_dir, ignore_errors=True)


def gui_tests_off():
    """True while the GUI test switch is off (scripts/gui-tests.sh off). The two markers are the switch's own: the one under
    $SG_STATE is the visible idiom, the home one survives a suite that inherits the machine's SG_STATE and cleans it."""
    import os
    return (Path(os.environ.get("SG_STATE", Path.home() / ".cache" / "squad-goal")) / "auto" / "gui-tests-off").exists() \
        or (Path.home() / ".squad-gui-tests-off").exists()


def pytest_collection_modifyitems(config, items):
    # With the switch off, fork-gate.sh hands the GUI command to serial.sh, which deliberately does not run it -- so a test
    # asserting "the GUI suite the gate already ran must keep running" fails by construction, and the gate itself goes red on
    # it. Skipped, never weakened: with the switch on the test runs exactly as before.
    if not gui_tests_off():
        return
    skip = pytest.mark.skip(reason="the GUI test switch is off (scripts/gui-tests.sh off): the gate does not run the GUI suite")
    for it in items:
        if it.name == "test_the_fork_gate_runs_the_gui_command_and_the_shell_suites_and_fails_on_either":
            it.add_marker(skip)
