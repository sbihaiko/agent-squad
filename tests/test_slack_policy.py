"""The Slack file rules (ADR-0177): a committed `slack.yaml`, a local `slack.local.yaml`, and one defect per failing key.

Guards: a token pattern is refused in every string of both files, the `tone` included; a persona name outside the letters,
digits, space, period, hyphen and underscore set, or over 32 characters, is refused; a voice outside the Gemini list, a
`tone` over 300 characters, and a language outside the three tags after normalization are refused; a `guest` team id or
channel id in the committed file is refused; `prod` in a role deploy list, a second `guest` workspace on the machine, and a
key named `secret` are refused; a `send_grant` outside the local file, or without a date, is refused; a language tag
normalizes; the new ADR names the one home of the rules.
"""
import copy
import datetime
import subprocess
import sys
from pathlib import Path

import pytest

from squad import slackpolicy as S

COMMITTED = {
    "version": 1,
    "project": {"language": "en-US", "persona": "hal"},
    "apps": [{"id": "hal9k", "keychain": {"bot": "agent-squad-slack-bot", "app": "agent-squad-slack-app"}}],
    "personas": [
        {"id": "hal", "name": "HAL9K", "voice": "Kore", "tone": "Calm and direct. Starts with the result."},
        {"id": "iara", "name": "Iara", "voice": "Sulafat", "tone": "Warm and patient."},
    ],
    "events": [{"id": "stg_release", "template": "Subiu para STG: {items}."}],
    "roles": [{"id": "manager", "actions": ["status", "logs"]}],
    "workspaces": [
        {"id": "T0TEAM0001", "label": "team", "class": "owned", "app": "hal9k", "language": "en-US", "persona": "hal",
         "channels": [{"id": "C0PROJ0001", "name": "proj-agent-squad", "mode": "listen+speak", "language": "pt-BR",
                       "persona": "hal", "roles": ["manager"]}]},
    ],
}

LOCAL = {
    "version": 1,
    "workspaces": [
        {"id": "T0CLIENT01", "label": "client-a", "class": "guest", "language": "en-US", "persona": "iara",
         "channels": [{"id": "C0CLISUP01", "name": "support", "mode": "listen"}]},
    ],
    "members": [{"workspace": "T0TEAM0001", "channel": "C0PROJ0001", "role": "manager", "users": ["U0OWNER001"]}],
}


def files(**over):
    """A valid committed and local pair; a keyword names a key of one document to replace."""
    committed, local = copy.deepcopy(COMMITTED), copy.deepcopy(LOCAL)
    for name, value in over.items():
        target, _, rest = name.partition("__")
        doc = committed if target == "committed" else local
        head, _, tail = rest.partition("__")
        if tail:
            a, _, b = tail.partition("__")
            doc[head][int(a)][b] = value
        else:
            doc[head] = value
    return committed, local


def keys(defects):
    return [d.key for d in defects]


def test_a_valid_pair_has_no_defect():
    committed, local = files()
    assert S.validate(committed, local) == []


@pytest.mark.parametrize("prefix", S.TOKEN_PREFIXES)
def test_a_token_pattern_in_a_tone_is_refused(prefix):
    committed, local = files(committed__personas__0__tone=f"Calm. Use the key {prefix}1234567890.")
    defects = S.validate(committed, local)
    assert "personas[0].tone" in keys(defects)
    assert prefix in str(defects[0])


@pytest.mark.parametrize("prefix", S.TOKEN_PREFIXES)
def test_a_token_pattern_in_the_local_file_is_refused(prefix):
    committed, local = files(local__workspaces__0__label=f"client-{prefix}abc")
    defects = S.validate(committed, local)
    assert "workspaces[0].label" in keys(defects)


def test_a_token_pattern_in_any_nested_string_is_refused():
    committed, local = files()
    committed["events"][0]["template"] = "Posted: xoxp-42"
    assert "events[0].template" in keys(S.validate(committed, local))


def test_a_persona_name_with_a_format_character_is_refused():
    committed, local = files(committed__personas__0__name="HAL *9K*")
    assert "personas[0].name" in keys(S.validate(committed, local))


def test_a_persona_name_over_32_characters_is_refused():
    committed, local = files(committed__personas__0__name="A" * 33)
    assert "personas[0].name" in keys(S.validate(committed, local))


