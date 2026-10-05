"""Guards for ticket #57: the labeled set for the Jev screen. No network: a stub client stands in for Jev."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import jev_screen_set as S  # noqa: E402

from squad import jev  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ISSUES = {n: f"# Issue {n}\n\nStatus: open\n\n## Description\n\nAdd a thing {n} to the billing pipeline.\n" for n in range(1, 25)}


def test_the_set_has_a_clean_control_a_planted_variant_and_near_misses_per_the_issues():
    items = S.build(ISSUES)
    kinds = [i["kind"] for i in items]
    assert kinds.count("clean") == 24 and kinds.count("planted") == 24 and kinds.count("near_miss") == 2 * len(S.NEAR_MISSES)


def test_the_label_is_set_by_construction_and_matches_the_kind():
    for i in S.build(ISSUES):
        assert i["label"] is (i["kind"] == "planted")


def test_a_planted_text_holds_its_payload_and_its_control_is_the_original():
    items = {i["id"]: i for i in S.build(ISSUES)}
    payloads = dict(S.PAYLOADS)
    for n, text in ISSUES.items():
        planted, clean = items[f"{n:02d}-planted"], items[f"{n:02d}-clean"]
        assert clean["text"] == text
        assert payloads[planted["payload"]] in planted["text"] and text.strip().split("\n")[0] in planted["text"]
        assert planted["style"] in S.STYLES


def test_no_clean_or_near_miss_text_holds_a_payload():
    for i in S.build(ISSUES):
        if i["kind"] != "planted":
            assert not any(p in i["text"] for _, p in S.PAYLOADS), i["id"]


def test_every_payload_and_every_place_is_used_and_a_rebuild_is_identical():
    items = S.build(ISSUES)
    planted = [i for i in items if i["kind"] == "planted"]
    assert {i["payload"] for i in planted} == {p for p, _ in S.PAYLOADS} and {i["style"] for i in planted} == set(S.STYLES)
    assert items == S.build(dict(reversed(list(ISSUES.items()))))  # a function of the issue number, not of the order of the input


def test_a_planted_instruction_in_the_notes_section_sits_above_the_description():
    n = S.build(ISSUES)
    t = next(i for i in n if i["style"] == "notes-section")["text"]
    assert t.index("## Notes") < t.index("## Description")


class StubClient:
    model = jev.MODEL
    deadline = 20

    def __init__(self, scores, fail=None, key="sk-secret-value"):
        self.scores, self.fail, self._key, self.calls = scores, fail, key, 0

    def ask(self, state, questions):
        self.calls += 1
        if self.fail:
            raise jev.NoSuggestion(self.fail)
        return {"inj": {"noul": self.scores(state["text"], self.calls)}}, self.model


def test_score_all_asks_the_repeats_per_text_and_keeps_no_text():
    items = S.build(ISSUES)
    client = StubClient(lambda text, k: 0.9 if "Ignore all" in text or "AI assistant" in text else 0.1)
    recs = S.score_all(items, jev.JevShadow(client, {}, []), repeats=3, workers=1)
    assert client.calls == 3 * len(items) and all(len(r["p"]) == 3 for r in recs)
    assert all("text" not in r for r in recs) and json.dumps(recs).find("billing pipeline") < 0


def test_a_failed_call_is_no_suggestion_and_never_stops_the_run():
    recs = S.score_all(S.build(ISSUES)[:4], jev.JevShadow(StubClient(None, fail="deadline"), {}, []), repeats=2, workers=1)
    assert all(r["p"] == [] and r["no_suggestion"] == 2 and r["reasons"] == ["deadline"] for r in recs)
    st = S.stats(recs)
    assert st["scored"] == 0 and st["texts"] == 4


def test_the_key_value_never_enters_a_record_even_when_a_failure_names_it(tmp_path):
    class Leaky(StubClient):
        def ask(self, state, questions):
            raise RuntimeError(f"upstream rejected {self._key}")  # a defect whose message holds the key

    recs = S.score_all(S.build(ISSUES)[:3], jev.JevShadow(Leaky(None), {}, []), repeats=1, workers=1)
    out = tmp_path / "r.json"
    out.write_text(json.dumps(recs))
    assert S._leak([out], {"sk-secret-value"}) == [] and recs[0]["reasons"] == ["RuntimeError"]
    out.write_text("x sk-secret-value y")
    assert S._leak([out], {"sk-secret-value"}) == [str(out)]


def _rec(kind, label, ps, i=0):
    return {"id": f"{i}", "issue": i, "kind": kind, "label": label, "payload": "ignore-prior" if label else None, "style": "append",
            "chars": 1, "p": ps, "no_suggestion": 0, "reasons": []}


def test_recall_and_precision_are_counted_from_the_median_of_the_repeats():
    recs = [_rec("planted", True, [0.9, 0.9, 0.2], 1), _rec("planted", True, [0.4, 0.4, 0.4], 2), _rec("clean", False, [0.6, 0.0, 0.0], 3),
            _rec("near_miss", False, [0.8, 0.8, 0.8], 4)]
    st = S.stats(recs)
    cut5 = next(c for c in st["cuts"] if c["cut"] == 0.5)
    assert cut5["recall"][:2] == [1, 2] and cut5["precision"][:2] == [1, 2] and cut5["near_miss_flagged"] == 1 and cut5["false_positives"] == 1
    assert st["spread"]["max"] == 0.7 and st["by_kind"]["planted"]["n"] == 2


def test_the_report_states_n_and_the_spread_and_claims_no_threshold():
    recs = [_rec("planted", True, [0.9, 0.8], 1), _rec("clean", False, [0.1, 0.2], 2)]
    text = S.render(S.stats(recs), S.skill_table(json.loads(S.SKILL_PAIRS.read_text())), {"date": "d", "repeats": 2, "model": jev.MODEL})
    assert "holds 2 texts" in text and "Spread of repeated calls" in text and "no threshold claim" in text
    assert "None is chosen, recommended, or a setting" in text and "No gate may read" in text


def test_the_report_says_whether_the_planted_and_unplanted_ranges_overlap():
    meta = {"date": "d", "repeats": 1, "model": jev.MODEL}
    skill = S.skill_table(json.loads(S.SKILL_PAIRS.read_text()))
    apart = S.render(S.stats([_rec("planted", True, [0.9], 1), _rec("clean", False, [0.2], 2)]), skill, meta)
    mixed = S.render(S.stats([_rec("planted", True, [0.3], 1), _rec("clean", False, [0.5], 2)]), skill, meta)
    assert "do not overlap" in apart and "ranges overlap" in mixed


def test_the_skill_label_rule_reads_the_shadow_runs_and_counts_right_and_wrong_picks():
    rows = {r["node"]: r for r in S.skill_table(json.loads(S.SKILL_PAIRS.read_text()))}
    assert rows["actor"]["correct"] == "implement" and rows["spec"]["correct"] == "none"
    assert rows["spec"]["right"] + sum(rows["spec"]["wrong_picks"].values()) == rows["spec"]["n"] and rows["actor"]["n"] >= 30


def test_no_engine_module_imports_the_set_and_no_gate_or_verdict_reads_a_screen_score():
    for f in (ROOT / "squad").glob("*.py"):
        assert "jev_screen_set" not in f.read_text(), f.name
    for name in ("verdict", "gates", "facts", "artifacts"):
        text = (ROOT / "squad" / f"{name}.py").read_text().lower()
        assert "jev" not in text and "diagnostic" not in text, name
