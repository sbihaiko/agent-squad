"""The card limit of each board column (#2287): `columns.<column>.max_cards` in the project settings.

The project settings file accepts one limit per board column, a whole number from 1 to 100 or `none`. The plugin
defaults follow the present machine limits, so the first release changes no behaviour. The module answers a card limit
only (#2430): a machine cap counts run slots, and the pass that bounds slots reads it in its own unit.
"""
import pytest
import yaml

from squad import board_limits as B
from squad import defaults as D
from squad import project_settings as P

TABLE = {"Backlog": None, "Todo": None, "Doing": 6, "Reviewing": 4, "Batching": None, "Testing": None}


def _project(tmp_path, text):
    (tmp_path / "docs" / "squad").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs" / "squad" / "defaults.yaml").write_text(text)
    return tmp_path


# --- the parse

def test_the_settings_accept_one_limit_per_column():
    assert P.defects({"columns": {n: {"max_cards": 5} for n in TABLE}}) == []


def test_the_settings_accept_none_as_a_limit():
    assert P.defects({"columns": {"Todo": {"max_cards": None}, "Doing": {"max_cards": None}}}) == []


@pytest.mark.parametrize("bad", [0, 101, -1, True, 2.5, "eight", [3]])
def test_a_limit_outside_one_to_a_hundred_is_refused(bad):
    assert P.defects({"columns": {"Doing": {"max_cards": bad}}}), f"max_cards {bad!r} was accepted"


def test_a_column_the_board_does_not_carry_is_refused():
    bad = P.defects({"columns": {"Someday": {"max_cards": 3}}})
    assert bad and bad[0][0] == "columns.Someday.max_cards"


def test_the_text_none_writes_a_column_with_no_limit(tmp_path):
    P.set_key(tmp_path, "columns.Doing.max_cards", "none")
    data = yaml.safe_load((tmp_path / "docs" / "squad" / "defaults.yaml").read_text())
    assert data["columns"]["Doing"]["max_cards"] is None


def test_the_text_of_a_number_writes_an_integer(tmp_path):
    P.set_key(tmp_path, "columns.Doing.max_cards", "9")
    data = yaml.safe_load((tmp_path / "docs" / "squad" / "defaults.yaml").read_text())
    assert data["columns"]["Doing"]["max_cards"] == 9


# --- the default values

def test_the_plugin_defaults_hold_the_table():
    # The plugin file names every column of the table, so a column it left out fails here instead of answering the
    # module's default unnoticed.
    cfg = D.load()["columns"]
    assert set(cfg) == set(TABLE)
    assert {n: cfg[n]["max_cards"] for n in TABLE} == TABLE


def test_the_plugin_defaults_pass_their_own_checker():
    assert D.check(D.load()) == []


def test_the_resolved_limits_are_the_table_with_no_project_file(tmp_path):
    assert B.limits(tmp_path) == TABLE


# --- what a limit stops (#2432)

def test_each_active_column_answers_the_pass_that_stops_at_its_limit():
    assert "autofrontier" in B.column_stop("Doing")
    assert "autofrontier" in B.column_stop("Batching")
    assert "branch review pass" in B.column_stop("Reviewing")


@pytest.mark.parametrize("name", ["Backlog", "Todo", "Testing"])
def test_a_column_the_limit_stops_no_pass_yet_says_so(name):
    assert B.column_stop(name) == "no pass reads it yet"


def test_a_column_the_store_does_not_carry_answers_the_no_reader_line():
    assert B.column_stop("Closed") == "no pass reads it yet"


# --- the project overlay

def test_a_project_may_change_a_column_limit(tmp_path):
    _project(tmp_path, "columns:\n  Doing:\n    max_cards: 2\n  Todo:\n    max_cards: 20\n")
    got = B.limits(tmp_path)
    assert got == {**TABLE, "Doing": 2, "Todo": 20}


def test_a_column_with_no_limit_answers_none(tmp_path):
    assert B.limit("Todo", tmp_path) is None
    assert B.limit("Doing", tmp_path) == 6


def test_a_project_null_keeps_an_explicit_none_over_a_default_number(tmp_path):
    # A present `null` is a limit of none, not an absent key: it overrides the plugin default of 6 and keeps that column
    # unbounded, so a required gate never disappears through a null.
    _project(tmp_path, "columns:\n  Doing:\n    max_cards: null\n")
    assert B.limits(tmp_path)["Doing"] is None


def test_the_command_answers_a_card_limit_and_takes_no_machine_cap(tmp_path, capsys):
    # #2430: the column-limit seam answers a card limit only. A run slot is another unit, read by the pass that bounds
    # run slots, so this command holds no cap and refuses one.
    B.main(["Doing", "--target", str(tmp_path)])
    assert capsys.readouterr().out.strip() == "6"
    with pytest.raises(SystemExit):
        B.main(["Doing", "--target", str(tmp_path), "--machine-cap", "2"])