@pytest.mark.parametrize("name", ["HAL9K", "Iara", "Hal 9K.", "hal-9k", "hal_9k", "A" * 32])
def test_an_allowed_persona_name_passes(name):
    committed, local = files(committed__personas__0__name=name)
    assert S.validate(committed, local) == []


def test_a_voice_outside_the_gemini_list_is_refused():
    committed, local = files(committed__personas__0__voice="Hal")
    assert "personas[0].voice" in keys(S.validate(committed, local))


def test_the_gemini_list_holds_the_spec_examples():
    assert {"Kore", "Sulafat"} <= S.GEMINI_VOICES


def test_a_tone_over_300_characters_is_refused():
    committed, local = files(committed__personas__0__tone="x" * 301)
    assert "personas[0].tone" in keys(S.validate(committed, local))


def test_a_role_row_accepts_an_optional_tone():
    committed, local = files(committed__roles=[{"id": "manager", "actions": ["status"], "tone": "Short sentences."}])
    assert S.validate(committed, local) == []


def test_a_role_tone_over_300_characters_is_refused_and_names_its_key():
    committed, local = files(committed__roles=[{"id": "manager", "actions": ["status"], "tone": "x" * 301}])
    defects = S.validate(committed, local)
    assert "roles[0].tone" in keys(defects), defects


def test_a_tone_of_300_characters_passes():
    committed, local = files(committed__personas__0__tone="x" * 300)
    assert S.validate(committed, local) == []


@pytest.mark.parametrize("raw, tag", [("pt_br", "pt-BR"), ("pt-br", "pt-BR"), ("pt-BR", "pt-BR"),
                                      ("en_us", "en-US"), ("es", "es-ES"), ("ES-es", "es-ES")])
def test_a_language_tag_normalizes(raw, tag):
    assert S.normalize_language(raw) == tag


def test_a_language_outside_the_three_tags_is_refused():
    committed, local = files()
    committed["workspaces"][0]["channels"][0]["language"] = "fr-FR"
    assert "workspaces[0].channels[0].language" in keys(S.validate(committed, local))


def test_a_language_tag_normalizes_before_it_passes():
    committed, local = files()
    committed["workspaces"][0]["channels"][0]["language"] = "pt_br"
    assert S.validate(committed, local) == []


def test_a_guest_workspace_id_in_the_committed_file_is_refused():
    committed, local = files(committed__workspaces=None)
    committed["workspaces"] = [{"id": "T0CLIENT01", "label": "client-a", "class": "owned"}]
    assert "workspaces[0].id" in keys(S.validate(committed, local))


def test_a_guest_channel_id_in_the_committed_file_is_refused():
    committed, local = files(committed__workspaces__0__channels=None)
    committed["workspaces"][0]["channels"] = [{"id": "C0CLISUP01", "name": "support"}]
    assert "workspaces[0].channels[0].id" in keys(S.validate(committed, local))


def test_prod_in_a_role_deploy_list_is_refused():
    committed, local = files(committed__roles__0__deploy=["stg", "prod"])
    assert "roles[0].deploy" in keys(S.validate(committed, local))


def test_a_deploy_list_without_prod_passes():
    committed, local = files(committed__roles__0__deploy=["stg"])
    assert S.validate(committed, local) == []


def test_an_action_outside_the_executor_list_is_refused():
    """The actions a role may list are the ones the executor runs (issue #2699): the editor offers that list, and a
    role that names another action is refused, with the message naming the key and the action."""
    committed, local = files(committed__roles=[{"id": "manager", "actions": ["status", "publish"]}])
    defects = S.validate(committed, local)
    assert "roles[0].actions[1]" in keys(defects), defects
    assert "'publish'" in str(defects[0]), defects


def test_the_role_actions_are_the_executor_actions():
    """One list, never a copy: the policy module answers the vocabulary the executor runs."""
    from squad import slackexecutor
    assert set(S.role_actions()) == set(slackexecutor.ACTIONS)


def test_each_module_imports_first_in_a_fresh_interpreter():
    """Each module imports alone, in either order (#2747). The policy asks the executor for the action list, and the
    executor asks the policy for `decide`. A top-level import on both sides makes the pair import in one order only:
    `tests/test_slack_executor.py` imports the executor first and fails to collect. This test starts one interpreter per
    module, so no earlier import hides the cycle."""
    root = Path(__file__).resolve().parents[1]
    for name in ("squad.slackexecutor", "squad.slackpolicy"):
        out = subprocess.run([sys.executable, "-c", f"import {name}"], cwd=root, capture_output=True, text=True,
                             timeout=20)
        assert out.returncode == 0, f"{name} does not import alone: {out.stderr}"


