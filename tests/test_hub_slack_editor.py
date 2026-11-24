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


# ---------------------------------------------------------------- roles and members (SPEC 2682 #2699)

def with_member(local=None):
    """The local document with one member row: a Slack workspace, a channel, a role and one user id. The format is the
    one the mockup draws, and the user id is a placeholder."""
    doc = copy.deepcopy(LOCAL if local is None else local)
    doc["members"] = [{"workspace": "T0TEAM0001", "channel": "C0PROJ0001", "role": "manager",
                       "users": ["U0OWNER001"]}]
    return doc


def git(cwd, *args):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r.stdout.strip()


def hub_worktree(tmp_path):
    """A git worktree the hub owns, one commit above origin/dev, and the bare origin it fetches and pushes. The
    committed Slack file is the fixture's, so a landed save is read back from it."""
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
    c.write_text(yaml.safe_dump(COMMITTED, sort_keys=False))
    git(wt, "add", "--", "docs/squad/slack.yaml")
    git(wt, "commit", "-q", "-m", "seed the Slack file")
    git(wt, "remote", "add", "origin", str(origin))
    git(wt, "push", "-q", "origin", "dev")
    git(wt, "fetch", "-q", "origin")
    return wt, origin


def test_the_editor_saves_an_edited_role_into_slack_yaml(hub, tmp_path, monkeypatch):
    """Criterion 1: the owner edits a role's id, and the save lands the role in the committed `slack.yaml` through the
    #2694 path, while the local file keeps its value."""
    monkeypatch.setattr(hub, "STATE", tmp_path / "state")
    wt, origin = hub_worktree(tmp_path)
    monkeypatch.setenv("SG_SLACK_WORKTREE", str(wt))
    monkeypatch.setenv("SG_CHECKOUT", str(tmp_path / "loop-checkout"))
    c, loc = paths(tmp_path, local=with_member())
    new_c = copy.deepcopy(COMMITTED)
    new_c["roles"][0]["id"] = "supervisor"
    out = hub.slack_files_set({"committed": new_c, "local": with_member(), "confirm": True}, committed=c, local=loc)
    assert out.get("err") is None, out
    assert out["sha"] and git(origin, "rev-parse", "dev") == out["sha"], out
    landed = yaml.safe_load((wt / "docs" / "squad" / "slack.yaml").read_text())
    assert landed["roles"][0]["id"] == "supervisor", landed["roles"]
    assert "members" not in landed, landed


def test_the_owner_edits_a_role_id_and_its_actions(hub, tmp_path):
    """Criterion 1: a role carries an `id` and its `actions`, and each edit changes that field alone in the committed
    document. The local document keeps its value."""
    out = edit(hub, tmp_path, {"kind": "set", "doc": "committed", "path": ["roles", 0, "id"],
                               "value": "supervisor"}, local=with_member())
    assert out["err"] is None, out
    assert out["committed"]["roles"][0] == {"id": "supervisor", "actions": ["status", "logs"]}, out["committed"]
    out = edit(hub, tmp_path, {"kind": "set", "doc": "committed", "path": ["roles", 0, "actions"],
                               "value": ["status"]}, local=with_member())
    assert out["err"] is None, out
    assert out["committed"]["roles"][0]["actions"] == ["status"], out["committed"]
    assert out["local"]["members"] == with_member()["members"], out["local"]


def test_the_editor_refuses_an_action_outside_the_executor_list(hub, tmp_path):
    """Criterion 2: a role lists only the actions the executor runs. Another action is refused by its key, and the
    documents come back unchanged."""
    out = edit(hub, tmp_path, {"kind": "set", "doc": "committed", "path": ["roles", 0, "actions"],
                               "value": ["status", "reboot"]}, local=with_member())
    assert out["err"] and "roles[0].actions[1]" in out["err"], out
    assert out["committed"]["roles"][0]["actions"] == ["status", "logs"], out["committed"]


