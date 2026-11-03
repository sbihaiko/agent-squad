"""The question card (#1089): a fixed-schema card that the chat surface draws from one pure rule module.

The rules run under node alone (`contrib/squad-goal/scripts/test-hub-question-card.sh`). These cases prove the page: the
module is inlined into the served page, the renderer draws only what the rule module answers, and a card booted under the
stub DOM shows its tabs and marks, runs the actions its controls and keys send, and names every unanswered question.
"""
import pathlib
import re

import pytest

import tests.test_hub_mobile_move as move

hub = move.hub
run_page = move.run_page

ROOT = pathlib.Path(__file__).resolve().parent.parent
QUESTION_JS_PATH = ROOT / "contrib" / "squad-goal" / "scripts" / "hub-question-card.js"

CARD_TWO = """{questions: [
  {header: "Scope", body: "Which scope?", options: [{label: "Narrow", description: "Only the hub"},
                                                    {label: "Wide", description: "Every surface", recommended: true}]},
  {header: "Depth", body: "How deep?", options: [{label: "Shallow", description: "a"}, {label: "Deep", description: "b"}]}]}"""


def _draw(driver_body, cards=CARD_TWO):
    return f"""
      const doc = {cards};
      const submitted = [];
      const el = drawQuestionCard(doc, answers => submitted.push(answers));
      const button = label => el.querySelectorAll(".qctl button").find(b => b.textContent === label);
      const option = i => el.querySelectorAll(".qopt")[i];
      const key = (k, extra) => el.listeners.keydown[0](Object.assign({{ key: k, preventDefault() {{}} }}, extra || {{}}));
      {driver_body}
    """


def test_the_rule_module_is_inlined_into_the_page(hub):
    assert QUESTION_JS_PATH.read_text() in hub.MOBILE_PAGE, "the page does not carry the question card's rule module"
    assert "/*QUESTION*/" not in hub.MOBILE_PAGE


def test_the_renderer_draws_only_what_the_rule_module_answers(hub):
    block = re.search(r"// <question-card-view>(.*?)// </question-card-view>", hub.MOBILE_PAGE, re.S)
    assert block, "the renderer block is missing from the page"
    text = block.group(1)
    assert text.count("function drawQuestionCard") == 1, "a card is drawn by more than one renderer"
    assert "cardView(" in text, "the renderer does not draw from the rule module's view"
    for banned in ("innerHTML", "insertAdjacentHTML", "outerHTML", "fetch(", "localStorage", "sessionStorage"):
        assert banned not in text, f"the renderer holds {banned!r}: the schema is data, never markup"


def test_the_card_uses_the_chat_surface_tokens_and_the_phone_floor(hub):
    rule = re.search(r"\.qcard\{[^}]*\}", hub.MOBILE_PAGE)
    assert rule, "the card has no style of its own in the chat surface"
    for token in ("var(--panel)", "var(--line)", "var(--fs-body)", "border-radius:var(--radius-lg)"):
        assert token in rule.group(0), f"the card does not use {token}: the chat turn's surface is the shared one"
    assert re.search(r"\.qcard button\{[^}]*min-height:44px", hub.MOBILE_PAGE), "a card control is under the 44 px floor"


@pytest.mark.node_bound
def test_tabs_appear_only_for_more_than_one_question_and_the_recommendation_is_marked(hub):
    out = run_page(hub.MOBILE_PAGE, _draw("""
      const single = drawQuestionCard({questions: [doc.questions[0]]}, () => {});
      return {
        tabs: el.querySelectorAll(".qtab").map(t => t.textContent),
        badges: el.querySelectorAll(".qbadge").map(b => b.textContent),
        singleTabs: single.querySelectorAll(".qtab").length,
      };
    """))
    assert out["tabs"] == ["Scope", "Depth"], out
    assert out["singleTabs"] == 0, "a one-question card drew tabs"
    assert out["badges"] == ["Recommended"], out


