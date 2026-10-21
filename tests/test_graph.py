"""Guards for the graph rules."""
import json

import pytest

from squad import graph, skills
from squad.envelope import validate_request
from tests.helpers import DESIGN, five, load_doc, node, route, router


def errors(doc):
    return [str(e) for e in graph.validate(doc)]


def has(doc, *words):
    errs = errors(doc)
    assert any(all(w in e for w in words) for e in errs), errs


def test_both_design_fixtures_load_and_validate():
    for name in ("five-phases", "router"):
        g = graph.load(DESIGN / f"{name}.graph.json")
        assert g.root == "main" and len(g.hash) == 64


def test_false_positive_valid_documents_raise_no_error():
    assert errors(five()) == [] and errors(router()) == []


# --- kinds, gate rules ---
def test_refuses_a_fifth_kind_and_an_eighth_kind():
    for kind in ("subagent", "router"):
        d = five()
        node(d, "scout")["kind"] = kind
        has(d, "scout", "seven kinds")


def test_refuses_a_when_on_a_gate():
    d = five()
    node(d, "spec_gate")["when"] = {"fact": "tier", "op": "eq", "value": "large"}
    has(d, "spec_gate", "never carries a when")


def test_refuses_an_adapt_block_on_a_gate():
    d = five()
    node(d, "spec_gate")["adapt"] = {"kinds": ["set_model"], "models": ["x"]}
    has(d, "spec_gate", "never adapts")


def test_refuses_a_gate_next_that_names_a_node():
    d = five()
    node(d, "spec_gate")["next"] = {"fail": "spec_draft"}
    has(d, "spec_gate", "never names a node")


# --- when holds one predicate ---
def test_refuses_a_when_with_two_predicates():
    d = five()
    node(d, "spec")["when"] = [{"fact": "tier", "op": "eq", "value": "large"}, {"fact": "git", "op": "eq", "value": True}]
    assert any("spec" in e or "nodes" in e for e in errors(d))
    d = five()
    node(d, "spec")["when"]["all"] = []
    has(d, "spec", "when")


def test_refuses_a_loop_with_a_when_that_holds_only_gates():
    d = five()
    n = node(d, "spec")
    n["children"] = ["spec_gate"]
    node(d, "spec_draft")["name"] = "spec_draft"
    has(d, "spec", "must hold an agent")


# --- deadlines, workspaces ---
def test_refuses_a_deadline_on_a_gate_and_on_a_loop():
    d = five()
    node(d, "spec_gate")["deadline"] = 60
    has(d, "spec_gate", "no deadline")
    d = five()
    node(d, "spec")["deadline"] = 60
    has(d, "spec", "no deadline")


def test_refuses_a_node_with_no_effective_deadline():
    d = five()
    del d["run"]["deadline"]
    has(d, "deadline")


def test_refuses_parallel_items_sharing_a_workspace():
    d = five()
    node(d, "actor")["workspace"] = "worktrees/shared"
    has(d, "execute", "share workspace")
    d = five()
    extra = {"name": "worktree2", "kind": "action", "effect": "worktree_create", "workspace": "worktrees/item"}
    d["nodes"].append(extra)
    node(d, "execute")["children"].append("worktree2")
    has(d, "execute", "share workspace", "worktree2")


def test_accepts_a_sequence_each_on_one_workspace():
    d = five()
    n = node(d, "execute")
    n["mode"] = "sequence"
    node(d, "actor")["workspace"] = "."
    assert errors(d) == []


# --- proposal kinds, ranges ---
def test_refuses_a_seventh_proposal_kind():
    d = five()
    node(d, "spec_draft")["adapt"]["kinds"].append("set_tier")
    has(d, "spec_draft", "set_tier")


def test_refuses_an_adapt_range_that_widens_a_declared_bound():
    d = five()
    node(d, "actor")["adapt"]["tools"].append("WebFetch")
    has(d, "actor", "widen")
    d = five()
    node(d, "spec_draft")["adapt"]["models"] = ["sonnet"]
    has(d, "spec_draft", "exclude the node's own model")


def test_refuses_shrink_loop_max_on_an_agent_and_a_model_kind_on_a_loop():
    d = five()
    node(d, "actor")["adapt"]["kinds"].append("shrink_loop_max")
    has(d, "actor", "non-loop")
    d = five()
    node(d, "work")["adapt"]["kinds"].append("set_model")
    has(d, "work", "non-agent")


