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
            "slTGo(slTreeKey(slTFlat(),'w0','ArrowLeft'));"
            "console.log(JSON.stringify({before,after:slTFlat().map(x=>x.id)}))")
    got = json.loads(_node(body))
    assert got["before"] == ["p", "w0", "w0.c0", "w0.c1", "w1", "w1.c0", "w1.c1"], got["before"]
    assert got["after"] == ["p", "w0", "w1", "w1.c0", "w1.c1"], got["after"]


def test_a_pick_at_phone_width_keeps_the_keyboard_focus_in_the_detail_pane(hub, tmp_path):
    """A phone folds the tree on a pick, so the picked tree item has no box and takes no focus: focus moves to the
    detail pane instead of falling to BODY (WebDriver #2843)."""
    out = read(hub, tmp_path)
    body = (_slices(hub.PAGE) + f"\nslS={{}};slR={json.dumps(out)};slTsel='p';"
            "const focused=[];"
            "const treeEl={focus(){focused.push('tree')},getClientRects(){return []}};"   # inside the folded column: no box
            "const detailEl={focus(){focused.push('detail')},getClientRects(){return [{top:0,left:0,width:1,height:1}]}};"
            "globalThis.document={querySelectorAll:()=>[],getElementById:id=>id==='slt_w0.c0'?treeEl:id==='sltdetail'?detailEl:null};"
            "slTPick('w0.c0');"
            "console.log(JSON.stringify({focused,sel:slTsel}))")
    got = json.loads(_node(body))
    assert got["sel"] == "w0.c0"
    assert got["focused"] == ["detail"], got


def test_at_600_px_the_tree_shows_until_the_first_selection_and_the_now_block_keeps_two_tiles(hub, tmp_path):
    page = hub.PAGE
    assert re.search(r"\.slttop:not\(\.sltshow\) \.sltdetail\{display:none\}", page), \
        "the phone shows the tree until the first selection"
    assert re.search(r"\.slttop\.sltshow \.sltcol\{display:none\}", page), \
        "the phone folds the tree after the first selection"
    assert not re.search(r":not\(\.sltshow\) \.sltcol\{display:none\}", page), \
        "the tree is never hidden before a selection"
    assert re.search(r"#tab-set #sbody \.sltnow\{grid-template-columns:repeat\(2,minmax\(0,1fr\)\)\}", page), \
        "the phone keeps two tiles"


def _item(html, node_id):
    return re.search(r'<div role=treeitem id="slt_' + re.escape(node_id) + r'".*?</div>', html, re.S).group(0)


def test_the_tree_draws_indent_guides_and_level_icons_and_no_glyph_fallback(hub, tmp_path):
    html = render(hub, read(hub, tmp_path))
    assert "⌂" not in html and "▣" not in html, "the house and square glyphs are gone"
    assert "<svg class=sltkind" in _item(html, "p"), "the project carries a level icon"
    assert "<svg class=sltkind" in _item(html, "w0"), "the workspace carries a level icon"
    assert _item(html, "p").count("class=sltg") == 0, "the project is the root of the tree"
    assert _item(html, "w0").count("class=sltg") == 1, "the workspace draws one guide"
    assert _item(html, "w0.c0").count("class=sltg") == 2, "a channel draws one guide per ancestor level"


def test_a_guest_or_dm_channel_shows_the_chat_icon_and_the_other_channels_a_hash(hub, tmp_path):
    local = json.loads(json.dumps(LOCAL))
    local["workspaces"][0]["channels"].append({"id": "D0DM00001", "name": "owner-dm", "mode": "listen"})
    root = tree(hub, tmp_path, local=local)["root"]
    assert by_name(root, "#support")["icon"] == "chat", "a guest channel shows the chat icon"
    assert by_name(root, "#owner-dm")["icon"] == "chat", "a direct message shows the chat icon"
    assert by_name(root, "#proj-agent-squad")["icon"] == "hash", "another channel shows the hash"
    html = render(hub, read(hub, tmp_path, local=local))
    assert "<svg class=sltkind" in _item(html, "w1.c2"), _item(html, "w1.c2")   # the DM channel
    assert '<span class=sltkind aria-hidden=true>#</span>' in _item(html, "w0.c0")
