"""GitHub issue #431: when the review chain is down to one model, or none, the loop and the hub say which models are out,
why, and when they come back.

One reader (`chainhealth.py`) reads the markers review.sh already skips a model over: `codex-limited` and `agy-limited`
under $SG_SHARED (an epoch), `quota-guard`, and models.json's `off` and `hours`. The hub draws what the reader returns and
never computes the rule (ADR-0137); branchreviewpass.sh puts the same summary in the issue's status file.
"""
import datetime as dt
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
NOON = dt.datetime(2026, 10, 15, 12, 0).timestamp()


def _load():
    spec = importlib.util.spec_from_file_location("chainhealth", SCRIPTS / "chainhealth.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def ch():
    return _load()


CFG = {
    "review_models": ["gpt-5.6-terra", "claude:claude-opus-5-5", "gemini-3.8-flash-high"],
    "anthropic_models": [],
}


def _reset(hh, mm):
    return dt.datetime(2026, 10, 15, hh, mm).timestamp()


def test_a_healthy_chain_has_no_banner_and_no_notice(ch, tmp_path):
    s = ch.chain_state(CFG, "review_models", tmp_path, now=NOON)
    assert (s["eligible"], s["total"]) == (3, 3)
    assert s["banner"] is None and s["notice"] is None and s["out"] == []


def test_a_usage_limit_is_out_with_its_reset_time(ch, tmp_path):
    (tmp_path / "codex-limited").write_text(str(int(_reset(22, 32))))
    s = ch.chain_state(CFG, "review_models", tmp_path, now=NOON)
    assert s["eligible"] == 2
    assert s["out"] == [{"model": "gpt-5.6-terra", "reason": "usage limit", "until": int(_reset(22, 32)), "until_text": "22:32"}]
    assert s["banner"] is None                      # two left: a notice, not yet the banner
    assert "gpt-5.6-terra: usage limit, resets 22:32" in s["notice"]


def test_a_passed_marker_is_no_limit(ch, tmp_path):
    (tmp_path / "codex-limited").write_text(str(int(NOON) - 60))
    assert ch.chain_state(CFG, "review_models", tmp_path, now=NOON)["eligible"] == 3


def test_one_model_left_gives_the_banner_naming_who_is_out(ch, tmp_path):
    (tmp_path / "codex-limited").write_text(str(int(_reset(22, 32))))
    (tmp_path / "agy-limited").write_text(str(int(_reset(15, 5))))
    s = ch.chain_state(CFG, "review_models", tmp_path, now=NOON)
    assert s["eligible"] == 1
    assert s["banner"].startswith("review chain: only claude-opus-5-5 of 3 models available")
    assert "gpt-5.6-terra: usage limit, resets 22:32" in s["banner"]
    assert "gemini-3.8-flash-high: usage limit, resets 15:05" in s["banner"]


def test_off_and_hours_are_reasons_too(ch, tmp_path):
    cfg = CFG | {"off": {"review_models": {"gpt-5.6-terra": True}},
                 "hours": {"review_models": {"claude:claude-opus-5-5": "19-07"}}}
    s = ch.chain_state(cfg, "review_models", tmp_path, now=NOON, hour=12)
    assert {o["model"]: o["reason"] for o in s["out"]} == {"gpt-5.6-terra": "switched off",
                                                           "claude-opus-5-5": "outside its window 19-07"}
    assert s["banner"].startswith("review chain: only gemini-3.8-flash-high of 3")


def test_no_model_left_says_so(ch, tmp_path):
    cfg = CFG | {"off": {"review_models": {m: True for m in CFG["review_models"]}}}
    s = ch.chain_state(cfg, "review_models", tmp_path, now=NOON, hour=12)
    assert s["eligible"] == 0 and s["banner"].startswith("review chain: no model available of 3")


def test_the_quota_guard_takes_an_anthropic_model_out(ch, tmp_path):
    (tmp_path / "quota-guard").write_text("")
    s = ch.chain_state(CFG | {"anthropic_models": ["claude:claude-opus-5-5"]}, "review_models", tmp_path, now=NOON)
    assert [o["reason"] for o in s["out"]] == ["weekly quota guard"]


def test_a_single_model_chain_has_no_banner(ch, tmp_path):
    s = ch.chain_state({"review_models": ["grok-4.5"]}, "review_models", tmp_path, now=NOON)
    assert s["banner"] is None


def test_the_cli_prints_the_summary_a_status_file_carries(tmp_path):
    (tmp_path / "codex-limited").write_text(str(int(_reset(22, 32))))
    (tmp_path / "agy-limited").write_text(str(int(_reset(15, 5))))
    models = tmp_path / "models.json"
    models.write_text(json.dumps(CFG))
    p = subprocess.run([sys.executable, str(SCRIPTS / "chainhealth.py"), str(models), str(tmp_path), str(int(NOON))],
                       capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    assert p.stdout.startswith("review chain: only claude-opus-5-5 of 3 models available")


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_chain_health", SCRIPTS / "hub.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_models_panel_gets_the_reader_result_and_posts_it_back_harmlessly(hub, tmp_path, monkeypatch):
    path = tmp_path / "models.json"
    path.write_text(json.dumps(CFG | {"coding_models": ["claude-sonnet-5-5"], "vision_models": ["grok-4.5"]}))
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "codex-limited").write_text(str(int(time_in_future())))
    monkeypatch.setattr(hub, "MODELS", path)
    monkeypatch.setattr(hub, "SHARED", shared)
    doc = hub.models_get()
    assert doc["chain"]["out"][0]["model"] == "gpt-5.6-terra" and doc["chain"]["out"][0]["reason"] == "usage limit"
    assert "chain" in hub.READONLY
    assert "error" not in hub.models_set(doc) and "err" not in hub.models_set(doc)


def time_in_future():
    import time
    return time.time() + 3600


def test_the_page_draws_the_reader_field_and_computes_no_rule(hub):
    page = (SCRIPTS / "hub.py").read_text().split("function mdlRender")[1].split("async function mdlLoad")[0]
    assert "mdl.chain" in page and "chain.banner" in page and "chain.out" in page