# --- facts registry ---
def test_refuses_an_unregistered_fact_in_a_when_and_a_sees_name():
    d = five()
    node(d, "spec")["when"]["fact"] = "mystery"
    has(d, "spec", "unregistered fact mystery")
    d = five()
    node(d, "spec_draft")["adapt"]["sees"] = ["mystery"]
    has(d, "spec_draft", "sees unregistered fact mystery")


def test_refuses_tier_in_a_sees_list_but_reads_it_in_a_when():
    d = five()
    node(d, "spec_draft")["adapt"]["sees"] = ["tier"]
    has(d, "spec_draft", "tier")
    assert errors(five()) == []  # the same tier is read by the spec loop's when


@pytest.mark.parametrize("name,instrument", [
    ("gate_pass_rate", "code: share of passing gate runs"),
    ("gate_finding_count", "code: count of gate findings"),
    ("model_self_report", "code: what the model says about itself"),
    ("judge_score", "code: a judge scores the diff"),
])
def test_refuses_each_forbidden_trigger_example(name, instrument):
    d = five()
    d["facts"].append({"name": name, "type": "int", "instrument": instrument, "source_kind": "run_record"})
    has(d, name)


def test_refuses_a_fact_whose_instrument_is_a_model():
    d = five()
    d["facts"].append({"name": "size_guess", "type": "int", "instrument": "a model estimates the size", "source_kind": "artifact"})
    has(d, "size_guess", "must be code")


def test_refuses_a_fact_sourced_from_the_adaptation_report_or_another_run():
    for kind in ("adaptation_report", "other_run"):
        d = five()
        d["facts"].append({"name": "helped", "type": "bool", "instrument": "code: x", "source_kind": kind})
        assert any("source_kind" in e or "helped" in e for e in errors(d)), kind


def test_refuses_a_fact_instrument_that_names_the_report_or_a_past_run():
    for text in ("code: read the adaptation report", "code: count from a previous run"):
        d = five()
        d["facts"].append({"name": "x_fact", "type": "int", "instrument": text, "source_kind": "run_record"})
        has(d, "x_fact")


# --- names, registry, schema version ---
def test_refuses_stop_after_naming_an_unknown_node():
    d = five()
    assert graph.check_node_names(d, ["scout", "execute"]) == []
    assert [str(e) for e in graph.check_node_names(d, ["nowhere"])] == ["unknown node name 'nowhere'"]


def test_refuses_a_gate_check_id_outside_the_registry():
    d = five()
    node(d, "spec_gate")["check"] = "nope"
    has(d, "spec_gate", "unknown check nope")
    d = five()
    d["gates"].append({"id": "made-up", "params": []})
    has(d, "made-up", "outside the engine registry")


def test_refuses_a_document_with_no_schema_version():
    d = five()
    del d["schema_version"]
    has(d, "schema_version")


def test_refuses_a_when_over_a_fact_of_the_wrong_type_or_value():
    d = five()
    node(d, "spec")["when"] = {"fact": "tier", "op": "eq", "value": "enormous"}
    has(d, "spec", "not a value of fact tier")


# --- caps ---
def test_refuses_a_run_cap_below_an_agent_spend_minimum():
    d = five()
    d["run"]["spend_cap"] = 0.15
    has(d, "run spend cap")


# --- model resolution opt-in ---
def test_a_graph_asks_for_resolution_with_its_models_block():
    """ADR-0147 rule 8: resolution is applied only when the graph opts in with `models.resolve`."""
    d = five()
    assert errors(d) == []
    d["models"] = {"resolve": True}
    assert errors(d) == []
    d["models"] = {"resolve": "yes"}
    assert errors(d)


# --- adaptation report, learnings ---
def test_adaptation_report_is_never_a_registered_source():
    schema = json.loads((DESIGN / "graph.schema.json").read_text())
    assert schema["$defs"]["fact"]["properties"]["source_kind"]["enum"] == ["artifact", "run_record", "target"]


def test_a_cross_run_fact_is_refused_at_load():
    d = five()
    d["facts"].append({"name": "prior_failures", "type": "int", "instrument": "code: failures counted in another run", "source_kind": "run_record"})
    has(d, "prior_failures")


