#!/usr/bin/env python3
"""The live board as an instance of the board definition (ADR-0141): board_define.py --definition <board.json> [--check] [--apply] [--migrate|--contract] <owner> <project>
The run reads the project's fields once, over REST through the board binding (issue_state), and names each difference
between the board and the definition by name: an option the definition names and the board lacks, an option the board
carries and the definition does not name, a field the board lacks, and a holder text field the binding writes (issue_state.HOLDER_FIELDS) the board lacks.
Then it prints the expand plan.
Expand never removes: the options go in the definition's order, a missing one where the definition puts it, every option already there keeps its id, name,
colour and description, so no card loses the value it carries, and no item is written, so no card moves. A missing field
is created with the definition's options; a missing typed field (a text field the holder write reads, or the Gate Tries
count) is created with its type and no value (#487, spec #1218). An option the definition does not name stays, reported as drift; dropping it
is the definition's applier's, which refuses a column cards still carry.
With no --apply the run is a dry run: one read, no write. --check exits 1 when the board drifts. --apply is a live write
on the board and waits for the owner's grant (ADR-0024). The definition is read from its path, never kept here (ADR-0141).
--migrate moves each card on an old Status option to the column board_migrate plans (ADR-0143), and proves every move by
one items read after the writes. --contract drops each Status option the definition does not name, and refuses while a
card still carries one: no card ever loses the value it carries. --rename renames the two old options in place (#2154):
one field call carries every option with its own id, so each card keeps its column, and one items read proves every card
moved with its option and the count is the same. All three are dry runs without --apply."""
import argparse, json, sys
from issue_state import HOLDER_FIELDS, board_state
import board_migrate

REFUSED = board_migrate.REFUSED

GROUPS = ("statuses", "kinds", "freeze", "outcomes")


def text(v):
    return v.get("raw", "") if isinstance(v, dict) else (v or "")


def scalars(definition):
    """[(field name, type)] for each typed field the definition names: its entry is the field's type, so the applier
    creates it with that type (the Gate Tries count is a number). A holder field is created as text by its own row, so a
    definition that also types one never names it twice: the two sets stay disjoint."""
    return [(name, definition[key]["type"]) for key, name in definition["fieldNames"].items()
            if key not in GROUPS and name not in HOLDER_FIELDS]


def snapshot(fields):
    """{field name: {"id": node id, "options": [{id, name, color, description}]}} for the single-select fields, and
    {field name: {"id": node id, "options": None}} for the typed fields (text and number)."""
    out = {}
    for f in fields:
        if f.get("data_type") in ("text", "number"): out[f["name"]] = {"id": f.get("node_id") or f.get("id"), "options": None}
        if f.get("data_type") != "single_select": continue
        out[f["name"]] = {"id": f.get("node_id") or f.get("id"), "options": [
            {"id": o["id"], "name": text(o["name"]), "color": o.get("color", "GRAY"), "description": text(o.get("description"))}
            for o in f.get("options", [])]}
    return out


def diff(definition, board):
    """[(field name, entries, board field or None, missing names, extra names)] for each field the definition names.
    A text field has no entries (None) and no options to miss; a typed field carries its type as its entries."""
    out = []
    for key in GROUPS:
        name, entries = definition["fieldNames"][key], definition[key]
        field = board.get(name)
        have = [o["name"] for o in field["options"]] if field else []
        missing = [e["name"] for e in entries if e["name"] not in have]
        extra = [n for n in have if n not in {e["name"] for e in entries}]
        out.append((name, entries, field, missing, extra))
    out += [(name, kind, board.get(name), [], []) for name, kind in scalars(definition)]
    out += [(name, None, board.get(name), [], []) for name in HOLDER_FIELDS]
    return out


def option(entry):
    return {"name": entry["name"], "color": entry["color"], "description": entry.get("fact", "")}


