import atexit
import os
import shutil
import subprocess
import sys
import tempfile
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


ORIGINAL_SG_STATE = os.environ.get("SG_STATE")   # the inherited value, read before any test runs (#1328)
# At import, before any module or module-scoped fixture reads it: a gate under the live loop inherits the loop's SG_STATE and
# SG_GH_PASS, and a module that captured either at import would write the live api.log. Each process (each xdist worker too)
# gets its own session dir, and no test starts with a loop's pass name.
_SESSION_STATE = tempfile.mkdtemp(prefix="squad-session-state-")
atexit.register(shutil.rmtree, _SESSION_STATE, ignore_errors=True)
os.environ["SG_STATE"] = _SESSION_STATE
os.environ.pop("SG_GH_PASS", None)


@pytest.fixture(autouse=True)
def _own_state_dir(monkeypatch, tmp_path_factory):
    """No test writes to the SG_STATE the suite inherited: a gate under the live loop inherits the loop's api.log and
    cooldown (#1328). Each test gets its own dir and no pass name; a test that sets or deletes either itself runs after this and wins."""
    monkeypatch.setenv("SG_STATE", str(tmp_path_factory.mktemp("state")))
    monkeypatch.delenv("SG_GH_PASS", raising=False)


def gui_tests_off():
    """True while the GUI test switch is off (scripts/gui-tests.sh off). The two markers are the switch's own: the one under
    the inherited $SG_STATE is the visible idiom, the home one survives a suite that inherits the machine's SG_STATE and cleans it."""
    state = Path(ORIGINAL_SG_STATE) if ORIGINAL_SG_STATE is not None else Path.home() / ".cache" / "squad-goal"
    return (state / "auto" / "gui-tests-off").exists() \
        or (Path.home() / ".squad-gui-tests-off").exists()


HOST_BOUND_MARKER = "host_bound"
HOST_ROOTS = ("MESENAI_REPO", "MESENAI_ROOT")
HOST_ABSENT_REASON = f"no MesenAI checkout on this runner: {' and '.join(HOST_ROOTS)} absent"
NODE_BOUND_MARKER = "node_bound"
NODE_ABSENT_REASON = "no node on this runner: the test runs the page's JavaScript under node"
SANDBOX_BOUND_MARKER = "sandbox_bound"
SANDBOX_ABSENT_REASON = "no macOS sandbox-exec on this runner: the OS signal wall (#1469) is not enforceable here (ADR-0139)"
SANDBOX_WALL_REASON = "this run is inside the signal wall: a process outside the session cannot be started from here (#1469)"
PROCESS_BOUND_MARKER = "process_bound"
PROCESS_ABSENT_REASON = "this runner denies the setuid process table binary: /bin/ps cannot be started from a session (ADR-0139)"
CC_BOUND_MARKER = "cc_bound"
CC_ABSENT_REASON = "no C compiler and no macOS process table on this runner: the compiled reader (#1511) is not built here (ADR-0139)"
CI_RUNNER_SWITCH = "SQUAD_CI_RUNNER"


def ci_runner_simulated():
    """True while `SQUAD_CI_RUNNER=1`: the MesenAI checkout and the node binary are hidden, as on a CI runner that holds neither.
    One switch, so the fixture-bound rule can be checked on this machine (#1323, ADR-0139)."""
    return os.environ.get(CI_RUNNER_SWITCH) == "1"


def host_present():
    """True when this runner holds the MesenAI checkout a host-bound test needs: `MESENAI_REPO` and `MESENAI_ROOT`,
    each set to a directory that exists. A name that is set but absent on disk counts as absent (ADR-0139)."""
    if ci_runner_simulated():
        return False
    for name in HOST_ROOTS:
        value = os.environ.get(name)
        if not value or not Path(value).expanduser().is_dir():
            return False
    return True


def node_present():
    return not ci_runner_simulated() and shutil.which("node") is not None


def sandbox_present():
    return not ci_runner_simulated() and os.path.exists("/usr/bin/sandbox-exec")


def inside_signal_wall():
    """True in a session the adapter started: its CLI carries the signal profile, and every process it starts inherits the variable."""
    return bool(os.environ.get("SQUAD_SIGNAL_PROFILE"))


def cc_present():
    """True when this runner builds the compiled process reader: macOS (its kernel structs) and a C compiler (#1511)."""
    return not ci_runner_simulated() and sys.platform == "darwin" and shutil.which("cc") is not None


def process_table_present():
    """True when a session on this runner may read the process table. A nested sandbox that denies the setuid-root binary
    fails every test that starts or observes a live process with `operation not permitted: /bin/ps`, by construction and
    not by a defect, so those tests are deselected there (ADR-0139)."""
    try:
        return subprocess.run(["/bin/ps", "-A"], capture_output=True).returncode == 0
    except OSError:
        return False


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "host_bound(reason): needs a MesenAI checkout this runner may not hold; deselected, never skipped, when it is absent (ADR-0139)")
    config.addinivalue_line(
        "markers",
        "node_bound: needs the node binary; deselected, never skipped, when it is absent (ADR-0139)")
    config.addinivalue_line(
        "markers",
        "sandbox_bound: needs macOS sandbox-exec; deselected, never skipped, when it is absent (ADR-0139)")
    config.addinivalue_line(
        "markers",
        "process_bound: needs to start or observe a live process through /bin/ps; deselected, never skipped, on a runner that denies it (ADR-0139)")
    config.addinivalue_line(
        "markers",
        "cc_bound: builds the compiled process reader; deselected, never skipped, on a runner with no C compiler (ADR-0139)")
    config._absent_deselected = []


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Print each deselected test by id and reason, so a runner's removal is read by name, never as a bare count (ADR-0139)."""
    rows = config._absent_deselected
    for nodeid, reason in rows:
        terminalreporter.write_line(f"deselected: {nodeid} -- {reason}")
    if rows:
        terminalreporter.write_line(f"{len(rows)} test(s) deselected for a fixture this runner lacks (ADR-0139)")


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
        _deselect_absent(config, items, HOST_BOUND_MARKER, HOST_ABSENT_REASON)
    # The same rule for node: a runner without the binary deselects the test that runs the page under it (ADR-0139).
    if not node_present():
        _deselect_absent(config, items, NODE_BOUND_MARKER, NODE_ABSENT_REASON)
    if not sandbox_present():
        _deselect_absent(config, items, SANDBOX_BOUND_MARKER, SANDBOX_ABSENT_REASON)
    elif inside_signal_wall():
        _deselect_absent(config, items, SANDBOX_BOUND_MARKER, SANDBOX_WALL_REASON)
    # A nested sandbox may deny the setuid process table binary; a test that starts or observes a live process then fails by
    # construction, so it is deselected by name, never skipped (ADR-0139).
    if not process_table_present():
        _deselect_absent(config, items, PROCESS_BOUND_MARKER, PROCESS_ABSENT_REASON)
    # The compiled process reader (#1511) is built on macOS only, and only with a C compiler.
    if not cc_present():
        _deselect_absent(config, items, CC_BOUND_MARKER, CC_ABSENT_REASON)


def _deselect_absent(config, items, marker_name, absent_reason):
    gone = [it for it in items if it.get_closest_marker(marker_name)]
    if not gone:
        return
    config.hook.pytest_deselected(items=gone)
    items[:] = [it for it in items if not it.get_closest_marker(marker_name)]
    config._absent_deselected.extend(
        (it.nodeid, it.get_closest_marker(marker_name).kwargs.get("reason", absent_reason)) for it in gone)
