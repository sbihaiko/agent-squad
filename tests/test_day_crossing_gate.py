"""The day crossing is a gate (#1908, SPEC 1895).

One fixed record set — a day of run records and card transitions — is replayed through the board's rule layer and a fake
carrier. The columns come from the events, never from the fixture's own answer, so a rule that lets review time fall under
Doing, or a closed issue's card stand outside Closed, turns this test red. The fixture is checked in under
`tests/fixtures/`; the test reads no live loop record.
"""
import importlib.util
import json
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "day-crossing.json"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def rules():
    return _load("day_crossing_columns", SCRIPTS / "board_columns.py")


@pytest.fixture(scope="module")
def waits():
    return _load("day_crossing_wait", SCRIPTS / "board_wait.py")


@pytest.fixture(scope="module")
def history():
    return _load("day_crossing_history", SCRIPTS / "board_history.py")


@pytest.fixture(scope="module")
def profiler():
    return _load("day_crossing_profiler", SCRIPTS / "hub_profiler.py")


@pytest.fixture(scope="module")
def day():
    return json.loads(FIXTURE.read_text())


def _epoch(iso):
    if iso is None:
        return None
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


def _horizon(day):
    return _epoch(day["day"]["end"])


# The fact each recorded card transition names (SPEC 1895: the column follows the events, and the definition's closed
# grammar answers it). An added card is startable work; a close is its issue's own state.
FACT_OF_EVENT = {"added": {}, "dispatch": {"run_live": True}, "review_code_done": {"review_wait": True},
                 "gate": {"gating": True}, "wave_entry": {"wave_branch": True}, "land": {"wave_branch": True}}


def derived_column(rules, transition):
    """The column one recorded transition's event causes, through the rule layer: the definition's expression for the
    event's fact, or the issue's terminal column for a close."""
    if transition["event"] == "close":
        return rules.terminal_column("closed", "completed")
    column, _, unknown = rules.resolve_column(FACT_OF_EVENT[transition["event"]])
    assert unknown is None, f"the rule layer could not answer the column of {transition}: {unknown}"
    return column


class FakeCarrier:
    """The day's board, faked: a Status and a Wait per card, and every write it received. A write moves the carried value,
    as the board does for the next read."""

    def __init__(self, statuses):
        self.status = dict(statuses)
        self.wait = {}
        self.writes = []

    def write_status(self, issue, column):
        self.writes.append(("status", issue, column))
        self.status[issue] = column

    def read_waits(self):
        return dict(self.wait)

    def items(self):
        return {str(n): f"item-{n}" for n in self.status}

    def write_wait(self, item, name):
        issue = item.removeprefix("item-")
        self.writes.append(("wait", issue, name))
        self.wait[issue] = name


def replay(day, rules):
    """The day through the rule layer and the fake carrier: every transition writes the column its event derives, the
    forward-only rule refuses a backward move, and then one pass reads each issue's state and moves a closed issue's card
    to its terminal column (SPEC 1895: the close moves the card because of the issue's own state)."""
    carrier = FakeCarrier({})
    for transition in sorted(day["transitions"], key=lambda t: t["t"]):
        issue, want = transition["issue"], derived_column(rules, transition)
        if rules.refused_for_automated(carrier.status.get(issue), want):
            continue
        carrier.write_status(issue, want)
    for issue, meta in sorted(day["issues"].items()):
        want = rules.terminal_column(meta["state"], meta.get("state_reason"))
        if want and not rules.refused_for_automated(carrier.status.get(issue), want):
            carrier.write_status(issue, want)
    return carrier


def _events(day):
    """{issue: the card's recorded events} in the shape the history fold reads: an add, then each status, then a close
    only where the board recorded one. A card whose issue closed with no recorded move keeps the column it held."""
    out = {issue: [{"t": "2026-11-16T00:00:00Z", "type": "added"}] for issue in day["issues"]}
    for transition in sorted(day["transitions"], key=lambda t: t["t"]):
        out[transition["issue"]].append({"t": transition["t"], "type": "status", "status": transition["to"]})
    return out


def column_runs(day, history):
    """{issue: the column intervals the history fold reads from the card's events}, each (column, start, end) in epochs.
    The end of an open run is the day's horizon."""
    horizon = _horizon(day)
    out = {}
    for issue, events in _events(day).items():
        runs = []
        for run in history.intervals(events):
            if run["column"] is None or run["to"] == run["from"]:
                continue
            runs.append((run["column"], _epoch(run["from"]), _epoch(run["to"]) if run["to"] else horizon))
        out[issue] = runs
    return out


def _records(day, issue):
    return day["records"].get(issue, [])


def _with_ts(events):
    """One record's events with the numeric instant the trip fold reads. A record's `t` is ISO here, so the fixture stays
    readable and the fold reads its own shape."""
    return [{**e, "ts": _epoch(e["t"])} for e in events]


