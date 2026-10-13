"""The engine commits a passing node's dirty workspace, so a branch tip always equals the run's final state (#84).

One writer: the commit is the engine's, never the model's. It happens only for a node that declared a workspace,
only when that workspace is a git worktree inside the run's own area with uncommitted changes, and only when the
node passed. A failed node leaves its tree as it is: a person reads it. A commit the engine cannot make is one
`workspace_commit` event naming the reason; it never fails the node. The git call has a deadline (ADR-0051).
"""
import re
import subprocess
from pathlib import Path

from squad import actions, git

COMMIT_DEADLINE_S = 60      # the wall-clock cap of the one git call; a timeout commits nothing

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


def commit_after(engine, *, node, entry, item, logical, path, outcome):
    """Commit the node's workspace when the rules allow. Return the record event written, or None when nothing applied."""
    if engine.dry or outcome != "pass" or logical in (None, ".") or not engine.has_git:
        return None
    p = Path(path).resolve()
    fields = dict(node=node, entry=entry, workspace=logical, path=str(p), **({"item": item} if item is not None else {}))
    refuse = lambda reason, **x: engine.record.append("workspace_commit", **fields, status="refused", reason=reason, **x)   # noqa: E731
    if (engine.run_dir / "ws").resolve() not in p.parents:
        return refuse("outside_run_area")
    if not p.is_dir():
        return refuse("workspace_missing")
    if git.run(["rev-parse", "--is-inside-work-tree"], p).returncode != 0:
        return refuse("not_a_worktree")
    if not git.dirty(p):
        return None
    try:
        r = actions.commit(p, message(node, entry, logical, engine.run_id, issue_of(engine.g.doc)), timeout=COMMIT_DEADLINE_S)
    except subprocess.TimeoutExpired:
        return refuse("timeout")
    if not r.ok:
        return refuse("commit_failed", stderr=r.extra.get("stderr", ""))
    return engine.record.append("workspace_commit", **fields, status="committed", head=r.head)
