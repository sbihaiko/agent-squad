"""The end-reason class of a failed child: a structured field, read by the session and folded into the verdict (#2217)."""

from squad import session
from squad import verdict as V
from tests.test_verdict import events, out


def test_a_fail_artifact_names_its_class_only_by_the_structured_field():
    assert session.end_class({"status": "fail", "end_class": "owner-only", "reasons": ["gate red"]}) == "owner-only"
    assert session.end_class({"status": "fail", "end_class": "does-not-reproduce"}) == "does-not-reproduce"
    assert session.end_class({"status": "fail", "reasons": ["owner-only: criterion (1) needs a credential"]}) is None
    assert session.end_class({"status": "fail", "end_class": "gate"}) is None
    assert session.end_class({"status": "pass", "end_class": "owner-only"}) is None


def test_a_failed_child_with_an_owner_only_class_puts_it_in_the_verdict_record():
    evs = events(dict(out("a", "fail", ["owner-only: criterion (1) needs a credential"]), end_class="owner-only"), out("main", "fail", ["owner-only: criterion (1) needs a credential"]))
    v = V.fold(evs)
    assert v.verdict == "failed"
    assert v.end_class == "owner-only"
    assert v.event_fields()["end_class"] == "owner-only"


def test_a_gate_failure_carries_no_end_class_in_the_verdict_record():
    evs = events(out("a", "fail", ["ac-verify:no_ac_commands"]), out("main", "fail", ["ac-verify:no_ac_commands"]))
    v = V.fold(evs)
    assert v.verdict == "failed"
    assert v.end_class is None
    assert "end_class" not in v.event_fields()
