import atexit
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

pytest_plugins = ["tests.slowcap", "tests.real_app_guard_plugin"]


def _serve_the_whole_hub():
    """A read of contrib/squad-goal/scripts/hub.py serves the whole hub: hub.py and the theme files it loads (#2742).

    The hub is one module namespace over hub.py and its theme files, so a test that asserts about the hub's code must see
    all of it, exactly as it saw one file before the split. The patch answers that one path only; every other read is the
    untouched read_text. It is installed when this file loads, before any test module is imported, so a module-level
    source read (a constant built at import) reads the composed text too.
    """
    from pathlib import Path as _Path

    from tests.hub_source import hub_source
    real = _Path.read_text

    def read_text(self, *args, **kwargs):
        if self.name == "hub.py" and self.parent.name == "scripts" and self.parent.parent.name == "squad-goal":
            return hub_source()
        return real(self, *args, **kwargs)

    _Path.read_text = read_text
    atexit.register(lambda: setattr(_Path, "read_text", real))


_serve_the_whole_hub()


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
    old_projects = os.environ.get("SG_CLAUDE_PROJECTS")
    os.environ["SG_CLAUDE_PROJECTS"] = str(tmp_path_factory.mktemp("claude_projects"))   # the hub's operator source reads no real transcript (#1756)
    yield
    if old is None:
        os.environ.pop("SG_CHECKOUT", None)
    else:
        os.environ["SG_CHECKOUT"] = old
    if old_base is None:
        os.environ.pop("SG_STATE_BASE", None)
    else:
        os.environ["SG_STATE_BASE"] = old_base
    if old_projects is None:
        os.environ.pop("SG_CLAUDE_PROJECTS", None)
    else:
        os.environ["SG_CLAUDE_PROJECTS"] = old_projects


@pytest.fixture(scope="session", autouse=True)
def _one_dashboard_runner():
    """One node process serves every run_node call of the session, started and answered here in setup, which no cap
    measures. A call that starts its own node pays the binary's 89 MB cold start inside a measured call: the first node
    user of a serial shard paid it whole, 3.1 s against the 2 s line (#1534, #2580). A start alone is not enough -- Popen
    returns before node has loaded -- so setup takes one answer. No node on this runner means no start; the call that
    needs node still says so.

    Guards: tests/test_dashboard_record.py::test_the_session_starts_the_dashboard_runner_before_a_measured_call,
    tests/test_dashboard.py::test_start_runner_takes_one_answer_before_the_first_call."""
    from tests import dashboard_helpers
    dashboard_helpers.start_runner()
    yield
    dashboard_helpers.stop_runner()


DESIGN = Path(__file__).resolve().parent.parent / "docs" / "design"


@pytest.fixture(autouse=True)
def _proxies_end_with_their_test():
    """A model proxy a test starts is ended when that test ends, pass or fail (#1686)."""
    yield
    from tests.proxystop import end_started
    end_started()


@pytest.fixture(scope="session", autouse=True)
def _no_model_proxy_survives():
    """A model proxy this test process started must be ended by its test; one that outlives the session is a leak (#1686)."""
    yield
    from tests.proxystop import live_proxy_pids
    leaked = live_proxy_pids(os.getpid())
    if leaked:
        raise AssertionError(f"model proxy process(es) {', '.join(map(str, leaked))} outlived the test session (#1686)")


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


def drop_git_location():
    """A git hook or a gate exports GIT_DIR, and every test inherits it: a fixture that runs `git init --bare` or `git config`
    in its own folder then writes the checkout that runs the suite, and set core.bare = true there (#2306).
    This process drops the three names the criteria list. A spawned shell suite starts with all five cleared, in
    tests/shell_suites._run."""
    from tests.shell_suites import GIT_HOOK_LOCATION
    for name in GIT_HOOK_LOCATION:
        os.environ.pop(name, None)


drop_git_location()   # at import, before any module or fixture starts git


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
SDK_BOUND_MARKER = "sdk_bound"
SDK_ABSENT_REASON = "the hosted interpreter cannot import claude_agent_sdk on this runner: no hosted session starts here (ADR-0139)"
CC_BOUND_MARKER = "cc_bound"
CC_ABSENT_REASON = "no C compiler and no macOS process table on this runner: the compiled reader (#1511) is not built here (ADR-0139)"
CI_RUNNER_SWITCH = "SQUAD_CI_RUNNER"
CLAUDE_BOUND_MARKER = "claude_bound"
CLAUDE_ABSENT_REASON = ("no claude CLI, or sandbox-exec cannot apply a profile on this runner: the real session probe "
                        "(#1619) needs both (ADR-0139)")
