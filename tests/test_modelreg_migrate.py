"""SPEC 505 ticket 13: migrate a v1 models.json to the v2 chain shape.

The migration keeps today's behavior: it writes an explicit route on every entry (coding and router gateway,
review as review.sh runs it), folds the keyed settings into the entries, and keeps the notes and the global keys.
A setting keyed by a spelling that survives in no chain goes to `_orphans`, is reported, and is never applied.
The write is atomic, keeps a `.v1.bak`, prints a diff, and a second run is a no-op.
"""
import json
from pathlib import Path

import pytest

import squad.modelreg as mr

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "config" / "models.registry.json"
FIXTURES = ROOT / "tests" / "fixtures" / "modelreg"
REPO = FIXTURES / "repo.models.json"
LIVE = FIXTURES / "live.models.json"


@pytest.fixture(scope="module")
def reg():
    return mr.load(REGISTRY)


def _doc(path):
    return json.loads(path.read_text())


def _migrate(tmp_path, source, name="models.json", **kwargs):
    path = tmp_path / name
    path.write_text(source.read_text())
    return path, mr.migrate(path, **kwargs)


def _by_raw(chain):
    return {e.get("raw") or e["id"]: e for e in chain}


# --- compatible: a chain offers and keeps only compatible models ------------------------------------------

def test_compatible_coding_and_router_need_a_gateway_route_whose_model_takes_tools(reg):
    assert mr.compatible("claude-opus-5-5", "coding", reg=reg) == (True, None)
    assert mr.compatible("claude-opus-5-5", "router", reg=reg) == (True, None)
    ok, reason = mr.compatible("gemini-3.8-flash", "coding", reg=reg)
    assert ok is False and "gateway" in reason
    ok, reason = mr.compatible("deepseek-flash", "coding", reg=reg)
    assert ok is False and "tools" in reason


def test_compatible_review_takes_any_route_and_vision_needs_vision(reg):
    for mid in ("claude-opus-5-5", "grok-4.7", "deepseek-flash"):
        assert mr.compatible(mid, "review", reg=reg) == (True, None)
    assert mr.compatible("claude-opus-5-5", "vision", reg=reg) == (True, None)
    ok, reason = mr.compatible("deepseek-v4-flash", "vision", reg=reg)
    assert ok is False and "vision" in reason


def test_compatible_refuses_a_retired_or_an_unknown_id(reg):
    ok, reason = mr.compatible("grok-4.5", "review", reg=reg)
    assert ok is False and reason == "retired"
    ok, reason = mr.compatible("no-such-model", "review", reg=reg)
    assert ok is False and "unknown" in reason


# --- migration of the repository shape --------------------------------------------------------------------

def test_migrate_writes_an_explicit_route_and_folds_the_settings(tmp_path, reg):
    path, result = _migrate(tmp_path, REPO, reg=reg)
    assert result["changed"] is True
    doc = _doc(path)
    assert doc["schema_version"] == 2
    assert set(doc["chains"]) == {"coding", "review", "vision", "router"}

    coding = _by_raw(doc["chains"]["coding"])
    assert list(coding) == ["claude-gpt-5.6-terra", "claude-sonnet-5-5"]
    assert coding["claude-gpt-5.6-terra"]["route"] == "gateway"
    assert coding["claude-gpt-5.6-terra"]["id"] == "gpt-5.6-terra"
    assert coding["claude-sonnet-5-5"]["hrs"] == "19-07"

    review = _by_raw(doc["chains"]["review"])
    assert review["grok-4.7"]["route"] == "cli"
    notools = review["claude:claude-deepseek-flash"]
    assert (notools["route"], notools["timeout_s"], notools["no_tools"]) == ("direct", 120, True)
    assert review["gemini-3.8-flash-high"]["route"] == "cli"
    assert review["gemini-3.8-flash-high"]["effort"] == "high"
    assert review["claude:claude-opus-5-5"]["hrs"] == "19-07"

    vision = _by_raw(doc["chains"]["vision"])
    assert list(vision) == ["gpt-5.6-terra", "gemini-3.8-flash-low"]
    assert vision["gemini-3.8-flash-low"]["effort"] == "low"
    assert doc["chains"]["router"] == []


def test_migrate_keeps_the_notes_and_the_global_keys(tmp_path, reg):
    path, _ = _migrate(tmp_path, REPO, reg=reg)
    doc = _doc(path)
    assert doc["_doc"].startswith("made up repo-shape")
    assert doc["review_order"] == "config"
    assert doc["review_timeout_seconds"] == 360
    assert doc["review_max_lines"] == 60
    assert doc["anthropic_models"] == []
    for folded in ("coding_models", "review_models", "vision_models",
                   "hours", "efforts", "off", "review_timeout_seconds_by_model", "review_no_tools"):
        assert folded not in doc, folded


def test_migrate_drops_every_incompatible_entry_and_names_the_reason(tmp_path, reg):
    _, result = _migrate(tmp_path, REPO, reg=reg)
    dropped = {(d["chain"], d["raw"]): d["reason"] for d in result["dropped"]}
    assert "gateway" in dropped[("coding", "gemini-3.8-flash-high")]
    assert "tools" in dropped[("coding", "claude-deepseek-flash[1m]")]
    assert dropped[("vision", "deepseek-v4-flash")] == "no vision capability"
    assert dropped[("vision", "grok-4.5")] == "retired"