def test_a_member_value_that_is_not_a_user_id_is_refused():
    """A member names its people by Slack user id in the local file (ADR-0177, issue #2699): any other value is
    refused, and the message names the user inside the member row."""
    committed, local = files()
    local["members"][0]["users"] = ["U0OWNER001", "sandro"]
    defects = S.validate(committed, local)
    assert "members[0].users[1]" in keys(defects), defects
    assert "'sandro'" in str(defects[0]), defects


def test_members_live_only_in_the_local_file():
    """The committed file holds a role's actions; the people of the role stay on the machine (ADR-0177). A committed
    member list is refused, so the editor never lands a user id in `slack.yaml` (issue #2699)."""
    committed, local = files()
    committed["members"] = copy.deepcopy(local["members"])
    defects = S.validate(committed, local)
    assert [(d.file, d.key) for d in defects] == [("slack.yaml", "members")], defects


def test_a_member_that_names_no_workspace_is_refused():
    """One format, four fields: a member names its Slack workspace, its channel, its role and its user ids."""
    committed, local = files()
    del local["members"][0]["workspace"]
    defects = S.validate(committed, local)
    assert "members[0].workspace" in keys(defects), defects


def test_a_member_alias_is_an_optional_string_and_grants_the_same_users():
    """A member row carries an optional alias in the local file (issue #2873). A member with or without one grants the
    same users, and an alias that is not a string is refused by its key."""
    committed, local = files()
    member = local["members"][0]
    granted = S.resolved_members(local, member["workspace"], member["channel"], [member["role"]])
    member["alias"] = "alias-a"
    assert S.validate(committed, local) == [], S.validate(committed, local)
    assert S.resolved_members(local, member["workspace"], member["channel"], [member["role"]]) == granted
    member["alias"] = ["alias-a"]
    assert "members[0].alias" in keys(S.validate(committed, local)), S.validate(committed, local)


def test_a_send_grant_in_the_committed_file_is_refused():
    # The grant is written only by the hub, in the local file, with the date of the grant.
    committed, local = files()
    committed["workspaces"][0]["channels"][0]["send_grant"] = "2026-11-20"
    out = S.validate(committed, local)
    assert [(d.file, d.key) for d in out] == [("slack.yaml", "workspaces[0].channels[0].send_grant")]


def test_a_send_grant_that_is_not_a_date_is_refused():
    committed, local = files()
    local["workspaces"][0]["channels"][0].update({"speak": "send", "send_grant": "yesterday"})
    out = S.validate(committed, local)
    assert [d.key for d in out] == ["workspaces[0].channels[0].send_grant"]
    assert "date" in out[0].reason


def test_a_dated_send_grant_in_the_local_file_passes():
    committed, local = files()
    local["workspaces"][0]["channels"][0].update({"speak": "send", "send_grant": "2026-11-20"})
    assert S.validate(committed, local) == []


def test_a_second_guest_workspace_on_the_machine_names_the_file_that_holds_it():
    committed, local = files()
    other = copy.deepcopy(LOCAL)
    other["workspaces"][0]["id"] = "T0CLIENT02"
    defects = S.validate(committed, local, others=[("other.slack.local.yaml", other)])
    assert keys(defects) == ["workspaces[0].id"]
    assert defects[0].file == "other.slack.local.yaml"
    assert defects[0].file != "slack.local.yaml"


def test_a_secret_key_in_another_local_file_names_that_file():
    committed, local = files()
    other = {"keychain": {"secret": "agent-squad-slack-bot"}}
    defects = S.validate(committed, local, others=[("b.slack.local.yaml", other)])
    assert keys(defects) == ["keychain.secret"]
    assert defects[0].file == "b.slack.local.yaml"


def test_a_token_in_another_local_file_names_that_file():
    committed, local = files()
    other = {"apps": [{"keychain": {"bot": "xoxb-1234567890"}}]}
    defects = S.validate(committed, local, others=[("b.slack.local.yaml", other)])
    assert defects[0].file == "b.slack.local.yaml"
    assert "apps[0].keychain.bot" in keys(defects)


