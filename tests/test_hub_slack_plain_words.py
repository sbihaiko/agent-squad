"""GitHub issue #2912: the Slack channel detail speaks plainly and answers first (SPEC 2682).

A design review found that the reader learns who the persona answers only after five tiles and a callout, that the
tree speaks internal terms, that a hash channel row shows two "#" marks and that the detail subtitle carries a raw
Slack id. The hub writes every word and the verdict; the view draws them (ADR-0137).

The seams are the hub's read, `slack_overview`, and the theme's renderer run in node. Every id is a placeholder.
"""
import json
import re

from tests import test_hub_slack_tree as T

hub = T.hub   # the shared module fixture, registered here under its own name
LOCAL, COMMITTED, by_name, read, render = T.LOCAL, T.COMMITTED, T.by_name, T.read, T.render


def _no_member(channel="C0CLISUP01"):
    local = json.loads(json.dumps(LOCAL))
    local["members"] = [m for m in local["members"] if m["channel"] != channel]
    return local


def _texts(node):
    return [b["text"] for b in node["badges"]]


def test_the_badges_speak_the_users_words(hub, tmp_path):
    root = read(hub, tmp_path, local=_no_member())["tree"]["root"]
    assert "nobody can ask" in _texts(by_name(root, "#support")), by_name(root, "#support")["badges"]
    assert _texts(by_name(root, "team"))[:1] == ["our workspace"]
    assert _texts(by_name(root, "client-a"))[:1] == ["guest workspace"]
    for word in ("no one", "owned", "guest", "follows default"):
        assert all(word not in _texts(n) for n in T.walk(root)), word


def test_a_message_from_nobody_eligible_is_read_and_never_obeyed(hub, tmp_path):
    root = read(hub, tmp_path, local=_no_member())["tree"]["root"]
    ch = by_name(root, "#support")
    assert T.tiles(ch)["A member's message"]["value"] == "read, never obeyed"
    assert "read as data" not in json.dumps(ch)


def test_a_listen_only_channel_with_nobody_eligible_is_neutral(hub, tmp_path):
    root = read(hub, tmp_path)["tree"]["root"]
    badge = next(b for b in by_name(root, "#squad-alerts")["badges"] if b["text"] == "nobody can ask")
    assert badge["tone"] == "neutral", badge


def test_the_channel_detail_subtitle_carries_no_slack_id_and_a_copy_control_does(hub, tmp_path):
    out = read(hub, tmp_path)
    ch = by_name(out["tree"]["root"], "#proj-agent-squad")
    assert "C0PROJ0001" not in ch["detail"]["sub"], ch["detail"]
    html = render(hub, out, sel=ch["id"])
    sub = re.search(r"<p class=sltsub>(.*?)</p>", html).group(1)
    assert "C0PROJ0001" not in sub, sub
    assert re.search(r'<button[^>]*data-slt-copy="C0PROJ0001"[^>]*>copy id</button>', html), html


def test_a_hash_channel_row_shows_one_hash(hub, tmp_path):
    out = read(hub, tmp_path)
    html = render(hub, out)
    row = re.search(r'<span class=sltname[^>]*>([^<]*proj-agent-squad)</span>', html).group(1)
    assert row == "proj-agent-squad", row


def test_the_verdict_answers_who_can_ask_before_the_tiles(hub, tmp_path):
    out = read(hub, tmp_path, local=_no_member())
    root = out["tree"]["root"]
    assert by_name(root, "#proj-agent-squad")["detail"]["verdict"] == \
        "HAL9K answers 1 person here: anyone who holds manager."
    assert by_name(root, "#support")["detail"]["verdict"] == \
        "Nobody can ask Iara here: no member holds a role this channel accepts."
    assert by_name(root, "#squad-alerts")["detail"]["verdict"] == "HAL9K only listens here and answers nobody."
    ch = by_name(root, "#proj-agent-squad")
    html = render(hub, out, sel=ch["id"])
    assert re.search(r"<p class=sltverdict>HAL9K answers 1 person here: anyone who holds manager\.</p>", html), html
    assert html.index("sltverdict") < html.index("sltnow"), "the verdict sits above the Now tiles"


def test_the_back_button_reads_the_panel_width_not_the_details(hub):
    """The detail pane is a container itself, so an unnamed query showed "Show the tree" beside the tree."""
    q = re.search(r"@container (\w+ )?\(max-width:700px\)\{[^@]*?\.sltback\{display:inline-flex\}", hub.PAGE)
    assert q and q.group(1), "the query that shows the back button names its container"
    assert re.search(rf"container-name:{q.group(1).strip()}", hub.PAGE)
