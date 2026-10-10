"""Build five-phases.graph.json and five-phases.mmd. Design hand-off, not runtime code."""
import json


def rng(a, b): return {"min": a, "max": b}
def bud(t0, t1, s0, s1): return {"stalls": rng(t0, t1), "spend": rng(s0, s1)}
def agent(name, model, tools, b, dl, **kw):
    n = {"name": name, "kind": "agent", "model": model, "tools": tools, "budget": b, "deadline": dl}
    n.update(kw); return n
def gate(name, check, params, fail="repeat"):
    return {"name": name, "kind": "gate", "check": check, "params": params, "next": {"fail": fail}}
def loop(name, mx, children, **kw):
    n = {"name": name, "kind": "loop", "max": {"value": mx, "min": 1}, "children": children}
    n.update(kw); return n
REP = {"fail": "repeat"}
tier_ne = {"fact": "tier", "op": "ne", "value": "trivial"}
def adapt(kinds, models=None, tools=None, sees=None):
    a = {"kinds": kinds}
    if models: a["models"] = models
    if "set_effort" in kinds: a["efforts"] = ["low", "medium", "high"]
    if tools is not None: a["tools"] = tools
    if sees: a["sees"] = sees
    return a

facts = [
 {"name": "tier", "type": "enum", "values": ["trivial", "medium", "large"], "instrument": "code: classify tier from scout.size_estimate; a missing estimate is large", "source_kind": "artifact"},
 {"name": "sensitive", "type": "bool", "instrument": "code: sensitive path set over scout.paths", "source_kind": "artifact"},
 {"name": "visual", "type": "bool", "instrument": "code: changed files and web app flag, computed before execute", "source_kind": "target"},
 {"name": "git", "type": "bool", "instrument": "code: git is available in the target", "source_kind": "target"},
 {"name": "work_items", "type": "int", "instrument": "code: length of decompose.work_items, or 1 when decompose did not run", "source_kind": "artifact"},
 {"name": "review_required", "type": "bool", "instrument": "code: derived from diff size and sensitive; replaces the size backstop gate", "source_kind": "artifact"},
 {"name": "blocking_learnings", "type": "int", "instrument": "code: count of blocking entries in the declared file learnings.json (no cross-run input)", "source_kind": "artifact"},
 {"name": "architecture_issues", "type": "int", "instrument": "code: count of architecture issues in the critic artifact", "source_kind": "run_record"},
 {"name": "run_spend", "type": "number", "instrument": "code: sum of SDK result spend over agent entries", "source_kind": "run_record"},
]
gates = [
 {"id": "spec-shape", "params": []},
 {"id": "ac-command-shape", "params": ["duplicate_warn_ratio"]},
 {"id": "plan-semantics", "params": ["max_work_items"]},
 {"id": "spend-floor", "params": ["min_remaining_usd"]},
 {"id": "scope", "params": ["allowed_generated"]},
 {"id": "security", "params": ["sensitive_paths"]},
 {"id": "ac-verify", "params": ["commands_from"]},
 {"id": "visual-guard", "params": []},
 {"id": "learning-check", "params": []},
]
RO = ["Read", "Grep", "Glob"]
nodes = [
 loop("main", 1, ["scout", "spec", "plan", "budget_gate", "execute", "verify", "ship"]),
 agent("scout", "sonnet", RO, bud(5, 40, 0.05, 2), 600, output_key="size_estimate", next=REP),
 loop("spec", 4, ["spec_draft", "spec_gate", "spec_proxy"], when=tier_ne),
 agent("spec_draft", "opus", ["Read", "Grep"], bud(5, 60, 0.1, 4), 900, output_key="spec", next=REP,
       adapt=adapt(["set_model", "set_effort", "shrink_stalls", "shrink_spend"], ["opus", "sonnet"], sees=["run_spend"]), may_propose=True),
 gate("spec_gate", "ac-command-shape", {"duplicate_warn_ratio": 0.4}),
 agent("spec_proxy", "opus", ["Read"], bud(3, 20, 0.05, 2), 600, next=REP),
 loop("plan", 4, ["decompose", "plan_gate", "plan_proxy"], when={"fact": "tier", "op": "eq", "value": "large"}),
 agent("decompose", "opus", ["Read", "Grep"], bud(5, 60, 0.1, 4), 900, output_key="work_items", next=REP),
 gate("plan_gate", "plan-semantics", {"max_work_items": 5}),
 agent("plan_proxy", "opus", ["Read"], bud(3, 20, 0.05, 2), 600, next=REP),
 gate("budget_gate", "spend-floor", {"min_remaining_usd": 1}, "exit:fail"),
 {"name": "execute", "kind": "each", "items": "work_items", "mode": "parallel", "max_parallel": 2, "join": "all",
  "children": ["worktree", "work", "merge", "manual_merge"], "deadline": 7200},
 {"name": "worktree", "kind": "action", "effect": "worktree_create", "workspace": "worktrees/item"},
 loop("work", 12, ["actor", "scope_gate", "security_gate", "critic"],
      adapt=adapt(["shrink_loop_max"])),
 agent("actor", "sonnet", ["Read", "Edit", "Write", "Bash"], bud(10, 120, 0.2, 8), 1800, output_key="diff",
       workspace="worktrees/item", next=REP,
       adapt=adapt(["set_model", "set_effort", "narrow_tools", "shrink_stalls", "shrink_spend"], ["sonnet", "haiku"], ["Read", "Edit", "Write"], ["sensitive", "run_spend"])),
 gate("scope_gate", "scope", {"allowed_generated": ["package-lock.json"]}),
 gate("security_gate", "security", {"sensitive_paths": ["auth", "crypto", ".env*"]}),
 agent("critic", "opus", ["Read", "Grep"], bud(3, 30, 0.05, 3), 900, next=REP,
       when={"fact": "review_required", "op": "eq", "value": True},
       adapt=adapt(["set_model", "shrink_stalls"], ["opus", "sonnet"], sees=["run_spend"])),
 {"name": "merge", "kind": "action", "effect": "merge_land", "workspace": "worktrees/item", "deadline": 300, "next": {"fail": "continue"}},
 {"name": "manual_merge", "kind": "wait", "owner": "rafael", "deadline": 86400, "default": "fail",
  "when": {"fact": "outcome:merge", "op": "eq", "value": "fail"}, "next": {"fail": "exit:fail"}},
 loop("verify", 1, ["ac_verify", "visual", "learning_gate", "audit_a", "audit_b", "audit_c"]),
 gate("ac_verify", "ac-verify", {"commands_from": ["spec", "scout"]}, "exit:fail"),
 loop("visual", 4, ["visual_actor", "visual_guard", "visual_critic"], when={"fact": "visual", "op": "eq", "value": True}),
 agent("visual_actor", "sonnet", ["Bash", "Read"], bud(5, 40, 0.05, 2), 600, next=REP),
 gate("visual_guard", "visual-guard", {}),
 agent("visual_critic", "opus", ["Read"], bud(3, 20, 0.05, 2), 600, next=REP),
 gate("learning_gate", "learning-check", {}, "exit:fail"),
]
for nm, f, v in [("audit_a", "blocking_learnings", 0), ("audit_b", "architecture_issues", 0), ("audit_c", "work_items", 1)]:
    nodes.append(agent(nm, "haiku", ["Read"], bud(1, 10, 0.01, 0.5), 300, when={"fact": f, "op": "gt", "value": v},
                       next={"fail": "continue", "deadline": "continue"}))