# --- hash and layout ---
def test_moving_a_layout_leaves_the_hash_equal(tmp_path):
    d = five()
    p = tmp_path / "flow.graph.json"
    p.write_text(json.dumps(d))
    h1 = graph.load(p).hash
    (tmp_path / "flow.layout.json").write_text(json.dumps({"scout": {"x": 1, "y": 2}}))
    g2 = graph.load(p)
    (tmp_path / "flow.layout.json").write_text(json.dumps({"scout": {"x": 500, "y": 900}}))
    g3 = graph.load(p)
    assert h1 == g2.hash == g3.hash and g2.layout != g3.layout


def test_changing_a_node_changes_the_hash():
    a, b = five(), five()
    node(b, "scout")["deadline"] = 601
    assert graph.canon_hash(a) != graph.canon_hash(b)


def test_a_layout_key_inside_the_document_is_refused():
    d = five()
    d["layout"] = {"scout": {"x": 1}}
    has(d, "layout")


# --- every gate a graph declares is reached by a walk (ADR-0100: a gate is never skipped) ---
SHIPPED_GRAPHS = (DESIGN / "router.graph.json", DESIGN / "five-phases.graph.json", DESIGN.parent / "workflows" / "spec-to-build.graph.json")


def reached(doc):
    """Every node a walk from the root enters: a loop's children, and a dispatch's entry, checks and review."""
    nodes, seen, stack = {n["name"]: n for n in doc["nodes"]}, set(), [doc["root"]]
    while stack:
        n = nodes[stack.pop()]
        if n["name"] in seen:
            continue
        seen.add(n["name"])
        stack += n.get("children", [])
        if n["kind"] == "dispatch":
            stack += [n["entry"], *n["checks"], *([n["review"]] if "review" in n else [])]
    return seen


def test_every_gate_a_shipped_graph_declares_is_reached_from_the_root():
    """#731: `main` held only `route` and `final_gate`, and `scope_gate`, `security_gate` and `review_gate` sat in no loop's
    children: only the dispatch's `checks` and `review` reached them. A gate no walk enters is a check the graph declares
    and nothing runs, which a reader sees as "not run"."""
    for p in SHIPPED_GRAPHS:
        doc = json.loads(p.read_text())
        gates = {n["name"] for n in doc["nodes"] if n["kind"] == "gate"}
        assert gates and gates <= reached(doc), (p.name, sorted(gates - reached(doc)))


def test_refuses_a_gate_that_no_loop_and_no_dispatch_reaches():
    d = five()
    node(d, "spec")["children"].remove("spec_gate")
    has(d, "spec_gate", "orphan")
    d = router()
    del node(d, "route")["review"]
    d["nodes"] = [n for n in d["nodes"] if n["name"] != "review_gate"]
    assert errors(d) == []          # the gate is gone from the document: nothing declares a check nothing reaches
    d = router()
    node(d, "route")["checks"] = ["scope_gate"]
    has(d, "security_gate", "orphan")   # declared, out of every loop, named by no dispatch: no walk enters it


# --- dispatch ---
def test_refuses_a_dispatch_with_no_checks():
    d = router()
    del node(d, "route")["checks"]
    has(d, "route", "checks")
    d = router()
    node(d, "route")["checks"] = []
    has(d, "route", "checks")


def test_a_spawn_value_outside_allow_is_rejected_whole_with_its_rule():
    disp = route()
    ok = {"prompt": "go", "skill": {"name": "tdd"}, "model": node(router(), "route")["allow"]["models"][0], "tools": ["Read"]}
    _, errs = validate_request(disp, ok, 1, None, 10.0)
    assert errs == []
    cases = [({"model": "gpt"}, "model_outside_allow:gpt"), ({"tools": ["WebFetch"]}, "tool_outside_allow:WebFetch"),
             ({"skill": {"name": "tdd", "version": "2"}}, "skill_pin_mismatch:tdd"), ({"stalls": 999}, "stalls_outside_budget"),
             ({"spend": 99.0}, "spend_outside_budget"), ({"deadline": 99999}, "deadline_over_dispatch"),
             ({"workspace": "/etc"}, "workspace_outside_allow:requested '/etc', allowed ['worktrees/child']"), ({"effort": "extreme"}, "effort_outside_allow")]
    for patch, rule in cases:
        _, errs = validate_request(disp, {**ok, **patch}, 1, None, 10.0)
        assert rule in errs, (patch, errs)
    _, errs = validate_request(disp, ok, 3, None, 10.0)
    assert "depth_over_max" in errs


