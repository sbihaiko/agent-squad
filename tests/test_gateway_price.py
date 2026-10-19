"""A gateway model's spend comes from its own price table, never from the Claude rates the SDK borrows.

The SDK prices a model it does not know at Claude rates, so `total_cost_usd` of a gateway call is many times
its real cost, and a node cap stops the call on a price the model does not have.
"""
import pytest

from squad import defaults, prices, record
from squad.adapter import AdapterError, ScriptedAdapter
from squad.engine import Engine
from squad.graph import from_doc
from squad.sdk_adapter import SdkAdapter
from tests.flows import agent, doc
from tests.gitrepo import make_repo
from tests.test_sdk_adapter import AssistantMessage, Options, ResultMessage, request, stub

pytestmark = pytest.mark.anyio

GATEWAY = "claude-deepseek-v4-flash"
FLASH = {"input": 0.30, "cache_read": 0.006, "cache_write": 0.30, "output": 1.20}
CLIENT = {"input": 3.0, "cache_read": 0.30, "cache_write": 3.75, "output": 15.0}
TABLE = {"client": CLIENT, "models": {GATEWAY: FLASH}}
TERRA = "claude-gpt-5.6-terra"
# The API-equivalent price of GPT-5.6 Terra, as the hub's PRICES row reads it (cache hit, cache miss, output); it runs on
# the owner's subscription, so this is what it would cost, never what the SDK borrows at Claude rates.
TERRA_RATES = {"input": 2.00, "cache_read": 0.20, "cache_write": 2.00, "output": 12.00}
# The usage of a real gateway child: tokens_in holds every prompt token, cache reads included.
USAGE = {"input_tokens": 110_000, "cache_read_input_tokens": 10_590_000, "cache_creation_input_tokens": 0, "output_tokens": 64_000}


def adapter(messages, seen=None, table=TABLE):
    return SdkAdapter(query=stub(messages, seen), options_cls=Options, prices=table)


def test_the_defaults_file_holds_one_price_table_seeded_with_the_flash_peak_price():
    cfg = defaults.load()
    assert cfg["prices"]["models"][GATEWAY] == FLASH
    assert set(cfg["prices"]["client"]) == set(prices.KEYS)
    assert defaults.check(cfg) == []


def test_a_context_suffix_resolves_to_the_bare_id_price():
    """#574: `claude-deepseek-flash[1m]` is the same model as `claude-deepseek-flash`. The suffix names how much context
    a call may use, never a different price, so both spellings read one entry of the default table."""
    cfg = defaults.load()["prices"]
    assert prices.rates(cfg, "claude-deepseek-flash[1m]") == prices.rates(cfg, "claude-deepseek-flash") == FLASH
    assert prices.rates(cfg, "claude-sonnet-5-5[1m]") is None     # a bare id with no entry stays unpriced


def test_terra_has_an_explicit_subscription_price_so_the_cap_never_reads_it_at_claude_rates():
    """#574: GPT-5.6 Terra runs on the owner's subscription. Without an entry the SDK prices it at the Claude client
    rates, about 1.3 times its output price, so the cap stops it early; the entry names its API-equivalent price."""
    cfg = defaults.load()
    r = cfg["prices"]["models"][TERRA]
    assert r == TERRA_RATES
    assert r["output"] < CLIENT["output"]     # never the borrowed Claude rate
    assert defaults.check(cfg) == []


def test_a_price_entry_with_a_missing_or_negative_rate_is_a_defect():
    cfg = defaults.load()
    cfg["prices"]["models"][GATEWAY] = {**FLASH, "output": -1}
    assert "prices.models.claude-deepseek-v4-flash" in defaults.check(cfg)
    cfg["prices"]["models"][GATEWAY] = {k: v for k, v in FLASH.items() if k != "cache_read"}
    assert "prices.models.claude-deepseek-v4-flash" in defaults.check(cfg)