def report(rows):
    lines = []
    for name, entries, field, missing, extra in rows:
        if field is None: lines.append(f"drift {name}: field missing"); continue
        lines += [f"drift {name}: missing {n}" for n in missing] + [f"drift {name}: not in the definition {n}" for n in extra]
    for name, entries, field, missing, extra in rows:
        if field is None and entries is None: lines.append(f"plan {name}: create text field")
        elif field is None and isinstance(entries, str): lines.append(f"plan {name}: create {entries} field")
        elif field is None: lines.append(f"plan {name}: create " + ", ".join(e["name"] for e in entries))
        elif missing: lines.append(f"plan {name}: add " + ", ".join(missing))
    return lines


def expand(binding, project_id, rows):
    """One mutation per field that lacks an option or is missing; none for a field that already carries them all."""
    def graphql(query, variables):
        out = json.loads(binding.api("graphql", "--input", "-", inp=json.dumps({"query": query, "variables": variables})) or "{}")
        if out.get("errors"): raise RuntimeError(json.dumps(out["errors"])[:300])
    for name, entries, field, missing, extra in rows:
        if field is None and entries is None:
            graphql("mutation($input: CreateProjectV2FieldInput!) { createProjectV2Field(input: $input) { projectV2Field { ... on ProjectV2Field { id } } } }",
                    {"input": {"projectId": project_id, "dataType": "TEXT", "name": name}})
        elif field is None and isinstance(entries, str):
            graphql("mutation($input: CreateProjectV2FieldInput!) { createProjectV2Field(input: $input) { projectV2Field { ... on ProjectV2Field { id } } } }",
                    {"input": {"projectId": project_id, "dataType": entries.upper(), "name": name}})
        elif field is None:
            graphql("mutation($input: CreateProjectV2FieldInput!) { createProjectV2Field(input: $input) { projectV2Field { ... on ProjectV2SingleSelectField { id } } } }",
                    {"input": {"projectId": project_id, "dataType": "SINGLE_SELECT", "name": name, "singleSelectOptions": [option(e) for e in entries]}})
        elif missing:
            today = {o["name"]: dict(o) for o in field["options"]}   # each with its id: a card keeps the value it carries
            ordered = [today.get(e["name"]) or option(e) for e in entries]   # the definition's order sets the column order
            named = {e["name"] for e in entries}
            extra = [dict(o) for o in field["options"] if o["name"] not in named]   # an option the definition does not name goes after
            graphql("mutation($input: UpdateProjectV2FieldInput!) { updateProjectV2Field(input: $input) { projectV2Field { ... on ProjectV2SingleSelectField { id } } } }",
                    {"input": {"fieldId": field["id"], "singleSelectOptions": ordered + extra}})


def cards(binding, status):
    """Every card in one paginated items read: n (repo#number, the key on a board two repositories share), repo, number,
    iid, st (the Status option name), s (open/closed), l (labels) and cr (the issue's close reason, #921)."""
    out = binding.api("--paginate", f"{binding.base}/items?per_page=100&fields[]={status['id']}", "--jq",
                      '.[]|select(.content.number)|{iid:.id,number:.content.number,repo:((.content.repository_url//"")|sub(".*/repos/";"")),'
                      's:(.content.state//""),l:[.content.labels[]?.name],'
                      'cr:((.content.state_reason//"")|if type=="string" then ascii_downcase else . end),'
                      'st:(((.fields[]?|select(.name=="Status")|.value.name|if type=="object" then .raw else . end))//"")}|@json')
    rows = [json.loads(l) for l in out.splitlines() if l.strip()]
    for r in rows: r["n"] = f"{r['repo']}#{r['number']}"
    return rows


def status_field(fields):
    s = next((f for f in fields if f.get("name") == "Status"), None)
    if not s: raise SystemExit("refused: the board has no Status field")
    return {"id": s["id"], "node": s.get("node_id") or s["id"],
            "opt": {text(o["name"]): o["id"] for o in s.get("options", [])}, "options": snapshot([s])["Status"]["options"]}


