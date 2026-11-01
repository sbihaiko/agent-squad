"""SPEC 889 #920: a model is switched off once for every chain, from the models table or a sidebar chip.

The switch is one key in models.json, `model_off`, keyed by the model's normalized id (squad/modelreg.py reads it, and
the one eligibility function skips the model in every chain). The hub writes that key through one route, POST
/api/models, and it writes no chain-level `off` any more. Each flip records one line in the models switch ledger.

The page is one string in `hub.py`, so the page guards read it there, as the neighbours do.
"""
import importlib.util
import json
import os
import re
import tempfile
from pathlib import Path

import pytest

from squad import modelreg

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib" / "squad-goal" / "scripts" / "hub.py"

MODELS_DOC = {
    "coding_models": ["claude-sonnet-5-5", "claude-deepseek-v4-flash", "gpt-5.6-terra"],
    "review_models": ["gemini-3.8-flash-high", "claude:claude-opus-5-5", "grok-4.5"],
    "vision_models": ["grok-4.5", "claude-sonnet-5-5"],
    "efforts": {"coding_models": {"gpt-5.6-terra": "low"}},
    "hours": {"coding_models": {"claude-sonnet-5-5": "19-07"}},
}


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_model_level_switch", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def models(hub, tmp_path, monkeypatch):
    """The panel's own file and state folder: a Save writes there and nowhere else."""
    path = tmp_path / "models.json"
    path.write_text(json.dumps(MODELS_DOC, indent=2) + "\n")
    monkeypatch.setattr(hub, "MODELS", path)
    monkeypatch.setattr(hub, "STATE", tmp_path)
    return path


@pytest.fixture(scope="module")
def page():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_model_level_page", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.PAGE


def _flip(hub, model, off):
    """One sidebar click, as the page posts it: the version it read, and the one key it flips."""
    return hub.models_set({"version": hub.models_get()["version"], "model_off": {model: off}})


def _ledger(hub, tmp_path):
    path = tmp_path / hub.MODELS_LEDGER
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()] if path.exists() else []


# --- the models table reads the switch --------------------------------------------------------------------------

def test_the_panel_reads_a_model_switched_off_at_model_level(hub, models):
    models.write_text(json.dumps({**MODELS_DOC, "model_off": {"claude-sonnet-5-5": True}}))
    got = hub.models_get()
    assert got["model_off"] == {"claude-sonnet-5-5": True}
    row = next(r for r in got["models"] if r["id"] == "claude-sonnet-5-5")
    assert row["off"] is True and row["state"] == "off (model)"


def test_the_models_table_has_one_row_per_model_with_its_subscription(hub, models):
    got = hub.models_get()
    ids = [r["id"] for r in got["models"]]
    assert len(ids) == len(set(ids)), "one row per model, never one per chain"
    assert "claude-sonnet-5-5" in ids and "grok-4.5" in ids
    assert all("sub" in r and "state" in r for r in got["models"])


def test_the_table_holds_no_hours(hub, models):
    """Hours live only in the chain rows (#913): the table row carries no window."""
    assert all("hrs" not in r and "hours" not in r for r in hub.models_get()["models"])


# --- one write route, one key, no chain-level write --------------------------------------------------------------

def test_a_model_level_switch_is_written_at_the_top_level_key_and_chains_stay(hub, models):
    before = hub.models_get()
    out = _flip(hub, "claude-sonnet-5-5", True)
    saved = json.loads(models.read_text())
    key = modelreg.normalize("claude-sonnet-5-5").id
    assert saved["model_off"] == {key: True}
    assert out["coding_models"] == before["coding_models"], "the switch never edits a chain"
    assert out["model_off"] == {"claude-sonnet-5-5": True}


def test_a_flip_back_on_removes_the_key(hub, models):
    _flip(hub, "claude-sonnet-5-5", True)
    out = _flip(hub, "claude-sonnet-5-5", False)
    assert "claude-sonnet-5-5" not in out["model_off"]
    assert json.loads(models.read_text()).get("model_off", {}) == {}


