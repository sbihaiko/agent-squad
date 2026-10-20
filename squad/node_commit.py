"""The engine commits a passing node's dirty workspace, so a branch tip always equals the run's final state (#84, #90).

One writer: the commit is the engine's, never the model's. It happens only for a node that passed, and only in a git
worktree the run owns: a workspace the run made under its own `ws` folder (#84), or the run's target when that target is
a linked worktree that holds the run folder (#90: the loop starts a run with `--target <issue worktree>`, so its nodes
declare no workspace and the target is the workspace). The owner's checkout, the main worktree of the repository, is
never touched, and nor is a path outside the run: each is one `workspace_commit` event naming the reason. A failed node
leaves its tree as it is: a person reads it. A clean tree writes nothing. A refusal never fails the node. Every git call
has a deadline (ADR-0051); a timeout commits nothing.
"""
import re
import subprocess
from pathlib import Path

from squad import actions, git

COMMIT_DEADLINE_S = 60      # the wall-clock cap of the one git call; a timeout commits nothing
PROBE_DEADLINE_S = 10       # the cap of each read-only git call that decides whether the tree is the run's to commit

_REQUEST = re.compile(r"\n\n(?:Work|Task):\n(.*)\Z", re.S)


def issue_of(doc):
    """The issue a run names: the first `#<n>` in the request `with_request` appended to the entry agent, else None."""
    for n in doc.get("nodes", []):
        m = _REQUEST.search(n.get("instructions") or "")
        if m:
            found = re.search(r"#(\d+)", m.group(1))
            return int(found.group(1)) if found else None
    return None


def message(node, entry, logical, run_id, issue):
    """One subject line, a short body naming the node and its outcome, then `Refs #<n>` when the run names an issue."""
    tag = f" for #{issue}" if issue is not None else ""
    body = f"squad: {node} passed{tag}\n\nNode {node} entry {entry} passed in workspace {logical} (run {run_id}). The engine committed its tree.\n"
    return body + (f"\nRefs #{issue}\n" if issue is not None else "")


def _inside(p, parent):
    return parent == p or parent in p.parents


def run_folder_under(engine, p):
    """The run folder as a path relative to `p` when it lives inside `p`, else None: the one path a commit never stages."""
    r = engine.run_dir.resolve()
    return str(r.relative_to(p)) if _inside(r, p) and r != p else None


def ownership(engine, p, is_target):
    """Why the engine may not commit `p`, or None when `p` is a worktree the run owns.

    A path under the run's `ws` folder is the run's (#84). The target is the run's only when it is a linked worktree whose
    main checkout is elsewhere, and the run folder lives inside it (#90). Everything else is outside the run.
    """
    if _inside(p, (engine.run_dir / "ws").resolve()):
        return None
    if not is_target:
        return "outside_run_area"
    if not git.owned_worktree(p, PROBE_DEADLINE_S):
        return "owner_checkout"
    return None if run_folder_under(engine, p) else "outside_run_area"


def commit_after(engine, *, node, entry, item, logical, path, outcome):
    """Commit the node's workspace when the rules allow. Return the record event written, or None when nothing applied."""
    if engine.dry or outcome != "pass" or not engine.has_git:
        return None
    is_target = logical in (None, ".")
    p, logical = Path(path).resolve(), "target" if is_target else logical
    fields = dict(node=node, entry=entry, workspace=logical, path=str(p), **({"item": item} if item is not None else {}))
    refuse = lambda reason, **x: engine.record.append("workspace_commit", **fields, status="refused", reason=reason, **x)   # noqa: E731
    if not p.is_dir():
        return refuse("workspace_missing")
    try:
        if git.run(["rev-parse", "--is-inside-work-tree"], p, timeout=PROBE_DEADLINE_S).returncode != 0:
            return refuse("not_a_worktree")
        exclude = run_folder_under(engine, p)
        if not git.dirty(p, exclude, PROBE_DEADLINE_S):
            return None
        reason = ownership(engine, p, is_target)
        if reason:
            return refuse(reason)
        r = actions.commit(p, message(node, entry, logical, engine.run_id, issue_of(engine.g.doc)), timeout=COMMIT_DEADLINE_S, exclude=exclude)
    except subprocess.TimeoutExpired:
        return refuse("timeout")
    if not r.ok:
        return refuse("commit_failed", stderr=r.extra.get("stderr", ""))
    return engine.record.append("workspace_commit", **fields, status="committed", head=r.head)
