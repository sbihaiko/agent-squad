"""Guards for the full suite's shard partition (#2442).

The partition is derived from the tree at run time: `tests/ci_shards.py` weighs every `tests/test_*.py` of the tree and
places each one, heaviest first, in the lightest shard. No branch writes the partition, so two branches that each add a
test file touch no shared file: their branches merge clean, and the merged tree's partition holds both files.

The module names no test path, so it is no ledger a card edits, and `.gitattributes` gives it no merge driver: there is
nothing to merge.
"""

import ast
import re
import subprocess
import sys
from pathlib import Path

from tests import ci_shards
from tests.gitrepo import make_repo, sh, sh_result

ROOT = Path(__file__).resolve().parent.parent
SHARDS_FILE = ROOT / "tests" / "ci_shards.py"
SHARD_COUNT = ci_shards.SHARD_COUNT


def _shards(root: Path) -> list[str]:
    """Every test file the partition of `root` holds, through the one function the CI command prints."""
    return [path for shard in range(1, SHARD_COUNT + 1) for path in ci_shards.shard_paths(shard, root)]


def _partition_in(repo: Path) -> tuple[int, str, list[str]]:
    """Two branches each add one test file and one merges the other: the merge's code, the shard file's text and the
    merged tree's partition."""
    g = lambda *a: sh(repo, "git", "-c", "user.email=t@t", "-c", "user.name=t", *a)  # noqa: E731
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests" / "ci_shards.py").write_text(SHARDS_FILE.read_text())
    (repo / "tests" / "test_a.py").write_text("def test_a():\n    pass\n")
    g("add", "-A")
    g("commit", "-q", "-m", "base")
    g("checkout", "-q", "-b", "one")
    (repo / "tests" / "test_b.py").write_text("def test_b():\n    pass\n")
    g("add", "-A")
    g("commit", "-q", "-m", "one")
    g("checkout", "-q", "main")
    g("checkout", "-q", "-b", "two")
    (repo / "tests" / "test_c.py").write_text("def test_c():\n    pass\n")
    g("add", "-A")
    g("commit", "-q", "-m", "two")
    g("checkout", "-q", "one")
    done = sh_result(repo, "git", "-c", "user.email=t@t", "-c", "user.name=t", "merge", "--no-edit", "two")
    return done.returncode, (repo / "tests" / "ci_shards.py").read_text(), _shards(repo)


def test_two_branches_that_each_add_a_test_file_merge_and_the_partition_holds_both(tmp_path):
    """The expected behavior (#2442): a branch that adds a test file edits no ledger, so a sibling's addition merges clean,
    and the merged tree's partition runs both files once."""
    code, text, listed = _partition_in(make_repo(tmp_path / "merge"))
    assert code == 0, f"the merge of two added test files conflicted: {text}"
    assert "<<<<<<<" not in text, f"the merge left a conflict marker: {text}"
    for added in ("tests/test_b.py", "tests/test_c.py"):
        assert listed.count(added) == 1, f"the merged tree runs {added} {listed.count(added)} times: {listed}"


def test_the_partition_is_derived_from_the_tree_at_run_time(tmp_path):
    """Every test file of the tree is in exactly one shard, with no ledger to keep in step: a file the derivation reads
    from the tree can never be left out or listed twice."""
    listed = _shards(ROOT)
    on_disk = sorted(f"tests/{p.name}" for p in (ROOT / "tests").glob("test_*.py"))
    assert sorted(listed) == on_disk, f"the partition does not hold each test file once: {sorted(listed)} against {on_disk}"


def test_the_partition_names_no_test_path():
    """The partition is derived, never declared: the module's own source holds no `tests/test_*.py` path, so no branch
    writes a row and no two branches touch one line."""
    tree = ast.parse(SHARDS_FILE.read_text())
    named = sorted(node.value for node in ast.walk(tree)
                   if isinstance(node, ast.Constant) and isinstance(node.value, str)
                   and re.fullmatch(r"tests/test_.*\.py", node.value))
    assert named == [], f"the partition module declares a test path, so cards edit it again: {named}"


def test_the_command_prints_the_shard_the_module_answers():
    """The CI job runs the module as a command, so the command prints the shard the module answers, one line."""
    r = subprocess.run([sys.executable, "tests/ci_shards.py", "2"], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.split() == list(ci_shards.shard_paths(2)), r.stdout


def test_a_shard_answer_is_the_same_twice():
    """The derivation is deterministic: two answers for one tree name the same files, so two CI jobs of one shard run one set."""
    for shard in range(1, SHARD_COUNT + 1):
        assert ci_shards.shard_paths(shard) == ci_shards.shard_paths(shard)


def test_the_partition_spreads_the_weight_evenly():
    """The greedy placement leaves no shard heavier than the lightest plus the heaviest single file: a wall clock is a
    sibling job's, so one shard carrying the heavy suites sets the tier (ADR-0163)."""
    shards = {k: set(ci_shards.shard_paths(k)) for k in range(1, SHARD_COUNT + 1)}
    weights = {path: ci_shards.weight(ROOT / path) for paths in shards.values() for path in paths}
    totals = [sum(weights[path] for path in paths) for paths in shards.values()]
    heaviest = max(weights.values())
    assert max(totals) - min(totals) <= heaviest, f"one shard carries {totals}, uneven by more than one file's weight"
    assert all(paths for paths in shards.values()), "a shard is empty, so its job runs no proof"
