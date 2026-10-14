"""Parity guards: the dashboard core and the engine share one schema, one set of rules, one hash, and one import rule."""
import ast
import copy
import json
import shutil
import sys

import pytest

from squad import graph
from tests.dashboard_helpers import ROOT, SCHEMA_ERR, js_pairs, pair, run_node
from tests.helpers import DESIGN, five, node, router


# --- parity: same schema and rules as the engine ---
def test_valid_fixtures_raise_no_error_in_the_dashboard():
    assert run_node([{"op": "validate", "doc": five()}, {"op": "validate", "doc": router()}]) == [[], []]


WAIT = {"name": "stray", "kind": "wait", "reason": "x", "next": {}}


def _rule_cases():
    """One mutation per rule: every GraphError site of squad/graph.py has a case here."""
    c = []
    add = lambda label, d: c.append((label, d))  # noqa: E731
    d = five(); d["layout"] = {"scout": {"x": 1}}; add("layout key", d)
    d = five(); del d["schema_version"]; add("no schema_version", d)
    d = five(); d["nodes"].append(copy.deepcopy(node(d, "scout"))); add("duplicate name", d)
    d = five(); d["nodes"].append("x"); add("node not an object", d)
    d = five(); node(d, "main")["children"].append("ghost"); add("unknown child", d)
    d = five(); node(d, "spec")["children"].append("scout"); add("two parents", d)
    d = five(); d["nodes"].append(copy.deepcopy(WAIT)); add("orphan", d)
    d = five(); d["root"] = "scout"; add("root not a loop", d)
    d = five(); node(d, "spec")["when"] = {"fact": "nope", "op": "eq", "value": True}; add("unregistered fact", d)
    d = five(); node(d, "spec")["when"] = {"fact": "outcome:ghost", "op": "eq", "value": "pass"}; add("when unknown node", d)
    d = five(); node(d, "spec")["when"] = {"fact": "tier", "op": "eq", "value": "huge"}; add("bad enum value", d)
    d = five(); d["facts"].append({"name": "flag", "type": "bool", "instrument": "code: x", "source_kind": "artifact"}); node(d, "spec")["when"] = {"fact": "flag", "op": "eq", "value": 3}; add("bool value", d)
    d = five(); d["facts"].append({"name": "cnt", "type": "int", "instrument": "code: x", "source_kind": "artifact"}); node(d, "spec")["when"] = {"fact": "cnt", "op": "eq", "value": "a"}; add("numeric value", d)
    d = five(); node(d, "spec_gate")["check"] = "ghost-check"; add("unknown gate check", d)
    d = five(); node(d, "spec_gate")["params"] = {"x": 1}; add("gate params differ", d)
    d = five(); d["gates"].append({"id": "ghost", "params": []}); add("registry check outside", d)
    d = five(); d["gates"][1]["params"] = ["other"]; add("registry params differ", d)
    d = five(); node(d, "execute")["items"] = "nope"; add("each items unregistered", d)
    d = five(); node(d, "actor")["workspace"] = "worktrees/shared"; add("shared workspace", d)
    d = five(); node(d, "merge")["effect"] = "worktree_create"; node(d, "merge")["workspace"] = node(d, "worktree").get("workspace"); add("two worktree_create share", d)
    d = five(); node(d, "spec_gate")["when"] = {"fact": "tier", "op": "eq", "value": "large"}; add("gate when", d)
    d = five(); node(d, "spec_gate")["adapt"] = {"kinds": ["narrow_tools"]}; add("gate adapt", d)
    d = five(); node(d, "spec_gate")["deadline"] = 60; add("gate deadline", d)
    d = five(); node(d, "spec_gate")["next"] = {"fail": "spec_draft"}; add("gate next names a node", d)
    d = five(); node(d, "spec")["deadline"] = 60; add("loop deadline", d)
    d = five(); node(d, "scout")["deadline"] = 999999; add("deadline above run", d)
    d = five(); node(d, "worktree")["next"] = {"pass": "repeat"}; add("repeat outside loop", d)
    d = five(); d["run"]["spend_cap"] = 0.01; add("spend cap below minimum", d)
    d = five(); node(d, "scout")["kind"] = "subagent"; add("fifth kind", d)
    d = five(); node(d, "scout")["budget"]["stalls"] = {"min": 50, "max": 5}; add("agent turns min above max", d)
    d = five(); node(d, "scout")["budget"]["spend"] = {"min": 5, "max": 0.5}; add("agent spend min above max", d)
    d = five(); node(d, "scout")["may_spawn"] = True; add("may_spawn not an entry", d)
    d = five(); node(d, "scout")["may_propose"] = True; add("may_propose without adapt", d)
    d = five(); node(d, "main")["max"] = {"min": 9, "value": 2}; add("loop max min above value", d)
    d = five(); node(d, "work")["children"] = [x for x in node(d, "work")["children"] if node(d, x)["kind"] == "gate"]; node(d, "work")["when"] = {"fact": "tier", "op": "eq", "value": "large"}; add("loop when without worker child", d)
    d = five(); node(d, "actor")["adapt"]["tools"].append("WebFetch"); add("adapt widens tools", d)
    d = five(); node(d, "actor")["adapt"]["models"] = ["other-model"]; add("adapt excludes model", d)
    d = five(); node(d, "actor")["adapt"]["efforts"] = ["nope"]; node(d, "actor")["effort"] = "medium"; add("adapt excludes effort", d)
    d = five(); node(d, "actor")["adapt"]["sees"] = ["tier"]; add("sees forbidden", d)
    d = five(); node(d, "actor")["adapt"]["sees"] = ["ghost_fact"]; add("sees unregistered", d)
    d = five(); node(d, "actor")["adapt"]["kinds"] = ["shrink_loop_max"]; add("shrink_loop_max on agent", d)
    d = five(); node(d, "main")["adapt"] = {"kinds": ["set_model"], "models": ["x"]}; add("agent kind on loop", d)
    d = five(); node(d, "actor")["adapt"]["kinds"] = ["set_effort"]; node(d, "actor")["adapt"].pop("efforts", None); add("adapt kind needs list", d)
    d = five(); d["nodes"][d["nodes"].index(node(d, "scout"))]["adapt"] = {"kinds": ["narrow_tools"], "tools": []}; add("adapt on a non-listed", d)
    d = five(); d["facts"].append({"name": "pass_rate", "type": "int", "instrument": "code: x", "source_kind": "artifact"}); add("forbidden fact name", d)
    d = five(); d["facts"].append({"name": "ok_fact", "type": "int", "instrument": "a model judges", "source_kind": "artifact"}); add("instrument not code", d)
    d = five(); d["facts"].append({"name": "ok_fact", "type": "int", "instrument": "code: ask the judge", "source_kind": "artifact"}); add("instrument names a judge", d)
    d = five(); d["facts"].append(copy.deepcopy(d["facts"][0])); add("fact twice", d)
    d = five(); node(d, "scout")["reads"] = ["a.md"]; node(d, "spec_draft")["writes"] = ["a.md"]; add("reads a written file", d)
    d = router(); del node(d, "route")["checks"]; add("dispatch no checks", d)
    d = router(); node(d, "route")["checks"] = ["router"]; add("dispatch check not a gate", d)
    d = router(); node(d, "route")["checks"] = ["ghost"]; add("dispatch unknown check", d)
    d = router(); node(d, "route")["entry"] = "scope_gate"; add("dispatch entry not an agent", d)
    d = router(); node(d, "route")["max_parallel"] = 99; add("dispatch max_parallel", d)
    d = router(); node(d, "route")["budget"]["stalls"] = {"min": 9, "max": 1}; add("dispatch turns min above max", d)
    d = router(); node(d, "route")["budget"]["spend"] = {"min": 9, "max": 1}; add("dispatch spend min above max", d)
    d = router(); node(d, "route")["allow"]["skills"].append(copy.deepcopy(node(d, "route")["allow"]["skills"][0])); add("dispatch duplicate skill", d)
    d = router(); node(d, "router")["may_spawn"] = False; add("entry lacks may_spawn", d)
    d = router(); node(d, "router")["when"] = {"fact": "git", "op": "eq", "value": True}; add("entry carries a when", d)
    d = router(); node(d, "main")["children"].append("router"); add("dispatch two parents", d)
    return c


