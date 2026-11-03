"""The board facts have one runtime producer, and the column table reads them (ADR-0145)."""
import importlib.util
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "contrib" / "squad-goal" / "scripts"


def _module(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


facts = _module("board_facts")
columns = _module("board_columns")


def _column(**given):
    base = dict(entered=True, standing="ahead", approved=False, dev_running=False, review_waits=False)
    return columns.column_for(facts.issue_facts(**{**base, **given}))[0]


def test_a_child_in_the_wave_branch_is_code_complete():
    assert _column() == "Code Complete"


def test_an_approved_wave_still_ahead_of_dev_is_code_complete():
    assert _column(approved=True) == "Code Complete"


def test_a_wave_in_dev_with_a_running_ci_is_closed_not_held_open():
    assert _column(standing="behind", dev_running=True) == "Closed"
    assert columns.column_for({"wave_branch": True, "in_dev": True}) == ("Closed", None)


def test_a_wave_in_dev_with_no_running_ci_names_no_column_fact():
    produced = facts.issue_facts(entered=True, standing="identical", approved=True, dev_running=False, review_waits=False)

    assert not produced["dev_ci"] and not produced["wave_branch"] and not produced["landing"]


def test_a_child_that_did_not_enter_the_wave_has_no_wave_fact():
    produced = facts.issue_facts(entered=False, standing="ahead", approved=True, dev_running=True, review_waits=False)

    assert not (produced["wave_branch"] or produced["landing"] or produced["dev_ci"])


def test_a_waiting_review_marks_code_review():
    assert _column(entered=False, review_waits=True) == "Code Review"


def test_review_state_reads_the_verdict_for_the_tip_only():
    assert facts.review_state(None, None, "abc") == "none"
    assert facts.review_state("VERDICT: APPROVE\n", "abc\n", "abc") == "approved"
    assert facts.review_state("VERDICT: APPROVE\n", "old\n", "abc") == "waiting"
    assert facts.review_state("VERDICT: CHANGES\n", "abc\n", "abc") == "waiting"


def _spec(*children):
    """The facts of a spec whose sub-issue cards read `children` (the loop's own words for them)."""
    return {"spec": True, **facts.spec_facts(list(children))}


def test_a_spec_with_a_ticket_in_flight_reads_todo():
    for child in ("Todo", "In Progress", "Doing", "Code Review", "Code Complete", "Gating"):
        assert columns.column_for(_spec(child))[0] == "Todo"


def test_a_spec_whose_child_is_no_longer_a_column_reads_backlog():
    for child in ("Pushing", "Testing"):
        assert columns.column_for(_spec(child))[0] == "Backlog"


def test_a_spec_with_no_ticket_in_flight_reads_backlog():
    assert columns.column_for(_spec())[0] == "Backlog"
    assert columns.column_for(_spec("Backlog"))[0] == "Backlog"
    assert columns.column_for(_spec("Closed", "Abandoned"))[0] == "Backlog"


def test_an_open_work_issue_with_no_spec_label_still_reads_todo():
    assert columns.column_for({}) == ("Todo", None)
    assert columns.column_for({"child_live": False}) == ("Todo", None)


def test_the_child_live_fact_marks_a_ticket_in_flight():
    assert facts.spec_facts(["Todo", "Closed", "Abandoned"]) == {"child_live": True}
    assert facts.spec_facts(["Code Complete"]) == {"child_live": True}
    assert facts.spec_facts(["Gating"]) == {"child_live": True}
    assert facts.spec_facts(["Code Review", "Todo"]) == {"child_live": True}
    assert facts.spec_facts(["Backlog", "Closed"]) == {"child_live": False}


def test_the_hub_legacy_table_names_no_pull_request_fact():
    names = {c["fact"] for c in columns.COLUMNS}

    assert "merged" not in names and "open_pr" not in names
    assert set(columns.ROLE) == {"issue_closed", "abandoned", "unscheduled", "issue_todo"}
    assert "Testing" not in {s["name"] for s in columns.shipped_columns()}
