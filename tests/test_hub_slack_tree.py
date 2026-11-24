"""GitHub issue #2824: the Communication tree, a read view of the Slack policy (SPEC 2682).

Settings > Communication shows one tree, Project > Workspace > Channel. The owner selects a node and reads its
effective state in a Now block. Every effective value comes from `squad/slackpolicy.py` on the server, through the one
read route `/api/slack/files`; the view holds no rule of its own (ADR-0137, ADR-0177).

The seams under test are the hub's own read, `slack_overview`, whose `tree` key is the tree, and the theme's renderer
run in node. No browser runs here. Every id and name is a neutral placeholder.
"""
import datetime
import importlib.util
import json
import pathlib
import re
import subprocess
import tempfile
import urllib.parse
from html.parser import HTMLParser

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib" / "squad-goal" / "scripts" / "hub.py"
NOW = datetime.datetime(2026, 11, 24, 12, 0, tzinfo=datetime.UTC)

COMMITTED = {
    "version": 1,
    "project": {"language": "en-US", "persona": "hal"},
    "apps": [{"id": "hal9k", "keychain": {"bot": "agent-squad-slack-bot", "app": "agent-squad-slack-app"}}],
    "personas": [
        {"id": "hal", "name": "HAL9K", "voice": "Kore", "tone": "Calm and direct."},
        {"id": "iara", "name": "Iara", "voice": "Sulafat", "tone": "Warm and patient."},
    ],
    "events": [
        {"id": "stg_release", "template": "Deployed to STG: {items}."},
        {"id": "prod_release", "template": "Published to PROD: {items}."},
    ],
    "roles": [
        {"id": "manager", "actions": ["status", "logs"]},
        {"id": "colleague", "actions": ["status"]},
    ],
    "workspaces": [
        {"id": "T0TEAM0001", "label": "team", "class": "owned", "app": "hal9k", "language": "pt-BR",
         "channels": [
             {"id": "C0PROJ0001", "name": "proj-agent-squad", "mode": "listen+speak", "roles": ["manager"],
              "announce": ["stg_release"]},
             {"id": "C0ALERT001", "name": "squad-alerts", "mode": "listen", "persona": "hal",
              "announce": ["prod_release"]},
         ]},
    ],
}

LOCAL = {
    "version": 1,
    "workspaces": [
        {"id": "T0CLIENT01", "label": "client-a", "class": "guest", "language": "en-US", "persona": "iara",
         "channels": [
             {"id": "C0CLISUP01", "name": "support", "mode": "listen+speak", "speak": "draft", "roles": ["manager"]},
             {"id": "C0CLIGEN01", "name": "general", "mode": "listen"},
         ]},
    ],
    "members": [
        {"workspace": "T0TEAM0001", "channel": "C0PROJ0001", "role": "manager", "users": ["U0OWNER001"]},
        {"workspace": "T0CLIENT01", "channel": "C0CLISUP01", "role": "manager", "users": ["U0CLIENT01"]},
    ],
}


def guest_workspace(**over):
    """The project that holds the machine's one guest slot, with the base local file's other rows."""
    local = json.loads(json.dumps(LOCAL))
    local["workspaces"][0].update(over)
    return local


