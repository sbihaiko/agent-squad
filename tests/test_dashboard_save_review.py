"""Save, draft copy, and review: the save gate, the diff, and the review sheet (#75, #84, #118)."""
import copy
import json
import re

from squad import graph
from tests.dashboard_helpers import CORE_DIR, page_template, run_node
from tests.helpers import five, node


# --- save, draft copy, and the changed-file stop (decision #62, ticket #75) ---
def test_the_hash_chip_hashes_the_bytes_that_save_writes_and_equals_the_engine_hash():
    d = five()
    text = json.dumps(d, indent=2)
    chip, saved, again = run_node([{"op": "chip", "text": text}, {"op": "savetext", "text": text}, {"op": "chip", "text": text + "\n"}])
    assert saved == text + "\n" and run_node([{"op": "savetext", "text": saved}])[0] == saved
    assert chip["full"] == again["full"] == graph.canon_hash(d)
    assert chip["short"] == chip["full"][:12] and len(chip["short"]) == 12


def test_a_draft_copy_holds_five_fixed_keys_never_a_graph_key_and_is_offered_only_for_the_same_name_and_other_text():
    d = five()
    text = json.dumps(d, indent=2) + "\n"
    args = {"name": "five", "text": text, "layout": {"a": {"x": 1, "y": 2}}, "time": 1790000000, "baseHash": "ab" * 32}
    draft = run_node([{"op": "draft", "args": args}])[0]
    assert sorted(draft) == ["baseHash", "layout", "name", "text", "time"]
    assert json.loads(draft["text"]) == d and "nodes" not in draft and "draft" not in d
    assert run_node([{"op": "savetext", "text": text}])[0] == text   # making a draft never changes what a save writes
    other = text.replace('"agent"', '"agent"', 1) + " "
    offers = run_node([{"op": "offer", "draft": draft, "name": "five", "text": other}, {"op": "offer", "draft": draft, "name": "five", "text": text},
                       {"op": "offer", "draft": draft, "name": "other", "text": other}, {"op": "offer", "draft": None, "name": "five", "text": other}])
    assert [o is not None for o in offers] == [True, False, False, False]


def test_the_layout_writer_drops_a_key_for_a_missing_node_keeps_the_rest_and_says_what_it_dropped():
    layout = {"a": {"x": 1, "y": 2}, "gone": {"x": 3, "y": 4}, "b": {"x": 5, "y": 6}}
    res = run_node([{"op": "layoutsave", "layout": layout, "names": ["a", "b"]}, {"op": "layoutsave", "layout": layout, "names": ["a", "b", "gone"]}])
    assert res[0] == {"layout": {"a": {"x": 1, "y": 2}, "b": {"x": 5, "y": 6}}, "dropped": ["gone"]}
    assert res[1] == {"layout": layout, "dropped": []}


def test_the_in_place_save_stops_when_the_file_changed_and_a_validation_error_never_stops_it():
    same = {"lastModified": 100, "text": "x"}
    cases = [
        ({"mode": "in-place", "stored": same, "current": {"lastModified": 100, "text": "x"}, "errors": 0}, "write"),
        ({"mode": "in-place", "stored": same, "current": {"lastModified": 101, "text": "x"}, "errors": 0}, "stop"),
        ({"mode": "in-place", "stored": same, "current": {"lastModified": 100, "text": "y"}, "errors": 0}, "stop"),
        ({"mode": "in-place", "stored": same, "current": {"lastModified": 100, "text": "x"}, "errors": 7}, "write"),
        ({"mode": "in-place", "stored": same, "current": {"lastModified": 100, "text": "x"}, "errors": 99}, "write"),
        ({"mode": "download", "stored": None, "current": None, "errors": 3}, "write"),
    ]
    got = run_node([{"op": "gate", "args": a} for a, _ in cases])
    assert got == [w for _, w in cases]


def test_the_dashboard_saves_by_download_when_the_file_picker_is_absent_and_names_the_mode():
    res = run_node([{"op": "mode", "args": {"picker": False, "handle": False}}, {"op": "mode", "args": {"picker": True, "handle": False}},
                    {"op": "mode", "args": {"picker": True, "handle": True}}, {"op": "mode", "args": {"picker": False, "handle": True}}])
    assert [r["mode"] for r in res] == ["download", "download", "in-place", "download"]
    assert [r["label"] for r in res] == ["Download save", "Download save", "In-place save", "Download save"]
    rel = run_node([{"op": "hashrel", "record": "ab", "chip": "ab"}, {"op": "hashrel", "record": "ab", "chip": "cd"}, {"op": "hashrel", "record": None, "chip": "cd"}])
    assert rel == ["matches", "does not match", None]


