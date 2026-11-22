"""GitHub issue #2698: the events editor of the Communication theme in the hub Settings page.

The owner declares the project events, one template for each, the `announce` list of each channel, and a template
override for one event in one channel. The events and the templates are committed facts, so the editor saves them in
`docs/squad/slack.yaml` through the save path of #2694: the hub validates both files, shows the diff, and lands the
committed file in the hub's own worktree. The rules stay in the policy module (ADR-0177), so the refusal of an unknown
event and of a token in a template come from it, and the hub holds no copy of a rule (ADR-0137).

The seams under test are the policy module's `validate`, the hub's own save function `slack_files_set`, and the
theme's renderer and edit functions run in node. No browser runs here. Every id is a placeholder, and every file is a
temporary fixture.
"""
import copy
import importlib.util
import json
import pathlib
import re
import subprocess
import tempfile

import pytest
import yaml

from squad import slackpolicy as P

ROOT = pathlib.Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib" / "squad-goal" / "scripts" / "hub.py"

COMMITTED = {
    "version": 1,
    "project": {"language": "en-US", "persona": "hal"},
    "personas": [{"id": "hal", "name": "HAL9K", "voice": "Kore", "tone": "Calm and direct."}],
    "events": [
        {"id": "stg_release", "template": "Deployed to STG: {items}."},
        {"id": "prod_release", "template": "Published to PROD: {items}."},
    ],
    "roles": [{"id": "manager", "actions": ["status", "logs"]}],
    "workspaces": [
        {"id": "T0TEAM0001", "label": "team", "class": "owned", "persona": "hal",
         "channels": [
             {"id": "C0PROJ0001", "name": "proj-agent-squad", "mode": "listen+speak", "roles": ["manager"],
              "announce": ["stg_release"], "templates": {"prod_release": "PROD for the team: {items}."}},
             {"id": "C0ALERT001", "name": "squad-alerts", "mode": "listen+speak"},
         ]},
    ],
}

LOCAL = {
    "version": 1,
    "workspaces": [
        {"id": "T0CLIENT01", "label": "client-a", "class": "guest", "language": "en-US",
         "channels": [{"id": "C0CLISUP01", "name": "support", "mode": "listen", "announce": ["prod_release"]}]},
    ],
}


