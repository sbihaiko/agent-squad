"""Issue #70: a counted ceiling on the dashboards the loop started.

Rules B, J and C reap the dashboard of the one run the pass knows about. A run that left active-runs.txt, or a record the pass
cannot read, leaves its dashboard behind, and nothing bounded the count: 41 were alive after the freeze. One pass runs here in a
throwaway checkout, over a live run's dashboard and a dashboard no run the pass can see owns, and the test reads what the pass did.

The pass runs dry on purpose: a dry pass names the ceiling and its victims and kills nothing, so this test never signals a process
of the machine it runs on. The kills themselves are the shell suite's, beside the script.

Host-free: the janitor, its stubbed gh and its temp state live in tmp_path.
"""
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "contrib" / "squad-goal" / "scripts"
JANITOR = SCRIPTS / "janitor.py"
# The dashboard server on the machine is named by its argv: the pass reads it out of ps (`python -m squad.dashboard_server
# <folder>`). A file by that name, started as a process, is what makes this fixture a process and not a string.
DASH_NAME = "squad.dashboard_server"
DASH_BODY = "import sys, time\ntime.sleep(float(sys.argv[2]) if len(sys.argv) > 2 else 60)\n"


def _env(root, tmp_path, dash_max):
    gh = tmp_path / "bin"
    gh.mkdir(exist_ok=True)
    (gh / "gh").write_text("#!/bin/bash\n")
    (gh / "gh").chmod(0o755)
    e = {k: v for k, v in os.environ.items() if not k.startswith(("SG_", "FG_"))}
    e.update({"PATH": str(gh) + ":/usr/bin:/bin", "HOME": str(tmp_path / "home"),
              "SG_STATE": str(root / "st"), "SG_CHECKOUT": str(root / "co"), "SG_REPO": "x/y", "SG_BASE": "main",
              "JANITOR_DRY": "1", "JANITOR_DASHBOARD_MAX": str(dash_max), "JANITOR_NOTIFY": "/bin/true"})
    return e


def _run(name, root, *, live):
    """A run folder of the checkout's runs. A live run carries a fresh record with no verdict; a finished one carries a verdict."""
    f = root / "co" / "runs" / name
    f.mkdir(parents=True, exist_ok=True)
    (f / "request.txt").write_text("CHILD — GitHub issue(s) 700 (bug)\n")
    event = '{"event": "run_start", "run_id": "%s", "ts": 1}\n' if live else '{"event": "verdict", "verdict": "failed", "reasons": ["x"]}\n'
    (f / "record.jsonl").write_text(event % name if live else event)
    return f


def _dashboard(root, tmp_path, folder):
    """A live process whose argv names the folder, as squad.dashboard_server does, and its pid."""
    script = tmp_path / "bin" / DASH_NAME
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(DASH_BODY)
    p = subprocess.Popen([sys.executable, str(script), str(folder), "60"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return p


def test_a_pass_counts_the_loops_dashboards_and_spares_the_live_runs(tmp_path):
    root = tmp_path
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
