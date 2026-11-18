"""The daily shell-suite pass (#2307): every `test-*.sh` beside the scripts, on the dev tip, once a day, outside the landing
path.

A landing gate runs only the shell suites its delta selects (`base-battery.sh`, `sg_shell_suites`), so a suite no ticket
touches can stay red on dev for hours; two did, and each surfaced only when an unrelated ticket's delta selected it. This
pass runs all of them.

These cases drive `daysuites.sh` against a stubbed suite list: a throwaway git tree stands in for the dev tip, its
`contrib/squad-goal/scripts/test-*.sh` are the suites, and the loop's filer (`autohunt.sh`) is a stub that logs its call, so
no real suite, model or network is touched. The pass's guard beside the script is
`contrib/squad-goal/scripts/test-daysuites.sh`; these cases prove the same seam from pytest.
"""
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
DAYSUTES = SCRIPTS / "daysuites.sh"
G = ["git", "-c", "user.email=a@b", "-c", "user.name=t"]
pytestmark = pytest.mark.heavy   # each case runs a real pass with its lock and slot machinery


def _clean(extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("SG_", "SERIAL_"))}
    env.update(extra)
    return env


def _write(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(0o755)
    return path


class World:
    """One fixture: the dev-tip tree with its stubbed suites, the pass and its sibling stubs, and one state directory."""

    def __init__(self, tmp_path, suites):
        self.tmp = tmp_path
        self.tree = tmp_path / "tree"
        self.suites = self.tree / "contrib" / "squad-goal" / "scripts"
        self.suites.mkdir(parents=True)
        subprocess.run(G + ["init", "-q", "-b", "dev", str(self.tree)], check=True)
        self.scripts = tmp_path / "scripts"
        self.scripts.mkdir()
        for name in ("serial.sh", "caps.sh"):
            (self.scripts / name).write_bytes((SCRIPTS / name).read_bytes())
        (self.scripts / "daysuites.sh").write_bytes(DAYSUTES.read_bytes())
        # The loop's one filer: the pass must reuse it, never grow a second.
        self.filed = tmp_path / "filed.log"
        _write(self.scripts / "autohunt.sh",
               '#!/bin/bash\n{ echo "ARGV: $*"; echo "BODY:"; cat "$3" 2>/dev/null; echo; } >> "$FILED"\n')
        self.st = tmp_path / "st"
        self.st.mkdir()
        self.runlog = tmp_path / "ran.log"
        self.slots = tmp_path / "slots.log"
        self.set_suites(suites)

    def set_suites(self, suites):
        """The stubbed suite list, one `name: shell body` per entry, committed so the tree has one dev sha."""
        for old in self.suites.glob("test-*.sh"):
            old.unlink()
        for name, body in suites.items():
            _write(self.suites / name, "#!/bin/bash\n" + body)
        subprocess.run(G + ["-C", str(self.tree), "add", "-A"], check=True)
        subprocess.run(G + ["-C", str(self.tree), "commit", "-q", "--allow-empty", "-m", "one"], check=True)
        self.sha = subprocess.run(G + ["-C", str(self.tree), "rev-parse", "HEAD"], check=True,
                                  capture_output=True, text=True).stdout.strip()

    def run(self, **extra):
        env = {
            "SG_STATE": str(self.st), "SG_DAYSUITES_TREE": str(self.tree), "SG_REPO": "o/r",
            "SG_OWNER": "o", "SG_PROJECT": "4", "SG_TODO": "Todo", "SG_BASE": "dev",
            "SG_SQUAD": str(ROOT), "SQUAD_CAPS_DIR": str(self.tmp / "caps"),
            "FILED": str(self.filed), "RUNLOG": str(self.runlog), "SLOTS_SEEN": str(self.slots),
            "SERIAL_POLL_SECONDS": "0.05", "PATH": os.environ["PATH"],
        }
        env.update({k: str(v) for k, v in extra.items()})
        return subprocess.run(["bash", str(self.scripts / "daysuites.sh")], capture_output=True, text=True,
                              env=_clean(env), timeout=120)

    def clear_day(self):
        for marker in self.st.glob("auto/daysuites-*"):
            if marker.name != "daysuites-red":
                marker.unlink()

    def ran(self):
        return self.runlog.read_text().splitlines() if self.runlog.exists() else []

    def filings(self):
        if not self.filed.exists():
            return []
        return ["ARGV:" + block for block in self.filed.read_text().split("ARGV:")[1:]]


GREEN = 'echo "$0" >> "$RUNLOG"\nexit 0\n'
GREEN_SLOTS = 'echo "$0" >> "$RUNLOG"\n/bin/ls "$SQUAD_CAPS_DIR/gates" 2>/dev/null >> "$SLOTS_SEEN"\nexit 0\n'
RED = 'echo "$0" >> "$RUNLOG"\necho "FAIL the widget did not settle"\necho "second line"\nexit 1\n'


def test_every_suite_runs_on_the_dev_tip_once_a_day(tmp_path):
    """Criterion 1: one pass runs every stubbed suite, in the dev-tip tree, and a later pass the same day runs none."""
    w = World(tmp_path, {"test-alpha.sh": GREEN, "test-beta.sh": GREEN})
    first = w.run()
    assert first.returncode == 0, first.stdout + first.stderr
    assert [Path(p).name for p in w.ran()] == ["test-alpha.sh", "test-beta.sh"]
    assert all(str(w.suites) in p for p in w.ran()), "a suite ran outside the dev-tip tree"
    second = w.run()
    assert second.returncode == 0, second.stdout + second.stderr
    assert [Path(p).name for p in w.ran()] == ["test-alpha.sh", "test-beta.sh"], "a second pass ran the suites again"


def test_the_pass_holds_a_gate_slot_while_a_suite_runs(tmp_path):
    """Criterion 2a: a suite runs while the pass holds a caps.gates slot, and the pass releases it at the end."""
    w = World(tmp_path, {"test-alpha.sh": GREEN_SLOTS})
    done = w.run()
    assert done.returncode == 0, done.stdout + done.stderr
    assert w.slots.read_text().split(), "no gate slot was held while the suite ran"
    leases = list((tmp_path / "caps" / "gates").glob("*")) if (tmp_path / "caps" / "gates").exists() else []
    assert leases == [], f"the pass left a gate slot held: {leases}"


def test_a_landing_gate_blocks_the_pass_and_no_suite_runs(tmp_path):
    """Criterion 2b: a live holder of the gate lock (a landing gate) keeps the pass from running a suite beside it; the
    pass ends held and writes no day marker, so a later cycle tries again."""
    w = World(tmp_path, {"test-alpha.sh": GREEN})
    holder = subprocess.Popen(["sleep", "60"])
    try:
        lock = w.st / "locks" / "gate"
        lock.mkdir(parents=True)
        (lock / "pid").write_text(f"{holder.pid}\n")
        held = w.run(SG_DAYSUITES_LOCK_WAIT="1")
        assert held.returncode == 75, held.stdout + held.stderr
        assert w.ran() == [], "a suite ran beside a live landing gate"
        assert "held" in held.stdout + held.stderr, "the pass did not say it was held"
        assert not list(w.st.glob("auto/daysuites-2*")), "a held pass spent the day's marker"
    finally:
        holder.kill()
        holder.wait()
    again = w.run()
    assert again.returncode == 0, again.stdout + again.stderr
    assert [Path(p).name for p in w.ran()] == ["test-alpha.sh"]


def test_a_red_suite_files_one_bug_with_its_name_first_fail_line_and_the_dev_sha(tmp_path):
    """Criterion 3: one bug, through the loop's one filer, naming the suite, its first FAIL line and the dev sha."""
    w = World(tmp_path, {"test-alpha.sh": GREEN, "test-red.sh": RED})
    done = w.run()
    assert done.returncode == 1, done.stdout + done.stderr
    filings = w.filings()
    assert len(filings) == 1, f"expected one filing, found {len(filings)}"
    block = filings[0]
    assert "--file" in block, "the pass did not file through the one filer's --file door"
    assert "shell suite test-red.sh" in block, "the title does not name the suite"
    assert "test-red.sh" in block
    assert "FAIL the widget did not settle" in block, "the body does not name the first FAIL line"
    assert "second line" not in block, "the body names more than the first FAIL line"
    assert w.sha in block, "the body does not name the dev sha"


def test_a_suite_red_on_two_passes_in_a_row_files_no_second_bug(tmp_path):
    """Criterion 4: the second consecutive red pass files nothing; a green pass in between re-arms the filing."""
    w = World(tmp_path, {"test-red.sh": RED})
    assert w.run().returncode == 1
    assert len(w.filings()) == 1
    w.clear_day()
    assert w.run().returncode == 1
    assert len(w.filings()) == 1, "a suite red on two passes in a row filed a second bug"
    w.set_suites({"test-red.sh": GREEN})
    w.clear_day()
    assert w.run().returncode == 0
    w.set_suites({"test-red.sh": RED})
    w.clear_day()
    assert w.run().returncode == 1
    assert len(w.filings()) == 2, "a suite green in between did not file again on a later red pass"
