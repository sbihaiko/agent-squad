"""#783: on the gateway route the usage carries no Anthropic cache_read key, so every cache re-read read as fresh input."""
import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace

from squad.sdk_adapter import usage_of

TOKENS = os.path.join(os.path.dirname(__file__), "..", "contrib", "squad-goal", "scripts", "tokens.py")


def _r(usage, turns=None):
    return SimpleNamespace(usage=usage, num_turns=turns)


def test_anthropic_shape_is_unchanged():
    u = usage_of(_r({"input_tokens": 10, "cache_creation_input_tokens": 5, "cache_read_input_tokens": 100, "output_tokens": 7}))
    assert u == {"tokens_in": 115, "tokens_out": 7, "cache_read": 100, "cache_write": 5}


def test_openai_shape_cached_tokens_are_a_cache_read():
    # prompt_tokens_details.cached_tokens is a share of input_tokens, never an addition to it
    u = usage_of(_r({"input_tokens": 1000, "output_tokens": 7, "prompt_tokens_details": {"cached_tokens": 900}}))
    assert u["tokens_in"] == 1000 and u["cache_read"] == 900 and "cache_estimated" not in u


def test_input_tokens_details_and_flat_names_are_read():
    assert usage_of(_r({"input_tokens": 50, "output_tokens": 1, "input_tokens_details": {"cached_tokens": 40}}))["cache_read"] == 40
    assert usage_of(_r({"input_tokens": 50, "output_tokens": 1, "cache_read_tokens": 30}))["cache_read"] == 30


def test_no_cache_key_on_a_multi_turn_call_is_estimated_and_marked():
    # 10 turns of a growing context: the fresh input is about the last context, 2 * total / turns
    u = usage_of(_r({"input_tokens": 5_000_000, "output_tokens": 20_000}, turns=10))
    assert u["cache_estimated"] is True
    assert u["cache_read"] == 4_000_000 and u["tokens_in"] == 5_000_000


def test_a_reported_zero_is_a_measure_never_an_estimate():
    u = usage_of(_r({"input_tokens": 500, "output_tokens": 1, "cache_read_input_tokens": 0}, turns=10))
    assert u["cache_read"] == 0 and "cache_estimated" not in u


def test_one_turn_with_no_cache_key_estimates_nothing():
    u = usage_of(_r({"input_tokens": 500, "output_tokens": 1}, turns=1))
    assert "cache_read" not in u and "cache_estimated" not in u


def test_the_guard_counts_fresh_input_and_output_only(tmp_path):
    run = tmp_path / "co" / "runs" / "r1"
    run.mkdir(parents=True)
    u = usage_of(_r({"input_tokens": 5_000_000, "output_tokens": 20_000}, turns=10))
    ev = {"event": "outcome", "ts": time.time(), "node": "code", "model": "haiku", **u}
    (run / "record.jsonl").write_text(json.dumps(ev) + "\n")
    out = subprocess.run([sys.executable, "-I", TOKENS, "burners", str(tmp_path / "st"), "--checkout", str(tmp_path / "co"),
                          "--cache-weight", "0", "--json"], capture_output=True, text=True, check=True, timeout=10)
    assert json.loads(out.stdout)["total"] == 1_000_000 + 20_000
