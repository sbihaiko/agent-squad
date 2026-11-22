"""GitHub issue #924: Settings > Communication sets how the agent reads and writes Slack for this profile.

The settings are one gitignored file beside the profile. The hub renders the theme, validates, shows the diff, writes the
file on confirm (ADR-0030) and holds no token. Posting is out of scope here: nothing reads this file yet.

No browser runs: the seams under test are the hub's own functions, its `GET`/`POST /api/slack` route and the page text.
Every name in this file is a placeholder; a real channel or person never appears in the repository.
"""
import http.client
import importlib.util
import json
import pathlib
import re
import shutil
import subprocess
import threading
from html.parser import HTMLParser
from http.server import ThreadingHTTPServer

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib" / "squad-goal" / "scripts" / "hub.py"


@pytest.fixture(scope="module")
def ctx(tmp_path_factory):
    root = tmp_path_factory.mktemp("slack-settings")
    prof, state, co = root / "profiles", root / "state", root / "checkout"
    for d in (prof, state, co):
        d.mkdir()
    (prof / "t.sh").write_text(': "${SG_REPO:=x/y}"\n')
    mp = pytest.MonkeyPatch()
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("SG_PROFILE", "t"),
                 ("SG_PROFILES_DIR", str(prof)), ("SG_STATE", str(state)), ("SG_CHECKOUT", str(co)),
                 ("SG_HUB_DIR", str(root / "hub")), ("HOME", str(root))):
        mp.setenv(k, v)
    spec = importlib.util.spec_from_file_location("hub_slack_settings", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    yield {"hub": mod, "prof": prof, "state": state}
    mp.undo()


@pytest.fixture(autouse=True)
def fresh(ctx):
    for p in ctx["prof"].glob("t.slack.json"):
        p.unlink()
    return ctx


def chan(name="#example-channel", **kw):
    base = {"name": name, "lang": "en-US", "watch": True, "thread": True, "mention": False,
            "events": {"stg": True, "prod": True, "dev": False, "bug": True, "fix": True}}
    return {**base, **kw}


def save(ctx, **body):
    return ctx["hub"].slack_set(body)


def saved_file(ctx):
    return ctx["prof"] / "t.slack.json"


# --- storage

def test_the_settings_file_is_gitignored_beside_the_profile():
    rel = "contrib/squad-goal/scripts/profiles/t.slack.json"
    r = subprocess.run(["git", "check-ignore", "-q", rel], cwd=ROOT, capture_output=True, timeout=30)
    assert r.returncode == 0, "the per-profile Slack file is not gitignored"


def test_with_no_file_the_theme_names_no_channel_and_no_person(ctx):
    got = ctx["hub"].slack_read(saved_file(ctx))
    assert got["channels"] == [] and got["people"] == []
    assert got["connection"] == "not connected"
    assert not any("token" in k.lower() for k in got)


def test_a_preview_names_the_diff_and_writes_nothing(ctx):
    got = save(ctx, prefix="[bot]", confirm=False)
    assert got["diff"] == ["Communication · Slack · prefix: [agent] -> [bot]"]
    assert not saved_file(ctx).exists()


def test_every_diff_line_is_prefixed_for_the_communication_theme(ctx):
    got = save(ctx, prefix="[bot]", tick=3, channels=[chan()], people=[{"name": "Example Person", "role": "Manager"}],
               confirm=False)
    assert len(got["diff"]) == 4, got["diff"]
    assert all(line.startswith("Communication · Slack · ") for line in got["diff"]), got["diff"]


def test_a_confirmed_save_writes_the_file_and_the_theme_reads_it_back(ctx):
    got = save(ctx, channels=[chan()], people=[{"name": "Example Person", "role": "Colleague"}], confirm=True)
    assert "err" not in got and saved_file(ctx).exists()
    back = ctx["hub"].slack_read(saved_file(ctx))
    assert [c["name"] for c in back["channels"]] == ["#example-channel"]
    assert back["people"] == [{"name": "Example Person", "role": "Colleague"}]
    assert "Communication · Slack · channel #example-channel added" in got["diff"]


def test_a_channel_with_watch_off_is_saved_as_off(ctx):
    save(ctx, channels=[chan(watch=False)], confirm=True)
    back = ctx["hub"].slack_read(saved_file(ctx))
    assert back["channels"][0]["watch"] is False


def test_the_file_holds_only_the_settings_and_no_token(ctx):
    save(ctx, token="not-a-token", channels=[chan()], confirm=True)
    assert set(json.loads(saved_file(ctx).read_text())) <= set(ctx["hub"].SLACK_DEFAULT)


def test_a_value_in_token_format_is_refused_and_the_key_is_named(ctx):
    got = save(ctx, channels=[chan(name="#xoxb-1234567890")], confirm=True)
    assert "channels[0].name" in got["err"] and "xoxb-" in got["err"]
    assert not saved_file(ctx).exists()


def test_a_person_role_is_manager_or_colleague(ctx):
    got = save(ctx, people=[{"name": "Example Person", "role": "Admin"}], confirm=True)
    assert got["err"] == "role is Manager or Colleague"
    assert not saved_file(ctx).exists()


def spy_policy(ctx, monkeypatch, validate):
    """A stand-in for the Slack rules module that records the document the hub hands `validate`."""
    real = ctx["hub"].slack_policy()
    seen = {}

    class Spy:
        first_token = staticmethod(real.first_token)

        @staticmethod
        def validate(committed=None, **kw):
            seen["committed"] = committed
            return validate(committed, **kw)

    monkeypatch.setattr(ctx["hub"], "slack_policy", lambda: Spy)
    return seen


def test_the_hub_editor_accepts_a_role_deploy_without_prod(ctx, monkeypatch):
    # The spy proves the posted roles reach the validator unfiltered: a hub that dropped the key would fail here.
    real = ctx["hub"].slack_policy().validate
    seen = spy_policy(ctx, monkeypatch, real)
    roles = [{"id": "manager", "deploy": ["stg", "dev"]}]
    got = save(ctx, roles=roles, confirm=True)
    assert "err" not in got, got
    assert seen["committed"]["roles"] == roles, "the hub dropped the posted roles before the validator saw them"


def test_the_hub_editor_refuses_prod_in_a_role_deploy_and_names_deploy(ctx):
    # Every other setting is its valid default, so the only error source is roles[0].deploy.
    got = save(ctx, roles=[{"id": "manager", "deploy": ["prod"]}], confirm=True)
    assert "roles[0].deploy" in got.get("err", "")
    assert not saved_file(ctx).exists()


def test_a_validator_that_raises_answers_a_clean_error(ctx, monkeypatch):
    def boom(committed=None, **kw):
        raise ValueError("the roles shape is not what the validator expects")

    spy_policy(ctx, monkeypatch, boom)
    got = save(ctx, prefix="[bot]", confirm=True)
    assert "err" in got and "roles shape" in got["err"], got
    assert not saved_file(ctx).exists()


@pytest.mark.parametrize("bad", [
    {"prefix": "two\nlines"},
    {"prefix": ""},
    {"tick": 0},
    {"tick": 31},
    {"tick": 5, "max": 4},
    {"quiet": "25-07"},
    {"lang": "fr"},
    {"other": "maybe"},
    {"channels": [chan(), chan()]},
    {"channels": [chan("bad name")]},
])
def test_a_value_outside_its_rule_is_refused_and_nothing_is_written(ctx, bad):
    got = save(ctx, confirm=True, **bad)
    assert "err" in got
    assert not saved_file(ctx).exists()


# --- route

def test_the_route_serves_the_theme_and_takes_a_confirmed_save(ctx):
    mod = ctx["hub"]
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        port = srv.server_port
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request("GET", "/api/slack", headers={"Host": f"127.0.0.1:{port}"})
        r = c.getresponse()
        got = json.loads(r.read() or b"{}")
        assert r.status == 200 and got["prefix"] == "[agent]" and got["channels"] == [], got
        c.close()
        head = {"Host": f"127.0.0.1:{port}", "Origin": f"http://127.0.0.1:{port}", "Content-Type": "application/json"}
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request("POST", "/api/slack", body=json.dumps({"prefix": "[bot]", "confirm": False}), headers=head)
        r = c.getresponse()
        assert json.loads(r.read())["diff"] == ["Communication · Slack · prefix: [agent] -> [bot]"]
        assert not saved_file(ctx).exists()
        c.close()
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request("POST", "/api/slack", body=json.dumps({"prefix": "[bot]", "confirm": True}), headers=head)
        r = c.getresponse()
        assert r.status == 200 and json.loads(saved_file(ctx).read_text())["prefix"] == "[bot]"
        c.close()
    finally:
        srv.shutdown()
        srv.server_close()


# --- the page

def test_the_page_carries_the_communication_theme_and_no_token_field(ctx):
    page = ctx["hub"].served_page()
    assert 'id=slk class=sp' in page and "id=sbody" in page and "function slLoad(" in page
    assert '"slk"' in page
    assert "type=password" not in page
    assert "Communication" in page and "Discard" in page


def _channel_row(ctx, channel):
    """One channel row as the page's own `slChan` draws it, run in node; parsed into its controls."""
    if shutil.which("node") is None:
        pytest.skip("node is required")
    page = ctx["hub"].served_page()
    rules = page[page.index("const SL_LANG="):page.index("function slRender(")]
    esc = 'const esc=t=>String(t).replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));'
    script = esc + "\n" + rules + "\nconst c=" + json.dumps(channel) + ";console.log(JSON.stringify(slChan(c,0)));"
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


class _Controls(HTMLParser):
    def __init__(self):
        super().__init__()
        self.controls = []

    def handle_starttag(self, tag, attrs):
        if tag in ("input", "select", "button"):
            self.controls.append(dict(attrs))


def _controls(html_text):
    p = _Controls()
    p.feed(html_text)
    return {a["aria-label"]: "disabled" in a for a in p.controls}


def test_a_channel_with_watch_off_is_struck_marked_and_its_controls_disabled(ctx):
    off = {"name": "#example-channel", "lang": "en-US", "watch": False, "thread": True, "mention": False,
           "events": {"stg": True, "prod": True, "dev": False, "bug": True, "fix": True}}
    html_text = _channel_row(ctx, off)
    assert 'class="slc off"' in html_text and "do not watch" in html_text
    assert ".slc.off b{text-decoration:line-through" in ctx["hub"].served_page()
    state = _controls(html_text)
    assert state["#example-channel: watch"] is False
    assert state["#example-channel: language"] is True
    assert state["#example-channel: thread only"] is True
    assert state["#example-channel: mention only"] is True
    for k in ("stg", "prod", "dev", "bug", "fix"):
        assert state[f"#example-channel: {k} posts"] is True
    assert state["remove #example-channel"] is False


def test_a_channel_with_watch_on_has_every_control_enabled(ctx):
    on = {"name": "#example-channel", "lang": "en-US", "watch": True, "thread": True, "mention": False,
          "events": {"stg": True, "prod": True, "dev": False, "bug": True, "fix": True}}
    html_text = _channel_row(ctx, on)
    assert "do not watch" not in html_text
    assert not any(_controls(html_text).values())


def test_the_communication_theme_reads_its_connection_and_edits_everything_else(ctx):
    """#1278: one shape for every theme. The Now block holds the connection as a tile and no control; every switch,
    field and row the owner can change sits in the Settings block."""
    if shutil.which("node") is None:
        pytest.skip("node is required")
    page = ctx["hub"].served_page()
    esc = 'const esc=t=>String(t).replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));'
    sett = page[page.index("// SETT{"):page.index("// }SETT")]
    rules = page[page.index("const SL_LANG="):page.index("async function slLoad(")]
    stub = 'const STORE={};const $=s=>STORE[s]||(STORE[s]={innerHTML:"",textContent:""});let slMsg="";'
    view = {k: v for k, v in ctx["hub"].slack_view(ctx["hub"].slack_file()).items() if k != "new_channel"}
    script = "\n".join([stub, esc, sett, rules, f"slS={json.dumps(view)};slDraft=JSON.parse(JSON.stringify(slS));"
                        "slRender();console.log(STORE['#sbody'].innerHTML);"])
    r = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    now = r.stdout[r.stdout.index("data-blk=now"):r.stdout.index("data-blk=settings")]
    settings = r.stdout[r.stdout.index("data-blk=settings"):]
    assert re.findall(r"<span class=tk>(.*?)</span>", now) == ["Connection"], now
    for ctl in ("<input", "<select", "<textarea", "<button"):
        assert ctl not in now, f"the Communication Now block carries {ctl}"
    assert "data-sl=prefix" in settings and "<h4>Channels</h4>" in settings


def test_the_page_states_what_the_agent_does_for_each_role(ctx):
    page = ctx["hub"].served_page()
    assert "drafts the answer; posts only after the owner's OK" in page
    assert "a bug it reports becomes an issue in Todo at once" in page


def test_the_page_stacks_each_row_on_a_phone_and_labels_each_switch(ctx):
    page = ctx["hub"].served_page()
    assert "@media (max-width:600px){.slc{grid-template-columns:1fr}" in page
    assert 'data-l="watch"' in page and 'data-l="thread only"' in page and 'data-l="mention only"' in page


# --- templates, the new-channel defaults and the profile folder

def test_each_release_event_has_one_template_from_the_hub(ctx):
    templates = ctx["hub"].slack_read(saved_file(ctx))["templates"]
    assert set(templates) == set(ctx["hub"].SLACK_EVENTS)
    assert all(text.startswith("<prefix>") for text in templates.values())


def test_an_edited_template_is_diffed_then_saved_on_confirm(ctx):
    templates = dict(ctx["hub"].slack_read(saved_file(ctx))["templates"], stg="<prefix>\nSTG is green. PR <pr>.")
    got = save(ctx, templates=templates, confirm=False)
    assert got["diff"] == ["Communication · Slack · template stg: changed"]
    assert not saved_file(ctx).exists()
    save(ctx, templates=templates, confirm=True)
    assert ctx["hub"].slack_read(saved_file(ctx))["templates"]["stg"] == "<prefix>\nSTG is green. PR <pr>."


@pytest.mark.parametrize("bad", [
    {"stg": ""},
    {"stg": "x" * 601},
    {"stg": 5},
    {"unknown": "text"},
])
def test_a_template_is_text_of_one_to_600_characters_for_a_release_event(ctx, bad):
    templates = {**ctx["hub"].slack_read(saved_file(ctx))["templates"], **bad}
    got = save(ctx, templates=templates, confirm=True)
    assert "err" in got
    assert not saved_file(ctx).exists()


def test_the_page_edits_each_template_and_holds_no_template_text(ctx):
    page = ctx["hub"].served_page()
    assert "const SL_TPL" not in page
    assert "Deployed to STG" not in page
    assert "tpl:" in page


def test_the_hub_serves_the_defaults_for_a_new_channel(ctx):
    view = ctx["hub"].slack_view(saved_file(ctx))
    assert view["new_channel"] == {"watch": True, "thread": True, "mention": False,
                                   "events": {"stg": True, "prod": True, "dev": False, "bug": True, "fix": True}}


def test_the_page_adds_a_channel_from_the_defaults_the_hub_serves(ctx):
    page = ctx["hub"].served_page()
    assert "...slS.new_channel" in page
    assert "events:{stg:true,prod:true,dev:false" not in page


def test_audio_is_off_whenever_the_owner_self_dm_is_off(ctx):
    save(ctx, dm=True, audio=True, confirm=True)
    save(ctx, dm=False, audio=True, confirm=True)
    back = ctx["hub"].slack_read(saved_file(ctx))
    assert back["dm"] is False and back["audio"] is False


def test_a_profile_folder_git_would_track_is_refused_and_nothing_is_written(ctx, tmp_path, monkeypatch):
    repo = tmp_path / "tracked"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, timeout=30)
    monkeypatch.setenv("SG_PROFILES_DIR", str(repo))
    got = save(ctx, channels=[chan()], confirm=True)
    assert "would be tracked" in got.get("err", ""), got
    assert not (repo / "t.slack.json").exists()
