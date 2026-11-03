"""A chain entry's hours window can say when a provider is off-peak, in its own days and in UTC.

DeepSeek is half price off-peak. Its peak is 01:00-04:00 and 06:00-10:00 UTC, Monday to Friday; every other hour, and
all of Saturday and Sunday (UTC), is off-peak. A window is one or more clauses joined by ";". A clause is an optional
day set, one or more "HH-HH" ranges joined by ",", and an optional "UTC" suffix. The window is open when one clause
holds the moment. A plain "HH-HH" window reads as before, in local hours, and a window that does not parse closes
nothing. One function owns the grammar (`modelreg.in_window`); every reader reads a moment, never a bare hour.

No test reads the machine clock or the machine time zone: each moment is a fixed UTC instant and TZ is set.
"""
import calendar
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from squad import cli, modelreg, session
from squad.options import OptionError, resolve

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
DEEPSEEK = "mon-fri 00-01,04-06,10-24 UTC; sat-sun 00-24 UTC"


def utc(y, mo, d, h, mi=0):
    """The timestamp of one UTC wall time."""
    return calendar.timegm((y, mo, d, h, mi, 0, 0, 0, 0))


FRI_2330 = utc(2026, 10, 30, 23, 30)    # Friday, off-peak to the end of the day
SAT_0200 = utc(2026, 10, 31, 2)         # Saturday: off-peak all day, even inside the weekday peak hours
SUN_2300 = utc(2026, 11, 1, 23)         # Sunday, last hour of the weekend
MON_0030 = utc(2026, 11, 2, 0, 30)      # Monday, off-peak before the first peak
MON_0130 = utc(2026, 11, 2, 1, 30)      # Monday, inside 01-04 peak
TUE_0500 = utc(2026, 11, 3, 5)          # Tuesday, between the two peaks
TUE_0800 = utc(2026, 11, 3, 8)          # Tuesday, inside 06-10 peak
TUE_1000 = utc(2026, 11, 3, 10)         # Tuesday, the peak just ended


@pytest.fixture
def tz(monkeypatch):
    """Set the process time zone for one test, and restore it after."""
    def set_(name):
        monkeypatch.setenv("TZ", name)
        time.tzset()
    yield set_
    monkeypatch.undo()
    time.tzset()


@pytest.mark.parametrize("zone", ["UTC", "Asia/Tokyo", "America/Sao_Paulo"])
@pytest.mark.parametrize("at, open_", [
    (FRI_2330, True), (SAT_0200, True), (SUN_2300, True), (MON_0030, True),
    (MON_0130, False), (TUE_0500, True), (TUE_0800, False), (TUE_1000, True),
])
def test_the_deepseek_window_holds_its_off_peak_in_utc_whatever_the_local_zone(tz, zone, at, open_):
    tz(zone)
    assert modelreg.in_window(DEEPSEEK, at) is open_
    assert modelreg.in_window(DEEPSEEK, time.localtime(at)) is open_    # a struct_time is the same moment
    assert modelreg.in_window(DEEPSEEK, time.gmtime(at)) is open_


@pytest.mark.parametrize("window, hour, open_", [
    ("07-19", 7, True), ("07-19", 18, True), ("07-19", 19, False), ("07-19", 6, False),
    ("22-07", 22, True), ("22-07", 3, True), ("22-07", 7, False), ("22-07", 12, False),
    ("19-07", 0, True), ("19-07", 12, False), ("07-07", 7, False), ("07-07", 12, False),
])
def test_a_plain_window_reads_local_hours_exactly_as_before(tz, window, hour, open_):
    tz("Asia/Tokyo")
    at = time.mktime((2026, 11, 3, hour, 15, 0, 0, 0, -1))     # local Tuesday
    assert modelreg.in_window(window, at) is open_
    assert session.in_window(window, at) is open_


@pytest.mark.parametrize("window", ["", "junk", "7-19", "mon 07-19 PST", "xyz 01-02", "01-25", "25-02", "mon-fri", ";", None])
def test_a_window_that_does_not_parse_closes_nothing(tz, window):
    tz("UTC")
    for at in (MON_0130, TUE_0800, SAT_0200):
        assert modelreg.in_window(window, at) is True
    assert modelreg.window_valid(window) is False