def test_the_price_counts_each_token_once_with_cache_tokens_split_out_of_tokens_in():
    usage = {"tokens_in": 10_700_000, "tokens_out": 64_000, "cache_read": 10_590_000, "cache_write": 0}
    assert prices.spend(FLASH, usage) == pytest.approx(0.11 * 0.30 + 10.59 * 0.006 + 0.064 * 1.20)
    assert prices.spend(FLASH, {"tokens_in": 1_000_000, "tokens_out": 0, "cache_write": 1_000_000}) == pytest.approx(0.30)
    assert prices.spend(FLASH, None) is None


def test_a_renamed_or_aliased_spelling_keeps_its_table_price():
    """A run records the spelling it was given. The table keys are the recorded ids, so the registry resolves the rest."""
    table = defaults.load()["prices"]
    assert prices.rates(table, GATEWAY) == FLASH
    assert prices.rates(table, "deepseek-v4-flash") == FLASH
    assert prices.rates(table, "claude-deepseek-v4-flash[1m]") == FLASH
    assert prices.rates(table, "claude-sonnet-5-5") is None


def test_an_alias_is_never_priced_through_another_models_row(tmp_path, monkeypatch):
    """Precedence: the raw key wins, then the canonical id, then an alias of that entry. An alias equal to another
    entry's id is not registered, so an entry with no row of its own is never priced at a stranger's rate."""
    import json
    from squad import modelreg
    p = tmp_path / "registry.json"
    p.write_text(json.dumps({"subscriptions": {"claude": {"label": "Claude"}}, "models": [
        {"id": "a", "label": "A", "provider": "claude", "subscription": "claude",
         "routes": {"gateway": {"auth": "subscription", "model": "a", "served": ["a", "b"]}}, "aliases": ["b"]},
        {"id": "b", "label": "B", "provider": "claude", "subscription": "claude",
         "routes": {"gateway": {"auth": "subscription", "model": "b", "served": ["b"]}}, "aliases": []}]}))
    monkeypatch.setenv("SG_MODELS_REGISTRY", str(p))
    modelreg._CACHE.clear()
    try:
        table = {"client": CLIENT, "models": {"b": FLASH}}
        assert prices.rates(table, "b") == FLASH          # the raw key wins
        assert prices.rates(table, "a") is None           # never priced through b's row
    finally:
        modelreg._CACHE.clear()


def test_the_no_tools_flash_spelling_resolves_to_its_own_row_never_the_v4_row():
    """`deepseek-flash` is its own id, not an alias of the v4 id, so a changed row is never read through the other."""
    models = defaults.load()["prices"]["models"]
    own = {**models["claude-deepseek-flash"], "output": 9.99}
    table = {"client": defaults.load()["prices"]["client"], "models": {**models, "claude-deepseek-flash": own}}
    assert prices.rates(table, "deepseek-flash") == own
    assert prices.rates(table, "claude-deepseek-flash[1m]") == own
    assert prices.rates(table, "claude-deepseek-v4-flash") == FLASH


async def test_a_priced_gateway_call_spends_its_table_price_and_names_the_source():
    r = await adapter([ResultMessage(total_cost_usd=4.08, usage=USAGE)]).call(request(model=GATEWAY))
    assert r.spend == pytest.approx(0.11 * 0.30 + 10.59 * 0.006 + 0.064 * 1.20)
    assert r.spend_source == "price_table"


async def test_a_call_on_a_suffixed_model_spends_its_table_price_not_the_sdk_cost():
    """#574: the overnight DeepSeek calls came back at 1.44 and 1.52 USD because `[1m]` hid the entry, so the call
    kept `total_cost_usd` at Claude rates. The real cost is about 0.08 USD."""
    r = await adapter([ResultMessage(total_cost_usd=1.44, usage=USAGE)], table=defaults.load()["prices"]).call(
        request(model="claude-deepseek-flash[1m]"))
    assert r.spend == pytest.approx(0.11 * 0.30 + 10.59 * 0.006 + 0.064 * 1.20)
    assert r.spend_source == "price_table"


