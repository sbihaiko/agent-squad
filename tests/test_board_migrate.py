"""The migration planner moves today's cards onto the shipped vocabulary, then lists the old options for removal (ADR-0143)."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "contrib" / "squad-goal" / "scripts"
SCRIPT = SCRIPTS / "board_migrate.py"
MOVED = ("Blocked", "In Progress", "Done", "Open Specs", "Merging")
RETIRED = ("Testing", "Pushing")   # the definition no longer names them, and no column replaces them
OLD = MOVED + RETIRED
ITEMS = [
    {"n": 1, "st": "Open Specs", "s": "OPEN", "l": []},
    {"n": 2, "st": "Open Specs", "s": "OPEN", "l": ["spec"]},
    {"n": 3, "st": "Blocked", "s": "OPEN", "l": ["needs-human"], "facts": {"wave_branch": True}},
    {"n": 4, "st": "Blocked", "s": "OPEN", "l": []},
    {"n": 5, "st": "In Progress", "s": "OPEN", "l": []},
    {"n": 6, "st": "Done", "s": "CLOSED", "l": []},
    {"n": 7, "st": "Todo", "s": "OPEN", "l": []},
    {"n": 8, "st": "Doing", "s": "OPEN", "l": []},
    {"n": 9, "st": "Merging", "s": "OPEN", "l": []},
    {"n": 10, "st": "Landing", "s": "OPEN", "l": []},
]


@pytest.fixture(scope="module")
def mig():
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("board_migrate", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _move(plan, n):
    return next(m for m in plan["moves"] if m["card"] == n)


def test_an_open_spec_lands_in_backlog_with_the_spec_label(mig):
    plan = mig.plan(ITEMS)

    assert _move(plan, 1) == {"card": 1, "old": "Open Specs", "new": "Backlog", "add_labels": ["spec"]}
    assert _move(plan, 2)["add_labels"] == []


def test_a_blocked_card_lands_where_its_work_reached_with_the_owner_marker(mig):
    plan = mig.plan(ITEMS)

    assert _move(plan, 3) == {"card": 3, "old": "Blocked", "new": "Landing", "add_labels": ["frozen:owner"]}
    assert _move(plan, 4)["new"] == "Backlog"


def test_a_card_closed_as_not_planned_moves_to_abandoned(mig):
    """#921: the issue closed as NOT_PLANNED, or as a duplicate, was not delivered, so the migration moves its card to
    Abandoned."""
    items = [{"n": 11, "st": "Done", "s": "CLOSED", "l": [], "cr": "not_planned"},
             {"n": 12, "st": "Done", "s": "CLOSED", "l": [], "cr": "completed"},
             {"n": 13, "st": "Done", "s": "CLOSED", "l": [], "cr": "duplicate"}]

    plan = mig.plan(items)

    assert _move(plan, 11)["new"] == "Abandoned"
    assert _move(plan, 12)["new"] == "Closed"
    assert _move(plan, 13)["new"] == "Abandoned"


def test_merging_takes_the_shipped_landing_and_a_landing_card_is_never_moved(mig):
    plan = mig.plan(ITEMS)

    assert _move(plan, 9)["new"] == "Landing"
    assert 10 not in {m["card"] for m in plan["moves"]}, "the legacy Landing card already holds the shipped column"


def test_a_card_on_a_shipped_column_is_never_moved(mig):
    plan = mig.plan(ITEMS)

    assert {m["card"] for m in plan["moves"]} == {1, 2, 3, 4, 5, 6, 9}


def test_the_plan_lists_every_old_option_for_removal_after_the_moves(mig):
    plan = mig.plan(ITEMS)

    assert sorted(r["option"] for r in plan["removals"]) == sorted(OLD)
    assert all(r["remaining"] == 0 for r in plan["removals"])


def test_removal_is_refused_with_counts_while_a_card_carries_an_option(mig):
    counts = mig.carried(ITEMS)

    assert counts == {"Blocked": 2, "In Progress": 1, "Done": 1, "Open Specs": 2, "Merging": 1}
    assert "Blocked: 2" in mig.refusal(counts) and "Merging: 1" in mig.refusal(counts)


def test_removal_is_allowed_once_no_card_carries_an_old_option(mig):
    assert mig.carried([i for i in ITEMS if i["st"] not in OLD]) == {}


def test_a_retired_option_is_never_moved_and_its_removal_is_refused(mig):
    """Testing and Pushing are retired: no column replaces them, so a card still on one blocks the removal by name."""
    items = [{"n": 30, "st": "Testing", "s": "OPEN", "l": []},
             {"n": 31, "st": "Pushing", "s": "OPEN", "l": []},
             {"n": 32, "st": "Todo", "s": "OPEN", "l": []}]

    plan = mig.plan(items)

    assert plan["moves"] == [], "a card on a retired option is moved by the migration"
    assert {r["option"]: r["remaining"] for r in plan["removals"]}["Testing"] == 1
    assert {r["option"]: r["remaining"] for r in plan["removals"]}["Pushing"] == 1
    counts = mig.carried(items)
    assert counts == {"Testing": 1, "Pushing": 1}
    assert "Testing: 1" in mig.refusal(counts) and "Pushing: 1" in mig.refusal(counts)


def _run(tmp_path, *args, items=ITEMS):
    f = tmp_path / "items.json"
    f.write_text(json.dumps(items))
    return subprocess.run([sys.executable, str(SCRIPT), *args, str(f)], capture_output=True, text=True)


def test_the_dry_run_prints_every_move_and_removal_and_writes_nothing(tmp_path):
    before = sorted(p.name for p in tmp_path.iterdir())
    r = _run(tmp_path, "--plan")
    out = r.stdout

    assert r.returncode == 0, r.stderr
    assert "dry run: nothing is written" in out
    assert "MOVE #1 Open Specs -> Backlog +spec" in out
    assert "MOVE #3 Blocked -> Landing +frozen:owner" in out
    assert "MOVE #9 Merging -> Landing" in out
    assert "MOVE #10" not in out, "a card on the shipped Landing column is never moved"
    assert out.count("MOVE #") == 7
    assert all(f"REMOVE option {o}" in out for o in OLD)
    assert "REMOVE option Testing" in out and "REMOVE option Pushing" in out
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(before + ["items.json"])


def test_the_check_refuses_with_counts_and_exit_code(tmp_path):
    r = _run(tmp_path, "--check")

    assert r.returncode == 3
    assert "Blocked: 2" in r.stderr and "Done: 1" in r.stderr


def test_the_check_refuses_a_retired_option_with_its_count(tmp_path):
    r = _run(tmp_path, "--check", items=[{"n": 30, "st": "Testing", "s": "OPEN", "l": []},
                                         {"n": 31, "st": "Pushing", "s": "OPEN", "l": []}])

    assert r.returncode == 3
    assert "Testing: 1" in r.stderr and "Pushing: 1" in r.stderr


def test_the_check_passes_when_no_card_carries_an_old_option(tmp_path):
    r = _run(tmp_path, "--check", items=[i for i in ITEMS if i["st"] not in OLD])

    assert r.returncode == 0


def test_a_card_on_a_terminal_column_stays_and_a_closed_old_card_lands_in_closed(mig):
    items = [{"n": 20, "st": "Closed", "s": "CLOSED", "l": []}, {"n": 21, "st": "Abandoned", "s": "CLOSED", "l": []},
             {"n": 22, "st": "Blocked", "s": "CLOSED", "l": [], "facts": {"pr_open": True}}]
    plan = mig.plan(items)

    assert [(m["card"], m["new"]) for m in plan["moves"]] == [(22, "Closed")]
