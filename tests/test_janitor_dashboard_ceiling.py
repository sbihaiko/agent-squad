"""Issue #70: a counted ceiling on the dashboards the loop started.

Rules B, J and C reap the dashboard of the one run the pass knows about. A run that left active-runs.txt, or a record the pass
cannot read, leaves its dashboard behind, and nothing bounded the count: 41 were alive after the freeze. One pass runs here in a
throwaway checkout, over a live run's dashboard and a dashboard no run the pass can see owns, and the test reads what the pass did.

The pass runs dry on purpose: a dry pass names the ceiling and its victims and kills nothing, so this test never signals a process
of the machine it runs on. The kills themselves are the shell suite's, beside the script.

Host-free: the janitor's stubbed gh lives in tmp_path, and its state and checkout in a temp folder of their own (#261: a
folder no pytest session's session-end stop matches, so a dashboard of this test lives until the test stops it).
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from tests.dashstop import dashboard_pattern

SCRIPTS = Path(__file__).resolve().parent.parent / "contrib" / "squad-goal" / "scripts"
JANITOR = SCRIPTS / "janitor.py"
# The dashboard server on the machine is named by its argv: the pass reads it out of ps (`python -m squad.dashboard_server
# <folder>`). A file by that name, started as a process, is what makes this fixture a process and not a string.
DASH_NAME = "squad.dashboard_server"
DASH_BODY = "import sys, time\ntime.sleep(float(sys.argv[2]) if len(sys.argv) > 2 else 60)\n"
# An executable the system already trusts: exits 0 and prints nothing, as the stub gh and an lsof that names no cwd must (#261).
TRUE = next(p for p in ("/usr/bin/true", "/bin/true") if os.path.exists(p))


def _env(root, tmp_path, dash_max):
    gh = tmp_path / "bin"
    gh.mkdir(exist_ok=True)
    if not (gh / "gh").exists():
        (gh / "gh").symlink_to(TRUE)
    e = {k: v for k, v in os.environ.items() if not k.startswith(("SG_", "FG_"))}
    e.update({"PATH": str(gh) + ":/usr/bin:/bin", "HOME": str(tmp_path / "home"),
              "SG_STATE": str(root / "st"), "SG_CHECKOUT": str(root / "co"), "SG_REPO": "x/y", "SG_BASE": "main", "SG_LSOF": TRUE,
              "JANITOR_DRY": "1", "JANITOR_DIAG": "1", "JANITOR_DASHBOARD_MAX": str(dash_max), "JANITOR_NOTIFY": "/bin/true"})
    return e


def _checkout():
    """The root of the pass's state and checkout: outside every pytest basetemp. Any pytest session on the machine stops
    the dashboards under a `pytest-of-` folder when it ends; a checkout there lost its dashboards mid-pass (#261)."""
    return Path(tempfile.mkdtemp(prefix="sg-janitor-")).resolve()


def _run(name, root, *, live):
    """A run folder of the checkout's runs. A live run carries a fresh record with no verdict; a finished one carries a verdict."""
    f = root / "co" / "runs" / name
    f.mkdir(parents=True, exist_ok=True)
    (f / "request.txt").write_text("CHILD — GitHub issue(s) 700 (bug)\n")
    # A live run started now: a start past the takeover bound is a run rule T ends (ADR-0166).
    event = '{"event": "run_start", "run_id": "%s", "ts": ' + str(time.time()) + '}\n' if live else '{"event": "verdict", "verdict": "failed", "reasons": ["x"]}\n'
    (f / "record.jsonl").write_text(event % name if live else event)
    return f


def _dashboard(root, tmp_path, folder):
    """A live process whose argv names the folder, as squad.dashboard_server does, and its pid."""
    script = tmp_path / "bin" / DASH_NAME
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(DASH_BODY)
    p = subprocess.Popen([sys.executable, str(script), str(folder), "60"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return p


@pytest.mark.process_bound
def test_a_pass_counts_the_loops_dashboards_and_spares_the_live_runs(tmp_path):
    root = _checkout()
    (root / "st" / "auto").mkdir(parents=True)
    live = _run("run-live", root, live=True)
    gone = _run("run-gone", root, live=False)
    (root / "st" / "active-runs.txt").write_text("run-live\n")
    procs = [_dashboard(root, tmp_path, live), _dashboard(root, tmp_path, gone)]
    try:
        p = subprocess.run([sys.executable, "-I", str(JANITOR)], cwd=root, env=_env(root, tmp_path, 1),
                           capture_output=True, text=True, timeout=60)
        out = p.stdout + p.stderr
        assert p.returncode == 0, out
        assert "DASHBOARD-CEILING" in out, f"the pass counted no dashboard over its ceiling: {out}"
        assert "2 dashboards of this loop over the ceiling of 1" in out, out
        # The run the pass cannot see owns no dashboard: nobody reaped it, so the ceiling is the only thing that reaches it.
        assert f"kill pid={procs[1].pid}" in out, f"the orphan dashboard was not named: {out}"
        # A live run's own dashboard is never a victim, and a dry pass signals nothing.
        assert f"kill pid={procs[0].pid}" not in out, f"the live run's dashboard was named as a victim: {out}"
        assert [q.poll() for q in procs] == [None, None], "a dry pass killed a process"
    finally:
        for q in procs:
            q.terminate()
        for q in procs:
            q.wait(timeout=10)
        shutil.rmtree(root, ignore_errors=True)


def test_the_pass_is_handed_no_fresh_executable_and_no_machine_wide_cwd_scan(tmp_path):
    """Issue #261: the pass must not read the machine. macOS assesses a freshly written executable on its first exec, through one
    daemon every xdist worker shares, and a machine-wide lsof scales with every process alive: under -n 4 the two took the call
    past the 2 s line. The stub gh is an executable the system already trusts, and the cwd lister the pass reads is the
    janitor's SG_LSOF seam, scoped to nothing (rule S reads the argv of ps, never a cwd)."""
    e = _env(tmp_path, tmp_path, 1)
    gh = Path(e["PATH"].split(":")[0]) / "gh"
    assert tmp_path.resolve() not in Path(os.path.realpath(gh)).parents, f"the stub gh is a file this test wrote: {os.path.realpath(gh)}"
    assert e.get("SG_LSOF") == TRUE, f"the pass scans the cwd of every process on the machine: SG_LSOF={e.get('SG_LSOF')!r}"
    root = _checkout()
    try:
        assert "pytest-of-" not in str(root), f"a session-end stop of any pytest session matches the checkout's dashboards: {root}"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_session_end_stops_only_the_dashboards_under_its_own_basetemp(tmp_path_factory):
    """Issue #261: one xdist worker ends its session while another still runs the pass above, and the session-end stop
    matched every test dashboard on the machine, so the pass found none. The pattern names our basetemp, so a dashboard
    under another worker's or session's basetemp is not ours to stop."""
    base = tmp_path_factory.getbasetemp().resolve()
    pattern = dashboard_pattern(base)
    py = "/usr/bin/python3 -m squad.dashboard_server"
    assert re.search(pattern, f"{py} {base}/test_x0/runs/run-a --idle 7200")
    for other in (base.parent / "popen-gw99", base.parent.parent / "pytest-0", Path("/tmp/pytest-of-someone/pytest-1")):
        assert not re.search(pattern, f"{py} {other}/test_x0/runs/run-a --idle 7200"), f"our stop matches a dashboard under {other}"
