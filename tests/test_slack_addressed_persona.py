"""Guard: issue #2819 (SPEC 2682). A leading `@<name>` addresses a message to the persona of that name, in an `owned`
channel and in a `guest` channel. Slack has no user for a persona, so the address is plain text; a name the project does
not declare is no address, and a native mention keeps the default persona of the channel. A channel may set
`reply: addressed`, so only an addressed message gets a reply path.

The seams are the policy decision, the decision's writer plan, the validator of the editor value, the poller's event, and
the daemon's own outbox path. A fake Slack client records the post, so no case reaches Slack.
"""
import asyncio
import copy
from datetime import datetime

from squad import slackpolicy as S
from squad import slackpoller as P
from squad import slackwriter as W
from tests.test_voiced import OWNER, FakeClient, append_outbox, configure, voiced
from tests.test_voiced_persona import PersonaVoice, outbox_daemon
from tests.test_voiced_policy import REPO

NOW = datetime(2026, 11, 22, 12, 0, 0)
OWNED = "C0PROJ0001"
GUEST = "C0CLISUP01"
WORKSPACE = "T0TEAM0001"

COMMITTED = {
    "version": 1,
    "project": {"language": "en-US", "persona": "hal"},
    "personas": [
        {"id": "hal", "name": "HAL9K", "voice": "Kore", "tone": "Calm and direct."},
        {"id": "iara", "name": "Iara", "voice": "Sulafat", "tone": "Warm and patient."},
    ],
    "roles": [{"id": "manager", "actions": ["status"]}, {"id": "colleague", "actions": ["status"]}],
    "workspaces": [
        {"id": WORKSPACE, "label": "team", "class": "owned", "persona": "hal",
         "channels": [{"id": OWNED, "name": "proj", "mode": "listen+speak", "persona": "hal",
                       "roles": ["manager"]}]},
    ],
}

LOCAL = {
    "version": 1,
    "workspaces": [
        {"id": "T0CLIENT01", "label": "client-a", "class": "guest", "persona": "iara",
         "channels": [{"id": GUEST, "name": "support", "mode": "listen+speak", "speak": "draft",
                       "persona": "iara", "roles": ["colleague"]}]},
    ],
    "members": [
        {"workspace": WORKSPACE, "channel": OWNED, "role": "manager", "users": ["U0OWNER001"]},
        {"workspace": "T0CLIENT01", "channel": GUEST, "role": "colleague", "users": ["U0CLIENT01"]},
    ],
}


def files():
    return copy.deepcopy(COMMITTED), copy.deepcopy(LOCAL)


def owned(text="hello", **over):
    event = {"kind": "message", "workspace": WORKSPACE, "channel": OWNED, "user": "U0OWNER001", "text": text}
    event.update(over)
    return event


def guest(text="hello", **over):
    event = {"kind": "message", "workspace": "T0CLIENT01", "channel": GUEST, "user": "U0CLIENT01", "text": text}
    event.update(over)
    return event


def decide(event, committed=None, local=None):
    base, local_doc = files()
    return S.decide(committed or base, local or local_doc, event, NOW)


# --- 1. A declared name addresses the message to that persona ------------------------------------------------

def test_a_leading_address_selects_the_persona_the_project_declares():
    d = decide(owned("@Iara please check the deploy"))
    assert d.kind == "inbox"
    assert (d.persona.id, d.persona.name) == ("iara", "Iara")
    assert (d.persona.voice, d.persona.tone) == ("Sulafat", "Warm and patient.")
    assert d.reply == "bot"
    assert d.mark is False


def test_the_address_match_ignores_case():
    assert decide(owned("@hal9k check")).persona.id == "hal"
    assert decide(owned("@IARA check")).persona.id == "iara"


def test_an_undeclared_name_gets_the_same_decision_as_a_message_with_no_address():
    plain, named = decide(owned("check")), decide(owned("@Nobody check"))
    assert (named.kind, named.role, named.persona, named.reply, named.mark) == (
        plain.kind, plain.role, plain.persona, plain.reply, plain.mark)


def test_a_native_mention_of_the_bot_gets_the_default_persona_of_the_channel():
    d = decide(owned("<@U0BOT0001> check"))
    assert d.persona.id == "hal"


def test_a_quoted_reply_that_addresses_a_persona_still_addresses_it():
    d = decide(owned("> @Iara check the quote"))
    assert (d.persona.id, d.persona.name) == ("iara", "Iara")


def test_the_address_rule_holds_in_a_guest_channel():
    d = decide(guest("@HAL9K status"))
    assert d.kind == "proposal"
    assert d.persona.id == "hal"
    assert d.reply == "draft"
    assert d.mark is True


# --- 2. The reply carries the addressed persona ---------------------------------------------------------------

