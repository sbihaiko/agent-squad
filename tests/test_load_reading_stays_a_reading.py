"""Issue #81: the machine load is a reading, never a rule.

A full gate on a busy machine is not a stall: the child is healthy, its CPU moves, and its record stays quiet only
while a long command runs. So no path may kill, pause or hold a run because of a load number -- the hub reads CPU, RAM
and GPU and paints them, and that is all the reading is for.

This is a source guard, not a behavior test: it fails the day someone wires the load into a path that ends, parks or
blocks a run, which is the shape the rule is about.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP = {".git", ".venv", "runs", "worktrees", ".worktrees", "vendor", "node_modules", "__pycache__", "tests", "docs", "bench"}
# The names the machine load reading goes by: the hub's samplers, its thresholds and its served route. `json.loads` and a
# variable called `load` are different readings and are not in this list.
LOAD_NAMES = re.compile(r"sample_load|cpu_pct|ram_pct|gpu_pct|load_payload|load_line|/api/load|LOAD_(WARN|ERR|MIN_SHARE|SAMPLE|HISTORY|STATE)")
# The hub is the reading's one home: it samples inside its own process and serves the number to its own page.
READS_THE_LOAD = {"contrib/squad-goal/scripts/hub.py"}
# Every path that can end a run, park it or block it, by the name the loop knows it by.
CONTROL_PATHS = [
    "contrib/squad-goal/scripts/serial.sh",           # holds a run: the machine-wide locks
    "contrib/squad-goal/scripts/shell-suites.sh",     # holds the machine-wide gate
    "contrib/squad-goal/scripts/fork-gate.sh",
    "contrib/squad-goal/scripts/forkclose.sh",
    "contrib/squad-goal/scripts/watch.sh",
    "contrib/squad-goal/scripts/watchd.sh",
    "contrib/squad-goal/scripts/single.sh",
    "contrib/squad-goal/scripts/autopilot.sh",
    "contrib/squad-goal/scripts/janitor.py",
    "contrib/squad-goal/scripts/autofrontier.sh",
    "contrib/squad-goal/scripts/autobug.sh",
    "squad/monitor.py",          # the stuck-agent watchdog
    "squad/stall.py",            # the stall cap
    "squad/gates.py",            # a stop condition
    "squad/control.py",          # the pause and resume write
    "squad/caps.py",             # the ceiling a run is held under
    "squad/run_caps.py",
    "squad/budget.py",           # the spend cap
    "squad/launch.py",           # where a run starts
    "squad/engine.py",           # the loop itself
    "squad/verdict.py",          # where a run ends
    "squad/workcmd.py",          # the acceptance gate's command runner
]


def _code_files():
    """Every tracked source file of the repository, minus the tests, the suites and the documents."""
    for p in sorted(ROOT.rglob("*")):
        if p.suffix not in (".py", ".sh", ".js") or not p.is_file():
            continue
        rel = p.relative_to(ROOT)
        if rel.parts[0] in SKIP or p.name.startswith(("test-", "test_")):
            continue
        yield rel


def test_the_hub_is_the_only_code_that_reads_the_machine_load():
    readers = {str(rel) for rel in _code_files() if LOAD_NAMES.search((ROOT / rel).read_text(errors="replace"))}
    assert readers == READS_THE_LOAD, (
        "the machine load is read outside the hub, which is where a reading turns into a rule: "
        f"{sorted(readers - READS_THE_LOAD)}")


def test_no_path_that_kills_pauses_or_holds_a_run_reads_the_load():
    offenders = [rel for rel in CONTROL_PATHS
                 if LOAD_NAMES.search((ROOT / rel).read_text(errors="replace"))]
    assert offenders == [], f"these paths decide by a load reading: {offenders}"


def test_the_control_paths_are_still_real_paths():
    # A renamed or removed path would make the list above pass by naming nothing.
    missing = [rel for rel in CONTROL_PATHS if not (ROOT / rel).exists()]
    assert missing == [], f"the run-control paths this guard names have moved: {missing}"
