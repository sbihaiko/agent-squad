"""GitHub issue #2844: the tree detail pane edits a channel, and each field writes the file that owns it (SPEC 2682).

The detail pane of a channel node edits the channel's settings. The address is the channel's Slack id, never its place
in a list, so an edit still reaches the row that carries the id after the list changes order. A delivery field
(`send_grant`, `draft_for`, every poll setting, transcription and voice reply) writes the profile's local file, where
`send_grant` is valid; a presentation field (mode, persona, language, the accepted roles and announce) writes the file
that holds the channel row. Every setting row names its file in a chip.

The seams under test are the hub's own editor, `slack_docs_edit`, the hub's read, `slack_overview`, whose `tree` key is
the tree, and the theme's renderer run in node. No browser runs here. Every id and name is a neutral placeholder.
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
    "roles": [{"id": "manager", "actions": ["status"]}, {"id": "colleague", "actions": ["status"]}],
    "workspaces": [
        {"id": "T0TEAM0001", "label": "team", "class": "owned", "app": "hal9k",
         "channels": [
             {"id": "C0PROJ0001", "name": "proj-agent-squad", "mode": "listen+speak", "roles": ["manager"],
              "announce": ["stg_release"]},
             {"id": "C0ALERT001", "name": "squad-alerts", "mode": "listen", "persona": "hal"},
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
}

# The Slack Connect shape (#2770): the committed file holds the channel row of an owned workspace, and the local file
# narrows that one channel to `guest`. Its delivery fields belong to the local file alone.
OVERLAY = {
    "version": 1,
    "workspaces": [
        {"id": "T0TEAM0001", "label": "team",
         "channels": [
             {"id": "C0ALERT001", "class": "guest", "mode": "listen+speak", "speak": "send"},
             {"id": "C0PROJ0001"},
         ]},
    ],
}


@pytest.fixture(scope="module")
def hub():
    """The hub module under a pinned environment, the same pin the tree suite uses, so the policy module resolves from
    this tree and never from a loop checkout the caller exported."""
    mp = pytest.MonkeyPatch()
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("SG_SQUAD", str(ROOT))):
        mp.setenv(k, v)
    for k in ("SG_HUB_DIR", "SG_STATE", "SG_CHECKOUT", "SG_SHARED", "SG_PROFILES_DIR", "SG_SLACK_WORKTREE"):
        mp.setenv(k, tempfile.mkdtemp())
    spec = importlib.util.spec_from_file_location("hub_slack_field_file", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    yield mod
    mp.undo()


def paths(tmp_path, committed=None, local=None):
    c = tmp_path / "checkout" / "docs" / "squad" / "slack.yaml"
    c.parent.mkdir(parents=True, exist_ok=True)
    c.write_text(yaml.safe_dump(COMMITTED if committed is None else committed, sort_keys=False))
    loc = tmp_path / "profiles" / "4.slack.local.yaml"
    loc.parent.mkdir(parents=True, exist_ok=True)
    loc.write_text(yaml.safe_dump(LOCAL if local is None else local, sort_keys=False))
    return c, loc


def field(hub, tmp_path, channel, name, value, committed=None, local=None):
    """One tree-pane edit: the channel by its Slack id, the field and its value."""
    c, loc = paths(tmp_path, committed, local)
    body = {"committed": copy.deepcopy(COMMITTED if committed is None else committed),
            "local": copy.deepcopy(LOCAL if local is None else local),
            "op": {"kind": "field", "channel": channel, "field": name, "value": value}}
    return hub.slack_docs_edit(body, committed=c, local=loc)


def read(hub, tmp_path, committed=None, local=None):
    c, loc = paths(tmp_path, committed, local)
    return hub.slack_overview(c, loc)


def walk(node):
    yield node
    for child in node.get("children") or []:
        yield from walk(child)


def by_name(root, name):
    for node in walk(root):
        if node["name"] == name:
            return node
    raise AssertionError(f"the tree names no node {name!r}: {[n['name'] for n in walk(root)]}")


def channel_row(doc, ws_id, ch_id):
    ws = next(w for w in doc["workspaces"] if str(w.get("id")) == ws_id)
    return next(c for c in ws["channels"] if str(c.get("id")) == ch_id)


def expected(committed=None, local=None, edit=None):
    """The two documents with one edit applied by hand: an independent source of truth for the hub's answer."""
    c = copy.deepcopy(COMMITTED if committed is None else committed)
    loc = copy.deepcopy(LOCAL if local is None else local)
    edit(c, loc)
    return c, loc