def test_a_persona_name_holds_ascii_letters_and_digits_only():
    for name in ("OláOláOláOlá", "名前", "HAL9K\n", "HAL9K\t"):
        committed, local = files(committed__personas__0__name=name)
        assert "personas[0].name" in keys(S.validate(committed, local)), name


def test_a_local_persona_voice_is_checked_too():
    local = {"version": 1, "personas": [{"id": "iara", "name": "Iara", "voice": "Nobody", "tone": "Warm."}]}
    committed, _ = files()
    defects = S.validate(committed, local)
    assert "personas[0].voice" in keys(defects)
    assert defects[0].file == "slack.local.yaml"


def test_a_local_role_deploy_of_prod_is_refused():
    local = {"version": 1, "roles": [{"id": "manager", "deploy": ["prod"]}]}
    committed, _ = files()
    defects = S.validate(committed, local)
    assert "roles[0].deploy" in keys(defects)
    assert defects[0].file == "slack.local.yaml"


def test_a_key_that_equals_its_value_is_one_string():
    assert list(S.strings({"tone": "tone"})) == [("tone", "tone")]


def test_two_guest_workspaces_in_one_local_file_are_refused():
    committed, local = files()
    second = copy.deepcopy(local["workspaces"][0])
    second["id"] = "T0CLIENT02"
    local["workspaces"].append(second)
    assert "workspaces[1].id" in keys(S.validate(committed, local))


def test_one_guest_workspace_with_an_owned_one_passes():
    committed, local = files()
    assert S.validate(committed, local) == []


def test_a_key_named_secret_is_refused():
    committed, local = files()
    committed["apps"][0]["keychain"]["secret"] = "agent-squad-slack-bot"
    assert "apps[0].keychain.secret" in keys(S.validate(committed, local))


def test_the_word_secret_in_a_tone_is_data_not_a_key():
    committed, local = files(committed__personas__0__tone="Never say the word secret in an answer.")
    assert S.validate(committed, local) == []


def test_an_unreadable_committed_file_is_a_named_defect(tmp_path):
    import yaml
    root = tmp_path / "proj"
    (root / "docs" / "squad").mkdir(parents=True)
    path = root / "docs" / "squad" / "slack.yaml"
    path.write_text(yaml.safe_dump(COMMITTED))
    path.chmod(0o000)
    defects = S.check(root, profile=None, project="proj")
    path.chmod(0o644)
    assert [d.key for d in defects] == ["(root)"]
    assert defects[0].file == "slack.yaml"
    assert "read" in defects[0].reason


def test_a_broken_file_of_another_project_is_named(tmp_path):
    root = tmp_path / "proj"
    (root / "docs" / "squad").mkdir(parents=True)
    (root / "docs" / "squad" / "slack.yaml").write_text("version: 1\n")
    profile = tmp_path / "profiles"
    profile.mkdir()
    (profile / "proj.slack.local.yaml").write_text("version: 1\n")
    (profile / "other.slack.local.yaml").write_text("personas: [\n")
    defects = S.check(root, profile=str(profile), project="proj")
    assert [(d.file, d.key) for d in defects] == [("other.slack.local.yaml", "(root)")]


def test_the_adr_names_the_one_home_of_the_rules():
    adr = sorted(S.ADR_DIR.glob("0177-*.md"))
    assert len(adr) == 1
    text = adr[0].read_text()
    assert "squad/slackpolicy.py" in text
    assert "ADR-0154" in text


def test_the_mark_is_the_persona_names_one_bold_line():
    """The mark of a reply from the owner's account is the policy rule: `*<name>'s Msg*`, alone and bold. A persona
    with no name has no mark, so a channel with no persona never wears another's name."""
    persona = S.Persona("hal", "HAL9K", "Kore", "Calm and direct.")
    assert S.mark(persona) == "*HAL9K's Msg*"
    assert S.mark(S.Persona("x", "", "Kore", "")) == ""
    assert S.mark(None) == ""