def migrate(binding, status, apply):
    """Move every card on an old option to its planned column, then prove each move with one read. Returns the exit code."""
    items = cards(binding, status)
    p = board_migrate.plan(items)
    by = {i["n"]: i for i in items}
    for m in p["moves"]: print(f"MOVE {m['card']} {m['old']} -> {m['new']}" + "".join(f" +{a}" for a in m["add_labels"]))
    missing = sorted({m["new"] for m in p["moves"]} - set(status["opt"]))
    if missing: print("refused: the board lacks the column(s) " + ", ".join(missing) + "; expand first", file=sys.stderr); return REFUSED
    if not apply: print(f"dry run: {len(p['moves'])} move(s), nothing written"); return 0
    for m in p["moves"]:
        i = by[m["card"]]
        if m["add_labels"]:
            binding.api("-X", "POST", f"repos/{i['repo']}/issues/{i['number']}/labels", "--input", "-", inp=json.dumps({"labels": m["add_labels"]}))
        binding.api("-X", "PATCH", f"{binding.base}/items/{i['iid']}", "--input", "-",
                    inp=json.dumps({"fields": [{"id": status["id"], "value": status["opt"][m["new"]]}]}))
    after = {i["n"]: i["st"] for i in cards(binding, status)}   # the read is the proof, never the write's answer
    lost = [m for m in p["moves"] if after.get(m["card"]) != m["new"]]
    for m in lost: print(f"NOT-CARRIED {m['card']}: wanted {m['new']}, carries {after.get(m['card'], 'no card')!r}", file=sys.stderr)
    print(f"done: {len(p['moves']) - len(lost)} of {len(p['moves'])} move(s) carried")
    return 1 if lost else 0


def contract(binding, status, definition, apply):
    """Drop each Status option the definition does not name. A card on such an option refuses the whole contract."""
    named = {e["name"] for e in definition["statuses"]}
    extra = [o["name"] for o in status["options"] if o["name"] not in named]
    if not extra: print("no drift: the Status field carries no option the definition does not name"); return 0
    counts = {o: n for o, n in ((o, sum(1 for i in cards(binding, status) if i["st"] == o)) for o in extra) if n}
    if counts:
        print("refused: a card still carries an old option; move it first. " + ", ".join(f"{o}: {n}" for o, n in counts.items()), file=sys.stderr)
        return REFUSED
    for o in extra: print(f"REMOVE option {o}")
    if not apply: print("dry run: nothing written"); return 0
    kept = [dict(o) for o in status["options"] if o["name"] in named]   # each kept option keeps its id: no card moves
    out = json.loads(binding.api("graphql", "--input", "-", inp=json.dumps({"query":
        "mutation($input: UpdateProjectV2FieldInput!) { updateProjectV2Field(input: $input) { projectV2Field { ... on ProjectV2SingleSelectField { id } } } }",
        "variables": {"input": {"fieldId": status["node"], "singleSelectOptions": kept}}})) or "{}")
    if out.get("errors"): raise RuntimeError(json.dumps(out["errors"])[:300])
    print("done: the old options are removed")
    return 0


