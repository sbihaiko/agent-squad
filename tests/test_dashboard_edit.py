"""Edit operations: add, remove, connect, rename, change kind, fields by purpose, and undo."""
import copy
import json
import re

from squad import graph
from squad.gates import GATE_REGISTRY
from tests.dashboard_helpers import js_pairs, page_bundle, page_template, py_pairs, run_node
from tests.helpers import five, node, router


# --- read-only fields ---
def test_a_gate_check_and_thresholds_cannot_be_edited_but_its_place_can():
    d = five()
    g = copy.deepcopy(node(d, "spec_gate"))
    changed_check = {**g, "check": "scope"}
    changed_params = {**g, "params": {**g["params"], "extra": 1}}
    moved = {**g, "next": {"fail": "exit:fail"}}
    res = run_node([{"op": "edit", "doc": d, "name": "spec_gate", "edited": e} for e in (changed_check, changed_params, moved)])
    assert "error" in res[0] and "error" in res[1] and "doc" in res[2]


def test_an_agent_edit_is_applied_and_the_name_and_kind_stay():
    d = five()
    a = copy.deepcopy(node(d, "scout"))
    res = run_node([{"op": "edit", "doc": d, "name": "scout", "edited": e} for e in ({**a, "deadline": 777}, {**a, "name": "other"}, {**a, "kind": "wait"})])
    assert node(res[0]["doc"], "scout")["deadline"] == 777 and "error" in res[1] and "error" in res[2]
    assert res[0]["doc"]["facts"] == d["facts"] and res[0]["doc"]["gates"] == d["gates"]


def test_the_page_has_no_path_that_writes_the_record_or_the_registries():
    page = page_template()
    assert "S.events =" in page and "S.events." not in page.replace("S.events.filter", "").replace("S.events.length", "")
    assert "doc.facts =" not in page and "doc.gates =" not in page and "SERVED." + "x =" not in page
    assert not re.search(r"\bSERVED(\.\w+|\[[^\]]*\])?\s*=[^=]", page_bundle())  # the snapshot is read, never assigned


# --- add and remove a node ---
KINDS = ["agent", "gate", "wait", "loop", "each", "action", "dispatch"]
NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def added(doc, kind, **opts):
    r = run_node([{"op": "add", "doc": doc, "kind": kind, "opts": opts}])[0]
    assert "doc" in r, r
    return r


def test_an_added_node_gets_the_rules_of_the_engine_whatever_its_kind():
    """The dashboard and the engine report the same (node, rule) pairs on the result of an add, for each of the seven kinds."""
    for kind in KINDS:
        r = added(five(), kind)
        js = run_node([{"op": "validate", "doc": r["doc"]}])[0]
        assert js_pairs(js) == py_pairs(r["doc"]), kind


def test_an_added_agent_wait_action_or_gate_leaves_the_graph_valid_and_the_others_show_their_gaps_on_the_new_node():
    for kind in ("agent", "wait", "action", "gate"):
        r = added(five(), kind)
        assert py_pairs(r["doc"]) == set(), kind
    for kind in ("loop", "each", "dispatch"):
        r = added(five(), kind)
        errs = graph.validate(r["doc"])
        assert errs and {e.node for e in errs} == {r["name"]}, kind


def test_an_added_dispatch_copies_the_limits_of_an_existing_one_and_nothing_that_routes_or_gates_it():
    d = router()
    node(d, "route")["next"] = {"fail": "exit:fail"}
    r = added(d, "dispatch")
    n, old = node(r["doc"], r["name"]), node(d, "route")
    assert n["allow"] == old["allow"] and n["budget"] == old["budget"] and "next" not in n and "when" not in n


def test_an_added_node_has_a_unique_legal_name_the_kind_and_a_place_under_the_root_loop():
    d = five()
    names = set()
    for _ in range(3):
        r = added(d, "agent")
        d = r["doc"]
        assert NAME_RE.match(r["name"]) and r["name"] not in names
        names.add(r["name"])
    assert len({n["name"] for n in d["nodes"]}) == len(d["nodes"])
    assert all(node(d, n)["kind"] == "agent" for n in names)
    assert set(names) <= set(node(d, d["root"])["children"])
    assert graph.validate(d) == []


