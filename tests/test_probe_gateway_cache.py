"""Guard for #150 acceptance 2: the gateway cache probe classifies usage honestly, and the sizing page states the open answer."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _probe():
    spec = importlib.util.spec_from_file_location("probe_gateway_cache", ROOT / "scripts" / "probe_gateway_cache.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_a_second_call_with_cache_read_caches():
    assert _probe().verdict({"tokens_in": 9, "cache_read": 0}, {"tokens_in": 9, "cache_read": 7}) == "caches"


def test_zero_or_absent_cache_read_is_no_cache_and_missing_usage_is_unknown():
    v = _probe().verdict
    assert v({"tokens_in": 9}, {"tokens_in": 9, "cache_read": 0}) == "no_cache"
    assert v(None, {"tokens_in": 9}) == "unknown" and v({"tokens_in": 9}, None) == "unknown"


def test_the_sizing_page_names_what_the_probe_found_and_budgets_assume_an_uncached_prefix():
    text = (ROOT / "docs" / "research" / "node-budget-sizing.md").read_text()
    assert "Unknown for the reporter's gateway" in text and "caches in a real run" in text
    assert "uncached prefix" in text
