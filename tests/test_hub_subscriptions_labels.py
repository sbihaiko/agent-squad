"""Hub subscriptions panel: a stale reading is no reading, and every label comes from the account."""
import importlib.util
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
HUB_PY = SCRIPTS / "hub.py"
HUB_SRC = HUB_PY.read_text()

QUOTA_OUT = ("Gemini Models\tWeekly Limit Remaining\t0%\t2026-10-24T01:59:34Z\n"
             "Claude and GPT models\tWeekly Limit Remaining\t100%\t2026-10-24T23:56:09Z\n")


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_subscriptions_labels", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def cache(hub, monkeypatch):
    """An empty quota cache, and no recorded runs or plans."""
    monkeypatch.setattr(hub, "quota", {})
    monkeypatch.setattr(hub, "all_runs", lambda: [])
    monkeypatch.setattr(hub, "plans_get", lambda: {k: None for k in hub.SUBS})
    monkeypatch.setattr(hub, "rate", lambda cur: 1.0)
    return hub.quota


def _reading(asof, pct=22.0, plan=None):
    return {"windows": [{"label": "weekly", "pct": pct, "resets": time.time() + 86400}], "asof": asof, "plan": plan}


def test_a_reading_16_hours_old_is_no_reading_with_its_reason_and_time(hub, cache):
    last = time.time() - 16 * 3600
    cache["grok"] = _reading(last)
    row = hub.quota_row("grok")
    assert row["unavailable"] is True and not row.get("windows")
    assert row["asof"] == last                                   # the time of the last reading travels with it
    assert "no reading" in row["err"] and "16 h old" in row["err"]
    assert time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(last)) in row["err"]


def test_a_fresh_reading_passes_through(hub, cache):
    cache["grok"] = _reading(time.time() - 600)
    assert hub.quota_row("grok")["windows"][0]["pct"] == 22.0


def test_the_age_limit_covers_every_provider(hub, cache):
    cache["claude"] = _reading(time.time() - 30 * 3600)
    assert hub.quota_row("claude")["unavailable"] is True


def test_the_card_name_is_the_detected_plan(hub, cache):
    cache["gpt"] = _reading(time.time(), plan="pro")
    assert hub.subs_month()["subs"]["gpt"]["name"] == "ChatGPT Pro"


def test_the_card_name_falls_back_to_the_chosen_plan_not_a_fixed_name(hub, cache, monkeypatch):
    monkeypatch.setattr(hub, "plans_get", lambda: {**{k: None for k in hub.SUBS},
                                                   "claude": {"plan": "max20", "amount": 200, "currency": "USD"}})
    assert hub.subs_month()["subs"]["claude"]["name"] == "Claude Max 20x"


def test_the_card_name_with_no_plan_known_is_the_provider(hub, cache):
    assert hub.subs_month()["subs"]["claude"]["name"] == "Claude"


def test_the_page_takes_the_name_from_the_row_and_titles_the_bars_for_what_they_show():
    assert "SUBL[k]" not in HUB_SRC and "v.name" in HUB_SRC
    assert "Per subscription: API-equivalent usage" not in HUB_SRC
    assert "Per subscription: quota used, as each provider's CLI reports it" in HUB_SRC


def test_q_agy_reads_the_gemini_line_of_quota(hub, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, QUOTA_OUT, ""))
    w = hub.q_agy()["windows"][0]
    assert w["pct"] == 100.0 and w["resets"] == 1792807174.0


def test_agyquota_gives_the_gemini_reset_when_the_weekly_limit_is_spent():
    sys.path.insert(0, str(SCRIPTS))
    import agyquota
    assert agyquota.spent_reset(QUOTA_OUT, now=1792000000) == 1792807174
    assert agyquota.spent_reset(QUOTA_OUT.replace("\t0%", "\t40%"), now=1792000000) is None
    assert agyquota.spent_reset(QUOTA_OUT, now=1792900000) is None     # a reset already past is no reset
    assert agyquota.spent_reset("agy: not logged in\n", now=1792000000) is None
