"""SPEC 889 #920: a model switched off at model level is off in every chain, and its eligibility reason says so.

The switch is one key in the models file, `model_off`, keyed by model id. The one eligibility function reads it, so the
hub, the runs and the review chain agree (ADR-0147).
"""
import json

import squad.modelreg as mr

V1 = {
    "coding_models": ["claude-opus-5-5", "grok-4.7"],
    "review_models": ["grok-4.7"],
    "model_off": {"grok-4.7": True},
}

V2 = {"schema_version": 2, "chains": {
    "coding": [{"id": "claude-opus-5-5", "route": "gateway", "effort": "medium", "hrs": None,
                "on": True, "timeout_s": None}],
    "review": [{"id": "grok-4.7", "route": "gateway", "effort": None, "hrs": None,
                "on": True, "timeout_s": None}],
    "vision": [],
    "router": [],
}, "model_off": {"claude-opus-5-5": True}}


def _entry(tmp_path, doc, chain, mid):
    path = tmp_path / "models.json"
    path.write_text(json.dumps(doc))
    return next(e for e in mr.chain(path, chain).entries if e.id == mid)


def test_a_model_off_at_model_level_is_read_into_every_chain_it_sits_in(tmp_path):
    coding = _entry(tmp_path, V1, "coding", "grok-4.7")
    review = _entry(tmp_path, V1, "review", "grok-4.7")
    assert coding.model_off is True and review.model_off is True


def test_a_model_off_entry_is_not_eligible_and_says_so(tmp_path):
    entry = _entry(tmp_path, V1, "review", "grok-4.7")
    ev = mr.eligible(entry, shared=tmp_path, hour=12)
    assert ev.ok is False
    assert ev.reason == "off (model)", ev.reason


def test_a_model_on_at_model_level_stays_eligible(tmp_path):
    entry = _entry(tmp_path, V1, "coding", "claude-opus-5-5")
    assert entry.model_off is False
    assert mr.eligible(entry, shared=tmp_path, hour=12).ok is True


def test_the_model_switch_reads_from_a_v2_file_too(tmp_path):
    entry = _entry(tmp_path, V2, "coding", "claude-opus-5-5")
    assert entry.model_off is True
    ev = mr.eligible(entry, shared=tmp_path, hour=12)
    assert ev.ok is False and ev.reason == "off (model)", ev.reason


def test_a_model_off_wins_over_a_chain_row_that_is_on(tmp_path):
    entry = _entry(tmp_path, V2, "coding", "claude-opus-5-5")
    assert entry.on is True and entry.model_off is True
