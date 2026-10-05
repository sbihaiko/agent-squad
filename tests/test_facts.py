"""Guards for ticket #27: ADR-0104 (closed registry, sees, forbidden triggers, freshness), ADR-0106 rule 6."""
import json

import pytest

from squad import facts as F
from squad.graph import from_doc
from tests.helpers import five, router


def store(doc=None, **kw):
    d = doc or five()
    g = from_doc(d)
    return F.FactStore(d["facts"], g.nodes, "r1", **kw), g


def ctx(s, artifacts=None, ran=(), options=None, item=None, target=None, git=True, pos=5):
    artifacts = artifacts or {}
    return F.DeriveCtx(lambda k, i: artifacts.get((k, i)) if (k, i) in artifacts else artifacts.get((k, None)),
                       lambda n, i: n in ran, item, target or __import__("pathlib").Path("."), options or {}, git, s, pos)


def test_the_registry_loads_from_the_graph_document_and_refuses_an_unregistered_read():
    s, _ = store()
    assert s.registered("tier") and s.registered("run_spend") and s.registered("outcome:merge")
    for bad in ("mystery", "outcome:nowhere"):
        with pytest.raises(F.UnregisteredFact):
            s.read(bad)
        with pytest.raises(F.UnregisteredFact):
            s.produce(bad, 1, {"kind": "run_record", "run": "r1", "pos": 1})


def test_the_built_in_families_are_produced_and_read():
    s, _ = store()
    src = {"kind": "run_record", "run": "r1", "pos": 2}
    for name, v in (("outcome:scout", "pass"), ("run_spend", 1.5), ("run_spend_remaining", 58.5), ("depth", 2),
                    ("children_count", 3), ("spawn_rejected_count", 1)):
        s.produce(name, v, src)
        assert s.read(name) == v
    f = s.get("run_spend")
    assert f.instrument.startswith("code:") and f.source == src and f.timestamp and f.severity == "info"


def test_a_when_reads_a_fact_and_an_unavailable_fact_is_false_with_its_name():
    s, _ = store()
    w = {"fact": "tier", "op": "ne", "value": "trivial"}
    assert s.evaluate(w) == (False, "fact_unavailable:tier")
    s.produce("tier", "medium", {"kind": "run_record", "run": "r1", "pos": 3})
    assert s.evaluate(w) == (True, None)
    assert s.evaluate({"fact": "tier", "op": "eq", "value": "large"}) == (False, None)


def test_a_fact_in_an_item_scope_hides_the_run_scope_value():
    s, _ = store()
    src = {"kind": "run_record", "run": "r1", "pos": 3}
    s.produce("outcome:merge", "pass", src)
    s.produce("outcome:merge", "fail", src, item=2)
    assert s.read("outcome:merge", 2) == "fail" and s.read("outcome:merge", 1) == "pass"


@pytest.mark.parametrize("est,tier", [({"files": 1, "added_lines": 10}, "trivial"), ({"files": 6, "added_lines": 200}, "medium"),
                                      ({"files": 30, "added_lines": 2000}, "large"), (None, "large"), ({"files": "x"}, "large")])
def test_tier_is_derived_from_the_scout_estimate_and_a_missing_estimate_is_large(est, tier):
    s, _ = store()
    c = ctx(s, {("size_estimate", None): est} if est is not None else {}, ran={"scout"})
    F.refresh(s, c)
    assert s.read("tier") == tier


def test_tier_is_not_ready_before_the_scout_runs_and_tier_pin_overrides():
    s, _ = store()
    assert "tier" not in F.refresh(s, ctx(s))
    s.produce("tier_pin", "large", {"kind": "run_record", "run": "r1", "pos": 1})
    F.refresh(s, ctx(s, options={"tier_pin": "large"}))
    assert s.read("tier") == "large"


