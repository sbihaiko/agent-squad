"""The fast read of the open cards (SPEC 968): one GraphQL read of the project's open items, page by page.

A snapshot is whole or nothing: a page that fails, a GraphQL error, a card whose labels are cut short, a spent hourly
budget or a passed deadline raises SnapshotError, and the caller moves no card. The read writes column inputs onto the
cards the cache already holds; a card the snapshot does not carry keeps its row, and a card the snapshot carries and the
cache does not hold asks for a full read through the board-dirty flag. The snapshot drops a card already in a terminal
column, so such a card is not seen here and waits for the full read.

A held card the last whole snapshot carried and this one lacks is checked once more in a batched nodes(ids:) read and
classified: closed, abandoned, archived, removed from the project, or unreadable (kept, and retried in the pass).
"""
import json
import re
import time

HOUR = 3600
LABELS_PER_CARD = 20
INPUTS = ("st", "su", "iu", "s", "cr", "l")   # the column inputs: Status and its instant, item instant, state, close reason, labels
QUERY = """query($owner:String!,$number:Int!,$after:String,$q:String!){
  rateLimit{cost remaining}
  ROOT(login:$owner){projectV2(number:$number){items(first:100,after:$after,query:$q){
    pageInfo{hasNextPage endCursor}
    nodes{updatedAt isArchived
      fieldValueByName(name:"Status"){... on ProjectV2ItemFieldSingleSelectValue{name updatedAt}}
      content{... on Issue{id number fullDatabaseId state stateReason updatedAt repository{nameWithOwner}
        labels(first:LABELS){totalCount nodes{name}}}}}}}}}"""
NODES = """query{rateLimit{cost remaining} nodes(ids:IDS){__typename ... on Issue{id number state stateReason updatedAt
  repository{nameWithOwner} labels(first:LABELS){totalCount nodes{name}}
  projectItems(first:50){totalCount nodes{updatedAt isArchived project{number owner{... on Organization{login} ... on User{login}}}
    fieldValueByName(name:"Status"){... on ProjectV2ItemFieldSingleSelectValue{name updatedAt}}}}}}}"""
ARCHIVED, REMOVED, CLOSED, ABANDONED, UNREADABLE = "archived", "removed", "closed", "abandoned", "unreadable"
NODE_BATCH = 100   # nodes(ids:) takes at most this many ids
CHECK_ATTEMPTS = 2   # the batched check's answers in one pass: the first and one retry of what stayed unreadable
ABANDONED_REASONS = ("not_planned", "duplicate")   # the close reasons the board reads as not delivered (board_columns.NOT_PLANNED)
GRAPHQL_ID = re.compile(r"[A-Za-z0-9_=-]+")


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


def nodes_query(ids):
    return NODES.replace("IDS", json.dumps(ids)).replace("LABELS", str(LABELS_PER_CARD))


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
        variables = {"owner": owner, "number": number, "after": after, "q": flt}
        try: answer = gql(text, {k: v for k, v in variables.items() if v is not None}, left)
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


def _key(database_id):
    """A card's database id as an int: GraphQL sends `fullDatabaseId` as a string, the items route sends `id` as a number."""
    return None if database_id is None else int(database_id)


def _inputs(state, reason, status, item_updated, labels):
    """The column inputs of one card, the same whether the snapshot or the batched check read it."""
    return {"st": status.get("name"), "su": status.get("updatedAt"), "iu": item_updated,
            "s": str(state or "").lower(), "cr": (str(reason).lower() if reason else None), "l": labels}


def rows_of(nodes, repo):
    """The issues of `repo` in the snapshot, keyed by their database id; a draft item and an archived item are not cards."""
    out = []
    for n in nodes:
        c = n.get("content") or {}
        if c.get("number") is None or (c.get("repository") or {}).get("nameWithOwner") != repo or n.get("isArchived"): continue
        out.append({"fd": _key(c.get("fullDatabaseId")), "nid": c.get("id"), "r": repo,
                    **_inputs(c.get("state"), c.get("stateReason"), n.get("fieldValueByName") or {}, n.get("updatedAt"),
                              [x["name"] for x in (c.get("labels") or {}).get("nodes") or []])})
    return out


def apply(held, rows, observed):
    """The held rows with the snapshot's column inputs written where the snapshot is newer. A held row the snapshot does
    not carry keeps its row; a joined row takes the snapshot's inputs and the instant the snapshot was read."""
    by = {_key(r["fd"]): r for r in rows if r.get("fd") is not None}
    out = []
    for i in held:
        r = by.get(_key(i.get("fd")))
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


def missing(seen, rows, held):
    """The cards the last whole snapshot held (`seen`: database id -> node id) that this snapshot lacks and the cache holds."""
    now = {r["fd"] for r in rows if r.get("fd") is not None}
    cached = {_key(i.get("fd")) for i in held}
    return {_key(fd): nid for fd, nid in seen.items() if nid and _key(fd) in cached and _key(fd) not in now}


