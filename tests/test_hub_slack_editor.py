"""GitHub issue #2696: the Hub editor for Slack workspaces and channels, in the Communication theme.

The owner adds and edits a workspace and its channels. A `guest` workspace and its channels live only in the profile's
local file (ADR-0177), the editor refuses a `guest` id in the committed file, and it refuses a second `guest` workspace
with the mockup's own sentence. The files are the raw documents the save path (#2694) hands back, so this slice edits
them and the save path writes them.

The seams under test are the hub's own editor function, `slack_docs_edit`, and the theme's renderer run in node. No
browser runs here. Every id is a placeholder, and every file is a temporary fixture.
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
    "events": [{"id": "stg_release", "template": "Deployed to STG: {items}."}],
    "roles": [{"id": "manager", "actions": ["status", "logs"]}, {"id": "colleague", "actions": ["status"]}],
    "workspaces": [
        {"id": "T0TEAM0001", "label": "team", "class": "owned", "app": "hal9k", "persona": "hal",
         "channels": [{"id": "C0PROJ0001", "name": "proj-agent-squad", "mode": "listen+speak",
                       "roles": ["manager"], "announce": ["stg_release"]}]},
    ],
}

LOCAL = {
    "version": 1,
    "workspaces": [
        {"id": "T0CLIENT01", "label": "client-a", "class": "guest", "language": "en-US", "persona": "iara",
         "channels": [{"id": "C0CLISUP01", "name": "support", "mode": "listen+speak"}]},
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
    spec = importlib.util.spec_from_file_location("hub_slack_editor", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    yield mod
    mp.undo()


def paths(tmp_path, committed=None, local=None):
    """The two files of one fixture project: the committed file at the checkout root and the profile's local file."""
    c = tmp_path / "checkout" / "docs" / "squad" / "slack.yaml"
    c.parent.mkdir(parents=True, exist_ok=True)
    c.write_text(yaml.safe_dump(COMMITTED if committed is None else committed, sort_keys=False))
    loc = tmp_path / "profiles" / "4.slack.local.yaml"
    loc.parent.mkdir(parents=True, exist_ok=True)
    loc.write_text(yaml.safe_dump(LOCAL if local is None else local, sort_keys=False))
    return c, loc


def edit(hub, tmp_path, op, committed=None, local=None):
    c, loc = paths(tmp_path, committed, local)
    body = {"committed": copy.deepcopy(COMMITTED if committed is None else committed),
            "local": copy.deepcopy(LOCAL if local is None else local), "op": op}
    return hub.slack_docs_edit(body, committed=c, local=loc)


def gid(doc, key="id"):
    return [row.get(key) for row in doc["workspaces"]]


# ---------------------------------------------------------------- the hub's editor

def test_adding_a_guest_workspace_writes_it_only_to_the_local_file(hub, tmp_path):
    """Criterion 4: a `guest` workspace and its channels stay in the profile's local file. The class decides the file,
    so the editor places the row there even when the request names the committed file."""
    ws = {"id": "T0CLIENT02", "label": "client-b", "class": "guest", "language": "en-US",
          "channels": [{"id": "C0CLIB02", "name": "general", "mode": "listen"}]}
    out = edit(hub, tmp_path, {"kind": "add", "doc": "committed", "path": ["workspaces"], "value": ws},
               local={"version": 1, "workspaces": []})
    assert out["err"] is None, out
    assert gid(out["committed"]) == ["T0TEAM0001"]
    assert gid(out["local"]) == ["T0CLIENT02"]
    assert [c["id"] for c in out["local"]["workspaces"][0]["channels"]] == ["C0CLIB02"]


def test_adding_an_owned_workspace_writes_it_to_the_committed_file(hub, tmp_path):
    """Criterion 1: a workspace has an `id`, a `label`, a `class` and an `app`. An `owned` workspace goes to the
    committed file, whichever file the request named."""
    ws = {"id": "T0TEAM0002", "label": "team-b", "class": "owned", "app": "hal9k", "channels": []}
    out = edit(hub, tmp_path, {"kind": "add", "doc": "local", "path": ["workspaces"], "value": ws})
    assert out["err"] is None, out
    assert gid(out["committed"]) == ["T0TEAM0001", "T0TEAM0002"]
    assert out["committed"]["workspaces"][1]["app"] == "hal9k"
    assert gid(out["local"]) == ["T0CLIENT01"]