@pytest.mark.node_bound
def test_arrow_tab_enter_and_escape_act_on_the_card_and_its_controls_reach_by_touch(hub):
    out = run_page(hub.MOBILE_PAGE, _draw("""
      key("ArrowDown");
      const focusedAfterArrow = el.querySelectorAll(".qopt.focused .qlabel").map(o => o.textContent);
      key("Enter");
      const selectedAfterEnter = el.querySelectorAll(".qopt.selected .qlabel").map(o => o.textContent);
      key("Tab");
      const headerAfterTab = el.querySelectorAll(".qtab.active").map(t => t.textContent);
      option(1).listeners.click[0]({});
      const selectedAfterTouch = el.querySelectorAll(".qopt.selected .qlabel").map(o => o.textContent);
      button("Cancel").listeners.click[0]({});
      return {
        focusedAfterArrow, selectedAfterEnter, headerAfterTab, selectedAfterTouch,
        controlsAfterCancel: el.querySelectorAll(".qctl button").length,
        text: el.textContent,
      };
    """))
    assert out["focusedAfterArrow"] == ["Wide"], out
    assert out["selectedAfterEnter"] == ["Wide"], out
    assert out["headerAfterTab"] == ["Depth"], out
    assert out["selectedAfterTouch"] == ["Deep"], out
    assert out["controlsAfterCancel"] == 0, "a cancelled card still offers controls"
    assert "Cancelled" in out["text"], out


@pytest.mark.node_bound
def test_the_control_floor_names_every_action_a_touch_can_send(hub):
    out = run_page(hub.MOBILE_PAGE, _draw("""
      return { labels: el.querySelectorAll(".qctl button").map(b => b.textContent) };
    """))
    assert out["labels"] == ["Up", "Down", "Previous question", "Next question", "Select", "Cancel", "Submit"], out


@pytest.mark.node_bound
def test_submit_names_every_unanswered_question_and_none_is_skipped(hub):
    out = run_page(hub.MOBILE_PAGE, _draw("""
      button("Submit").listeners.click[0]({});
      const named = el.querySelectorAll(".qwarn").map(w => w.textContent);
      option(1).listeners.click[0]({});
      button("Submit").listeners.click[0]({});
      const namedAfterOne = el.querySelectorAll(".qwarn").map(w => w.textContent);
      key("Tab"); option(1).listeners.click[0]({});
      button("Submit").listeners.click[0]({});
      return { named, namedAfterOne, submitted, text: el.textContent };
    """))
    assert out["named"] == ["Not answered: 1. Scope, 2. Depth"], out
    assert out["namedAfterOne"] == ["Not answered: 2. Depth"], out
    assert out["submitted"] == [[{"header": "Scope", "label": "Wide"}, {"header": "Depth", "label": "Deep"}]], out


@pytest.mark.node_bound
def test_submit_names_each_of_three_unanswered_questions(hub):
    three = CARD_TWO[:-2] + """,
  {header: "Owner", body: "Who owns it?", options: [{label: "Hub", description: "a"}, {label: "Loop", description: "b"}]}]}"""
    out = run_page(hub.MOBILE_PAGE, _draw("""
      button("Submit").listeners.click[0]({});
      return { named: el.querySelectorAll(".qwarn").map(w => w.textContent), submitted };
    """, cards=three))
    assert out["named"] == ["Not answered: 1. Scope, 2. Depth, 3. Owner"], out
    assert out["submitted"] == [], "a card with unanswered questions was submitted"


@pytest.mark.node_bound
def test_a_card_the_rule_module_refuses_draws_no_controls(hub):
    out = run_page(hub.MOBILE_PAGE, """
      const el = drawQuestionCard({questions: []}, () => {});
      return { controls: el.querySelectorAll(".qctl button").length, text: el.textContent };
    """)
    assert out["controls"] == 0, out
    assert "at least one question" in out["text"], out

