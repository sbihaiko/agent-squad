"""SPEC 750: the router's models are one chain of models.json, and a Save never touches the graph.

The panel once wrote the queue into the run graph's dispatch `allow.models` and restamped the record beside it (#95,
#225). The envelope now names kinds, the router agent runs on `chains.router`, and a child's model is its kind's chain,
so the graph is the author's and the panel edits models.json only.

The hub is imported with a checkout of its own, so the Save runs for real against a graph, a record and a models.json
in a temp folder. The gateway is pinned to a list: the guard runs against the module, never the live hub.
"""
import importlib.util
import json
import os
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib" / "squad-goal" / "scripts" / "hub.py"

# A part of what the gateway's `/v1/models` served on 2026-10-13: every id spelled `claude-...`.
GATEWAY = ["claude-gpt-5.6-terra", "claude-gpt-5.6-sol", "claude-gpt-6-astra", "claude-deepseek-v4-flash[1m]",
           "claude-opus-5-5"]
# Ids the box offered for other CLIs (codex, agy, grok) that the gateway does not serve.
OTHER_CLI = ["gpt-6.1-sol", "gemini-3.8-flash-high", "grok-4.6"]


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_router_queue_models", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def checkout(hub, monkeypatch, tmp_path):
    """A checkout of the hub's own: the run graph and its record, which no Save may touch, and models.json."""
    d = tmp_path / "docs" / "design"
    d.mkdir(parents=True)
    graph = d / "router.graph.json"
    graph.write_text(json.dumps({"schema_version": 1, "name": "router-dynamic", "nodes": [
        {"name": "main", "kind": "loop", "children": ["route"]},
        {"name": "route", "kind": "dispatch", "allow": {"kinds": ["coding", "review", "vision"]}}]},
        indent=2) + "\n")
    rec = d / "router.record.jsonl"
    rec.write_text('{"graph_hash": "x"}\n')
    s = tmp_path / "scripts"
    s.mkdir()
    (s / "stamp_router_record.py").write_text("import sys; sys.exit(0)\n")
    models = tmp_path / "models.json"
    models.write_text(json.dumps({"schema_version": 2, "chains": {
        "coding": [{"id": "gpt-5.6-terra", "raw": "claude-gpt-5.6-terra", "route": "gateway"}],
        "review": [{"id": "gpt-6.1-sol", "route": "cli"}],
        "vision": [{"id": "grok-4.6", "route": "cli"}],
        "router": [{"id": "claude-opus-5-5", "route": "gateway"}]}}, indent=2) + "\n")
    monkeypatch.setattr(hub, "CHECKOUT", str(tmp_path))
    monkeypatch.setattr(hub, "MODELS", models)
    monkeypatch.setattr(hub, "gateway_models", lambda ttl=120: list(GATEWAY))
    monkeypatch.setattr(hub.pathlib.Path, "home", classmethod(lambda cls: tmp_path))
    return graph, rec, models


def _chain(models, canon="router"):
    return json.loads(models.read_text())["chains"][canon]


def test_a_router_save_writes_models_json_and_never_the_graph(hub, checkout):
    """SPEC 750: the router chain is one chain of models.json; the graph and its record are the author's."""
    graph, rec, models = checkout
    g, r = graph.read_bytes(), rec.read_bytes()
    out = hub.models_set({**hub.models_get(), "router_models": ["claude-opus-5-5", "claude-gpt-5.6-sol"]})
    assert "err" not in out, out
    assert [e["id"] for e in _chain(models)] == ["claude-opus-5-5", "gpt-5.6-sol"]
    assert out["router_models"] == ["claude-opus-5-5", "claude-gpt-5.6-sol"]
    assert (graph.read_bytes(), rec.read_bytes()) == (g, r), "a Save touched the graph or its record"


def test_a_new_row_takes_the_spelling_the_gateway_serves(hub, checkout):
    """A bare id is the codex CLI's spelling: the gateway names the `claude-` alias. The panel shows the id it typed, the
    file keeps the wire spelling, as `chain_id` and the registry rule it uses already did for the role chains."""
    _, _, models = checkout
    out = hub.models_set({**hub.models_get(), "router_models": ["claude-opus-5-5", "gpt-5.6-sol"]})
    assert "err" not in out, out
    assert _chain(models)[1] == {"id": "gpt-5.6-sol", "raw": "claude-gpt-5.6-sol", "route": "gateway"}


def test_an_entry_the_router_cannot_run_is_stored_and_never_drawn(hub, checkout):
    """The chain keeps the owner's row; the panel draws only the entries the router can run (ADR-0147). A gemini id has
    no gateway route, so the router cannot ask a child for it."""
    _, _, models = checkout
    out = hub.models_set({**hub.models_get(), "router_models": ["claude-opus-5-5", "gemini-3.8-flash"]})
    assert "err" not in out, out
    assert [e["id"] for e in _chain(models)] == ["claude-opus-5-5", "gemini-3.8-flash"]
    assert out["router_models"] == ["claude-opus-5-5"]


