"""The Slack file rules (ADR-0177): a committed `slack.yaml`, a local `slack.local.yaml`, and one defect per failing key.

Guards: a token pattern is refused in every string of both files, the `tone` included; a persona name outside the letters,
digits, space, period, hyphen and underscore set, or over 32 characters, is refused; a voice outside the Gemini list, a
`tone` over 300 characters, and a language outside the three tags after normalization are refused; a `guest` team id or
channel id in the committed file is refused; `prod` in a role deploy list, a second `guest` workspace on the machine, and a
key named `secret` are refused; a `send_grant` outside the local file, or without a date, is refused; a language tag
normalizes; the new ADR names the one home of the rules.
"""
import copy
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
    committed, local = files(committed__roles=[{"id": "manager", "actions": ["status", "deploy"]}])
    defects = S.validate(committed, local)
    assert "roles[0].actions[1]" in keys(defects), defects
    assert "'deploy'" in str(defects[0]), defects


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
