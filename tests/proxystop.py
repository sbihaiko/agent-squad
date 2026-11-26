"""The model proxies a test process started, and the end each test gives them (#1686)."""
import os
import signal
import subprocess
import time

PROXY_SCRIPT = "model_proxy.py"
STARTED = []


def live_proxy_pids(parent_pid):
    """The pids of model_proxy.py processes whose parent is `parent_pid`."""
    result = subprocess.run(["pgrep", "-P", str(parent_pid), "-f", PROXY_SCRIPT], capture_output=True, text=True)
    if result.returncode > 1:
        raise RuntimeError(f"pgrep could not list the children of {parent_pid}: {result.stderr.strip()}")
    return [int(pid) for pid in result.stdout.split()]


def describe(pids):
    """One line per pid: its parent and its command line, so a leak names the test folder its refusals path holds (#2989)."""
    lines = []
    for pid in pids:
        result = subprocess.run(["ps", "-o", "ppid=,args=", "-p", str(pid)], capture_output=True, text=True)
        lines.append(f"{pid}: {result.stdout.strip() or 'gone'}")
    return "\n".join(lines)


def track_starts(module):
    """Record each proxy the module starts, so the test that started it ends it at teardown."""
    start = module.start_proxy

    def tracked(*args, **kwargs):
        entry = start(*args, **kwargs)
        STARTED.append(entry)
        return entry

    module.start_proxy = tracked


def end_started():
    """End every proxy recorded since the last call; the list is cleared even when one end raises."""
    try:
        while STARTED:
            end_proxy(STARTED.pop()["pid"])
    finally:
        STARTED.clear()


def end_proxy(pid, timeout=5):
    """Stop a proxy this process started and reap it, so no survivor or zombie is left."""
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if os.waitpid(pid, os.WNOHANG)[0]:
                return
        except ChildProcessError:
            return
        time.sleep(0.05)
    try:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
    except (ProcessLookupError, ChildProcessError):
        pass
