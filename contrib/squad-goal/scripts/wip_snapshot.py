#!/usr/bin/env python3
"""Snapshot a run's uncommitted work before the run is torn down (#1662).

The trees of one run are its own tree (a worktree run's `worktrees/run/<key>`, or the run folder itself when that is a git
tree), and every git tree under `ws/` and `worktrees/` in the run folder. Each tree's tracked changes and untracked files
(`.gitignore` honoured) go into one commit, reached only by `refs/wip/<run-id>/<label>`. The staging uses a temporary
index file outside the repository, so the real index, the branch and the working tree do not move.

usage: python3 -I wip_snapshot.py <run-folder> [<run-id>] [--dry]
One line per tree on stdout: `WIP saved <label> <ref>` or `WIP clean <label>`, or `WIP failed <label> <reason>`.
A dry run writes nothing and prints nothing. Exit 1 when a tree could not be saved.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

IDENTITY = {"GIT_AUTHOR_NAME": "agent-squad wip", "GIT_AUTHOR_EMAIL": "wip@agent-squad.invalid",
            "GIT_COMMITTER_NAME": "agent-squad wip", "GIT_COMMITTER_EMAIL": "wip@agent-squad.invalid"}


def git(path: pathlib.Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, env=env, check=False)


def is_own_toplevel(path: pathlib.Path) -> bool:
    """True when the directory is the top of its own git tree: a folder inside the checkout is not a tree of the run."""
    r = git(path, "rev-parse", "--show-toplevel")
    return r.returncode == 0 and pathlib.Path(r.stdout.strip()).resolve() == path.resolve()


def trees(folder: pathlib.Path) -> list[tuple[str, pathlib.Path]]:
    """(label, path) for each git tree of the run. The own tree is labelled `own`; the rest by their path in the run folder."""
    out: list[tuple[str, pathlib.Path]] = []
    own = folder
    # A worktree run lives at worktrees/run/<key>/runs/<run>: its tree is the <key> directory above the run folder.
    if len(folder.parents) > 3 and folder.parent.name == "runs" and folder.parents[2].name == "run" and folder.parents[3].name == "worktrees":
        own = folder.parents[1]
    if own.is_dir() and is_own_toplevel(own): out.append(("own", own))
    for kind in ("ws", "worktrees"):
        base = folder / kind
        if not base.is_dir(): continue
        for p in sorted(base.iterdir()):
            if p.is_dir() and is_own_toplevel(p): out.append((f"{kind}/{p.name}", p))
    return out


def snapshot_tree(label: str, path: pathlib.Path, run_id: str) -> tuple[str, bool]:
    """One line for one tree, and whether it was saved (or was clean). The index is a temporary file outside the tree."""
    head_r = git(path, "rev-parse", "--verify", "-q", "HEAD")
    head = head_r.stdout.strip() if head_r.returncode == 0 else ""
    scratch = tempfile.mkdtemp(prefix="wip-index-")
    env = {**os.environ, **IDENTITY, "GIT_INDEX_FILE": os.path.join(scratch, "index")}
    try:
        steps = []
        if head: steps.append(["read-tree", head])
        steps += [["add", "-A", "--", "."], ["write-tree"]]
        tree_sha = ""
        for args in steps:
            r = git(path, *args, env=env)
            if r.returncode != 0: return f"WIP failed {label} {(r.stderr.strip().splitlines() or ['git ' + args[0] + ' failed'])[0]}", False
            if args[0] == "write-tree": tree_sha = r.stdout.strip()
        base_tree = git(path, "rev-parse", f"{head}^{{tree}}").stdout.strip() if head else ""
        if tree_sha == base_tree: return f"WIP clean {label}", True
        ca = ["commit-tree", tree_sha, "-m", f"WIP {run_id} {label}"]
        if head: ca[1:1] = ["-p", head]
        r = git(path, *ca, env=env)
        if r.returncode != 0: return f"WIP failed {label} commit-tree refused", False
        ref = f"refs/wip/{run_id}/{label}"
        r = git(path, "update-ref", ref, r.stdout.strip())
        if r.returncode != 0: return f"WIP failed {label} update-ref refused", False
        return f"WIP saved {label} {ref}", True
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def snapshot_run(folder: pathlib.Path, run_id: str, dry: bool = False) -> tuple[list[str], bool]:
    """Save every tree of the run. Returns the lines and whether every tree was saved. A dry run touches nothing."""
    if dry: return [], True
    lines, ok = [], True
    for label, path in trees(folder):
        line, saved = snapshot_tree(label, path, run_id)
        lines.append(line); ok = ok and saved
    return lines, ok


def main(argv: list[str]) -> int:
    dry = "--dry" in argv
    args = [a for a in argv if a != "--dry"]
    if not args: print("usage: wip_snapshot.py <run-folder> [<run-id>] [--dry]", file=sys.stderr); return 2
    folder = pathlib.Path(args[0]).resolve()
    run_id = args[1] if len(args) > 1 else folder.name
    lines, ok = snapshot_run(folder, run_id, dry=dry)
    for line in lines: print(line, flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
