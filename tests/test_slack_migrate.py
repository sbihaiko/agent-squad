"""GitHub issue #2700: the one-time migration of the old per-profile Slack file (story 46, SPEC 2682).

The old file is `<profile>.slack.json`, written by Settings > Communication and read by no process. The migration
follows the spec's field table: the language becomes `project.language`, `tick` and `max` become the `two_state` poll
of each migrated channel, a channel that is not watched is dropped, and a channel with no Slack id stays pending until
the owner gives the id in the channel editor. The seam under test is the policy module's pure `migrate`: the hub's own
reader of the old file retired with the file (#2701). Every id is a placeholder.

Guard: `squad/slackpolicy.py` (`migrate`).
"""
import copy
import json
import pathlib

from squad import slackpolicy

ROOT = pathlib.Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib" / "squad-goal" / "scripts" / "hub.py"

OLD = {
    "connection": "connected",
    "prefix": "[agent]",
    "lang": "pt-br",
    "tick": 5,
    "max": 30,
    "quiet": "22-07",
    "other": "reply",
    "dm": True,
    "audio": False,
    "channels": [
        {"name": "#proj-agent", "lang": "pt-br", "watch": True, "thread": True, "mention": False,
         "events": {"stg": True, "prod": True, "dev": False, "bug": True, "fix": False}},
        {"name": "#off", "lang": "en-US", "watch": False, "thread": True, "mention": False,
         "events": {"stg": True, "prod": True, "dev": True, "bug": True, "fix": True}},
        {"name": "#alerts", "lang": "es", "watch": True, "thread": False, "mention": True,
         "events": {"stg": False, "prod": True, "dev": False, "bug": False, "fix": False}},
    ],
    "people": [{"name": "Ana", "role": "Manager"}, {"name": "Bo", "role": "Colleague"}],
    "templates": {
        "stg": "<prefix>\nDeployed to STG. PR <pr>.",
        "prod": "<prefix>\nDeployed to PROD. PR <pr>.",
        "dev": "<prefix>\nLanded in dev.",
        "bug": "<prefix>\nA bug: <issue>.",
        "fix": "<prefix>\n<state>.",
    },
}
WORKSPACE = {"id": "T0TEAM0001", "label": "team"}
IDS = {"#proj-agent": "C0PROJ0001", "#alerts": "C0ALERT001"}


def mig(old=None, workspace=WORKSPACE, ids=IDS):
    return slackpolicy.migrate(copy.deepcopy(OLD if old is None else old), workspace=workspace, ids=ids)


def channels(m):
    return [c for w in m.local.get("workspaces") or [] for c in w.get("channels") or []]


def by_label(m):
    return {c.get("name"): c for c in channels(m)}


def events(m):
    return {e.get("id"): e.get("template") for e in m.committed.get("events") or []}


# ---------------------------------------------------------------- the field table

def test_the_old_language_becomes_the_project_language_normalized():
    """Criterion 2: `pt-br`, `es` and `en-US` land on `project.language` as valid BCP 47 tags."""
    assert mig().committed["project"]["language"] == "pt-BR"
    assert mig(old={**OLD, "lang": "es"}).committed["project"]["language"] == "es-ES"
    assert mig(old={**OLD, "lang": "en-US"}).committed["project"]["language"] == "en-US"


def test_a_channel_language_is_normalized_and_carried():
    """Criterion 1: `channels[].lang` is the `language` of the channel."""
    assert by_label(mig())["proj-agent"]["language"] == "pt-BR"
    assert by_label(mig())["alerts"]["language"] == "es-ES"


def test_tick_and_max_become_fast_and_slow_of_a_two_state_poll_per_channel():
    """Criterion 3: both migrated channels carry the poll, and so does a channel still pending its id."""
    for ch in channels(mig()):
        assert ch["poll"] == {"mode": "two_state", "fast": "5m", "slow": "30m"}, ch
    pending = {p["name"]: p for p in mig(ids={}).pending}
    assert pending["proj-agent"]["poll"] == {"mode": "two_state", "fast": "5m", "slow": "30m"}


def test_a_channel_with_watch_false_is_not_migrated():
    """Criterion 4: a channel that is not watched appears in no document and in no pending row."""
    m = mig(ids={})
    assert "#off" not in {c.get("name") for c in channels(m)}
    assert "off" not in {p.get("name") for p in m.pending}
    assert any(line.startswith("dropped: channel #off") for line in m.report), m.report