def test_the_editor_refuses_prod_in_a_role_deploy_list(hub, tmp_path):
    """Criterion 6: a role never deploys to production. `prod` in the deploy list is refused by its key, and nothing is
    written."""
    out = edit(hub, tmp_path, {"kind": "set", "doc": "committed", "path": ["roles", 0, "deploy"],
                               "value": ["stg", "prod"]}, local=with_member())
    assert out["err"] and "roles[0].deploy" in out["err"], out
    assert "deploy" not in out["committed"]["roles"][0], out["committed"]


def test_a_role_deploy_list_is_saved_from_the_typed_field(hub, tmp_path):
    """Criterion 6 from the other side: the owner types the deploy list as one comma-separated field, and the hub splits
    it into the list the file holds."""
    out = edit(hub, tmp_path, {"kind": "set", "doc": "committed", "path": ["roles", 0, "deploy"],
                               "value": "stg, stage"}, local=with_member())
    assert out["err"] is None, out
    assert out["committed"]["roles"][0]["deploy"] == ["stg", "stage"], out["committed"]


def test_a_role_edit_in_the_local_file_is_refused(hub, tmp_path):
    """Criterion 1 from the other side: the roles of the project live in the committed file, so a role edit that names
    the local file is refused."""
    out = edit(hub, tmp_path, {"kind": "set", "doc": "local", "path": ["roles", 0, "id"], "value": "supervisor"},
               local=with_member())
    assert out["err"] and "committed" in out["err"], out


def test_a_role_with_no_id_is_refused(hub, tmp_path):
    """Criterion 1: a role names its id. A role that the Add control staged and the owner saved blank is refused by its
    key, so a nameless grant never reaches the file."""
    out = edit(hub, tmp_path, {"kind": "add", "doc": "committed", "path": ["roles"],
                               "value": {"id": "", "actions": ["status"]}}, local=with_member())
    assert out["err"] and "roles" in out["err"] and "id" in out["err"], out
    assert len(out["committed"]["roles"]) == 2, out["committed"]


def test_a_member_edit_lands_in_the_local_document_only(hub, tmp_path):
    """Criteria 3 and 4: a member names its Slack workspace, channel, role and user ids, and its edit reaches only the
    local document. The committed document keeps no member key."""
    out = edit(hub, tmp_path, {"kind": "set", "doc": "local", "path": ["members", 0, "users"],
                               "value": ["U0OWNER002", "U0OWNER003"]}, local=with_member())
    assert out["err"] is None, out
    assert out["local"]["members"][0] == {"workspace": "T0TEAM0001", "channel": "C0PROJ0001", "role": "manager",
                                          "users": ["U0OWNER002", "U0OWNER003"]}, out["local"]
    assert "members" not in out["committed"], out["committed"]


def test_the_hub_splits_the_raw_user_ids_field(hub, tmp_path):
    """Criterion 3: the owner types the user ids in one field, and the hub splits the raw value into the list the policy
    module validates. The format rule stays in the hub and the policy, never in the view."""
    out = edit(hub, tmp_path, {"kind": "set", "doc": "local", "path": ["members", 0, "users"],
                               "value": "U0OWNER002, U0OWNER003"}, local=with_member())
    assert out["err"] is None, out
    assert out["local"]["members"][0]["users"] == ["U0OWNER002", "U0OWNER003"], out["local"]


def test_a_member_edit_in_the_committed_file_is_refused(hub, tmp_path):
    """Criterion 4 from the other side: a user id never lands in the committed file, so a member edit that names it is
    refused."""
    out = edit(hub, tmp_path, {"kind": "add", "doc": "committed", "path": ["members"],
                               "value": {"workspace": "T0TEAM0001", "channel": "C0PROJ0001", "role": "manager",
                                         "users": ["U0OWNER002"]}})
    assert out["err"] and "local file" in out["err"], out


