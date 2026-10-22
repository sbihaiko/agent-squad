"""Thin git helpers. The engine never repairs a workspace; it observes."""
import hashlib
import shutil
import subprocess
from pathlib import Path


def run(args, cwd, check=False, timeout=None):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=check, timeout=timeout)


def on_path():
    return shutil.which("git") is not None


def available(target, timeout=None):
    return on_path() and run(["rev-parse", "--is-inside-work-tree"], target, timeout=timeout).returncode == 0


def head(cwd):
    r = run(["rev-parse", "HEAD"], cwd)
    return r.stdout.strip() if r.returncode == 0 else None


def merge_base(a, b, cwd):
    """The commit two revisions share, or None when they share none. Read for a `{base}` that never moves: a merge base
    is the point a branch was cut from, so a sibling's later merge leaves it as it was."""
    r = run(["merge-base", a, b], cwd)
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def state(cwd):
    """A digest of the working state: HEAD, status, and the diff against HEAD. Equal digests mean no change."""
    parts = [head(cwd) or "", run(["status", "--porcelain=v1", "-uall"], cwd).stdout, run(["diff", "HEAD"], cwd).stdout]
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()


def dirty(cwd, exclude=None, timeout=None):
    """Changed paths, tracked or untracked. `exclude` is one path under `cwd` that never counts: the run's own folder."""
    spec = ["--", ".", f":(exclude){exclude}"] if exclude else []
    return [ln for ln in run(["status", "--porcelain=v1", "-uall", *spec], cwd, timeout=timeout).stdout.splitlines() if ln.strip()]


def main_worktree(cwd, timeout=None):
    """The directory of the repository's main checkout: where `.git` is a directory, never a linked worktree. None outside git."""
    r = run(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd, timeout=timeout)
    return Path(r.stdout.strip()).resolve().parent if r.returncode == 0 and r.stdout.strip() else None


def toplevel(cwd, timeout=None):
    r = run(["rev-parse", "--show-toplevel"], cwd, timeout=timeout)
    return Path(r.stdout.strip()).resolve() if r.returncode == 0 and r.stdout.strip() else None


def owned_worktree(path, timeout=None):
    """True when `path` is the top of a linked worktree whose main checkout is elsewhere: the only kind of tree a run owns.
    The owner's checkout and a subfolder of a repository are not."""
    return main_worktree(path, timeout) != path and toplevel(path, timeout) == path


def branch(cwd, timeout=None):
    """The branch `cwd` stands on, or None (no git, a detached HEAD)."""
    r = run(["symbolic-ref", "--short", "-q", "HEAD"], cwd, timeout=timeout)
    return r.stdout.strip() or None if r.returncode == 0 else None


def unmerged(cwd):
    return run(["diff", "--name-only", "--diff-filter=U"], cwd).stdout.split()


def tracked_changes(cwd):
    """Modified or staged tracked files. Untracked files do not count as a dirty base."""
    return [ln for ln in run(["status", "--porcelain=v1", "-uno"], cwd).stdout.splitlines() if ln.strip()]
