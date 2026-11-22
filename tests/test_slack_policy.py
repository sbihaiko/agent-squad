"""The Slack file rules (ADR-0177): a committed `slack.yaml`, a local `slack.local.yaml`, and one defect per failing key.

Guards: a token pattern is refused in every string of both files, the `tone` included; a persona name outside the letters,
digits, space, period, hyphen and underscore set, or over 32 characters, is refused; a voice outside the Gemini list, a
`tone` over 300 characters, and a language outside the three tags after normalization are refused; a `guest` team id or
channel id in the committed file is refused; `prod` in a role deploy list, a second `guest` workspace on the machine, and a
key named `secret` are refused; a language tag normalizes; the new ADR names the one home of the rules.
"""
import copy

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


def test_a_second_guest_workspace_on_the_machine_is_refused():
    committed, local = files()
    other = copy.deepcopy(LOCAL)
    other["workspaces"][0]["id"] = "T0CLIENT02"
    assert "workspaces[0].id" in keys(S.validate(committed, local, others=[other]))


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


def test_the_adr_names_the_one_home_of_the_rules():
    adr = sorted(S.ADR_DIR.glob("0177-*.md"))
    assert len(adr) == 1
    text = adr[0].read_text()
    assert "squad/slackpolicy.py" in text
    assert "ADR-0154" in text