def test_an_added_node_goes_under_the_chosen_loop_or_each_and_a_bad_parent_is_refused():
    r = added(five(), "action", parent="execute")
    assert r["name"] in node(r["doc"], "execute")["children"] and r["name"] not in node(r["doc"], "main")["children"]
    bad = run_node([{"op": "add", "doc": five(), "kind": "action", "opts": {"parent": "scout"}},
                    {"op": "add", "doc": five(), "kind": "action", "opts": {"parent": "ghost"}},
                    {"op": "add", "doc": five(), "kind": "banana"}])
    assert all("error" in b for b in bad)


def test_an_added_gate_picks_a_registered_check_copies_its_thresholds_and_never_touches_the_registries():
    d = five()
    r = added(d, "gate", check="scope")
    g = node(r["doc"], r["name"])
    assert g["check"] == "scope" and g["params"] == node(d, "scope_gate")["params"]
    assert r["doc"]["gates"] == d["gates"] and r["doc"]["facts"] == d["facts"]
    blank = added(d, "gate", check="spec-shape")
    assert node(blank["doc"], blank["name"])["params"] == {}
    nulls = added(d, "gate", check="ac-command-shape")
    assert set(node(nulls["doc"], nulls["name"])["params"]) == set(GATE_REGISTRY["ac-command-shape"])
    outside = run_node([{"op": "add", "doc": d, "kind": "gate", "opts": {"check": "ghost-check"}}])[0]
    assert "error" in outside


def test_removing_a_node_cuts_the_link_from_its_parent_and_warns_about_each_link_it_breaks():
    d = five()
    leaf = run_node([{"op": "remove", "doc": d, "name": "scout"}])[0]
    assert "scout" not in {n["name"] for n in leaf["doc"]["nodes"]} and "scout" not in node(leaf["doc"], "main")["children"]
    assert any(w["node"] == "main" and "scout" in w["message"] for w in leaf["warnings"])
    parent = run_node([{"op": "remove", "doc": d, "name": "execute"}])[0]
    kids = node(d, "execute")["children"]
    orphans = {w["node"] for w in parent["warnings"] if "no parent" in w["message"]}
    assert orphans == set(kids)
    js = run_node([{"op": "validate", "doc": parent["doc"]}])[0]
    assert js_pairs(js) == py_pairs(parent["doc"]) and {e["node"] for e in js if "orphan" in e["message"]} >= set(kids)


def test_removing_a_dispatch_entry_or_check_keeps_the_dangling_name_so_the_engine_error_shows_on_the_dispatch():
    d = router()
    entry = node(d, "route")["entry"]
    r = run_node([{"op": "remove", "doc": d, "name": entry}])[0]
    assert node(r["doc"], "route")["entry"] == entry
    assert any(w["node"] == "route" and entry in w["message"] for w in r["warnings"])
    js = run_node([{"op": "validate", "doc": r["doc"]}])[0]
    assert js_pairs(js) == py_pairs(r["doc"]) and any(e["node"] == "route" for e in js)


def test_the_root_loop_and_an_unknown_node_are_not_removed():
    d = five()
    a, b = run_node([{"op": "remove", "doc": d, "name": d["root"]}, {"op": "remove", "doc": d, "name": "ghost"}])
    assert "error" in a and "error" in b


# --- connect and disconnect ---
# A link in the graph is a loop or each child, a dispatch entry, or a dispatch check. `next` holds reserved words, not node names, so a port drag never sets it.
def links_of(d, name):
    n = node(d, name)
    return list(n.get("children", [])) + ([n["entry"], *n["checks"]] if n["kind"] == "dispatch" else [])


def conn(d, a, b):
    return run_node([{"op": "connect", "doc": d, "from": a, "to": b}])[0]