def test_a_day_set_without_utc_reads_the_local_day(tz):
    tz("Asia/Tokyo")                                       # Fri 23:30 UTC is Sat 08:30 in Tokyo
    assert modelreg.in_window("sat-sun 00-24", FRI_2330) is True
    assert modelreg.in_window("sat-sun 00-24 UTC", FRI_2330) is False
    assert modelreg.in_window("sat,sun 08-09", FRI_2330) is True
    assert modelreg.in_window("fri-mon 08-09", FRI_2330) is True     # a day range may wrap the week
    assert modelreg.in_window("mon-fri 08-09", FRI_2330) is False


def test_24_ends_a_range_at_midnight(tz):
    tz("UTC")
    assert modelreg.in_window("10-24 UTC", FRI_2330) is True
    assert modelreg.in_window("00-24 UTC", MON_0130) is True
    assert modelreg.window_valid("10-24") is True


@pytest.mark.parametrize("window", ["07-19", "22-07", DEEPSEEK, "sat,sun 00-24", "mon-wed,fri 08-12, 14-18 UTC",
                                    "Mon-Fri 00-01 utc;sat-sun 00-24 utc"])
def test_the_grammar_accepts_its_forms(window):
    assert modelreg.window_valid(window) is True


def test_an_empty_range_is_not_a_window_to_save():
    assert modelreg.window_valid("07-07") is False


def test_session_reads_the_one_grammar():
    assert session.in_window is modelreg.in_window


def test_the_skill_copy_of_the_resolver_is_the_same_file():
    assert (SCRIPTS / "modelreg.py").read_text() == (ROOT / "squad/modelreg.py").read_text()


# --- the readers pass a moment --------------------------------------------------------------------------------

def test_model_chain_skips_a_model_outside_its_window_at_a_moment(tz):
    tz("America/Sao_Paulo")
    hours = {"deepseek": DEEPSEEK}
    assert session.model_chain(["opus", "deepseek"], "opus", hours=hours, at=TUE_0800) == ["opus"]
    assert session.model_chain(["opus", "deepseek"], "opus", hours=hours, at=SUN_2300) == ["opus", "deepseek"]
    assert session.hours_skipped(["opus", "deepseek"], "opus", hours=hours, at=TUE_0800) == \
        [("deepseek", f"outside_hours:{DEEPSEEK}")]
    assert session.hours_closed(hours, MON_0130) == hours and session.hours_closed(hours, FRI_2330) == {}
    assert session.hours_closed(hours, None) == {}


def _entry(tmp_path, hrs):
    doc = {"schema_version": 2, "chains": {"coding": [{"id": "claude-sonnet-5-5", "route": "gateway", "hrs": hrs}]}}
    (tmp_path / "models.json").write_text(json.dumps(doc))
    return modelreg.chain(tmp_path / "models.json", "coding").entries[0]


def test_eligible_reads_the_window_at_its_moment(tz, tmp_path):
    tz("Asia/Tokyo")
    entry = _entry(tmp_path, DEEPSEEK)
    assert modelreg.eligible(entry, shared=tmp_path, now=FRI_2330).ok is True
    ev = modelreg.eligible(entry, shared=tmp_path, now=MON_0130)
    assert ev.ok is False and ev.reason == f"outside its window {DEEPSEEK}"


def test_eligible_hour_still_overrides_the_local_hour_of_its_moment(tz, tmp_path):
    tz("UTC")
    entry = _entry(tmp_path, "19-07")
    assert modelreg.eligible(entry, shared=tmp_path, now=TUE_1000, hour=12).ok is False
    assert modelreg.eligible(entry, shared=tmp_path, now=TUE_1000, hour=20).ok is True