def test_the_resolution_names_the_level_of_each_value():
    """The settings tree states where each effective value comes from (#2824): the channel wins, then its workspace,
    then the project, then `en-US`. The persona chain is the same, and it ends on `default` when no level names one."""
    committed, local = files()
    ws = S.rows(committed["workspaces"])[0]
    ch = S.rows(ws["channels"])[0]
    got = S.resolution(committed, local, ws, ch)
    assert (got["language"], got["language_source"]) == ("pt-BR", "channel")
    assert (got["persona"].name, got["persona_source"]) == ("HAL9K", "channel")
    got = S.resolution(committed, local, ws)
    assert (got["language"], got["language_source"]) == ("en-US", "workspace")
    committed["workspaces"][0].pop("language")
    committed["project"].pop("language")
    got = S.resolution(committed, local, committed["workspaces"][0])
    assert (got["language"], got["language_source"]) == ("en-US", "default")
    committed["project"].pop("persona")
    committed["workspaces"][0].pop("persona")
    got = S.resolution(committed, local, committed["workspaces"][0])
    assert got["persona"] is None and got["persona_source"] == "default"


def test_the_reply_label_reads_each_policy_path():
    """The tile's words are the policy's (#2824): the reply the decision names, a listen channel's `never posts`, and
    `none` when the channel may reply and no member holds an accepted role."""
    committed, local = files()
    ws = S.rows(committed["workspaces"])[0]
    ch = S.rows(ws["channels"])[0]
    decision = S.decide(committed, local, {"workspace": ws["id"], "channel": ch["id"], "user": "U0OWNER001"}, None)
    assert S.reply_label(ch, decision) == "bot"
    assert S.transport(ws, ch) == "bot"
    guest = S.rows(local["workspaces"])[0]
    gch = S.rows(guest["channels"])[0]
    gch = {**gch, "mode": "listen+speak", "speak": "draft"}
    now = datetime.datetime(2026, 11, 24, 12, 0, tzinfo=datetime.UTC)
    assert S.reply_label(gch, S.decide(committed, local, {"workspace": guest["id"], "channel": gch["id"],
                                                          "user": ""}, now)) == "none"
    assert S.reply_label(gch, S.Decision(S.PROPOSAL, "manager", (), "en-US", None, "send", False, None)) == "send"
    assert S.reply_label({**gch, "draft_for": ["U1", "U2"]},
                         S.Decision(S.PROPOSAL, "manager", (), "en-US", None, "send", False, None)) == \
        "send, except 2 listed"
    listen = {**gch, "mode": "listen"}
    assert S.reply_label(listen, S.decide(committed, local, {"workspace": guest["id"], "channel": gch["id"],
                                                             "user": ""}, now)) == "never posts"
    assert S.transport(guest, gch) == "send" and S.transport(guest, listen) == "listen"


def test_the_destination_label_names_the_decision_kind():
    def decision(kind):
        return S.Decision(kind, None, (), "en-US", None, None, False, None)
    assert S.destination_label(decision(S.INBOX)) == "inbox"
    assert S.destination_label(decision(S.PROPOSAL)) == "proposal"
    assert S.destination_label(decision(S.DATA)) == "read, never obeyed"


def test_resolved_members_counts_only_the_roles_a_channel_accepts():
    """A user of the local file counts for a channel only when the role the file gives them is one the channel
    accepts, which is the rule `decide` reads (#2824)."""
    committed, local = files()
    local["members"] = [{"workspace": "T0TEAM0001", "channel": "C0PROJ0001", "role": "manager",
                         "users": ["U0OWNER001", "U0OWNER002"]},
                        {"workspace": "T0TEAM0001", "channel": "C0PROJ0001", "role": "colleague", "users": ["U0X"]}]
    assert S.resolved_members(local, "T0TEAM0001", "C0PROJ0001", ["manager"]) == ["U0OWNER001", "U0OWNER002"]
    assert S.resolved_members(local, "T0TEAM0001", "C0PROJ0001", []) == []


# --- the project identity's deploy target (SPEC 2682 S5, #2724) ------------------------------------------------

def deploy_identity(environment="stg", command="make deploy-stg"):
    """A valid pair whose committed project identity declares the deploy target."""
    committed, local = files()
    committed["project"]["deploy"] = {"environment": environment, "command": command}
    return committed, local


def test_the_project_identity_declares_the_environment_and_the_command():
    """The deploy action reads both from the project identity: one environment and one command. The module resolves the
    merged project row in one place, so the executor never parses the identity's shape itself."""
    committed, local = deploy_identity()
    assert S.validate(committed, local) == []
    assert S.deploy_target(S.project_identity(committed, local)) == ("stg", "make deploy-stg")


