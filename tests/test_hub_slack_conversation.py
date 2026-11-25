"""GitHub issue #2917: the Slack channel detail shows a short conversation (SPEC 2682).

The owner chose the conversation variant from a throwaway prototype. A channel detail tells what happens in the channel
as a Slack user sees it: a member asks and the persona answers or stays quiet, anyone else writes and is read and never
obeyed, an announcement waits as a draft. The hub writes every line; the view draws them (ADR-0137).

The seams are the hub's read, `slack_overview`, and the theme's renderer run in node. Every id is a placeholder.
"""
import json
import re

from tests import test_hub_slack_tree as T

hub = T.hub   # the shared module fixture, registered here under its own name
LOCAL, COMMITTED, by_name, read, render = T.LOCAL, T.COMMITTED, T.by_name, T.read, T.render


def _lines(node):
    return [(x["side"], x["who"], x["text"]) for x in node["conversation"]]


def test_a_speaking_channel_shows_the_member_question_and_the_persona_answer(hub, tmp_path):
    ch = by_name(read(hub, tmp_path)["tree"]["root"], "#proj-agent-squad")
    assert _lines(ch)[:3] == [
        ("in", "someone who holds manager", "@HAL9K status?"),
        ("out", "HAL9K", "Here is the status: …"),
        ("sys", "", "HAL9K answers the 1 person who holds manager. They can ask for: status, logs."),
    ], _lines(ch)


def test_anyone_else_is_read_and_never_obeyed_and_an_announcement_is_a_draft(hub, tmp_path):
    ch = by_name(read(hub, tmp_path)["tree"]["root"], "#proj-agent-squad")
    assert ("in", "anyone else", "@HAL9K deploy now") in _lines(ch)
    assert ("sys", "", "HAL9K reads it and never obeys it. No reply.") in _lines(ch)
    assert ("out", "HAL9K", "Draft for your review: stg_release.") in _lines(ch)
    assert ch["conversation"][-1]["tone"] == "draft"


def test_a_listen_channel_with_a_member_stays_quiet_and_proposes_in_the_dm(hub, tmp_path):
    committed = json.loads(json.dumps(COMMITTED))
    committed["workspaces"][0]["channels"][1]["roles"] = ["manager"]
    local = json.loads(json.dumps(LOCAL))
    local["members"].append({"workspace": "T0TEAM0001", "channel": "C0ALERT001", "role": "manager",
                             "users": ["U0OWNER001"]})
    ch = by_name(read(hub, tmp_path, committed=committed, local=local)["tree"]["root"], "#squad-alerts")
    assert ("sys", "", "HAL9K stays quiet here. A proposal with the manager actions reaches your DM.") in _lines(ch)
    assert all(side != "out" or "Draft" in text for side, _, text in _lines(ch)), _lines(ch)


def test_a_channel_with_no_member_says_nobody_holds_an_accepted_role(hub, tmp_path):
    ch = by_name(read(hub, tmp_path)["tree"]["root"], "#squad-alerts")
    assert _lines(ch)[0] == ("sys", "", "Nobody here holds a role this channel accepts."), _lines(ch)


def test_the_detail_draws_the_conversation_and_keeps_the_tiles_under_details(hub, tmp_path):
    out = read(hub, tmp_path)
    ch = by_name(out["tree"]["root"], "#proj-agent-squad")
    html = render(hub, out, sel=ch["id"])
    assert "What happens here" in html, html
    assert re.search(r'<div class="slcb in"><span class=slcbn>someone who holds manager</span>'
                     r'<span class=slcbt>@HAL9K status\?</span></div>', html), html
    assert re.search(r"<details class=slcdet><summary>Details</summary>.*sltnow", html, re.S), html
    assert html.index("What happens here") < html.index("<details class=slcdet>")


def test_a_workspace_keeps_its_now_tiles(hub, tmp_path):
    out = read(hub, tmp_path)
    ws = by_name(out["tree"]["root"], "team")
    assert "conversation" not in ws
    html = render(hub, out, sel=ws["id"])
    assert "What happens here" not in html and "sltnow" in html


def test_the_channel_note_moves_under_details_beside_the_tiles(hub, tmp_path):
    out = read(hub, tmp_path)
    ch = by_name(out["tree"]["root"], "#proj-agent-squad")
    html = render(hub, out, sel=ch["id"])
    det = html.index("<details class=slcdet>")
    assert html.index("sltnote") > det, "the note sits under Details, not under the conversation"