def test_the_dashboard_page_builds_its_form_from_the_core_and_never_writes_a_registry():
    page = page_template()
    assert "core.fieldGroups(" in page and "core.setField(" in page
    # a field's change event fires after a click on another node moved the selection: the edit goes to the node the form was drawn for
    body = page.split("async function setFieldFrom(", 1)[1].split("\n}\n", 1)[0]
    assert "S.selected" not in body and "setFieldFrom(name, el.dataset.key" in page
    assert "doc.facts =" not in page and "doc.gates =" not in page


def test_the_dashboard_page_writes_a_file_only_after_the_save_gate_and_never_autosaves_to_disk():
    page = page_template()
    # a picker is used only where it exists; an absent picker leaves the file input and the download
    assert "const PICKER = !!window.showOpenFilePicker" in page and "PICKER && t.handle()" in page
    # every in-place write sits behind the gate, the stop panel, or a person's click on Overwrite anyway
    assert page.count("createWritable()") == 1 and "core.saveGate(" in page
    save = page.split("async function saveTarget(", 1)[1].split("\n}\n", 1)[0]
    assert save.index("core.saveGate(") < save.index("writeInPlace(")
    assert "Reload from disk" in page and "Save a copy" in page and "Overwrite anyway" in page
    # the draft goes to browser storage only; no timer writes a file
    draft = page.split("function scheduleDraft()", 1)[1].split("\n}\n", 1)[0]
    assert "draftIdb(" in draft and "createWritable" not in draft and "download(" not in draft
    assert "core.hashChip(" in page


# --- review (ticket #84): a diff by node name, declared caps, a command template, a review sheet. No run starts here (ADR-0117). ---
def _hash(d):
    return graph.canon_hash(d)


def test_the_diff_compares_by_node_name_and_lists_added_removed_and_changed_fields():
    prior = five()
    cur = copy.deepcopy(prior)
    cur["nodes"] = [n for n in cur["nodes"] if n["name"] != "visual_guard"]
    node(cur, "scout")["deadline"] = 777
    extra = copy.deepcopy(node(cur, "scout"))
    extra["name"] = "scout_two"
    cur["nodes"].append(extra)
    cur["run"]["spend_cap"] = 30
    d = run_node([{"op": "diff", "prior": prior, "cur": cur}])[0]
    assert d["nodes"]["added"] == ["scout_two"] and d["nodes"]["removed"] == ["visual_guard"]
    assert d["nodes"]["changed"] == [{"name": "scout", "fields": [{"key": "deadline", "before": str(node(prior, "scout")["deadline"]), "after": "777"}]}]
    assert d["run"] == [{"key": "spend_cap", "before": "60", "after": "30"}] and d["empty"] is False


def test_a_diff_of_two_equal_documents_is_empty_and_an_order_change_is_no_change():
    d = five()
    swapped = copy.deepcopy(d)
    swapped["nodes"].reverse()
    out = run_node([{"op": "diff", "prior": d, "cur": d}, {"op": "diff", "prior": d, "cur": swapped}])
    assert out[0]["empty"] and out[1]["empty"]


def test_the_diff_names_a_changed_registry_and_a_field_that_appears_or_goes():
    prior = five()
    cur = copy.deepcopy(prior)
    cur["gates"] = cur["gates"][:-1]
    node(cur, "scout")["effort"] = "high"
    del node(cur, "spec_draft")["deadline"]
    d = run_node([{"op": "diff", "prior": prior, "cur": cur}])[0]
    assert d["gates"]["removed"] == [prior["gates"][-1]["id"]]
    fields = {c["name"]: c["fields"] for c in d["nodes"]["changed"]}
    assert {"key": "effort", "before": None, "after": '"high"'} in fields["scout"]
    assert fields["spec_draft"][0]["key"] == "deadline" and fields["spec_draft"][0]["after"] is None


def test_declared_caps_are_the_document_values_as_written_and_nothing_is_added_up():
    d = five()
    caps = run_node([{"op": "caps", "doc": d}])[0]
    assert json.loads(caps["run"]) == d["run"]
    scout = next(r for r in caps["nodes"] if r["name"] == "scout")
    assert json.loads(scout["budget"]) == node(d, "scout")["budget"] and json.loads(scout["deadline"]) == node(d, "scout")["deadline"]
    assert {g["name"] for g in caps["gates"]} == {n["name"] for n in d["nodes"] if n["kind"] == "gate"}
    gate = next(g for g in caps["gates"] if g["name"] == "plan_gate")
    assert gate["check"] == '"plan-semantics"' and json.loads(gate["params"]) == {"max_work_items": 5}
    flat = json.dumps(caps).lower()
    assert not any(w in flat for w in ("total", "sum", "bound", "forecast", "score"))   # ADR-0117 rule 3: no computed spend bound