def test_a_channel_with_no_slack_id_stays_pending_and_the_hub_asks():
    """Criterion 5: without an id the channel is a pending row outside both documents, and the report names the ask.
    With the id given, the same channel is written."""
    m = mig(ids={"#alerts": "C0ALERT001"})
    assert [p["name"] for p in m.pending] == ["proj-agent"]
    assert [c["id"] for c in channels(m)] == ["C0ALERT001"]
    assert "proj-agent" not in json.dumps(m.committed) + json.dumps(m.local)
    line = next(line for line in m.report if line.startswith("pending: channel proj-agent"))
    assert "the hub asks" in line and "Slack id" in line, line
    done = mig()
    assert {c["id"] for c in channels(done)} == {"C0PROJ0001", "C0ALERT001"}
    assert done.pending == []


def test_pending_names_only_the_channels_with_no_slack_id():
    """Criterion 5: `pending` is the id-ask list, so a channel that already carries an id never lands in it, even when
    the workspace team id is missing. A channel with an id but no workspace goes to `unplaced`, and the report says so."""
    m = mig(workspace=None)
    assert m.pending == [], m.pending
    assert [c["name"] for c in m.unplaced] == ["proj-agent", "alerts"], m.unplaced
    mixed = mig(workspace=None, ids={"#alerts": "C0ALERT001"})
    assert [p["name"] for p in mixed.pending] == ["proj-agent"], mixed.pending
    assert [c["name"] for c in mixed.unplaced] == ["alerts"], mixed.unplaced
    assert any(line.startswith("unplaced: channel alerts") for line in mixed.report), mixed.report
    assert any(line.startswith("pending: the Slack workspace has no team id") for line in mixed.report), mixed.report


def test_manager_and_colleague_become_manager_and_colleague_with_only_status():
    """Criterion 6: both roles exist under the new id, and each lists only `status`."""
    roles = {r["id"]: r for r in mig().committed["roles"]}
    assert set(roles) == {"manager", "colleague"}
    assert all(r["actions"] == ["status"] for r in roles.values())


def test_channel_events_become_announce_and_templates_lose_the_prefix():
    """Criterion 1: `channels[].events` and `templates` land on `announce` and on the project's events, after the
    mark. The `prefix` placeholder never survives into a template."""
    assert events(mig()) == {
        "stg_release": "Deployed to STG. PR <pr>.",
        "prod_release": "Deployed to PROD. PR <pr>.",
        "dev_release": "Landed in dev.",
        "bug_report": "A bug: <issue>.",
        "fix_report": "<state>.",
    }
    assert by_label(mig())["proj-agent"]["announce"] == ["stg_release", "prod_release", "bug_report"]
    assert by_label(mig())["alerts"]["announce"] == ["prod_release"]


def test_a_migrated_channel_listens_and_speaks():
    """The old theme posts replies and announcements in its channels, so a migrated channel is `listen+speak`."""
    assert by_label(mig())["proj-agent"]["mode"] == "listen+speak"


def test_the_workspace_is_a_guest_of_the_local_file():
    """The old file posted through the owner's account by the connector: that is the `guest` class, so the workspace
    lives in the local file with the team id the caller gives."""
    local = mig().local
    assert [w["id"] for w in local["workspaces"]] == ["T0TEAM0001"]
    assert [w["class"] for w in local["workspaces"]] == ["guest"]


def test_the_dropped_fields_never_enter_the_documents():
    """Criterion 7: the eight fields the table drops appear in no document."""
    m = mig()
    text = json.dumps({"committed": m.committed, "local": m.local})
    assert "<prefix>" not in text
    for key in ("prefix", "thread", "mention", "quiet", "people", "other", "dm", "audio"):
        assert f'"{key}"' not in text, (key, text)


def test_the_dropped_fields_are_named_in_the_report():
    """Criterion 8: each dropped field has its own line in the report."""
    m = mig()
    for word in ("prefix", "thread", "mention", "people", "quiet", "other", "dm", "audio"):
        assert any(line.startswith("dropped:") and word in line for line in m.report), (word, m.report)
    assert any(line.startswith("carried: language pt-br becomes project.language pt-BR") for line in m.report), m.report


def test_the_migrated_documents_pass_the_slack_validator():
    """Criterion 1 end to end: the pair the migration builds is a valid `slack.yaml` and `slack.local.yaml`."""
    m = mig()
    assert slackpolicy.validate(m.committed, m.local) == []
