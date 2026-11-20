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


def test_the_legacy_deepseek_spelling_resolves_to_the_canonical_id(reg):
    """#730: one model, one id. The v4 spelling is an alias of the canonical entry, never an entry of its own, so a
    chain that names it and a price row that keys the other one cannot disagree."""
    assert "deepseek-v4-flash" not in reg.entries
    canonical = _entry(reg, "deepseek-flash")
    assert canonical.capabilities["tools"] is True
    assert mr.normalize("claude-deepseek-v4-flash[1m]").id == "deepseek-flash"


def test_deepseek_flash_uses_tools_so_a_coding_chain_keeps_it(reg):
    """#683: both DeepSeek V4.1 Flash ids used tools through `claude -p`; the canonical id is a coding choice."""
    assert _entry(reg, "deepseek-flash").capabilities["tools"] is True
    assert mr.compatible("claude-deepseek-flash[1m]", "coding", reg) == (True, None)
    assert mr.no_tools_route("claude-deepseek-flash[1m]", reg) is False


def test_haiku_has_an_id_and_the_three_short_names_are_aliases(reg):
    haiku = reg.aliases["haiku"]
    assert haiku in reg.entries
    assert reg.aliases["opus"] == "claude-opus-5-5"
    assert reg.aliases["sonnet"] == "claude-sonnet-5-5"


def test_retired_history_ids_have_an_entry_a_label_and_a_price(reg):
    for mid in ("grok-4.6", "gpt-5.6-sol-fast", "gpt-5.5"):   # gpt-6.1-sol (#686) and grok-4.5 answer, so they are live
        e = _entry(reg, mid)
        assert e.retired is True, mid
        assert e.label and e.display_price, mid


def test_a_served_model_is_selectable_and_a_withdrawn_one_may_stay_retired(reg, tmp_path):
    """#1164: the codex subscription still serves GPT-6 Astra, so its entry is live and its chain row is eligible.
    Only an entry the provider has withdrawn may stay retired with no route."""
    e = _entry(reg, "gpt-6-astra")
    assert e.retired is False
    assert e.routes["cli"]["auth"] == "subscription"
    assert "gpt-6-astra" in mr.invocation("gpt-6-astra", "cli", reg=reg).argv
    row = mr.chain({"coding_models": ["gpt-6-astra"]}, "coding", reg).entries[0]
    assert row.known and row.route and row.subscription == "gpt"
    assert mr.eligible(row, shared=tmp_path, hour=12).ok is True

    withdrawn = _good("gpt-withdrawn", "gpt", "gpt", {})
    withdrawn["retired"] = True
    wreg = mr.load(_write_registry(tmp_path / "registry.json", [withdrawn]))
    assert wreg.dropped == []
    assert wreg.entries["gpt-withdrawn"].retired is True
    assert mr.chain({"coding_models": ["gpt-withdrawn"]}, "coding", wreg).entries[0].route is None


def test_fast_gateway_ids_are_gateway_only(reg):
    for mid in ("gpt-5.6-terra-fast", "gpt-5.6-luna-fast"):
        e = _entry(reg, mid)
        assert e.gateway_only is True, mid
        assert set(e.routes) == {"gateway"}, mid


@pytest.mark.parametrize("mid,label,sub,sdk_wire,cli_runner", [
    ("gpt-6-luna", "GPT-6 Luna", "gpt", "claude-gpt-6-luna", "codex"),
    ("gpt-6.1-sol", "GPT-6.1 Sol", "gpt", "claude-gpt-6.1-sol", "codex"),   # answers on the codex subscription (#686)
    ("claude-haiku-5-5", "Claude Haiku 5.5", "claude", "claude-haiku-5-5", "claude"),
])
def test_a_live_coding_model_has_an_entry_and_a_route(reg, mid, label, sub, sdk_wire, cli_runner):
    """The live coding chain names gpt-6-luna and claude-haiku-5-5 (#679). Each needs its own entry, so a child
    takes the gateway wire, the quota marker of its subscription applies, and the hub shows its label."""
    e = _entry(reg, mid)
    assert (e.label, e.subscription, e.retired) == (label, sub, False)
    assert e.capabilities["tools"] is True
    assert mr.invocation(mid, "sdk", reg=reg).wire == sdk_wire
    assert e.routes["cli"]["argv"][0] == cli_runner
    doc = {"coding_models": [mid], "vision_models": [mid]}
    for name in ("coding", "vision"):
        row = mr.chain(doc, name, reg).entries[0]
        assert row.known and row.route and row.subscription == sub, (name, row)


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
    ("deepseek-v4-flash", "deepseek-flash", "gateway", None, None),
    ("claude:claude-deepseek-v4-flash[1m]", "deepseek-flash", "cli", None, "1m"),
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