def test_the_owner_edits_a_workspace_label_and_app(hub, tmp_path):
    """Criterion 1: an edit of one field changes that field alone and names no other."""
    out = edit(hub, tmp_path, {"kind": "set", "doc": "committed", "path": ["workspaces", 0, "label"],
                               "value": "team-renamed"})
    assert out["err"] is None, out
    row = out["committed"]["workspaces"][0]
    assert row["label"] == "team-renamed" and row["app"] == "hal9k" and row["id"] == "T0TEAM0001"


def test_the_owner_edits_a_channel_mode_language_persona_roles_and_announce(hub, tmp_path):
    """Criterion 2: the channel editor writes each of the six fields the mockup draws."""
    committed, local = None, None
    for field, value in (("id", "C0PROJ0002"), ("name", "proj-renamed"), ("mode", "listen"),
                         ("language", "pt-BR"), ("persona", "iara"), ("roles", ["manager", "colleague"]),
                         ("announce", ["stg_release"])):
        out = edit(hub, tmp_path, {"kind": "set", "doc": "committed",
                                   "path": ["workspaces", 0, "channels", 0, field], "value": value},
                   committed=committed, local=local)
        assert out["err"] is None, (field, out)
        committed, local = out["committed"], out["local"]
        ch = committed["workspaces"][0]["channels"][0]
        assert ch[field] == value, (field, ch)
    assert ch["roles"] == ["manager", "colleague"]


def test_the_owner_edits_the_guest_fields_of_a_guest_channel(hub, tmp_path):
    """Criterion 3: `speak`, `transcribe`, `voice_reply`, every `poll.<subfield>` and `draft_for` are written on the
    guest channel. The dotted poll paths are the ones the renderer emits, so the subfield path is what this test holds."""
    cases = (("speak", "draft"), ("transcribe", "local"), ("voice_reply", True),
             ("poll.mode", "two_state"), ("poll.fast", "2m"), ("poll.slow", "15m"),
             ("poll.quiet_after", "12m"), ("poll.thread_open", "45m"),
             ("draft_for", ["U0CLIENT02"]))
    committed, local = None, None
    for field, value in cases:
        out = edit(hub, tmp_path, {"kind": "set", "doc": "local",
                                   "path": ["workspaces", 0, "channels", 0, field], "value": value},
                   committed=committed, local=local)
        assert out["err"] is None, (field, out)
        committed, local = out["committed"], out["local"]
        ch = local["workspaces"][0]["channels"][0]
        if field.startswith("poll."):
            assert ch["poll"][field.split(".", 1)[1]] == value, (field, ch)
        else:
            assert ch[field] == value, (field, ch)
    assert ch["speak"] == "draft" and ch["voice_reply"] is True
    assert ch["poll"] == {"mode": "two_state", "fast": "2m", "slow": "15m",
                          "quiet_after": "12m", "thread_open": "45m"}, ch["poll"]
    assert ch["draft_for"] == ["U0CLIENT02"], ch


def test_a_whole_poll_mapping_is_still_written_on_the_guest_channel(hub, tmp_path):
    """Criterion 3, the older shape: one `poll` mapping is still accepted, so a page that posts the whole schedule keeps
    working beside the dotted paths."""
    poll = {"mode": "two_state", "fast": "1m", "slow": "10m", "quiet_after": "10m", "thread_open": "30m"}
    out = edit(hub, tmp_path, {"kind": "set", "doc": "local",
                               "path": ["workspaces", 0, "channels", 0, "poll"], "value": poll})
    assert out["err"] is None, out
    assert out["local"]["workspaces"][0]["channels"][0]["poll"] == poll, out["local"]


def test_a_guest_field_reads_the_class_of_the_document_the_path_names(hub, tmp_path):
    """Criterion 3: the guest class comes from the row the path names, never from the other file at the same index. A
    guest row that sits in the committed file is misplaced, so the editor refuses its guest field and names it."""
    committed = {"workspaces": [{"id": "T1", "class": "guest", "channels": [{"id": "C1"}]}]}
    local = {"workspaces": [{"id": "T2", "class": "owned", "channels": [{"id": "C2"}]}]}
    out = edit(hub, tmp_path, {"kind": "set", "doc": "committed",
                               "path": ["workspaces", 0, "channels", 0, "speak"], "value": "draft"},
               committed=committed, local=local)
    assert out["err"] and "guest channel" in out["err"], out
    assert "speak" not in out["committed"]["workspaces"][0]["channels"][0], out["committed"]


