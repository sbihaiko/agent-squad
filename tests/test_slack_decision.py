"""The policy decision for one Slack event (ADR-0177, SPEC 2682).

One call takes the two validated files, one event and one clock value, and returns one decision: `ignore`, `data`,
`inbox` or `proposal`. Every id here is a placeholder.
"""
import copy
from datetime import datetime, timedelta

from squad import slackpolicy as S

NOW = datetime(2026, 11, 22, 12, 0, 0)

COMMITTED = {
    "version": 1,
    "project": {"language": "en-US", "persona": "hal"},
    "personas": [
        {"id": "hal", "name": "HAL9K", "voice": "Kore", "tone": "Calm and direct."},
        {"id": "iara", "name": "Iara", "voice": "Sulafat", "tone": "Warm and patient."},
    ],
    "roles": [
        {"id": "manager", "actions": ["status", "logs"]},
        {"id": "colleague", "actions": ["status"]},
    ],
    "workspaces": [
        {"id": "T0TEAM0001", "label": "team", "class": "owned", "app": "hal9k", "language": "es-ES", "persona": "hal",
         "channels": [
             {"id": "C0PROJ0001", "name": "proj", "mode": "listen+speak", "language": "pt-BR", "persona": "hal",
              "roles": ["manager", "colleague"]},
             {"id": "C0ALERT001", "name": "alerts", "mode": "listen", "roles": ["manager"]},
         ]},
    ],
}

LOCAL = {
    "version": 1,
    "workspaces": [
        {"id": "T0CLIENT01", "label": "client-a", "class": "guest", "language": "es-ES", "persona": "iara",
         "channels": [
             {"id": "C0CLISUP01", "name": "support", "mode": "listen+speak", "speak": "draft", "roles": ["colleague"],
              "poll": {"mode": "two_state", "fast": "1m", "slow": "10m", "quiet_after": "10m", "thread_open": "30m"}},
             {"id": "C0CLIGEN01", "name": "general", "mode": "listen", "roles": ["colleague"],
              "poll": {"mode": "exponential", "floor": "5m", "ceiling": "1h", "factor": 2,
                       "idle_window": "15m", "thread_open": "30m"}},
         ]},
    ],
    "members": [
        {"workspace": "T0TEAM0001", "channel": "C0PROJ0001", "role": "manager", "users": ["U0OWNER001"]},
        {"workspace": "T0TEAM0001", "channel": "C0PROJ0001", "role": "colleague", "users": ["U0PEER0001"]},
        {"workspace": "T0TEAM0001", "channel": "C0ALERT001", "role": "manager", "users": ["U0OWNER001"]},
        {"workspace": "T0CLIENT01", "channel": "C0CLISUP01", "role": "colleague", "users": ["U0CLIENT01"]},
    ],
}


def files():
    return copy.deepcopy(COMMITTED), copy.deepcopy(LOCAL)


def message(workspace="T0TEAM0001", channel="C0PROJ0001", user="U0OWNER001", **over):
    event = {"kind": "message", "workspace": workspace, "channel": channel, "user": user}
    event.update(over)
    return event


def decide(event, now=NOW, thread_state=None):
    committed, local = files()
    return S.decide(committed, local, event, now, thread_state=thread_state)


def test_a_role_holder_in_an_owned_channel_gets_inbox_bot_and_no_mark():
    d = decide(message())
    assert d.kind == "inbox"
    assert d.role == "manager"
    assert d.actions == ("status", "logs")
    assert d.language == "pt-BR"
    assert d.persona.id == "hal"
    assert (d.persona.name, d.persona.voice, d.persona.tone) == ("HAL9K", "Kore", "Calm and direct.")
    assert d.reply == "bot"
    assert d.mark is False
    assert d.next_poll is None


