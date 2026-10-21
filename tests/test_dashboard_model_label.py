"""The page presents a model by its registry label, and an unknown id raw (SPEC 505 ticket 9).

Every label and alias is inlined from `config/models.registry.json` by the build, so the page and the hub cannot
drift. An old record names a spelling the registry keeps as an alias, and the alias map finds its label. An id the
registry does not know renders exactly as the record wrote it: display never guesses, and never crashes.

Display only: the record, the graph, an editable field and the editor's own suggestion list keep the id whole. A page
that showed a label there would invite a run to be configured with a word no CLI resolves. The spend re-pricing stays
the page's own table, unchanged: the registry landing moved no caller (ADR-0147).
"""
import json
import re

from tests.dashboard_helpers import CORE_DIR, ROOT, run_node

PAGE = ROOT / "dashboard" / "page"
DASH = "—"


def _model(v):
    return run_node([{"op": "fmt", "kind": "model", "value": v}])[0]


def _record(events):
    return run_node([{"op": "record", "text": "".join(json.dumps(e) + "\n" for e in events)}])[0]


def test_a_registry_id_reads_as_its_label():
    assert _model("gpt-5.6-terra") == "GPT-5.6 Terra"
    assert _model("claude-opus-5-5") == "Claude Opus 5.5"   # re-anchored to the #687 scheme: <Family> <version> <tier>, no parentheses


def test_an_alias_reads_as_its_registry_label():
    """An old record or a graph may still name the prefixed spelling; the alias map resolves it."""
    assert _model("opus") == "Claude Opus 5.5"
    assert _model("claude-gpt-5.6-terra") == "GPT-5.6 Terra"
    assert _model("claude-deepseek-v4-flash") == "DeepSeek V4.1 Flash"


def test_an_unknown_id_renders_raw_and_never_crashes():
    for m in ("claude-gpt-5.5", "gemini-9.9-nova", "claude:a"):
        assert _model(m) == m


def test_a_harness_phrase_keeps_everything_after_the_id():
    """`harnessOf` builds `model effort skill`, so the group card's line is a phrase and not a bare id."""
    assert _model("claude-gpt-5.6-terra medium tdd") == "GPT-5.6 Terra medium tdd"


def test_a_missing_model_is_a_dash_like_every_other_missing_figure():
    assert _model(None) == DASH and _model("") == DASH


def test_a_gateway_call_is_re_priced_from_its_tokens_and_keeps_its_cache_read_rate():
    """The SDK prices a model it does not know at Claude rates, so the page prices a gateway call from its tokens.
    The table is the page's own (unchanged by the registry landing): a cache read keeps the table's cache-read rate."""
    parsed = _record([
        {"event": "run_start", "pos": 1, "run_id": "r", "graph_name": "g", "graph_hash": "h", "engine_options": {}, "root": "main", "plan": []},
        {"event": "outcome", "pos": 2, "node": "actor", "entry": 1, "outcome": "pass", "reasons": [], "model": "claude-gpt-5.6-terra",
         "spend": 99.0, "tokens_in": 1000, "tokens_out": 100, "cache_read": 400},
    ])
    ev = parsed["events"][1]
    assert ev["spend"] == (400 * 0.2 + 600 * 2 + 100 * 12) / 1e6
    assert ev["spend_sdk"] == 99.0


def test_a_claude_call_keeps_the_sdk_spend_and_an_unknown_id_is_never_re_priced():
    parsed = _record([
        {"event": "run_start", "pos": 1, "run_id": "r", "graph_name": "g", "graph_hash": "h", "engine_options": {}, "root": "main", "plan": []},
        {"event": "outcome", "pos": 2, "node": "a", "entry": 1, "outcome": "pass", "reasons": [], "model": "claude-sonnet-5-5",
         "spend": 0.0414606, "tokens_in": 1200, "tokens_out": 400},
        {"event": "outcome", "pos": 3, "node": "b", "entry": 1, "outcome": "pass", "reasons": [], "model": "claude:a",
         "spend": 1.5, "tokens_in": 1000, "tokens_out": 100},
    ])
    assert parsed["events"][1]["spend"] == 0.0414606 and "spend_sdk" not in parsed["events"][1]
    assert parsed["events"][2]["spend"] == 1.5 and "spend_sdk" not in parsed["events"][2]


def test_the_formatter_lives_in_the_core_and_not_in_the_view():
    """The rule is shared by every view, so it sits in the one formatter beside `usd` and `tokens`."""
    assert re.search(r"model: \(m\) =>", (CORE_DIR / "totals.js").read_text()), "core.fmt has no model formatter"


def test_the_snapshot_reading_module_carries_the_seam_header():
    """A file that reads the registry instead of parsing an id carries the seam header (ADR 505). The record's
    re-pricing reads no registry: it keeps the page's own table."""
    head = (CORE_DIR / "totals.js").read_text().splitlines()[:5]
    assert any("modelreg: seam-clean" in line for line in head), "totals.js carries no modelreg seam header"


def test_every_place_the_page_presents_a_model_goes_through_the_formatter():
    """The positive half: each render site names the formatter. Measured 2026-10-13: the card, the group card, the
    roster, the child row, the available and used harness rows, the inherited allow list, and the agent summary.
    Each goes through `fmt.llm`, which reads like `fmt.model` and marks an unregistered id (#677)."""
    graph = (PAGE / "graph.js").read_text()
    panel = (PAGE / "panel.js").read_text()
    for src, site in [
        (graph, "${role}${f.llm(c.model)}"),                      # the child card's kind line
        (graph, "core.fmt.llm(gr.harness)"),                      # the group card, drawn and in its tooltip
        (panel, "core.fmt.llm(gr.harness)"),                      # the group detail box
        (panel, "esc(f.llm(c.model))"),                           # the selected child's row
        (panel, "esc(core.fmt.llm(r.model))"),                    # the roster table
        (panel, "esc(f.llm(a.model))"),                           # the available harness
        (panel, "esc(f.llm(u.model))"),                           # the used harness
        (panel, "a.models.map((m) => f.llm(m))"),                 # the inherited allow list
        (panel, "esc(f.llm(model))"),                             # the agent summary of the run
    ]:
        assert site in src, f"this render site does not hide the prefix: {site}"


def test_no_page_source_interpolates_a_raw_model_id():
    """The negative half: a render site added later that reaches for the field itself fails here."""
    raw = re.compile(r"esc\((?:String\()?[caur]\.model\b")
    for f in sorted(PAGE.glob("*.js")):
        for i, line in enumerate(f.read_text().splitlines(), 1):
            m = raw.search(line)
            assert not m, f"{f.name}:{i} presents a model id through the field and not the formatter: {m.group(0)}"