def test_a_project_identity_that_declares_prod_is_refused():
    """Production stays with the owner, so a project identity that declares prod is refused by its key, as a role that
    lists prod is."""
    committed, local = deploy_identity(environment="prod", command="make deploy-prod")
    defects = S.validate(committed, local)
    assert "project.deploy.environment" in keys(defects), defects
    assert "prod" in str(defects[0])


def test_a_project_deploy_target_with_no_environment_or_no_command_is_refused():
    """The identity is the only source of both, so a target that names one and not the other is refused by the key it
    misses."""
    for row, key in (({"command": "make deploy-stg"}, "project.deploy.environment"),
                     ({"environment": "stg"}, "project.deploy.command")):
        committed, local = files()
        committed["project"]["deploy"] = row
        defects = S.validate(committed, local)
        assert key in keys(defects), (row, defects)


def test_a_project_deploy_target_that_is_not_a_mapping_is_refused():
    """A shape the reader cannot use is refused by its key, so a scalar under `deploy` never reads as no target."""
    committed, local = files()
    committed["project"]["deploy"] = "make deploy-stg"
    defects = S.validate(committed, local)
    assert "project.deploy" in keys(defects), defects


def test_the_local_file_never_sets_the_deploy_environment():
    """The identity is the committed project row: a local file that names another environment, or prod, changes neither
    the validated file nor the target."""
    committed, local = deploy_identity()
    local["project"] = {"deploy": {"environment": "prod", "command": "make deploy-prod"}}
    assert S.validate(committed, local) == []
    assert S.deploy_target(S.project_identity(committed, local)) == ("stg", "make deploy-stg")


# --- the guard block of the committed file (issue #2947) ---------------------------------------------------------
def test_a_missing_guard_block_takes_the_plugin_defaults():
    """The guard is optional, so a file that names none still bounds the operator session by the shipped defaults."""
    assert S.guard({}) == {"rate": 6, "max_chars": 4000, "answer_max_chars": 8000, "read_wrappers": []}
    assert S.guard({"version": 1})["rate"] == 6


def test_a_committed_guard_block_passes_validation():
    """The committed file accepts the guard block, and the reader returns the values the file names."""
    committed, local = files(committed__guard={"rate": 3, "max_chars": 1000, "answer_max_chars": 2000,
                                               "read_wrappers": ["<@U0MENTION>"]})
    assert S.validate(committed, local) == []
    got = S.guard(committed)
    assert got == {"rate": 3, "max_chars": 1000, "answer_max_chars": 2000, "read_wrappers": ["<@U0MENTION>"]}


def test_a_guard_block_that_is_not_a_mapping_is_refused():
    committed, local = files(committed__guard="fast")
    defects = S.validate(committed, local)
    assert "guard" in keys(defects), defects


@pytest.mark.parametrize("field,value", [
    ("rate", "six"),
    ("rate", 0),
    ("max_chars", -1),
    ("max_chars", "4000"),
    ("answer_max_chars", "many"),
    ("answer_max_chars", 0),
    ("read_wrappers", "text"),
    ("read_wrappers", [1]),
])
def test_a_malformed_guard_field_is_named(field, value):
    """Each malformed field is one defect that names its own key, so a person repairs the key the message names."""
    committed, local = files()
    committed["guard"] = {field: value}
    defects = S.validate(committed, local)
    assert f"guard.{field}" in keys(defects), defects


def test_a_guard_field_the_file_omits_reads_the_default():
    """A guard block that names one field keeps the default of every field it omits."""
    committed, local = files(committed__guard={"rate": 2})
    assert S.validate(committed, local) == []
    got = S.guard(committed)
    assert got == {"rate": 2, "max_chars": 4000, "answer_max_chars": 8000, "read_wrappers": []}


def test_the_project_declares_the_command_of_each_action():
    """#2939: a project declares one command per action id, and the reader hands it to the executor. The local file
    declares none, so it never widens what a role may run."""
    committed, local = files()
    committed["project"]["actions"] = {"status": "squad status", "logs": "tail -n 50 run.log"}
    assert S.validate(committed, local) == []
    assert S.project_actions(committed, local) == {"status": "squad status", "logs": "tail -n 50 run.log"}
    local["project"] = {"actions": {"status": "rm -rf /"}}
    assert S.project_actions(committed, local)["status"] == "squad status"


