"""SPEC 505 ticket 7: the review pass picks each reviewer's route from the registry, through the one resolver.

A reviewer's runner (CLI, gateway or direct API) and its tools come from the registry entry, never from an id prefix
or from a `review_no_tools` list. The repo review chain equals the live chain.
"""
import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "squad"))
sys.path.insert(0, str(ROOT / "tests"))

import modelreg  # noqa: E402
from test_model_resolver_seam import LEGACY, scan_file  # noqa: E402

REVIEW = ROOT / "contrib/squad-goal/scripts/review.sh"
MODELS = ROOT / "contrib/squad-goal/scripts/models.json"
LIVE_SHAPE = ROOT / "tests/fixtures/modelreg/live.models.json"


@pytest.fixture(scope="module")
def reg():
    return modelreg.load(ROOT / "config/models.registry.json")


@pytest.mark.parametrize("raw,route,runner,wire", [
    ("claude:claude-opus-5-5", "cli", "claude", "claude-opus-5-5"),
    ("gemini-3.8-flash-medium", "cli", "agy", "gemini-3.8-flash-medium"),
    ("grok-4.6", "cli", "grok", "grok-4.6"),
    ("gpt-5.6-luna", "cli", "codex", "gpt-5.6-luna"),
    ("claude:claude-deepseek-flash", "direct", "", "claude-deepseek-flash"),
    ("claude:claude-deepseek-flash[1m]", "direct", "", "claude-deepseek-flash[1m]"),
])
def test_a_reviewer_route_comes_from_its_registry_entry(reg, raw, route, runner, wire):
    got = modelreg.review_route(raw, reg)
    assert (got.route, got.runner, got.wire) == (route, runner, wire)


def test_a_reviewer_with_no_tools_capability_runs_with_no_tools(reg):
    assert modelreg.review_route("claude:claude-deepseek-flash", reg).tools is False
    assert modelreg.review_route("claude:claude-opus-5-5", reg).tools is True


def test_tools_follow_the_registry_capability_not_a_list(tmp_path):
    doc = json.loads((ROOT / "config/models.registry.json").read_text())
    models = copy.deepcopy(doc["models"])
    for m in models:
        if m["id"] == "claude-opus-5-5":
            m["capabilities"]["tools"] = False
    doc["models"] = models
    p = tmp_path / "registry.json"
    p.write_text(json.dumps(doc))
    got = modelreg.review_route("claude:claude-opus-5-5", modelreg.load(p))
    assert (got.route, got.tools) == ("direct", False)


def test_a_spelling_the_registry_does_not_know_keeps_its_old_runner(reg):
    assert modelreg.review_route("claude:x", reg).runner == "claude"
    assert modelreg.review_route("claude:x", reg).wire == "x"
    assert modelreg.review_route("some-model", reg).runner == "codex"


def test_the_cli_prints_the_route_as_one_tab_row():
    import subprocess
    out = subprocess.run([sys.executable, str(ROOT / "squad/modelreg.py"), "review-route", "grok-4.6"],
                         capture_output=True, text=True, check=True).stdout
    assert out == "cli\tgrok\tgrok-4.6\t1\n"


def test_the_cli_marks_an_empty_runner_with_a_dash():
    """A shell `read` collapses an empty tab field, so the row never holds one."""
    import subprocess
    out = subprocess.run([sys.executable, str(ROOT / "squad/modelreg.py"), "review-route", "claude:claude-deepseek-flash"],
                         capture_output=True, text=True, check=True).stdout
    assert out == "direct\t-\tclaude-deepseek-flash\t0\n"


def test_review_sh_parses_no_model_prefix_and_is_not_legacy():
    assert "contrib/squad-goal/scripts/review.sh" not in LEGACY
    assert scan_file(REVIEW) == []


def test_review_sh_reads_no_review_no_tools_list():
    assert "review_no_tools" not in REVIEW.read_text()
    assert "review_no_tools" not in json.loads(MODELS.read_text())


def test_the_repo_review_chain_is_the_live_chain(reg):
    live = json.loads(LIVE_SHAPE.read_text())["review_models"]
    repo = json.loads(MODELS.read_text())["review_models"]
    assert repo == live
    ids = [e.id for e in modelreg.chain(MODELS, "review", reg).entries]
    assert ids == ["claude-opus-5-5", "gemini-3.8-flash", "grok-4.6"]
    assert [e.effort for e in modelreg.chain(MODELS, "review", reg).entries] == [None, "medium", None]
