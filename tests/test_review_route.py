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


def _no_tools_registry(path):
    """The shipped registry with `deepseek-flash` marked tools:false. No shipped entry says no tools since #683, but
    the no-tools review path stays, so a test registry keeps one entry on it."""
    doc = json.loads((ROOT / "config/models.registry.json").read_text())
    for m in doc["models"]:
        if m["id"] == "deepseek-flash":
            m["capabilities"]["tools"] = False
    path.write_text(json.dumps(doc))
    return path


@pytest.mark.parametrize("raw,route,runner,wire", [
    ("claude:claude-opus-5-5", "cli", "claude", "claude-opus-5-5"),
    ("gemini-3.8-flash-medium", "cli", "agy", "gemini-3.8-flash-medium"),
    ("grok-4.6", "cli", "grok", "grok-4.6"),
    ("gpt-5.6-luna", "cli", "codex", "gpt-5.6-luna"),
    ("claude:claude-deepseek-flash", "direct", "", "claude-deepseek-flash"),   # review_tools: false
    ("claude:claude-deepseek-flash[1m]", "direct", "", "claude-deepseek-flash[1m]"),
])
def test_a_reviewer_route_comes_from_its_registry_entry(reg, raw, route, runner, wire):
    got = modelreg.review_route(raw, reg)
    assert (got.route, got.runner, got.wire) == (route, runner, wire)


def test_a_reviewer_with_no_tools_capability_runs_with_no_tools(reg, tmp_path):
    nt = modelreg.load(_no_tools_registry(tmp_path / "registry.json"))
    got = modelreg.review_route("claude:claude-deepseek-flash", nt)
    assert (got.route, got.runner, got.wire, got.tools) == ("direct", "", "claude-deepseek-flash", False)
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


def test_a_coding_capability_never_decides_the_review_lane(reg):
    """#683 gave DeepSeek tools so a coding chain keeps it. The review pass read that same capability and sent every
    review through `claude -p`: 17 s a review on the direct call, 335 s through the CLI, most killed at the deadline.
    A reviewer's lane comes from its review capability, and the coding chain keeps its tools."""
    assert (modelreg.review_route("claude:claude-deepseek-flash", reg).route,
            modelreg.review_route("claude:claude-deepseek-flash", reg).tools) == ("direct", False)
    assert (modelreg.review_route("claude:claude-opus-5-5", reg).route,
            modelreg.review_route("claude:claude-opus-5-5", reg).tools) == ("cli", True)
    assert modelreg.compatible("claude-deepseek-flash", "coding", reg) == (True, None)


def test_the_cli_prints_the_route_as_one_tab_row():
    import subprocess
    out = subprocess.run([sys.executable, str(ROOT / "squad/modelreg.py"), "review-route", "grok-4.6"],
                         capture_output=True, text=True, check=True).stdout
    assert out == "cli\tgrok\tgrok-4.6\t1\n"


def test_the_cli_marks_an_empty_runner_with_a_dash(tmp_path):
    """A shell `read` collapses an empty tab field, so the row never holds one."""
    import os
    import subprocess
    env = {**os.environ, "SG_MODELS_REGISTRY": str(_no_tools_registry(tmp_path / "registry.json"))}
    out = subprocess.run([sys.executable, str(ROOT / "squad/modelreg.py"), "review-route", "claude:claude-deepseek-flash"],
                         capture_output=True, text=True, check=True, env=env).stdout
    assert out == "direct\t-\tclaude-deepseek-flash\t0\n"


def test_review_sh_parses_no_model_prefix_and_is_not_legacy():
    assert "contrib/squad-goal/scripts/review.sh" not in LEGACY
    assert scan_file(REVIEW) == []


def test_review_sh_reads_no_review_no_tools_list():
    assert "review_no_tools" not in REVIEW.read_text()
    assert "review_no_tools" not in json.loads(MODELS.read_text())


def test_the_repo_review_chain_is_the_live_chain(reg):
    live = modelreg.chain(json.loads(LIVE_SHAPE.read_text()), "review", reg).entries
    repo = modelreg.chain(json.loads(MODELS.read_text()), "review", reg).entries
    assert [e.raw for e in repo] == [e.raw for e in live if e.id != "grok-4.6"]
    assert [e.id for e in repo] == ["claude-opus-5-5", "gemini-3.8-flash"]
    assert [e.effort for e in repo] == [None, "medium"]


@pytest.mark.parametrize("raw", ["gpt-6.1-sol", "gpt-6.1-sol-fast", "gpt-5.6-terra-fast", "gpt-5.6-luna-fast"])
def test_a_tools_entry_with_no_cli_route_keeps_codex_with_tools(reg, raw):
    got = modelreg.review_route(raw, reg)
    assert (got.route, got.runner, got.wire, got.tools) == ("cli", "codex", raw, True)


def test_a_served_gpt_6_astra_reviews_on_its_own_cli_route(reg):
    assert "cli" in reg.entries["gpt-6-astra"].routes
    got = modelreg.review_route("gpt-6-astra", reg)
    assert (got.route, got.runner, got.wire, got.tools) == ("cli", "codex", "gpt-6-astra", True)


def test_the_no_tools_call_runs_only_when_the_registry_says_no_tools():
    text = REVIEW.read_text()
    assert '[ "$rroute" != cli ]' not in text
    assert "cli:codex)" in text


def test_an_empty_route_row_makes_a_model_ineligible_and_the_cause_is_kept():
    text = REVIEW.read_text()
    assert '[ -n "$rt" ] || return 1' in text
    assert 'review-route.$$.err' in text


def test_an_unknown_runner_is_refused_by_name():
    assert "unsupported runner" in REVIEW.read_text()


def test_the_review_timeouts_name_only_models_in_the_chain(reg):
    doc = json.loads(MODELS.read_text())
    assert "review_timeout_seconds_by_model" not in doc
    chain = {e.raw for e in modelreg.chain(doc, "review", reg).entries}
    assert {e.raw for e in modelreg.chain(doc, "review", reg).entries if e.timeout_s is not None} <= chain



def test_a_gateway_only_tools_entry_keeps_its_legacy_runner_on_the_raw_spelling(reg):
    got = modelreg.review_route("claude:claude-deepseek-v4-pro", reg)
    assert (got.route, got.runner, got.wire, got.tools) == ("cli", "claude", "claude-deepseek-v4-pro", True)


def test_the_route_error_file_is_one_per_process_and_removed_at_exit():
    text = REVIEW.read_text()
    assert 'review-route.err"' not in text
    assert text.count('review-route.$$.err') >= 3


def test_an_unsupported_runner_clears_stderr_and_ends_its_tries():
    arm = next(line for line in REVIEW.read_text().splitlines() if "unsupported runner" in line)
    assert ': > "$e"' in arm and "lim=$i" in arm and "fmt=1" in arm