def test_the_editor_refuses_a_member_that_is_not_a_user_id(hub, tmp_path):
    """Criterion 5: a member names its people by Slack user id. Any other value is refused by its key, and the local
    document comes back unchanged."""
    out = edit(hub, tmp_path, {"kind": "set", "doc": "local", "path": ["members", 0, "users"],
                               "value": ["sandro"]}, local=with_member())
    assert out["err"] and "members[0].users[0]" in out["err"], out
    assert out["local"]["members"][0]["users"] == ["U0OWNER001"], out["local"]


def test_the_editor_refuses_a_channel_outside_the_member_workspace(hub, tmp_path):
    """Criterion 3: a member names the channel of its own Slack workspace. A second workspace's channel is refused by
    its key, so a row never pairs one workspace with another workspace's channel."""
    committed = copy.deepcopy(COMMITTED)
    committed["workspaces"].append({"id": "T0TEAM0002", "label": "team-b", "class": "owned", "app": "hal9k",
                                    "channels": [{"id": "C0TEAM0002", "name": "ops", "mode": "listen",
                                                  "roles": ["manager"]}]})
    out = edit(hub, tmp_path, {"kind": "set", "doc": "local", "path": ["members", 0, "channel"],
                               "value": "C0TEAM0002"}, committed=committed, local=with_member())
    assert out["err"] and "members[0].channel" in out["err"], out
    assert out["local"]["members"][0]["channel"] == "C0PROJ0001", out["local"]


def test_the_editor_adds_and_removes_a_role_and_a_member(hub, tmp_path):
    """The mockup's Add and Remove controls: a role goes into the committed list, and a member into the local one."""
    out = edit(hub, tmp_path, {"kind": "add", "doc": "committed", "path": ["roles"],
                               "value": {"id": "auditor", "actions": ["status"]}}, local=with_member())
    assert out["err"] is None, out
    assert [r["id"] for r in out["committed"]["roles"]] == ["manager", "colleague", "auditor"], out["committed"]
    out = edit(hub, tmp_path, {"kind": "add", "doc": "local", "path": ["members"],
                               "value": {"workspace": "T0TEAM0001", "channel": "C0PROJ0001", "role": "manager",
                                         "users": ["U0OWNER002"]}}, local=with_member())
    assert out["err"] is None, out
    assert out["local"]["members"][-1]["users"] == ["U0OWNER002"], out["local"]
    assert "members" not in out["committed"], out["committed"]
    out = edit(hub, tmp_path, {"kind": "remove", "doc": "committed", "path": ["roles", 0]}, local=with_member())
    assert out["err"] is None and [r["id"] for r in out["committed"]["roles"]] == ["colleague"], out["committed"]
    out = edit(hub, tmp_path, {"kind": "remove", "doc": "local", "path": ["members", 0]}, local=with_member())
    assert out["err"] is None and out["local"]["members"] == [], out["local"]


def test_a_refused_add_leaves_the_request_value_whole(hub, tmp_path):
    """A refused Add never rewrites the request it was handed: the hub works on its own copy of the row, so a refusal
    answers the owner's own value and the next request is not built from a mutated one."""
    value = {"id": "", "deploy": "stg, stage"}
    op = {"kind": "add", "doc": "committed", "path": ["roles"], "value": value}
    out = edit(hub, tmp_path, op, local=with_member())
    assert out["err"], out
    assert value == {"id": "", "deploy": "stg, stage"}, value


def test_an_added_role_splits_its_deploy_list_and_refuses_prod(hub, tmp_path):
    """Criterion 6 on the Add path: a typed deploy list is split the way the Set path splits it, and `prod` in the
    list is refused before the row reaches the file."""
    out = edit(hub, tmp_path, {"kind": "add", "doc": "committed", "path": ["roles"],
                               "value": {"id": "deployer", "actions": ["status"], "deploy": "stg, stage"}},
               local=with_member())
    assert out["err"] is None, out
    assert out["committed"]["roles"][-1]["deploy"] == ["stg", "stage"], out["committed"]
    out = edit(hub, tmp_path, {"kind": "add", "doc": "committed", "path": ["roles"],
                               "value": {"id": "deployer", "actions": ["status"], "deploy": "stg, prod"}},
               local=with_member())
    assert out["err"] and "roles[2].deploy" in out["err"], out
    assert [r["id"] for r in out["committed"]["roles"]] == ["manager", "colleague"], out["committed"]