def disc(d, a, b):
    return run_node([{"op": "disconnect", "doc": d, "from": a, "to": b}])[0]


def test_connecting_a_loop_or_each_to_a_free_node_appends_it_as_the_last_child_and_leaves_the_graph_valid():
    free = disc(five(), "main", "scout")
    ok = conn(free["doc"], "main", "scout")
    assert node(ok["doc"], "main")["children"][-1] == "scout" and py_pairs(ok["doc"]) == set() and not ok["warnings"]


def test_a_node_that_holds_no_child_refuses_a_connect_and_so_do_a_self_link_a_cycle_the_root_and_an_unknown_node():
    d = five()
    for a, b in [("scout", "spec"), ("scope_gate", "scout"), ("execute", "execute"), ("execute", "main"), ("ghost", "scout"), ("main", "ghost")]:
        assert "error" in conn(d, a, b), (a, b)
    assert "error" in conn(d, "main", "scout")  # already a child of main


def test_connecting_a_dispatch_to_a_gate_adds_a_check_and_to_anything_else_replaces_the_entry():
    d = router()
    name = node(d, "route")["name"]
    old_entry, old_checks = node(d, name)["entry"], list(node(d, name)["checks"])
    g = added(d, "gate", check=node(d, old_checks[0])["check"])
    free = disc(g["doc"], d["root"], g["name"])
    c = conn(free["doc"], name, g["name"])
    assert node(c["doc"], name)["checks"] == old_checks + [g["name"]]
    a = added(d, "agent")
    free = disc(a["doc"], d["root"], a["name"])
    e = conn(free["doc"], name, a["name"])
    assert node(e["doc"], name)["entry"] == a["name"]
    assert any(w["node"] == old_entry and "no parent" in w["message"] for w in e["warnings"])
    assert "error" in conn(c["doc"], name, g["name"])  # the check is already listed


def test_a_second_parent_is_allowed_and_the_engine_rule_shows_on_the_child_never_blocking():
    d = five()
    r = conn(d, "execute", "scout")
    assert "doc" in r
    js = run_node([{"op": "validate", "doc": r["doc"]}])[0]
    assert js_pairs(js) == py_pairs(r["doc"]) and any(e["node"] == "scout" and "two parents" in e["message"] for e in js)


def test_disconnecting_cuts_the_link_warns_of_the_orphan_and_keeps_the_node():
    d = five()
    r = disc(d, "main", "scout")
    assert "scout" not in node(r["doc"], "main")["children"] and node(r["doc"], "scout")
    assert any(w["node"] == "scout" and "no parent" in w["message"] for w in r["warnings"])
    js = run_node([{"op": "validate", "doc": r["doc"]}])[0]
    assert js_pairs(js) == py_pairs(r["doc"]) and any(e["node"] == "scout" and "orphan" in e["message"] for e in js)
    two = disc(conn(d, "execute", "scout")["doc"], "execute", "scout")
    assert not two["warnings"] and py_pairs(two["doc"]) == set()  # scout still has main as its parent


def test_disconnecting_a_dispatch_check_works_and_an_entry_is_replaced_never_cut():
    d = router()
    name = node(d, "route")["name"]
    check = node(d, name)["checks"][0]
    r = disc(d, name, check)
    assert check not in node(r["doc"], name)["checks"]
    assert "error" in disc(d, name, node(d, name)["entry"])
    assert "error" in disc(d, "main" if "main" in {n["name"] for n in d["nodes"]} else d["root"], "ghost")


def test_connect_and_disconnect_results_match_the_engine_for_every_pair_of_nodes():
    """Parity sweep: for each ordered pair, the (node, rule) pairs of the result equal the engine's. Never skips."""
    for d in (five(), router()):
        names = [n["name"] for n in d["nodes"]]
        ops = [{"op": "connect", "doc": d, "from": a, "to": b} for a in names for b in names]
        ops += [{"op": "disconnect", "doc": d, "from": a, "to": b} for a in names for b in links_of(d, a)]
        res = run_node(ops)
        docs = [r["doc"] for r in res if "doc" in r]
        assert docs
        js = run_node([{"op": "validate", "doc": x} for x in docs])
        for x, j in zip(docs, js, strict=False):
            assert js_pairs(j) == py_pairs(x)


