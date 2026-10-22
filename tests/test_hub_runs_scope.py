"""The hub of one profile lists only the runs of that profile.

`runs()` finds live runs by a machine-wide `ps` scan for dashboard servers. A dashboard whose run folder lies outside
the profile's own run roots (its checkout, SG_PROJECTS, SG_FORK_DIR) belongs to another profile and is never listed,
and never adds its folder to `project_dirs()`.
"""
import importlib.util
import os
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib" / "squad-goal" / "scripts" / "hub.py"


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_runs_scope", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def machine(hub, monkeypatch):
    """Two profiles' dashboards alive on one machine: one under this profile's checkout, one under another's."""
    base = Path(tempfile.mkdtemp(prefix="hubscope-"))   # tmp_path holds "pytest-of-", which runs() skips as a fixture
    mine, other = base / "mine", base / "other"
    for co in (mine, other):
        (co / "runs" / "run-a").mkdir(parents=True)
    run_mine, run_other = mine / "runs" / "run-a", other / "runs" / "run-a"
    ps = "\n".join(f"{100 + i} /usr/bin/python3 -m squad.dashboard_server {d}" for i, d in enumerate((run_mine, run_other)))

    def fake_sh(cmd, *args, **kw):
        if cmd == "/bin/ps":
            return ps
        if cmd == hub.LSOF:
            return "n127.0.0.1:5555"
        return ""

    monkeypatch.setattr(hub, "sh", fake_sh)
    monkeypatch.setattr(hub, "CHECKOUT", str(mine))
    monkeypatch.setattr(hub, "STATE", base / "state")
    monkeypatch.delenv("SG_PROJECTS", raising=False)
    monkeypatch.delenv("SG_FORK_DIR", raising=False)
    return mine, other


def test_runs_lists_no_dashboard_of_another_profile(hub, machine):
    mine, other = machine
    assert [r["project"] for r in hub.runs() if r.get("port")] == ["mine"]
    assert not any(str(other) in str(d) for d in hub.project_dirs())


def test_a_run_root_named_by_sg_projects_or_the_fork_dir_is_still_listed(hub, machine, monkeypatch):
    mine, other = machine
    monkeypatch.setenv("SG_FORK_DIR", str(other))
    assert len([r for r in hub.runs() if r.get("port")]) == 2


def test_a_dashboard_started_by_any_interpreter_is_listed(hub, machine, monkeypatch):
    """The ps scan reads the server's own argv, never the spelling of the interpreter: macOS starts the framework build
    as `.../MacOS/Python`, and a scan that required the program to read `python` listed no run of this machine, so the
    hub showed no live run at all (#1751)."""
    mine, other = machine
    run_mine = mine / "runs" / "run-a"
    ps = (f"4242 /Library/Frameworks/Python.framework/Versions/3.12/Resources/Python.app/Contents/MacOS/Python"
          f" -m squad.dashboard_server {run_mine}")

    def fake_sh(cmd, *args, **kw):
        if cmd == "/bin/ps":
            return ps
        if cmd == hub.LSOF:
            return "n127.0.0.1:5555"
        return ""

    monkeypatch.setattr(hub, "sh", fake_sh)
    assert [r["project"] for r in hub.runs() if r.get("port")] == ["mine"]
