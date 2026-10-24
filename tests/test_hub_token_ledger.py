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


def test_a_claude_review_call_raises_the_spend_totals(hub, state):
    """No PRICES row names a claude model: the call is priced from its registry display_price ($15 in, $75 out per mtok)."""
    _ledger(state, ("review:fix_3", "claude:claude-opus-5-5", 1000000, 1000000, 0))
    assert hub.stats("day")["totals"]["all"] == pytest.approx(90.0, abs=0.01)


def test_no_ledger_leaves_the_stats_as_they_were(hub, state):
    assert hub.stats("day")["models"] == []