def unknown(rows, held):
    """The snapshot's rows the cache does not hold: a card the fast read saw and the full read never wrote, so the board
    never draws it until a full read runs (#985). A row with no database id joins no card and is no card of this board."""
    cached = {_key(i.get("fd")) for i in held}
    return [r for r in rows if r.get("fd") is not None and _key(r["fd"]) not in cached]


def _on_board(item, owner, number):
    project = item.get("project") or {}
    return str(project.get("number")) == str(number) and str((project.get("owner") or {}).get("login") or "").lower() == str(owner).lower()


def _verdict(node, owner, number):
    """(class, inputs) of one answered issue: the item on this board decides archived and removed, and its Status with the
    issue decide closed or abandoned. A card no class fits, or whose answer is cut short, is unreadable."""
    if not isinstance(node, dict) or node.get("number") is None or str(node.get("state") or "").upper() not in ("OPEN", "CLOSED"):
        return UNREADABLE, None
    items = node.get("projectItems") or {}
    found = [x for x in items.get("nodes") or [] if _on_board(x, owner, number)]
    if not found:
        return (UNREADABLE, None) if (items.get("totalCount") or 0) > len(items.get("nodes") or []) else (REMOVED, None)
    item = found[0]
    if item.get("isArchived"): return ARCHIVED, None
    labels = node.get("labels") or {}
    if (labels.get("totalCount") or 0) > len(labels.get("nodes") or []): return UNREADABLE, None
    status = item.get("fieldValueByName") or {}
    inputs = _inputs(node.get("state"), node.get("stateReason"), status, item.get("updatedAt"), [x["name"] for x in labels.get("nodes") or []])
    if status.get("name") == "Abandoned" or (inputs["s"] == "closed" and inputs["cr"] in ABANDONED_REASONS): return ABANDONED, inputs
    if status.get("name") == "Closed" or inputs["s"] == "closed": return CLOSED, inputs
    return UNREADABLE, None


def _unreadable(cards):
    return {fd: {"cls": UNREADABLE, "nid": nid, "inputs": None} for fd, nid in cards.items()}


def _ask(gql, owner, number, cards, budget, timeout):
    """One batched nodes(ids:) read over `cards` ({database id: node id}). A node id the query cannot carry is unreadable
    and never sent; an error or a failed call leaves every sent card unreadable."""
    sendable = {fd: nid for fd, nid in cards.items() if GRAPHQL_ID.fullmatch(nid)}
    out = _unreadable({fd: nid for fd, nid in cards.items() if fd not in sendable})
    if not sendable: return out
    try: answer = gql(nodes_query(list(sendable.values())), {}, timeout)
    except Exception:
        budget.charge(1)
        return {**out, **_unreadable(sendable)}
    budget.charge(_points(answer))
    nodes = ((answer.get("data") or {}).get("nodes") if isinstance(answer, dict) and not answer.get("errors") else None)
    if not isinstance(nodes, list): return {**out, **_unreadable(sendable)}
    by = {n["id"]: n for n in nodes if isinstance(n, dict) and n.get("id")}
    for fd, nid in sendable.items():
        cls, inputs = _verdict(by.get(nid), owner, number)
        out[fd] = {"cls": cls, "nid": nid, "inputs": inputs}
    return out


def check(gql, owner, number, lost, budget, deadline, clock=time.time):
    """The cards the snapshot lost, checked in one batched read per NODE_BATCH ids, with one retry of what stayed unreadable.
    Returns {database id: {"cls", "nid", "inputs"}}. A card no answer reached is unreadable, so its row stays for the next pass."""
    verdicts, todo = {}, dict(lost)
    for _ in range(CHECK_ATTEMPTS):
        for start in range(0, len(todo), NODE_BATCH):
            if not budget.open() or deadline - clock() <= 0: break
            chunk = {fd: todo[fd] for fd in list(todo)[start:start + NODE_BATCH]}
            verdicts.update(_ask(gql, owner, number, chunk, budget, deadline - clock()))
        todo = {fd: nid for fd, nid in todo.items() if verdicts.get(fd, {}).get("cls", UNREADABLE) == UNREADABLE}
    verdicts.update({fd: v for fd, v in _unreadable(todo).items() if fd not in verdicts})
    return verdicts


def settle(held, verdicts, observed):
    """The held rows with the batched check's classes written: archived and removed rows leave, closed and abandoned rows
    take their inputs, and an unreadable row keeps its inputs unchanged."""
    out = []
    for i in held:
        v = verdicts.get(_key(i.get("fd")))
        if v is None or v["cls"] == UNREADABLE:
            out.append(i)
            continue
        if v["cls"] in (ARCHIVED, REMOVED): continue
        merged = dict(i)
        for k in INPUTS:
            if v["inputs"].get(k) is None: merged.pop(k, None)
            else: merged[k] = v["inputs"][k]
        merged["ot"] = observed
        out.append(merged)
    return out


def seen_of(rows, verdicts):
    """The cards the last whole snapshot holds for the next pass: the snapshot's own, and the unreadable ones kept for a retry."""
    seen = {str(r["fd"]): r["nid"] for r in rows if r.get("fd") is not None and r.get("nid")}
    seen.update({str(fd): v["nid"] for fd, v in verdicts.items() if v["cls"] == UNREADABLE})
    return seen