def test_a_role_only_save_keeps_a_hidden_router_entry(hub, checkout):
    """The panel shows only the entries its chain can run, so a hidden entry is absent from what it posts back. A Save
    that edits a role chain must not read that as an edit of the router chain and delete the entry."""
    _, _, models = checkout
    models.write_text(json.dumps({"schema_version": 2, "chains": {
        "coding": [{"id": "gpt-5.6-terra", "raw": "claude-gpt-5.6-terra", "route": "gateway"}],
        "review": [{"id": "gpt-6.1-sol", "route": "cli"}],
        "vision": [{"id": "grok-4.6", "route": "cli"}],
        "router": [{"id": "claude-opus-5-5", "route": "gateway"}, {"id": "gemini-3.8-flash", "route": "cli"}]}},
        indent=2) + "\n")
    got = hub.models_get()
    assert got["router_models"] == ["claude-opus-5-5"]
    out = hub.models_set({**got, "review_models": ["gpt-6.1-sol"]})
    assert "err" not in out, out
    assert [e["id"] for e in _chain(models)] == ["claude-opus-5-5", "gemini-3.8-flash"]


def test_a_coding_model_is_never_appended_to_the_router_chain(hub, checkout):
    """A coding child takes its model from the coding chain; the envelope names no list (#690, retired by SPEC 750)."""
    _, _, models = checkout
    before = _chain(models)
    out = hub.models_set({**hub.models_get(), "coding_models": ["gpt-5.6-terra", "gpt-5.6-sol"]})
    assert "err" not in out, out
    assert _chain(models) == before


def test_a_role_save_keeps_the_review_settings_the_panel_does_not_draw(hub, checkout):
    """`timeout_s` and `no_tools` are review's; the panel draws none of them, so a Save carries them over per entry."""
    _, _, models = checkout
    models.write_text(json.dumps({"schema_version": 2, "chains": {
        "coding": [{"id": "gpt-5.6-terra", "raw": "claude-gpt-5.6-terra", "route": "gateway"}],
        "review": [{"id": "gpt-6.1-sol", "route": "cli", "timeout_s": 420, "no_tools": True}],
        "vision": [{"id": "grok-4.6", "route": "cli"}],
        "router": [{"id": "claude-opus-5-5", "route": "gateway"}]}}, indent=2) + "\n")
    out = hub.models_set({**hub.models_get(), "review_models": ["gpt-6.1-sol"]})
    assert "err" not in out, out
    assert _chain(models, "review") == [{"id": "gpt-6.1-sol", "route": "cli", "timeout_s": 420, "no_tools": True}]


def test_a_bad_setting_names_the_row_and_the_rule(hub, checkout):
    """The Save's refusal has to say what to send: the row that is wrong, and the shape the setting takes. A bare
    "bad value" leaves a hand POST or a script with nothing to fix, and the file is left as it was."""
    _, _, models = checkout
    before = models.read_text()
    out = hub.models_set({**hub.models_get(), "hours": {"coding_models": {"gpt-5.6-terra": "25-99"}}})
    assert out.get("err"), out
    assert "gpt-5.6-terra" in out["err"] and "HH-HH" in out["err"], out["err"]
    out = hub.models_set({**hub.models_get(), "efforts": {"coding_models": {"gpt-5.6-terra": "extreme"}}})
    assert "gpt-5.6-terra" in out.get("err", "") and "low|medium|high|max" in out["err"], out
    assert models.read_text() == before, "a refused Save wrote the file"


def test_the_router_add_box_offers_gateway_ids_and_the_role_box_keeps_the_others(hub, checkout):
    got = hub.models_get()
    # The box also offers the registry's live Anthropic ids, which the gateway passes through (#687).
    reg = hub.modelreg.default_registry()
    anthropic = {m for m, e in reg.entries.items() if hub.ANTHROPIC_ID.match(m) and not e.retired}
    assert sorted(got["router_known"]) == sorted(set(GATEWAY) | anthropic)
    assert [m for m in OTHER_CLI if m not in got["known"]] == []
    router_suggest = {s["id"] for s in got["suggest"]["router_models"]}
    assert "claude-opus-5-5" in router_suggest
    assert "gpt-6.1-sol" not in router_suggest, "the router box offers a codex-only id"
    src = HUB_PY.read_text()
    # The page's add control offers each chain its own suggest list; the server fills the router's from router_known (#913).
    assert 'data-add="${k}"' in src and "(mdl.suggest||{})[k]" in src, "the Router card has no add box of its own"


def test_the_router_add_box_offers_the_registry_live_anthropic_ids(hub, checkout, monkeypatch):
    """The owner could not add Opus 5.5 to the router chain (#687): the gateway's `/v1/models` lists no Anthropic id,
    so the box offered none, though the server passes one through. A retired id stays out."""
    served = [m for m in GATEWAY if not hub.ANTHROPIC_ID.match(m)]
    monkeypatch.setattr(hub, "gateway_models", lambda ttl=120: list(served))
    got = hub.router_known()
    assert {"claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"} <= set(got)
    reg = hub.modelreg.default_registry()
    assert [m for m in got if m in reg.entries and reg.entries[m].retired] == []
    assert [m for m in got if hub.ANTHROPIC_ID.match(m) and m not in reg.entries] == []
