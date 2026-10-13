"""Action nodes: deterministic code with declared side effects. No model, no spend.

Effects: worktree_create, worktree_revert, commit, merge_land. Each one returns an ActionResult. The engine
records the workspace state in the outcome event, so a resume can tell what a person changed.
"""
import re
from dataclasses import dataclass, field
from pathlib import Path

from squad import git


@dataclass
class ActionResult:
    ok: bool
    reasons: list = field(default_factory=list)
    head: str | None = None
    extra: dict = field(default_factory=dict)


def branch_name(run_id, logical, item):
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", logical).strip("-")
    return f"squad/{run_id}/{slug}" + (f"-{item}" if item is not None else "")


def worktree_create(target, path, branch, timeout=None):
    path = Path(path)
    if path.exists():
        return ActionResult(False, ["workspace_exists"])
    path.parent.mkdir(parents=True, exist_ok=True)
    r = git.run(["worktree", "add", "-b", branch, str(path), "HEAD"], target, timeout=timeout)
    if r.returncode != 0:
        return ActionResult(False, ["worktree_create_failed"], extra={"stderr": r.stderr.strip()})
    return ActionResult(True, [], git.head(path))


def worktree_revert(path, head, timeout=None):
    """Discard the work in a worktree that this run made, back to the recorded HEAD."""
    if not Path(path).exists() or head is None:
        return ActionResult(False, ["workspace_missing"])
    git.run(["reset", "--hard", head], path, timeout=timeout)
    git.run(["clean", "-fd"], path, timeout=timeout)
    return ActionResult(True, [], git.head(path))


def commit(path, message, timeout=None, exclude=None):
    """Stage and commit every change under `path`. `exclude` is one path under it that is never staged: the run's own folder."""
    if not git.dirty(path, exclude):
        return ActionResult(True, ["nothing_to_commit"], git.head(path))
    git.run(["add", "-A", *(["--", ".", f":(exclude){exclude}"] if exclude else [])], path, timeout=timeout)
    r = git.run(["-c", "user.name=agent-squad", "-c", "user.email=squad@localhost", "commit", "-m", message], path, timeout=timeout)
    if r.returncode != 0:
        return ActionResult(False, ["commit_failed"], extra={"stderr": r.stderr.strip()})
    return ActionResult(True, [], git.head(path))


def merge_land(target, worktree, branch, message, timeout=None):
    """Commit pending work in the worktree, then merge its branch into the target's current branch."""
    c = commit(worktree, message, timeout)
    if not c.ok:
        return c
    r = git.run(["-c", "user.name=agent-squad", "-c", "user.email=squad@localhost", "merge", "--no-ff", "-m", message, branch], target, timeout=timeout)
    if r.returncode != 0:
        git.run(["merge", "--abort"], target)
        return ActionResult(False, ["merge_conflict"], git.head(target), {"stderr": (r.stdout + r.stderr).strip()[-400:]})
    return ActionResult(True, [], git.head(target))