# --- the chain id: the ordered registry ids, and one hash of that order (SPEC 1035) ----------------------

def test_chain_ids_names_each_entry_by_its_registry_id_in_its_own_order():
    reg = mr.load(REGISTRY)
    assert mr.chain_ids(["opus", "sonnet", "claude-haiku-4-5-20251001"], reg) == [
        "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5-20251001"]


def test_chain_ids_keeps_a_spelling_the_registry_does_not_know():
    """An unknown spelling is data, as `normalize` treats it: it stands in the chain as written, never dropped."""
    reg = mr.load(REGISTRY)
    assert mr.chain_ids(["sonnet", "no-such-model"], reg) == ["claude-sonnet-5-5", "no-such-model"]


def test_the_chain_hash_is_stable_for_one_order_and_differs_for_another():
    ids = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5-20251001"]
    assert mr.chain_sha256(ids) == mr.chain_sha256(list(ids))
    assert mr.chain_sha256(ids) != mr.chain_sha256(["claude-opus-5-5", "claude-haiku-4-5-20251001", "claude-sonnet-5-5"])


def test_the_chain_hash_is_the_sha256_of_the_ids_joined_in_order():
    """The recipe is stated for a reader who must recompute it: sha256 over the ids joined by a newline."""
    import hashlib
    assert mr.chain_sha256(["a", "b"]) == hashlib.sha256(b"a\nb").hexdigest()


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
    # The owner's chains: DeepSeek, the cheapest model, heads coding (the order the live copy runs), review and
    # vision; Luna heads the router, with Grok 4.5 behind it (3 of 3 on the router contract). Grok 4.7 covers the
    # window DeepSeek closes in coding, and in review it preempts Opus inside that window: a cost trade, not a
    # quality one, and every grok entry names its effort because unset means high and a timeout.
    "coding": ["claude-deepseek-flash[1m]", "claude-haiku-5-5", "grok-4.7", "gpt-6-luna"],   # no model that takes no tools (ADR-0147)
    "review": ["claude:claude-deepseek-flash", "grok-4.7", "claude:claude-opus-5-5", "gemini-3.8-flash-medium", "gpt-6.1-sol"],
    "vision": ["claude-deepseek-flash[1m]", "grok-4.7", "gpt-5.6-luna"],
    "router": ["claude-haiku-5-5", "gpt-6-luna", "claude-grok-4.5"],
    "editorial": ["ps5-qwen-coder", "grok-4.5", "claude-haiku-5-5"],
}


def test_an_alias_that_is_another_entrys_id_is_not_registered(tmp_path):
    """A spelling names one entry. An alias equal to another entry's id is ambiguous, so it is dropped and named."""
    p = tmp_path / "registry.json"
    p.write_text(json.dumps({"subscriptions": {"claude": {"label": "Claude"}}, "models": [
        {"id": "a", "label": "A", "provider": "claude", "subscription": "claude",
         "routes": {"gateway": {"auth": "subscription", "model": "a", "served": ["a"]}}, "aliases": ["b"]},
        {"id": "b", "label": "B", "provider": "claude", "subscription": "claude",
         "routes": {"gateway": {"auth": "subscription", "model": "b", "served": ["b"]}}, "aliases": []}]}))
    reg = mr.load(p)
    assert "b" not in reg.aliases and mr.normalize("b", reg).id == "b"
    assert any(d["id"] == "a" and "another entry" in d["reason"] for d in reg.dropped)


def test_every_repo_spelling_is_known_and_pinned(reg):
    """The chains and graphs in this repository are the table's source; none may fall out of the registry."""
    mdoc = json.loads(MODELS.read_text())
    assert mdoc["schema_version"] == 2
    for name, pinned in PINNED_CHAINS.items():
        raws = [e.raw for e in mr.chain(mdoc, name, reg).entries]
        assert raws == pinned, f"{name} moved: {raws}"
        for raw in raws:
            assert mr.normalize(raw, reg).known, f"{raw} is not in the registry"


