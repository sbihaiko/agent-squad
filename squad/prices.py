"""Gateway model prices: the spend of a call on a model the SDK cannot price.

The SDK prices a model it does not know at Claude rates (`prices.client` in config/agent-defaults.yaml). For a model
with an entry in `prices.models`, the spend of a call is its tokens times the entry's rates, in USD per 1M tokens.
A model with no entry keeps the SDK's `total_cost_usd`. A model id may carry a context suffix (`[1m]`); the suffix
names how much context a call may use, never a different price, so the lookup strips it.
"""
import re

KEYS = ("input", "cache_read", "cache_write", "output")
CONTEXT_SUFFIX = re.compile(r"\[[^\]]*\]$")
TABLE_SOURCE = "price_table"
SDK_SOURCE = "total_cost_usd"


def rates(table, model):
    """The rates of one model, or None when the table has no entry for it.

    A context suffix (`claude-deepseek-flash[1m]`) is stripped before the read, so both spellings of one model
    resolve to one entry."""
    models = (table or {}).get("models") or {}
    if model in models:
        return models[model]
    return models.get(CONTEXT_SUFFIX.sub("", model or ""))


def spend(r, usage):
    """The USD price of one call's usage. tokens_in holds every prompt token, cache reads and writes included, so the
    plain input is tokens_in minus both cache shares. None when the usage is unknown."""
    if not usage:
        return None
    read, write = usage.get("cache_read", 0), usage.get("cache_write", 0)
    plain = usage["tokens_in"] - read - write
    return (plain * r["input"] + read * r["cache_read"] + write * r["cache_write"] + usage["tokens_out"] * r["output"]) / 1e6


def sdk_budget(table, model, cap):
    """The max_budget_usd to give the SDK so its borrowed price never stops a call below the true cap.

    The SDK's cost is at most the largest ratio of a client rate to the model rate times the table cost, so a cap
    scaled by that ratio stops no call before its table cost reaches the cap. A model with no entry keeps the cap."""
    r = rates(table, model)
    if r is None or cap is None:
        return cap
    client = table["client"]
    return cap * max(1.0, max(client[k] / r[k] for k in KEYS if r[k] > 0))


def defects(block):
    """The defects of a `prices` block: every rate is a number of zero or more, and the client entry is complete."""
    if block is None:
        return []
    if not isinstance(block, dict):
        return ["prices"]
    bad = []
    entries = [("prices.client", block.get("client"))] + [(f"prices.models.{m}", v) for m, v in (block.get("models") or {}).items()]
    for name, r in entries:
        if not isinstance(r, dict) or set(r) != set(KEYS) or any(isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0 for v in r.values()):
            bad.append(name)
    return bad
