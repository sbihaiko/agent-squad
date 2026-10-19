"""SPEC 505 ticket 1: one model registry, one resolver, and the guards around them.

The registry is data (`config/models.registry.json`); the resolver is `squad/modelreg.py`, stdlib only. This ticket
adds no caller: every row below pins the wire string the repository or the live configs send today, so a later
caller can move to the resolver without changing what the provider receives.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

import squad.modelreg as mr

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "config" / "models.registry.json"
SHIPPED_REGISTRY = ROOT / "contrib" / "squad-goal" / "scripts" / "registry.json"
MODELS = ROOT / "contrib" / "squad-goal" / "scripts" / "models.json"


@pytest.fixture(scope="module")
def reg():
    return mr.load(REGISTRY)


def _entry(reg, mid):
    return reg.entries[mid]


# --- the registry holds the accepted shape ---------------------------------------------------------------

def test_the_registry_loads_with_no_dropped_entry(reg):
    assert reg.dropped == [], reg.dropped
    assert {"claude", "gpt", "grok", "gemini", "deepseek"} <= set(reg.subscriptions)
    assert len(reg.entries) >= 20


def test_every_entry_has_the_accepted_fields(reg):
    for e in reg.entries.values():
        assert e.id and e.label and e.provider and e.subscription, e.id
        assert set(e.routes) <= {"cli", "gateway", "direct"}, e.id
        for name, route in e.routes.items():
            assert route["auth"] in ("subscription", "api_key", "unverified"), (e.id, name)
            if name in ("gateway", "direct"):
                assert isinstance(route["model"], str) and route["model"], (e.id, name)
                assert all(isinstance(m, str) and m for m in route.get("served", [])), (e.id, name)
            if name == "cli":
                assert route["argv"] and all(isinstance(a, str) for a in route["argv"]), e.id


def test_gemini_efforts_are_low_medium_high(reg):
    assert list(_entry(reg, "gemini-3.8-flash").efforts) == ["low", "medium", "high"]


def test_deepseek_v4_flash_stays_its_own_tools_id(reg):
    no_tools, tools = _entry(reg, "deepseek-flash"), _entry(reg, "deepseek-v4-flash")
    assert no_tools.id != tools.id
    assert no_tools.capabilities["tools"] is False
    assert tools.capabilities["tools"] is True


def test_haiku_has_an_id_and_the_three_short_names_are_aliases(reg):
    haiku = reg.aliases["haiku"]
    assert haiku in reg.entries
    assert reg.aliases["opus"] == "claude-opus-5-5"
    assert reg.aliases["sonnet"] == "claude-sonnet-5-5"


def test_retired_history_ids_have_an_entry_a_label_and_a_price(reg):
    for mid in ("grok-4.5", "grok-4.6", "gpt-6-astra", "gpt-5.6-sol-fast", "gpt-5.5", "gpt-6.1-sol"):
        e = _entry(reg, mid)
        assert e.retired is True, mid
        assert e.label and e.display_price, mid


def test_fast_gateway_ids_are_gateway_only(reg):
    for mid in ("gpt-5.6-terra-fast", "gpt-5.6-luna-fast"):
        e = _entry(reg, mid)
        assert e.gateway_only is True, mid
        assert set(e.routes) == {"gateway"}, mid


# --- load: fault isolation and named failures ------------------------------------------------------------

def _write_registry(path, models, subscriptions=None):
    doc = {"schema_version": 1,
           "subscriptions": subscriptions or {
               "gpt": {"label": "ChatGPT Plus", "marker": "codex-limited", "quota_probe": "gpt"},
               "deepseek": {"label": "DeepSeek API key", "spend_only": True},
           },
           "models": models}
    path.write_text(json.dumps(doc))
    return path


def _good(mid, provider, subscription, routes):
    return {"id": mid, "label": mid, "provider": provider, "subscription": subscription,
            "efforts": ["low", "medium", "high"], "routes": routes, "capabilities": {},
            "display_price": {"input": 1, "output": 2}, "aliases": [], "retired": False,
            "gateway_only": False}


def test_load_drops_an_api_key_route_on_a_non_deepseek_provider(tmp_path):
    bad = _good("bad-gpt", "gpt", "gpt", {"gateway": {"model": "bad-gpt", "served": ["bad-gpt"], "auth": "api_key"}})
    good = _good("good-ds", "deepseek", "deepseek", {"gateway": {"model": "good-ds", "served": ["good-ds"], "auth": "api_key"}})
    out = mr.load(_write_registry(tmp_path / "registry.json", [bad, good]))
    assert [d["id"] for d in out.dropped] == ["bad-gpt"]
    assert "api_key" in out.dropped[0]["reason"]
    assert "good-ds" in out.entries


def test_load_drops_a_bad_entry_and_names_it(tmp_path):
    bad = _good("bad-shape", "gpt", "gpt", {"gateway": {"served": ["x"], "auth": "subscription"}})
    good = _good("good", "gpt", "gpt", {"gateway": {"model": "good", "served": ["good"], "auth": "unverified"}})
    out = mr.load(_write_registry(tmp_path / "registry.json", [bad, good]))
    assert [d["id"] for d in out.dropped] == ["bad-shape"]
    assert "good" in out.entries


def test_load_names_a_json_parse_error(tmp_path):
    path = tmp_path / "registry.json"
    path.write_text("{not json")
    with pytest.raises(mr.RegistryError) as err:
        mr.load(path)
    assert "invalid JSON" in str(err.value)
    assert str(path) in str(err.value)


# --- normalize never raises, and an unknown spelling passes through --------------------------------------

@pytest.mark.parametrize("raw", ["claude:a", "claude:slow"])
def test_normalize_calls_an_unknown_spelling_unknown_and_passes_it_through(raw):
    ref = mr.normalize(raw)
    assert ref.known is False
    assert ref.raw == raw
    assert mr.invocation(ref, "gateway").wire == raw


def test_normalize_never_raises_on_a_non_string():
    for raw in (None, 7, {"x": 1}, ""):
        ref = mr.normalize(raw)
        assert ref.known is False


@pytest.mark.parametrize("raw,mid,route,effort,ctx", [
    ("gemini-3.8-flash-medium", "gemini-3.8-flash", "cli", "medium", None),
    ("deepseek-v4-flash", "deepseek-v4-flash", "gateway", None, None),
    ("claude:claude-deepseek-v4-flash[1m]", "deepseek-v4-flash", "cli", None, "1m"),
    ("opus", "claude-opus-5-5", "gateway", None, None),
])
def test_normalize_splits_prefix_context_alias_and_effort(raw, mid, route, effort, ctx):
    ref = mr.normalize(raw)
    assert ref.known is True
    assert (ref.id, ref.route, ref.effort, ref.ctx) == (mid, route, effort, ctx)


# --- entries_sha256: every spelling the graph names, resolved or not --------------------------------------

def test_entries_sha256_distinguishes_an_unknown_spelling_from_no_spelling():
    reg = mr.load(REGISTRY)
    assert mr.entries_sha256(["no-such-model"], reg) != mr.entries_sha256([], reg)


def test_entries_sha256_changes_when_an_unknown_spelling_becomes_known(tmp_path):
    known = _good("known", "gpt", "gpt", {"gateway": {"model": "known", "served": ["known"], "auth": "subscription"}})
    before = mr.entries_sha256(["mystery"], mr.load(_write_registry(tmp_path / "before.json", [known])))
    later = _good("later", "gpt", "gpt", {"gateway": {"model": "later", "served": ["mystery"], "auth": "subscription"}})
    later["aliases"] = ["mystery"]
    after = mr.entries_sha256(["mystery"], mr.load(_write_registry(tmp_path / "after.json", [known, later])))
    assert after != before


# --- invocation: today's wire string, spelled once -------------------------------------------------------

WIRE = [
    # repo/models.json coding chain
    ("claude-gpt-5.6-terra", "gateway", "claude-gpt-5.6-terra"),
    ("claude-deepseek-flash[1m]", "gateway", "claude-deepseek-flash[1m]"),
    ("claude-grok-4.7", "gateway", "claude-grok-4.7"),
    ("claude-sonnet-5-5", "gateway", "claude-sonnet-5-5"),
    # repo/models.json review chain
    ("grok-4.7", "cli", "grok-4.7"),
    ("claude:claude-deepseek-flash", "cli", "claude-deepseek-flash"),
    ("claude:claude-deepseek-flash", "direct", "claude-deepseek-flash"),
    ("gemini-3.8-flash-high", "cli", "gemini-3.8-flash-high"),
    ("claude:claude-opus-5-5", "cli", "claude-opus-5-5"),
    # repo/models.json vision chain
    ("grok-4.5", "cli", "grok-4.5"),
    ("gpt-5.6-terra", "cli", "gpt-5.6-terra"),
    ("gemini-3.8-flash-low", "cli", "gemini-3.8-flash-low"),
    # subject.yaml and the router graph
    ("deepseek-flash", "gateway", "deepseek-flash"),
    ("opus", "gateway", "opus"),
    ("sonnet", "gateway", "sonnet"),
    ("haiku", "gateway", "haiku"),
    # the live models.json
    ("claude:claude-deepseek-v4-flash[1m]", "cli", "claude-deepseek-v4-flash[1m]"),
    ("claude:claude-deepseek-v4-flash[1m]", "gateway", "claude-deepseek-v4-flash[1m]"),
    ("gpt-5.6-luna", "cli", "gpt-5.6-luna"),
    ("grok-4.6", "cli", "grok-4.6"),
    ("claude-deepseek-v4-flash", "gateway", "claude-deepseek-v4-flash"),
]


@pytest.mark.parametrize("raw,use,wire", WIRE)
def test_invocation_keeps_todays_wire_string(raw, use, wire, reg):
    ref = mr.normalize(raw, reg)
    assert ref.known, raw
    assert mr.invocation(ref, use, reg=reg).wire == wire


# The role chains this repository ships. A registry landing changes no caller, so a chain entry moves only in its
# own landing with its own test; this pin makes an accidental rewrite of models.json fail here.
PINNED_CHAINS = {
    "coding_models": ["claude-gpt-5.6-terra", "claude-deepseek-flash[1m]", "claude-grok-4.7", "claude-sonnet-5-5"],
    "review_models": ["grok-4.7", "claude:claude-deepseek-flash", "gemini-3.8-flash-high", "claude:claude-opus-5-5"],
    "vision_models": ["grok-4.5", "gpt-5.6-terra", "gemini-3.8-flash-low"],
}


def test_every_repo_spelling_is_known_and_pinned(reg):
    """The chains and graphs in this repository are the table's source; none may fall out of the registry."""
    mdoc = json.loads(MODELS.read_text())
    raws = []
    for key, pinned in PINNED_CHAINS.items():
        assert mdoc.get(key) == pinned, f"{key} moved: {mdoc.get(key)}"
        raws += pinned
    for raw in raws:
        assert mr.normalize(raw, reg).known, f"{raw} is not in the registry"
    for models in (mdoc.get("hours") or {}).values():
        for raw in models:
            assert mr.normalize(raw, reg).known, f"{raw} is not in the registry"
    for raw in mdoc.get("review_timeout_seconds_by_model") or {}:
        assert mr.normalize(raw, reg).known, f"{raw} is not in the registry"


