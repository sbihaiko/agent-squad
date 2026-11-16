"""The hub draws a card write the ledger holds pending, over a board cache that lags (#1841).

Every writer of a card -- the autobug dispatch, the janitor's reconcile and `reconcile.py` -- records its write in the issue
ledger before it calls the API, and marks it written only when the API agrees (ADR-0165). The hub reads a board cache that
lags for minutes, so a writer's own next pass used to read its own write as absent. The ledger's pending events are the
prediction of the board's next read: the hub overlays them on the cache, so a card the cache lacks is drawn in the column
its writer named, marked pending. A prediction older than a bound is reported as a named defect, never as a silently missing
card. The bound is the setting `SG_PENDING_WINDOW_SECONDS`, measured against the instant the cache read began, as the proved
moves' window is. The hub reads the ledger and never writes it: only the writer's own `boardw` or `boardx` ends a prediction
(ADR-0149).

The suite loads the hub for a throwaway profile whose state directory is its own, so the ledger it reads is the test's.
"""
import importlib.util
import pathlib
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
HUB_PY = SCRIPTS / "hub.py"


def _module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REPO = "x/y"


@pytest.fixture
def hub(monkeypatch, tmp_path):
    """The hub loaded for a throwaway profile, with its own state directory: the ledger is the test's, never the loop's."""
    state = tmp_path / "state"
    state.mkdir()
    for k, v in (("SG_REPO", REPO), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("HOME", str(tmp_path)),
                 ("SG_CHECKOUT", str(tmp_path)), ("SG_HUB_DIR", str(tmp_path / "hub")), ("SG_STATE", str(state))):
        monkeypatch.setenv(k, v)
    for k in ("SG_STATE_BASE", "SG_SHARED", "SG_PROFILE", "SG_PENDING_WINDOW_SECONDS", "SG_PENDING_OVERLAY"):
        monkeypatch.delenv(k, raising=False)
    return _module("hub_board_prediction", HUB_PY), state


def now():
    return time.time()


def card(n, status="Todo", **extra):
    """One row as the board pass stores it: `ot` is the instant its read began, the only stamp the overlay may use."""
    return {"n": n, "t": f"card {n}", "s": "OPEN", "st": status, "l": [], "i": f"ITEM{n}", **extra}


def lagging(mod, items, read_at):
    """The rows the last board pass read, and the instant its read began."""
    mod.board_cache = {"t": read_at + 1, "err": None, "errs": [], "items": [dict(i, ot=int(read_at)) for i in items],
                       "prs": [], "lc": {}, "cases": []}


def predict(mod, n, column, writer="janitor", instant=None):
    """One card write the writer recorded before its API call: a pending event in the issue's ledger."""
    key = f"{REPO}#{n}"
    assert mod.issue_ledger.board(key, writer, column, "", by=writer,
                                  instant=now() if instant is None else instant) == "recorded"


def where(board, n):
    """Every (column, row) the served board draws card `n` in."""
    return [(g["name"], x) for g in board["groups"] for x in g["items"] if x["n"] == n]


def test_a_prediction_draws_a_card_the_cache_lacks(hub):
    """The writer added a card or moved one the cache never carried: the prediction draws it in the named column, marked
    pending. A prediction is never a silently missing card."""
    mod, _ = hub
    lagging(mod, [], now() - 10)
    predict(mod, 7, "Doing")
    placed = where(mod.board(), 7)
    assert [p[0] for p in placed] == ["Doing"], f"the predicted card is not drawn in its column: {placed}"
    assert placed[0][1].get("pd") is True, "the predicted card wears no pending mark"


def test_a_prediction_draws_the_card_the_cache_still_holds_in_its_old_column(hub):
    """The cache lags: the read holds Todo while the writer recorded Doing. The predicted column is the one drawn."""
    mod, _ = hub
    lagging(mod, [card(7, "Todo")], now() - 10)
    predict(mod, 7, "Doing")
    board = mod.board()
    placed = where(board, 7)
    assert [p[0] for p in placed] == ["Doing"], f"the lagging read kept the card in its old column: {placed}"
    assert placed[0][1].get("pd") is True, "the card the prediction moved wears no pending mark"
    assert {g["name"]: g["n"] for g in board["groups"]}["Todo"] == 0