MACOS_BOUND_MARKER = "macos_bound"
MACOS_ABSENT_REASON = ("a loop script suite needs the macOS userland the loop runs on (BSD date and stat, sandbox-exec, a set "
                       "TMPDIR); this runner is not macOS (ADR-0139)")


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
    """True when this runner builds the compiled process reader: macOS (its kernel structs), a C compiler, and a build
    folder it can write (#1511). A read-only TMPDIR deselects the test rather than failing it at the read."""
    if ci_runner_simulated() or sys.platform != "darwin" or shutil.which("cc") is None:
        return False
    return os.access(os.environ.get("TMPDIR") or "/tmp", os.W_OK)


def macos_present():
    """True on macOS, the one platform the loop's scripts run on. `SQUAD_CI_RUNNER=1` reads as a Linux runner (ADR-0139)."""
    return sys.platform == "darwin" and not ci_runner_simulated()


def sdk_present():
    """True when the interpreter a hosted session starts under imports the SDK. The CI install has no `sdk` extra, so a
    hosted start refuses there, by construction and not by a defect (ADR-0139)."""
    if ci_runner_simulated():
        return False
    return subprocess.run([sys.executable, "-c", "import claude_agent_sdk"], capture_output=True).returncode == 0


def process_table_present():
    """True when a session on this runner may read the process table. A nested sandbox that denies the setuid-root binary
    fails every test that starts or observes a live process with `operation not permitted: /bin/ps`, by construction and
    not by a defect, so those tests are deselected there (ADR-0139)."""
    try:
        return subprocess.run(["/bin/ps", "-A"], capture_output=True).returncode == 0
    except OSError:
        return False


def sandbox_applies():
    """True when sandbox-exec applies a profile here. A session nested in another sandbox is refused the apply (ADR-0139)."""
    if not sandbox_present():
        return False
    return subprocess.run(["/usr/bin/sandbox-exec", "-p", "(version 1)(allow default)", "/usr/bin/true"],
                          capture_output=True).returncode == 0


def claude_present():
    """True when the real `claude` CLI is on the path and sandbox-exec applies a profile for it to run under (#1619)."""
    return not ci_runner_simulated() and shutil.which("claude") is not None and sandbox_applies()


def pytest_sessionstart(session):
    session.config._reap_since = time.time()


def pytest_sessionfinish(session):
    """The session ends the processes it started and left behind (#1722): a leftover of this tree whose parent is 1. A worker
    never reaps: the controller does, once, after every worker ended. A session a test started ends nothing (#2680): the
    detached processes it sees belong to the session that started it."""
    if hasattr(session.config, "workerinput"):
        return
    from tests import reap_leaks
    ended = reap_leaks.session_reap(session.config.rootpath, getattr(session.config, "_reap_since", time.time()))
    if ended:
        print(f"\nreap-leaks: ended {len(ended)} leftover process(es) the session left behind: {ended}", file=sys.stderr)


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
    config.addinivalue_line(
        "markers",
        "sdk_bound: starts a hosted session; deselected, never skipped, where its interpreter cannot import the SDK (ADR-0139)")
    config.addinivalue_line(
        "markers",
        "macos_bound: runs a loop script suite; deselected, never skipped, on a runner that is not macOS (ADR-0139)")
    config.addinivalue_line(
        "markers",
        "claude_bound: runs the real claude CLI under sandbox-exec; deselected, never skipped, where either is absent (ADR-0139)")
    config._absent_deselected = []


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Print each deselected test by id and reason, so a runner's removal is read by name, never as a bare count (ADR-0139)."""
    rows = config._absent_deselected
    for nodeid, reason in rows:
        terminalreporter.write_line(f"deselected: {nodeid} -- {reason}")
    if rows:
        terminalreporter.write_line(f"{len(rows)} test(s) deselected for a fixture this runner lacks (ADR-0139)")


FAIL_TAIL_LINES = 15


