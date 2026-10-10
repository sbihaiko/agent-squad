"""Thin git helpers. The engine never repairs a workspace; it observes."""
import hashlib
import shutil
import subprocess


def run(args, cwd, check=False, timeout=None):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=check, timeout=timeout)


def on_path():
    return shutil.which("git") is not None


def available(target):
    return on_path() and run(["rev-parse", "--is-inside-work-tree"], target).returncode == 0


def head(cwd):
    r = run(["rev-parse", "HEAD"], cwd)
    return r.stdout.strip() if r.returncode == 0 else None


def state(cwd):
    """A digest of the working state: HEAD, status, and the diff against HEAD. Equal digests mean no change."""
    parts = [head(cwd) or "", run(["status", "--porcelain=v1", "-uall"], cwd).stdout, run(["diff", "HEAD"], cwd).stdout]
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()


def dirty(cwd):
    return [ln for ln in run(["status", "--porcelain=v1", "-uall"], cwd).stdout.splitlines() if ln.strip()]


def unmerged(cwd):
    return run(["diff", "--name-only", "--diff-filter=U"], cwd).stdout.split()


def tracked_changes(cwd):
    """Modified or staged tracked files. Untracked files do not count as a dirty base."""
    return [ln for ln in run(["status", "--porcelain=v1", "-uno"], cwd).stdout.splitlines() if ln.strip()]