def test_the_language_falls_back_to_the_workspace_then_the_project():
    committed, local = files()
    del committed["workspaces"][0]["channels"][0]["language"]
    assert S.decide(committed, local, message(), NOW).language == "es-ES"
    del committed["workspaces"][0]["language"]
    assert S.decide(committed, local, message(), NOW).language == "en-US"
    del committed["project"]["language"]
    assert S.decide(committed, local, message(), NOW).language == "en-US"


def test_the_persona_falls_back_and_a_persona_never_changes_language_or_actions():
    committed, local = files()
    base = S.decide(committed, local, message(), NOW)
    committed["workspaces"][0]["channels"][0]["persona"] = "iara"
    changed = S.decide(committed, local, message(), NOW)
    assert changed.persona.name == "Iara"
    assert (changed.language, changed.actions) == (base.language, base.actions)
    del committed["workspaces"][0]["channels"][0]["persona"]
    assert S.decide(committed, local, message(), NOW).persona.id == "hal"


def test_a_bot_an_external_user_and_a_person_without_a_role_get_data():
    for event in (message(bot=True), message(external=True), message(user="U0STRANGER1")):
        d = decide(event)
        assert d.kind == "data"
        assert d.reply is None
        assert d.mark is False


def test_a_reply_in_a_listen_channel_has_no_reply_path():
    d = decide(message(channel="C0ALERT001"))
    assert d.kind == "inbox"
    assert d.reply is None
    assert d.mark is False


def test_a_role_holder_in_a_guest_channel_gets_a_marked_draft_proposal():
    d = decide(message(workspace="T0CLIENT01", channel="C0CLISUP01", user="U0CLIENT01"))
    assert d.kind == "proposal"
    assert d.role == "colleague"
    assert d.actions == ("status",)
    assert d.language == "es-ES"
    assert d.persona.id == "iara"
    assert d.reply == "draft"
    assert d.mark is True


def test_a_guest_send_and_a_draft_leave_the_owner_account_and_both_carry_the_mark():
    # The mark names authorship, not the transport: every guest write leaves from the owner's account, so a direct
    # send carries it exactly as a draft does (SPEC 2682, stories 34, 36 and 38).
    committed, local = files()
    channel = local["workspaces"][0]["channels"][0]
    channel["speak"] = "send"
    event = message(workspace="T0CLIENT01", channel="C0CLISUP01", user="U0CLIENT01")
    draft = S.decide(committed, local, event, NOW)
    assert (draft.reply, draft.mark) == ("draft", True)
    channel["send_grant"] = "2026-11-20"
    sent = S.decide(committed, local, event, NOW)
    assert (sent.reply, sent.mark) == ("send", True)


def test_a_reply_to_a_user_in_draft_for_stays_a_draft_in_a_speak_send_channel():
    # The grant makes the channel a send channel, but a person in `draft_for` is reviewed by the owner first.
    committed, local = files()
    channel = local["workspaces"][0]["channels"][0]
    channel.update({"speak": "send", "send_grant": "2026-11-20", "draft_for": ["U0CLIENT01"]})
    addressed = message(workspace="T0CLIENT01", channel="C0CLISUP01", user="U0CLIENT01")
    assert S.decide(committed, local, addressed, NOW).reply == "draft"
    # A person outside `draft_for` still gets the direct send the grant allows.
    local["members"][3]["users"].append("U0CLIENT02")
    other = message(workspace="T0CLIENT01", channel="C0CLISUP01", user="U0CLIENT02")
    assert S.decide(committed, local, other, NOW).reply == "send"


def test_a_slack_connect_channel_never_gets_a_direct_send():
    # `slack_send_message` does not post in a Slack Connect channel, so the grant cannot make it a send channel.
    committed, local = files()
    channel = local["workspaces"][0]["channels"][0]
    channel.update({"speak": "send", "send_grant": "2026-11-20", "connect": True})
    event = message(workspace="T0CLIENT01", channel="C0CLISUP01", user="U0CLIENT01")
    d = S.decide(committed, local, event, NOW)
    assert (d.reply, d.mark) == ("draft", True)