def test_the_editorial_chain_leads_with_the_local_ps5_and_takes_the_spelling_the_gateway_serves(reg):
    """Editorial text -- a commit message, a pull-request body, a status summary -- is short, serial and never on a
    gate's path, so it takes the owner's local PS5 first and wakes no subscription. The head is the PS5 model on the
    bare `ps5-qwen-coder` spelling, which the runner gateway answers; the `claude-` prefixed spelling it also
    answers stays served, so no caller breaks. Behind it sits Grok 4.5 on its idle subscription, which fits the
    shape of this work: short, format-bound, and checked mechanically. Effort is named low: unset, grok reasons at
    its high default for a two-line summary."""
    mdoc = json.loads(MODELS.read_text())
    entries = mr.chain(mdoc, "editorial", reg).entries
    assert entries[0].id == "ps5-qwen-coder", [e.id for e in entries]
    assert entries[0].route == "gateway", entries[0].route
    assert entries[0].raw == "ps5-qwen-coder", entries[0].raw
    assert "ps5-qwen-coder" in reg.entries["ps5-qwen-coder"].routes["gateway"]["served"]
    assert entries[0].effort == "low", "a local model on a 9B head takes low effort"
    fallback = entries[1]
    assert fallback.id == "grok-4.5" and fallback.route == "gateway", (fallback.id, fallback.route)
    assert fallback.effort == "low", "grok always reasons, so its effort is named"
    assert entries[2].id == "claude-haiku-5-5" and entries[2].route == "gateway", (entries[2].id, entries[2].route)


def test_every_grok_entry_names_its_reasoning_effort(reg):
    """Grok always reasons, its default effort is high, and no setting disables the reasoning. An entry that named no
    effort made every review reason for minutes and die on the timeout, so the chains that ship name the effort on
    every grok entry names low, so a later reorder cannot quietly drop it."""
    mdoc = json.loads(MODELS.read_text())
    wrong = [(name, e.get("id"), e.get("effort")) for name, chain in mdoc["chains"].items() for e in chain
             if str(e.get("id", "")).startswith("grok") and e.get("effort") != "low"]
    assert wrong == [], f"a grok entry does not name low effort: {wrong}"


def test_a_local_gateway_route_serves_both_spellings_of_its_model(reg):
    """The runner's gateway answers a model under its `claude-` prefixed name and under its bare name (probed
    2026-11-03 on the runner gateway: both `claude-ps5-qwen-coder` and `ps5-qwen-coder` return a completion), and the
    bare one is the name the local model server itself uses. Both are served, so a caller may spell either, and
    `_wire` sends the spelling it was given."""
    local = {"ps5-qwen-coder": "claude-ps5-qwen-coder", "nemotron-nano": "claude-nemotron-nano",
             "nemotron-super": "claude-nemotron-super"}
    for mid, prefixed in local.items():
        route = reg.entries[mid].routes["gateway"]
        assert route["served"] == [prefixed, mid], (mid, route)
        assert route["model"] == prefixed, (mid, route)
        assert mr._wire(route, mid) == mid, mid
        assert mr._wire(route, prefixed) == prefixed, mid
        assert mr.normalize(prefixed, reg).id == mid, mid


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
    """#1417: the grok marker holds its write epoch and is fresh for the TTL; the other pools hold a reset epoch."""
    entry = _chain_entry(tmp_path, {"coding_models": ["grok-4.7"]}, "coding", "grok-4.7")
    (tmp_path / "grok-limited").write_text("1000")
    ev = mr.eligible(entry, shared=tmp_path, now=1000)
    assert ev.ok is False and ev.reason == "balance exhausted" and ev.until == 1000 + 21600
    assert mr.eligible(entry, shared=tmp_path, now=1000 + 21600).ok is True


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
    (tmp_path / "grok-limited").write_text("1000")   # #1417: the marker is its write epoch, fresh for the TTL
    q = mr.quota("grok", shared=tmp_path, now=1000)
    assert q["limited"] is True and q["until"] == 1000 + 21600 and q["reason"] == "balance exhausted"
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


# --- #678: one quota path, a stale cache, a spent weekly window -------------------------------------------------

def _quota(tmp_path, doc):
    (tmp_path / "hub-quota.json").write_text(json.dumps(doc))