@pytest.fixture(scope="module")
def hub():
    """The hub module under a pinned environment, the same pin the read-view suite uses, so the policy module resolves
    from this tree and never from a loop checkout the caller exported."""
    mp = pytest.MonkeyPatch()
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("SG_SQUAD", str(ROOT))):
        mp.setenv(k, v)
    for k in ("SG_HUB_DIR", "SG_STATE", "SG_CHECKOUT", "SG_SHARED", "SG_PROFILES_DIR"):
        mp.setenv(k, tempfile.mkdtemp())
    spec = importlib.util.spec_from_file_location("hub_slack_tree", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    yield mod
    mp.undo()


def files(tmp_path, committed=None, local=None):
    c = tmp_path / "docs" / "squad" / "slack.yaml"
    c.parent.mkdir(parents=True, exist_ok=True)
    c.write_text(yaml.safe_dump(COMMITTED if committed is None else committed))
    loc = tmp_path / "t.slack.local.yaml"
    loc.write_text(yaml.safe_dump(LOCAL if local is None else local))
    return c, loc


def read(hub, tmp_path, committed=None, local=None):
    c, loc = files(tmp_path, committed, local)
    return hub.slack_overview(c, loc)


def tree(hub, tmp_path, committed=None, local=None):
    return read(hub, tmp_path, committed, local)["tree"]


def walk(node):
    yield node
    for child in node.get("children") or []:
        yield from walk(child)


def by_name(root, name):
    for node in walk(root):
        if node["name"] == name:
            return node
    raise AssertionError(f"the tree names no node {name!r}: {[n['name'] for n in walk(root)]}")


def tiles(node):
    return {t["key"]: t for t in node.get("now") or []}


# ---------------------------------------------------------------- the tree shape

def test_the_tree_has_a_project_a_workspace_and_a_channel(hub, tmp_path):
    root = tree(hub, tmp_path)["root"]
    assert (root["kind"], root["level"]) == ("project", 1)
    ws = by_name(root, "team")
    assert (ws["kind"], ws["level"]) == ("workspace", 2)
    ch = by_name(root, "#proj-agent-squad")
    assert (ch["kind"], ch["level"]) == ("channel", 3)
    assert ch["children"] == []
    assert [w["name"] for w in root["children"]] == ["team", "client-a"]


def test_a_channel_now_block_names_persona_language_destination_reply_and_announcements(hub, tmp_path):
    root = tree(hub, tmp_path)["root"]
    now = tiles(by_name(root, "#proj-agent-squad"))
    assert list(now) == ["Persona", "Language", "A member's message", "Reply", "Announcements"], list(now)
    assert now["Persona"]["value"] == "HAL9K" and now["Persona"]["sub"] == "from project"
    assert now["Language"]["value"] == "pt-BR" and now["Language"]["sub"] == "from workspace"
    assert now["Announcements"]["value"] == "1 event", now["Announcements"]


def test_each_source_tile_names_the_level_it_comes_from(hub, tmp_path):
    """The chain is the policy's: a channel field wins, then its workspace, then the project, then the default."""
    root = tree(hub, tmp_path)["root"]
    alert = tiles(by_name(root, "#squad-alerts"))
    assert (alert["Persona"]["value"], alert["Persona"]["sub"]) == ("HAL9K", "from channel")
    assert (alert["Language"]["value"], alert["Language"]["sub"]) == ("pt-BR", "from workspace")
    gen = tiles(by_name(root, "#general"))
    assert (gen["Language"]["value"], gen["Language"]["sub"]) == ("en-US", "from workspace")
    assert (gen["Persona"]["value"], gen["Persona"]["sub"]) == ("Iara", "from workspace")
    committed = json.loads(json.dumps(COMMITTED))
    committed["project"]["language"] = None
    committed["workspaces"][0]["language"] = None
    root = tree(hub, tmp_path, committed=committed)["root"]
    assert (tiles(by_name(root, "#proj-agent-squad"))["Language"]["value"],
            tiles(by_name(root, "#proj-agent-squad"))["Language"]["sub"]) == ("en-US", "from default")


def test_the_destination_tile_reads_inbox_or_proposal(hub, tmp_path):
    root = tree(hub, tmp_path)["root"]
    assert tiles(by_name(root, "#proj-agent-squad"))["A member's message"]["value"] == "inbox"
    assert tiles(by_name(root, "#support"))["A member's message"]["value"] == "proposal"


def test_a_channel_with_no_eligible_member_reads_as_data(hub, tmp_path):
    """No member of the local file holds a role the channel accepts, so a message stays data and the reply is `none`."""
    local = json.loads(json.dumps(LOCAL))
    local["members"] = [m for m in local["members"] if m["channel"] != "C0CLISUP01"]
    root = tree(hub, tmp_path, local=local)["root"]
    now = tiles(by_name(root, "#support"))
    assert now["A member's message"]["value"] == "read as data"
    assert now["Reply"]["value"] == "none"
    assert any(b["text"] == "no one" for b in by_name(root, "#support")["badges"])


def test_the_reply_tile_reads_each_policy_path(hub, tmp_path):
    root = tree(hub, tmp_path)["root"]
    assert tiles(by_name(root, "#proj-agent-squad"))["Reply"]["value"] == "bot"
    assert tiles(by_name(root, "#squad-alerts"))["Reply"]["value"] == "never posts"
    assert tiles(by_name(root, "#support"))["Reply"]["value"] == "draft"


def test_a_guest_channel_with_a_grant_reads_send_or_send_except_n(hub, tmp_path):
    """`speak: send` with a `send_grant` posts from the owner's account; `draft_for` names people it only drafts for."""
    local = json.loads(json.dumps(LOCAL))
    ch = local["workspaces"][0]["channels"][0]
    ch["speak"], ch["send_grant"] = "send", "2026-11-24"
    root = tree(hub, tmp_path, local=local)["root"]
    assert tiles(by_name(root, "#support"))["Reply"]["value"] == "send"
    local["workspaces"][0]["channels"][1]["speak"] = "send"
    local["workspaces"][0]["channels"][1]["mode"] = "listen+speak"
    local["workspaces"][0]["channels"][0]["draft_for"] = ["U0CLIENT09", "U0CLIENT10"]
    root = tree(hub, tmp_path, local=local)["root"]
    assert tiles(by_name(root, "#support"))["Reply"]["value"] == "send, except 2 listed"
    assert tiles(by_name(root, "#general"))["Reply"]["value"] == "none"


def test_an_owned_workspace_with_a_guest_channel_says_so_and_offers_no_move(hub, tmp_path):
    local = {"workspaces": [{"id": "T0TEAM0001", "channels": [
        {"id": "C0SAND0001", "name": "guest-sandbox", "class": "guest", "mode": "listen+speak", "speak": "send",
         "send_grant": "2026-11-24", "roles": ["manager"]}]}],
        "members": [{"workspace": "T0TEAM0001", "channel": "C0SAND0001", "role": "manager", "users": ["U0OWNER001"]}]}
    out = read(hub, tmp_path, local=local)
    ws = by_name(out["tree"]["root"], "team")
    assert tiles(ws)["Class"]["value"] == "owned · contains guest channel", tiles(ws)
    assert by_name(out["tree"]["root"], "#guest-sandbox")["badges"][0] == {"text": "guest", "tone": "neutral",
                                                                           "kind": "class"}
    html = render(hub, out, sel="w0")
    assert "owned · contains guest channel" in html
    assert "Move to owned" not in html


def test_a_refused_guest_workspace_shows_one_defect_and_its_channels_no_mode_badge(hub, tmp_path):
    local = json.loads(json.dumps(LOCAL))
    local["workspaces"].append({"id": "T0OTHER001", "label": "client-b", "class": "guest",
                                "channels": [{"id": "C0CLIB0001", "name": "b-general", "mode": "listen+speak"}]})
    root = tree(hub, tmp_path, local=local)["root"]
    refused = by_name(root, "client-b")
    assert refused["state"] == "refused"
    assert len(refused["defects"]) == 1, refused["defects"]
    assert "a second guest workspace is refused" in refused["defects"][0]
    assert [b["text"] for b in refused["badges"]] == ["guest", "refused"], refused["badges"]
    assert by_name(root, "#b-general")["badges"] == [{"text": "refused", "tone": "err", "kind": "refused"}]


def test_the_guest_slot_names_the_project_that_holds_it(hub, tmp_path):
    out = read(hub, tmp_path)["tree"]
    assert out["guest_slot"] == {"held": "client-a", "count": 1, "line": "Guest slot 1 of 1 · client-a"}, out["guest_slot"]


def test_a_project_with_no_slack_files_shows_two_setup_choices(hub, tmp_path):
    out = read(hub, tmp_path, committed={}, local={})["tree"]
    root = out["root"]
    assert out["empty"] is True and root["state"] == "empty"
    assert root["now"] == []
    assert [c["label"] for c in root["setup"]] == ["Add owned workspace", "Add guest workspace"], root.get("setup")


def test_the_read_route_matches_the_policy_for_each_node(hub, tmp_path):
    """The one read route answers what `slackpolicy` answers for an owned channel, a guest channel, a listen channel
    and a refused guest workspace. The route computes no rule of its own (ADR-0137)."""
    import sys
    sys.path.insert(0, str(ROOT))
    from squad import slackpolicy as P
    local_fixture = json.loads(json.dumps(LOCAL))
    local_fixture["workspaces"].append({"id": "T0OTHER001", "label": "client-b", "class": "guest",
                                        "channels": [{"id": "C0CLIB0001", "name": "b-general",
                                                      "mode": "listen+speak"}]})
    c, loc = files(tmp_path, local=local_fixture)
    committed, local = yaml.safe_load(c.read_text()), yaml.safe_load(loc.read_text())
    root = hub.slack_overview(c, loc)["tree"]["root"]
    for name, ws_id, ch_id, user in (("#proj-agent-squad", "T0TEAM0001", "C0PROJ0001", "U0OWNER001"),
                                     ("#support", "T0CLIENT01", "C0CLISUP01", "U0CLIENT01"),
                                     ("#squad-alerts", "T0TEAM0001", "C0ALERT001", "U0OWNER001")):
        node = by_name(root, name)
        ws = next(w for w in P.rows(committed.get("workspaces")) + P.rows(local.get("workspaces"))
                  if str(w.get("id")) == ws_id)
        ch = P.channel_row(local, ws_id, ch_id) or next(c for c in P.rows(ws.get("channels"))
                                                        if str(c.get("id")) == ch_id)
        decision = P.decide(committed, local, {"workspace": ws_id, "channel": ch_id, "user": user}, NOW)
        language, persona = P.effective(committed, local, ws, ch)
        now = tiles(node)
        assert now["Language"]["value"] == language, (name, now["Language"])
        assert now["Persona"]["value"] == (persona.name if persona else "none"), (name, now["Persona"])
        assert now["Reply"]["value"] == P.reply_label(ch, decision), (name, now["Reply"], decision.reply)
        assert now["A member's message"]["value"] == P.destination_label(decision), (name, decision.kind)
    assert [b["text"] for b in by_name(root, "client-b")["badges"]] == ["guest", "refused"]
    assert by_name(root, "client-b")["defects"] == [str(d) for d in P.validate(committed, local,
                                                                               local_name="t.slack.local.yaml")
                                                    if d.reason == P.SECOND_GUEST_REASON]


# ---------------------------------------------------------------- the renderer

def _node(body):
    p = subprocess.run(["node", "-"], input=body, capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    return p.stdout


def _slices(page):
    esc = re.search(r"^const esc=.*$", page, re.M).group(0)
    sett = page[page.index("// SETT{"):page.index("// }SETT")]
    sl = page[page.index("let slS=null"):page.index("async function slLoad()")]
    stub = 'const STORE={};const $=s=>STORE[s]||(STORE[s]={innerHTML:"",textContent:""});'
    return "\n".join([stub, esc, sett, sl])


def render(hub, out, sel=None):
    body = (_slices(hub.PAGE) + f"\nslS={{}};slR={json.dumps(out)};" + (f"slTsel={json.dumps(sel)};" if sel else "") + "slRender();"
            "console.log(JSON.stringify(STORE['#sbody'].innerHTML))")
    return json.loads(_node(body))


def test_the_tree_follows_the_wai_aria_pattern(hub, tmp_path):
    html = render(hub, read(hub, tmp_path))
    assert 'role=tree aria-label="Communication tree"' in html
    items = re.findall(r"<div[^>]*role=treeitem[^>]*>", html)
    assert items and all("aria-level=" in i for i in items), items[:2]
    assert sum('tabindex="0"' in i for i in items) == 1, items     # one tab stop
    assert 'aria-expanded="true"' in html and 'aria-selected="true"' in html


def test_a_tree_item_accessible_name_carries_its_badges(hub, tmp_path):
    html = render(hub, read(hub, tmp_path))
    items = re.findall(r"<div[^>]*role=treeitem[^>]*>", html)
    team = next(i for i in items if 'id="slt_w0"' in i)
    assert 'aria-label="team, owned"' in team, team


def test_the_tree_is_the_keyboard_tree_of_the_view(hub, tmp_path):
    """Arrow keys move and fold, Home and End jump: the movement is one pure function, run here in node."""
    out = read(hub, tmp_path)
    body = (_slices(hub.PAGE) + f"\nslR={json.dumps(out)};slT=slR.tree;slTsel='p';"
            "const f=slTFlat();"
            "const at=(cur,k)=>slTreeKey(f,cur,k);"
            "console.log(JSON.stringify({ids:f.map(x=>x.id),down:at('p','ArrowDown'),up:at(f[f.length-1].id,'ArrowUp'),"
            "home:at(f[3].id,'Home'),end:at('p','End'),right:at('w0','ArrowRight'),left:at('w0','ArrowLeft'),"
            "leftChild:at('w0.c0','ArrowLeft')}))")
    got = json.loads(_node(body))
    assert got["ids"] == ["p", "w0", "w0.c0", "w0.c1", "w1", "w1.c0", "w1.c1"], got["ids"]
    assert got["down"] == {"focus": "w0"}
    assert got["home"] == {"focus": "p"}
    assert got["end"] == {"focus": "w1.c1"}
    assert got["up"] == {"focus": "w1.c0"}
    assert got["right"] == {"focus": "w0.c0"}, got["right"]          # an open node moves to its first child
    assert got["left"] == {"focus": "w0", "toggle": "w0"}, got["left"]  # an open node folds
    assert got["leftChild"] == {"focus": "w0"}


def test_a_long_channel_name_is_cut_and_kept_whole(hub, tmp_path):
    committed = json.loads(json.dumps(COMMITTED))
    committed["workspaces"][0]["channels"][0]["name"] = "proj-agent-squad-with-a-very-long-name-that-runs-past-the-tree"
    html = render(hub, read(hub, tmp_path, committed=committed))
    full = "#proj-agent-squad-with-a-very-long-name-that-runs-past-the-tree"
    name = re.search(r'<span class=sltname title="(' + re.escape(full) + r')"', html)
    assert name and name.group(1) == full, name
    assert f'aria-label="{full}' in html, "the accessible name keeps the full name"


def test_arrow_left_folds_a_node_that_was_never_toggled(hub, tmp_path):
    """ArrowLeft on an open node folds it on the first press, whether or not the owner ever toggled it (WebDriver #2843).
    An unfolded node reads as open, so the toggle must flip the state the row shows, never the absent entry."""
    out = read(hub, tmp_path)
    body = (_slices(hub.PAGE) + f"\nslS={{}};slR={json.dumps(out)};slT=slR.tree;slTsel='w0';slTopen={{}};"
            "const before=slTFlat().map(x=>x.id);"
            "const openBefore=slTOpen(slTNode('w0'));"
            "slTGo(slTreeKey(slTFlat(),'w0','ArrowLeft'));"
            "console.log(JSON.stringify({before,openBefore,after:slTFlat().map(x=>x.id),openAfter:slTOpen(slTNode('w0'))}))")
    got = json.loads(_node(body))
    assert got["openBefore"] is True, "an untouched node reads as open before the keypress"
    assert got["before"] == ["p", "w0", "w0.c0", "w0.c1", "w1", "w1.c0", "w1.c1"], got["before"]
    assert got["after"] == ["p", "w0", "w1", "w1.c0", "w1.c1"], got["after"]
    assert got["openAfter"] is False, "the first ArrowLeft press folds the never-toggled node"


def test_a_pick_at_phone_width_keeps_the_keyboard_focus_in_the_detail_pane(hub, tmp_path):
    """A phone folds the tree on a pick, so the picked tree item has no box and takes no focus: focus moves to the
    detail pane instead of falling to BODY (WebDriver #2843). A picked row that still has a box keeps the focus."""
    out = read(hub, tmp_path)
    base = (_slices(hub.PAGE) + f"\nslS={{}};slR={json.dumps(out)};slTsel='p';"
            "const focused=[];"
            "const sbody={getBoundingClientRect(){return {width:390}}};"
            "const detailEl={focus(){focused.push('detail')},getClientRects(){return [{top:0,left:0,width:1,height:1}]}};")
    folded = base + (
            "const treeEl={focus(){focused.push('tree')},getClientRects(){return []}};"   # inside the folded column: no box
            "globalThis.document={querySelectorAll:()=>[],getElementById:id=>id==='sbody'?sbody:"
            "id==='slt_w0.c0'?treeEl:id==='sltdetail'?detailEl:null};"
            "slTPick('w0.c0');"
            "console.log(JSON.stringify({focused,sel:slTsel,show:slTshow}))")
    got = json.loads(_node(folded))
    assert got["sel"] == "w0.c0"
    assert got["show"] is True, "a pick at phone width folds the tree"
    assert got["focused"] == ["detail"], got

    shown = base + (
            "const rowEl={focus(){focused.push('tree')},getClientRects(){return [{top:0,left:0,width:1,height:1}]}};"
            "globalThis.document={querySelectorAll:()=>[],getElementById:id=>id==='sbody'?sbody:"
            "id==='slt_w0.c0'?rowEl:id==='sltdetail'?detailEl:null};"
            "slTPick('w0.c0');"
            "console.log(JSON.stringify({focused,sel:slTsel,show:slTshow}))")
    tree = json.loads(_node(shown))
    assert tree["focused"] == ["tree"], "a picked row that still has a box keeps the focus"


def test_at_600_px_the_tree_shows_until_the_first_selection_and_the_now_block_keeps_one_tile(hub, tmp_path):
    page = hub.PAGE
    assert re.search(r"\.slttop:not\(\.sltshow\) \.sltdetail\{display:none\}", page), \
        "the phone shows the tree until the first selection"
    assert re.search(r"\.slttop\.sltshow \.sltcol\{display:none\}", page), \
        "the phone folds the tree after the first selection"
    assert not re.search(r":not\(\.sltshow\) \.sltcol\{display:none\}", page), \
        "the tree is never hidden before a selection"
    assert re.search(r"#tab-set #sbody \.sltnow\{grid-template-columns:1fr\}", page), \
        "the phone keeps one tile"
    assert re.search(r"@container \(min-width:701px\)\{#sbody \.slttop\.sltshow \.sltcol\{display:block\}\}", page), \
        "a wide panel keeps the tree beside the detail through every selection"


def test_a_pick_on_a_wide_panel_keeps_the_tree_beside_the_detail(hub, tmp_path):
    """The fold belongs to a narrow panel alone: a pick at 1440 px leaves `slTshow` false so the tree stays paneled
    beside the detail, and the wide rule keeps its column visible (WebDriver #2843)."""
    out = read(hub, tmp_path)
    body = (_slices(hub.PAGE) + f"\nslS={{}};slR={json.dumps(out)};slTsel='p';"
            "const wide={getBoundingClientRect(){return {width:1440}}};"
            "globalThis.document={querySelectorAll:()=>[],getElementById:id=>id==='sbody'?wide:null};"
            "slTPick('w0.c0');"
            "console.log(JSON.stringify({sel:slTsel,show:slTshow}))")
    got = json.loads(_node(body))
    assert got["sel"] == "w0.c0"
    assert got["show"] is False, "a wide panel never folds the tree on a pick"


def _item(html, node_id):
    return re.search(r'<div role=treeitem id="slt_' + re.escape(node_id) + r'".*?</div>', html, re.S).group(0)


def test_the_tree_draws_indent_guides_and_level_icons_and_no_glyph_fallback(hub, tmp_path):
    html = render(hub, read(hub, tmp_path))
    assert "⌂" not in html and "▣" not in html, "the house and square glyphs are gone"
    assert "<svg class=sltkind" in _item(html, "p"), "the project carries a level icon"
    assert "<svg class=sltkind" in _item(html, "w0"), "the workspace carries a level icon"
    assert _item(html, "p").count('class="sltg') == 0, "the project is the root of the tree"
    assert _item(html, "w0").count('class="sltg') == 1, "the workspace draws one guide"
    assert _item(html, "w0.c0").count('class="sltg') == 2, "a channel draws one guide per ancestor level"
    # The line runs only while its ancestor has another sibling after it, so a subtree's rule ends at its last child.
    assert 'class="sltg more"' in _item(html, "w0"), "the project's rule continues past a workspace with one after it"
    assert 'more' not in _item(html, "w1"), "the project's last workspace ends the rule"
    last = _item(html, "w1.c1")
    assert last.count('class="sltg') == 2 and "more" not in last, "the last channel ends both rules"


def test_a_guest_or_dm_channel_shows_the_chat_icon_and_the_other_channels_a_hash(hub, tmp_path):
    """A direct message of an owned workspace has no guest class, so its own Slack id (a `D` id) reads as a chat, and a
    regular channel keeps the hash (mockup, #2843)."""
    committed = json.loads(json.dumps(COMMITTED))
    committed["workspaces"][0]["channels"].append({"id": "D0DM00001", "name": "owner-dm", "mode": "listen"})
    root = tree(hub, tmp_path, committed=committed)["root"]
    assert by_name(root, "#support")["icon"] == "chat", "a guest channel shows the chat icon"
    assert by_name(root, "#owner-dm")["icon"] == "chat", "a direct message shows the chat icon"
    assert by_name(root, "#proj-agent-squad")["icon"] == "hash", "another channel shows the hash"
    html = render(hub, read(hub, tmp_path, committed=committed))
    assert "<svg class=sltkind" in _item(html, "w0.c2"), _item(html, "w0.c2")   # the DM channel
    assert '<span class=sltkind aria-hidden=true>#</span>' in _item(html, "w0.c0")


# ---------------------------------------------------------------- the detail pane's width and type (#2841)

def _rule(page, head):
    """The one CSS rule body whose selector starts with `head`, so a test reads the declaration it names."""
    m = re.search(re.escape(head) + r"\{([^}]*)\}", page)
    assert m, f"no rule starts with {head!r}"
    return m.group(1)


def _token(css, name):
    m = re.search(r"--" + re.escape(name) + r":(\d+)px", css)
    assert m, f"the tokens name no {name}"
    return int(m.group(1))


def test_at_a_desktop_width_the_now_tiles_fill_one_row(hub, tmp_path):
    """At 1440 px a channel shows its five Now tiles in one row and a workspace its four (mockup, #2841). The pane's own
    grid must outrank the generic two-column `.now`, so the rule carries both ids."""
    page = hub.PAGE
    assert re.search(r"#tab-set #sbody \.sltn5\{grid-template-columns:repeat\(5,minmax\(0,1fr\)\)", page), \
        "the channel's five tiles take one row"
    assert re.search(r"#tab-set #sbody \.sltn4\{grid-template-columns:repeat\(4,minmax\(0,1fr\)\)", page), \
        "the workspace's four tiles take one row"
    assert re.search(r"#tab-set #sbody \.sltnow\{grid-template-columns:repeat\(auto-fit,minmax\(120px,1fr\)\)", page), \
        "a count the block does not name still fills one row"
    channel = render(hub, read(hub, tmp_path), sel="w0.c0")
    assert "now sltnow sltn5" in channel, "a five-tile block names five columns"
    workspace = render(hub, read(hub, tmp_path), sel="w0")
    assert "now sltnow sltn4" in workspace, "a four-tile block names four columns"


def test_a_folded_detail_keeps_three_tiles_and_a_phone_one(hub, tmp_path):
    """A panel under 700 px has no room for five tiles: three keep every label whole. A phone shows one tile a row."""
    page = hub.PAGE
    assert re.search(r"@container \(max-width:700px\)\{[^@]*#tab-set #sbody \.sltnow\{grid-template-columns:repeat\(3,minmax\(0,1fr\)\)", page), \
        "a folded detail keeps three tiles"
    assert re.search(r"@media \(max-width:600px\)\{[^@]*#tab-set #sbody \.sltnow\{grid-template-columns:1fr\}", page), \
        "a phone keeps one tile"


def test_a_tile_value_breaks_between_words_and_never_inside_one(hub):
    """`overflow-wrap:anywhere` cuts a word at the pane's edge; the pane's own rule breaks between words alone (#2841)."""
    rule = _rule(hub.PAGE, "#tab-set #sbody .sltnow .tile .tv")
    assert "overflow-wrap:break-word" in rule, rule
    assert "anywhere" not in rule, rule
    assert "hyphens:none" in rule or "word-break:normal" in rule, rule


def _sized(size_rule):
    """The token a `font-size:var(--X)` declaration names, so a test compares sizes the tokens define, not their spelling."""
    m = re.search(r"font-size:var\(--([a-z-]+)\)", size_rule)
    assert m, f"no font-size token in {size_rule!r}"
    return m.group(1)


def test_the_node_title_is_the_largest_text_of_the_detail_pane(hub):
    """The title names a token the tokens define and resolves to a size larger than the pane's own tile value, so it is
    the largest text of the detail pane (#2841). The comparison reads only `#sbody` rules, so a settings-pane selector
    cannot stand in for the detail pane's value."""
    page = hub.PAGE
    assert "var(--fs-title)" not in page, "no pane rule names a size the tokens never define"
    title_size = _sized(_rule(page, "#sbody .slth"))
    value_size = _sized(_rule(page, "#tab-set #sbody .sltnow .tile .tv"))
    tokens = (ROOT / "contrib" / "squad-goal" / "scripts" / "web-tokens.css").read_text()
    assert _token(tokens, "fs-display") > _token(tokens, "fs-heading") > _token(tokens, "fs-body") > _token(tokens, "fs-caption")
    assert title_size == "fs-display", title_size
    assert _token(tokens, title_size) > _token(tokens, value_size), \
        f"the title {title_size} must be larger than the tile value {value_size}"


def test_the_tile_label_is_muted_and_its_value_bold(hub):
    """The label is a muted caption and the value is bold, as the mockup draws them (#2841)."""
    page = hub.PAGE
    assert re.search(r"#tab-set #sbody \.sltnow \.tile \.tk\{[^}]*color:var\(--mute\)", page), "the label is muted"
    rule = _rule(page, "#tab-set #sbody .sltnow .tile .tv")
    assert "font-weight:600" in rule, rule


def test_a_project_level_value_is_never_green(hub, tmp_path):
    """A count of the whole project is a fact, not a passing state: every project tile stays neutral (mockup, #2841)."""
    root = tree(hub, tmp_path)["root"]
    assert root["now"], "the project node carries its Now tiles"
    assert [t["tone"] for t in root["now"]] == ["neutral"] * len(root["now"]), root["now"]


def test_the_edit_button_of_the_personas_table_stays_on_one_line(hub):
    """A stacked Personas row at a narrow panel keeps `Edit` on one line and inside its own cell, so neither the word nor
    the button is cut (#2841)."""
    rule = _rule(hub.PAGE, "#sbody .slt2 td button")
    assert "white-space:nowrap" in rule, rule
    assert "justify-self:start" in rule, rule
    assert "min-width:0" in rule, rule


# ---------------------------------------------------------------- the permissions and the sender preview

def permissions(hub, tmp_path, name="#proj-agent-squad", committed=None, local=None):
    return by_name(tree(hub, tmp_path, committed, local)["root"], name)["permissions"]


def _sender_type(local, workspace, channel, user):
    """The sender kind the preview asks for: a member when the local file lists the user in this channel, a stranger
    otherwise. The route checks the kind against the channel's own resolved member list."""
    for row in (local or {}).get("members") or ():
        if (str(row.get("workspace")), str(row.get("channel"))) == (str(workspace), str(channel)) \
                and str(user) in {str(u) for u in row.get("users") or ()}:
            return "member"
    return "stranger"


def preview(hub, tmp_path, committed=None, local=None, **query):
    c, loc = files(tmp_path, committed, local)
    if "sender_type" not in query:
        query["sender_type"] = "bot" if hub._slack_flag(query.get("bot")) else \
            _sender_type(LOCAL if local is None else local, query.get("workspace", ""), query.get("channel", ""),
                         query.get("user", ""))
    return hub.slack_decide(query, c, loc)


def test_the_permissions_section_lists_the_project_roles_with_their_actions_and_members(hub, tmp_path):
    node = by_name(tree(hub, tmp_path)["root"], "#proj-agent-squad")
    assert node["ref"] == {"workspace": "T0TEAM0001", "channel": "C0PROJ0001"}, node.get("ref")
    roles = {r["id"]: r for r in node["permissions"]["roles"]}
    assert list(roles) == ["manager", "colleague"], node["permissions"]["roles"]
    assert roles["manager"]["actions"] == ["status", "logs"]
    assert roles["colleague"]["actions"] == ["status"]
    assert roles["manager"]["accepted"] is True
    assert roles["colleague"]["accepted"] is False
    assert roles["manager"]["members"] == ["U0OWNER001"]
    assert roles["colleague"]["members"] == []


def test_the_section_counts_distinct_eligible_people(hub, tmp_path):
    local = json.loads(json.dumps(LOCAL))
    local["members"].append({"workspace": "T0TEAM0001", "channel": "C0PROJ0001", "role": "manager",
                             "users": ["U0OWNER001", "U0OTHER001"]})
    assert permissions(hub, tmp_path, local=local)["eligible"] == 2


def test_a_person_in_two_accepted_roles_gets_a_note_naming_the_membership_the_policy_takes(hub, tmp_path):
    committed = json.loads(json.dumps(COMMITTED))
    committed["workspaces"][0]["channels"][0]["roles"] = ["manager", "colleague"]
    local = json.loads(json.dumps(LOCAL))
    local["members"].append({"workspace": "T0TEAM0001", "channel": "C0PROJ0001", "role": "colleague",
                             "users": ["U0OWNER001"]})
    notes = permissions(hub, tmp_path, committed=committed, local=local)["notes"]
    assert len(notes) == 1 and "U0OWNER001" in notes[0] and "manager" in notes[0], notes
    decided = preview(hub, tmp_path, committed, local, workspace="T0TEAM0001", channel="C0PROJ0001", user="U0OWNER001")
    assert decided["role"] == "manager", decided


def test_the_preview_answers_an_owned_member_a_guest_member_a_stranger_a_bot_and_an_own_post(hub, tmp_path):
    owned = preview(hub, tmp_path, workspace="T0TEAM0001", channel="C0PROJ0001", user="U0OWNER001")
    assert owned == {"err": None, "decision": "inbox", "role": "manager", "actions": "status, logs",
                     "reply": "bot", "mark": "*HAL9K's Msg*", "language": "pt-BR"}, owned
    guest = preview(hub, tmp_path, workspace="T0CLIENT01", channel="C0CLISUP01", user="U0CLIENT01")
    assert guest == {"err": None, "decision": "proposal", "role": "manager", "actions": "status, logs",
                     "reply": "draft", "mark": "*Iara's Msg*", "language": "en-US"}, guest
    stranger = preview(hub, tmp_path, workspace="T0TEAM0001", channel="C0PROJ0001", user="U0STRANGER")
    assert stranger == {"err": None, "decision": "data", "role": "none", "actions": "none",
                        "reply": "none", "mark": "*HAL9K's Msg*", "language": "pt-BR"}, stranger
    bot = preview(hub, tmp_path, workspace="T0TEAM0001", channel="C0PROJ0001", user="U0OWNER001", bot="1")
    assert bot == {"err": None, "decision": "data", "role": "manager", "actions": "none",
                   "reply": "none", "mark": "*HAL9K's Msg*", "language": "pt-BR"}, bot
    own = preview(hub, tmp_path, workspace="T0TEAM0001", channel="C0PROJ0001", user="U0OWNER001", own="1")
    assert own["decision"] == "ignore" and own["reply"] == "none", own


def test_the_preview_calls_decide_with_one_message_event_and_no_other_call(hub, tmp_path, monkeypatch):
    P = hub.slack_policy()
    seen, real = [], P.decide

    def spy(committed, local, event, now, thread_state=None):
        seen.append(dict(event))
        return real(committed, local, event, now, thread_state)

    monkeypatch.setattr(P, "decide", spy)
    monkeypatch.setattr(hub, "slack_policy", lambda: P)
    preview(hub, tmp_path, workspace="T0TEAM0001", channel="C0PROJ0001", user="U0OWNER001")
    assert [e["kind"] for e in seen] == ["message"], seen


def test_a_reply_with_no_persona_shows_the_policy_mark_and_no_empty_name(hub, tmp_path):
    committed = json.loads(json.dumps(COMMITTED))
    committed["project"].pop("persona")
    P = hub.slack_policy()
    expected = P.mark(None) or "none"
    got = preview(hub, tmp_path, committed, guest_workspace(persona=None),
                  workspace="T0CLIENT01", channel="C0CLISUP01", user="U0CLIENT01")
    assert got["reply"] == "draft" and got["mark"] == expected, got
    assert expected == "none", "the fixture holds no persona, so the policy mark is empty"
    assert "'s Msg" not in json.dumps(got), got


def test_the_preview_shows_a_non_empty_policy_mark_and_never_a_shortcut_none(hub, tmp_path):
    """A stranger in a channel with a persona keeps that persona's mark. The mark is the policy's answer for the
    resolved persona, never a value the reply path chooses (AC9)."""
    P = hub.slack_policy()
    c, loc = files(tmp_path)
    doc_c, doc_l, _ = P.documents(c, loc)
    ws = next(w for w in doc_l["workspaces"] if w["id"] == "T0CLIENT01")
    ch = next(ch for ch in ws["channels"] if ch["id"] == "C0CLISUP01")
    expected = P.mark(P.effective_persona(doc_c, doc_l, ws, ch))
    assert expected == "*Iara's Msg*", expected
    got = preview(hub, tmp_path, workspace="T0CLIENT01", channel="C0CLISUP01", user="U0STRANGER")
    assert got["mark"] == expected and got["mark"] != "none", got


def test_the_preview_route_refuses_an_unknown_sender_type(hub, tmp_path):
    got = preview(hub, tmp_path, workspace="T0TEAM0001", channel="C0PROJ0001", user="U0OWNER001",
                  sender_type="owner")
    assert got.get("err"), got


def test_the_preview_route_binds_a_member_sender_to_the_channel_member_list(hub, tmp_path):
    got = preview(hub, tmp_path, workspace="T0TEAM0001", channel="C0PROJ0001", user="U0STRANGER",
                  sender_type="member")
    assert got.get("err"), got


def test_the_preview_route_refuses_a_stranger_that_the_channel_lists_as_a_member(hub, tmp_path):
    got = preview(hub, tmp_path, workspace="T0TEAM0001", channel="C0PROJ0001", user="U0OWNER001",
                  sender_type="stranger")
    assert got.get("err"), got


def test_the_preview_route_reads_an_unknown_workspace_or_channel_as_unknown(hub, tmp_path):
    ws = preview(hub, tmp_path, workspace="T0NOPE", channel="C0NOPE", user="U0X", sender_type="stranger")
    assert ws["decision"] == "unknown" and ws["err"], ws
    ch = preview(hub, tmp_path, workspace="T0TEAM0001", channel="C0NOPE", user="U0X", sender_type="stranger")
    assert ch["decision"] == "unknown" and ch["err"], ch


def test_the_preview_senders_are_the_accepted_members_only(hub, tmp_path):
    """The `members` list feeds the sender options, so it holds the same accepted people the eligibility counts, never
    a user the channel's accepted roles do not grant (AC3, AC6)."""
    local = json.loads(json.dumps(LOCAL))
    local["members"].append({"workspace": "T0TEAM0001", "channel": "C0PROJ0001", "role": "colleague",
                             "users": ["U0NOTELIG1"]})
    got = permissions(hub, tmp_path, local=local)
    assert got["members"] == ["U0OWNER001"], got
    assert got["eligible"] == len(got["members"]), got


def test_the_dual_role_note_names_the_membership_the_policy_decides(hub, tmp_path):
    """The note derives the taken membership from the same resolution `decide` runs, never from a local list order
    (AC4)."""
    committed = json.loads(json.dumps(COMMITTED))
    committed["workspaces"][0]["channels"][0]["roles"] = ["manager", "colleague"]
    local = json.loads(json.dumps(LOCAL))
    local["members"].insert(0, {"workspace": "T0TEAM0001", "channel": "C0PROJ0001", "role": "colleague",
                                "users": ["U0OWNER001"]})
    notes = permissions(hub, tmp_path, committed=committed, local=local)["notes"]
    assert len(notes) == 1, notes
    decided = preview(hub, tmp_path, committed, local,
                      workspace="T0TEAM0001", channel="C0PROJ0001", user="U0OWNER001")
    assert f"The policy takes {decided['role']}" in notes[0], (notes[0], decided)


class _Dom(HTMLParser):
    """A parsed view of the rendered pane: each start tag with its attributes and its open ancestors. The test reads
    the DOM the page builds, never a string of its source."""
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

    def __init__(self):
        super().__init__()
        self._stack, self.elements = [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.elements.append((tag, a, list(self._stack)))
        if tag not in self.VOID:
            self._stack.append((tag, a))

    def handle_startendtag(self, tag, attrs):
        self.elements.append((tag, dict(attrs), list(self._stack)))

    def handle_endtag(self, tag):
        if any(t == tag for t, _ in self._stack):
            while self._stack and self._stack.pop()[0] != tag:
                pass


def _pane_elements(hub, tmp_path):
    dom = _Dom()
    dom.feed(render(hub, read(hub, tmp_path), sel="w0.c0"))
    return dom.elements


def test_the_tree_page_links_the_permissions_section_and_offers_the_sender_preview(hub, tmp_path):
    """AC5 and AC6, read from the parsed pane: the Permissions link, its focus target, one sender option per accepted
    member plus a stranger and a bot, and the read route the preview fetches."""
    elements = _pane_elements(hub, tmp_path)
    assert [a for t, a, _ in elements if t == "a" and a.get("href") == "#slperm" and "data-slt-perm" in a], \
        "the pane links the Permissions section"
    assert [a for t, a, _ in elements if t == "h3" and a.get("id") == "slperm" and a.get("tabindex") == "-1"], \
        "the Permissions heading is the focus target"
    select = [a for t, a, _ in elements if t == "select" and "data-slp-sender" in a]
    assert len(select) == 1, select
    options = [a.get("value") for t, a, parents in elements
               if t == "option" and any(p == "select" and "data-slp-sender" in pa for p, pa in parents)]
    assert options == ["U0OWNER001", "stranger", "bot"], options

    out = read(hub, tmp_path)
    body = (_slices(hub.PAGE) + f"\nslR={json.dumps(out)};slT=slR.tree;slTsel='w0.c0';" + "slRender=()=>{};"
            "const urls=[];globalThis.document={querySelector:()=>null};"
            "globalThis.fetch=async u=>{urls.push(String(u));return {json:async()=>({decision:'x'})}};"
            "slPvAsk('w0.c0','U0OWNER001').then(()=>slPvAsk('w0.c0','stranger'))"
            ".then(()=>slPvAsk('w0.c0','bot'))"
            ".then(()=>console.log(JSON.stringify(urls)))")
    urls = json.loads(_node(body))
    assert all(u.startswith("/api/slack/decide?") for u in urls), urls
    member, stranger, bot = (dict(urllib.parse.parse_qsl(u.partition("?")[2])) for u in urls)
    assert member == {"workspace": "T0TEAM0001", "channel": "C0PROJ0001", "sender_type": "member",
                      "user": "U0OWNER001"}, member
    assert stranger == {"workspace": "T0TEAM0001", "channel": "C0PROJ0001", "sender_type": "stranger"}, stranger
    assert bot == {"workspace": "T0TEAM0001", "channel": "C0PROJ0001", "sender_type": "bot"}, bot


def test_the_sender_preview_drops_a_stale_reading_state_for_a_sender_the_channel_no_longer_offers(hub, tmp_path):
    """A sender the channel no longer lists falls back to its first option, so the pane never keeps "Reading the
    decision." for a request it never made (AC6)."""
    out = read(hub, tmp_path)
    body = (_slices(hub.PAGE) + f"\nslR={json.dumps(out)};slT=slR.tree;slTsel='w1.c0';"
            "slSnd={node:'w1.c0',sender:'U0GONE',data:null};slRender();"
            "const html=STORE['#sbody'].innerHTML;"
            "const box=html.match(/<select data-slp-sender=\"[^\"]*\"[^>]*>([\\s\\S]*?)<\\/select>/);"
            "console.log(JSON.stringify({reading:html.includes('Reading the decision.'),"
            "selected:(box&&box[1].match(/value=\"([^\"]+)\" selected/)||[])[1]}))")
    got = json.loads(_node(body))
    assert got["reading"] is False, got
    assert got["selected"] == "U0CLIENT01", got


def test_the_permissions_link_focuses_the_heading_and_scrolls_only_when_it_is_out_of_view(hub):
    """One scroll mechanism: the focus carries `preventScroll`, and `scrollIntoView` runs only for a heading outside
    the viewport (AC5)."""
    base = _slices(hub.PAGE)
    seen = base + (
        "const calls=[];const el={focus:o=>calls.push(['focus',o]),scrollIntoView:o=>calls.push(['scroll',o]),"
        "getBoundingClientRect:()=>({top:100,bottom:140})};"
        "globalThis.document={getElementById:id=>id==='slperm'?el:null};globalThis.innerHeight=900;"
        "slPermGo();console.log(JSON.stringify(calls))")
    assert json.loads(_node(seen)) == [["focus", {"preventScroll": True}]]
    hidden = base + (
        "const calls=[];const el={focus:o=>calls.push(['focus',o]),scrollIntoView:o=>calls.push(['scroll',o]),"
        "getBoundingClientRect:()=>({top:900,bottom:940})};"
        "globalThis.document={getElementById:id=>id==='slperm'?el:null};globalThis.innerHeight=900;"
        "slPermGo();console.log(JSON.stringify(calls))")
    assert json.loads(_node(hidden)) == [["focus", {"preventScroll": True}], ["scroll", {"block": "start"}]]


def test_a_refused_guest_workspace_reads_not_read_in_the_preview(hub, tmp_path):
    local = json.loads(json.dumps(LOCAL))
    local["workspaces"].append({"id": "T0OTHER001", "label": "client-b", "class": "guest",
                                "channels": [{"id": "C0CLIB0001", "name": "b-general", "mode": "listen+speak"}]})
    got = preview(hub, tmp_path, local=local, workspace="T0OTHER001", channel="C0CLIB0001", user="U0CLIENT01")
    assert got["decision"] == "not read", got
