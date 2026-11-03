"""The hub's cache-read price, the run sub/api split and the model box routes (#791).

A cache read is priced at the registry's cache_read when a model names one, else at the one shared Anthropic ratio
(modelreg.CACHE_READ_RATIO, the token guard's cache-read ratio, #495). The sub/api split of a run reads the canonical
model id, as the stats' run filter does, so the pie and the tiles agree. A model with several spellings keeps every
route it was called by.
"""
import importlib.util
import json
import os
import re
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
    spec = importlib.util.spec_from_file_location("hub_cache_split", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def state(hub, tmp_path, monkeypatch):
    monkeypatch.setattr(hub, "STATE", tmp_path)
    monkeypatch.setattr(hub, "all_runs", lambda: [])
    return tmp_path


def _ledger(state, *rows):
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    (state / "token-usage.tsv").write_text("".join(f"{now}\t{c}\t{m}\t{i}\t{o}\t{cr}\n" for c, m, i, o, cr in rows))


def test_cache_read_uses_the_shared_ratio_when_the_registry_names_none(hub, state, monkeypatch):
    """No registry cache_read: the cache share is priced at modelreg.CACHE_READ_RATIO, the token guard's ratio."""
    monkeypatch.setattr(hub.modelreg, "display_price", lambda *a, **k: {"input": 10.0, "output": 0.0, "currency": "USD"})
    _ledger(state, ("review:c1", "gemini-3.8-flash-medium", 0, 0, 1000000))
    expected = 1000000 * 10.0 * hub.modelreg.CACHE_READ_RATIO / 1e6
    assert hub.modelreg.CACHE_READ_RATIO == pytest.approx(0.1)
    assert hub.ledger_calls()[0]["spend"] == pytest.approx(expected, abs=1e-4)


def test_cache_read_uses_the_registry_cache_rate_when_named(hub, state, monkeypatch):
    """A registry display_price that names cache_read is the rate; the shared ratio is only the fallback."""
    monkeypatch.setattr(hub.modelreg, "display_price",
                        lambda *a, **k: {"input": 10.0, "output": 0.0, "cache_read": 2.0, "currency": "USD"})
    _ledger(state, ("review:c2", "gemini-3.8-flash-medium", 0, 0, 1000000))
    assert hub.ledger_calls()[0]["spend"] == pytest.approx(2.0, abs=1e-4)


def test_ledger_docstring_no_longer_says_input_rate(hub):
    assert "input rate" not in " ".join((hub.ledger_calls.__doc__ or "").split())


def test_run_split_reads_the_canonical_id(hub, tmp_path, monkeypatch):
    """A raw spelling of a subscription model is billed to the subscription, as its canonical id is."""
    run = tmp_path / "run-20261001-100000"
    run.mkdir()
    ts = "2026-10-01T10:00:00"
    rows = [
        {"ts": ts, "event": "run_start"},
        {"ts": ts, "event": "outcome", "node": "n1", "model": "claude:claude-opus-5-5", "spend": 2.0,
         "tokens_in": 10, "tokens_out": 5, "cache_read": 0},
        {"ts": ts, "event": "verdict", "verdict": "pass", "exit_code": 0},
    ]
    (run / "record.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    monkeypatch.setattr(hub, "project_dirs", lambda: [tmp_path])
    monkeypatch.setattr(hub, "HIST", tmp_path / "hist.json")
    hub.hist.clear()
    r = hub.scan(run / "record.jsonl")
    assert r["sub"] == pytest.approx(2.0, abs=1e-4), r
    assert r["api"] == pytest.approx(0.0, abs=1e-4), r


def test_a_model_keeps_every_route_of_its_spellings(hub, state, monkeypatch):
    """Two spellings of one canonical id keep both routes in the box, not only the first one's."""
    mod = {"claude:claude-opus-5-5": {"in": 1, "out": 1, "cache": 0, "spend": 1.0, "calls": 1},
           "claude-opus-5-5": {"in": 1, "out": 1, "cache": 0, "spend": 1.0, "calls": 1}}
    run = {"t0": 1790000000.0, "t1": 1790000600.0, "dur": 10.0, "done": True, "spend": 2.0, "sub": 2.0, "api": 0.0,
           "mod": mod, "id": "run-x", "dir": "/tmp/run-x"}
    monkeypatch.setattr(hub, "all_runs", lambda: [run])
    models = {m["model"]: m for m in hub.stats("day")["models"]}
    box = models["claude-opus-5-5"]["box"]
    routes = {hub.model_box(raw)["route"] for raw in mod}
    assert set(box.get("routes") or []) == routes, box


def test_a_registry_cache_rate_prices_a_non_anthropic_review_call(hub, state):
    """#793: deepseek-flash names its own cache_read in the registry, so its cache reads are not priced at the 0.1 ratio."""
    reg = hub.modelreg.display_price("deepseek-flash") or {}
    assert reg.get("cache_read") == pytest.approx(0.006)
    _ledger(state, ("review:c4", "deepseek-flash", 0, 0, 1000000))
    assert hub.ledger_calls()[0]["spend"] == pytest.approx(0.006, abs=1e-6)


def test_a_finished_run_cached_under_v14_is_read_again(hub, tmp_path, monkeypatch):
    """#793, #796, #1229: the scan cache marker is v18, so a finished run cached under v14 or v15 is read again. A v18 entry
    is served while the record keeps the write time and size it names."""
    run = tmp_path / "run-20261001-100000"
    run.mkdir()
    (run / "record.jsonl").write_text(json.dumps({"ts": "2026-10-01T10:00:00", "event": "run_start"}) + "\n")
    monkeypatch.setattr(hub, "HIST", tmp_path / "hist.json")
    stale = {"v14": 1, "sentinel": True, "sub": 9.9}
    monkeypatch.setattr(hub, "hist", {str(run / "record.jsonl"): stale})
    got = hub.scan(run / "record.jsonl")
    assert got is not stale and "sentinel" not in (got or {}), got
    st = (run / "record.jsonl").stat()
    fresh = {"v18": 1, "rec": [st.st_mtime_ns, st.st_size], "sentinel": True}
    monkeypatch.setattr(hub, "hist", {str(run / "record.jsonl"): fresh})
    assert hub.scan(run / "record.jsonl") is fresh


def test_every_non_claude_model_names_its_cache_read():
    """#795: a gateway model names cache_read in its display_price, a number or an explicit null (unknown, priced at the shared ratio)."""
    reg = json.loads((ROOT / "config/models.registry.json").read_text())
    missing = [m["id"] for m in reg["models"] if m["provider"] != "claude" and "cache_read" not in (m.get("display_price") or {})]
    assert missing == [], f"each must name cache_read: a number or an explicit null (unknown, priced at the shared ratio): {missing}"


def test_a_null_cache_read_is_unknown_and_priced_at_the_ratio(hub, state, monkeypatch):
    """#795: an explicit null cache_read is unknown, not no discount: cache reads price at the shared ratio, as before."""
    monkeypatch.setattr(hub.modelreg, "display_price", lambda *a, **k: {"input": 10.0, "output": 0.0, "cache_read": None, "currency": "USD", "per": "mtok"})
    _ledger(state, ("review:c5", "gemini-3.8-flash", 0, 0, 1000000))
    assert hub.ledger_calls()[0]["spend"] == pytest.approx(10.0 * hub.CACHE_READ_RATIO)


def test_every_priced_model_names_its_hub_hit_rate(hub):
    """#795: a model with a hub PRICES row names a non-null cache_read in the registry, equal to that row's cache-hit rate."""
    reg = json.loads((ROOT / "config/models.registry.json").read_text())
    checked, bad = [], []
    for m in reg["models"]:
        rows = [(rx, p) for rx, p in hub.PRICES if rx.search(m["id"])]
        if not rows:
            continue
        hit = rows[0][1][0]
        checked.append(m["id"])
        got = (m.get("display_price") or {}).get("cache_read")
        if got != hit:
            bad.append((m["id"], got, hit))
    assert checked, "no registry model matches a PRICES row"
    assert bad == [], bad


def test_the_scan_marker_comment_lists_each_version_once_in_order(hub):
    """#795, #796, #1229: the comment beside the scan marker names v10 to v18, each once, in order."""
    line = next(s for s in HUB_PY.read_text().splitlines() if 'if h and "v18" in h and' in s)
    versions = re.findall(r"\bv(\d+)\b", line.split("#", 1)[1])
    assert versions == [str(n) for n in range(10, 19)], versions
