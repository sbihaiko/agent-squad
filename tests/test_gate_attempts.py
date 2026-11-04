"""A gate attempt is journaled once and projected to the card's Gate Tries (#1255).

The flow-sample journal (board_flow_history.history_path) is the source: gate.sh appends one
`gate_attempt` record per verdict (a memo reuse is a `gate_reuse` marker, never an attempt), and
`Gate Tries` is the projection: one number per card, replayed from the journal, monotonically
rising, written only when the value changes. The projection itself lives in issue_state
(`set_gate_tries`) and is tested there (ADR-0144, ADR-0149, ADR-0168, ADR-0170).
"""
import importlib.util
import json
import pathlib
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def ga():
    return _load("gate_attempts_under_test", SCRIPTS / "gate_attempts.py")


@pytest.fixture()
def flow():
    return _load("flow_under_test", SCRIPTS / "board_flow_history.py")


@pytest.fixture()
def journal(tmp_path):
    return tmp_path / "hub-flow.x_y.jsonl"


def _lines(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class TestJournal:
    def test_the_attempt_journal_is_the_flow_sample_journal(self, ga, flow, tmp_path):
        """One file, so compaction and the projection read the same records (#1255)."""
        assert ga.journal_path(tmp_path / "board.json", "a/b") == flow.history_path(tmp_path / "board.json", "a/b")


class TestRecord:
    def test_one_attempt_adds_one_record(self, ga, journal):
        ga.record_attempt(journal, 1255, "abc", "pass", "a1", now=100)
        recs = [r for r in _lines(journal) if r.get("k") == "gate_attempt"]
        assert len(recs) == 1
        assert recs[0]["n"] == 1255 and recs[0]["tip"] == "abc"
        assert recs[0]["v"] == "pass" and recs[0]["t"] == 100

    def test_the_ordinal_counts_prior_attempts_of_the_same_card_and_tip(self, ga, journal):
        ga.record_attempt(journal, 1255, "abc", "fail", "a1", now=100)
        ga.record_attempt(journal, 1255, "abc", "pass", "a2", now=200)
        ords = [r["ord"] for r in _lines(journal) if r.get("k") == "gate_attempt"]
        assert ords == [1, 2]

    def test_two_attempts_on_different_tips_are_distinguishable(self, ga, journal):
        ga.record_attempt(journal, 1255, "abc", "fail", "a1", now=100)
        ga.record_attempt(journal, 1255, "def", "fail", "a2", now=200)
        recs = [r for r in _lines(journal) if r.get("k") == "gate_attempt"]
        assert [(r["tip"], r["ord"]) for r in recs] == [("abc", 1), ("def", 1)]

    def test_a_duplicate_identity_is_never_appended_twice(self, ga, journal):
        ga.record_attempt(journal, 1255, "abc", "fail", "a1", now=100)
        again = ga.record_attempt(journal, 1255, "abc", "fail", "a1", now=100)
        recs = [r for r in _lines(journal) if r.get("k") == "gate_attempt"]
        assert len(recs) == 1
        assert again["ord"] == 1   # the identity that exists answers, no second record

    def test_a_deadline_stop_is_an_unproven_attempt(self, ga, journal):
        rec = ga.record_attempt(journal, 1255, "abc", "unproven", "a1", now=100)
        assert rec["v"] == "unproven"
        assert ga.counts(journal)[1255] == 1

    def test_a_verdict_outside_pass_fail_unproven_is_refused(self, ga, journal):
        with pytest.raises(ValueError):
            ga.record_attempt(journal, 1255, "abc", "green", "a1", now=100)
        assert _lines(journal) == []

    def test_a_line_read_twice_counts_once(self, ga, journal):
        """A journal line duplicated by a crash in the middle of a write is still one attempt."""
        ga.record_attempt(journal, 1255, "abc", "fail", "a1", now=100)
        text = journal.read_text()
        journal.write_text(text + text)
        assert ga.counts(journal) == {1255: 1}
        assert len(ga.attempts(journal)) == 1

    def test_a_reuse_marker_adds_no_attempt(self, ga, journal):
        ga.record_attempt(journal, 1255, "abc", "pass", "a1", now=100)
        ga.record_reuse(journal, 1255, "abc", "m1", now=200)
        assert [r["k"] for r in _lines(journal)] == ["gate_attempt", "gate_reuse"]
        assert ga.counts(journal)[1255] == 1

    def test_the_identity_is_stable_inputs_not_the_caller_clock(self, ga, journal):
        """The identity is (card, tip, ordinal-before-append), not a pid- or clock-derived aid: a
        crash-restart appends nothing, whatever the replaying process carries (#1255 review)."""
        ga.record_attempt(journal, 1255, "abc", "fail", now=100)
        again = ga.record_attempt(journal, 1255, "abc", "pass", now=999999)
        recs = [r for r in _lines(journal) if r.get("k") == "gate_attempt"]
        assert len(recs) == 1
        assert again["ord"] == 1 and recs[0]["v"] == "fail"   # the journaled record answers

    def test_one_aid_names_one_attempt_across_a_retry_chain(self, ga, journal):
        """A landing's retry chain reruns one attempt: the first gate, the rerun and the re-gate
        carry one aid, and the chain journals one attempt, never three."""
        ga.record_attempt(journal, 1255, "abc", "fail", aid="chain-1", now=100)
        ga.record_attempt(journal, 1255, "abc", "fail", aid="chain-1", now=200)
        ga.record_attempt(journal, 1255, "abc", "pass", aid="chain-1", now=300)
        recs = [r for r in _lines(journal) if r.get("k") == "gate_attempt"]
        assert len(recs) == 1 and recs[0]["ord"] == 1
        assert ga.counts(journal)[1255] == 1

    def test_one_aid_is_one_attempt_across_tips(self, ga, journal):
        """A landing's ruff fix commits before the re-gate: the tip changes, the aid does not, so
        the chain still journals one attempt and Gate Tries counts one (#1255 review)."""
        ga.record_attempt(journal, 1255, "abc", "fail", aid="chain-x", now=100)
        ga.record_attempt(journal, 1255, "def", "pass", aid="chain-x", now=200)
        recs = [r for r in _lines(journal) if r.get("k") == "gate_attempt"]
        assert len(recs) == 1 and recs[0]["tip"] == "abc"
        assert ga.counts(journal) == {1255: 1}

    def test_a_reuse_marker_is_one_per_card_tip_and_memo(self, ga, journal):
        """Repeated memo hits on one tip answer the marker already journaled: the journal grows
        by one marker per (card, tip, memo signature), never one per hit."""
        ga.record_reuse(journal, 1255, "abc", "memo-1", now=100)
        ga.record_reuse(journal, 1255, "abc", "memo-1", now=200)
        ga.record_reuse(journal, 1255, "abc", "memo-2", now=300)
        ga.record_reuse(journal, 1255, "def", "memo-1", now=400)
        recs = [r for r in _lines(journal) if r.get("k") == "gate_reuse"]
        assert len(recs) == 3
        assert ga.counts(journal) == {}


class TestProjection:
    def test_the_count_is_the_distinct_attempt_identities_of_the_card(self, ga, journal):
        ga.record_attempt(journal, 1255, "abc", "fail", "a1", now=100)
        ga.record_attempt(journal, 1255, "abc", "fail", "a1", now=100)   # duplicate: never twice
        ga.record_attempt(journal, 1255, "def", "pass", "a3", now=200)
        ga.record_reuse(journal, 1255, "def", "m2", now=300)
        assert ga.counts(journal) == {1255: 2}

    def test_a_crash_between_journal_and_field_replays_to_one_count(self, ga, journal):
        # The journal append survived; the field write did not, and the replay ran under a new
        # process with a new clock. The count replays one, because the identity is stable inputs.
        ga.record_attempt(journal, 1255, "abc", "fail", now=100)
        ga.record_attempt(journal, 1255, "abc", "pass", now=200)   # the replayed invocation
        assert ga.counts(journal) == {1255: 1}

    def test_a_retry_chain_replays_to_one_count(self, ga, journal):
        ga.record_attempt(journal, 1255, "abc", "fail", aid="chain-9", now=100)
        ga.record_attempt(journal, 1255, "abc", "pass", aid="chain-9", now=200)   # the chain's re-gate
        assert ga.counts(journal) == {1255: 1}

    def test_a_card_with_no_attempt_reads_zero(self, ga, journal):
        assert ga.counts(journal) == {}


class TestCompaction:
    def test_the_flow_journal_rollup_keeps_attempt_records(self, ga, flow, journal):
        # Attempts older than the raw window survive a compaction pass, unlike samples and skips.
        old = int(time.time()) - (flow.RAW_DAYS + 1) * flow.DAY
        ga.record_attempt(journal, 1255, "abc", "fail", "a1", now=old)
        ga.record_reuse(journal, 1255, "abc", "m1", now=old + 1)
        row = {"n": 1, "t": "t", "s": "open", "st": "Todo", "l": []}
        flow.append(journal, [row], now=int(time.time()))
        kinds = [r.get("k") for r in _lines(journal)]
        assert kinds.count("gate_attempt") == 1
        assert "gate_reuse" in kinds
        assert ga.counts(journal)[1255] == 1

    def test_an_old_sample_beside_an_attempt_rolls_up_and_the_attempt_survives(self, ga, flow, journal):
        """The rollup's old set filters on the record's kind: a sample of the same age as an
        attempt is dropped, the attempt stays -- the guarantee is implemented, not incidental."""
        old = int(time.time()) - (flow.RAW_DAYS + 1) * flow.DAY
        row = {"n": 1, "t": "t", "s": "open", "st": "Todo", "l": []}
        two = [row, {"n": 2, "t": "t2", "s": "open", "st": "Doing", "l": []}]
        flow.append(journal, [row], now=old)          # an old sample
        flow.append(journal, two, now=old + 60)       # the same day's later sample: the first rolls up
        ga.record_attempt(journal, 1255, "abc", "fail", "a1", now=old + 2)
        ga.record_reuse(journal, 1255, "abc", "m1", now=old + 3)
        flow.append(journal, [row], now=int(time.time()))   # a fresh pass compacts the file
        recs = _lines(journal)
        samples = [r for r in recs if isinstance(r.get("counts"), dict)]
        assert len(samples) == 2, "the day's last old sample and the fresh one stay; the earlier rolled up"
        assert sum(1 for r in recs if r.get("k") == "gate_attempt") == 1
        assert sum(1 for r in recs if r.get("k") == "gate_reuse") == 1
        assert ga.counts(journal)[1255] == 1