def test_the_tsv_names_whether_each_window_is_open_at_its_now(tmp_path):
    doc = {"schema_version": 2, "chains": {"coding": [
        {"id": "claude-sonnet-5-5", "route": "gateway", "hrs": DEEPSEEK},
        {"id": "claude-opus-5-5", "route": "gateway"}]}}
    (tmp_path / "models.json").write_text(json.dumps(doc))
    env = {**os.environ, "TZ": "Asia/Tokyo"}

    def col8(now):
        out = subprocess.run([sys.executable, str(SCRIPTS / "modelreg.py"), "chain", "coding", "--models",
                              str(tmp_path / "models.json"), "--tsv", "--now", str(now)],
                             capture_output=True, text=True, env=env, check=True).stdout
        return [(r.split("\t")[1], r.split("\t")[4], r.split("\t")[7]) for r in out.splitlines()]
    assert col8(TUE_0800) == [("claude-sonnet-5-5", DEEPSEEK, "0"), ("claude-opus-5-5", "", "1")]
    assert col8(SUN_2300) == [("claude-sonnet-5-5", DEEPSEEK, "1"), ("claude-opus-5-5", "", "1")]


GRAPH = type("G", (), {"run": {"spend_cap": None, "deadline": None, "max_parallel": None}, "nodes": {}, "doc": {}, "hash": "h"})()


def test_the_engine_option_takes_a_window_of_the_grammar():
    assert resolve(GRAPH, {"model_hours": {"deepseek": DEEPSEEK}})[0]["model_hours"] == {"deepseek": DEEPSEEK}
    with pytest.raises(OptionError, match="window"):
        resolve(GRAPH, {"model_hours": {"deepseek": 7}})


def test_the_cli_keeps_the_commas_of_a_window():
    assert cli.model_hours_arg(f"deepseek={DEEPSEEK},opus=07-19") == {"deepseek": DEEPSEEK, "opus": "07-19"}
    assert cli.model_hours_arg("a=07-19,b=22-07") == {"a": "07-19", "b": "22-07"}
    assert cli.model_hours_arg("") == {}


# --- the hub ------------------------------------------------------------------------------------------------------

@pytest.fixture
def hub(monkeypatch, tmp_path):
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("HOME", str(tmp_path)),
                 ("SG_CHECKOUT", str(tmp_path)), ("SG_HUB_DIR", str(tmp_path))):
        monkeypatch.setenv(k, v)
    for k in ("SG_STATE", "SG_STATE_BASE", "SG_SHARED", "SG_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    spec = importlib.util.spec_from_file_location("hub_model_window", SCRIPTS / "hub.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    tmp = tmp_path / "models.json"
    tmp.write_text((Path(__file__).resolve().parent / "fixtures/models.v2.shape.json").read_text())   # a frozen shape: the repo file follows the chains of the week
    monkeypatch.setattr(mod, "MODELS", tmp)
    return mod, tmp


def test_the_hub_saves_a_window_of_the_grammar(hub):
    mod, tmp = hub
    out = mod.models_set({"version": mod.models_get()["version"],
                          "hours": {"coding_models": {"claude-sonnet-5-5": DEEPSEEK}}})
    assert "err" not in out, out
    saved = json.loads(tmp.read_text())
    assert next(e for e in saved["chains"]["coding"] if e["id"] == "claude-sonnet-5-5")["hrs"] == DEEPSEEK
    bad = mod.models_set({"version": mod.models_get()["version"], "hours": {"coding_models": {"claude-sonnet-5-5": "junk"}}})
    assert "err" in bad


def test_the_hub_reads_a_v1_window_through_the_one_grammar(hub, tz):
    import datetime as dt
    mod, _ = hub
    tz("UTC")
    d = {"coding_models": ["deepseek-v4"], "hours": {"coding_models": {"deepseek-v4": DEEPSEEK}}}
    assert mod.model_hours(d, "coding_models", "deepseek-v4") == DEEPSEEK
    assert mod.in_hours(d, "coding_models", "deepseek-v4", dt.datetime.fromtimestamp(TUE_0800, dt.UTC)) is False
    assert mod.in_hours(d, "coding_models", "deepseek-v4", dt.datetime.fromtimestamp(SUN_2300, dt.UTC)) is True


def test_the_hub_panel_accepts_a_window_of_the_grammar():
    src = (SCRIPTS / "hub.py").read_text()
    line = next(x for x in src.splitlines() if x.startswith("const HRP="))
    assert DEEPSEEK in line                                  # the off-peak window is a preset of the hours field
    assert "HRX=" in line and "([01]\\d|2[0-3])-([01]\\d|2[0-3])$/" not in line   # the old one-range test is gone
