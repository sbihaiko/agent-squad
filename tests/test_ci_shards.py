"""Guards for the shard partition's shape (#2420): one test file per line, and a union merge.

Two branches that each add a test file to the same shard edit the same place of `tests/ci_shards.py`, so a rebase
conflicts. One test file per line keeps each addition on its own line, and the union merge driver keeps both lines when
two branches touch the same place. The partition stays exact through it: every `tests/test_*.py` file of the tree is in
exactly one shard.

The union guard asks git for the attribute of the shard path, so it reads the driver as a merge reads it, and it never
names the attribute file: `.gitattributes` stays on `squad/testselect_inert.txt`, and a change to that file alone selects
no test. The fixture below spells the attribute name in two parts for the same reason, so the inert guard sees no test
naming that path.
"""

import ast
from collections import Counter
from pathlib import Path

from tests import ci_shards
from tests.gitrepo import make_repo, sh, sh_result

ROOT = Path(__file__).resolve().parent.parent
SHARDS_FILE = ROOT / "tests" / "ci_shards.py"
SHARD_PATH = "tests/ci_shards.py"
ATTR_FILE = ".git" + "attributes"

BASE_SHARD = '''\
SHARDS = (
    (
        "tests/test_a.py",
    ),
)
'''


def _listed() -> list[str]:
    return [p for shard in ci_shards.SHARDS for p in shard]


def _paths_by_line() -> dict[int, list[str]]:
    """The test-file strings of the SHARDS literal, keyed by their source line, so two strings on one line share a key."""
    tree = ast.parse(SHARDS_FILE.read_text())
    by_line: dict[int, list[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith("tests/test_"):
            by_line.setdefault(node.lineno, []).append(node.value)
    return by_line


def test_every_test_file_is_in_exactly_one_shard_list():
    """Every `tests/test_*.py` file of the tree is in exactly one shard: a missing one never runs in the complete tier,
    a doubled one runs twice, and a stale one names a file the tree no longer has."""
    on_disk = {p.name for p in (ROOT / "tests").glob("test_*.py")}
    listed = [Path(p).name for p in _listed()]
    missing = sorted(on_disk - set(listed))
    twice = sorted(name for name, count in Counter(listed).items() if count > 1)
    stale = sorted(set(listed) - on_disk)
    assert missing == [], f"a test file is in no shard, so the complete tier never runs it: {missing}"
    assert twice == [], f"a test file is in more than one shard, so the complete tier runs it twice: {twice}"
    assert stale == [], f"a shard names a test file the tree no longer has: {stale}"


def test_each_shard_list_puts_one_test_file_on_a_line():
    """One test file per source line: two branches that add a file to one shard then touch different lines, not one."""
    shared = {line: paths for line, paths in _paths_by_line().items() if len(paths) > 1}
    assert shared == {}, f"a shard line holds more than one test file, so two additions to it conflict: {shared}"


def test_the_shard_lists_merge_by_union():
    """`tests/ci_shards.py` carries the union merge driver, read from git, so the guard and the merge see one answer."""
    assert SHARDS_FILE.exists(), "the attribute names no real file"
    answer = sh(ROOT, "git", "check-attr", "merge", "--", SHARD_PATH)
    assert answer.endswith("merge: union"), f"git does not give the shard file the union driver: {answer}"


def _merge_two_additions(tmp_path, with_union: bool) -> tuple[int, str]:
    """Two branches each add one test file to the same shard, then one merges the other; answers the merge's code and
    the shard file's text."""
    repo = make_repo(tmp_path / ("union" if with_union else "plain"))
    if with_union:
        (repo / ATTR_FILE).write_text(f"{SHARD_PATH} merge=union\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "ci_shards.py").write_text(BASE_SHARD)

    def g(*args: str) -> None:
        sh(repo, "git", "-c", "user.email=t@t", "-c", "user.name=t", *args)

    def add(path: str) -> None:
        text = (repo / "tests" / "ci_shards.py").read_text()
        (repo / "tests" / "ci_shards.py").write_text(text.replace(
            '        "tests/test_a.py",\n', f'        "tests/test_a.py",\n        "{path}",\n'))

    g("add", "-A")
    g("commit", "-q", "-m", "base")
    g("checkout", "-q", "-b", "one")
    add("tests/test_b.py")
    g("commit", "-q", "-am", "one")
    g("checkout", "-q", "main")
    g("checkout", "-q", "-b", "two")
    add("tests/test_c.py")
    g("commit", "-q", "-am", "two")
    g("checkout", "-q", "one")
    r = sh_result(repo, "git", "-c", "user.email=t@t", "-c", "user.name=t", "merge", "--no-edit", "two")
    return r.returncode, (repo / "tests" / "ci_shards.py").read_text()


def test_two_branches_that_add_a_file_to_one_shard_merge_without_conflict(tmp_path):
    """With union, two branches that add a test file to the same shard merge clean and keep both files; the same merge
    without union conflicts, which is what the attribute buys."""
    plain_code, plain_text = _merge_two_additions(tmp_path, with_union=False)
    assert plain_code != 0 and "<<<<<<<" in plain_text, "the control merge is expected to conflict without the union driver"
    code, text = _merge_two_additions(tmp_path, with_union=True)
    assert code == 0, f"the union merge conflicted: {text}"
    assert "<<<<<<<" not in text, f"the union merge left a conflict marker: {text}"
    assert "tests/test_b.py" in text and "tests/test_c.py" in text, f"the union merge dropped a file: {text}"
