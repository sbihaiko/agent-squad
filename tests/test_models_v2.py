"""The repository models.json is the v2 chain shape, and the readers the hub and the resolver run accept it (ADR-0147, #544)."""
import importlib.util
import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from squad import modelreg

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "contrib/squad-goal/scripts/models.json"
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
V1_KEYS = ("coding_models", "review_models", "vision_models", "router_models")
# A frozen v2 file for the reader and writer tests: the repository file follows the owner's chain choices, so a
# behavior test reads this shape and never the chains of the week.
SHAPE = ROOT / "tests/fixtures/models.v2.shape.json"


@pytest.fixture(scope="module")
def doc():
    return json.loads(SHAPE.read_text())


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_models_v2", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_repository_models_json_is_v2():
    doc = json.loads(MODELS.read_text())
    assert doc["schema_version"] == 2
    assert set(doc["chains"]) == {"coding", "review", "vision", "router", "editorial", "loop_defect"}
    assert not [k for k in V1_KEYS if k in doc]


def test_the_resolver_reads_every_repository_chain(doc):
    assert [e.raw for e in modelreg.chain(doc, "coding").entries][0] == "claude-deepseek-v4-pro"
    assert [e.raw for e in modelreg.chain(doc, "review").entries][0] == "gpt-5.6-terra"
    assert modelreg.chain(doc, "vision").entries
    assert [e.raw for e in modelreg.chain(doc, "router").entries] == ["gpt-6-luna", "claude-gpt-5.6-terra",
                                                                     "claude-sonnet-5-5"]


def test_the_panel_reads_the_repository_chains_under_the_v1_keys(hub, doc):
    view = hub.v1_view(doc)
    assert view["coding_models"][0] == "claude-deepseek-v4-pro"
    assert view["review_models"] == ["gpt-5.6-terra", "claude:claude-opus-5-5"]
    assert view["hours"]["review_models"] == {"claude:claude-opus-5-5": "19-07"}
    assert view["efforts"]["vision_models"] == {"gemini-3.8-flash-low": "low"}
    assert view["router_models"] == ["gpt-6-luna", "claude-gpt-5.6-terra", "claude-sonnet-5-5"]
    assert "schema_version" not in view and "chains" not in view


def test_a_v1_file_passes_through_the_panel_reader_unchanged(hub):
    v1 = {"coding_models": ["claude-sonnet-5-5"], "review_models": ["claude:claude-opus-5-5"]}
    assert hub.v1_view(v1) is v1


def test_the_panel_saves_over_a_v2_file(hub, monkeypatch):
    """SPEC 750: the repository file is v2, so the panel writes it. The v1 refusal is gone, not re-worded: a Save of a
    v2 file writes the chains through the repository's own writer."""
    tmp = Path(tempfile.mkdtemp()) / "models.json"
    shutil.copy(SHAPE, tmp)
    monkeypatch.setattr(hub, "MODELS", tmp)
    out = hub.models_set({**hub.models_get(), "coding_models": ["claude-sonnet-5-5"]})
    assert "err" not in out, out
    assert json.loads(tmp.read_text())["schema_version"] == 2


def test_a_save_writes_schema_2_and_keeps_all_four_chains(hub, monkeypatch):
    """The panel's Save writes `{"schema_version": 2, "chains": {...}}`: the four chains it draws, the editorial chain it
    does not draw, and every top-level key the file carries beside them."""
    tmp = Path(tempfile.mkdtemp()) / "models.json"
    shutil.copy(SHAPE, tmp)
    before = json.loads(tmp.read_text())
    monkeypatch.setattr(hub, "MODELS", tmp)
    out = hub.models_set({**hub.models_get(), "router_models": ["claude-opus-5-5"]})
    assert "err" not in out, out
    saved = json.loads(tmp.read_text())
    assert saved["schema_version"] == 2
    assert list(saved["chains"]) == ["coding", "review", "vision", "router", "editorial"]   # the undrawn chain stays
    assert [e["id"] for e in saved["chains"]["router"]] == ["claude-opus-5-5"]
    def rows(d):
        return [(e.id, e.raw, e.route, e.effort, e.hrs, e.on) for e in modelreg.chain(d, "review").entries]
    assert rows(saved) == rows(before), "a router Save changed what the review chain resolves to"
    assert saved["_doc"] == before["_doc"] and saved["stall_timeout_minutes"] == before["stall_timeout_minutes"]
    assert not [k for k in V1_KEYS if k in saved]


def _copy(hub, monkeypatch):
    """The repository file, copied into a temp folder the Save may write."""
    tmp = Path(tempfile.mkdtemp()) / "models.json"
    shutil.copy(SHAPE, tmp)
    monkeypatch.setattr(hub, "MODELS", tmp)
    return tmp


