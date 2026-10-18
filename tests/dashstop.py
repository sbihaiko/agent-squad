"""The session-end stop of the dashboard servers a test left behind."""
import json
import os
import re
import signal
import subprocess
from pathlib import Path

from squad import dashboard_server


def dashboard_pattern(basetemp):
    """The `pkill -f` pattern of the dashboard servers that serve a folder under `basetemp`, and no other. Each xdist worker
    and each pytest session on the machine has its own basetemp: a pattern any `pytest-of-` folder matches kills the
    dashboards a test of another worker or session still reads (#261)."""
    base = re.sub(r"([.\[\]()*+?{}|^$\\])", r"\\\1", str(Path(basetemp).resolve()))
    return rf"squad\.dashboard_server .*{base}/"


def stop_test_dashboard_servers(basetemp):
    subprocess.run(["pkill", "-f", dashboard_pattern(basetemp)], check=False)


def stop_dashboards_by_state(basetemp):
    """Stop each dashboard server whose state file names a folder under `basetemp`. Reads the state directory only, so a test that
    started none costs no process: the per-test stop runs after every test."""
    base = Path(basetemp).resolve()
    for f in dashboard_server._state_dir().glob("*.json"):
        try:
            rec = json.loads(f.read_text())
            folder, pid = Path(rec["folder"]), int(rec["pid"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if base in folder.parents:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
            f.unlink(missing_ok=True)