def test_the_guest_draft_carries_the_mark_of_the_addressed_persona():
    d = decide(guest("@HAL9K status"))
    plan = W.plan(d, GUEST, "2.000200", "All green.")
    assert plan is not None
    assert plan.name == "HAL9K"
    assert plan.text.startswith(f"{S.mark(d.persona)}\n")


def test_an_owned_bot_reply_posts_under_the_addressed_persona_name(tmp_path):
    mod = voiced()
    client = FakeClient()
    channel = configure(tmp_path, mod, client, [REPO])[1]["channels"][REPO]["id"]
    revision = {
        "version": 1,
        "project": {"language": "en-US", "persona": "hal"},
        "personas": [
            {"id": "hal", "name": "HAL9K", "voice": "Kore", "tone": "Calm and direct."},
            {"id": "iara", "name": "Iara", "voice": "Sulafat", "tone": "Warm and patient."},
        ],
        "workspaces": [{"id": WORKSPACE, "class": "owned", "persona": "hal",
                        "channels": [{"id": channel, "mode": "listen+speak", "persona": "hal"}]}],
    }
    mod.State(str(tmp_path)).save_active_revision(REPO, {"repo": REPO, "sha": "a" * 40, "revision": revision,
                                                         "local": {}})
    daemon = outbox_daemon(tmp_path, mod, client, PersonaVoice(tmp_path))
    append_outbox(daemon.state.inbox_for(channel),
                  {"id": "m1", "ts": "1762500000.000100", "channel": channel, "user": OWNER, "persona": "Iara"})
    append_outbox(daemon.state.outbox_for(channel),
                  {"thread_ts": "1762500000.000100", "text": "Green."})

    asyncio.run(daemon.drain_outboxes())

    posts = client.posts()
    assert len(posts) == 1, posts
    assert posts[0]["username"] == "Iara"


# --- 3. reply: addressed ----------------------------------------------------------------------------------------

def test_a_reply_addressed_owned_channel_needs_an_address_for_a_reply_path():
    committed, local = files()
    committed["workspaces"][0]["channels"][0]["reply"] = "addressed"
    plain = S.decide(committed, local, owned("check"), NOW)
    assert plain.kind == "inbox"
    assert plain.role == "manager"
    assert plain.actions == ("status",)
    assert plain.reply is None
    assert plain.mark is False
    addressed = S.decide(committed, local, owned("@Iara check"), NOW)
    assert addressed.reply == "bot"


def test_a_reply_addressed_guest_channel_needs_an_address_for_a_reply_path():
    committed, local = files()
    local["workspaces"][0]["channels"][0]["reply"] = "addressed"
    plain = S.decide(committed, local, guest("check"), NOW)
    assert plain.kind == "proposal"
    assert plain.role == "colleague"
    assert plain.reply is None
    assert plain.mark is False
    addressed = S.decide(committed, local, guest("@HAL9K check"), NOW)
    assert addressed.reply == "draft"
    assert addressed.mark is True


def test_the_validator_accepts_all_and_addressed_and_the_default_is_all():
    path = ["workspaces", 0, "channels", 0]
    assert S.validate_field({}, {}, path, "reply", "all") is None
    assert S.validate_field({}, {}, path, "reply", "addressed") is None
    bad = S.validate_field({}, {}, path, "reply", "sometimes")
    assert bad is not None
    assert bad[0] == "workspaces[0].channels[0]"


# --- 4. The plumbing carries the text to the one decision ------------------------------------------------------

def test_the_poller_event_carries_the_message_text():
    event = P.event_of({"ts": "1.000100", "user": "U0OWNER001", "text": "@Iara check"}, WORKSPACE, OWNED)
    assert event["text"] == "@Iara check"
    d = S.decide(*files(), event, NOW)
    assert d.persona.id == "iara"


def test_the_voiced_decision_reads_the_text_of_the_event(tmp_path):
    mod = voiced()
    client = FakeClient()
    channel = configure(tmp_path, mod, client, [REPO])[1]["channels"][REPO]["id"]
    revision = {
        "version": 1,
        "project": {"language": "en-US", "persona": "hal"},
        "personas": [
            {"id": "hal", "name": "HAL9K", "voice": "Kore", "tone": "Calm and direct."},
            {"id": "iara", "name": "Iara", "voice": "Sulafat", "tone": "Warm and patient."},
        ],
        "workspaces": [{"id": WORKSPACE, "class": "owned", "persona": "hal",
                        "channels": [{"id": channel, "mode": "listen+speak", "persona": "hal"}]}],
    }
    state = mod.State(str(tmp_path))
    state.save_active_revision(REPO, {"repo": REPO, "sha": "a" * 40, "revision": revision, "local": {}})
    daemon = outbox_daemon(tmp_path, mod, client, PersonaVoice(tmp_path))

    d = daemon.policy_decision({"channel": channel, "team": WORKSPACE, "user": OWNER,
                                "text": "@Iara check", "ts": "1.000100"})
    assert d.persona.id == "iara"
