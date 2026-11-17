"""Write one native blocking edge only after the deadlock validator passes it (#1985).

The strict read calls `gh api` directly. It never goes through the throttle's snapshot, so the check sees GitHub as it is now.
A rerun finds the edges already written and writes only the missing ones.

usage: python3 edge_write.py <owner/repo> BLOCKED:BLOCKER [BLOCKED:BLOCKER ...]
"""
from __future__ import annotations

import json
import subprocess
import sys

import deadlock

CLOSURE_CAP = 200
PAGE = 100
REFUSED = ("invalid", "unknown")


class GhFailed(RuntimeError):
    pass


class Unreadable(RuntimeError):
    pass


class WriteUnconfirmed(RuntimeError):
    pass


def gh(argv):
    proc = subprocess.run(["gh", *argv], capture_output=True, text=True, check=False)
    if proc.returncode:
        raise GhFailed(proc.stderr.strip() or f"gh exited {proc.returncode}")
    return proc.stdout


def _number(url):
    return int(url.rsplit("/", 1)[1]) if url else None


def _repo_of(url):
    return "/".join(url.rsplit("/", 2)[-2:])


def _get(run, route):
    return json.loads(run(["api", route]))


def _blockers(repo, number, run):
    return _get(run, f"repos/{repo}/issues/{number}/dependencies/blocked_by?per_page={PAGE}")


def _node(repo, number, run):
    issue = _get(run, f"repos/{repo}/issues/{number}")
    blocks = _blockers(repo, number, run)
    subs = _get(run, f"repos/{repo}/issues/{number}/sub_issues?per_page={PAGE}")
    return {
        "repo": repo, "number": number, "id": issue["id"], "state": issue["state"],
        "labels": issue.get("labels") or [], "parent": _number(issue.get("parent_issue_url")),
        "blocked_by_edges": [{"repo": _repo_of(b["repository_url"]), "number": b["number"], "state": b["state"]} for b in blocks],
        "blocked_by_complete": len(blocks) < PAGE and len(subs) < PAGE,
        "children": [s["number"] for s in subs],
    }


def _closure(repo, seeds, run):
    nodes, todo = {}, list(seeds)
    while todo:
        number = todo.pop()
        if number in nodes:
            continue
        if len(nodes) >= CLOSURE_CAP:
            raise Unreadable(f"the waits around {sorted(seeds)} reach more than {CLOSURE_CAP} issues")
        node = _node(repo, number, run)
        nodes[number] = node
        todo.extend(e["number"] for e in node["blocked_by_edges"] if e["repo"] == repo)
        todo.extend(node["children"])
        if node["parent"]:
            todo.append(node["parent"])
    return nodes


def add_edge(repo, blocked, blocker, run=gh):
    """Write `blocked` waits on `blocker` when the validator passes it; return what happened."""
    try:
        nodes = _closure(repo, [blocked, blocker], run)
    except Unreadable as exc:
        return {"status": "refused", "verdict": "unknown", "reasons": [str(exc)], "path": []}
    if any(e["number"] == blocker and e["repo"] == repo for e in nodes[blocked]["blocked_by_edges"]):
        return {"status": "exists", "blocked": blocked, "blocker": blocker}
    verdict = deadlock.validate(deadlock.graph_of(list(nodes.values())),
                                [(deadlock.key(repo, blocked), deadlock.key(repo, blocker))])
    if verdict["status"] in REFUSED:
        return {"status": "refused", "verdict": verdict["status"], "reasons": verdict["reasons"], "path": verdict["witness"]}
    run(["api", "--method", "POST", f"repos/{repo}/issues/{blocked}/dependencies/blocked_by",
         "-F", f"issue_id={nodes[blocker]['id']}"])
    if blocker not in {b["number"] for b in _blockers(repo, blocked, run)}:
        raise WriteUnconfirmed(f"the edge {repo}#{blocked} blocked by {repo}#{blocker} does not read back")
    return {"status": "written", "blocked": blocked, "blocker": blocker}


def publish_edges(repo, pairs, run=gh):
    """Write each (blocked, blocker) pair in order; a failure stops the run and a rerun writes what is missing."""
    return [add_edge(repo, blocked, blocker, run=run) for blocked, blocker in pairs]


def main(argv):
    repo, *pairs = argv
    edges = [tuple(int(n) for n in pair.split(":")) for pair in pairs]
    results = publish_edges(repo, edges)
    for result in results:
        print(json.dumps(result))
    return 1 if any(r["status"] == "refused" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