def test_a_stale_other_member_row_does_not_block_a_member_edit(hub, tmp_path):
    """The member channel check scopes to the row the operation touches, so an unrelated row that names a workspace the
    documents no longer hold never blocks an edit of another row."""
    local = with_member()
    local["members"].append({"workspace": "T9GONE0001", "channel": "C9GONE001", "role": "manager",
                             "users": ["U0OWNER001"]})
    out = edit(hub, tmp_path, {"kind": "set", "doc": "local", "path": ["members", 0, "users"],
                               "value": ["U0OWNER002"]}, local=local)
    assert out["err"] is None, out
    assert out["local"]["members"][0]["users"] == ["U0OWNER002"], out["local"]


def test_a_member_remove_is_not_blocked_by_a_stale_member_row(hub, tmp_path):
    """The same scope on the Remove path: removing one row is not refused because an untouched sibling names a
    workspace the documents no longer hold."""
    local = with_member()
    local["members"].append({"workspace": "T9GONE0001", "channel": "C9GONE001", "role": "manager",
                             "users": ["U0OWNER001"]})
    out = edit(hub, tmp_path, {"kind": "remove", "doc": "local", "path": ["members", 0]}, local=local)
    assert out["err"] is None, out
    assert out["local"]["members"][0]["workspace"] == "T9GONE0001", out["local"]


def test_adding_a_role_with_an_existing_id_is_refused(hub, tmp_path):
    """A role id is the role's key, so the Add refuses a second role of the same id and the list stays unique."""
    out = edit(hub, tmp_path, {"kind": "add", "doc": "committed", "path": ["roles"],
                               "value": {"id": "manager", "actions": []}}, local=with_member())
    assert out["err"] and "manager" in out["err"], out
    assert [r["id"] for r in out["committed"]["roles"]] == ["manager", "colleague"], out["committed"]


def test_renaming_a_role_to_an_existing_id_is_refused(hub, tmp_path):
    """The same rule on the Set path: two rows never share one id, because the member select and the channel roles
    read the id as the role's key."""
    out = edit(hub, tmp_path, {"kind": "set", "doc": "committed", "path": ["roles", 0, "id"],
                               "value": "colleague"}, local=with_member())
    assert out["err"] and "colleague" in out["err"], out
    assert out["committed"]["roles"][0]["id"] == "manager", out["committed"]


def test_a_refused_role_op_leaves_no_empty_role_list_behind(hub, tmp_path):
    """A Remove on a document that holds no roles is refused and leaves the document as it came: a refused edit never
    writes an empty root key."""
    out = edit(hub, tmp_path, {"kind": "remove", "doc": "committed", "path": ["roles", 0]},
               committed={"workspaces": []}, local={"version": 1, "workspaces": []})
    assert out["err"], out
    assert "roles" not in out["committed"], out["committed"]


def test_a_body_with_no_documents_edits_the_files_the_paths_name(hub, tmp_path):
    """A body that omits `committed` and `local` reads both documents from the two paths, so the edit answers a
    document of this project and never a shape refusal. The answer carries the documents it used, and the trailing
    `documents` field names the same pair."""
    c, loc = paths(tmp_path)
    op = {"kind": "set", "doc": "committed", "path": ["workspaces", 0, "label"], "value": "renamed"}
    out = hub.slack_docs_edit({"op": op}, committed=c, local=loc)
    assert "each one mapping" not in str(out.get("err") or ""), out
    assert out["err"] is None, out
    assert out["committed"]["workspaces"][0]["label"] == "renamed", out["committed"]
    assert out["local"]["workspaces"][0]["id"] == "T0CLIENT01", out["local"]
    assert out["documents"] == {"committed": out["committed"], "local": out["local"]}, out