OUTSIDE_VALIDATE = {"check_node_names", "load"}  # not part of validate(); the dashboard has no twin for them


def _field_cases():
    """Every top-level field and every first-level subfield of every node of both fixtures, under five mutations."""
    cases = []
    swaps = {"delete": None, "number": 12345, "string": "x", "list": [], "null": None, "object": {}}
    for base_name, base in (("five", five), ("router", router)):
        for n in base()["nodes"]:
            paths = []
            for key, val in n.items():
                paths.append((key,))
                if isinstance(val, dict):
                    paths += [(key, sub) for sub in val]
            for path in paths:
                for how, value in swaps.items():
                    if how == "delete" and path == ("name",):
                        continue
                    d = base()
                    t = node(d, n["name"])
                    for p in path[:-1]:
                        t = t[p]
                    if how == "delete":
                        del t[path[-1]]
                    else:
                        t[path[-1]] = copy.deepcopy(value)
                    cases.append((f"{base_name}:{n['name']}:{'.'.join(path)}:{how}", d))
    return cases


@pytest.mark.slow(reason="about 1500 documents through node and the engine: 9 s alone, and parallel workers share the CPU")
@pytest.mark.heavy
def test_dashboard_validation_parity():
    """The dashboard and the engine give the same (node, rule) pairs and the same hash on both fixtures, one mutation per rule, and every node-field mutation."""
    rules, fields = _rule_cases(), _field_cases()
    cases = [("fixture:five", five()), ("fixture:router", router())] + rules + fields
    res = run_node([{"op": "validate", "doc": d} for _, d in cases])
    errs = [graph.validate(d) for _, d in cases]  # the engine runs once per document; every check below reuses this
    py = [{pair(e.node, e.message) for e in es} for es in errs]
    bad = [(label, sorted(p - js_pairs(js)), sorted(js_pairs(js) - p)) for (label, _), js, p in zip(cases, res, py, strict=False) if p != js_pairs(js)]
    assert not bad, f"{len(bad)} of {len(cases)} cases differ: {bad[:5]}"
    # every rule case is a real error, and the field cases hit many rules, so the check is not trivial
    rule_py, field_py = py[2:2 + len(rules)], py[2 + len(rules):]
    assert all(rule_py), [label for (label, _), p in zip(rules, rule_py, strict=False) if not p]
    assert len(fields) > 1000 and sum(1 for p in field_py if p) > 500
    # a rule message of the engine reaches the dashboard in the engine's own words (cross-reference rules only; library text differs)
    skip = ("layout key", "no schema_version", "fifth kind")
    xref = [(label, d) for label, d in rules if label not in skip]
    msgs = [{(e.node or "", e.message) for e in es if not SCHEMA_ERR.match(e.message)} for (label, _), es in zip(rules, errs[2:2 + len(rules)], strict=False) if label not in skip]
    got = [{(e["node"] or "", e["message"]) for e in js if not SCHEMA_ERR.match(e["message"])} for js in run_node([{"op": "validate", "doc": d} for _, d in xref])]
    assert msgs == got
    hashes = [graph.canon_hash(d) for _, d in cases[:60]]
    jsh = run_node([{"op": "hash", "text": json.dumps(d)} for _, d in cases[:60]])
    assert hashes == jsh