# ---------------------------------------------------------------- the address is the Slack channel id

def test_an_edit_names_its_channel_by_id_and_writes_the_row_that_carries_that_id(hub, tmp_path):
    """Criterion 1: the operation names `C0ALERT001`, and the save writes that row alone."""
    out = field(hub, tmp_path, "C0ALERT001", "mode", "listen+speak")
    assert out["err"] is None, out
    assert channel_row(out["committed"], "T0TEAM0001", "C0ALERT001")["mode"] == "listen+speak"
    assert channel_row(out["committed"], "T0TEAM0001", "C0PROJ0001")["mode"] == "listen+speak"
    want_c, want_l = expected(edit=lambda c, loc: channel_row(
        c, "T0TEAM0001", "C0ALERT001").update(mode="listen+speak"))
    assert (out["committed"], out["local"]) == (want_c, want_l)


def test_an_edit_reaches_the_right_row_after_the_channel_list_changes_order(hub, tmp_path):
    """Criterion 2: the same edit, on a document whose channel list holds the rows in the other order, changes the row
    that carries the id and no other. The local file's overlay list is reordered too, so neither file's order answers."""
    committed = copy.deepcopy(COMMITTED)
    committed["workspaces"][0]["channels"].reverse()
    local = copy.deepcopy(OVERLAY)
    local["workspaces"][0]["channels"].reverse()
    out = field(hub, tmp_path, "C0ALERT001", "mode", "listen+speak", committed=committed, local=local)
    assert out["err"] is None, out
    assert [c["id"] for c in out["committed"]["workspaces"][0]["channels"]] == ["C0ALERT001", "C0PROJ0001"]
    assert channel_row(out["committed"], "T0TEAM0001", "C0ALERT001")["mode"] == "listen+speak"
    want_c, want_l = expected(committed, local, edit=lambda c, loc: channel_row(
        c, "T0TEAM0001", "C0ALERT001").update(mode="listen+speak"))
    assert (out["committed"], out["local"]) == (want_c, want_l)   # one row changed, and no order moved


def test_an_unknown_channel_id_is_refused(hub, tmp_path):
    out = field(hub, tmp_path, "C0NOSUCH01", "mode", "listen")
    assert out["err"] and "C0NOSUCH01" in out["err"], out


# ---------------------------------------------------------------- delivery fields write the local file

def test_a_delivery_edit_on_a_channel_with_a_committed_row_writes_the_local_file_only(hub, tmp_path):
    """Criterion 5: the committed file holds the row and the local file narrows it to `guest`. A grant written into the
    committed file is invalid, so the edit lands in the local row that carries the id and the committed file keeps
    its bytes."""
    out = field(hub, tmp_path, "C0ALERT001", "send_grant", "2026-11-24", local=OVERLAY)
    assert out["err"] is None, out
    assert out["committed"] == COMMITTED, out["committed"]
    row = channel_row(out["local"], "T0TEAM0001", "C0ALERT001")
    assert row["send_grant"] == "2026-11-24" and row["class"] == "guest", row
    assert "send_grant" not in channel_row(out["local"], "T0TEAM0001", "C0PROJ0001")


def test_every_delivery_field_writes_the_local_file(hub, tmp_path):
    """Criterion 3: `send_grant`, `draft_for`, the poll settings, transcription and voice reply land in the local row."""
    cases = (("send_grant", "2026-11-24"), ("draft_for", "U0CLIENT01, U0CLIENT02"), ("poll.fast", "2m"),
             ("poll.slow", "15m"), ("transcribe", "local"), ("voice_reply", True))
    committed, local = None, None
    for name, value in cases:
        out = field(hub, tmp_path, "C0CLISUP01", name, value, committed=committed, local=local)
        assert out["err"] is None, (name, out)
        assert out["committed"] == COMMITTED, (name, out["committed"])
        committed, local = out["committed"], out["local"]
    row = channel_row(local, "T0CLIENT01", "C0CLISUP01")
    assert row["send_grant"] == "2026-11-24" and row["draft_for"] == ["U0CLIENT01", "U0CLIENT02"], row
    assert row["poll"] == {"fast": "2m", "slow": "15m"}, row["poll"]
    assert row["transcribe"] == "local" and row["voice_reply"] is True, row