def test_the_migrated_file_reads_through_chain(tmp_path, reg):
    path, _ = _migrate(tmp_path, REPO, reg=reg)
    review = mr.chain(path, "review", reg=reg)
    assert review.mixed is False
    e = next(e for e in review.entries if e.raw == "claude:claude-deepseek-flash")
    assert (e.id, e.route, e.effort, e.timeout_s, e.no_tools) == ("deepseek-flash", "direct", None, 120, True)
    coding = mr.chain(path, "coding", reg=reg)
    assert [e.id for e in coding.entries] == ["gpt-5.6-terra", "claude-sonnet-5-5"]


# --- the live shape and the orphan report -----------------------------------------------------------------

def test_migrate_folds_off_by_chain_on_the_live_shape(tmp_path, reg):
    path, _ = _migrate(tmp_path, LIVE, reg=reg)
    doc = _doc(path)
    assert doc["_models_note"] == "made up note on the live shape."
    assert doc["review_order"] == "config"
    review = _by_raw(doc["chains"]["review"])
    assert review["gemini-3.8-flash-medium"]["on"] is False
    assert list(review) == ["claude:claude-opus-5-5", "gemini-3.8-flash-medium"]


def test_migrate_drops_a_retired_entry_on_the_live_shape(tmp_path, reg):
    _, result = _migrate(tmp_path, LIVE, reg=reg)
    dropped = {(d["chain"], d["raw"]): d["reason"] for d in result["dropped"]}
    assert dropped[("review", "grok-4.6")] == "retired"
    assert dropped[("vision", "grok-4.5")] == "retired"
    assert dropped[("coding", "claude-deepseek-flash[1m]")] == "gateway model takes no tools"


def test_a_setting_keyed_by_a_spelling_in_no_surviving_chain_is_an_orphan_and_is_never_applied(tmp_path, reg):
    path, result = _migrate(tmp_path, LIVE, reg=reg)
    doc = _doc(path)
    orphans = doc["_orphans"]
    assert orphans["review_timeout_seconds_by_model"]["claude:claude-deepseek-flash"] == 120
    assert orphans["review_no_tools"] == ["claude:claude-deepseek-flash"]
    assert orphans["hours"]["coding_models"]["claude-deepseek-flash[1m]"] == "07-19"
    assert len(result["orphans"]["review_timeout_seconds_by_model"]) == 5

    for chain in doc["chains"].values():
        for entry in chain:
            assert entry.get("timeout_s") != 120
            assert entry.get("no_tools") is not True
            assert entry.get("hrs") != "07-19"
    assert any("orphan review_timeout_seconds_by_model.claude:claude-deepseek-flash" in line
               for line in mr.migrate_lines(result))


def test_orphan_report_lines_name_each_setting(tmp_path, reg):
    _, result = _migrate(tmp_path, LIVE, reg=reg)
    lines = mr.migrate_lines(result)
    assert "orphan hours.coding_models.claude-deepseek-flash[1m]: not in the coding chain" in lines
    assert "orphan review_no_tools.claude:claude-deepseek-flash: not in the review chain" in lines


# --- write behavior ---------------------------------------------------------------------------------------

def test_a_second_run_is_a_no_op(tmp_path, reg):
    path, first = _migrate(tmp_path, REPO, reg=reg)
    before = path.read_bytes()
    second = mr.migrate(path, reg=reg)
    assert first["changed"] is True
    assert second["changed"] is False
    assert path.read_bytes() == before
    assert second["diff"] == ""


def test_migrate_keeps_a_v1_backup_and_an_atomic_write(tmp_path, reg):
    path, _ = _migrate(tmp_path, REPO, reg=reg)
    backup = tmp_path / "models.json.v1.bak"
    assert backup.read_text() == REPO.read_text()
    assert not (tmp_path / "models.json.tmp").exists()


def test_dry_run_prints_the_same_lines_and_writes_nothing(tmp_path, reg):
    path, dried = _migrate(tmp_path, LIVE, reg=reg, dry_run=True)
    assert path.read_text() == LIVE.read_text()
    assert not (tmp_path / "models.json.v1.bak").exists()
    assert dried["diff"]
    _, wet = _migrate(tmp_path, LIVE, reg=reg)
    assert mr.migrate_lines(dried) == mr.migrate_lines(wet)


def test_migrate_refuses_a_mixed_file(tmp_path, reg):
    mixed = {**json.loads(LIVE.read_text()), "chains": {"coding": []}}
    path = tmp_path / "models.json"
    path.write_text(json.dumps(mixed))
    with pytest.raises(mr.RegistryError, match="mixed"):
        mr.migrate(path, reg=reg)
    assert json.loads(path.read_text()) == mixed
    assert not (tmp_path / "models.json.v1.bak").exists()


def test_cli_migrate_prints_the_diff_and_the_dropped_lines(tmp_path, capsys):
    path = tmp_path / "models.json"
    path.write_text(REPO.read_text())
    rc = mr.main(["migrate", str(path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "dropped coding gemini-3.8-flash-high" in out
    assert "--- " in out and "+++ " in out
    assert json.loads(path.read_text())["schema_version"] == 2


def test_cli_migrate_dry_run_writes_nothing(tmp_path, capsys):
    path = tmp_path / "models.json"
    path.write_text(REPO.read_text())
    rc = mr.main(["migrate", "--dry-run", str(path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "dropped coding gemini-3.8-flash-high" in out
    assert path.read_text() == REPO.read_text()