def test_the_command_template_carries_the_reviewed_hash_and_every_set_option():
    d = five()
    h = _hash(d)
    args = {"file": "my graph.json", "hash": h, "doc": d, "opts": {"adapter": "sdk", "stopAfter": "scout", "spendCap": "20", "deadline": "3600", "maxParallel": "1", "tierPin": "sonnet"}}
    t = run_node([{"op": "template", "args": args}])[0]
    assert t["errors"] == []
    assert t["text"] == f"squad run 'my graph.json' --adapter sdk --stop-after scout --spend-cap 20 --deadline 3600 --max-parallel 1 --tier-pin sonnet --expect-graph-hash {h}"
    script = run_node([{"op": "template", "args": {**args, "opts": {"adapter": "script", "script": "s.json"}}}])[0]
    assert script["text"] == f"squad run 'my graph.json' --script s.json --expect-graph-hash {h}"


def test_the_command_template_is_null_with_an_error_for_each_bad_option_and_overrides_only_go_down():
    d = five()
    h = _hash(d)

    def tpl(**o):
        return run_node([{"op": "template", "args": {"file": "g.json", "hash": h, "doc": d, "opts": {"adapter": "sdk", **o}}}])[0]
    assert tpl(spendCap="61")["text"] is None and "only goes down" in tpl(spendCap="61")["errors"][0]
    assert tpl(deadline="21601")["text"] is None and tpl(maxParallel="3")["text"] is None
    assert tpl(maxParallel="1.5")["text"] is None and tpl(spendCap="-1")["text"] is None and tpl(deadline="abc")["text"] is None
    assert tpl(stopAfter="no_such_node")["text"] is None and "names no node" in tpl(stopAfter="no_such_node")["errors"][0]
    assert tpl(spendCap="60", deadline="21600", maxParallel="2")["errors"] == []   # equal to the run block is allowed
    none = run_node([{"op": "template", "args": {"file": "g.json", "hash": h, "doc": d, "opts": {}}}])[0]
    assert none["text"] is None and "--adapter sdk" in none["errors"][0]
    nohash = run_node([{"op": "template", "args": {"file": "g.json", "hash": None, "doc": d, "opts": {"adapter": "sdk"}}}])[0]
    assert nohash["text"] is None and "no hash" in nohash["errors"][0]


def test_the_review_sheet_is_plain_text_that_says_it_approves_nothing_and_holds_the_five_parts():
    d = five()
    h = _hash(d)
    cur = copy.deepcopy(d)
    node(cur, "scout")["deadline"] = 5
    caps = run_node([{"op": "caps", "doc": cur}])[0]
    diff = run_node([{"op": "diff", "prior": d, "cur": cur}])[0]
    tpl = run_node([{"op": "template", "args": {"file": "g.json", "hash": _hash(cur), "doc": cur, "opts": {"adapter": "sdk"}}}])[0]["text"]
    note = run_node([{"op": "note"}])[0]
    args = {"file": "g.json", "hash": _hash(cur), "nodeCount": len(cur["nodes"]), "errors": ["x: an error"], "caps": caps, "diff": diff, "prior": {"file": "old.json", "hash": h}, "template": tpl}
    sheet, bare = run_node([{"op": "sheet", "args": args}, {"op": "sheet", "args": {**args, "diff": None, "prior": None, "template": None}}])
    assert f"Graph hash: {_hash(cur)}" in sheet and "Errors: 1" in sheet and "Declared caps" in sheet and "deadline" in sheet
    assert f"Diff against old.json (hash {h})" in sheet and "~ scout" in sheet and tpl in sheet and note in sheet
    assert "approve nothing" in sheet and "authorize no live write" in sheet and "person's act" in sheet
    assert "Diff: no prior graph opened." in bare and "(none: fix the options first)" in bare
    assert not re.search(r"approved\s*[:=]\s*true|signed", sheet, re.I)


def test_the_review_code_starts_no_process_and_makes_no_request_and_the_page_has_no_run_control():
    core = "".join(p.read_text() for p in sorted(CORE_DIR.glob("*.js")))
    review = core.split("// --- review (ticket #84", 1)[1].split("// The view is {k, tx, ty}", 1)[0]
    assert not re.search(r"require\(|child_process|fetch\(|XMLHttpRequest|WebSocket|https?://|createWritable|localStorage|indexedDB", review)
    page = page_template()
    panel = page.split("// --- review panel (ticket #84", 1)[1].split("\n// --- ", 1)[0]
    assert not re.search(r"fetch\(|XMLHttpRequest|WebSocket|child_process|https?://", panel)
    assert not re.search(r"<button[^>]*>\s*(Run|Launch|Start|Approve|Execute)", page, re.I)   # a control that starts a run or approves
    assert not re.search(r'id="b-(run|launch|start|approve)', page)
    assert "core.commandTemplate(" in panel and "core.reviewSheet(" in panel and "core.diffGraphs(" in panel