def test_a_delivery_field_on_an_owned_channel_is_refused_and_names_the_key(hub, tmp_path):
    """The delivery fields belong to a guest channel, and the refusal names the row of the file it would write."""
    out = field(hub, tmp_path, "C0PROJ0001", "send_grant", "2026-11-24")
    assert out["err"] and "send_grant" in out["err"] and "guest channel" in out["err"], out
    assert out["committed"] == COMMITTED and out["local"] == LOCAL, out


# ---------------------------------------------------------------- presentation fields write the row's file

def test_presentation_fields_write_the_file_that_holds_the_channel_row(hub, tmp_path):
    """Criterion 4: on a committed row the five fields write `slack.yaml`; on a local-only row they write the local
    file. The other file keeps its bytes in each case."""
    for name, value in (("mode", "listen"), ("persona", "iara"), ("language", "pt-BR"),
                        ("roles", "manager, colleague"), ("announce", "stg_release")):
        out = field(hub, tmp_path, "C0PROJ0001", name, value)
        assert out["err"] is None, (name, out)
        assert out["local"] == LOCAL, (name, out["local"])
        assert channel_row(out["committed"], "T0TEAM0001", "C0PROJ0001")[name] == (
            ["manager", "colleague"] if name == "roles" else ["stg_release"] if name == "announce" else value), (name, out)
        out = field(hub, tmp_path, "C0CLIGEN01", name, value, local=copy.deepcopy(LOCAL))
        assert out["err"] is None, (name, out)
        assert out["committed"] == COMMITTED, (name, out["committed"])
        assert channel_row(out["local"], "T0CLIENT01", "C0CLIGEN01")[name] == (
            ["manager", "colleague"] if name == "roles" else ["stg_release"] if name == "announce" else value), (name, out)


def test_a_cleared_language_or_persona_leaves_its_key_out_of_the_file(hub, tmp_path):
    """An empty select inherits, so the key leaves the row: the file holds the values the owner chose, never a blank."""
    committed = copy.deepcopy(COMMITTED)
    committed["workspaces"][0]["channels"][0].update(language="pt-BR", persona="hal")
    out = field(hub, tmp_path, "C0PROJ0001", "language", "", committed=committed)
    assert out["err"] is None, out
    row = channel_row(out["committed"], "T0TEAM0001", "C0PROJ0001")
    assert "language" not in row and row["persona"] == "hal", row
    out = field(hub, tmp_path, "C0PROJ0001", "persona", "", committed=committed)
    assert out["err"] is None, out
    assert "persona" not in channel_row(out["committed"], "T0TEAM0001", "C0PROJ0001")


# ---------------------------------------------------------------- the read names the file of every row

def test_every_setting_row_of_a_guest_channel_names_the_local_file(hub, tmp_path):
    """Criterion 6 at the read seam: a guest workspace's channel writes only the local file, and each row says so."""
    node = by_name(read(hub, tmp_path)["tree"]["root"], "#support")
    files = {row["file"] for row in node["settings"]["rows"]}
    assert files == {"4.slack.local.yaml"}, files
    keys = [row["key"] for row in node["settings"]["rows"]]
    for key in ("mode", "persona", "language", "delivery", "draft_for", "watch", "audio", "roles", "announce"):
        assert key in keys, (key, keys)


def test_each_field_row_names_its_own_file_on_a_slack_connect_channel(hub, tmp_path):
    """Criteria 4 and 6: the committed row holds mode, persona and language, and the local overlay holds the delivery
    fields, so one pane names both files, row by row."""
    node = by_name(read(hub, tmp_path, local=OVERLAY)["tree"]["root"], "#squad-alerts")
    rows = {row["key"]: row for row in node["settings"]["rows"]}
    assert rows["mode"]["file"] == "slack.yaml" and rows["mode"]["doc"] == "committed", rows["mode"]
    assert rows["delivery"]["file"] == "4.slack.local.yaml" and rows["delivery"]["doc"] == "local", rows["delivery"]
    assert rows["draft_for"]["doc"] == "local" and rows["watch"]["doc"] == "local" and rows["audio"]["doc"] == "local"
    assert [c["field"] for c in rows["delivery"]["controls"]] == ["speak", "send_grant"], rows["delivery"]