# --- chain: v1, v2, mixed ----------------------------------------------------------------------------------

V1 = {
    "coding_models": ["claude-opus-5-5", "grok-4.7"],
    "review_models": ["grok-4.7", "claude:claude-deepseek-flash"],
    "vision_models": ["grok-4.5"],
    "off": {"review_models": {"grok-4.7": True}},
    "hours": {"coding_models": {"claude-opus-5-5": "19-07"}},
    "efforts": {"review_models": {"claude:claude-deepseek-flash": "high"}},
    "review_timeout_seconds_by_model": {"grok-4.7": 900},
    "review_no_tools": ["claude:claude-deepseek-flash"],
}

V2 = {"schema_version": 2, "chains": {
    "coding": [{"id": "claude-opus-5-5", "route": "gateway", "effort": "medium", "hrs": None,
                "on": True, "timeout_s": None}],
    "review": [{"id": "deepseek-flash", "raw": "claude:claude-deepseek-flash", "route": "direct",
                "effort": "high", "on": True, "timeout_s": 120, "no_tools": True}],
    "vision": [],
    "router": [{"id": "grok-4.7", "on": False}],
}}


def test_chain_reads_v1_and_folds_off_hours_efforts_timeout_and_no_tools(tmp_path):
    path = tmp_path / "models.json"
    path.write_text(json.dumps(V1))
    coding = mr.chain(path, "coding")
    assert [e.id for e in coding.entries] == ["claude-opus-5-5", "grok-4.7"]
    assert coding.entries[0].hrs == "19-07"
    assert coding.entries[0].route == "gateway"
    review = {e.id: e for e in mr.chain(path, "review").entries}
    assert review["grok-4.7"].on is False
    assert mr.chain(path, "review").entries[1].effort == "high"
    assert review["deepseek-flash"].no_tools is True
    assert review["grok-4.7"].timeout_s == 900