def test_a_field_value_outside_its_choices_is_refused_and_names_the_key(hub, tmp_path):
    """Criterion 2: a value the renderer never offers is refused by the policy module, and the key is named."""
    out = edit(hub, tmp_path, {"kind": "set", "doc": "committed",
                               "path": ["workspaces", 0, "channels", 0, "mode"], "value": "bogus"})
    assert out["err"] and "workspaces[0].channels[0].mode" in out["err"], out
    assert out["committed"]["workspaces"][0]["channels"][0]["mode"] == "listen+speak", out["committed"]
    out = edit(hub, tmp_path, {"kind": "set", "doc": "committed",
                               "path": ["workspaces", 0, "channels", 0, "language"], "value": "xx-YY"})
    assert out["err"] and "workspaces[0].channels[0].language" in out["err"], out


def test_a_pre_existing_defect_on_the_returned_documents_refuses_the_edit(hub, tmp_path):
    """Criterion 5: the save path writes the documents the editor answers with, so a pre-existing defect in either file
    refuses the edit. A defect that the edit leaves untouched is still blocking, not only a new one."""
    committed = {"workspaces": [{"id": "T0CLIENT01", "label": "x", "class": "owned", "channels": []}]}
    local = {"version": 1, "workspaces": [{"id": "T0CLIENT01", "class": "guest", "channels": []}]}
    out = edit(hub, tmp_path, {"kind": "set", "doc": "committed", "path": ["workspaces", 0, "label"],
                               "value": "y"}, committed=committed, local=local)
    assert out["err"], out
    assert "guest team id" in out["err"], out
    assert out["committed"]["workspaces"][0]["label"] == "x", out["committed"]


def test_a_class_move_with_no_committed_workspace_is_refused_by_name(hub, tmp_path):
    """Criterion 4: a request that names the committed file but carries no workspace there answers a named refusal,
    never a crash from the move."""
    out = edit(hub, tmp_path, {"kind": "set", "doc": "committed", "path": ["workspaces", 0, "class"],
                               "value": "guest"}, committed={},
               local={"workspaces": [{"id": "T1", "class": "owned", "channels": []}]})
    assert out["err"] and "does not exist" in out["err"], out


def test_a_guest_field_on_an_owned_channel_is_refused_and_names_the_key(hub, tmp_path):
    """Criterion 3 is about a guest channel: an `owned` channel takes no `speak`, and the message names the key."""
    out = edit(hub, tmp_path, {"kind": "set", "doc": "committed",
                               "path": ["workspaces", 0, "channels", 0, "speak"], "value": "draft"})
    assert out["err"] and "workspaces[0].channels[0].speak" in out["err"], out
    assert "speak" not in out["committed"]["workspaces"][0]["channels"][0], out["committed"]


def test_the_editor_refuses_a_guest_team_id_in_the_committed_file(hub, tmp_path):
    """Criterion 5: the local file declares `T0CLIENT01` as guest, so the committed file never holds that team id. The
    refusal names the file and the key, and the documents do not change."""
    out = edit(hub, tmp_path, {"kind": "add", "doc": "committed", "path": ["workspaces"],
                               "value": {"id": "T0CLIENT01", "label": "team-c", "class": "owned", "channels": []}})
    assert out["err"], out
    assert "slack.yaml" in out["err"] and "workspaces[1].id" in out["err"], out["err"]
    assert "guest team id" in out["err"], out["err"]
    assert gid(out["committed"]) == ["T0TEAM0001"], out["committed"]


def test_the_editor_refuses_a_second_guest_workspace_with_the_mockup_text(hub, tmp_path):
    """Criterion 6: the machine serves one guest workspace at a time, so a second one is refused with the mockup's
    sentence and the policy module's own reason, and nothing is written."""
    out = edit(hub, tmp_path, {"kind": "add", "doc": "local", "path": ["workspaces"],
                               "value": {"id": "T0CLIENT02", "label": "client-b", "class": "guest", "channels": []}})
    assert out["err"], out
    assert out["err"].startswith("One guest Slack workspace at a time"), out["err"]
    assert "a second guest workspace is refused" in out["err"], out["err"]
    assert gid(out["local"]) == ["T0CLIENT01"], out["local"]


def test_a_committed_workspace_that_becomes_guest_moves_to_the_local_file(hub, tmp_path):
    """Criterion 4 from the other side: the owner changes the class to `guest`, and the row and its channels leave the
    committed file for the local one."""
    out = edit(hub, tmp_path, {"kind": "set", "doc": "committed", "path": ["workspaces", 0, "class"],
                               "value": "guest"}, local={"version": 1, "workspaces": []})
    assert out["err"] is None, out
    assert gid(out["committed"]) == []
    assert gid(out["local"]) == ["T0TEAM0001"]
    assert out["local"]["workspaces"][0]["class"] == "guest"
    assert out["local"]["workspaces"][0]["channels"][0]["id"] == "C0PROJ0001"


