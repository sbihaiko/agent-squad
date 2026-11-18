"""Write one native blocking edge only after the deadlock validator passes it (#1985).

An edge that adds no order is refused as `redundant` (#2259): the blocked ticket already waits for the blocker through
a chain of open tickets. A written edge names in `now_redundant` each older direct edge that it makes redundant.

Every read and write goes through the GitHub door (ghread.py). The strict read is a direct GraphQL read, never the
throttle's snapshot, so the check sees GitHub as it is now. A rerun finds the edges already written and writes only the
missing ones.

usage: python3 edge_write.py <owner/repo> BLOCKED:BLOCKER [BLOCKED:BLOCKER ...]
"""
from __future__ import annotations

import json
import sys

import deadlock
import ghread

CLOSURE_CAP = 200
REFUSED = ("invalid", "unknown")

ISSUE_QUERY = """query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    issue(number: $number) {
      databaseId state
      labels(first: 100) { nodes { name } }
      parent { number }
      subIssues(first: 100) { pageInfo { hasNextPage } nodes { number } }
      blockedBy(first: 100) { pageInfo { hasNextPage } nodes { number state repository { nameWithOwner } } }
    }
  }
}"""


class GhFailed(RuntimeError):
    pass


class Unreadable(RuntimeError):
    pass


class WriteUnconfirmed(RuntimeError):
    pass


def _node(repo, number):
    owner, name = repo.split("/")
    res = ghread.graphql_read(ISSUE_QUERY, {"owner": owner, "name": name, "number": number})
    if not res.is_answer:
        raise GhFailed(res.message or res.detail or f"the read of {repo}#{number} {res.kind}")
    issue = ((res.answer.get("data") or {}).get("repository") or {}).get("issue")
    if issue is None:
        raise GhFailed(f"{repo}#{number} is not readable")
    blocked, kids = issue["blockedBy"], issue["subIssues"]
    return {
        "repo": repo, "number": number, "id": issue["databaseId"], "state": issue["state"].lower(),
        "labels": issue["labels"]["nodes"], "parent": issue["parent"]["number"] if issue["parent"] else None,
        "blocked_by_edges": [{"repo": b["repository"]["nameWithOwner"], "number": b["number"], "state": b["state"].lower()}
                             for b in blocked["nodes"]],
        "blocked_by_complete": not blocked["pageInfo"]["hasNextPage"] and not kids["pageInfo"]["hasNextPage"],
        "children": [k["number"] for k in kids["nodes"]],
    }


def _closure(repo, seeds):
    nodes, todo = {}, list(seeds)
    while todo:
        number = todo.pop()
        if number in nodes:
            continue
        if len(nodes) >= CLOSURE_CAP:
            raise Unreadable(f"the waits around {sorted(seeds)} reach more than {CLOSURE_CAP} issues")
        node = _node(repo, number)
        nodes[number] = node
        todo.extend(e["number"] for e in node["blocked_by_edges"] if e["repo"] == repo)
        todo.extend(node["children"])
        if node["parent"]:
            todo.append(node["parent"])
    return nodes


def _chain(nodes, repo, start, goal, skip=None):
    """The shortest chain of waits from `start` to `goal` over open tickets, or []; `skip` is one direct edge to ignore."""
    paths = [[start]]
    seen = {start}
    while paths:
        path = paths.pop(0)
        for edge in nodes[path[-1]]["blocked_by_edges"]:
            number = edge["number"]
            if edge["repo"] != repo or number in seen or (path[-1], number) == skip:
                continue
            if number == goal:
                return path + [number]
            if edge["state"] == "open" and number in nodes:   # a closed ticket holds no one, so no chain goes through it
                seen.add(number)
                paths.append(path + [number])
    return []


def _post(repo, blocked, blocker_id):
    res = ghread.write(f"repos/{repo}/issues/{blocked}/dependencies/blocked_by", "POST", {"issue_id": blocker_id})
    if res.kind != ghread.DONE:
        raise GhFailed(res.message or res.detail or f"the write of {repo}#{blocked} {res.kind}")


def add_edge(repo, blocked, blocker):
    """Write `blocked` waits on `blocker` when the validator passes it; return what happened."""
    try:
        nodes = _closure(repo, [blocked, blocker])
    except Unreadable as exc:
        return {"status": "refused", "verdict": "unknown", "reasons": [str(exc)], "path": []}
    if any(e["number"] == blocker and e["repo"] == repo for e in nodes[blocked]["blocked_by_edges"]):
        return {"status": "exists", "blocked": blocked, "blocker": blocker}
    verdict = deadlock.validate(deadlock.graph_of(list(nodes.values())),
                                [(deadlock.key(repo, blocked), deadlock.key(repo, blocker))])
    if verdict["status"] in REFUSED:
        return {"status": "refused", "verdict": verdict["status"], "reasons": verdict["reasons"], "path": verdict["witness"]}
    chain = _chain(nodes, repo, blocked, blocker)
    if chain:
        said = " > ".join(deadlock.key(repo, n) for n in chain)
        return {"status": "refused", "verdict": "redundant", "path": [deadlock.key(repo, n) for n in chain],
                "reasons": [f"{deadlock.key(repo, blocked)} already waits for {deadlock.key(repo, blocker)}: {said}"]}
    shadowed = [e["number"] for e in nodes[blocked]["blocked_by_edges"]
                if e["repo"] == repo and e["state"] == "open" and nodes[blocker]["state"] == "open"
                and _chain(nodes, repo, blocker, e["number"])]
    _post(repo, blocked, nodes[blocker]["id"])
    if not any(e["number"] == blocker and e["repo"] == repo for e in _node(repo, blocked)["blocked_by_edges"]):
        raise WriteUnconfirmed(f"the edge {repo}#{blocked} blocked by {repo}#{blocker} does not read back")
    return {"status": "written", "blocked": blocked, "blocker": blocker, "now_redundant": shadowed}


def publish_edges(repo, pairs):
    """Write each (blocked, blocker) pair in order; a failure stops the run and a rerun writes what is missing."""
    return [add_edge(repo, blocked, blocker) for blocked, blocker in pairs]


def main(argv):
    repo, *pairs = argv
    edges = [tuple(int(n) for n in pair.split(":")) for pair in pairs]
    results = publish_edges(repo, edges)
    for result in results:
        print(json.dumps(result))
    return 1 if any(r["status"] == "refused" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