def test_every_engine_rule_site_has_a_mutation_case(monkeypatch):
    """Every GraphError site of squad/graph.py fires on some rule case. A new site fails here until a case and its JS twin exist."""
    tree = ast.parse((ROOT / "squad" / "graph.py").read_text())
    sites = {c.lineno for f in ast.walk(tree) if isinstance(f, ast.FunctionDef) and f.name not in OUTSIDE_VALIDATE
             for c in ast.walk(f) if isinstance(c, ast.Call) and getattr(c.func, "id", "") == "GraphError"}
    hit, real = set(), graph.GraphError

    def spy(*a, **k):
        hit.add(sys._getframe(1).f_lineno)
        return real(*a, **k)

    monkeypatch.setattr(graph, "GraphError", spy)
    for _, d in _rule_cases() + [("doc not an object", [])]:
        graph.validate(d)
    # a call can span lines, so match a hit to the site that starts at or just before it
    covered = {s for s in sites if any(s <= h <= s + 3 for h in hit)}
    assert sites == covered, sorted(sites - covered)
    

def test_the_parity_guard_fails_and_never_skips_when_node_is_missing(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(AssertionError, match="never skips"):
        test_valid_fixtures_raise_no_error_in_the_dashboard()


# --- the hash: the overlay shows only when the hash matches the engine's ---
def test_the_dashboard_hash_equals_the_engine_hash_on_both_fixtures():
    for name in ("five-phases", "router"):
        text = (DESIGN / f"{name}.graph.json").read_text()
        assert run_node([{"op": "hash", "text": text}])[0] == graph.canon_hash(json.loads(text))


def test_the_canonical_form_matches_python_on_floats_unicode_and_key_order():
    text = '{"b":[2.0,1e-7,1.5e20,-0.0,"\\u00e9\\ud83d\\ude00\\n\\u007f",1e16,0.1,123456789012345678901234567890,-5,2.50],"a":{"z":null,"y":true,"A":0.0001,"\\u00e9":1},"c":[]}'
    want = json.dumps(json.loads(text), sort_keys=True, separators=(",", ":"))
    assert run_node([{"op": "canon", "text": text}])[0] == want


def test_changing_a_node_changes_the_dashboard_hash_and_a_key_order_change_does_not():
    base = (DESIGN / "five-phases.graph.json").read_text()
    d = json.loads(base)
    reordered = json.dumps(dict(reversed(list(d.items()))), indent=4)
    d2 = json.loads(base)
    node(d2, "scout")["deadline"] = 601
    h = run_node([{"op": "hash", "text": base}, {"op": "hash", "text": reordered}, {"op": "hash", "text": json.dumps(d2)}])
    assert h[0] == h[1] != h[2]


# --- import ---
def test_import_refuses_unparseable_and_schema_less_files_and_loads_an_invalid_graph():
    bad = run_node([{"op": "import", "text": "{nope"}, {"op": "import", "text": '{"name": "x"}'}, {"op": "import", "text": "[1]"}])
    assert all("error" in r for r in bad)
    d = five(); node(d, "spec_gate")["when"] = {"fact": "tier", "op": "eq", "value": "large"}
    ok = run_node([{"op": "import", "text": json.dumps(d)}])[0]
    assert "doc" in ok and run_node([{"op": "validate", "doc": ok["doc"]}])[0]  # loads, and the errors show on the nodes