def test_a_section_the_save_omits_keeps_the_settings_the_file_holds(hub, monkeypatch):
    """A body that names the chains and no `efforts`/`hours`/`off` leaves every row's setting alone: absent means keep.
    The old writer read an omitted section as "clear", so a caller that posted one chain wiped the window, the effort
    and the switch of every row it did not name."""
    tmp = _copy(hub, monkeypatch)
    got = hub.models_get()
    out = hub.models_set({"version": got["version"], "coding_models": got["coding_models"]})
    assert "err" not in out, out
    saved = json.loads(tmp.read_text())
    assert [(e["id"], e.get("hrs"), e.get("effort"), e.get("on")) for e in saved["chains"]["coding"]] == [
        ("deepseek-v4-pro", None, "low", None), ("gpt-5.6-terra", None, None, None),
        ("claude-sonnet-5-5", "19-07", None, None), ("grok-4.7", None, None, None)]
    assert [(e["id"], e.get("effort")) for e in saved["chains"]["vision"] if e.get("effort")] == [("gemini-3.8-flash", "low")]


def test_a_section_that_names_one_chain_leaves_the_other_chains_as_they_are(hub, monkeypatch):
    """The hub's documented write contract posts one section, or one chain, at a time: the chain it names is applied
    whole, and the chains it does not name keep the settings the file holds."""
    tmp = _copy(hub, monkeypatch)
    out = hub.models_set({"version": hub.models_get()["version"], "hours": {"coding_models": {"claude-sonnet-5-5": "23-06"}}})
    assert "err" not in out, out
    assert out["hours"]["coding_models"]["claude-sonnet-5-5"] == "23-06"
    saved = json.loads(tmp.read_text())
    assert next(e for e in saved["chains"]["coding"] if e["id"] == "claude-sonnet-5-5")["hrs"] == "23-06"
    assert next(e for e in saved["chains"]["review"] if e["id"] == "claude-opus-5-5")["hrs"] == "19-07"
    assert next(e for e in saved["chains"]["vision"] if e["id"] == "gemini-3.8-flash")["effort"] == "low"


def test_two_spellings_of_one_model_write_one_row(hub, monkeypatch):
    """`gpt-5.6-terra` and the gateway's `claude-gpt-5.6-terra` are one model and one stored spelling. Both are posted
    when the panel draws the stored row and the Add box offers the bare id; the fold must leave one entry, or the file
    holds two identical rows and no later Save can pass the distinctness check."""
    tmp = _copy(hub, monkeypatch)
    got = hub.models_get()
    out = hub.models_set({"version": got["version"], "coding_models": [*got["coding_models"], "gpt-5.6-terra"]})
    assert "err" not in out, out
    written = [e.get("raw") or e["id"] for e in json.loads(tmp.read_text())["chains"]["coding"]]
    assert written.count("claude-gpt-5.6-terra") == 1, written
    assert "err" not in hub.models_set({**hub.models_get(), "review_models": hub.models_get()["review_models"]}), \
        "the next Save of any chain is refused by the duplicate this wrote"


def test_the_default_effort_drops_the_suffix_that_fixed_another(hub, monkeypatch):
    """The panel encodes medium as an absent entry, so selecting medium posts an empty map for the chain. The row's
    `-low` suffix fixes effort low, so the suffix must go with it; otherwise the owner cannot set that row to the
    default effort at all."""
    tmp = _copy(hub, monkeypatch)
    got = hub.models_get()
    clear = {k: {} for k in ("coding_models", "review_models", "vision_models", "router_models")}
    out = hub.models_set({"version": got["version"], "vision_models": got["vision_models"], "efforts": clear})
    assert "err" not in out, out
    row = next(e for e in json.loads(tmp.read_text())["chains"]["vision"] if e["id"] == "gemini-3.8-flash")
    assert "raw" not in row and "effort" not in row, row
    assert "gemini-3.8-flash" in out["vision_models"]


def test_a_save_over_a_v1_file_keeps_the_settings_of_a_model_no_chain_holds(hub, monkeypatch):
    """A v1 machine copy still holds `review_timeout_seconds_by_model`. The first panel Save migrates it, so a setting
    for a model that is not in a chain now must reach `_orphans` as `modelreg.migrate` keeps it, and the input file must
    stay as `.v1.bak`; dropping it leaves no record and no way back when the model returns."""
    tmp = Path(tempfile.mkdtemp()) / "models.json"
    tmp.write_text(json.dumps({"coding_models": ["claude-sonnet-5-5"], "review_models": ["gpt-6.1-sol"],
                               "vision_models": ["deepseek-v4-pro"],
                               "review_timeout_seconds_by_model": {"gpt-6.1-sol": 420, "parked-9": 99}}) + "\n")
    monkeypatch.setattr(hub, "MODELS", tmp)
    out = hub.models_set({"version": hub.models_get()["version"], "coding_models": ["claude-sonnet-5-5"]})
    assert "err" not in out, out
    saved = json.loads(tmp.read_text())
    assert saved["schema_version"] == 2
    assert saved["_orphans"]["review_timeout_seconds_by_model"] == {"parked-9": 99}
    assert next(e for e in saved["chains"]["review"] if e["id"] == "gpt-6.1-sol")["timeout_s"] == 420
    assert tmp.with_name("models.json.v1.bak").exists(), "the v1 file is gone with no copy"