def test_the_hub_writes_no_chain_level_off_even_when_an_old_tab_posts_one(hub, models):
    """A page from before this change posts `off` per chain. The hub keeps the chains and writes no `on: false`."""
    hub.models_set({**hub.models_get(), "off": {"coding_models": {"claude-sonnet-5-5": True}}})
    coding = json.loads(models.read_text())["chains"]["coding"]
    assert all(e.get("on") is not False for e in coding)


def test_a_chain_level_off_already_in_the_file_is_honored_and_shown(hub, models):
    models.write_text(json.dumps({**MODELS_DOC, "off": {"review_models": {"grok-4.5": True}}}))
    got = hub.models_get()
    assert got["off"]["review_models"] == {"grok-4.5": True}
    assert "off (chain)" in json.dumps(got)


# --- the add lists never offer an off model -----------------------------------------------------------------------

def test_an_add_list_does_not_offer_a_model_switched_off(hub, models):
    before = hub.models_get()["suggest"]["coding_models"]
    assert before, "the add list is empty, so the guard proves nothing"
    pick = before[0]["id"]
    _flip(hub, pick, True)
    after = [s["id"] for s in hub.models_get()["suggest"]["coding_models"]]
    assert pick not in after
    assert all(pick not in [s["id"] for s in hub.models_get()["suggest"][k]] for k in ("review_models", "vision_models"))


# --- the audit line -------------------------------------------------------------------------------------------------

def test_each_flip_records_one_audit_line_naming_model_state_who_and_when(hub, models, tmp_path):
    _flip(hub, "claude-sonnet-5-5", True)
    _flip(hub, "claude-sonnet-5-5", False)
    lines = _ledger(hub, tmp_path)
    assert len(lines) == 2, "one line per flip, no line for a read"
    first = lines[0]
    assert first["model"] == "claude-sonnet-5-5" and first["state"] == "off"
    assert first["who"] and first["at"]
    assert lines[1]["state"] == "on"


def test_a_save_that_changes_nothing_records_no_line(hub, models, tmp_path):
    hub.models_set(hub.models_get())
    assert _ledger(hub, tmp_path) == []


# --- the served page -------------------------------------------------------------------------------------------------

def test_the_page_draws_the_models_table_with_a_switch_that_states_its_value(page):
    assert "Models table" in page or ">Models<" in page
    assert re.search(r'<button[^>]*data-mt="[^"]*"[^>]*aria-pressed="\$\{', page), \
        "the table switch is a button with aria-pressed, drawn per model"


def test_the_page_draws_no_chip_that_writes_at_once(page):
    """Re-anchored (#913): a switch writes only through the page Save, after its diff; no chip writes at once."""
    assert "data-mo=" not in page


def test_the_switch_is_a_44_px_target_on_the_phone_with_a_focus_ring(page):
    """Re-anchored (#913 review): the chip is gone; the models table switch (.mtg) is the one control left."""
    assert ".mch" not in page, "dead chip CSS is back"
    assert re.search(r"@media \(max-width:600px\)\{\.mtg\{[^}]*min-height:44px", page), "the switch has no 44 px target on the phone"
    assert re.search(r"\.mtg:focus-visible\{[^}]*outline", page), "the switch has no visible focus ring"


def test_the_chain_rows_no_longer_carry_a_chain_level_switch_button(page):
    assert 'data-sw="' not in page, "the chain-level switch is gone; the switch is per model"


def test_the_diff_names_each_row_and_the_model_level_change(page):
    assert "always" in page and "outside hours" in page
    assert "switched off" in page or "off (model)" in page


def test_the_hub_predicate_skips_a_model_switched_off_in_every_chain(hub, models):
    """One predicate for the hub too: a model switched off at model level may not run in any chain it sits in."""
    d = {**MODELS_DOC, "model_off": {"claude-sonnet-5-5": True}}
    assert hub.may_run(d, "coding_models", "claude-sonnet-5-5") is False
    assert hub.may_run(d, "vision_models", "claude-sonnet-5-5") is False
    assert hub.may_run(d, "coding_models", "claude-deepseek-v4-flash") is True