def test_a_workspace_inside_an_allowed_one_is_inside_the_envelope():
    """The author allows `worktrees/child`; the router names each child's own path below it (`worktrees/child/gui-26`).
    The rule read the value as a string, so an allowed envelope rejected every child that named the directory it works in:
    three children of one frontier run, and the run folded to fail `no_dispatch, spawn_rejected:workspace_outside_allow`
    with nothing delivered (#55)."""
    disp = route()
    ok = {"prompt": "go", "skill": {"name": "tdd"}, "model": node(router(), "route")["allow"]["models"][0], "tools": ["Read"]}
    for w in ("worktrees/child", "worktrees/child/gui-26", "worktrees/child/gui-26/", "worktrees/child/a/b"):
        _, errs = validate_request(disp, {**ok, "workspace": w}, 1, None, 10.0)
        assert errs == [], (w, errs)
    for w in ("worktrees", "worktrees/childish", "worktrees/child/../other", "worktrees/child/..",
              "../worktrees/child", "/worktrees/child", "/etc"):
        _, errs = validate_request(disp, {**ok, "workspace": w}, 1, None, 10.0)
        assert any(e.startswith("workspace_outside_allow") for e in errs), (w, errs)   # the refusal quotes it (#55)


def test_a_model_supplied_identity_in_a_spawn_request_is_refused():
    disp = route()
    for key in ("id", "child", "request_key", "name"):
        _, errs = validate_request(disp, {"prompt": "go", "skill": "tdd", key: "x"}, 1, None, 10.0)
        assert f"model_supplied_identity:{key}" in errs


def test_skill_name_alone_floats_to_the_current_hash():
    disp = route()
    req, errs = validate_request(disp, {"prompt": "go", "skill": "tdd"}, 1, None, 10.0)
    assert errs == [] and req["skill"] == {"name": "tdd", "version": skills.load()["tdd"]["hash"]}


def _agents(doc):
    return [n["name"] for n in doc["nodes"] if n["kind"] == "agent"]


def test_refuses_a_node_that_reads_a_file_another_node_writes():
    d = five()
    a, b = _agents(d)[:2]
    node(d, a)["writes"] = ["docs/notes.md"]
    node(d, b)["reads"] = ["docs/notes.md"]
    has(d, b, "docs/notes.md", "never written by the graph")


def test_accepts_a_declared_read_that_no_node_writes_and_the_fixtures_still_load():
    d = five()
    a, b = _agents(d)[:2]
    node(d, a)["writes"] = ["docs/out.md"]
    node(d, b)["reads"] = ["docs/guideline.md"]
    assert errors(d) == []
    for name in ("five-phases", "router"):
        assert graph.validate(load_doc(name)) == []


def test_reads_and_writes_are_lists_of_nonempty_strings_and_change_the_hash():
    d = five()
    a = _agents(d)[0]
    node(d, a)["reads"] = [""]
    has(d, "reads")
    h0 = graph.from_doc(five()).hash
    d2 = five()
    node(d2, a)["reads"] = ["docs/guideline.md"]
    assert graph.from_doc(d2).hash != h0


# --- control is order-based; next takes reserved words only ---
def test_refuses_a_next_that_names_a_node_on_every_node_kind():
    kinds_seen = set()
    for make in (five, router):
        for n in make()["nodes"]:
            d = make()
            target = next(x for x in d["nodes"] if x["name"] == n["name"])
            other = next(x["name"] for x in d["nodes"] if x["name"] != n["name"])
            target["next"] = {"pass": other}
            assert errors(d), f"{n['name']} ({n['kind']}) accepted a next that names a node"
            kinds_seen.add(n["kind"])
    assert {"agent", "gate", "loop", "action"} <= kinds_seen


def test_the_next_words_are_the_six_reserved_words():
    schema = json.loads((DESIGN / "graph.schema.json").read_text())
    assert schema["$defs"]["next_word"]["enum"] == [
        "repeat", "continue", "exit:pass", "exit:fail", "exit:deadline", "exit:needs-human"]


@pytest.mark.parametrize("pin", ["1", "ABC", "a" * 63, "g" * 64])
def test_a_malformed_skill_pin_is_refused_and_a_hash_or_no_pin_passes(pin):
    d = router()
    node(d, "route")["allow"]["skills"] = [{"name": "tdd", "version": pin}]
    has(d, "version")
    node(d, "route")["allow"]["skills"] = [{"name": "tdd", "version": skills.load()["tdd"]["hash"]}]
    assert errors(d) == []
    node(d, "route")["allow"]["skills"] = [{"name": "tdd"}]
    assert errors(d) == []