def test_chain_reads_v2(tmp_path):
    path = tmp_path / "models.json"
    path.write_text(json.dumps(V2))
    review = mr.chain(path, "review").entries
    assert len(review) == 1
    e = review[0]
    assert (e.id, e.route, e.effort, e.timeout_s, e.no_tools, e.raw) == (
        "deepseek-flash", "direct", "high", 120, True, "claude:claude-deepseek-flash")
    assert mr.chain(path, "router").entries[0].on is False
    assert mr.chain(path, "vision").entries == []


def test_chain_flags_a_mixed_file_and_chains_wins(tmp_path):
    path = tmp_path / "models.json"
    path.write_text(json.dumps({**V1, **V2}))
    c = mr.chain(path, "coding")
    assert c.mixed is True
    assert "coding_models" in c.stray
    assert [e.id for e in c.entries] == ["claude-opus-5-5"]


# --- eligible, quota, mark_limited, display_price ---------------------------------------------------------

def _chain_entry(tmp_path, models, chain, mid):
    path = tmp_path / "models.json"
    path.write_text(json.dumps(models))
    return next(e for e in mr.chain(path, chain).entries if e.id == mid)


def test_eligible_honours_off_and_hours(tmp_path):
    entry = _chain_entry(tmp_path, V1, "coding", "claude-opus-5-5")
    ev = mr.eligible(entry, shared=tmp_path, hour=12)
    assert ev.ok is False and "window" in ev.reason
    assert mr.eligible(entry, shared=tmp_path, hour=20).ok is True
    off = _chain_entry(tmp_path, V1, "review", "grok-4.7")
    ev = mr.eligible(off, shared=tmp_path, hour=12)
    assert ev.ok is False and ev.reason == "off"


