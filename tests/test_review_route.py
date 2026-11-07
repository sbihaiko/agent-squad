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


def test_the_repo_review_chain_is_the_one_this_repository_runs(reg):
    """The review chain leads with DeepSeek V4.1 Flash, the cheapest model, inside its hours window.

    The fixture under `tests/fixtures/modelreg/live.models.json` is a captured old live shape, kept for the migration
    tests; the live file itself is published from this repository, so the pin lives here and not against that
    capture. DeepSeek first; Grok 4.7 behind it, which preempts Opus for the hours DeepSeek closes: a cost trade,
    not a quality one, and the reason the entry names low. Grok always reasons and its default effort is high, so
    an entry that named none would reason for minutes and outlive the timeout. Then Opus, Gemini and Sol."""
    repo = modelreg.chain(json.loads(MODELS.read_text()), "review", reg).entries
    assert [e.id for e in repo] == ["deepseek-flash", "grok-4.7", "claude-opus-5-5", "gemini-3.8-flash", "gpt-6.1-sol"]
    assert repo[0].hrs, "DeepSeek keeps its hours window, so the chain falls through outside it"


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
    assert '"$rd.err"' in text


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


def test_the_route_error_file_lives_in_the_invocation_dir_and_is_removed_at_exit():
    """The resolver's stderr has one home per review invocation, and the trap removes it with the cache."""
    text = REVIEW.read_text()
    assert '"$rd.err"' in text
    assert 'review-route.err"' not in text
    assert 'rm -rf "$rd"' in text


def test_an_unsupported_runner_clears_stderr_and_ends_its_tries():
    arm = next(line for line in REVIEW.read_text().splitlines() if "unsupported runner" in line)
    assert ': > "$e"' in arm and "lim=$i" in arm and "fmt=1" in arm


def test_a_no_tools_grok_reviewer_answers_on_the_gateway(reg):
    """#1373: a grok reviewer that names review_tools:false takes the no-tools gateway call, and its CLI row still
    names the vendor that the quota markers and the CLI fallback follow."""
    got = modelreg.review_route("grok-4.7", reg)
    assert (got.route, got.runner, got.wire, got.tools) == ("direct", "", "claude-grok-4.7", False)
    fallback = modelreg.review_route("grok-4.7", reg, route="cli")
    assert (fallback.route, fallback.runner, fallback.wire, fallback.tools) == ("cli", "grok", "grok-4.7", True)


def test_the_cli_prints_a_reviewers_fallback_row_on_request():
    import subprocess
    out = subprocess.run([sys.executable, str(ROOT / "squad/modelreg.py"), "review-route", "grok-4.7", "--route", "cli"],
                         capture_output=True, text=True, check=True).stdout
    assert out == "cli\tgrok\tgrok-4.7\t1\n"


def test_a_reviewer_with_no_cli_route_names_no_fallback(tmp_path):
    """A no-tools reviewer with no CLI row has no fallback: the request answers the gateway row, never a made-up one."""
    doc = json.loads((ROOT / "config/models.registry.json").read_text())
    entry = next(m for m in doc["models"] if "gateway" in m.get("routes", {}) and "cli" not in m["routes"])
    entry["capabilities"]["review_tools"] = False
    p = tmp_path / "registry.json"
    p.write_text(json.dumps(doc))
    got = modelreg.review_route(entry["id"], modelreg.load(p), route="cli")
    assert (got.route, got.runner) == ("gateway", "")


def test_review_sh_sends_a_no_tools_grok_as_one_streamed_gateway_call():
    """The stream keeps the answer under the gateway's idle timeout, the effort rides in output_config, and no tools
    cross the bridge. The CLI stays the fallback for a cut or refused stream (#1373)."""
    text = REVIEW.read_text()
    assert "stream: true, output_config: {effort: $e}" in text
    assert "del(.thinking)" in text
    assert 'python3 "$(dirname "$0")/review_stream.py"' in text
    assert 'aroute=cli; arunner=grok; grok_cli' in text
    assert 'case $vendor in codex)' in text and 'case $vendor in agy)' in text and 'case $vendor in grok)' in text


def test_a_gateway_timeout_keeps_its_own_exit_and_never_falls_back():
    """A curl -m exit on the deadline is the model's timeout whenever it arrives, so the fallback never spends a second
    one (#1373). The classification never consults the wall clock: curl's own deadline fires a moment under the budget
    too, and a stream cut at 28 that fell through to the grok CLI would bill two budgets for one review."""
    text = REVIEW.read_text()
    line = next(row for row in text.splitlines() if "[ $r = 28 ]" in row)
    assert "r=142" in line
    assert "SECONDS" not in line
    assert 'stream" = 1 ] && [ "$r" != 142 ]' in text


def test_the_grok_cli_fallback_reshapes_its_json_envelope():
    """The grok CLI prints `{type, text, usage}`, not the bare verdict, so the fallback's answer file would start with
    `{`. grok_cli reshapes it into the CLI shape, and the ledger branch reads `.text` or `.result` (#1373)."""
    text = REVIEW.read_text()
    assert "result: .text" in text
    assert 'if (.text|type)=="string" then .text elif (.result|type)=="string" then .result' in text


def test_a_deliberate_gateway_error_never_falls_back_to_the_cli():
    """Only a cut or refused stream spends the CLI. An explicit SSE error event is the gateway's own answer, so the
    chain's retry path owns it and no grok call is spent twice (#1373)."""
    text = REVIEW.read_text()
    assert 'case $s in' in text
    assert any(line.strip() == '""|error_stream|unreadable)' for line in text.splitlines())


def test_a_converter_that_prints_no_json_is_an_explicit_failure():
    """A raw HTML error page or an unreadable stream is no answer: the body goes to $e, the call returns non-zero, and
    the failure carries its own marker, so the fallback and the ledger name an unreadable gateway answer instead of
    reading an absent $o as an ordinary cut (#1373)."""
    text = REVIEW.read_text()
    assert 'jq -e \'type=="object"\' "$o.s"' in text
    assert 'cat "$o.api" >> "$e"' in text and 'r=65' in text
    assert "gwhy=unreadable" in text
    assert '"subtype":"unreadable"' in text
    assert any(line.strip() == '""|error_stream|unreadable)' for line in text.splitlines())


def test_the_grok_cli_fallback_reads_the_effort_of_the_model_it_calls():
    """The fallback calls `grok -m $gwire`, so its `--reasoning-effort` is read from that spelling, never from the
    registry id the chain row named (#1373)."""
    text = REVIEW.read_text()
    assert 'effort_of "$w"' in text
    assert 'ef=${3:-$eff}' not in text


def test_the_route_cache_is_one_private_dir_per_review_invocation():
    """`$$` survives `$(...)`, so it shared the cache across the review's subshells and also outlived a killed review:
    a recycled pid could read a dead review's row for another registry. The cache is one `mktemp -d` per invocation,
    and the comment names that scope (#1373)."""
    text = REVIEW.read_text()
    assert "rd=$(mktemp -d)" in text
    assert 'f="$rd/$k"' in text
    assert "review-route.$$" not in text
    assert "one private dir per review invocation" in text