def rename(binding, status, apply):
    """Rename each option of the Status field in place: one field call carries every option with its own id, so no card
    moves. One items read after the call proves every card kept its column and the count is the same (#2154)."""
    sent, changed = board_migrate.renamed(status["options"])
    for o in status["options"]:
        if o["name"] in board_migrate.RENAMES:
            print(f"RENAME {o['name']} -> {board_migrate.RENAMES[o['name']]}")
    if not changed: print("no old name: the field already carries the shipped vocabulary")
    if not apply: print(f"dry run: {len(sent)} option(s) in one field call, nothing written"); return 0
    before = cards(binding, status)
    out = json.loads(binding.api("graphql", "--input", "-", inp=json.dumps({"query":
        "mutation($input: UpdateProjectV2FieldInput!) { updateProjectV2Field(input: $input) { projectV2Field { ... on ProjectV2SingleSelectField { id } } } }",
        "variables": {"input": {"fieldId": status["node"], "singleSelectOptions": sent}}})) or "{}")
    if out.get("errors"): raise RuntimeError(json.dumps(out["errors"])[:300])
    after = cards(binding, status)   # the read is the proof, never the write's answer
    was = {i["n"]: i["st"] for i in before}
    now = {i["n"]: i["st"] for i in after}
    not_renamed = [(n, board_migrate.RENAMES[s], now.get(n)) for n, s in was.items()
                   if s in board_migrate.RENAMES and now.get(n) != board_migrate.RENAMES[s]]
    stale = [(n, s) for n, s in now.items() if s in board_migrate.RENAMES]
    for n, want, got in not_renamed: print(f"NOT-RENAMED {n}: wanted {want}, reads {got!r}", file=sys.stderr)
    for n, s in stale: print(f"OLD-NAME {n}: still reads {s!r}", file=sys.stderr)
    if len(before) != len(after): print(f"COUNT {len(before)} before the call, {len(after)} after it", file=sys.stderr)
    ok = len(before) == len(after) and not not_renamed and not stale
    print(f"done: {len(after)} card(s), {len(before)} before the call; "
          + ("every card kept its column" if ok else "the proof failed"))
    return 0 if ok else 1


def main(argv):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--definition", required=True, help="the board definition's board.json")
    p.add_argument("--check", action="store_true", help="exit 1 when the board drifts from the definition")
    p.add_argument("--apply", action="store_true", help="write the expand plan to the live board (needs the owner's grant)")
    step = p.add_mutually_exclusive_group()
    step.add_argument("--migrate", action="store_true", help="move each card off an old Status option (ADR-0143)")
    step.add_argument("--contract", action="store_true", help="drop the Status options the definition does not name; refused while a card carries one")
    step.add_argument("--rename", action="store_true", help="rename the Status options in place, keeping every option id (#2154)")
    p.add_argument("owner"); p.add_argument("project")
    a = p.parse_args(argv)
    definition = json.load(open(a.definition))
    try:
        binding = board_state(owner=a.owner, project=a.project, kind=None).binding
    except KeyError as e:   # the binding caches under $SG_STATE: name the gap, never a traceback (#770)
        if e.args != ("SG_STATE",):
            raise
        print("board_define.py: SG_STATE is not set; source env.sh or set SG_STATE to the loop's state directory", file=sys.stderr)
        return 2
    try:
        fields = json.loads(binding.api(f"{binding.base}/fields?per_page=100"))   # the one read
    except RuntimeError as e:   # a refused read (rate limit, auth): one line, never a traceback (#781)
        print(f"board_define.py: board unreadable: {' '.join(str(e).split())}", file=sys.stderr)
        return 2
    if a.migrate: return migrate(binding, status_field(fields), a.apply)
    if a.contract: return contract(binding, status_field(fields), definition, a.apply)
    if a.rename: return rename(binding, status_field(fields), a.apply)
    rows = diff(definition, snapshot(fields))
    lines = report(rows)
    print(f"read: {len(fields)} field(s) from {binding.base}/fields")
    print("\n".join(lines) if lines else "no drift: the board carries every option the definition names, and no other")
    planned = any(f is None or m for _, _, f, m, _ in rows)
    if a.apply and planned:
        expand(binding, project_node(binding) if any(f is None for _, _, f, _, _ in rows) else None, rows)
        print("done: the expand plan is written")
    elif not a.apply:
        print("dry run: nothing written")
    return 1 if a.check and lines else 0


def project_node(binding):
    """The project's node id, which createProjectV2Field needs; read only when a field is created."""
    return json.loads(binding.api(binding.base)).get("node_id")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