def pytest_runtest_logreport(report):
    """On GitHub Actions, print each failure by id and its last lines the moment it ends, so a shard cut at its job timeout
    still names its failures; the end-of-run summary never prints for a cancelled job."""
    if os.environ.get("GITHUB_ACTIONS") != "true" or not report.failed:
        return
    tail = "\n".join(report.longreprtext.splitlines()[-FAIL_TAIL_LINES:])
    sys.stdout.write(f"\nFAILED-NOW {report.nodeid} ({report.when})\n{tail}\n")
    sys.stdout.flush()


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
    # The nightly's local half (ADR-0153) keeps only what a Linux runner deselects, read from the same rule.
    if os.environ.get(LOCAL_HALF_SWITCH) == "1":
        _keep_linux_deselected(config, items)
    for marker_name, reason in absent_markers(items):
        _deselect_absent(config, items, marker_name, reason)


LOCAL_HALF_SWITCH = "SQUAD_NIGHTLY_LOCAL_HALF"


def absent_markers(items):
    """(marker, reason) for each fixture this runner lacks: the one rule of what a runner deselects (ADR-0139).

    A MesenAI checkout, node, sandbox-exec (or a session outside the signal wall), the process table, a C compiler and macOS
    each have a marker. A hosted session's SDK and the real claude CLI are read only when a collected test needs them, since
    each read starts a process."""
    rows = []
    if not host_present():
        rows.append((HOST_BOUND_MARKER, HOST_ABSENT_REASON))
    if not node_present():
        rows.append((NODE_BOUND_MARKER, NODE_ABSENT_REASON))
    if not sandbox_present():
        rows.append((SANDBOX_BOUND_MARKER, SANDBOX_ABSENT_REASON))
    elif inside_signal_wall():
        rows.append((SANDBOX_BOUND_MARKER, SANDBOX_WALL_REASON))
    # A nested sandbox may deny the setuid process table binary; a test that starts or observes a live process then fails by
    # construction, so it is deselected by name, never skipped (ADR-0139).
    if not process_table_present():
        rows.append((PROCESS_BOUND_MARKER, PROCESS_ABSENT_REASON))
    # The compiled process reader (#1511) is built on macOS only, and only with a C compiler.
    if not cc_present():
        rows.append((CC_BOUND_MARKER, CC_ABSENT_REASON))
    # A loop script suite assumes the macOS userland; a Linux runner deselects it by name.
    if not macos_present():
        rows.append((MACOS_BOUND_MARKER, MACOS_ABSENT_REASON))
    # A hosted session needs the SDK in its interpreter; a runner installed without the `sdk` extra refuses the start.
    if any(it.get_closest_marker(SDK_BOUND_MARKER) for it in items) and not sdk_present():
        rows.append((SDK_BOUND_MARKER, SDK_ABSENT_REASON))
    if any(it.get_closest_marker(CLAUDE_BOUND_MARKER) for it in items) and not claude_present():
        rows.append((CLAUDE_BOUND_MARKER, CLAUDE_ABSENT_REASON))
    return rows


def linux_runner_markers(items):
    """The markers a Linux CI runner deselects: `absent_markers` read with the CI runner switch on, never a second list."""
    saved = os.environ.get(CI_RUNNER_SWITCH)
    os.environ[CI_RUNNER_SWITCH] = "1"
    try:
        return {name for name, _ in absent_markers(items)}
    finally:
        if saved is None:
            os.environ.pop(CI_RUNNER_SWITCH, None)
        else:
            os.environ[CI_RUNNER_SWITCH] = saved


def _keep_linux_deselected(config, items):
    """The nightly's local half (ADR-0153): every test a Linux runner runs is the GitHub half's, so it is deselected here."""
    linux = linux_runner_markers(items)
    gone = [it for it in items if not any(it.get_closest_marker(m) for m in linux)]
    if not gone:
        return
    config.hook.pytest_deselected(items=gone)
    items[:] = [it for it in items if any(it.get_closest_marker(m) for m in linux)]


def _deselect_absent(config, items, marker_name, absent_reason):
    gone = [it for it in items if it.get_closest_marker(marker_name)]
    if not gone:
        return
    config.hook.pytest_deselected(items=gone)
    items[:] = [it for it in items if not it.get_closest_marker(marker_name)]
    config._absent_deselected.extend(
        (it.nodeid, it.get_closest_marker(marker_name).kwargs.get("reason", absent_reason)) for it in gone)