def test_review_required_work_items_sensitive_visual_and_git_are_derived():
    s, _ = store()
    arts = {("size_estimate", None): {"files": 1, "added_lines": 5}, ("scout", None): {"paths": ["src/auth/login.py", "web/app.css"]},
            ("work_items", None): [{"id": 1}, {"id": 2}, {"id": 3}], ("diff", 1): {"files": [{"path": "a.py"}], "added_lines": 3}}
    F.refresh(s, ctx(s, arts, ran={"scout", "decompose"}))
    assert s.read("sensitive") is True and s.read("visual") is True and s.read("git") is True and s.read("work_items") == 3
    F.refresh(s, ctx(s, arts, ran={"scout"}, item=1))
    assert s.read("tier") == "trivial" and s.read("review_required", 1) is False
    arts[("diff", 1)] = {"files": [{"path": "src/auth/x.py"}], "added_lines": 3}
    F.refresh(s, ctx(s, arts, ran={"scout"}, item=1))
    assert s.read("review_required", 1) is True


def test_work_items_is_one_when_decompose_ran_without_an_artifact():
    s, _ = store()
    F.refresh(s, ctx(s, ran={"decompose"}))
    assert s.read("work_items") == 1


def test_blocking_learnings_reads_a_declared_file_in_the_target(tmp_path):
    s, _ = store()
    (tmp_path / "learnings.json").write_text(json.dumps({"entries": [{"blocking": True}, {"blocking": False}, {"blocking": True}]}))
    F.refresh(s, ctx(s, target=tmp_path))
    assert s.read("blocking_learnings") == 2


def test_tier_is_readable_by_a_when_and_refused_in_a_sees_list():
    s, g = store()
    s.produce("tier", "large", {"kind": "run_record", "run": "r1", "pos": 2})
    s.produce("run_spend", 1.0, {"kind": "run_record", "run": "r1", "pos": 2})
    assert s.evaluate({"fact": "tier", "op": "eq", "value": "large"}) == (True, None)
    with pytest.raises(F.FactError):
        s.view_for(["tier", "run_spend"], ["tier"])
    with pytest.raises(F.FactError):
        s.view_for(["run_spend"], ["sensitive"])
    assert s.view_for(["run_spend"], ["run_spend"])["run_spend"]["value"] == 1.0


@pytest.mark.parametrize("name", ["gate_pass_rate", "gate_finding_count", "gate_finding_trend", "model_self_report", "judge_score"])
def test_each_forbidden_trigger_example_is_refused_by_the_closed_registry(name):
    s, _ = store()
    with pytest.raises(F.UnregisteredFact):
        s.produce(name, 0.9, {"kind": "run_record", "run": "r1", "pos": 1})
    with pytest.raises(F.UnregisteredFact):
        s.evaluate({"fact": name, "op": "gt", "value": 0.5})


def test_a_fact_with_no_source_or_no_timestamp_is_refused():
    s, _ = store()
    with pytest.raises(F.FactError, match="no source"):
        s.produce("tier", "large", None)
    with pytest.raises(F.FactError, match="no timestamp"):
        s.produce("tier", "large", {"kind": "run_record", "run": "r1", "pos": 1}, timestamp=0)
    assert F.fact_errors(F.Fact("x", 1, "code: x", {}, 0.0), 5) == ["no_source", "no_timestamp"]


def test_freshness_refuses_a_future_position_and_a_newer_source_version():
    f = F.Fact("tier", "large", "code: x", {"kind": "run_record", "run": "r1", "pos": 9}, 1.0)
    assert F.fact_errors(f, 5) == ["future_record_position"]
    t = F.Fact("diff_size", 4, "code: x", {"kind": "target", "commit": "aaa", "path": "a.py"}, 1.0)
    assert F.fact_errors(t, 5, latest=lambda s: "bbb") == ["source_has_a_newer_version"]


def test_false_positive_an_old_fact_from_an_unmoved_source_passes():
    old = F.Fact("diff_size", 4, "code: x", {"kind": "target", "commit": "aaa", "path": "a.py"}, 0.001)
    assert F.fact_errors(old, 500, latest=lambda s: "aaa") == []
    early = F.Fact("tier", "large", "code: x", {"kind": "run_record", "run": "r1", "pos": 2}, 1.0)
    assert F.fact_errors(early, 400) == []


def test_the_router_graph_registry_serves_the_same_store():
    d = router()
    s = F.FactStore(d["facts"], from_doc(d).nodes, "r1")
    assert s.registered("git") and s.registered("outcome:route") and not s.registered("tier")
