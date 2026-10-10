# /// script
# requires-python = ">=3.10"
# dependencies = ["claude-agent-sdk==0.2.161", "anyio", "pyyaml"]
# ///
"""Probe: does a gateway return `cache_read` for a plugin-qualified model? (ticket #150, acceptance 2)

Run: ANTHROPIC_BASE_URL=http://localhost:8016 uv run scripts/probe_gateway_cache.py claude-deepseek-v4-flash [model ...]
Each model gets the same long fixed prompt twice, in two fresh sessions, through `squad.sdk_adapter`. A model that caches shows
`cache_read` above zero on the second call. The answer goes into docs/research/node-budget-sizing.md. A probe, not a gate.
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIXED = "Reference text, repeated to fill a cacheable prefix. " + "The quick brown fox jumps over the lazy dog. " * 700
ASK = "Reply with the single word: ok."


def verdict(first, second):
    """Classify two usage dicts ({"tokens_in", "cache_read", ...} or None) as `caches`, `no_cache` or `unknown`.
    None means the gateway reported no usage: unknown, never zero."""
    if not first or not second:
        return "unknown"
    if (second.get("cache_read") or 0) > 0:
        return "caches"
    return "no_cache"


async def probe(model):
    from squad import defaults
    from squad.adapter import CallRequest
    from squad.sdk_adapter import SdkAdapter
    cfg = defaults.load()
    adapter = SdkAdapter.from_defaults(cfg)
    out = []
    for i in (1, 2):
        req = CallRequest(node="probe", entry=i, item=None, prompt=f"{FIXED}\n\n{ASK}", model=model, effort=None, tools=[], max_stalls=2,
                          max_spend=0.25, workspace=None, deadline=120, session_id=f"probe-{model}-{i}")
        res = await adapter.call(req)
        out.append(res.usage)
    return out


def main(models):
    for m in models:
        a, b = asyncio.run(probe(m))
        print(m, verdict(a, b), a, b)


if __name__ == "__main__":
    main(sys.argv[1:] or ["opus"])