def test_a_new_node_gets_a_free_spot_that_overlaps_no_other_node():
    box = {"w": 200, "h": 44}
    layout = {"a": {"x": 100, "y": 100}, "b": {"x": 100, "y": 160}}
    free, taken = run_node([{"op": "spot", "layout": layout, "box": box, "want": {"x": 500, "y": 500}, "gap": 16},
                            {"op": "spot", "layout": layout, "box": box, "want": {"x": 100, "y": 100}, "gap": 16}])
    assert free == {"x": 500, "y": 500}
    for p in layout.values():
        assert abs(taken["x"] - p["x"]) >= box["w"] or abs(taken["y"] - p["y"]) >= box["h"]


# --- rename a node and change its kind ---
def renamed(doc, old, new):
    r = run_node([{"op": "rename", "doc": doc, "name": old, "to": new}])[0]
    assert "doc" in r, r
    return r


def with_outcome_refs(doc):
    """The five-phase graph, plus a `when` and a `sees` that name node `scout` through the outcome:<node> family."""
    d = copy.deepcopy(doc)
    node(d, "critic")["when"] = {"fact": "outcome:scout", "op": "eq", "value": "pass"}
    node(d, "actor")["adapt"]["sees"] = ["run_spend", "outcome:scout"]
    return d


def test_a_rename_leaves_no_stale_reference_on_either_fixture():
    """Rename each node of both valid fixtures: the graph stays valid, the old name is gone, and the dashboard and the engine agree."""
    for make_doc in (five, router):
        d = make_doc()
        names = [n["name"] for n in d["nodes"]]
        res = run_node([{"op": "rename", "doc": d, "name": n, "to": "zz_renamed"} for n in names])
        for n, r in zip(names, res, strict=False):
            out = r["doc"]
            assert graph.validate(out) == [], (make_doc.__name__, n, graph.validate(out))
            assert n not in {x["name"] for x in out["nodes"]} and "zz_renamed" in {x["name"] for x in out["nodes"]}
            assert [x["kind"] for x in out["nodes"]] == [x["kind"] for x in d["nodes"]]
            assert out["facts"] == d["facts"] and out["gates"] == d["gates"]
        js = run_node([{"op": "validate", "doc": r["doc"]} for r in res])
        assert all(js_pairs(j) == py_pairs(r["doc"]) for j, r in zip(js, res, strict=False))


def test_a_rename_updates_the_root_the_children_the_dispatch_entry_and_checks_and_every_outcome_reference():
    d = with_outcome_refs(five())
    r = renamed(d, "scout", "recon")["doc"]
    assert "recon" in node(r, "main")["children"] and "scout" not in node(r, "main")["children"]
    assert node(r, "critic")["when"]["fact"] == "outcome:recon" and node(r, "actor")["adapt"]["sees"] == ["run_spend", "outcome:recon"]
    assert graph.validate(r) == []
    assert renamed(d, "main", "start")["doc"]["root"] == "start"
    assert node(renamed(d, "merge", "land")["doc"], "manual_merge")["when"]["fact"] == "outcome:land"
    ro = router()
    entry, check = node(ro, "route")["entry"], node(ro, "route")["checks"][0]
    one = renamed(ro, entry, "front")["doc"]
    two = renamed(ro, check, "guard")["doc"]
    assert node(one, "route")["entry"] == "front" and node(two, "route")["checks"][0] == "guard"
    assert graph.validate(one) == [] and graph.validate(two) == []