nodes += [
 loop("ship", 1, ["commit"]),
 {"name": "commit", "kind": "action", "effect": "commit", "workspace": ".", "deadline": 120,
  "when": {"fact": "git", "op": "eq", "value": True}},
]
doc = {"schema_version": 1, "name": "dev-squad-default",
       "description": "The five dev_squad phases in the graph vocabulary. Fixture for the schema.",
       "run": {"deadline": 21600, "max_parallel": 2, "spend_cap": 60},
       "facts": facts, "gates": gates, "root": "main", "nodes": nodes}
json.dump(doc, open("five-phases.graph.json", "w"), indent=2); open("five-phases.graph.json", "a").write("\n")

# Mermaid
by = {n["name"]: n for n in nodes}
def label(n):
    k = n["kind"]; s = n["name"]
    if k == "agent": s += "<br/>" + n["model"]
    if k == "gate": s += "<br/>gate: " + n["check"]
    if k == "wait": s += f"<br/>wait: {n['owner']}, {n['deadline']}s"
    if k == "action": s += "<br/>action: " + n["effect"]
    if "when" in n: w = n["when"]; s += f"<br/>when {w['fact']} {w['op']} {str(w['value']).lower()}"
    return s
out = ["flowchart TD"]
def emit(name, ind):
    n = by[name]; p = "  " * ind
    if n["kind"] in ("loop", "each"):
        extra = f"loop max {n['max']['value']}" if n["kind"] == "loop" else f"each {n['items']} {n['mode']}"
        if "when" in n: w = n["when"]; extra += f" when {w['fact']} {w['op']} {str(w['value']).lower()}"
        out.append(f'{p}subgraph {name}["{name} {extra}"]'); out.append(f"{p}  direction TB")
        for c in n["children"]: emit(c, ind + 1)
        out.append(f"{p}end")
    elif n["kind"] == "gate": out.append(f'{p}{name}{{{{"{label(n)}"}}}}')
    elif n["kind"] == "wait": out.append(f'{p}{name}(["{label(n)}"])')
    else: out.append(f'{p}{name}["{label(n)}"]')
emit("main", 0)
# order edges: consecutive siblings, plus explicit next words
def first(name):
    n = by[name]
    return first(n["children"][0]) if n["kind"] in ("loop", "each") else name
def last(name):
    n = by[name]
    return last(n["children"][-1]) if n["kind"] in ("loop", "each") else name
for n in nodes:
    if n["kind"] in ("loop", "each"):
        ch = n["children"]
        for a, b in zip(ch, ch[1:]): out.append(f"  {last(a)} --> {first(b)}")
    for oc, w in (n.get("next") or {}).items():
        if w == "repeat":
            tgt = n["name"]
            out.append(f"  {n['name']} -. {oc}: repeat .-> {tgt}") if n["kind"] == "agent" else out.append(f"  {n['name']} -. {oc}: repeat loop .-> {n['name']}")
        elif w.startswith("exit"): out.append(f"  {n['name']} -. {oc}: {w} .-> exit_{w.split(':')[1]}")
        else: out.append(f"  {n['name']} -. {oc}: {w} .-> {n['name']}")
out.append("  exit_fail((exit:fail))")
out.append("  classDef gate fill:#eee,stroke:#333,stroke-width:2px")
out.append("  classDef wait fill:#fff4d6,stroke:#333")
out.append("  classDef action fill:#dde8f8,stroke:#336")
for n in nodes:
    if n["kind"] in ("gate", "wait", "action"): out.append(f"  class {n['name']} {n['kind']}")
open("five-phases.mmd", "w").write("\n".join(out) + "\n")
