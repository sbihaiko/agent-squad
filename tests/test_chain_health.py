"""GitHub issue #431: when a chain is down to one model, or none, the loop and the hub say which models are out, why,
and when they come back.

One reader (`chainhealth.py`) keeps the v1 rules it had for a v1 file: the registry landed beside it and this caller
will move in its own landing with no behavior change (ADR-0147). The report names every spelling where the v1 rules
and the registry disagree about eligibility, so that move is read, not guessed. A v2 file (`chains`) has no earlier
behavior and is read through `modelreg.chain` and `modelreg.eligible`. The hub draws what the reader returns and never
computes the rule (ADR-0137); branchreviewpass.sh puts the same summary in the issue's status file.
"""
import datetime as dt
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
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


def test_a_v1_file_keeps_its_own_rule_and_reports_where_the_registry_differs(ch, tmp_path):
    """ADR-0147: this reader did not move to the resolver, so it reads no `claude-limited` marker. The report names
    the spelling the registry would block, so the difference is read and not guessed."""
    (tmp_path / "claude-limited").write_text(str(int(_reset(22, 32))))
    s = ch.chain_state(CFG, "review_models", tmp_path, now=NOON)
    assert s["out"] == []                       # the v1 rules read no Claude marker
    assert s["divergence"] == [{"model": "claude-opus-5-5", "v1": "available", "registry": "usage limit"}]
    assert "claude-opus-5-5" in s["divergence_notice"]


def test_a_single_model_chain_has_no_banner(ch, tmp_path):
    s = ch.chain_state({"review_models": ["grok-4.5"]}, "review_models", tmp_path, now=NOON)
    assert s["banner"] is None


def test_a_v1_chain_keeps_the_codex_marker_for_its_legacy_spellings(ch, tmp_path):
    """A v1 file predates the registry's subscription field: every id outside the `claude:`, `grok-` and `gemini-`
    prefixes ran through Codex, so `codex-limited` still names it out (SPEC 505 keeps v1 behavior)."""
    (tmp_path / "codex-limited").write_text(str(int(_reset(22, 32))))
    cfg = {"coding_models": ["claude-deepseek-flash[1m]", "claude-grok-4.7", "claude-sonnet-5-5", "gpt-5.6-terra"]}
    s = ch.chain_state(cfg, "coding_models", tmp_path, now=NOON)
    assert [o["model"] for o in s["out"]] == cfg["coding_models"]
    assert {o["reason"] for o in s["out"]} == {"usage limit"}


def test_a_v2_chain_lets_the_registry_subscription_decide_the_marker(ch, tmp_path):
    """A v2 chain names registry ids: `claude-deepseek-flash[1m]` is spend-only now, not Codex (SPEC 505)."""
    (tmp_path / "codex-limited").write_text(str(int(_reset(22, 32))))
    cfg = {"chains": {"coding": [{"id": "deepseek-flash", "raw": "claude-deepseek-flash[1m]"}]}}
    s = ch.chain_state(cfg, "coding_models", tmp_path, now=NOON)
    assert s["out"] == [] and s["eligible"] == 1


@pytest.mark.parametrize("key,models,marker", [
    ("coding_models", ["gpt-5.6-terra"], "codex-limited"),
    ("review_models", ["gemini-3.8-flash-high"], "agy-limited"),
    ("vision_models", ["gpt-5.6-terra"], "codex-limited"),
])
def test_every_v1_chain_reads_the_two_v1_markers(ch, tmp_path, key, models, marker):
    (tmp_path / marker).write_text(str(int(_reset(22, 32))))
    s = ch.chain_state({key: models}, key, tmp_path, now=NOON)
    assert [o["model"] for o in s["out"]] == models
    assert [o["reason"] for o in s["out"]] == ["usage limit"]


def test_every_chain_is_read_through_the_registry(ch, tmp_path):
    cfg = {"coding_models": ["claude-sonnet-5-5"], "review_models": ["grok-4.7"],
           "vision_models": ["gpt-5.6-terra"], "router_models": ["grok-4.5"]}
    s = ch.chains_state(cfg, tmp_path, now=NOON)
    assert set(s) == {"coding", "review", "vision", "router"}
    assert (s["coding"]["total"], s["review"]["total"], s["vision"]["total"], s["router"]["total"]) == (1, 1, 1, 1)