def test_a_rename_keeps_the_order_of_the_nodes_and_every_other_field_and_changes_the_hash():
    d = five()
    out = renamed(d, "scout", "recon")["doc"]
    assert [n["name"] for n in out["nodes"]][1] == "recon"
    before = {k: v for k, v in node(d, "scout").items() if k != "name"}
    after = {k: v for k, v in node(out, "recon").items() if k != "name"}
    assert before == after
    h = run_node([{"op": "hash", "text": json.dumps(x)} for x in (d, out)])
    assert h[0] != h[1]


def test_a_rename_to_a_bad_or_taken_name_or_of_a_missing_node_is_refused_and_a_same_name_changes_nothing():
    d = five()
    bad = ["", "Recon", "9lives", "with space", "dash-ed", "spec", "main"]
    res = run_node([{"op": "rename", "doc": d, "name": "scout", "to": b} for b in bad] +
                   [{"op": "rename", "doc": d, "name": "ghost", "to": "recon"}, {"op": "rename", "doc": d, "name": "scout", "to": "scout"}])
    assert all("error" in r for r in res[:-1]), res
    assert "error" in res[-1] or res[-1]["doc"] == d


def test_a_rename_moves_the_layout_key_with_the_node():
    layout = {"scout": {"x": 1, "y": 2}, "spec": {"x": 3, "y": 4}}
    got = run_node([{"op": "layoutRename", "layout": layout, "name": "scout", "to": "recon"}])[0]
    assert got == {"recon": {"x": 1, "y": 2}, "spec": {"x": 3, "y": 4}}


def changed(doc, name, kind, **opts):
    return run_node([{"op": "kind", "doc": doc, "name": name, "kind": kind, "opts": opts}])[0]


def test_a_kind_change_keeps_the_name_the_place_and_the_fields_and_shows_the_engine_rules_on_the_node():
    """Every non-gate node to every other kind: the dashboard and the engine report the same (node, rule) pairs."""
    cases = []
    for make_doc in (five, router):
        d = make_doc()
        for n in d["nodes"]:
            for k in KINDS:
                if n["kind"] not in ("gate", k):
                    cases.append((make_doc.__name__, n["name"], n["kind"], k, d))
    res = run_node([{"op": "kind", "doc": d, "name": n, "kind": k, "opts": {}} for _, n, _, k, d in cases])
    assert len(cases) > 100
    for (label, n, _old_kind, k, d), r in zip(cases, res, strict=False):
        assert "doc" in r, (label, n, k, r)
        out = r["doc"]
        assert node(out, n)["kind"] == k and [x["name"] for x in out["nodes"]] == [x["name"] for x in d["nodes"]]
        assert node(out, "main")["children"] == node(d, "main")["children"] or n == "main"
        for key, val in node(d, n).items():
            if key != "kind":
                assert node(out, n).get(key) == val, (label, n, k, key)
    js = run_node([{"op": "validate", "doc": r["doc"]} for r in res])
    assert all(js_pairs(j) == py_pairs(r["doc"]) for j, r in zip(js, res, strict=False))


def test_a_kind_change_shows_a_rule_error_on_the_node_for_each_field_the_new_kind_does_not_allow():
    d = five()
    r = changed(d, "scout", "wait")["doc"]
    errs = [e for e in graph.validate(r) if e.node == "scout"]
    assert errs and any("model" in e.message or "budget" in e.message or "additional" in e.message.lower() for e in errs)
    # a leaf that fits the new kind leaves the graph as valid as the new kind allows: an action with no extra field
    a = changed(d, "worktree", "wait")["doc"]
    assert node(a, "worktree")["kind"] == "wait" and any(e.node == "worktree" for e in graph.validate(a))


def test_a_loop_and_an_each_swap_kinds_and_keep_their_children():
    d = five()
    r = changed(d, "work", "each")["doc"]
    assert node(r, "work")["kind"] == "each" and node(r, "work")["children"] == node(d, "work")["children"]
    assert "items" in node(r, "work")
    back = changed(r, "work", "loop")["doc"]
    assert node(back, "work")["kind"] == "loop" and node(back, "work")["children"] == node(d, "work")["children"]