def test_eligible_honours_a_marker_with_and_without_a_reset(tmp_path):
    entry = _chain_entry(tmp_path, {"coding_models": ["grok-4.7"]}, "coding", "grok-4.7")
    (tmp_path / "grok-limited").write_text("9999999999")
    ev = mr.eligible(entry, shared=tmp_path, now=1000)
    assert ev.ok is False and ev.reason == "usage limit" and ev.until == 9999999999
    (tmp_path / "grok-limited").write_text("no-reset")
    ev = mr.eligible(entry, shared=tmp_path, now=1000)
    assert ev.ok is False and ev.reason == "balance exhausted" and ev.until is None
    (tmp_path / "grok-limited").write_text("10")
    assert mr.eligible(entry, shared=tmp_path, now=1000).ok is True


def test_a_v1_chain_keeps_the_codex_marker_for_a_legacy_spelling(tmp_path):
    """The registry maps `claude-deepseek-flash[1m]` to the spend-only DeepSeek subscription, but a v1 file ran it
    through Codex: `codex-limited` still names it out, as the old chain reader did (SPEC 505 keeps v1 behavior)."""
    entry = _chain_entry(tmp_path, {"coding_models": ["claude-deepseek-flash[1m]"]}, "coding", "deepseek-flash")
    (tmp_path / "codex-limited").write_text("9999999999")
    ev = mr.eligible(entry, shared=tmp_path, now=1000)
    assert ev.ok is False and ev.reason == "usage limit"


def test_a_v2_chain_lets_the_registry_subscription_decide_the_marker(tmp_path):
    """A v2 file names registry ids, so the DeepSeek subscription is spend only and Codex's marker never touches it."""
    path = tmp_path / "models.json"
    path.write_text(json.dumps({"chains": {"coding": [{"raw": "claude-deepseek-flash[1m]"}]}}))
    entry = mr.chain(path, "coding").entries[0]
    (tmp_path / "codex-limited").write_text("9999999999")
    assert mr.eligible(entry, shared=tmp_path, now=1000).ok is True