def test_a_read_that_began_after_the_write_and_carries_its_column_needs_no_mark(hub):
    """The read proves the write: the cache carries the predicted column and began in a later second than the event."""
    mod, _ = hub
    read_at = int(now()) - 5
    lagging(mod, [card(7, "Doing")], read_at)
    predict(mod, 7, "Doing", instant=read_at - 5)
    placed = where(mod.board(), 7)
    assert [p[0] for p in placed] == ["Doing"] and "pd" not in placed[0][1], f"a proved write still wears a mark: {placed}"


def test_a_prediction_past_its_bound_is_a_named_defect_and_the_card_is_still_drawn(hub):
    """No read ever agreed with a write that never landed: past the bound the reader names the defect. The card stays
    drawn, never silently missing, and the defect carries its writer and its column."""
    mod, _ = hub
    lagging(mod, [], now() - 10)
    predict(mod, 7, "Doing", writer="reconcile", instant=now() - 600)
    board = mod.board()
    placed = where(board, 7)
    assert [p[0] for p in placed] == ["Doing"] and placed[0][1].get("pd") is True, \
        f"a prediction past its bound vanished from the board: {placed}"
    defect = next((d for d in board["pred"] if d["n"] == 7), None)
    assert defect is not None, f"the board names no defect for the stale prediction: {board.get('pred')}"
    assert defect["defect"] == "BOARD-PENDING-OLD", defect
    assert (defect["column"], defect["by"]) == ("Doing", "reconcile"), defect


def test_the_bound_is_a_setting(hub, monkeypatch):
    """The same 100 s old prediction is inside the 120 s default and past a 60 s setting."""
    mod, _ = hub
    lagging(mod, [], now() - 10)
    predict(mod, 7, "Doing", instant=now() - 100)
    assert [d for d in mod.board()["pred"] if d["n"] == 7] == [], "a prediction inside the default bound was a defect"
    monkeypatch.setenv("SG_PENDING_WINDOW_SECONDS", "60")
    assert [d for d in mod.board()["pred"] if d["n"] == 7], "the 60 s setting did not name the stale prediction"


def test_a_written_write_is_no_prediction(hub):
    """Only a pending event predicts: the writer's own proof ends it, and the board is the read alone."""
    mod, _ = hub
    lagging(mod, [], now() - 10)
    key = f"{REPO}#7"
    mod.issue_ledger.board(key, "janitor", "Doing", "", by="janitor")
    assert mod.issue_ledger.boardw(key, "janitor", "Doing", "janitor") == "written"
    board = mod.board()
    assert where(board, 7) == [], "a written event was drawn as a prediction"
    assert board["pred"] == [], "a written event was named as a defect"


def test_a_column_the_board_does_not_ship_is_never_drawn(hub):
    """A prediction of a column no board ships draws nothing, as a proved move of one never does."""
    mod, _ = hub
    lagging(mod, [card(7, "Todo")], now() - 10)
    predict(mod, 7, "Nowhere")
    placed = where(mod.board(), 7)
    assert [p[0] for p in placed] == ["Todo"] and "pd" not in placed[0][1], f"an unknown column was drawn: {placed}"


def test_the_hub_never_writes_the_ledger(hub):
    """One writer per loop fact (ADR-0149): rendering the prediction leaves the ledger byte for byte as it was."""
    mod, _ = hub
    lagging(mod, [], now() - 10)
    predict(mod, 7, "Doing", instant=now() - 600)
    before = (mod.issue_ledger.path(f"{REPO}#7")).read_bytes()
    mod.board()
    assert mod.issue_ledger.path(f"{REPO}#7").read_bytes() == before, "the hub wrote the ledger"
    assert mod.issue_ledger.pending(f"{REPO}#7"), "the hub dropped the writer's pending event"


def test_the_overlay_setting_turns_the_prediction_off(hub, monkeypatch):
    """One setting turns every overlay off: the served board is then the list read alone."""
    mod, _ = hub
    monkeypatch.setenv("SG_PENDING_OVERLAY", "off")
    lagging(mod, [], now() - 10)
    predict(mod, 7, "Doing")
    board = mod.board()
    assert where(board, 7) == [], "the overlay drew a predicted card though it is off"
    assert board["pred"] == [], "the overlay named a defect though it is off"