def test_a_node_changed_to_a_gate_picks_a_registered_check_and_a_gate_is_never_changed_to_another_kind():
    d = five()
    g = changed(d, "scout", "gate", check="scope")["doc"]
    assert node(g, "scout")["check"] == "scope" and node(g, "scout")["params"] == node(d, "scope_gate")["params"]
    assert g["gates"] == d["gates"] and g["facts"] == d["facts"]
    outside = changed(d, "scout", "gate", check="ghost-check")
    away = changed(d, "spec_gate", "agent")
    same = changed(d, "scout", "agent")
    ghost = changed(d, "ghost", "agent")
    banana = changed(d, "scout", "banana")
    assert all("error" in x for x in (outside, away, same, ghost, banana)), (outside, away, same, ghost, banana)
    assert "read-only" in away["error"]


def test_the_side_panel_reads_the_served_lists_by_their_real_keys_so_selecting_an_agent_never_throws():
    """A node panel for an agent named SERVED.model, a key that does not exist (the lists are models, efforts, tools, skills)."""
    from build_dashboard import served_lists  # tests.dashboard_helpers, imported above, puts scripts on sys.path
    served = served_lists()
    real = [n for n in five()["nodes"] if n["kind"] == "agent"][0]
    ghost = {**real, "model": "ghost-model", "effort": "ghost-effort", "tools": ["GhostTool"], "skills": [{"name": "ghost-skill", "version": "1"}]}
    clean, flagged = run_node([{"op": "unseen", "node": {"name": "x", "kind": "agent", "model": served["models"][0], "tools": [served["tools"][0]]}, "served": served},
                               {"op": "unseen", "node": ghost, "served": served}])
    assert clean == []
    assert set(flagged) == {"model ghost-model", "effort ghost-effort", "tools GhostTool", "skills ghost-skill"}
    page = page_template()
    assert "core.unseenValues(n, SERVED)" in page


def test_a_rename_or_kind_change_never_writes_the_registries_or_the_record():
    page = page_template()
    assert "renameNode" in page and "changeKind" in page
    assert "doc.facts =" not in page and "doc.gates =" not in page


# --- fields grouped by purpose ---
def groups_of(n):
    return run_node([{"op": "groups", "node": n}])[0]


def set_field(doc, name, key, text):
    return run_node([{"op": "setfield", "doc": doc, "name": name, "key": key, "text": text}])[0]


def test_every_field_of_every_node_sits_in_exactly_one_group_and_every_kind_offers_its_schema_fields():
    for make_doc in (five, router):
        for n in make_doc()["nodes"]:
            groups = groups_of(n)
            keys = [f["key"] for g in groups for f in g["fields"]]
            assert len(keys) == len(set(keys)), (n["name"], keys)
            assert set(n) <= set(keys), (n["name"], set(n) - set(keys))
            offered = set(json.loads(graph.SCHEMA_PATH.read_text())["$defs"][n["kind"]]["properties"])
            assert offered <= set(keys), (n["name"], offered - set(keys))


def test_the_groups_follow_purpose_and_a_rare_group_starts_collapsed_unless_it_holds_a_value():
    agent = [n for n in five()["nodes"] if n["kind"] == "agent"][0]
    groups = {g["id"]: g for g in groups_of(agent)}
    assert {"identity", "settings", "instructions", "limits", "control", "files"} <= set(groups)
    where = {f["key"]: g["id"] for g in groups.values() for f in g["fields"]}
    assert where["model"] == where["tools"] == "settings" and where["budget"] == where["deadline"] == "limits"
    assert where["when"] == where["next"] == "control" and where["instructions"] == "instructions"
    bare = {"name": "x", "kind": "agent", "model": "sonnet"}
    assert {g["id"]: g["open"] for g in groups_of(bare)}["files"] is False
    held = {g["id"]: g["open"] for g in groups_of({**bare, "reads": ["docs/a.md"]})}
    assert held["files"] is True