def test_an_action_command_outside_the_role_vocabulary_is_refused_by_its_key():
    """#2939: an owner-only action stays with the owner, so a project never declares a command for one."""
    committed, local = files()
    committed["project"]["actions"] = {"publish_to_prod": "make release"}
    defects = S.validate(committed, local)
    assert "project.actions.publish_to_prod" in keys(defects), defects
    assert "owner" in str(defects)


def test_action_commands_in_the_local_file_are_refused_by_name():
    """project.actions lives in the committed file (ADR-0177), so a local `project.actions` row, empty or not, is a
    defect that names the local file and says where the row belongs."""
    committed, local = files()
    for bad in ({}, {"status": "echo hi"}, ["squad status"]):
        local["project"] = {"actions": bad}
        defects = S.validate(committed, local)
        named = [d for d in defects if d.key == "project.actions" and d.file == "slack.local.yaml"]
        assert named, defects
        assert "committed" in named[0].reason, named[0].reason


def test_an_action_command_that_is_empty_or_not_one_mapping_is_refused():
    """#2939: a command the executor could not run is refused by its key, and a shape the reader cannot use too."""
    committed, local = files()
    committed["project"]["actions"] = {"status": "  "}
    assert "project.actions.status" in keys(S.validate(committed, local))
    committed["project"]["actions"] = ["squad status"]
    assert "project.actions" in keys(S.validate(committed, local))


# --- a `full` role holds only the workspace owner or a granted member (#2966, ADR-0180) ---------------------
NOW = datetime.datetime(2026, 11, 25, 12, 0, 0)
FULL_ROLE = {"id": "manager", "actions": ["status", "logs"], "session": "full"}


def full_files():
    """The valid pair whose one `manager` role holds the full session."""
    committed, local = files(committed__roles=[FULL_ROLE])
    return committed, local


def owner_event(**over):
    """A message from the workspace owner in the project channel, which answers an active revision."""
    event = {"kind": "message", "workspace": "T0TEAM0001", "channel": "C0PROJ0001", "user": "U0OWNER001",
             "text": "status please", "bot": False, "external": False, "own": False, "owner": True,
             "active": True, "dm": False}
    event.update(over)
    return event


def test_a_full_role_member_who_is_the_workspace_owner_is_no_defect():
    committed, local = full_files()
    assert S.validate(committed, local, owners={"T0TEAM0001": "U0OWNER001"}) == []


def test_a_full_role_member_neither_owner_nor_granted_is_one_defect_naming_the_user():
    committed, local = full_files()
    defects = S.validate(committed, local, owners={"T0TEAM0001": "U0BOSS0001"})
    assert keys(defects) == ["members[0].users[0]"], defects


def test_a_full_role_member_with_no_owner_map_is_a_defect():
    committed, local = full_files()
    assert keys(S.validate(committed, local)) == ["members[0].users[0]"]


def test_a_full_role_member_granted_in_its_workspace_is_no_defect():
    committed, local = full_files()
    grants = [{"workspace": "T0TEAM0001", "user": "U0OWNER001"}]
    assert S.validate(committed, local, owners={"T0TEAM0001": "U0BOSS0001"}, grants=grants) == []


def test_a_grant_in_another_workspace_does_not_pass_a_full_role_member():
    committed, local = full_files()
    grants = [{"workspace": "T0OTHER001", "user": "U0OWNER001"}]
    defects = S.validate(committed, local, owners={"T0TEAM0001": "U0BOSS0001"}, grants=grants)
    assert keys(defects) == ["members[0].users[0]"], defects


def test_a_grant_whose_user_is_not_a_string_grants_nothing():
    """A grant admits a member only when its workspace and user are non-empty strings. A malformed entry, such as a
    registry row with a null user, names no grant, so the full role still refuses a member the owner map omits."""
    committed, local = full_files()
    local["members"][0]["users"] = ["None"]
    defects = S.validate(committed, local, owners={}, grants=[{"workspace": "T0TEAM0001", "user": None}])
    assert any(d.key == "members[0].users[0]" and "owner of workspace" in d.reason for d in defects), defects


