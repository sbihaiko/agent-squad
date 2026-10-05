import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

pytest_plugins = ["tests.slowcap"]

DESIGN = Path(__file__).resolve().parent.parent / "docs" / "design"


def pytest_configure(config):
    config.addinivalue_line("markers", "chrome: drives headless Chrome; one such test runs at a time across the xdist workers")


@pytest.fixture(autouse=True)
def _one_chrome(request):
    """A `chrome` test waits here, in setup, for its turn: the wait is not part of the call the slow cap times (ADR-0123)."""
    if request.node.get_closest_marker("chrome") is None:
        yield
        return
    import fcntl
    import tempfile
    with open(Path(tempfile.gettempdir()) / "agent-squad-chrome.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="session", autouse=True)
def _real_git_first_on_path():
    """On macOS `/usr/bin/git` is a shim that runs xcrun on every call, which doubles the cost of each fork. Put the real binary first: same git, one resolve per session (#132).

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