def test_the_shared_directory_is_the_seam_one_without_sg_shared(tmp_path, monkeypatch):
    """#678: with no SG_SHARED the resolver read `$SG_STATE`, a per-profile directory, so it and the hub read two files.
    The seam names one directory for every profile, as env.sh and address.sh do."""
    monkeypatch.delenv("SG_SHARED", raising=False)
    monkeypatch.setenv("SG_STATE", str(tmp_path / "agent-squad"))
    monkeypatch.setenv("SG_STATE_BASE", str(tmp_path / "base"))
    assert mr._shared_dir() == tmp_path / "base" / "_shared"
    monkeypatch.setenv("SG_SHARED", str(tmp_path / "shared"))
    assert mr._shared_dir() == tmp_path / "shared"


def test_a_spent_weekly_window_closes_every_subscription_until_its_reset(tmp_path):
    """#678: grok at 100% weekly stayed eligible. A spent window with a reset ahead skips the entry until that reset,
    even when the reading is old: a window's use only grows until it resets."""
    entry = _chain_entry(tmp_path, {"coding_models": ["grok-4.5"]}, "coding", "grok-4.5")
    _quota(tmp_path, {"grok": {"windows": [{"label": "weekly", "pct": 100.0, "resets": 500000}], "asof": 1000,
                               "until": 500000}})
    ev = mr.eligible(entry, shared=tmp_path, now=200000)
    assert ev.ok is False and ev.reason == "weekly quota spent" and ev.until == 500000
    assert mr.eligible(entry, shared=tmp_path, now=600000).ok is True


def test_a_spent_window_takes_the_row_until_when_the_window_names_no_reset(tmp_path):
    entry = _chain_entry(tmp_path, {"coding_models": ["gemini-3.8-flash"]}, "coding", "gemini-3.8-flash")
    _quota(tmp_path, {"gemini": {"windows": [{"label": "weekly", "pct": 100}], "asof": 1000, "until": 9000}})
    ev = mr.eligible(entry, shared=tmp_path, now=1000)
    assert ev.ok is False and ev.reason == "weekly quota spent" and ev.until == 9000


def test_a_stale_cache_is_unknown_and_says_so(tmp_path, monkeypatch):
    """#678: a cache days old said claude 8%. An old reading never makes an entry eligible by itself, and never blocks
    one: the entry runs, and the reason names the stale cache."""
    entry = _chain_entry(tmp_path, {"coding_models": ["claude-opus-5-5"]}, "coding", "claude-opus-5-5")
    _quota(tmp_path, {"claude": {"windows": [{"label": "weekly", "pct": 95}], "asof": 1000}})
    now = 1000 + 7 * 3600
    ev = mr.eligible(entry, shared=tmp_path, now=now)
    assert ev.ok is True and "stale" in ev.reason
    ev = mr.eligible(entry, shared=tmp_path, now=1000 + 60)
    assert ev.ok is False and ev.reason == "claude weekly reserve"
    monkeypatch.setenv("SG_QUOTA_STALE_SECONDS", str(8 * 3600))
    assert mr.eligible(entry, shared=tmp_path, now=now).ok is False


def test_a_stale_cache_still_keeps_the_marker(tmp_path):
    entry = _chain_entry(tmp_path, {"coding_models": ["claude-opus-5-5"]}, "coding", "claude-opus-5-5")
    _quota(tmp_path, {"claude": {"windows": [{"label": "weekly", "pct": 5}], "asof": 1000}})
    (tmp_path / "claude-limited").write_text("9999999999")
    ev = mr.eligible(entry, shared=tmp_path, now=1000 + 30 * 3600)
    assert ev.ok is False and ev.reason == "usage limit"


def test_the_claude_reserve_holds_until_its_reset_and_lifts_after_it(tmp_path):
    entry = _chain_entry(tmp_path, {"coding_models": ["claude-opus-5-5"]}, "coding", "claude-opus-5-5")
    _quota(tmp_path, {"claude": {"windows": [{"label": "weekly", "pct": 92.0, "resets": 50000}], "asof": 1000}})
    ev = mr.eligible(entry, shared=tmp_path, now=40000)
    assert ev.ok is False and ev.reason == "claude weekly reserve" and ev.until == 50000
    assert mr.eligible(entry, shared=tmp_path, now=60000).ok is True