def test_eligible_honours_the_claude_reserve(tmp_path):
    entry = _chain_entry(tmp_path, {"coding_models": ["claude-opus-5-5"]}, "coding", "claude-opus-5-5")
    (tmp_path / "hub-quota.json").write_text(json.dumps(
        {"claude": {"windows": [{"label": "weekly", "pct": 95, "resets": 9999999999}]}}))
    ev = mr.eligible(entry, shared=tmp_path, now=1000)
    assert ev.ok is False and "reserve" in ev.reason
    (tmp_path / "hub-quota.json").write_text(json.dumps(
        {"claude": {"windows": [{"label": "weekly", "pct": 50, "resets": 9999999999}]}}))
    assert mr.eligible(entry, shared=tmp_path, now=1000).ok is True


def test_quota_reads_the_marker_and_the_probe(tmp_path):
    (tmp_path / "grok-limited").write_text("no-reset")
    q = mr.quota("grok", shared=tmp_path, now=1000)
    assert q["limited"] is True and q["until"] is None and q["reason"] == "balance exhausted"
    (tmp_path / "hub-quota.json").write_text(json.dumps(
        {"gpt": {"windows": [{"label": "weekly", "pct": 40, "resets": 9}]}}))
    q = mr.quota("gpt", shared=tmp_path, now=1000)
    assert q["limited"] is False
    assert q["windows"] == [{"label": "weekly", "pct": 40, "resets": 9}]
    assert mr.quota("deepseek", shared=tmp_path)["spend_only"] is True


def test_mark_limited_writes_an_epoch_and_a_no_reset_marker(tmp_path):
    p = mr.mark_limited("grok", 2000000000, shared=tmp_path)
    assert p.name == "grok-limited" and p.read_text().strip() == "2000000000"
    p = mr.mark_limited("grok", None, shared=tmp_path)
    assert p.read_text().strip() == "no-reset"
    p = mr.mark_limited("gpt", 123, shared=tmp_path)
    assert p.read_text().strip() == "123"


def test_display_price_is_read_from_the_registry(reg):
    assert mr.display_price("claude-opus-5-5", reg)["output"] == _entry(reg, "claude-opus-5-5").display_price["output"]
    assert mr.display_price("claude:claude-opus-5-5", reg) == _entry(reg, "claude-opus-5-5").display_price
    assert mr.display_price("claude:a", reg) is None


# --- CLI ---------------------------------------------------------------------------------------------------

def _cli(*args, cwd=ROOT):
    return subprocess.run([sys.executable, "-m", "squad.modelreg", *args],
                          cwd=cwd, capture_output=True, text=True)


def test_cli_chain_eligible_tsv_prints_only_the_eligible_rows(tmp_path):
    path = tmp_path / "models.json"
    path.write_text(json.dumps({"coding_models": ["claude-opus-5-5", "grok-4.7"]}))
    shared = tmp_path / "shared"
    shared.mkdir()
    r = _cli("chain", "coding", "--models", str(path), "--eligible", "--tsv",
             "--shared", str(shared), "--now", "1000")
    assert r.returncode == 0, r.stderr
    rows = [line.split("\t") for line in r.stdout.splitlines()]
    assert [row[0] for row in rows] == ["claude-opus-5-5", "grok-4.7"]
    assert rows[0][2] == "gateway"


def test_cli_argv_is_nul_separated():
    r = _cli("argv", "claude-opus-5-5", "--use", "cli", "--effort", "medium")
    assert r.returncode == 0, r.stderr
    argv = r.stdout.split("\0")
    assert argv[:2] == ["claude", "-p"]
    assert "--model" in argv and "claude-opus-5-5" in argv
    assert "--effort" in argv and "medium" in argv


def test_cli_mark_limited_writes_the_marker(tmp_path):
    r = _cli("mark-limited", "grok", "--until", "2000000000", "--shared", str(tmp_path))
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "grok-limited").read_text().strip() == "2000000000"


def test_cli_check_passes_the_committed_registry_and_names_a_bad_entry(tmp_path):
    r = _cli("check")
    assert r.returncode == 0, r.stdout + r.stderr
    bad = _good("bad-gpt", "gpt", "gpt", {"gateway": {"model": "x", "served": ["x"], "auth": "api_key"}})
    path = _write_registry(tmp_path / "registry.json", [bad])
    r = _cli("--registry", str(path), "check")
    assert r.returncode == 1
    assert "bad-gpt" in r.stdout