def test_a_guest_workspace_never_becomes_owned_in_the_committed_file(hub, tmp_path):
    """Criterion 5: a client's team id and channel ids stay out of the committed file, so the editor keeps a guest
    workspace in the local file and names the key of the refused class."""
    out = edit(hub, tmp_path, {"kind": "set", "doc": "local", "path": ["workspaces", 0, "class"],
                               "value": "owned"})
    assert out["err"] and "workspaces[0].class" in out["err"], out
    assert "stays in the local file" in out["err"], out["err"]
    assert gid(out["local"]) == ["T0CLIENT01"], out["local"]


def test_the_editor_removes_a_channel_and_a_workspace(hub, tmp_path):
    """The mockup's Remove controls: a channel goes from its workspace, and a workspace from its file."""
    out = edit(hub, tmp_path, {"kind": "remove", "doc": "committed",
                               "path": ["workspaces", 0, "channels", 0]})
    assert out["err"] is None and out["committed"]["workspaces"][0]["channels"] == [], out
    out = edit(hub, tmp_path, {"kind": "remove", "doc": "local", "path": ["workspaces", 0]})
    assert out["err"] is None and out["local"]["workspaces"] == [], out


def test_an_unknown_field_is_refused(hub, tmp_path):
    out = edit(hub, tmp_path, {"kind": "set", "doc": "committed", "path": ["workspaces", 0, "colour"],
                               "value": "blue"})
    assert out["err"] and "colour" in out["err"], out


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


def editor_html(hub, docs, open_="", msg=""):
    body = (_slices(hub.PAGE) + f"\nslDocs={json.dumps(docs)};slR=null;slOpen={json.dumps(open_)};"
            f"slMsg={json.dumps(msg)};console.log(JSON.stringify(slEditHtml()))")
    return json.loads(_node(body))


def docs(committed=None, local=None):
    return {"committed": copy.deepcopy(COMMITTED if committed is None else committed),
            "local": copy.deepcopy(LOCAL if local is None else local)}


def test_the_workspace_editor_draws_a_control_for_id_label_class_and_app(hub):
    """Criterion 1 on the page: the workspace detail carries one control per field."""
    html = editor_html(hub, docs(), open_="committed:0")
    for attr in ("committed:0::id", "committed:0::label", "committed:0::class", "committed:0::app"):
        assert f'data-sle="{attr}"' in html, (attr, html)


def test_the_channel_editor_draws_every_field_of_the_mockup(hub):
    """Criterion 2 on the page: the open channel carries `id`, `mode`, `language`, `persona`, `roles` and `announce`."""
    html = editor_html(hub, docs(), open_="committed:0:0")
    for field in ("id", "name", "mode", "language", "persona", "roles", "announce"):
        assert f'data-sle="committed:0:0:{field}"' in html, (field, html)


def test_the_guest_channel_editor_draws_the_five_guest_fields(hub):
    """Criterion 3 on the page: the open guest channel carries `speak`, `transcribe`, `voice_reply`, `poll` and
    `draft_for`, and an `owned` channel carries none of them."""
    html = editor_html(hub, docs(), open_="local:0:0")
    for field in ("speak", "transcribe", "voice_reply", "poll.mode", "poll.fast", "draft_for"):
        assert f'data-sle="local:0:0:{field}"' in html, (field, html)
    owned = editor_html(hub, docs(), open_="committed:0:0")
    for field in ("speak", "transcribe", "voice_reply", "draft_for"):
        assert f'data-sle="committed:0:0:{field}"' not in owned, (field, owned)


def test_the_add_workspace_form_offers_both_classes_and_the_add_channel_form(hub):
    """The mockup's add rows: a class choice of `owned` and `guest`, and one Add channel form per workspace."""
    html = editor_html(hub, docs())
    assert "Add Slack workspace" in html and "Add channel" in html, html
    assert 'data-slwsclass' in html, html
    assert "owned" in html and "guest" in html, html


def test_the_page_shows_the_refusal_text_the_hub_answers(hub):
    """Criterion 6 on the page: a refusal is one message above the editor, with the mockup's own sentence."""
    msg = "!One guest Slack workspace at a time: the machine serves one guest workspace at a time; a second guest workspace is refused"
    html = editor_html(hub, docs(), msg=msg)
    assert "One guest Slack workspace at a time" in html, html
    assert "a second guest workspace is refused" in html, html