@pytest.fixture(scope="module")
def hub():
    """The hub module under a pinned environment, so the policy module comes from this tree and no caller's SG_CHECKOUT
    answers for it. Every value is undone when the module ends."""
    mp = pytest.MonkeyPatch()
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("SG_SQUAD", str(ROOT))):
        mp.setenv(k, v)
    for k in ("SG_HUB_DIR", "SG_STATE", "SG_CHECKOUT", "SG_SHARED", "SG_PROFILES_DIR", "SG_SLACK_WORKTREE"):
        mp.setenv(k, tempfile.mkdtemp())
    spec = importlib.util.spec_from_file_location("hub_slack_events", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    yield mod
    mp.undo()


def files(tmp_path, committed=None, local=None):
    """The two files of one fixture project: the committed file at the checkout root and the profile's local file."""
    c = tmp_path / "checkout" / "docs" / "squad" / "slack.yaml"
    c.parent.mkdir(parents=True, exist_ok=True)
    c.write_text(yaml.safe_dump(COMMITTED if committed is None else committed, sort_keys=False))
    loc = tmp_path / "profiles" / "4.slack.local.yaml"
    loc.parent.mkdir(parents=True, exist_ok=True)
    loc.write_text(yaml.safe_dump(LOCAL if local is None else local, sort_keys=False))
    return c, loc


def git(cwd, *args):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r.stdout.strip()


def worktree(tmp_path, committed=None):
    """A git worktree the hub owns, one commit above origin/dev, and the bare origin it fetches and pushes."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True, timeout=60)
    git(origin, "symbolic-ref", "HEAD", "refs/heads/dev")
    wt = tmp_path / "hub-wt"
    wt.mkdir()
    git(wt, "init", "-q", "-b", "dev")
    git(wt, "config", "user.name", "fixture")
    git(wt, "config", "user.email", "fixture@localhost")
    c = wt / "docs" / "squad" / "slack.yaml"
    c.parent.mkdir(parents=True, exist_ok=True)
    c.write_text(yaml.safe_dump(COMMITTED if committed is None else committed, sort_keys=False))
    git(wt, "add", "--", "docs/squad/slack.yaml")
    git(wt, "commit", "-q", "-m", "seed the Slack file")
    git(wt, "remote", "add", "origin", str(origin))
    git(wt, "push", "-q", "origin", "dev")
    git(wt, "fetch", "-q", "origin")
    return wt, origin


def saved(hub, tmp_path, new_c=None, new_l=None, confirm=False):
    c, loc = files(tmp_path)
    body = {"committed": COMMITTED if new_c is None else new_c,
            "local": LOCAL if new_l is None else new_l, "confirm": confirm}
    return hub.slack_files_set(body, committed=c, local=loc), c, loc


# ---------------------------------------------------------------- the policy rule (criteria 5 and 6)

def test_an_announce_that_names_an_unknown_event_is_refused():
    """The announce list of a channel names only the events the project declares. A typo is a defect that names the
    exact entry, so the owner fixes it from the message alone (criterion 5)."""
    c = copy.deepcopy(COMMITTED)
    c["workspaces"][0]["channels"][1]["announce"] = ["prod_relase"]
    out = P.validate(committed=c, local={})
    assert [(d.file, d.key) for d in out] == [("slack.yaml", "workspaces[0].channels[1].announce[0]")], out
    assert "prod_relase" in out[0].reason and "prod_release" in out[0].reason, out[0].reason


def test_an_announce_of_a_declared_event_passes():
    assert P.validate(committed=copy.deepcopy(COMMITTED), local=copy.deepcopy(LOCAL)) == []


def test_a_guest_channel_announce_is_checked_against_the_committed_events():
    """A guest channel lives in the local file, and its events still come from the committed file. The message names
    the local file, so the owner knows which file to edit."""
    local = copy.deepcopy(LOCAL)
    local["workspaces"][0]["channels"][0]["announce"] = ["stg_relase"]
    out = P.validate(committed=copy.deepcopy(COMMITTED), local=local, local_name="4.slack.local.yaml")
    assert [(d.file, d.key) for d in out] == [("4.slack.local.yaml", "workspaces[0].channels[0].announce[0]")], out


def test_an_announce_that_is_not_a_list_is_refused():
    c = copy.deepcopy(COMMITTED)
    c["workspaces"][0]["channels"][1]["announce"] = "stg_release"
    out = P.validate(committed=c, local={})
    assert [(d.file, d.key) for d in out] == [("slack.yaml", "workspaces[0].channels[1].announce")], out


def test_a_template_that_holds_a_token_is_refused_and_names_its_key():
    """Criterion 6, for both the project template and the channel override: a pasted token never reaches the remote."""
    c = copy.deepcopy(COMMITTED)
    c["events"][0]["template"] = "Deployed: xoxb-123-secret"
    out = P.validate(committed=c, local={})
    assert [(d.file, d.key) for d in out] == [("slack.yaml", "events[0].template")], out
    assert "xoxb-" in out[0].reason, out[0].reason
    c = copy.deepcopy(COMMITTED)
    c["workspaces"][0]["channels"][0]["templates"]["stg_release"] = "Deployed: xoxp-999"
    out = P.validate(committed=c, local={})
    assert [(d.file, d.key) for d in out] == \
        [("slack.yaml", "workspaces[0].channels[0].templates.stg_release")], out


# ---------------------------------------------------------------- the save path (criteria 2 to 6)

def test_the_editor_refuses_an_announce_that_names_no_declared_event(hub, tmp_path, monkeypatch):
    """The preview of the save refuses the file and writes neither one. The message names the key, so the Save dialog
    can show it and keep its confirm disabled (criterion 5)."""
    monkeypatch.setattr(hub, "STATE", tmp_path / "state")
    new_c = copy.deepcopy(COMMITTED)
    new_c["workspaces"][0]["channels"][1]["announce"] = ["stg_release", "quarterly_release"]
    out, c, loc = saved(hub, tmp_path, new_c=new_c)
    assert out["err"], out
    assert "workspaces[0].channels[1].announce[1]" in out["err"] and "quarterly_release" in out["err"], out["err"]
    assert yaml.safe_load(c.read_text()) == COMMITTED
    assert yaml.safe_load(loc.read_text()) == LOCAL


def test_the_editor_refuses_a_template_that_holds_a_token(hub, tmp_path, monkeypatch):
    monkeypatch.setattr(hub, "STATE", tmp_path / "state")
    new_c = copy.deepcopy(COMMITTED)
    new_c["events"][1]["template"] = "Published: xoxb-000"
    out, c, loc = saved(hub, tmp_path, new_c=new_c, confirm=True)
    assert out["err"] and "events[1].template" in out["err"] and "token" in out["err"], out
    assert yaml.safe_load(c.read_text()) == COMMITTED
    new_c = copy.deepcopy(COMMITTED)
    new_c["workspaces"][0]["channels"][0]["templates"]["prod_release"] = "Published: xoxp-000"
    out, c, loc = saved(hub, tmp_path, new_c=new_c, confirm=True)
    assert out["err"] and "templates.prod_release" in out["err"], out


def test_the_editor_saves_the_events_their_announce_and_one_override(hub, tmp_path, monkeypatch):
    """Criteria 1 to 4 in one save: a new event with its template, the announce list of a channel that adds it, and an
    override for one event in one channel. The preview shows the diff of both files, and the confirm writes the local
    file and lands the committed one in dev."""
    monkeypatch.setattr(hub, "STATE", tmp_path / "state")
    wt, origin = worktree(tmp_path)
    loop = tmp_path / "loop-checkout"
    loop.mkdir()
    monkeypatch.setenv("SG_SLACK_WORKTREE", str(wt))
    monkeypatch.setenv("SG_CHECKOUT", str(loop))
    new_c, new_l = copy.deepcopy(COMMITTED), copy.deepcopy(LOCAL)
    new_c["events"].append({"id": "fix_release", "template": "Fixed: {items}."})
    new_c["workspaces"][0]["channels"][1]["announce"] = ["fix_release"]
    new_c["workspaces"][0]["channels"][1]["templates"] = {"stg_release": "STG for alerts: {items}."}
    new_l["workspaces"][0]["channels"][0]["announce"] = ["prod_release", "fix_release"]
    preview, c, loc = saved(hub, tmp_path, new_c=new_c, new_l=new_l)
    assert "err" not in preview, preview
    lines = preview["diff"]["committed"] + preview["diff"]["local"]
    assert any("fix_release" in line for line in lines), lines
    out, c, loc = saved(hub, tmp_path, new_c=new_c, new_l=new_l, confirm=True)
    assert "err" not in out, out
    landed = yaml.safe_load((wt / "docs" / "squad" / "slack.yaml").read_text())
    assert landed["events"][-1] == {"id": "fix_release", "template": "Fixed: {items}."}
    assert landed["workspaces"][0]["channels"][1]["announce"] == ["fix_release"]
    assert landed["workspaces"][0]["channels"][1]["templates"] == {"stg_release": "STG for alerts: {items}."}
    assert yaml.safe_load(loc.read_text())["workspaces"][0]["channels"][0]["announce"] == ["prod_release", "fix_release"]
    assert out["sha"] and git(origin, "rev-parse", "dev") == out["sha"]


def test_the_saved_override_is_the_text_the_announcement_writer_reads(hub, tmp_path, monkeypatch):
    """Criterion 3 reads at its effect, never at the file: after the save, the announcement writer of the project
    resolves the channel's own text for that event, and the project template for every other channel."""
    monkeypatch.setattr(hub, "STATE", tmp_path / "state")
    wt, origin = worktree(tmp_path)
    loop = tmp_path / "loop-checkout"
    loop.mkdir()
    monkeypatch.setenv("SG_SLACK_WORKTREE", str(wt))
    monkeypatch.setenv("SG_CHECKOUT", str(loop))
    new_c = copy.deepcopy(COMMITTED)
    new_c["workspaces"][0]["channels"][0]["templates"]["stg_release"] = "Team only: {items}."
    out, c, loc = saved(hub, tmp_path, new_c=new_c, confirm=True)
    assert "err" not in out, out
    landed = yaml.safe_load((wt / "docs" / "squad" / "slack.yaml").read_text())
    from squad import slackannounce as A
    team = landed["workspaces"][0]["channels"][0]
    other = landed["workspaces"][0]["channels"][1]
    assert A.template(landed, team, "stg_release") == "Team only: {items}."
    assert A.template(landed, other, "stg_release") == "Deployed to STG: {items}."


# ---------------------------------------------------------------- the renderer and the edit functions

def _node(body):
    p = subprocess.run(["node", "-"], input=body, capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    return p.stdout


def _slack_js(page):
    esc = re.search(r"^const esc=.*$", page, re.M).group(0)
    sett = page[page.index("// SETT{"):page.index("// }SETT")]
    # The Slack slice runs from `let slS=null` to `slLoad`, so it already carries `slEdit` and every helper of the
    # workspace editor (#2696). A second copy would declare each of the editor's consts twice, which node refuses.
    sl = page[page.index("let slS=null"):page.index("async function slLoad()")]
    return "\n".join([esc, sett, sl])


STUB = 'const STORE={};const $=s=>STORE[s]||(STORE[s]={innerHTML:"",textContent:""});'


def overview(hub, tmp_path):
    c, loc = files(tmp_path)
    return hub.slack_overview(c, loc)


def render(hub, out, docs=None):
    body = (_slack_js(hub.PAGE) + "\n" + STUB
            + f"\nslS={{}};slR={json.dumps(out)};"
            + f"slDocs={json.dumps(docs if docs is not None else out['docs'])};slSavedDocs=slClone(slDocs);slRender();"
            "console.log(JSON.stringify(STORE['#sbody'].innerHTML))")
    return json.loads(_node(body))


def edit(hub, out, docs, calls):
    """Run edit calls in order and hand back the two documents they left, so a test reads the state the Save posts."""
    body = (_slack_js(hub.PAGE) + "\n" + STUB
            + f"\nslS={{}};slR={json.dumps(out)};"
            + f"slDocs={json.dumps(docs)};slSavedDocs=slClone(slDocs);"
            + "".join(f"slEdit({json.dumps(k)},{json.dumps(v)});" for k, v in calls)
            + "console.log(JSON.stringify(slDocs))")
    return json.loads(_node(body))


def test_a_confirmed_save_reloads_the_read_view(hub, tmp_path):
    """The workspaces table draws the announce list from the read view, and the editor writes it into the documents. A
    save that reloaded neither would leave the table on the old list, so the confirm reads the view again (#2698)."""
    page = hub.PAGE
    esc = re.search(r"^const esc=.*$", page, re.M).group(0)
    sett = page[page.index("// SETT{"):page.index("// }SETT")]
    sl = page[page.index("let slS=null"):page.index('$("#sbody").addEventListener("change"')]
    body = "\n".join([esc, sett, sl]) + "\n" + STUB
    body += ('\nconst save={files:{committed:"slack.yaml",local:"4.slack.local.yaml"},diff:{committed:[],local:[]}};'
             'const read={files:{committed:"slack.yaml",local:"4.slack.local.yaml"},now:[],personas:[],roles:[],'
             'workspaces:[],events:[{id:"fix_release",template:"Fixed: {items}."}],defects:[],'
             'project:{language:"en-US",persona:null,persona_name:null},'
             'docs:{committed:{events:[{id:"fix_release",template:"Fixed: {items}."}]},local:{}}};'
             'let n=0;globalThis.fetch=async()=>({json:async()=>n++?read:save});'
             'slS={};slR=null;'
             'slDocs={committed:{events:[]},local:{}};'
             'slSavedDocs=slClone(slDocs);await slPost(true);'
             'console.log(JSON.stringify({docs:slDocs,events:(slR||{}).events,msg:slMsg}))')
    out = json.loads(_node(body))
    assert out["docs"]["committed"]["events"] == [{"id": "fix_release", "template": "Fixed: {items}."}], out
    assert out["events"] == [{"id": "fix_release", "template": "Fixed: {items}."}], out
    assert out["msg"].startswith("saved"), out


def state(hub, out, docs, lines):
    """Run the functions the editor's buttons call, in order, and hand back the two documents they left."""
    body = (_slack_js(hub.PAGE) + "\n" + STUB
            + f"\nslS={{}};slR={json.dumps(out)};"
            + f"slDocs={json.dumps(docs)};slSavedDocs=slClone(slDocs);" + "".join(lines)
            + "console.log(JSON.stringify(slDocs))")
    return json.loads(_node(body))


def events_block(html):
    """The Project events section alone: from its heading to the end of the read block."""
    i = html.index("Project events")
    return html[i:]


def test_the_events_editor_draws_a_control_for_each_field(hub, tmp_path):
    """Criterion 1, 2 and 3 on screen: the event id, the template, the announce checkbox and the override of each
    channel, plus the add control. Every project event is drawn from the raw committed document."""
    html = events_block(render(hub, overview(hub, tmp_path)))
    for key in ('data-sl="ev:0:id"', 'data-sl="ev:0:tpl"', 'data-sl="ev:1:id"', 'data-sl="ev:1:tpl"',
                'data-sl="an:c:0:0:0"', 'data-sl="an:c:0:1:0"', 'data-sl="an:l:0:0:1"',
                'data-sl="ov:c:0:0:0"', 'id=slevnew', 'id=slevadd', 'data-slevrm="0"'):
        assert key in html, (key, html)


def test_the_events_editor_names_the_channel_of_each_subscription(hub, tmp_path):
    """A checkbox is unreadable without its channel: each row names the channel, its Slack workspace, and the file it
    lives in, and the row states the subscription the file holds."""
    html = events_block(render(hub, overview(hub, tmp_path)))
    assert "#proj-agent-squad" in html and "#squad-alerts" in html and "#support" in html, html
    assert "client-a" in html and "team" in html, html
    # the first event: proj-agent-squad announces it, the other two channels do not
    first = html[:html.index("prod_release", html.index("ev:1:id"))]
    assert re.search(r'data-sl="an:c:0:0:0"\s+checked', first), first
    assert not re.search(r'data-sl="an:c:0:1:0"\s+checked', first), first
    assert not re.search(r'data-sl="an:l:0:0:0"\s+checked', first), first


def test_the_override_box_shows_the_text_the_channel_holds(hub, tmp_path):
    html = events_block(render(hub, overview(hub, tmp_path)))
    assert re.search(r'data-sl="ov:c:0:0:1"[^>]*value="PROD for the team: \{items\}\."', html), html
    assert re.search(r'data-sl="ov:c:0:0:0"[^>]*value=""', html), html


def test_every_section_that_names_an_editor_carries_its_control(hub, tmp_path):
    """The Personas section (#2697), the Roles section (#2699), the workspaces section (#2696) and the Project events
    section (#2698) each carry an editor of their own, so each one draws a control."""
    html = render(hub, overview(hub, tmp_path))
    roles = html[html.index("<h4>Roles"):html.index("Slack workspaces")]
    assert "data-sle=" in roles and "data-sladdrole" in roles, roles
    section = html[html.index("Slack workspaces"):html.index("Project events")]
    assert "data-slopen=" in section and "<input" in section, section


def test_checking_announce_writes_the_channel_list(hub, tmp_path):
    """Criterion 2 through the interface: the checkbox writes the raw document, and the empty list removes the key."""
    out = overview(hub, tmp_path)
    docs = edit(hub, out, out["docs"], [("an:c:0:1:0", {"type": "checkbox", "checked": True}),
                                        ("an:c:0:0:0", {"type": "checkbox", "checked": False}),
                                        ("an:l:0:0:0", {"type": "checkbox", "checked": True})])
    assert docs["committed"]["workspaces"][0]["channels"][1]["announce"] == ["stg_release"], docs
    assert "announce" not in docs["committed"]["workspaces"][0]["channels"][0], docs
    assert docs["local"]["workspaces"][0]["channels"][0]["announce"] == ["prod_release", "stg_release"], docs


def test_the_override_box_writes_the_channel_template(hub, tmp_path):
    """Criterion 3 through the interface: a text writes the override, and an empty text removes it so the project
    template answers again."""
    out = overview(hub, tmp_path)
    docs = edit(hub, out, out["docs"],
                [("ov:c:0:0:0", {"type": "text", "value": "Team: {items}."}),
                 ("ov:c:0:0:1", {"type": "text", "value": ""})])
    templates = docs["committed"]["workspaces"][0]["channels"][0]["templates"]
    assert templates == {"stg_release": "Team: {items}."}, docs


def test_a_new_event_and_its_template_reach_the_document(hub, tmp_path):
    """Criterion 1 through the interface: an event is added with its id, its template, and a remove that drops it."""
    out = overview(hub, tmp_path)
    docs = state(hub, out, out["docs"], [
        "slEvAdd('fix_release');",
        "slEdit('ev:2:tpl',{type:'text',value:'Fixed: {items}.'});",
        "slEvAdd('fix_release');"])   # a second event of the same id is refused: two rows never share one key
    assert docs["committed"]["events"][-1] == {"id": "fix_release", "template": "Fixed: {items}."}, docs
    assert [e["id"] for e in docs["committed"]["events"]] == ["stg_release", "prod_release", "fix_release"], docs
    docs = state(hub, out, out["docs"], ["slEvRemove(1);"])
    assert [e["id"] for e in docs["committed"]["events"]] == ["stg_release"], docs


def test_a_renamed_event_carries_its_announce_and_its_overrides(hub, tmp_path):
    """The event id is the key every channel's announce list and override map holds, so a rename keeps the channel
    subscriptions and the overrides on the same event. Without this, a rename would silently subscribe nobody."""
    out = overview(hub, tmp_path)
    docs = edit(hub, out, out["docs"], [("ev:0:id", {"type": "text", "value": "stg_deploy"})])
    team = docs["committed"]["workspaces"][0]["channels"][0]
    assert docs["committed"]["events"][0]["id"] == "stg_deploy", docs
    assert team["announce"] == ["stg_deploy"], team
    assert docs["local"]["workspaces"][0]["channels"][0]["announce"] == ["prod_release"], docs
    docs = edit(hub, out, out["docs"], [("ev:1:id", {"type": "text", "value": "prod_deploy"})])
    assert docs["committed"]["workspaces"][0]["channels"][0]["templates"] == \
        {"prod_deploy": "PROD for the team: {items}."}, docs
    assert docs["local"]["workspaces"][0]["channels"][0]["announce"] == ["prod_deploy"], docs
