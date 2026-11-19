"""The full suite's CI shards: which test files each shard job runs, in both modes.

`ci.yml` runs the full suite in shard jobs by test path, never in matrix legs (ADR-0163): `full-xdist-<k>` runs
`python3 -m pytest -n auto $(python3 tests/ci_shards.py <k>)`, and `full-serial-<k>` runs the same files with
`-p no:xdist`. The partition is derived from the tree at run time, so no branch writes it: a branch that adds a test
file edits no ledger, and two branches that each add one never touch a shared line, so their branches merge clean and
the merged tree's partition holds both files.

Each test file carries a weight: one per test function and ten per `heavy` mark, because a heavy call spends at least a
second and often a subprocess. The files are placed heaviest first, each in the shard that carries the least weight so
far, ties by the lower shard number. The answer is deterministic: two jobs of one shard, at one tree, run one set. The
placement keeps the heaviest shard within one file's weight of the lightest, and the wave suites, one file each, spread
across the shards because a sibling job's check is the wall clock of the slowest shard.

usage: python3 tests/ci_shards.py <shard> [root]   print the shard's test files, one line (shard 1 to SHARD_COUNT);
                                                  `root` is the tree to read, its own repository by default
"""
import functools
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHARD_COUNT = 6
FUNCTION = re.compile(r"^def test_", re.MULTILINE)
HEAVY = re.compile(r"pytest\.mark\.heavy")
FUNCTION_WEIGHT = 1
HEAVY_WEIGHT = 10
NO_SHARD = 1   # a test file the tree lists but this module cannot read still runs somewhere


def weight(path: Path) -> int:
    """The declared weight of one test file: one per test function and ten per `heavy` mark."""
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return NO_SHARD
    return FUNCTION_WEIGHT * len(FUNCTION.findall(text)) + HEAVY_WEIGHT * len(HEAVY.findall(text))


def test_files(root: Path) -> list[str]:
    """Every `tests/test_*.py` of the tree, repo-relative, sorted."""
    return sorted(f"tests/{path.name}" for path in (root / "tests").glob("test_*.py"))


@functools.cache
def partition(root: str) -> tuple[tuple[str, ...], ...]:
    """The shards of one tree: each file, heaviest first, in the shard that carries the least weight so far."""
    tree = Path(root)
    weights = {path: weight(tree / path) for path in test_files(tree)}
    shards: list[list[str]] = [[] for _ in range(SHARD_COUNT)]
    totals = [0] * SHARD_COUNT
    for path in sorted(weights, key=lambda p: (-weights[p], p)):
        k = min(range(SHARD_COUNT), key=lambda i: (totals[i], i))
        shards[k].append(path)
        totals[k] += weights[path]
    return tuple(tuple(shard) for shard in shards)


def shard_paths(shard: int, root: Path = ROOT) -> tuple[str, ...]:
    """The test files of <shard>, 1-based; a number outside 1..SHARD_COUNT is a defect, never an empty answer."""
    if not 1 <= shard <= SHARD_COUNT:
        raise SystemExit(f"ci_shards: shard {shard} is outside 1..{SHARD_COUNT}")
    return partition(str(Path(root).resolve()))[shard - 1]


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3) or not argv[1].isdigit():
        raise SystemExit("usage: python3 tests/ci_shards.py <shard> [root]")
    root = Path(argv[2]) if len(argv) == 3 else ROOT
    print(" ".join(shard_paths(int(argv[1]), root)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
