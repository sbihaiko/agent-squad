"""The session-end stop of the dashboard servers a test left behind."""
import re
import subprocess
from pathlib import Path


def dashboard_pattern(basetemp):
    """The `pkill -f` pattern of the dashboard servers that serve a folder under `basetemp`, and no other. Each xdist worker
    and each pytest session on the machine has its own basetemp: a pattern any `pytest-of-` folder matches kills the
    dashboards a test of another worker or session still reads (#261)."""
    base = re.sub(r"([.\[\]()*+?{}|^$\\])", r"\\\1", str(Path(basetemp).resolve()))
    return rf"squad\.dashboard_server .*{base}/"


def stop_test_dashboard_servers(basetemp):
    subprocess.run(["pkill", "-f", dashboard_pattern(basetemp)], check=False)
