"""Check a run record against its graph: dispatch envelope, request keys, resume frontier, folded verdict (ADR-0113, ADR-0114)."""
import hashlib, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from squad.verdict import EXIT, fold

WORST = ["fail", "deadline", "needs-human", "pass"]  # order of the join (ADR-0102 rule 9)

def canon_hash(doc):
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def limit_errors(req, disp, parent_tools, parent_spend_left):
    """Return the rules a spawn request breaks. Empty list means it fits the envelope."""
    a, b, r = disp["allow"], disp["budget"], req["requested"]
    e = []
    if req["depth"] > disp["max_depth"]: e.append("depth_over_max")
    if r["model"] not in a["models"]: e.append(f"model_outside_allow:{r['model']}")
    if r.get("effort") and r["effort"] not in a["efforts"]: e.append("effort_outside_allow")
    for t in r["tools"]:
        if t not in a["tools"]: e.append(f"tool_outside_allow:{t}")
        elif parent_tools is not None and t not in parent_tools: e.append(f"tool_outside_parent:{t}")
    if {"name": r["skill"]["name"], "version": r["skill"]["version"]} not in a["skills"]: e.append("skill_outside_allow")
    if not b["turns"]["min"] <= r["turns"] <= b["turns"]["max"]: e.append("turns_outside_budget")
    if not b["spend"]["min"] <= r["spend"] <= b["spend"]["max"]: e.append("spend_outside_budget")
    if r["spend"] > parent_spend_left: e.append("spend_over_remainder")
    if r["deadline"] > disp["deadline"]: e.append("deadline_over_dispatch")
    if r["workspace"] not in a["workspaces"]: e.append("workspace_outside_allow")
    return e

def check(graph, events):
    errs = []
    nodes = {n["name"]: n for n in graph["nodes"]}
    disp = {n["name"]: n for n in graph["nodes"] if n["kind"] == "dispatch"}
    d = next(iter(disp.values())) if disp else None
    for i, ev in enumerate(events, 1):
        if ev["pos"] != i: errs.append(f"line {i}: pos {ev['pos']} differs from the line number")
    if events[0]["event"] != "run_start": errs.append("first event is not run_start")
    elif events[0]["graph_hash"] != canon_hash(graph): errs.append("run_start graph_hash differs from the graph")
    reqs, accepted, rejected, outcomes = {}, {}, {}, {}
    for ev in events:
        k = ev["event"]
        if k == "spawn_requested": reqs[ev["pos"]] = ev
        elif k == "spawn_accepted": accepted[ev["request_key"]] = ev
        elif k == "spawn_rejected": rejected[ev["request_key"]] = ev
        elif k == "outcome": outcomes.setdefault(ev["node"], []).append(ev)
    if d is None: return errs, [], None
    spent = 0.0
    granted = {}
    for key, ev in sorted(accepted.items()):
        q = reqs.get(key)
        if q is None or key >= ev["pos"]: errs.append(f"accepted {key}: no earlier request"); continue
        if key not in {e["pos"] for e in events if e["event"] == "spawn_requested"}: errs.append(f"accepted {key}: key is not a record position")
        parent = ev["parent"]
        ptools = granted.get(parent)          # None for the entry agent: only allow applies
        left = graph["run"]["spend_cap"] - spent
        errs += [f"accepted {key}: {x}" for x in limit_errors(q, d, ptools, left)]
        if parent != d["entry"] and parent not in granted: errs.append(f"accepted {key}: parent {parent} is not an accepted child")
        if ev["depth"] != (1 if parent == d["entry"] else q["depth"]) or ev["depth"] != q["depth"]: errs.append(f"accepted {key}: depth mismatch")
        if parent != d["entry"] and ev["depth"] != next(x["depth"] for x in accepted.values() if x["child"] == parent) + 1: errs.append(f"accepted {key}: depth is not parent depth + 1")
        for i in q["requested"].get("inputs", []):
            if not any(e["pos"] == i["pos"] and e["event"] == "outcome" and e.get("artifact") == i["artifact"] and i["pos"] < key for e in events):
                errs.append(f"accepted {key}: input {i['artifact']} is not an earlier artifact")
        granted[ev["child"]] = q["requested"]["tools"]
        spent += ev["reservation"]
    per_parent = {}
    for ev in accepted.values():
        k = (ev["parent"], ev.get("parent_entry"))
        per_parent[k] = per_parent.get(k, 0) + 1
        if "definition" not in ev: errs.append(f"accepted {ev['child']}: no full child definition (ADR-0114 rule 2)")
    for k, n in per_parent.items():
        if n > d["max_children"]: errs.append(f"{n} accepted children of {k[0]} entry {k[1]} exceed max_children {d['max_children']}")
    for key, ev in rejected.items():
        q = reqs.get(key)
        if q is None: errs.append(f"rejected {key}: no request"); continue
        if not limit_errors(q, d, granted.get(ev["parent"]), graph["run"]["spend_cap"] - spent): errs.append(f"rejected {key}: fits the envelope, so it was not over any limit")
    for key in reqs:
        if key not in accepted and key not in rejected: errs.append(f"request {key}: no accepted or rejected event")
    # resume frontier: replay up to each resume event
    frontier = None
    for ev in events:
        if ev["event"] != "resume": continue
        seen = {x["child"] for x in accepted.values() if x["pos"] < ev["pos"]}
        done = {e["node"] for e in events if e["event"] == "outcome" and e["pos"] < ev["pos"]}
        frontier = sorted(seen - done)
        if ev["frontier"] != frontier: errs.append(f"resume {ev['pos']}: lists {ev['frontier']} but the record gives {frontier}")
    # verdict fold (ADR-0114): dispatch joins its children worst-of; zero children is fail
    children = [x["child"] for x in accepted.values()]
    if not children: dres, reason = "fail", "no_dispatch"
    else:
        res = []
        for c in children:
            o = outcomes.get(c, [])
            res.append("fail" if not o else o[-1]["outcome"])
            if o and any(v != "pass" for v in o[-1].get("checks", {}).values()): res[-1] = "fail"
        dres = next((w for w in WORST if w in res), "pass"); reason = None
    rec = outcomes.get(d["name"], [{}])[-1].get("outcome")
    if rec and rec != dres: errs.append(f"dispatch outcome {rec} differs from the fold {dres}")
    verdict = fold([e for e in events if e["event"] != "verdict"]).verdict
    vs = [e for e in events if e["event"] == "verdict"]
    if not vs: errs.append("no verdict event")
    else:
        if vs[-1]["verdict"] != verdict: errs.append(f"verdict {vs[-1]['verdict']} differs from the fold {verdict}")
        if vs[-1]["exit_code"] != EXIT[verdict]: errs.append("exit code differs from the verdict")
    return errs, frontier, verdict

if __name__ == "__main__":
    graph = json.load(open(sys.argv[1])); events = [json.loads(l) for l in open(sys.argv[2]) if l.strip()]
    errs, frontier, verdict = check(graph, events)
    if errs: print("\n".join(errs)); sys.exit(1)
    acc = sum(e["event"] == "spawn_accepted" for e in events); rej = sum(e["event"] == "spawn_rejected" for e in events)
    print(f"OK: {len(events)} events, {acc} accepted, {rej} rejected, resume frontier {frontier}, verdict {verdict}")
