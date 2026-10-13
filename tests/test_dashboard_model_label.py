"""Owner ruling: a model name the page presents hides its `claude-` prefix.

A dispatch child's card read `tdd · claude-gpt-5.5 medium`. The prefix is the CLI's provider marker: it says which CLI
serves the id, and it is not part of what a reader compares. The rule lives in the one formatter, `core.fmt`, so the
child card, the group card, the roster and the harness panel cannot drift apart.

Display only: the record, the graph, an editable field and the editor's own suggestion list keep the id whole. A page that
showed `gpt-5.5` there would invite a run to be configured with an id no CLI resolves.
"""
import re

from tests.dashboard_helpers import CORE_DIR, ROOT, run_node

PAGE = ROOT / "dashboard" / "page"
DASH = "—"


def _model(v):
    return run_node([{"op": "fmt", "kind": "model", "value": v}])[0]


def test_the_provider_prefix_is_hidden():
    assert _model("claude-gpt-5.5") == "gpt-5.5"
    assert _model("claude-sonnet-4-6-thinking") == "sonnet-4-6-thinking"


def test_an_id_without_the_prefix_is_untouched():
    for m in ("sonnet", "opus", "haiku", "fable", "gemini-3.8-flash", "gpt-oss-120b-medium"):
        assert _model(m) == m


def test_a_harness_phrase_keeps_everything_after_the_id():
    """`harnessOf` builds `model effort skill`, so the group card's line is a phrase and not a bare id."""
    assert _model("claude-gpt-5.5 medium tdd") == "gpt-5.5 medium tdd"


def test_a_missing_model_is_a_dash_like_every_other_missing_figure():
    assert _model(None) == DASH and _model("") == DASH


def test_the_formatter_lives_in_the_core_and_not_in_the_view():
    """The rule is shared by every view, so it sits in the one formatter beside `usd` and `tokens`."""
    assert re.search(r"model: \(m\) =>", (CORE_DIR / "totals.js").read_text()), "core.fmt has no model formatter"


def test_every_place_the_page_presents_a_model_goes_through_the_formatter():
    """The positive half: each render site names the formatter. Measured 2026-10-13: the card, the group card, the
    roster, the child row, the available and used harness rows, the inherited allow list, and the agent summary."""
    graph = (PAGE / "graph.js").read_text()
    panel = (PAGE / "panel.js").read_text()
    for src, site in [
        (graph, "${role}${f.model(c.model)}"),                      # the child card's kind line
        (graph, "core.fmt.model(gr.harness)"),                      # the group card, drawn and in its tooltip
        (panel, "core.fmt.model(gr.harness)"),                      # the group detail box
        (panel, "esc(f.model(c.model))"),                           # the selected child's row
        (panel, "esc(core.fmt.model(r.model))"),                    # the roster table
        (panel, "esc(f.model(a.model))"),                           # the available harness
        (panel, "esc(f.model(u.model))"),                           # the used harness
        (panel, "a.models.map((m) => f.model(m))"),                 # the inherited allow list
        (panel, "esc(f.model(model))"),                             # the agent summary of the run
    ]:
        assert site in src, f"this render site does not hide the prefix: {site}"


def test_no_page_source_interpolates_a_raw_model_id():
    """The negative half: a render site added later that reaches for the field itself fails here."""
    raw = re.compile(r"esc\((?:String\()?[caur]\.model\b")
    for f in sorted(PAGE.glob("*.js")):
        for i, line in enumerate(f.read_text().splitlines(), 1):
            m = raw.search(line)
            assert not m, f"{f.name}:{i} presents a model id through the field and not the formatter: {m.group(0)}"
