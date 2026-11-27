"""GitHub issue #2693: the read view of the Communication theme in the hub Settings page.

The view reads the committed `slack.yaml` and the profile's `slack.local.yaml`, and draws the Now tiles and the four
sections of the mockup: Personas, Roles, Slack workspaces and Project events. Each field names the file it comes from.
Every resolved value comes from the policy module (#2684): the hub holds no copy of a rule (ADR-0177).

The seams under test are the hub's own read function, `slack_overview`, and the theme's renderer run in node. No browser
runs here. Every id is a placeholder.
"""
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
        {"id": "manager", "actions": ["status", "logs", "request_work"]},
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
             {"id": "C0CLISUP01", "name": "support", "mode": "listen+speak", "speak": "draft"},
             {"id": "C0CLIGEN01", "name": "general", "mode": "listen"},
         ]},
    ],
    "members": [{"workspace": "T0TEAM0001", "channel": "C0PROJ0001", "role": "manager", "users": ["U0OWNER001"]}],
}


@pytest.fixture(scope="module")
def hub():
    """The hub module under a pinned environment. The hub loads the policy module from SG_SQUAD first, then SG_CHECKOUT
    (`squad_roots`), so a caller's own SG_CHECKOUT, such as the loop checkout a gate exports, would answer with another
    tree's `slackpolicy`. SG_SQUAD names this tree, and every value is undone when the module ends."""
    mp = pytest.MonkeyPatch()
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("SG_SQUAD", str(ROOT))):
        mp.setenv(k, v)
    for k in ("SG_HUB_DIR", "SG_STATE", "SG_CHECKOUT", "SG_SHARED", "SG_PROFILES_DIR"):
        mp.setenv(k, tempfile.mkdtemp())
    spec = importlib.util.spec_from_file_location("hub_slack_read", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    yield mod
    mp.undo()


def files(tmp_path, committed=None, local=None):
    """The two files of one fixture project: the committed file at the checkout root and the profile's local file."""
    c = tmp_path / "docs" / "squad" / "slack.yaml"
    c.parent.mkdir(parents=True, exist_ok=True)
    c.write_text(yaml.safe_dump(COMMITTED if committed is None else committed))
    loc = tmp_path / "t.slack.local.yaml"
    loc.write_text(yaml.safe_dump(LOCAL if local is None else local))
    return c, loc


def overview(hub, tmp_path, committed=None, local=None):
    c, loc = files(tmp_path, committed, local)
    return hub.slack_overview(c, loc)


def test_each_persona_lists_the_channels_that_use_it(hub, tmp_path):
    """Story 55: a persona names every channel whose effective persona it is, through the channel, workspace and project
    order. HAL9K answers both team channels: one names it, one inherits the project. Iara answers the guest workspace."""
    out = overview(hub, tmp_path)
    used = {p["id"]: p["channels"] for p in out["personas"]}
    assert used == {"hal": ["team/#proj-agent-squad", "team/#squad-alerts"],
                    "iara": ["client-a/#support", "client-a/#general"]}, used


def test_each_role_lists_its_actions(hub, tmp_path):
    out = overview(hub, tmp_path)
    assert [(r["id"], r["actions"]) for r in out["roles"]] == \
        [("manager", ["status", "logs", "request_work"]), ("colleague", ["status"])]


def test_each_slack_workspace_names_its_class_and_its_channels(hub, tmp_path):
    """A workspace row carries the class that decides its transport and its channels in file order. Each channel
    carries its resolved language and persona: the team channel inherits pt-BR from its workspace and hal from the
    project, and the guest channel inherits iara from its workspace."""
    out = overview(hub, tmp_path)
    assert [(w["label"], w["class"]) for w in out["workspaces"]] == [("team", "owned"), ("client-a", "guest")]
    team, guest = out["workspaces"]
    assert [(c["name"], c["language"], c["persona"]) for c in team["channels"]] == \
        [("proj-agent-squad", "pt-BR", "hal"), ("squad-alerts", "pt-BR", "hal")], team["channels"]
    assert [(c["name"], c["language"], c["persona"]) for c in guest["channels"]] == \
        [("support", "en-US", "iara"), ("general", "en-US", "iara")], guest["channels"]
    assert [c["roles"] for c in team["channels"]] == [["manager"], []]
    assert team["channels"][0]["announce"] == ["stg_release"]


def test_each_project_event_names_its_template(hub, tmp_path):
    out = overview(hub, tmp_path)
    assert out["events"] == [
        {"id": "stg_release", "template": "Deployed to STG: {items}.", "file": "slack.yaml"},
        {"id": "prod_release", "template": "Published to PROD: {items}.", "file": "slack.yaml"},
    ]


def test_each_field_carries_the_label_of_its_target_file(hub, tmp_path):
    """The committed facts name `slack.yaml`; the guest workspace and its channels name the local file. The project row
    and every persona, role and event name the committed file."""
    out = overview(hub, tmp_path)
    assert out["files"] == {"committed": "slack.yaml", "local": "t.slack.local.yaml"}
    assert out["project"]["file"] == "slack.yaml"
    assert {p["file"] for p in out["personas"]} == {"slack.yaml"}
    assert {r["file"] for r in out["roles"]} == {"slack.yaml"}
    assert [(w["class"], w["file"]) for w in out["workspaces"]] == \
        [("owned", "slack.yaml"), ("guest", "t.slack.local.yaml")]
    assert {c["file"] for w in out["workspaces"] for c in w["channels"]} == {"slack.yaml", "t.slack.local.yaml"}


def test_the_now_tiles_state_the_read_facts(hub, tmp_path):
    out = overview(hub, tmp_path)
    tiles = {t["key"]: t["value"] for t in out["now"]}
    assert tiles["Workspaces"] == "2 · 1 owned · 1 guest"
    assert tiles["Channels"] == "4 · 2 speak"
    assert tiles["Personas"] == "2 · default HAL9K"
    assert tiles["Events"] == "2 · 2 channels subscribe"
    assert tiles["Files"] == "slack.yaml · t.slack.local.yaml"
    assert out["project"]["language"] == "en-US" and out["project"]["persona"] == "hal"


def test_the_read_view_reports_the_absent_files_and_keeps_its_shape(hub, tmp_path):
    """No file is no defect: the view draws empty sections and names no rule it cannot read."""
    out = hub.slack_overview(tmp_path / "docs" / "squad" / "slack.yaml", tmp_path / "none.slack.local.yaml")
    assert out["defects"] == [] and out["personas"] == [] and out["workspaces"] == [] and out["events"] == []
    assert out["project"]["language"] == "en-US" and out["project"]["persona"] is None
    assert {t["key"] for t in out["now"]} >= {"Workspaces", "Channels", "Personas", "Events", "Files"}


def test_the_read_view_resolves_the_profile_files_when_called_with_no_path(hub, tmp_path, monkeypatch):
    """The route calls the read with no path: the profile in force names both files, the checkout's committed one and
    the profile folder's local one. The read resolves them through the policy module, never a rule of its own."""
    prof = tmp_path / "profiles"
    prof.mkdir()
    (prof / "checkout.slack.local.yaml").write_text(yaml.safe_dump(LOCAL))
    checkout = tmp_path / "checkout"
    (checkout / "docs" / "squad").mkdir(parents=True)
    (checkout / "docs" / "squad" / "slack.yaml").write_text(yaml.safe_dump(COMMITTED))
    monkeypatch.setattr(hub, "CHECKOUT", str(checkout))
    monkeypatch.setattr(hub, "PROFILE_NAME", "owner")
    monkeypatch.setenv("SG_PROFILES_DIR", str(prof))
    monkeypatch.setenv("SG_PROJECT", "4")
    committed, local = hub.slack_yaml_paths()
    assert committed == checkout / "docs" / "squad" / "slack.yaml", committed
    assert local == prof / "checkout.slack.local.yaml", local
    out = hub.slack_overview()
    assert out["files"] == {"committed": "slack.yaml", "local": "checkout.slack.local.yaml"}
    assert out["project"]["persona"] == "hal" and out["project"]["persona_name"] == "HAL9K"


def test_the_read_view_keeps_its_now_and_its_four_sections_when_the_read_fails(hub, tmp_path, monkeypatch):
    """A rule that does not resolve is a message, never a page of its own: the reader keeps the Now tiles, the four
    headings and their empty-state text, and the reason sits in a defects banner above them. The ticket's shape holds
    on the failure path, so a broken module never costs the reader the page."""
    class Boom:
        def __getattr__(self, name):
            raise RuntimeError("boom")
    monkeypatch.setattr(hub, "slack_policy", lambda: Boom())
    out = hub.slack_overview(tmp_path / "c.yaml", tmp_path / "l.yaml")
    assert {t["key"] for t in out["now"]} == {"Workspaces", "Channels", "Personas", "Events", "Files"}, out["now"]
    assert out["personas"] == [] and out["roles"] == [] and out["workspaces"] == [] and out["events"] == []
    assert out["defects"] and "boom" in out["defects"][0], out["defects"]
    page = render(hub, out)
    assert "data-blk=now" in page and "Workspaces" in page, page
    html = read_block(page)
    heads = [re.sub(r"<[^>]+>", "", re.sub(r"<span class=slfile.*?</span>", "", h)).strip()
             for h in re.findall(r"<h4[^>]*>(.*?)</h4>", html)]
    assert heads == ["Personas", "Roles", "Slack workspaces", "Project events"], heads
    assert "No persona" in html and "No Slack workspace" in html and "No event" in html, html
    assert "boom" in html, html
    monkeypatch.setattr(hub, "slack_yaml_paths", lambda: (_ for _ in ()).throw(RuntimeError("no profile")))
    no_path = hub.slack_overview()
    assert no_path["now"] and no_path["defects"] and no_path["personas"] == [], no_path


# ---------------------------------------------------------------- the renderer

def _node(body):
    p = subprocess.run(["node", "-"], input=body, capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    return p.stdout


def _slices(page):
    esc = re.search(r"^const esc=.*$", page, re.M).group(0)
    sett = page[page.index("// SETT{"):page.index("// }SETT")]
    sl = page[page.index("let slS=null"):page.index("async function slLoad()")]
    stub = ('const STORE={};const $=s=>STORE[s]||(STORE[s]={innerHTML:"",textContent:""});'
            'let prS=null;const prLoad=()=>{};')   # the personas loader setTheme calls for the Personas tab (#3036)
    return "\n".join([stub, esc, sett, sl])


def render(hub, out):
    """The theme drawn from one read view: the page holds no state of its own before the read answers."""
    body = (_slices(hub.PAGE) + f"\nslS={{}};slR={json.dumps(out)};slT=slR.tree;slTsel=(slTHere()||{{}}).id||slTsel;slRender();"
            "console.log(JSON.stringify(STORE['#sbody'].innerHTML))")
    return json.loads(_node(body))


def read_block(html):
    """The read block alone: it ends where a Settings block begins, and at the end when the read view replaced it."""
    i = html.index("data-blk=read")
    j = html.find("data-blk=settings", i)
    return html[i:j if j >= 0 else len(html)]


def test_the_theme_writes_one_status_text_to_the_chip_and_the_nav_line(hub, tmp_path):
    """One read view, one summary (#1284): the chip and the nav line carry the same text from the one render, so a
    changed workspace or channel count can never leave the two disagreeing."""
    out = overview(hub, tmp_path)
    body = (_slices(hub.PAGE) + f"\nslS={{}};slR={json.dumps(out)};slT=slR.tree;slTsel=(slTHere()||{{}}).id||slTsel;slRender();"
            "console.log(JSON.stringify({chip:STORE['#slksr'].textContent,nav:STORE['#slknav'].textContent}))")
    got = json.loads(_node(body))
    assert got["chip"] == "1 client · 1 project · 4 channels", got
    assert got["nav"] == got["chip"], got


def test_the_read_view_draws_the_four_sections_and_wears_no_control(hub, tmp_path):
    """The Now block and the four sections of the mockup, in their order. The read view wears no control: the Personas
    section names each persona and links to the Personas theme, which edits it (#3036)."""
    out = overview(hub, tmp_path)
    html = read_block(render(hub, out))
    heads = re.findall(r"<h4[^>]*>(.*?)</h4>", html)
    assert [re.sub(r"<[^>]+>", "", h).split(" slack")[0] for h in heads] == \
        ["Personas", "Roles", "Slack workspaces", "Project events"], heads
    sections = re.split(r"(?=<section class=slsec>)", html)
    by_name = {re.sub(r"<[^>]+>", "", s[s.index("<h4"):s.index("</h4>")]).split(" slack")[0].strip(): s
               for s in sections if "<h4" in s}
    for name in ("Personas", "Roles", "Slack workspaces", "Project events"):
        for ctl in ("<input", "<select", "<textarea", "<button"):
            assert ctl not in by_name[name], f"the {name} section carries {ctl}"
    assert 'data-sp=prs' in by_name["Personas"], "a persona name does not open the Personas theme"
    for text in ("HAL9K", "Kore", "team/#proj-agent-squad", "manager", "status, logs, request_work",
                 "client-a", "guest", "support", "stg_release", "Deployed to STG: {items}."):
        assert text in html, text


def test_each_drawn_row_names_its_file(hub, tmp_path):
    """A reader sees where every fact lives before editing it: each section heading, each workspace and each project
    row names its file. Every heading carries a chip, so no section leaves its field unlabelled."""
    out = overview(hub, tmp_path)
    html = read_block(render(hub, out))
    h4 = re.findall(r"<h4[^>]*>(.*?)</h4>", html)
    assert len(h4) == 4 and all("class=slfile" in h for h in h4), h4
    # The project's default language and persona are the node's own Settings rows, each with its file chip (#2906), so
    # the block repeats neither.
    assert not re.findall(r"<div class=slrow>.*?</div>", html), html[:400]
    h5 = re.findall(r"<h5 class=slws>(.*?)</h5>", html)
    assert len(h5) == 2 and all("class=slfile" in h for h in h5), h5
    assert "team" in h5[0] and "slack.yaml" in h5[0], h5[0]
    assert "client-a" in h5[1] and "t.slack.local.yaml" in h5[1], h5[1]