def stage_spans(day, issue):
    """{kind: [(start, end), ...]} in epochs, the stages the fixture's run record holds: coding (each child's first call to
    its last answer), fix (a fix round's call), review (the last answer to the review round), review_active (the round to
    the record's next event) and gate (a gate's start to its result)."""
    events = _records(day, issue)
    kids = {e.get("child") for e in events if e.get("event") == "spawn_accepted" and e.get("child")}
    starts, answers = {}, {}
    for i, e in enumerate(events):
        if e.get("event") == "call_start":
            starts[(e.get("node"), e.get("entry"))] = i
        elif e.get("event") == "outcome":
            answers[(e.get("node"), e.get("entry"))] = i
    spans = {"coding": [], "fix": [], "review": [], "review_active": [], "gate": []}
    for (node, entry), i in starts.items():
        j = answers.get((node, entry))
        if j is None or _epoch(events[j]["t"]) < _epoch(events[i]["t"]):
            continue
        kind = "fix" if events[i].get("round") == "fix" else "coding" if node in kids else None
        if kind:
            spans[kind].append((_epoch(events[i]["t"]), _epoch(events[j]["t"])))
    prior, gate_starts = None, {}
    for i, e in enumerate(events):
        kind, t = e.get("event"), _epoch(e["t"])
        if kind == "outcome" and e.get("node") in kids:
            prior = t
        elif kind == "review_round":
            if prior is not None and t >= prior:
                spans["review"].append((prior, t))
            end = _epoch(events[i + 1]["t"]) if i + 1 < len(events) else _horizon(day)
            spans["review_active"].append((t, max(t, end)))
            prior = t
        elif kind == "gate_start":
            gate_starts[(e.get("node"), e.get("item"))] = t
        elif kind == "gate_result":
            start = gate_starts.get((e.get("node"), e.get("item")))
            if start is not None and t >= start:
                spans["gate"].append((start, t))
    return spans


def wait_spans(day):
    """{issue: [(start, end), ...]} in epochs, every wait the card's record and fixture name: the run start (the run's
    start to its first child call, open when no call has landed) and each named wait of the fixture."""
    horizon, out = _horizon(day), {}
    for issue, events in day["records"].items():
        starts = [e for e in events if e.get("event") == "run_start"]
        calls = [e for e in events if e.get("event") == "call_start" and e.get("round") != "fix"]
        if starts:
            begin = _epoch(starts[0]["t"])
            out.setdefault(issue, []).append((begin, _epoch(calls[0]["t"]) if calls else horizon))
    for wait in day["waits"]:
        out.setdefault(wait["issue"], []).append((_epoch(wait["t"]), _epoch(wait["until"]) if wait["until"] else horizon))
    return out


def _covered(spans, lo, hi):
    """True when the union of the spans covers every point of lo..hi."""
    point = lo
    for begin, end in sorted(spans):
        if end <= point:
            continue
        if begin > point:
            return False
        point = max(point, end)
        if point >= hi:
            return True
    return point >= hi


def _overlap(spans, lo, hi):
    """The seconds of lo..hi the spans cover."""
    return sum(max(0, min(hi, end) - max(lo, begin)) for begin, end in spans)


def test_the_fixture_is_checked_in_and_carries_no_live_record(day):
    """#1908 criterion 5: the fixture lives under tests/fixtures and names no live loop path or personal record."""
    assert FIXTURE.is_file(), "the day's record set is not checked in"
    assert FIXTURE.parent == Path(__file__).resolve().parent / "fixtures"
    text = FIXTURE.read_text()
    for live in ("/Users/", "$SG_STATE", "squad-state", "runs/run-", "transcript"):
        assert live not in text, f"the fixture names a live loop record: {live}"
    assert day["issues"] and day["transitions"] and day["records"], "the fixture is not a day of records and moves"


def test_each_recorded_move_is_the_column_its_event_derives(day, rules):
    """#1908 criterion 1: the rule layer recomputes each card's column from the fixture's events, and the recorded move is
    the column it should have reached."""
    for transition in day["transitions"]:
        assert derived_column(rules, transition) == transition["to"], \
            f"the transition of {transition['issue']} at {transition['t']} names {transition['to']} for {transition['event']}"


def test_the_replay_through_the_fake_carrier_reaches_each_expected_column(day, rules):
    """#1908 criterion 1: replayed through the rule layer and the fake carrier, the day ends on the column each card
    should have reached, and every write the carrier received names an event's column."""
    carrier = replay(day, rules)
    assert carrier.status == day["expected"], f"the carrier stands on {carrier.status}"
    assert all(kind == "status" for kind, _, _ in carrier.writes), "a write the rule layer did not derive reached the carrier"


def test_each_cards_column_time_is_a_run_of_events_the_hub_reads(day, history):
    """#1908 criterion 1: the card's column intervals come from the rule layer's history fold over the fixture's
    transitions, so the crossing is measured on the columns a reader sees."""
    runs = column_runs(day, history)
    assert set(runs) == set(day["issues"])
    for issue, spans in runs.items():
        assert spans, f"card {issue} has no column interval"
        recorded = [t["to"] for t in day["transitions"] if t["issue"] == issue][-1]
        assert spans[-1][0] == recorded, f"card {issue} ends on {spans[-1][0]}, and its board recorded {recorded}"