def test_an_empty_v2_vision_chain_is_reported(ch, tmp_path):
    s = ch.chains_state(CFG | {"chains": {"vision": []}}, tmp_path, now=NOON)
    assert s["vision"]["total"] == 0
    assert s["vision"]["banner"] == "vision chain: no model configured"


def test_chainhealth_carries_the_seam_header():
    text = (SCRIPTS / "chainhealth.py").read_text()
    assert any("modelreg: seam-clean" in line for line in text.splitlines()[:5])


def test_the_cli_reports_a_named_chain(tmp_path):
    models = tmp_path / "models.json"
    models.write_text(json.dumps({"chains": {"vision": []}}))
    p = subprocess.run([sys.executable, str(SCRIPTS / "chainhealth.py"), str(models), str(tmp_path),
                        "--chain", "vision"], capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == "vision chain: no model configured"


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


@pytest.mark.heavy   # the reader panel plus its POST round trip: 1.9 s here, 2 s on a CI runner
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
    first = hub.models_set(doc)
    assert "error" not in first and "err" not in first, first
    # a page reads the version again before its next Save (#688); posting the fresh reading back changes nothing
    again = hub.models_set(hub.models_get())
    assert "error" not in again and "err" not in again, again


def time_in_future():
    import time
    return time.time() + 3600


def test_the_page_draws_the_reader_field_and_computes_no_rule(hub):
    page = (SCRIPTS / "hub.py").read_text().split("function mdlRender")[1].split("async function mdlLoad")[0]
    assert "mdl.chain" in page and "ch.banner" in page and "ch.out" in page


def test_a_timeout_omission_shows_on_its_entry_and_the_configuration_is_untouched(ch, tmp_path):
    """ADR-0161: the panel and the loop read one source of state. A streak that took a model out of the review chains
    lived only in review-timeouts.json, which this reader never opened, so the entry sat in its rank with no tag."""
    (tmp_path / "review-timeouts.json").write_text(json.dumps(
        {"claude:claude-opus-5-5": {"streak": 3, "until": int(_reset(22, 24))}}))
    before = list(CFG["review_models"])
    s = ch.chain_state(CFG, "review_models", tmp_path, now=NOON)
    assert {"model": "claude-opus-5-5", "reason": "3 consecutive timeouts",
            "until": int(_reset(22, 24)), "until_text": "22:24"} in s["out"]
    assert s["eligible"] == 2 and CFG["review_models"] == before


def test_a_timeout_streak_that_has_ended_is_not_a_reason(ch, tmp_path):
    (tmp_path / "review-timeouts.json").write_text(json.dumps(
        {"claude:claude-opus-5-5": {"streak": 3, "until": int(NOON) - 60}}))
    assert ch.chain_state(CFG, "review_models", tmp_path, now=NOON)["out"] == []


# #1417: the grok-limited marker holds its write epoch; it is fresh for SG_GROK_LIMIT_TTL (default 6 h), then expired.
GROK_V1 = {"review_models": ["grok-4.7", "claude:claude-opus-5-5"], "anthropic_models": []}
GROK_V2 = {"chains": {"review": [{"id": "grok-4.7", "raw": "grok-4.7"}, {"id": "claude-opus-5-5", "raw": "claude:claude-opus-5-5"}]},
           "anthropic_models": []}


def _grok(ch, doc, shared, age, hold=False, monkeypatch=None, ttl=None):
    import fcntl
    (shared / "grok-limited").write_text(str(int(NOON - age)))
    if ttl and monkeypatch:
        monkeypatch.setenv("SG_GROK_LIMIT_TTL", str(ttl))
    held = None
    if hold:
        held = open(shared / "grok-probe.lock", "a")
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        return ch.chain_state(doc, "review_models", shared, now=NOON)
    finally:
        if held:
            held.close()


@pytest.mark.parametrize("doc", [GROK_V1, GROK_V2], ids=["v1", "v2"])
def test_a_fresh_grok_marker_is_limited_with_age_and_next_probe(ch, tmp_path, doc):
    s = _grok(ch, doc, tmp_path, 3600)
    assert [o["model"] for o in s["out"]] == ["grok-4.7"]
    assert "limited" in s["out"][0]["reason"] and "next probe" in s["out"][0]["reason"]
    assert s["grok"]["grok-4.7"]["state"] == "limited"
    assert s["grok"]["grok-4.7"]["next_probe"] == int(NOON - 3600) + 21600


@pytest.mark.parametrize("doc", [GROK_V1, GROK_V2], ids=["v1", "v2"])
def test_an_expired_grok_marker_is_eligible(ch, tmp_path, doc):
    s = _grok(ch, doc, tmp_path, 21600 + 60)
    assert s["out"] == [] and s["eligible"] == 2
    assert s["grok"]["grok-4.7"]["state"] == "eligible"


@pytest.mark.parametrize("doc", [GROK_V1, GROK_V2], ids=["v1", "v2"])
def test_an_expired_marker_with_the_lock_held_reads_probing(ch, tmp_path, doc):
    s = _grok(ch, doc, tmp_path, 21600 + 60, hold=True)
    assert s["grok"]["grok-4.7"]["state"] == "probing"
    assert [o["model"] for o in s["out"]] == ["grok-4.7"] and "probing" in s["out"][0]["reason"]


@pytest.mark.parametrize("doc", [GROK_V1, GROK_V2], ids=["v1", "v2"])
def test_the_ttl_comes_from_the_environment(ch, tmp_path, monkeypatch, doc):
    s = _grok(ch, doc, tmp_path, 3600, monkeypatch=monkeypatch, ttl=600)
    assert s["grok"]["grok-4.7"]["state"] == "eligible"


def test_the_review_and_the_hub_share_one_rule(tmp_path):
    """review.sh asks grokmark.py; the reader reads the same module, so the two never disagree."""
    out = subprocess.run([sys.executable, str(SCRIPTS / "grokmark.py"), "state", str(tmp_path), str(int(NOON))],
                         capture_output=True, text=True)
    assert out.returncode == 0 and out.stdout.split()[0] == "none"
    (tmp_path / "grok-limited").write_text(str(int(NOON - 100)))
    out = subprocess.run([sys.executable, str(SCRIPTS / "grokmark.py"), "state", str(tmp_path), str(int(NOON))],
                         capture_output=True, text=True)
    assert out.stdout.split() == ["fresh", str(int(NOON - 100) + 21600)]


@pytest.mark.parametrize("age,hold", [(None, False), (3600, False), (21600 + 60, False), (21600 + 60, True)],
                         ids=["none", "fresh", "expired", "probing"])
def test_review_sh_skip_decision_and_chain_state_agree_on_one_marker(ch, tmp_path, age, hold):
    """review.sh's eligible() grok line and chain_state read one marker; both skip it exactly when it is limited or probing."""
    import fcntl
    line = next(row for row in (SCRIPTS / "review.sh").read_text().splitlines() if row.lstrip().startswith("case $rn in grok)"))
    now = int(time.time())
    if age is not None:
        (tmp_path / "grok-limited").write_text(str(now - age))
    held = None
    if hold:
        held = open(tmp_path / "grok-probe.lock", "a")
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        script = (f'gm() {{ python3 "{SCRIPTS}/grokmark.py" "$@"; }}; SG_SHARED="{tmp_path}"; rn=grok\n'
                  f'f() {{\n{line}\nreturn 0; }}\nf')
        skipped = subprocess.run(["bash", "-c", script], capture_output=True, text=True).returncode == 1
        state = ch.chain_state({"review_models": ["grok-4.7"], "anthropic_models": []}, "review_models", tmp_path,
                               now=float(now))
    finally:
        if held:
            held.close()
    assert skipped == (state["out"] != [])
    assert skipped == (age is not None and (age < 21600 or hold))