def test_a_body_that_names_a_document_that_is_not_a_mapping_is_refused(hub, tmp_path):
    """A body that sends a document of the wrong shape is still refused by name: the loader answers only a document
    the body omits, never one the caller sent wrong."""
    c, loc = paths(tmp_path)
    body = {"committed": [], "local": {}, "op": {"kind": "set", "doc": "committed",
                                                 "path": ["workspaces", 0, "label"], "value": "x"}}
    out = hub.slack_docs_edit(body, committed=c, local=loc)
    assert out["err"] and "each one mapping" in out["err"], out


def test_the_second_guest_refusal_keeps_the_mockup_sentence(hub, tmp_path, monkeypatch):
    """The mockup's sentence belongs to the refusal, not to the policy module: a module that names no
    `SECOND_GUEST_REASON` still draws it, because the refusal matches the reason the validator answers."""
    real = hub.slack_policy()

    class NoSecond:
        def __getattr__(self, name):
            if name == "SECOND_GUEST_REASON":
                raise AttributeError(name)
            return getattr(real, name)

    monkeypatch.setattr(hub, "slack_policy", lambda: NoSecond())
    out = edit(hub, tmp_path, {"kind": "add", "doc": "local", "path": ["workspaces"],
                               "value": {"id": "T0CLIENT02", "label": "client-b", "class": "guest", "channels": []}})
    assert out["err"], out
    assert out["err"].startswith("One guest Slack workspace at a time"), out["err"]
    assert "a second guest workspace is refused" in out["err"], out["err"]


def test_a_pre_existing_defect_that_shifts_its_row_is_held_not_blamed_on_the_edit(hub, tmp_path):
    """A defect already in the file stays a held defect when the edit moves another row and shifts its index. The
    answer names the edit's own new defects apart from the ones the files already carried."""
    committed = {"workspaces": [
        {"id": "T0TEAM0001", "label": "a", "class": "owned", "channels": []},
        {"id": "T0TEAM0002", "label": "b", "class": "owned",
         "channels": [{"id": "C0TEAM0002", "name": "n", "language": "xx-YY"}]},
    ]}
    local = {"version": 1, "workspaces": []}
    out = edit(hub, tmp_path, {"kind": "set", "doc": "committed", "path": ["workspaces", 0, "class"],
                               "value": "guest"}, committed=committed, local=local)
    assert out["err"], out
    assert out["new_defects"] == [], out
    assert any("language" in d for d in out["held_defects"]), out


def test_the_draft_edit_does_not_round_trip_the_documents_through_yaml(hub, tmp_path, monkeypatch):
    """The draft is a copy, so the edit never serializes the documents. A YAML writer that refuses is no obstacle, and
    a scalar that reads as a boolean keeps its own text."""
    committed = copy.deepcopy(COMMITTED)
    committed["workspaces"][0]["label"] = "yes"
    c, loc = paths(tmp_path, committed)

    def boom(*a, **k):
        raise AssertionError("the draft must not round trip through yaml")
    monkeypatch.setattr(hub.yaml, "safe_dump", boom)
    out = hub.slack_docs_edit({"committed": committed, "local": copy.deepcopy(LOCAL),
                               "op": {"kind": "set", "doc": "committed",
                                      "path": ["workspaces", 0, "app"], "value": "hal9k"}},
                              committed=c, local=loc)
    assert out["err"] is None, out
    assert out["committed"]["workspaces"][0]["label"] == "yes", out["committed"]


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


