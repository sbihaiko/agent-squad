"""The fast read of the open cards (SPEC 968): one GraphQL read of the project's open items, page by page.

A snapshot is whole or nothing: a page that fails, a GraphQL error, a card whose labels are cut short, a spent hourly
budget or a passed deadline raises SnapshotError, and the caller moves no card. The read writes column inputs onto the
cards the cache already holds; a card the snapshot does not carry keeps its row, and a new card comes from the full read
or a board-dirty flag.
"""
import time

HOUR = 3600
LABELS_PER_CARD = 20
INPUTS = ("st", "su", "iu", "s", "cr", "l")   # the column inputs: Status and its instant, item instant, state, close reason, labels
QUERY = """query($owner:String!,$number:Int!,$after:String,$q:String!){
  rateLimit{cost}
  ROOT(login:$owner){projectV2(number:$number){items(first:100,after:$after,query:$q){
    pageInfo{hasNextPage endCursor}
    nodes{updatedAt isArchived
      fieldValueByName(name:"Status"){... on ProjectV2ItemFieldSingleSelectValue{name updatedAt}}
      content{... on Issue{number fullDatabaseId state stateReason updatedAt repository{nameWithOwner}
        labels(first:LABELS){totalCount nodes{name}}}}}}}}}"""


class SnapshotError(Exception):
    pass


class Budget:
    """GraphQL points per clock hour; a spent hour refuses reads until the next hour starts."""

    def __init__(self, per_hour, clock=time.time):
        self.per_hour, self.clock = per_hour, clock
        self.hour, self.spent = None, 0

    def _roll(self):
        now = int(self.clock() // HOUR)
        if now != self.hour:
            self.hour, self.spent = now, 0

    def open(self):
        self._roll()
        return self.spent < self.per_hour

    def charge(self, points):
        self._roll()
        self.spent += points


def query(root):
    if root not in ("user", "organization"): raise ValueError(f"an owner root is user or organization, not {root!r}")
    return QUERY.replace("ROOT", root).replace("LABELS", str(LABELS_PER_CARD))


def _points(answer):
    cost = (((answer.get("data") or {}).get("rateLimit") or {}).get("cost")) if isinstance(answer, dict) else None
    return cost if isinstance(cost, int) and cost > 0 else 1


def read_snapshot(gql, root, owner, number, closed, budget, deadline, clock=time.time):
    """Every open item's node, page by page. `gql(query, variables, timeout)` answers the parsed GraphQL body; a call
    that fails still spends a point of the hour, so a failure never reads the budget as free."""
    text = query(root)
    flt = " ".join(f"-status:{s}" for s in closed)
    nodes, after = [], None
    while True:
        if not budget.open(): raise SnapshotError("fast read budget spent for this hour")
        left = deadline - clock()
        if left <= 0: raise SnapshotError("fast read passed its deadline")
        try: answer = gql(text, {"owner": owner, "number": number, "after": after, "q": flt}, left)
        except Exception as e:
            budget.charge(1)
            raise SnapshotError(f"page failed: {str(e)[:80]}") from e
        budget.charge(_points(answer))
        if not isinstance(answer, dict) or answer.get("errors"): raise SnapshotError("GraphQL answered an error: the snapshot is discarded")
        page = (((answer.get("data") or {}).get(root) or {}).get("projectV2") or {}).get("items")
        if not isinstance(page, dict): raise SnapshotError("a page carried no items: the snapshot is discarded")
        for n in page.get("nodes") or []:
            if not isinstance(n, dict): continue
            labels = (n.get("content") or {}).get("labels") or {}
            if labels.get("totalCount", 0) > len(labels.get("nodes") or []):
                raise SnapshotError("a card's labels are cut short: the snapshot is discarded")
            nodes.append(n)
        info = page.get("pageInfo") or {}
        if not info.get("hasNextPage"): return nodes
        after = info.get("endCursor")
        if not after: raise SnapshotError("a page asks for another and names no cursor")


def rows_of(nodes, repo):
    """The issues of `repo` in the snapshot, keyed by their database id; a draft item and an archived item are not cards."""
    out = []
    for n in nodes:
        c = n.get("content") or {}
        if c.get("number") is None or (c.get("repository") or {}).get("nameWithOwner") != repo or n.get("isArchived"): continue
        status = n.get("fieldValueByName") or {}
        out.append({"fd": c.get("fullDatabaseId"), "st": status.get("name"), "su": status.get("updatedAt"), "iu": n.get("updatedAt"),
                    "s": str(c.get("state") or "").lower(), "cr": (str(c["stateReason"]).lower() if c.get("stateReason") else None),
                    "l": [x["name"] for x in (c.get("labels") or {}).get("nodes") or []]})
    return out


def apply(held, rows, observed):
    """The held rows with the snapshot's column inputs written where the snapshot is newer. A held row the snapshot does
    not carry keeps its row; a joined row takes the snapshot's inputs and the instant the snapshot was read."""
    by = {r["fd"]: r for r in rows if r.get("fd") is not None}
    out = []
    for i in held:
        r = by.get(i.get("fd"))
        if r is None or (i.get("ot") or 0) >= observed:
            out.append(i)
            continue
        merged = dict(i)
        for k in INPUTS:
            if r.get(k) is None: merged.pop(k, None)
            else: merged[k] = r[k]
        merged["ot"] = observed
        out.append(merged)
    return out
