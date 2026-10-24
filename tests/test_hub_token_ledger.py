"""The hub's cost & usage stats read the review ledger too (#787).

review.sh writes one line per review call to $SG_STATE/token-usage.tsv. The stats' "Tokens by model" pie and the
Spend totals count those calls under their canonical model id (the one model registry, ADR-0147), so the pie does
not show the coding model at 100%. """
import importlib.util
import os
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_token_ledger", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def state(hub, tmp_path, monkeypatch):
    monkeypatch.setattr(hub, "STATE", tmp_path)
    monkeypatch.setattr(hub, "all_runs", lambda: [])
    return tmp_path


def _ledger(state, *rows):
    """Ledger lines in review.sh's layout: ts, consumer, model, in, out, cache_read."""
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    (state / "token-usage.tsv").write_text("".join(f"{now}\t{c}\t{m}\t{i}\t{o}\t{cr}\n" for c, m, i, o, cr in rows))


def _models(hub):
    return {m["model"]: m for m in hub.stats("day")["models"]}


def test_review_calls_reach_the_model_pie_under_canonical_ids(hub, state):
    _ledger(state,
            ("review:fix_1-x", "claude:claude-opus-5-5", 100, 30, 400),
            ("review:fix_1-x#2", "gemini-3.8-flash-medium", 50, 5, 0))
    models = _models(hub)
    assert "claude-opus-5-5" in models, sorted(models)
    assert models["claude-opus-5-5"]["calls"] == 1
    assert models["claude-opus-5-5"]["out"] == 30
    assert "gemini-3.8-flash" in models, sorted(models)
    assert not [m for m in models if m.startswith("claude:") or "[" in m], sorted(models)


def test_review_spend_reaches_the_spend_totals(hub, state):
    _ledger(state, ("review:fix_2", "deepseek-flash", 1000000, 0, 0))
    assert hub.stats("day")["totals"]["all"] > 0


def _registry_spend(hub, model, tokens_in, tokens_out):
    """The expected spend from the registry's own display_price, by the same per-mtok arithmetic the hub uses."""
    rate = hub.modelreg.display_price(model)
    return (tokens_in * rate["input"] + tokens_out * rate["output"]) / 1e6


def test_a_claude_review_call_raises_the_spend_totals(hub, state):
    """No PRICES row names a claude model: the call is priced from its registry display_price, not a copied constant."""
    _ledger(state, ("review:fix_3", "claude:claude-opus-5-5", 1000000, 1000000, 0))
    expected = _registry_spend(hub, "claude-opus-5-5", 1000000, 1000000)
    assert hub.stats("day")["totals"]["all"] == pytest.approx(expected, abs=0.01)


def test_a_null_price_call_counts_zero_and_is_marked(hub, state, monkeypatch):
    """A registry entry with no display_price is priced 0.0, counted under unpriced, and never a KeyError."""
    monkeypatch.setattr(hub.modelreg, "display_price", lambda *a, **k: None)
    _ledger(state, ("review:fix_4", "claude:claude-opus-5-5", 1000000, 1000000, 0))
    stats = hub.stats("day")
    assert stats["totals"]["all"] == 0.0
    assert stats["totals"]["unpriced"] == 1
    assert _models(hub)["claude-opus-5-5"]["calls"] == 1


def test_a_partial_price_dict_counts_zero_and_is_marked(hub, state, monkeypatch):
    """A display_price with only an input rate is unpriced: no KeyError, the call counts 0.0 and is marked."""
    monkeypatch.setattr(hub.modelreg, "display_price", lambda *a, **k: {"input": 15.0})
    _ledger(state, ("review:fix_5", "claude:claude-opus-5-5", 1000000, 1000000, 0))
    stats = hub.stats("day")
    assert stats["totals"]["all"] == 0.0
    assert stats["totals"]["unpriced"] == 1


def test_no_ledger_leaves_the_stats_as_they_were(hub, state):
    assert hub.stats("day")["models"] == []
