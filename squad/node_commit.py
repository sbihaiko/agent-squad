"""The engine commits a passing node's declared workspace, so a branch tip is the run's final state (#84).

A node does its work in its declared workspace and often ends without committing. Measured: two dispatch children
ended with a tip equal to an ancestor of main while their worktrees held the work, so forkclose refused the branch
as UNLANDED, correctly, and no code committed it. The engine is the one writer; the model never commits.

The engine commits only a node that passed, only a workspace the run itself made inside the run folder, and only
when that workspace is dirty. A clean workspace, a node with no declared workspace, a failed node and a dry run
write no commit and touch no tree. A commit the engine cannot make is one `workspace_commit` event that names the
reason; it never fails the node. The one git call carries a deadline (ADR-0051): a timeout commits nothing.

The caller commits before it records the outcome's `workspace` snapshot, so a resume compares against the
committed state (ADR-0107).
"""
import subprocess
from pathlib import Path

from squad import actions, git

COMMIT_DEADLINE_S = 60      # the one git call's cap; a timeout commits nothing


def message(node, entry, run_id):
    """The engine's commit line: the same `squad:` shape the action runner's own commit writes."""
    return f"squad: {node} passed ({run_id})"


def commit_after(engine, *, node, entry, item, logical, path, outcome, timeout=COMMIT_DEADLINE_S):
    """Commit the node's declared workspace when the rules allow. Return the event written, or None.

    `logical` is the declared workspace name the record carries; `path` is the directory the caller resolved for
    it. Only a directory inside the run folder is ever committed: the target is the owner's checkout.
    """
    if engine.dry or outcome != "pass" or logical in (None, ".") or not engine.has_git:
        return None
    p = Path(path).resolve()
    fields = {"node": node, "entry": entry, "workspace": logical, "path": str(p), **({"item": item} if item is not None else {})}

    def refuse(reason, **extra):
        return engine.record.append("workspace_commit", **fields, status="refused", reason=reason, **extra)

    if engine.run_dir.resolve() not in p.parents:
        return refuse("outside_run_area")
    if not p.is_dir():
        return refuse("workspace_missing")
    if git.run(["rev-parse", "--is-inside-work-tree"], p).returncode != 0:
        return refuse("not_a_worktree")
    if not git.dirty(p):
        return None
    try:
        r = actions.commit(p, message(node, entry, engine.run_id), timeout)
    except subprocess.TimeoutExpired:
        return refuse("timeout")
    if not r.ok:
        return refuse("commit_failed", stderr=r.extra.get("stderr", ""))
    return engine.record.append("workspace_commit", **fields, status="committed", head=r.head)