def test_a_spent_entry_is_skipped_and_the_chain_order_kept(tmp_path):
    """#678, owner: an exhausted entry is skipped until its reset, never reordered."""
    path = tmp_path / "models.json"
    path.write_text(json.dumps({"coding_models": ["grok-4.5", "gpt-5.6-terra", "claude-opus-5-5", "gemini-3.8-flash"]}))
    _quota(tmp_path, {"grok": {"windows": [{"label": "weekly", "pct": 100, "resets": 9000}], "asof": 1000},
                      "claude": {"windows": [{"label": "weekly", "pct": 50, "resets": 9000}], "asof": 1000},
                      "gemini": {"windows": [{"label": "weekly", "pct": 100, "resets": 9000}], "asof": 1000}})
    entries = mr.chain(path, "coding").entries
    assert [e.id for e in entries if mr.eligible(e, shared=tmp_path, now=1000, hour=3).ok] == \
        ["gpt-5.6-terra", "claude-opus-5-5"]


def test_spent_names_the_spellings_whose_subscription_is_spent(tmp_path):
    """The loop's launcher hands the run `--spent-models`; the resolver names the ids whose subscription the quota cache
    or a marker closes, so a child is not given a spent subscription."""
    _quota(tmp_path, {"grok": {"windows": [{"label": "weekly", "pct": 100, "resets": 9000}], "asof": 1000}})
    assert mr.spent(["grok-4.5", "claude-opus-5-5", "nobody-knows"], shared=tmp_path, now=1000) == ["grok-4.5"]


def test_write_chains_with_no_doc_keeps_the_files_other_keys(tmp_path):
    """A caller that writes only the chains changes only the chains.

    `doc` defaults to `{}`, so a call that omits it wrote a file of two keys and dropped every constant beside the
    chains -- the review timeout, the review line cap and the stall budget. A live Save through that path left every
    review BLOCKED with `[: null: integer expression expected`, and nothing refused."""
    path = tmp_path / "models.json"
    path.write_text(json.dumps({"schema_version": 2,
                                "chains": {"coding": [{"id": "grok-4.7"}], "review": [], "vision": [], "router": []},
                                "review_timeout_seconds": 360, "review_max_lines": 60}))
    mr.write_chains(path, {"coding": [{"id": "grok-4.7"}], "review": [], "vision": [], "router": []})
    out = json.loads(path.read_text())
    assert out["review_timeout_seconds"] == 360, out
    assert out["review_max_lines"] == 60, out
    assert [e["id"] for e in out["chains"]["coding"]] == ["grok-4.7"], out


def test_write_chains_with_no_doc_and_no_file_writes_the_chains(tmp_path):
    path = tmp_path / "models.json"
    mr.write_chains(path, {"coding": [{"id": "grok-4.7"}], "review": [], "vision": [], "router": []})
    out = json.loads(path.read_text())
    assert set(out) == {"schema_version", "chains"}, out
    assert [e["id"] for e in out["chains"]["coding"]] == ["grok-4.7"], out


def test_grok_4_5_is_live_and_runs_the_router_on_the_gateway():
    """Grok 4.5 passed the router contract 3 of 3 on the gateway, so the registry keeps it live, and the hub panel shows it."""
    ok, reason = mr.compatible("claude-grok-4.5", "router")
    assert (ok, reason) == (True, None)
    assert mr.model("claude-grok-4.5").retired is False


def test_deepseek_flash_sees_images_and_runs_the_vision_chain():
    """DeepSeek V4.1 Flash read a test image 3 of 3 through the gateway, served by DeepSeek itself, so vision keeps it."""
    assert mr.compatible("deepseek-flash", "vision") == (True, None)
    assert mr.compatible("claude-deepseek-flash[1m]", "vision") == (True, None)


def test_the_registry_path_is_resolved_once_per_env_value(monkeypatch):
    """AC A of #2582: a second lookup for a value it already answered does no resolution work, and a new value answers anew."""
    calls = []
    real = mr._first_file
    monkeypatch.setattr(mr, "_first_file", lambda cs: (calls.append(1), real(cs))[1])
    monkeypatch.delenv("SG_MODELS_REGISTRY", raising=False)
    mr._default_registry_path()
    n = len(calls)
    assert mr._default_registry_path() == mr._default_registry_path(), "the same value answers the same path"
    assert len(calls) == n, "the second lookup resolved the path again"
    monkeypatch.setenv("SG_MODELS_REGISTRY", str(REGISTRY))
    assert mr._default_registry_path() == REGISTRY
    assert len(calls) == n, "a registry the environment names needs no resolution"
    monkeypatch.setenv("SG_MODELS_REGISTRY", str(SHIPPED_REGISTRY))
    assert mr._default_registry_path() == SHIPPED_REGISTRY, "a new value of the variable answers its own path"
