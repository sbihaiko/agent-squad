import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

pytest_plugins = ["tests.slowcap", "tests.real_app_guard_plugin"]


def drop_live_paths():
    """The loop exports SG_BOARD_CACHE, and a gate it runs inherits it; hub.py reads it before its own SG_HUB_DIR, so a hub
    test module would read and write the live board (#521). Without it, each module's board cache is under its SG_HUB_DIR."""
    os.environ.pop("SG_BOARD_CACHE", None)
    # The loop exports SG_RUN_REVIEW too (#601): the shipped router graph's review gate runs the script it names, so a run a
    # test drives would review its children through the live loop's runreview.sh. Without it, the gate answers "no review here".
    os.environ.pop("SG_RUN_REVIEW", None)


@pytest.fixture(scope="session", autouse=True)
def _hub_seam_defaults(tmp_path_factory):
    """Give module-scoped hub fixtures the checkout their profile context requires."""
    import os
    drop_live_paths()
    old = os.environ.get("SG_CHECKOUT")
    if not old:
        os.environ["SG_CHECKOUT"] = str(tmp_path_factory.mktemp("checkout"))
    old_base = os.environ.get("SG_STATE_BASE")
    os.environ["SG_STATE_BASE"] = str(tmp_path_factory.mktemp("state_base"))   # a loaded profile's board cache is never the machine's (#1000)
    yield
    if old is None:
        os.environ.pop("SG_CHECKOUT", None)
    else:
        os.environ["SG_CHECKOUT"] = old
    if old_base is None:
        os.environ.pop("SG_STATE_BASE", None)
    else:
        os.environ["SG_STATE_BASE"] = old_base


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


@pytest.fixture(autouse=True)
def _own_caps_dir(monkeypatch, tmp_path_factory):
    """The machine-wide run and child leases (`squad/caps.py`): no test takes a slot of the Owner's real ceiling."""
    monkeypatch.setenv("SQUAD_CAPS_DIR", str(tmp_path_factory.mktemp("caps")))


@pytest.fixture(scope="session", autouse=True)
def _stop_test_dashboard_servers(tmp_path_factory):
    """A run a test starts may leave a dashboard server on its tmp folder; stop those when the session ends."""
    yield
    from tests.dashstop import stop_test_dashboard_servers
    stop_test_dashboard_servers(tmp_path_factory.getbasetemp())


@pytest.fixture(autouse=True)
def _stop_dashboard_servers_per_test(tmp_path_factory):
    """A test that starts a run or a dashboard leaves no server when it ends: each server idles up to 2 h, and a suite that starts one per test piles them up.
    A worker's basetemp is its own, so the stop reaches no server of another worker or session."""
    yield
    from tests.dashstop import stop_dashboards_by_state
    stop_dashboards_by_state(tmp_path_factory.getbasetemp())


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
    return (Path(os.environ.get("SG_STATE", Path.home() / ".cache" / "squad-goal")) / "auto" / "gui-tests-off").exists() \
        or (Path.home() / ".squad-gui-tests-off").exists()


HOST_BOUND_MARKER = "host_bound"
HOST_ROOTS = ("MESENAI_REPO", "MESENAI_ROOT")
HOST_ABSENT_REASON = f"no MesenAI checkout on this runner: {' and '.join(HOST_ROOTS)} absent"
NODE_BOUND_MARKER = "node_bound"
NODE_ABSENT_REASON = "no node on this runner: the test runs the page's JavaScript under node"


def host_present():
    """True when this runner holds the MesenAI checkout a host-bound test needs: `MESENAI_REPO` and `MESENAI_ROOT`,
    each set to a directory that exists. A name that is set but absent on disk counts as absent (ADR-0139)."""
    for name in HOST_ROOTS:
        value = os.environ.get(name)
        if not value or not Path(value).expanduser().is_dir():
            return False
    return True


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "host_bound: needs a MesenAI checkout this runner may not hold; deselected, never skipped, when it is absent (ADR-0139)")
    config.addinivalue_line(
        "markers",
        "node_bound: needs the node binary; deselected, never skipped, when it is absent (ADR-0139)")


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Name the deselection in the run's own summary, so a host-bound suite reads its removal instead of a bare count."""
    n = len(getattr(config, "_host_bound_deselected", []))
    if n:
        terminalreporter.write_line(f"{n} host-bound test(s) deselected: {HOST_ABSENT_REASON}")
    m = len(getattr(config, "_node_bound_deselected", []))
    if m:
        terminalreporter.write_line(f"{m} node-bound test(s) deselected: {NODE_ABSENT_REASON}")


def pytest_collection_modifyitems(config, items):
    # With the switch off, serial.sh deliberately does not run a command under the `gui-suite` lock -- so a test asserting
    # "the GUI suite command ran" fails by construction, and a run goes red on it. Skipped, never weakened: with the switch
    # on the test runs exactly as before.
    if gui_tests_off():
        skip = pytest.mark.skip(reason="the GUI test switch is off (scripts/gui-tests.sh off): the gui-suite lock does not run the GUI suite")
        for it in items:
            if it.name == "test_1392_the_gui_suite_command_goes_through_the_serial_lock_and_the_shell_suites_beside_it":
                it.add_marker(skip)
    # A test whose fixture is a repository this runner does not own is deselected, never skipped: a skip would read green
    # while proving nothing (ADR-0018 item 6; ADR-0062 items 2 and 3 refuse a skip). The deselection is reported by name.
    if not host_present():
        deselected = [it for it in items if it.get_closest_marker(HOST_BOUND_MARKER)]
        if deselected:
            config.hook.pytest_deselected(items=deselected)
            items[:] = [it for it in items if not it.get_closest_marker(HOST_BOUND_MARKER)]
            config._host_bound_deselected = deselected
    # The same rule for node: a runner without the binary deselects the test that runs the page under it (ADR-0139).
    if shutil.which("node") is None:
        deselected = [it for it in items if it.get_closest_marker(NODE_BOUND_MARKER)]
        if deselected:
            config.hook.pytest_deselected(items=deselected)
            items[:] = [it for it in items if not it.get_closest_marker(NODE_BOUND_MARKER)]
            config._node_bound_deselected = deselected
