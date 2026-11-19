"""The live-copy mirror check: a suite's `# mirrors:` line must carry the modules its fixture loads (#2357).

A shell suite under contrib/squad-goal/scripts copies a small tree into a temporary live-copy shape, and its first
`# mirrors:` line names that tree. The check reads the suite's own fixture commands for `python3 <name>.py` and
`import <name>`, keeps the names the repository carries beside the suite, and fails when the list and the loads
disagree. The fixture suites here are whole trees in a temp dir, so the check runs against them the way the gate runs
it against the real ones.
"""
import subprocess
import sys
from pathlib import Path

CHECKER = Path(__file__).resolve().parent.parent / "contrib" / "squad-goal" / "scripts" / "mirrorcheck.py"


def run_checker(*suites: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CHECKER), *map(str, suites)],
        capture_output=True,
        text=True,
    )


def make_copy(tmp_path: Path) -> Path:
    """A tree beside the suite: the modules the repository carries, one of them loaded by the fixture."""
    d = tmp_path / "scripts"
    d.mkdir()
    (d / "a.py").write_text("x = 1\n")
    (d / "b.py").write_text("y = 2\n")
    return d


def write_suite(d: Path, name: str, body: str) -> Path:
    p = d / name
    p.write_text("#!/bin/bash\n" + body)
    return p


def test_a_load_the_mirror_list_does_not_carry_fails(tmp_path):
    d = make_copy(tmp_path)
    suite = write_suite(d, "test-miss.sh", "# mirrors: a.py\npython3 b.py\n")
    done = run_checker(suite)
    text = done.stdout + done.stderr
    assert done.returncode != 0, text
    assert "test-miss.sh" in text, text
    assert "b.py" in text, text


def test_a_mirror_name_the_repository_lacks_fails(tmp_path):
    d = make_copy(tmp_path)
    suite = write_suite(d, "test-ghost.sh", "# mirrors: a.py ghost.py\nimport a\n")
    done = run_checker(suite)
    text = done.stdout + done.stderr
    assert done.returncode != 0, text
    assert "ghost.py" in text, text


def test_a_list_and_its_loads_agree_passes_with_one_line(tmp_path):
    d = make_copy(tmp_path)
    suite = write_suite(d, "test-ok.sh", "# mirrors: a.py b.py\npython3 b.py\nimport a\n")
    done = run_checker(suite)
    text = done.stdout + done.stderr
    assert done.returncode == 0, text
    assert len(done.stdout.strip().splitlines()) == 1, done.stdout
