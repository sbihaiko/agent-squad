#!/usr/bin/env python3
"""The live board as an instance of the board definition (ADR-0141): board_define.py --definition <board.json> [--check] [--apply] <owner> <project>
The run reads the project's fields once, over REST through the board binding (issue_state), and names each difference
between the board and the definition by name: an option the definition names and the board lacks, an option the board
carries and the definition does not name, a field the board lacks. Then it prints the expand plan.
Expand never removes: a missing option is added beside today's options, every option already there keeps its id, name,
colour and description, so no card loses the value it carries, and no item is written, so no card moves. A missing field
is created with the definition's options. An option the definition does not name stays, reported as drift; dropping it
is the definition's applier's, which refuses a column cards still carry.
With no --apply the run is a dry run: one read, no write. --check exits 1 when the board drifts. --apply is a live write
on the board and waits for the owner's grant (ADR-0024). The definition is read from its path, never kept here (ADR-0141)."""
import argparse, json, sys
from issue_state import board_state

GROUPS = ("statuses", "kinds", "freeze", "outcomes")


def text(v):
    return v.get("raw", "") if isinstance(v, dict) else (v or "")


def snapshot(fields):
    """{field name: {"id": node id, "options": [{id, name, color, description}]}} for the single-select fields."""
    out = {}
    for f in fields:
        if f.get("data_type") != "single_select": continue
        out[f["name"]] = {"id": f.get("node_id") or f.get("id"), "options": [
            {"id": o["id"], "name": text(o["name"]), "color": o.get("color", "GRAY"), "description": text(o.get("description"))}
            for o in f.get("options", [])]}
    return out


def diff(definition, board):
    """[(field name, entries, board field or None, missing names, extra names)] for each field the definition names."""
    out = []
    for key in GROUPS:
        name, entries = definition["fieldNames"][key], definition[key]
        field = board.get(name)
        have = [o["name"] for o in field["options"]] if field else []
        missing = [e["name"] for e in entries if e["name"] not in have]
        extra = [n for n in have if n not in {e["name"] for e in entries}]
        out.append((name, entries, field, missing, extra))
    return out


def option(entry):
    return {"name": entry["name"], "color": entry["color"], "description": entry.get("fact", "")}


def report(rows):
    lines = []
    for name, entries, field, missing, extra in rows:
        if field is None: lines.append(f"drift {name}: field missing"); continue
        lines += [f"drift {name}: missing {n}" for n in missing] + [f"drift {name}: not in the definition {n}" for n in extra]
    for name, entries, field, missing, extra in rows:
        if field is None: lines.append(f"plan {name}: create " + ", ".join(e["name"] for e in entries))
        elif missing: lines.append(f"plan {name}: add " + ", ".join(missing))
    return lines


def expand(binding, project_id, rows):
    """One mutation per field that lacks an option or is missing; none for a field that already carries them all."""
    def graphql(query, variables):
        out = json.loads(binding.api("graphql", "--input", "-", inp=json.dumps({"query": query, "variables": variables})) or "{}")
        if out.get("errors"): raise RuntimeError(json.dumps(out["errors"])[:300])
    for name, entries, field, missing, extra in rows:
        if field is None:
            graphql("mutation($input: CreateProjectV2FieldInput!) { createProjectV2Field(input: $input) { projectV2Field { ... on ProjectV2SingleSelectField { id } } } }",
                    {"input": {"projectId": project_id, "dataType": "SINGLE_SELECT", "name": name, "singleSelectOptions": [option(e) for e in entries]}})
        elif missing:
            kept = [dict(o) for o in field["options"]]   # today's options first, each with its id: a card keeps the value it carries
            added = [option(e) for e in entries if e["name"] in missing]
            graphql("mutation($input: UpdateProjectV2FieldInput!) { updateProjectV2Field(input: $input) { projectV2Field { ... on ProjectV2SingleSelectField { id } } } }",
                    {"input": {"fieldId": field["id"], "singleSelectOptions": kept + added}})


def main(argv):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--definition", required=True, help="the board definition's board.json")
    p.add_argument("--check", action="store_true", help="exit 1 when the board drifts from the definition")
    p.add_argument("--apply", action="store_true", help="write the expand plan to the live board (needs the owner's grant)")
    p.add_argument("owner"); p.add_argument("project")
    a = p.parse_args(argv)
    definition = json.load(open(a.definition))
    binding = board_state(owner=a.owner, project=a.project, kind=None).binding
    fields = json.loads(binding.api(f"{binding.base}/fields?per_page=100"))   # the one read
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
