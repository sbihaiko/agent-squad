"""Spec rules (#1962): a ticket that closes its spec ranks first, a spec with no dev progress past the bound is named, and the
hub dates each open spec from its first landed ticket. The bound is a profile setting with a plugin default.

Guards: `tests/test_spec_stall.py` (the rules and the setting), `tests/test_hub_spec_progress.py` (the hub age),
`contrib/squad-goal/scripts/test-janitor-spec-stall.sh` (the alert), `contrib/squad-goal/scripts/test-autofrontier-spec-first.sh` (the order).
"""
import importlib.util
from pathlib import Path

from squad import defaults
from squad import project_settings as P

ROOT = Path(__file__).resolve().parent.parent
SPECSTALL = ROOT / "contrib/squad-goal/scripts/specstall.py"


def _load():
    spec = importlib.util.spec_from_file_location("specstall", SPECSTALL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sp = _load()


def test_the_last_open_ticket_of_a_spec_closes_it():
    kids = {10: [11, 12]}
    assert sp.closers(kids, {10, 11, 12}, {11: (1, 1)}) == [12]


def test_a_ticket_whose_siblings_are_all_in_dev_closes_the_spec():
    kids = {10: [11, 12, 13]}
    assert sp.closers(kids, {10, 11, 12, 13}, {11: (1, 1), 12: (2, 2)}) == [13]


def test_no_ticket_closes_a_spec_with_two_open_tickets_none_in_dev():
    assert sp.closers({10: [11, 12]}, {10, 11, 12}, {}) == []


def test_a_closed_spec_names_no_closer():
    assert sp.closers({10: [11]}, {11}, {}) == []


def test_a_spec_with_no_dev_progress_past_the_bound_names_its_open_tickets():
    kids = {10: [11, 12]}
    assert sp.stalled(kids, {10, 11, 12}, {}, {11: 0, 12: 0}, now=1000, bound=100) == {10: [11, 12]}


def test_a_spec_whose_ticket_landed_inside_the_bound_is_not_named():
    kids = {10: [11, 12]}
    landed = {11: (950, 950)}
    assert sp.stalled(kids, {10, 11, 12}, landed, {}, now=1000, bound=100) == {}


def test_a_closed_spec_is_never_named_as_stalled():
    assert sp.stalled({10: [11]}, {11}, {}, {11: 0}, now=1000, bound=100) == {}


def test_a_dev_commit_names_a_ticket_by_number_in_its_subject():
    assert sp.landed_of([(500, "Fix the janitor (#11, #12)"), (700, "Refs #11")]) == {11: (500, 700), 12: (500, 500)}


def test_a_state_names_the_stuck_ticket_as_stranded_conflict_failed_run_or_blocked():
    cols = {1: "Doing", 2: "Doing", 3: "Doing", 4: "Blocked"}
    assert sp.state(1, cols, {1: False}, {}, set()) == "stranded"
    assert sp.state(2, cols, {2: True}, {2: "diverged"}, set()) == "conflict"
    assert sp.state(3, cols, {3: False}, {}, {3}) == "failed run"
    assert sp.state(4, cols, {}, {}, set()) == "waiting on a blocker"


def test_the_age_of_a_spec_runs_from_its_first_landed_ticket():
    assert sp.ages({10: [11, 12]}, {11: (100, 100), 12: (300, 300)}, now=1000) == {10: 900}


def test_the_spec_stall_bound_defaults_to_one_day_and_a_project_file_lowers_it():
    assert defaults.load()["janitor"]["spec_stall_seconds"] == 86400
    assert P.overlay(defaults.load(), {"janitor": {"spec_stall_seconds": 3600}})[0]["janitor"]["spec_stall_seconds"] == 3600


def test_a_spec_stall_bound_below_one_hour_is_refused_naming_the_key():
    bad = P.defects({"janitor": {"spec_stall_seconds": 60}})
    assert [k for k, _ in bad] == ["janitor.spec_stall_seconds"]