async def test_a_refusal_after_work_carries_the_spend_of_the_result_it_produced():
    """#573: the CLI reports a terminal error as a result message and then exits non-zero. That result names what the
    work spent, so the refusal settles with it instead of holding its whole reservation as unmeasured."""
    async def query(*, prompt, options):
        yield AssistantMessage()     # the call did work before the provider refused it
        yield ResultMessage(total_cost_usd=4.08, usage=USAGE)
        raise RuntimeError("Claude Code returned an error result: You've hit your session limit")
    with pytest.raises(AdapterError) as info:
        await SdkAdapter(query=query, options_cls=Options, prices=TABLE).call(request(model=GATEWAY))
    assert info.value.served is True
    assert info.value.spend == pytest.approx(0.11 * 0.30 + 10.59 * 0.006 + 0.064 * 1.20)   # its own price, not the borrowed one
    assert info.value.spend_source == "price_table"


async def test_a_refusal_whose_result_reports_no_cost_is_estimated_from_its_tokens():
    """#573: a result with no `total_cost_usd` still reports its tokens, so the refusal settles with their price at the
    client rates the SDK borrows, never with its whole reservation."""
    async def query(*, prompt, options):
        yield ResultMessage(total_cost_usd=None, usage=USAGE)
        raise RuntimeError("Claude Code returned an error result: socket closed")
    with pytest.raises(AdapterError) as info:
        await SdkAdapter(query=query, options_cls=Options, prices=TABLE).call(request(model="claude-sonnet-5-5"))
    assert info.value.spend == pytest.approx(0.11 * 3.0 + 10.59 * 0.30 + 0.064 * 15.0)
    assert info.value.spend_source == "price_table"


async def test_a_model_with_no_entry_keeps_the_sdk_cost_and_its_source():
    r = await adapter([ResultMessage(total_cost_usd=4.08, usage=USAGE)]).call(request(model="claude-sonnet-5-5"))
    assert (r.spend, r.spend_source) == (4.08, "total_cost_usd")


async def test_a_priced_call_with_no_usage_keeps_the_sdk_cost_as_its_bound():
    r = await adapter([ResultMessage(total_cost_usd=4.08, usage=None)]).call(request(model=GATEWAY))
    assert (r.spend, r.spend_source) == (4.08, "total_cost_usd")


def test_the_sdk_budget_of_a_priced_model_is_scaled_so_the_borrowed_price_never_stops_it_first():
    """The SDK stops a call when its own cost reaches max_budget_usd. Its cost is at most the largest ratio of a borrowed
    rate to the table rate times the table cost, so a budget scaled by that ratio stops no call below the true cap."""
    a = adapter([])
    ratio = max(CLIENT[k] / FLASH[k] for k in prices.KEYS)
    assert a.options(request(model=GATEWAY, max_spend=2.0)).kw["max_budget_usd"] == pytest.approx(2.0 * ratio)
    assert a.options(request(model="claude-sonnet-5-5", max_spend=2.0)).kw["max_budget_usd"] == 2.0


def test_the_adapter_built_from_the_defaults_reads_the_price_table():
    a = SdkAdapter.from_defaults(defaults.load(), query=object(), options_cls=Options, hook_cls=dict)
    assert a.options(request(model=GATEWAY, max_spend=1.0)).kw["max_budget_usd"] > 1.0


@pytest.mark.heavy
async def test_the_outcome_names_the_spend_source(tmp_path):
    a = agent("a", output_key="x")
    script = {"a": {"output": {"x": 1}, "spend": 0.17, "turns": 2, "spend_source": "price_table"}}
    eng = Engine(from_doc(doc([a], ["a"])), ScriptedAdapter(script), tmp_path / "run", target=make_repo(tmp_path / "repo"))
    await eng.run()
    ev = [e for e in record.read(tmp_path / "run" / "record.jsonl") if e["event"] == "outcome"][0]
    assert (ev["spend"], ev["spend_source"]) == (0.17, "price_table")


@pytest.mark.heavy
async def test_a_dispatch_child_outcome_names_the_spend_source(tmp_path):
    from tests.test_waves import child_outs, make, req, routed, sibling_repo, writer
    priced = {**writer("a.txt"), "spend_source": "price_table"}
    eng = make(tmp_path, {"router": routed(req()), "route.router.1": priced}, target=sibling_repo(tmp_path))
    await eng.run()
    assert child_outs(tmp_path)["route.router.1"]["spend_source"] == "price_table"
