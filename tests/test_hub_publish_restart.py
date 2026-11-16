"""A publish of a file the hub imports or pins names the hub restart, and the kanban's stale label names the read error (#2027).

The hub loads its modules once at start, so a publish that changes board-definition.json or board_columns.py leaves a
running hub with the old pin and the new file: every board read then fails on the hash. live-guard.sh names the restart
for the drivers only; this file proves it names the hub too, once per publish, and that a publish of other files does not.
"""
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LIVE_GUARD = REPO / "contrib/squad-goal/scripts/live-guard.sh"
HUB_PY = REPO / "contrib/squad-goal/scripts/hub.py"
GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]


def _git(repo, *args):
    return subprocess.run([*GIT, "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def _commit(repo, message):
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _build(tmp_path, hub_changed):
    """A checkout at two commits and a live skill that holds the newer one. The hub's recorded build is the older commit."""
    co = tmp_path / "co"
    skill = co / "contrib/squad-goal"
    (skill / "scripts").mkdir(parents=True)
    (skill / "SKILL.md").write_text("skill v1\n")
    (skill / "scripts/hub.py").write_text("hub v1\n")
    (skill / "scripts/board_columns.py").write_text("cols v1\n")
    (skill / "scripts/board-definition.json").write_text('{"version": 1}\n')
    _git(co, "init", "-q", "-b", "stg")
    old = _commit(co, "old")

    (skill / "SKILL.md").write_text("skill v2\n")
    if hub_changed:
        (skill / "scripts/board_columns.py").write_text("cols v2\n")
        (skill / "scripts/board-definition.json").write_text('{"version": 2}\n')
    new = _commit(co, "new")

    live = tmp_path / "live"
    live.mkdir()
    (live / "SKILL.md").write_text("skill v2\n")
    (live / "PUBLISHED").write_text(f"{new} 2026-11-16T00:00:00Z\n")

    state = tmp_path / "st"
    state.mkdir()
    (state / "driver.sha").write_text(new + "\n")
    (state / "hub.sha").write_text(old + "\n")

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "live-guard.sh").write_text(LIVE_GUARD.read_text())
    (scripts / "env.sh").write_text(
        f"export SG_CHECKOUT={co} SG_LIVE_DIR={live} SG_STATE={state}\n")
    return scripts / "live-guard.sh"


def _run_pass(tmp_path, script):
    proc = subprocess.run(["bash", str(script)], capture_output=True, text=True, timeout=60,
                          env={"PATH": os.environ["PATH"], "HOME": str(tmp_path)})
    assert proc.returncode == 0, proc.stderr
    return proc.stdout + proc.stderr


def test_a_publish_of_a_hub_file_names_the_hub_restart(tmp_path):
    out = _run_pass(tmp_path, _build(tmp_path, hub_changed=True))
    line = next((ln for ln in out.splitlines() if "hubctl.sh --restart" in ln), "")
    assert line, f"no hub restart named: {out!r}"
    assert "board-definition.json" in line and "board_columns.py" in line


def test_the_hub_restart_is_named_once_per_publish(tmp_path):
    script = _build(tmp_path, hub_changed=True)
    first = _run_pass(tmp_path, script)
    second = _run_pass(tmp_path, script)
    assert first.count("hubctl.sh --restart") == 1
    assert "hubctl.sh --restart" not in second


def test_a_publish_of_no_hub_file_names_no_hub_restart(tmp_path):
    out = _run_pass(tmp_path, _build(tmp_path, hub_changed=False))
    assert "hubctl" not in out and "restart" not in out


def test_the_kanban_stale_label_names_the_board_read_error():
    src = HUB_PY.read_text()
    assert 'readStale?" (stale: "+lastBoardErr+")":""' in src
    assert "lastBoardErr=b.err||\"\"" in src