def test_a_gate_shows_its_check_and_thresholds_read_only_and_keeps_next_editable():
    gate = [n for n in five()["nodes"] if n["kind"] == "gate"][0]
    fields = {f["key"]: f for g in groups_of(gate) for f in g["fields"]}
    assert fields["check"]["readonly"] and fields["params"]["readonly"] and fields["name"]["readonly"] and fields["kind"]["readonly"]
    assert not fields["next"].get("readonly")


def test_the_suggestion_lists_are_named_per_field_and_never_restrict_a_value():
    agent = [n for n in five()["nodes"] if n["kind"] == "agent"][0]
    fields = {f["key"]: f for g in groups_of(agent) for f in g["fields"]}
    assert [fields[k].get("suggest") for k in ("model", "effort", "tools", "skill")] == ["models", "efforts", "tools", "skills"]
    r = set_field(five(), agent["name"], "model", "a-model-nobody-listed")
    assert node(r["doc"], agent["name"])["model"] == "a-model-nobody-listed"


def test_an_unknown_field_is_a_node_error_in_both_and_a_field_edit_keeps_it_unchanged():
    d = five()
    agent = [n for n in d["nodes"] if n["kind"] == "agent"][0]["name"]
    node(d, agent)["mystery"] = {"keep": [1, 2, 3]}
    assert any(e["node"] == agent for e in run_node([{"op": "validate", "doc": d}])[0])
    assert js_pairs(run_node([{"op": "validate", "doc": d}])[0]) == py_pairs(d)
    unknown = [g for g in groups_of(node(d, agent)) if g["id"] == "unknown"]
    assert unknown and [f["key"] for f in unknown[0]["fields"]] == ["mystery"] and unknown[0]["fields"][0]["unknown"]
    r = set_field(d, agent, "deadline", "90")
    assert node(r["doc"], agent)["mystery"] == {"keep": [1, 2, 3]} and node(r["doc"], agent)["deadline"] == 90
    assert list(node(r["doc"], agent)) == list(node(d, agent))   # key order, so the hash moves only for the value


def test_a_field_shown_and_set_back_unchanged_leaves_the_graph_byte_identical():
    cases = []
    for make_doc in (five, router):
        d = make_doc()
        for n in d["nodes"]:
            for g in groups_of(n):
                for f in g["fields"]:
                    if f["present"] and not f.get("readonly"):
                        cases.append((d, n["name"], f["key"], f["value"]))
    assert len(cases) > 100
    res = run_node([{"op": "setfield", "doc": d, "name": n, "key": k, "text": v} for d, n, k, v in cases])
    for (d, n, k, _v), r in zip(cases, res, strict=False):
        assert "doc" in r, (n, k, r)
        assert json.dumps(r["doc"]["nodes"], sort_keys=False) == json.dumps(d["nodes"], sort_keys=False), (n, k)


def test_a_blank_value_removes_the_field_and_a_bad_number_or_json_is_refused_without_a_change():
    d = five()
    agent = [n for n in d["nodes"] if n["kind"] == "agent" and "deadline" in n][0]["name"]
    assert "deadline" not in node(set_field(d, agent, "deadline", "  ")["doc"], agent)
    assert "error" in set_field(d, agent, "deadline", "soon") and "error" in set_field(d, agent, "budget", "{not json")
    assert node(set_field(d, agent, "deadline", "90")["doc"], agent)["deadline"] == 90
    assert node(set_field(d, agent, "may_propose", "true")["doc"], agent)["may_propose"] is True
    assert node(set_field(d, agent, "tools", "Read, Grep ,")["doc"], agent)["tools"] == ["Read", "Grep"]


def test_a_gate_check_thresholds_name_and_kind_cannot_be_set_and_its_next_can():
    d = five()
    gate = [n for n in d["nodes"] if n["kind"] == "gate"][0]["name"]
    for key, text in (("check", "other"), ("params", "{}"), ("name", "zzz"), ("kind", "wait")):
        assert "error" in set_field(d, gate, key, text), key
    assert "doc" in set_field(d, gate, "next", '{"fail": "repeat"}')


