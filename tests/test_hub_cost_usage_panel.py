"""Hub page: subscription cards carry no info lines; totals tiles sit in the cost panel (#1486)."""
import pathlib
import re

HUB = pathlib.Path(__file__).resolve().parents[1] / "contrib/squad-goal/scripts/hub.py"
SRC = HUB.read_text()


def _panel(pid):
    m = re.search(rf"<details id={pid} .*?</details>", SRC, re.S)
    assert m, f"panel {pid} missing"
    return m.group(0)


def test_subscription_card_has_no_info_lines():
    card = SRC[SRC.index("return `<div class=sub>"):]
    card = card[: card.index('}).join("")')]
    for label in ("quota value used", "plan paid so far", "API-equivalent (squad only)"):
        assert label not in card, label
    assert "class=kvs" not in card


def test_totals_tiles_live_in_cost_panel_not_subscriptions():
    assert "tot3" not in _panel("plans")
    assert "id=tot3" in _panel("stats")
    poll = SRC[SRC.index("async function pollSubs"):]
    poll = poll[: poll.index("const SUBL") if "const SUBL" in poll else len(poll)]
    assert '$("#subs").innerHTML=`<div class=tot3>' not in poll
    assert '$("#tot3")' in SRC


def test_cost_panel_is_named_cost_and_usage():
    assert "<summary>cost &amp; usage<" in _panel("stats")
    assert "cost &amp; speed" not in SRC and "cost & speed" not in SRC
