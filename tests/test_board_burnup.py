"""The spec burn-up (#1025): a scope line, a completion line and a held line per spec, and the marks that move them."""
import importlib.util
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "contrib" / "squad-goal" / "scripts"
SPEC = 1018


def _load():
    spec = importlib.util.spec_from_file_location("board_history", SCRIPTS / "board_history.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bh = _load()


def day(d):
    return f"2026-10-0{d}T09:00:00Z"


def ev(d, kind, **fields):
    return {"t": day(d), "type": kind, **fields}


def card(*events):
    return {"events": list(events), "complete": True}


def point(d, scope, done=0, held=0, marks=()):
    return {"t": day(d), "scope": scope, "done": done, "held": held, "marks": [{"k": k, "n": n} for k, n in marks]}


def test_a_child_added_to_the_spec_raises_the_scope_and_is_marked():
    store = {7: card(ev(1, "added"), ev(1, "status", status="Todo"), ev(2, "spec", spec=SPEC))}
    assert bh.burnup(store, SPEC)["points"] == [point(2, 1, marks=[("added", 7)])]


def test_a_child_taken_off_the_spec_lowers_the_scope_and_is_marked():
    store = {7: card(ev(1, "added"), ev(1, "status", status="Todo"), ev(1, "spec", spec=SPEC), ev(3, "spec", spec=None))}
    assert bh.burnup(store, SPEC)["points"] == [point(1, 1, marks=[("added", 7)]), point(3, 0, marks=[("removed", 7)])]


def test_a_child_removed_from_the_board_lowers_the_scope_and_is_marked():
    store = {7: card(ev(1, "added"), ev(1, "status", status="Todo"), ev(1, "spec", spec=SPEC), ev(3, "removed"))}
    assert bh.burnup(store, SPEC)["points"] == [point(1, 1, marks=[("added", 7)]), point(3, 0, marks=[("removed", 7)])]


def test_a_cancelled_child_lowers_the_scope_and_is_marked():
    store = {7: card(ev(1, "added"), ev(1, "status", status="Todo"), ev(1, "spec", spec=SPEC),
                     ev(4, "status", status="Abandoned"))}
    assert bh.burnup(store, SPEC)["points"] == [point(1, 1, marks=[("added", 7)]), point(4, 0, marks=[("cancelled", 7)])]


def test_a_child_cancelled_before_it_joins_never_enters_the_scope():
    store = {7: card(ev(1, "added"), ev(1, "status", status="Abandoned"), ev(2, "spec", spec=SPEC))}
    assert bh.burnup(store, SPEC)["points"] == []


def test_a_reopened_child_raises_the_scope_and_lowers_the_completion_line():
    store = {7: card(ev(1, "added"), ev(1, "status", status="Doing"), ev(1, "spec", spec=SPEC),
                     ev(3, "closed"), ev(5, "reopened"))}
    assert bh.burnup(store, SPEC)["points"] == [
        point(1, 1, marks=[("added", 7)]),
        point(3, 1, done=1),
        point(5, 1, done=0, marks=[("reopened", 7)]),
    ]


def test_a_card_reopened_from_cancelled_is_marked_reopened_and_raises_the_scope():
    store = {7: card(ev(1, "added"), ev(1, "status", status="Doing"), ev(1, "spec", spec=SPEC),
                     ev(2, "status", status="Abandoned"), ev(4, "status", status="Doing"))}
    assert bh.burnup(store, SPEC)["points"] == [
        point(1, 1, marks=[("added", 7)]),
        point(2, 0, marks=[("cancelled", 7)]),
        point(4, 1, marks=[("reopened", 7)]),
    ]


def test_a_parked_child_is_marked_and_stays_in_the_scope():
    store = {7: card(ev(1, "added"), ev(1, "status", status="Doing"), ev(1, "spec", spec=SPEC),
                     ev(4, "status", status="Backlog"))}
    assert bh.burnup(store, SPEC)["points"] == [point(1, 1, marks=[("added", 7)]), point(4, 1, marks=[("parked", 7)])]


def test_a_held_child_is_marked_and_counted_until_its_hold_clears():
    store = {7: card(ev(1, "added"), ev(1, "status", status="Doing"), ev(1, "spec", spec=SPEC),
                     ev(2, "label", name="frozen:owner"), ev(6, "unlabel", name="frozen:owner"))}
    assert bh.burnup(store, SPEC)["points"] == [
        point(1, 1, marks=[("added", 7)]),
        point(2, 1, held=1, marks=[("held", 7)]),
        point(6, 1, held=0, marks=[("released", 7)]),
    ]


def test_the_completion_line_counts_closed_children_of_the_scope():
    store = {
        7: card(ev(1, "added"), ev(1, "status", status="Doing"), ev(1, "spec", spec=SPEC), ev(3, "closed")),
        8: card(ev(1, "added"), ev(1, "status", status="Doing"), ev(1, "spec", spec=SPEC)),
    }
    assert bh.burnup(store, SPEC)["points"] == [
        point(1, 2, marks=[("added", 7), ("added", 8)]),
        point(3, 2, done=1),
    ]


def test_a_grandchild_or_another_spec_is_not_in_the_scope():
    store = {7: card(ev(1, "added"), ev(1, "status", status="Doing"), ev(1, "spec", spec=2000))}
    assert bh.burnup(store, SPEC)["points"] == []


def test_a_change_that_moves_no_line_makes_no_point():
    store = {
        7: card(ev(1, "added"), ev(1, "status", status="Doing"), ev(1, "spec", spec=SPEC)),
        8: card(ev(2, "added"), ev(2, "status", status="Todo")),
    }
    assert bh.burnup(store, SPEC)["points"] == [point(1, 1, marks=[("added", 7)])]