def test_a_project_block_only_in_the_local_file_supplies_the_language_and_persona():
    committed, local = files()
    del committed["project"]
    del committed["workspaces"][0]["language"]
    del committed["workspaces"][0]["persona"]
    local["project"] = {"language": "pt-BR", "persona": "iara"}
    d = S.decide(committed, local, message(channel="C0ALERT001"), NOW)
    assert d.language == "pt-BR"
    assert d.persona.id == "iara"


def test_an_unknown_channel_keeps_what_the_files_know_of_the_caller():
    committed, local = files()
    local["members"].append(
        {"workspace": "T0TEAM0001", "channel": "C0UNKNOWN1", "role": "manager", "users": ["U0OWNER001"]})
    d = S.decide(committed, local, message(channel="C0UNKNOWN1"), NOW)
    assert d.kind == "ignore"
    assert d.reply is None
    assert d.role == "manager"
    assert d.language == "es-ES"
    assert d.persona.id == "hal"
    assert d.next_poll is None


def test_a_guest_two_state_channel_polls_fast_then_slow_and_stays_fast_with_an_open_thread():
    event = message(workspace="T0CLIENT01", channel="C0CLISUP01", user="U0CLIENT01")
    recent = {"last_message_at": NOW - timedelta(minutes=1)}
    assert decide(event, thread_state=recent).next_poll == NOW + timedelta(minutes=1)
    quiet = {"last_message_at": NOW - timedelta(minutes=11)}
    assert decide(event, thread_state=quiet).next_poll == NOW + timedelta(minutes=10)
    threaded = {"last_message_at": NOW - timedelta(minutes=11), "thread_last_at": NOW - timedelta(minutes=2)}
    assert decide(event, thread_state=threaded).next_poll == NOW + timedelta(minutes=1)


def test_a_guest_exponential_channel_grows_the_interval_to_the_ceiling():
    event = message(workspace="T0CLIENT01", channel="C0CLIGEN01", user="U0CLIENT01")

    def poll(idle):
        return decide(event, thread_state={"last_message_at": NOW - idle}).next_poll

    # One window is the first step of the growth, so the boundary sits at exactly one window of idle.
    assert poll(timedelta(minutes=14, seconds=59)) == NOW + timedelta(minutes=5)
    assert poll(timedelta(minutes=15)) == NOW + timedelta(minutes=10)
    two_windows = {"last_message_at": NOW - timedelta(minutes=40)}
    assert decide(event, thread_state=two_windows).next_poll == NOW + timedelta(minutes=20)
    # The last whole step stays under the ceiling; the next one is capped at it.
    assert poll(timedelta(minutes=59, seconds=59)) == NOW + timedelta(minutes=40)
    assert poll(timedelta(hours=1)) == NOW + timedelta(hours=1)
    assert poll(timedelta(hours=5)) == NOW + timedelta(hours=1)


def test_a_guest_listen_channel_has_no_reply_path_but_still_holds_the_next_poll():
    d = decide(message(workspace="T0CLIENT01", channel="C0CLIGEN01", user="U0CLIENT01"))
    assert d.reply is None
    assert d.next_poll is not None


def test_an_unknown_channel_and_an_own_message_are_ignored():
    unknown = decide(message(channel="C0UNKNOWN1"))
    assert (unknown.kind, unknown.reply) == ("ignore", None)
    assert decide(message(own=True)).kind == "ignore"
    assert decide(message(kind="reaction")).kind == "ignore"


def test_a_decision_carries_the_environments_the_role_lists_for_deploy():
    """S5 (#2724): the executor reads the role's deploy environments from the decision, so the policy module resolves
    them once, from the same role row that gives the actions. A role that lists none carries an empty tuple."""
    committed, local = files()
    committed["roles"][0]["deploy"] = ["stg", "stage"]
    assert S.decide(committed, local, message(), NOW).deploy == ("stg", "stage")
    committed["roles"][0].pop("deploy")
    assert S.decide(committed, local, message(), NOW).deploy == ()