def test_a_grant_whose_workspace_is_not_a_string_grants_nothing():
    """The workspace side of a grant is a string too: a numeric workspace never matches a member row, even when its
    string form equals the member's workspace id."""
    committed, local = full_files()
    committed["workspaces"][0]["id"] = "5"
    local["members"][0]["workspace"] = "5"
    local["members"][0]["users"] = ["None"]
    defects = S.validate(committed, local, owners={}, grants=[{"workspace": 5, "user": "None"}])
    assert any(d.key == "members[0].users[0]" and "owner of workspace" in d.reason for d in defects), defects


def test_a_bounded_role_member_needs_neither_the_owner_nor_a_grant():
    """A `session: bounded` role member in a declared workspace with no owner and no grant is no defect: the owner and
    grant check belongs to the full session alone (ADR-0180)."""
    committed, local = files(committed__roles=[{"id": "manager", "actions": ["status", "logs"],
                                                "session": "bounded"}])
    assert S.validate(committed, local) == []


def test_a_full_role_member_in_a_workspace_the_committed_file_does_not_declare_is_a_defect():
    """A full-role member names a workspace the committed file declares; a workspace no file declares is a named defect
    before any owner or grant check (#2966)."""
    committed, local = full_files()
    local["members"][0]["workspace"] = "T0NONE0001"
    defects = S.validate(committed, local, owners={"T0NONE0001": "U0OWNER001"})
    assert keys(defects) == ["members[0].workspace"], defects
    assert "T0NONE0001" in str(defects[0]), defects


def test_a_full_role_member_with_no_workspace_names_the_workspace_key():
    """A full-role member with no workspace is a defect that names the key, never an empty workspace id in the text."""
    committed, local = full_files()
    del local["members"][0]["workspace"]
    defects = S.validate(committed, local, owners={})
    assert keys(defects) == ["members[0].workspace"], defects


def test_a_full_role_member_in_a_channel_of_another_workspace_is_a_defect():
    """A full-role member's channel, when it is named, belongs to that member's workspace; a channel another workspace
    declares is a named defect (#2966)."""
    committed, local = full_files()
    committed["workspaces"].append({"id": "T0OTHER001", "class": "owned",
                                    "channels": [{"id": "C0OTHER001", "name": "proj-other", "mode": "listen"}]})
    local["members"][0]["channel"] = "C0OTHER001"
    defects = S.validate(committed, local, owners={"T0TEAM0001": "U0OWNER001"})
    assert keys(defects) == ["members[0].channel"], defects


def test_a_full_decision_in_the_owner_project_channel_runs_the_full_session():
    committed, local = full_files()
    decision = S.decide(committed, local, owner_event(), NOW)
    assert decision.kind == S.INBOX
    assert decision.session == S.SESSION_FULL


def test_an_event_with_active_true_and_dm_false_runs_the_full_session():
    """`decide` reads both keys its caller must state: `active: true` and `dm: false` together with the owner give the
    full session. A caller that omits either one gets the bounded session (ADR-0180)."""
    committed, local = full_files()
    event = owner_event(active=True, dm=False)
    assert S.decide(committed, local, event, NOW).session == S.SESSION_FULL


def test_a_legacy_owner_event_with_no_active_flag_runs_bounded():
    """A caller that names no active flag proves no live revision, so the owner's session stays bounded."""
    committed, local = full_files()
    event = owner_event()
    del event["active"]
    assert S.decide(committed, local, event, NOW).session == S.SESSION_BOUNDED


def test_a_full_decision_in_a_direct_message_runs_bounded():
    committed, local = full_files()
    assert S.decide(committed, local, owner_event(dm=True), NOW).session == S.SESSION_BOUNDED


def test_a_full_decision_in_a_channel_with_no_active_revision_runs_bounded():
    committed, local = full_files()
    assert S.decide(committed, local, owner_event(active=False), NOW).session == S.SESSION_BOUNDED


def test_a_full_decision_in_a_guest_channel_runs_bounded():
    committed, local = full_files()
    local["workspaces"][0]["channels"][0]["roles"] = ["manager"]
    local["members"] = [{"workspace": "T0CLIENT01", "channel": "C0CLISUP01", "role": "manager",
                         "users": ["U0OWNER001"]}]
    event = owner_event(workspace="T0CLIENT01", channel="C0CLISUP01")
    decision = S.decide(committed, local, event, NOW)
    assert decision.kind == S.PROPOSAL
    assert decision.session == S.SESSION_BOUNDED