def test_no_review_time_falls_under_doing(day, history):
    """#1908 criterion 2: the review time of the day — the wait for the round, the active round and a fix round — never
    overlaps an interval the card spends in Doing."""
    runs = column_runs(day, history)
    under = {}
    for issue, spans in runs.items():
        for column, lo, hi in spans:
            if column != "Doing":
                continue
            stages = stage_spans(day, issue)
            review = stages["review"] + stages["review_active"] + stages["fix"]
            seconds = _overlap(review, lo, hi)
            if seconds:
                under[issue] = under.get(issue, 0) + seconds
    assert under == {}, f"review time falls under Doing: {under}"


def test_every_doing_interval_carries_a_running_stage_or_a_named_wait(day, history, waits):
    """#1908 criterion 2: a card in Doing is either worked or waits with a name from the closed Wait set. A card with
    neither fails; a card that waits never does."""
    runs, named = column_runs(day, history), wait_spans(day)
    for issue, spans in runs.items():
        stages = stage_spans(day, issue)
        running = stages["coding"] + stages["fix"] + stages["review"] + stages["review_active"] + stages["gate"]
        for column, lo, hi in spans:
            if column != "Doing":
                continue
            assert _covered(running + named.get(issue, []), lo, hi), \
                f"card {issue} waits in Doing at {lo}..{hi} with no running stage and no named wait"
    for wait in day["waits"]:
        answer = waits.wait_of({"record": True, "labels": day["issues"][wait["issue"]]["labels"],
                                "run_live": wait["name"] == "live run", "queued": wait["name"]})
        assert answer.name == wait["name"], f"the no-wait reason {wait['name']} is not a Wait the rule layer names"


def test_every_code_review_interval_holds_a_review_or_a_fix_round_or_a_named_wait(day, history, waits):
    """#1908 criterion 3: a card in Code Review is never empty. A review round, a fix round or a named wait holds it, and
    a card that legitimately waits passes."""
    runs, named = column_runs(day, history), wait_spans(day)
    for issue, spans in runs.items():
        stages = stage_spans(day, issue)
        holding = stages["review"] + stages["review_active"] + stages["fix"] + named.get(issue, [])
        for column, lo, hi in spans:
            if column != "Code Review":
                continue
            assert _covered(holding, lo, hi), \
                f"card {issue} stands in Code Review at {lo}..{hi} with no review, no fix round and no named wait"


def test_a_card_that_legitimately_waits_in_review_never_fails_the_check(day, history, waits):
    """#1908 criterion 3: a card that waits for a review slot passes the check. Its Code Review interval is covered, and
    the rule layer names the wait, so a wait is never read as an empty column."""
    runs = column_runs(day, history)
    waiting = {issue: spans for issue, spans in runs.items() if any(w["issue"] == issue for w in day["waits"])}
    assert waiting, "the fixture holds no card that waits in Code Review"
    for issue, spans in waiting.items():
        stages = stage_spans(day, issue)
        holding = stages["review"] + stages["review_active"] + stages["fix"] + wait_spans(day).get(issue, [])
        for column, lo, hi in spans:
            if column == "Code Review":
                assert _covered(holding, lo, hi), f"card {issue} waits in Code Review and the check would fail it"
        for wait in (w for w in day["waits"] if w["issue"] == issue):
            answer = waits.wait_of({"record": True, "labels": day["issues"][issue]["labels"],
                                    "run_live": False, "queued": wait["name"]})
            assert answer.name == wait["name"], f"the wait {wait['name']} is not a Wait the rule layer names"


def test_one_pass_moves_every_closed_issues_card_to_closed(day, rules):
    """#1908 criterion 4: after one pass over the issue states, no card of a closed issue stands outside Closed. A card
    that waits for a live run still reaches Closed, and its Wait names the run."""
    carrier = replay(day, rules)
    for issue, meta in day["issues"].items():
        if meta["state"] == "closed":
            assert carrier.status[issue] == "Closed", f"closed issue {issue} stands on {carrier.status[issue]}"
    live = next((n for n, meta in day["issues"].items()
                 if meta["state"] == "closed" and any(e.get("event") == "run_start" for e in day["records"][n])
                 and not any(e.get("event") == "land" for e in day["records"][n])), None)
    assert live is not None, "the fixture holds no issue closed while its run still holds it"
    assert replay(day, rules).status[live] == "Closed"
    assert rules.terminal_column("closed", day["issues"][live]["state_reason"]) == "Closed"


def test_the_folds_stage_totals_match_the_crossing_on_each_delivery(day, profiler):
    """The crossing is measured on the same stages the trip fold reads (#1907): a delivery's coding, review and gate
    totals here equal the rule layer's own totals, so the gate never measures a stage the loop does not."""
    for issue, events in day["records"].items():
        spans = profiler.trip_spans(_with_ts(events), {"todo": None, "closed": None})
        if spans is None:
            continue
        mine = stage_spans(day, issue)
        assert sum(b - a for a, b in mine["coding"]) == spans["coding"], f"card {issue} coding total"
        assert sum(b - a for a, b in mine["review"]) == spans["review"], f"card {issue} review total"
        assert sum(b - a for a, b in mine["gate"]) == spans["gates"], f"card {issue} gate total"