def test_the_guest_channel_editor_draws_the_exponential_poll_fields(hub):
    """Criterion 3: a guest channel whose poll mode is `exponential` draws the four fields that mode reads, in place
    of the two-state `fast` and `slow`."""
    local = copy.deepcopy(LOCAL)
    local["workspaces"][0]["channels"][0]["poll"] = {"mode": "exponential", "floor": "5m", "ceiling": "1h",
                                                     "factor": 2, "idle_window": "15m"}
    html = editor_html(hub, docs(local=local), open_="local:0:0")
    for field in ("poll.floor", "poll.ceiling", "poll.factor", "poll.idle_window"):
        assert f'data-sle="local:0:0:{field}"' in html, (field, html)
    for field in ("poll.fast", "poll.slow"):
        assert f'data-sle="local:0:0:{field}"' not in html, (field, html)


def test_the_read_table_heads_are_the_editor_heads_without_the_edit_column(hub):
    """The read-only table and the editor draw one header list: the editor adds the guest columns and the Edit column,
    and the read table keeps the six base columns, so no column is named in one place only."""
    read = json.loads(_node(_slices(hub.PAGE) + """
const r={workspaces:[{label:"w",class:"owned",file:"slack.yaml",channels:[{name:"n",id:"C1",mode:"listen",
 language:"en-US",persona:"",roles:[],announce:[]}]}]};
console.log(JSON.stringify(slWsReadHtml(r,(xs,or)=>xs.length?xs.join(", "):or)))"""))
    read_heads = re.findall(r"<th>(.*?)</th>", read)
    editor = editor_html(hub, docs())
    editor_heads = re.findall(r"<th>(.*?)</th>", editor)
    assert read_heads == ["Channel", "Mode", "Language", "Persona", "Roles", "Announce"], read_heads
    assert editor_heads[:len(read_heads)] == read_heads, (read_heads, editor_heads)


def test_the_add_workspace_form_offers_both_classes_and_the_add_channel_form(hub):
    """The mockup's add rows: a class choice of `owned` and `guest`, and one Add channel form per workspace."""
    html = editor_html(hub, docs())
    assert "Add Slack workspace" in html and "Add channel" in html, html
    assert 'data-slwsclass' in html, html
    assert "owned" in html and "guest" in html, html


def roles_html(hub, committed=None, local=None, actions=None):
    """The roles and members editor alone, drawn from the two raw documents and the hub's action list. `slR` carries
    the files and the actions, as the read view answers them. The action list default comes from the policy module at
    run time, so the test holds no copy of the executor's list."""
    acts = list(hub.slack_policy().role_actions()) if actions is None else actions
    r = {"files": {"committed": "slack.yaml", "local": "4.slack.local.yaml"}, "role_actions": acts}
    body = (_slices(hub.PAGE)
            + f"\nconst r={json.dumps(r)};slDocs={json.dumps(docs(committed, local))};slR=r;"
            + "slSavedDocs=slClone(slDocs);slMsg='';"
            + "console.log(JSON.stringify(slRolesEditHtml(r)))")
    return json.loads(_node(body))


def doc_op(hub, attr, el):
    """The operation the edit handler builds for one control, before it reaches the hub."""
    body = (_slices(hub.PAGE) + f"\nslDocs={json.dumps(docs(local=with_member()))};slSavedDocs=slClone(slDocs);"
            f"console.log(JSON.stringify(slDocOp({json.dumps(attr)},{json.dumps(el)})))")
    return json.loads(_node(body))


def two_workspaces():
    committed = copy.deepcopy(COMMITTED)
    committed["workspaces"].append({"id": "T0TEAM0002", "label": "team-b", "class": "owned", "app": "hal9k",
                                    "channels": [{"id": "C0TEAM0002", "name": "ops", "mode": "listen",
                                                  "roles": ["manager"]}]})
    return committed