def test_an_owned_channel_offers_no_delivery_row(hub, tmp_path):
    """The delivery fields belong to a guest channel, so an owned channel's pane draws the presentation rows alone."""
    node = by_name(read(hub, tmp_path)["tree"]["root"], "#proj-agent-squad")
    keys = [row["key"] for row in node["settings"]["rows"]]
    assert "delivery" not in keys and "watch" not in keys and "audio" not in keys, keys
    assert {row["file"] for row in node["settings"]["rows"]} == {"slack.yaml"}


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


def render(hub, out, sel, docs=None):
    docs = {"committed": copy.deepcopy(COMMITTED), "local": copy.deepcopy(LOCAL)} if docs is None else docs
    body = (_slices(hub.PAGE) + f"\nslS={{}};slR={json.dumps(out)};slDocs={json.dumps(docs)};slTsel={json.dumps(sel)};"
            "slRender();console.log(JSON.stringify(STORE['#sbody'].innerHTML))")
    return json.loads(_node(body))


def render_field_op(hub, channel, name, el):
    body = (_slices(hub.PAGE) + f"\nconsole.log(JSON.stringify(slFieldOp({json.dumps(channel)},{json.dumps(name)},"
            f"{json.dumps(el)})))")
    return json.loads(_node(body))


def test_the_channel_detail_draws_one_setting_row_per_field_with_its_file_chip(hub, tmp_path):
    """Criterion 6 on the page: the selected channel's detail carries a settings row for each field, and each row names
    its file in a chip."""
    out = read(hub, tmp_path)
    html = render(hub, out, "c0.p0.w1.c0")
    rows = re.findall(r'<div class=slsetrow[^>]*>(.*?)</div>', html, re.S)
    assert rows, html
    for row in rows:
        assert 'class=slfile' in row, row
        assert "4.slack.local.yaml" in row, row
    body = html[html.index("Settings"):]
    for field in ("mode", "persona", "language", "send_grant", "draft_for", "poll.fast", "transcribe", "voice_reply"):
        assert f'data-slf="C0CLISUP01|{field}"' in body, (field, body[:2000])


def test_the_pane_names_both_files_when_the_rows_live_in_both(hub, tmp_path):
    """Criterion 6 on the page: the Slack Connect channel's presentation rows carry `slack.yaml`, and its delivery rows
    carry the local file."""
    out = read(hub, tmp_path, local=OVERLAY)
    html = render(hub, out, "c0.p0.w0.c1", docs={"committed": copy.deepcopy(COMMITTED), "local": copy.deepcopy(OVERLAY)})
    rows = re.findall(r'<div class=slsetrow[^>]*>(.*?)</div>', html, re.S)
    by_field = {re.search(r'data-slf="[^|]+\|([^"]+)"', row).group(1): row
                for row in rows if re.search(r'data-slf="[^|]+\|([^"]+)"', row)}
    assert "slack.yaml" in by_field["mode"] and "4.slack.local.yaml" not in by_field["mode"], by_field["mode"]
    assert "4.slack.local.yaml" in by_field["send_grant"], by_field["send_grant"]


def test_the_edit_handler_builds_one_operation_that_names_the_channel_and_not_its_index(hub):
    """Criterion 1 on the page: the control carries the channel id and the field, never the row's position."""
    op = render_field_op(hub, "C0CLISUP01", "mode", {"type": "select-one", "value": "listen", "checked": False})
    assert op == {"kind": "field", "channel": "C0CLISUP01", "field": "mode", "value": "listen"}, op
    op = render_field_op(hub, "C0CLISUP01", "voice_reply", {"type": "checkbox", "value": "on", "checked": True})
    assert op == {"kind": "field", "channel": "C0CLISUP01", "field": "voice_reply", "value": True}, op