def test_a_field_edit_gives_the_engine_rules_on_the_node_whatever_the_value():
    """The dashboard and the engine report the same (node, rule) pairs after each edit; save is never blocked."""
    d = five()
    agent = [n for n in d["nodes"] if n["kind"] == "agent"][0]["name"]
    edits = [(agent, "deadline", "-5"), (agent, "effort", "ghost"), (agent, "tools", "Bash, Write"), (agent, "budget", '{"stalls": {"min": 9, "max": 1}}'),
             (agent, "reads", '["a.md"]'), (agent, "writes", '["a.md"]'), (agent, "when", '{"fact": "nope"}'), (agent, "next", '{"pass": "nowhere"}'),
             (agent, "skill", "ghost-skill"), (agent, "instructions", "line one\nline two")]
    res = run_node([{"op": "setfield", "doc": d, "name": n, "key": k, "text": t} for n, k, t in edits])
    docs = [r["doc"] for r in res]
    assert len(docs) == len(edits)
    js = run_node([{"op": "validate", "doc": x} for x in docs])
    assert all(js_pairs(j) == py_pairs(x) for j, x in zip(js, docs, strict=False))


# ---- #79: undo and redo for every edit
def HIST_EDITS(d):
    return [
    {"op": "add", "kind": "agent"},
    {"op": "move", "name": "scout", "x": 500, "y": 300},
    {"op": "rename", "name": "scout", "to": "scout2"},
    {"op": "disconnect", "from": "main", "to": "spec"},
    {"op": "connect", "from": "main", "to": "spec"},
    {"op": "setfield", "name": "scout2", "key": "effort", "text": "ghost"},
    {"op": "kind", "name": "scout2", "kind": "wait"},
    {"op": "remove", "name": "spec"},
]


def hist(d, edits, limit=100):
    layout = run_node([{"op": "layout", "doc": d}])[0]
    return run_node([{"op": "history", "doc": d, "layout": layout, "edits": edits, "limit": limit}])[0]


def test_undo_walks_back_through_every_edit_and_redo_walks_forward_to_the_same_graph_and_layout():
    d = five()
    r = hist(d, HIST_EDITS(d))
    assert not r["skipped"], r["skipped"]
    n = len(HIST_EDITS(d))
    assert len(r["states"]) == n + 1 and len(r["undone"]) == n and len(r["redone"]) == n
    # undo k steps back lands on the state before the edit, exactly (graph and layout)
    for k, st in enumerate(r["undone"]):
        assert st == r["states"][n - 1 - k], k
    assert r["afterAllUndo"] == r["states"][0]
    # redo returns to each later state, and to the last one
    for k, st in enumerate(r["redone"]):
        assert st == r["states"][k + 1], k
    assert r["redone"][-1] == r["states"][-1]


def test_a_refused_edit_leaves_no_history_step():
    d = five()
    r = hist(d, [{"op": "remove", "name": "main"}, {"op": "connect", "from": "scout", "to": "spec"}, {"op": "add", "kind": "gate", "opts": {"check": "scope"}}])
    assert len(r["skipped"]) >= 1 and len(r["undone"]) == len(r["states"]) - 1


def test_a_new_edit_after_an_undo_clears_the_redo_stack():
    r = hist(five(), HIST_EDITS(five())[:3])
    assert r["clearedRedo"] is True


def test_history_keeps_at_most_its_limit_of_steps_and_drops_the_oldest():
    d = five()
    edits = [{"op": "move", "name": "scout", "x": 10 * i, "y": 5} for i in range(1, 8)]
    r = hist(d, edits, limit=3)
    assert len(r["undone"]) == 3 and r["canUndoAfterAll"] is False and r["afterAllUndo"] == r["states"][4]


def test_undo_does_not_change_the_graph_text_the_page_saves():
    """History lives in the page, not in the graph or the layout file."""
    d = five()
    r = hist(d, HIST_EDITS(d)[:2])
    assert set(r["states"][-1]["doc"]) == set(d)  # the document gains no history key