def test_the_roles_editor_draws_the_id_a_checkbox_per_action_and_the_deploy(hub):
    """Criterion 1 on the page: each role is one row with a field for its id, one checkbox per action the executor
    runs, and a field for its deploy list. The actions come from the hub's read, never a copy in the view."""
    html = roles_html(hub)
    acts = list(hub.slack_policy().role_actions())
    assert 'data-sle="role:0:id"' in html and 'value="manager"' in html, html
    for action in acts:
        assert f'data-sle="role:0:act:{action}"' in html, (action, html)
    assert re.search(r'data-sle="role:0:act:status"[^>]* checked', html), html
    assert re.search(r'data-sle="role:0:act:logs"[^>]* checked', html), html
    assert not re.search(r'data-sle="role:0:act:approve_and_move"[^>]* checked', html), html
    assert 'data-sle="role:0:deploy"' in html, html
    assert "data-sladdrole" in html and "data-slrmrole" in html, html


def test_the_members_editor_draws_the_four_fields_of_one_format(hub):
    """Criterion 3 on the page: a member is one row of the four fields of the mockup: Slack workspace, channel, role
    and user ids. The row is drawn from the local document, and the controls name the ids the files hold."""
    html = roles_html(hub, local=with_member())
    for field in ("workspace", "channel", "role", "users"):
        assert f'data-sle="member:0:{field}"' in html, (field, html)
    assert 'value="U0OWNER001"' in html, html
    assert "T0TEAM0001" in html and "C0PROJ0001" in html, html
    assert "data-sladdmem" in html and "data-slrmmem" in html, html


def test_the_member_channel_select_offers_only_its_workspace_channels(hub):
    """Criterion 3 on the page: the channel control of a member row names the channels of the member's Slack workspace
    alone, so the owner never pairs a workspace with another workspace's channel."""
    local = with_member()
    local["members"][0]["workspace"] = "T0TEAM0002"
    local["members"][0]["channel"] = "C0TEAM0002"
    html = roles_html(hub, committed=two_workspaces(), local=local)
    row = html[html.index('data-sle="member:0:workspace"'):]
    row = row[:row.index("data-sle=\"member:0:role\"")]
    assert "C0TEAM0002" in row, row
    assert "C0PROJ0001" not in row, row


def test_a_role_edit_builds_a_committed_operation_and_a_member_a_local_one(hub):
    """The edit handler places each change in the file the policy names: a role in the committed document, and a
    member in the local one. A checked action joins the role's list, and the raw user id and deploy fields travel to
    the hub, which splits them."""
    op = doc_op(hub, "role:0:act:approve_and_move", {"type": "checkbox", "checked": True})
    assert op == {"kind": "set", "doc": "committed", "path": ["roles", 0, "actions"],
                  "value": ["status", "logs", "approve_and_move"]}, op
    op = doc_op(hub, "member:0:users", {"type": "text", "value": "U0OWNER002, U0OWNER003"})
    assert op == {"kind": "set", "doc": "local", "path": ["members", 0, "users"],
                  "value": "U0OWNER002, U0OWNER003"}, op
    op = doc_op(hub, "role:0:deploy", {"type": "text", "value": "stg, prod"})
    assert op == {"kind": "set", "doc": "committed", "path": ["roles", 0, "deploy"], "value": "stg, prod"}, op


def test_a_delivery_edit_on_a_committed_channel_writes_the_local_file_only(hub):
    """#2825 defect: a Delivery field on a channel whose row is committed goes to the local file, never the committed one."""
    op = doc_op(hub, "committed:0:0:send_grant", {"type": "text", "value": "U0CLIENT02"})
    assert op["doc"] == "local", op
    op = doc_op(hub, "committed:0:0:poll.fast", {"type": "text", "value": "2m"})
    assert op["doc"] == "local", op


def test_the_page_shows_the_refusal_text_the_hub_answers(hub):
    """Criterion 6 on the page: a refusal is one message above the editor, with the mockup's own sentence."""
    msg = "!One guest Slack workspace at a time: the machine serves one guest workspace at a time; a second guest workspace is refused"
    html = editor_html(hub, docs(), msg=msg)
    assert "One guest Slack workspace at a time" in html, html
    assert "a second guest workspace is refused" in html, html
