"""A release event as one draft in each `guest` channel that subscribes to it (SPEC 2682, story 71; #2715).

The event record is the one `release_events.py emit` writes (#2702). The draft is the writer's own draft (#2712), so it
goes through the `PreToolUse` hook and carries the persona mark. Every id, `ts` and item here is a placeholder.
"""
import asyncio
import copy

import pytest

from squad import slackannounce as A
from squad import slackpolicy as P
from squad import slackwriter as W

STG = {"id": "stg_release", "key": "stg_release:a1b2c3", "ts": "2026-11-22T12:00:00Z",
       "items": ["a1b2c3 Merge the fix", "d4e5f6 Land the doc"]}
PROD = {"id": "prod_release", "key": "prod_release:99aa", "ts": "2026-11-22T13:00:00Z",
        "items": ["99aa Ship the release"]}
SUPPORT, GENERAL, QUIET = "C0CLISUP01", "C0CLIGEN01", "C0CLIQUIET1"

COMMITTED = {
    "version": 1,
    "project": {"language": "en-US", "persona": "iara"},
    "personas": [{"id": "iara", "name": "Iara", "voice": "Sulafat", "tone": "Warm and patient."}],
    "events": [
        {"id": "stg_release", "template": "Subiu para STG: {items}."},
        {"id": "prod_release", "template": "Publicado em PROD: {items}."},
    ],
    "roles": [{"id": "colleague", "actions": ["status"]}],
    "workspaces": [],
}

LOCAL = {
    "version": 1,
    "workspaces": [
        {"id": "T0CLIENT01", "label": "client-a", "class": "guest", "language": "en-US", "persona": "iara",
         "channels": [
             {"id": SUPPORT, "name": "support", "mode": "listen+speak", "speak": "draft",
              "announce": ["prod_release"]},
             {"id": GENERAL, "name": "general", "mode": "listen+speak", "speak": "draft",
              "announce": ["stg_release"]},
             {"id": QUIET, "name": "quiet", "mode": "listen+speak", "speak": "draft"},
         ]},
    ],
    "members": [],
}


def _put(doc, parts, value):
    """Set one nested path of a document: a number indexes a list, any other part indexes a mapping. The last part is
    the key that takes the value, and a single part replaces a top-level section."""
    node = doc
    for part in parts[:-1]:
        node = node[int(part)] if isinstance(node, list) else node[part]
    last = parts[-1]
    if isinstance(node, list):
        node[int(last)] = value
    else:
        node[last] = value


def files(**over):
    """A valid committed and local pair; a keyword names a `__`-separated path of one document, a number indexing a
    list and any other part indexing a mapping, so `committed__workspaces__0__channels__0__templates` sets a nested map."""
    committed, local = copy.deepcopy(COMMITTED), copy.deepcopy(LOCAL)
    for name, value in over.items():
        target, _, rest = name.partition("__")
        doc = committed if target == "committed" else local
        _put(doc, rest.split("__"), value)
    return committed, local


def drafts(record, **over):
    committed, local = files(**over)
    return A.guest_announcements(committed, local, record)


def body(draft):
    """The body of one marked draft: the lines after the mark line, without the italic wrapper."""
    return draft.text.split("\n", 1)[1].strip("_")


class FakeConnector:
    """The connector the session talks to: it records each write tool call it receives and returns a `ts`."""

    def __init__(self):
        self.calls = []

    def __call__(self, tool, arguments):
        self.calls.append((tool, dict(arguments)))
        return {"ts": f"17{len(self.calls):09.6f}"}


def deliver(draft, connector):
    """One draft through the announcement module's own production path: the module runs the writer's `PreToolUse` hook,
    reads its answer, and hands the connector the channel post it decided. A change to how the answer becomes a
    connector call therefore fails every fake-connector test below."""
    return asyncio.run(A.post(draft, connector))


# --- 1. a subscribed guest channel gets the event as a draft -------------------------------------------------------

def test_a_subscribed_guest_channel_gets_the_event_as_a_draft():
    got = drafts(STG)
    assert [d.channel for d in got] == [GENERAL], got
    assert got[0].tool == W.DRAFT_WORD, "a guest announcement leaves as a draft, never a direct send"


def test_two_channels_that_subscribe_to_different_events_each_get_their_own():
    assert [d.channel for d in drafts(STG)] == [GENERAL]
    assert [d.channel for d in drafts(PROD)] == [SUPPORT]


def test_a_send_grant_still_leaves_the_announcement_as_a_draft():
    """Stage 3 produces drafts only (story 37): a `speak: send` channel with a grant does not send an announcement."""
    got = drafts(STG, local__workspaces__0__channels=[{"id": GENERAL, "name": "general", "mode": "listen+speak",
                                                       "speak": "send", "send_grant": True,
                                                       "announce": ["stg_release"]}])
    assert [d.tool for d in got] == [W.DRAFT_WORD], got


# --- 2. the draft uses the event's template, or the channel override ------------------------------------------------

def test_the_draft_uses_the_template_of_the_event():
    assert body(drafts(STG)[0]) == "Subiu para STG: a1b2c3 Merge the fix, d4e5f6 Land the doc."


def test_a_channel_override_wins_over_the_event_template():
    got = drafts(STG, local__workspaces__0__channels=[{"id": GENERAL, "name": "general", "mode": "listen+speak",
                                                       "speak": "draft", "announce": ["stg_release"],
                                                       "templates": {"stg_release": "No ar em STG ({items})."}}])
    assert [d.channel for d in got] == [GENERAL]
    assert body(got[0]) == "No ar em STG (a1b2c3 Merge the fix, d4e5f6 Land the doc)."


def test_a_numeric_override_key_still_wins():
    record = {"id": "2702", "items": ["x"]}
    got = drafts(record, committed__events=[{"id": "2702", "template": "Event {items}."}],
                 local__workspaces__0__channels=[{"id": GENERAL, "mode": "listen+speak", "speak": "draft",
                                                  "announce": ["2702"], "templates": {2702: "Override {items}."}}])
    assert body(got[0]) == "Override x."


def test_a_placeholder_the_record_does_not_carry_stays_as_written():
    record = {"id": "stg_release", "items": ["a"]}
    got = drafts(record, committed__events=[{"id": "stg_release", "template": "Key {key}: {items}."}])
    assert body(got[0]) == "Key {key}: a.", "a placeholder the record omits stays as written"


def test_a_committed_guest_channel_with_a_local_override_is_announced_once_with_the_override():
    committed_ws = [{"id": "T0CLIENT01", "label": "client-a", "class": "guest", "language": "en-US",
                     "persona": "iara", "channels": [
                         {"id": GENERAL, "name": "general", "mode": "listen+speak", "speak": "draft",
                          "announce": ["stg_release"]}]}]
    local_ws = [{"id": "T0CLIENT01", "class": "guest",
                 "channels": [{"id": GENERAL, "templates": {"stg_release": "Local: {items}."}}]}]
    got = drafts(STG, committed__workspaces=committed_ws, local__workspaces=local_ws)
    assert [d.channel for d in got] == [GENERAL], "one channel id yields one draft"
    assert body(got[0]) == "Local: a1b2c3 Merge the fix, d4e5f6 Land the doc.", "the local override wins"


# --- 3. the draft carries the mark ----------------------------------------------------------------------------------

def test_the_draft_carries_the_persona_mark():
    d = drafts(STG)[0]
    assert d.text.splitlines()[0] == "*Iara's Msg*", d.text
    assert d.text == "*Iara's Msg*\n_Subiu para STG: a1b2c3 Merge the fix, d4e5f6 Land the doc._"
    assert W.has_mark(d.text, "Iara")


# --- 4. a channel that does not subscribe gets nothing --------------------------------------------------------------

def test_a_channel_that_does_not_subscribe_gets_nothing():
    assert QUIET not in [d.channel for d in drafts(STG)]
    assert QUIET not in [d.channel for d in drafts(PROD)]


def test_a_listen_channel_gets_no_announcement():
    got = drafts(STG, local__workspaces__0__channels=[{"id": GENERAL, "name": "general", "mode": "listen",
                                                       "announce": ["stg_release"]}])
    assert got == [], "a listen channel never receives a post"


def test_a_subscription_to_an_event_with_no_template_in_either_place_has_nothing_to_post():
    """Story 71 gives each subscribed event its own template, and the ticket names the event's template or the channel
    override as the only two sources (#2715). A subscription to an event that neither place gives a template has no text
    to post, so the channel gets nothing. This is not a rule about templates in general: a channel that names a
    template still gets its draft."""
    got = drafts(STG, committed__events=[])
    assert got == [], "the subscription names an event with no template in either place"
    assert [d.channel for d in drafts(STG)] == [GENERAL], "the same channel still gets its draft from the event template"


# --- 5. the draft goes through the write hook -----------------------------------------------------------------------

def test_the_draft_goes_through_the_write_hook_and_the_connector_gets_the_decided_text():
    d = drafts(STG)[0]
    connector = FakeConnector()
    out = deliver(d, connector)
    assert out.get("permissionDecision") == "allow", out
    assert out["updatedInput"] == {"channel": GENERAL, "thread_ts": "", "text": d.text}, \
        "the hook keeps the writer's shape: updatedInput always carries thread_ts"
    assert connector.calls == [(W.DRAFT_WORD, {"channel": GENERAL, "text": d.text})], connector.calls


def test_the_connector_call_of_an_announcement_takes_the_channel_post_path():
    """An announcement is a channel post: the connector receives the channel and the decided text, and no thread. It
    never receives the empty `thread_ts` the hook carries, because a tool that reads a thread would read `''` as one."""
    d = drafts(STG)[0]
    assert W.channel_post(d) == {"channel": GENERAL, "text": d.text}
    hook = W.pre_write_tool_use(d)
    answer = asyncio.run(hook({"tool_name": f"mcp__slack__slack_{W.DRAFT_WORD}",
                               "tool_input": {"channel": GENERAL, "text": "a copy"}}, "t1", None))
    assert answer["hookSpecificOutput"]["updatedInput"]["thread_ts"] == "", "the hook keeps main's shape"


def test_the_announcement_reaches_the_connector_through_the_production_post():
    """The production path is one function in the announcement module: it reads the writer's hook answer and hands the
    connector the channel post the answer allows, so no caller reaches `channel_post` itself."""
    d = drafts(STG)[0]
    connector = FakeConnector()
    out = asyncio.run(A.post(d, connector))
    assert out.get("permissionDecision") == "allow", out
    assert connector.calls == [(W.DRAFT_WORD, {"channel": GENERAL, "text": d.text})], connector.calls


def test_the_hook_denies_a_thread_on_a_channel_post():
    d = drafts(STG)[0]
    hook = W.pre_write_tool_use(d)
    answer = asyncio.run(hook({"tool_name": f"mcp__slack__slack_{W.DRAFT_WORD}",
                               "tool_input": {"channel": GENERAL, "thread_ts": "123.45", "text": d.text}}, "t1", None))
    assert (answer.get("hookSpecificOutput") or {}).get("permissionDecision") == "deny", answer


def test_the_hook_denies_an_announcement_the_connector_tries_to_redirect():
    d = drafts(STG)[0]
    hook = W.pre_write_tool_use(d)
    wrong_channel = asyncio.run(hook({"tool_name": f"mcp__slack__slack_{W.DRAFT_WORD}",
                                      "tool_input": {"channel": SUPPORT, "text": d.text}}, "t1", None))
    wrong_tool = asyncio.run(hook({"tool_name": f"mcp__slack__slack_{W.SEND_WORD}",
                                   "tool_input": {"channel": GENERAL, "text": d.text}}, "t1", None))
    for answer in (wrong_channel, wrong_tool):
        assert (answer.get("hookSpecificOutput") or {}).get("permissionDecision") == "deny", answer


# --- 6. a fake connector proves each rule ---------------------------------------------------------------------------

_CHANNELS = "local__workspaces__0__channels"
RULES = [
    ("channel override", STG,
     {_CHANNELS: [{"id": GENERAL, "mode": "listen+speak", "speak": "draft", "announce": ["stg_release"],
                   "templates": {"stg_release": "No ar em STG ({items})."}}]},
     [(GENERAL, "*Iara's Msg*\n_No ar em STG (a1b2c3 Merge the fix, d4e5f6 Land the doc)._")]),
    ("numeric key", {"id": "2702", "items": ["x"]},
     {"committed__events": [{"id": "2702", "template": "Event {items}."}],
      _CHANNELS: [{"id": GENERAL, "mode": "listen+speak", "speak": "draft", "announce": ["2702"],
                   "templates": {2702: "Override {items}."}}]},
     [(GENERAL, "*Iara's Msg*\n_Override x._")]),
    ("no template", STG, {"committed__events": []}, []),
    ("listen mode", STG, {_CHANNELS: [{"id": GENERAL, "mode": "listen", "announce": ["stg_release"]}]}, []),
    ("no persona", STG, {"committed__personas": []}, []),
    ("unsubscribed", STG, {_CHANNELS: [{"id": GENERAL, "mode": "listen+speak", "speak": "draft"}]}, []),
]


@pytest.mark.parametrize("name,record,over,expected", RULES, ids=[rule[0] for rule in RULES])
def test_a_fake_connector_receives_each_rule(name, record, over, expected):
    """Each acceptance rule read through the one connector the session talks to: the call's channel and text, and no
    call at all for the rules that grant nothing."""
    connector = FakeConnector()
    for d in drafts(record, **over):
        deliver(d, connector)
    assert all(tool == W.DRAFT_WORD for tool, _ in connector.calls), (name, connector.calls)
    assert [(args["channel"], args["text"]) for _, args in connector.calls] == expected, (name, connector.calls)


def test_a_fake_connector_receives_a_committed_channel_and_its_local_override():
    committed_ws = [{"id": "T0CLIENT01", "label": "client-a", "class": "guest", "language": "en-US",
                     "persona": "iara", "channels": [
                         {"id": GENERAL, "name": "general", "mode": "listen+speak", "speak": "draft",
                          "announce": ["stg_release"]}]}]
    local_ws = [{"id": "T0CLIENT01", "class": "guest",
                 "channels": [{"id": GENERAL, "templates": {"stg_release": "Local: {items}."}}]}]
    committed, local = files(committed__workspaces=committed_ws, local__workspaces=local_ws)
    connector = FakeConnector()
    for draft in A.guest_announcements(committed, local, STG):
        deliver(draft, connector)
    calls = [(tool, args["channel"], args["text"]) for tool, args in connector.calls]
    assert calls == [(W.DRAFT_WORD, GENERAL, "*Iara's Msg*\n_Local: a1b2c3 Merge the fix, d4e5f6 Land the doc._")], calls


def test_a_fake_connector_receives_a_guest_channel_named_only_by_the_committed_file():
    """A project that commits its own guest workspace and channel, with no local row, still gets the announcement."""
    committed_ws = [{"id": "T0CLIENT01", "label": "client-a", "class": "guest", "language": "en-US",
                     "persona": "iara", "channels": [
                         {"id": GENERAL, "name": "general", "mode": "listen+speak", "speak": "draft",
                          "announce": ["stg_release"]}]}]
    committed, local = files(committed__workspaces=committed_ws, local__workspaces=[])
    connector = FakeConnector()
    for draft in A.guest_announcements(committed, local, STG):
        deliver(draft, connector)
    calls = [(tool, args["channel"], args["text"]) for tool, args in connector.calls]
    assert calls == [(W.DRAFT_WORD, GENERAL,
                      "*Iara's Msg*\n_Subiu para STG: a1b2c3 Merge the fix, d4e5f6 Land the doc._")], calls


def test_a_fake_connector_receives_a_committed_channel_with_its_local_persona_override():
    """A local channel row that names another persona wins, and the mark names that persona."""
    committed_ws = [{"id": "T0CLIENT01", "class": "guest", "persona": "iara",
                     "channels": [{"id": GENERAL, "name": "general", "mode": "listen+speak", "speak": "draft",
                                   "announce": ["stg_release"]}]}]
    local_ws = [{"id": "T0CLIENT01", "channels": [{"id": GENERAL, "persona": "lia"}]}]
    committed, local = files(committed__workspaces=committed_ws, local__workspaces=local_ws,
                             committed__personas=[{"id": "iara", "name": "Iara", "voice": "Sulafat",
                                                   "tone": "Warm and patient."},
                                                  {"id": "lia", "name": "Lia", "voice": "Kore",
                                                   "tone": "Direct."}])
    connector = FakeConnector()
    for draft in A.guest_announcements(committed, local, STG):
        deliver(draft, connector)
    calls = [(tool, args["channel"], args["text"]) for tool, args in connector.calls]
    assert calls == [(W.DRAFT_WORD, GENERAL,
                      "*Lia's Msg*\n_Subiu para STG: a1b2c3 Merge the fix, d4e5f6 Land the doc._")], calls


# --- 7. a local row merges into the committed one -------------------------------------------------------------------

def test_a_local_channel_override_keeps_the_committed_templates_it_does_not_name():
    """A local channel row that adds one event template keeps the committed templates for the other events."""
    templates = {"stg_release": "Committed STG: {items}.", "prod_release": "Committed PROD: {items}."}
    committed_ws = [{"id": "T0CLIENT01", "class": "guest", "persona": "iara",
                     "channels": [{"id": GENERAL, "mode": "listen+speak", "speak": "draft",
                                   "announce": ["stg_release", "prod_release"], "templates": templates}]}]
    local_ws = [{"id": "T0CLIENT01", "channels": [{"id": GENERAL, "templates": {"stg_release": "Local STG: {items}."}}]}]
    got_stg = drafts(STG, committed__workspaces=committed_ws, local__workspaces=local_ws)
    got_prod = drafts(PROD, committed__workspaces=committed_ws, local__workspaces=local_ws)
    assert [body(d) for d in got_stg] == ["Local STG: a1b2c3 Merge the fix, d4e5f6 Land the doc."]
    assert [body(d) for d in got_prod] == ["Committed PROD: 99aa Ship the release."], "the kept committed template"


def test_a_local_announce_list_adds_to_the_committed_one():
    """A local channel row that names another event keeps the events the committed row lists, so a local
    `['prod_release']` never drops a committed `['stg_release']`."""
    committed_ws = [{"id": "T0CLIENT01", "class": "guest", "persona": "iara",
                     "channels": [{"id": GENERAL, "mode": "listen+speak", "speak": "draft",
                                   "announce": ["stg_release"]}]}]
    local_ws = [{"id": "T0CLIENT01", "channels": [{"id": GENERAL, "announce": ["prod_release"]}]}]
    got_stg = drafts(STG, committed__workspaces=committed_ws, local__workspaces=local_ws)
    got_prod = drafts(PROD, committed__workspaces=committed_ws, local__workspaces=local_ws)
    assert [d.channel for d in got_stg] == [GENERAL], "the committed event stays announced"
    assert [d.channel for d in got_prod] == [GENERAL], "the local event is announced too"
    assert body(got_prod[0]) == "Publicado em PROD: 99aa Ship the release."


def test_a_local_channel_persona_wins_over_the_committed_one():
    """A local channel row that names another persona sets that channel's mark (`slackpolicy.effective_persona`)."""
    committed_ws = [{"id": "T0CLIENT01", "class": "guest", "persona": "iara",
                     "channels": [{"id": GENERAL, "mode": "listen+speak", "speak": "draft", "persona": "iara",
                                   "announce": ["stg_release"]}]}]
    local_ws = [{"id": "T0CLIENT01", "channels": [{"id": GENERAL, "persona": "lia"}]}]
    got = drafts(STG, committed__workspaces=committed_ws, local__workspaces=local_ws,
                 committed__personas=[{"id": "iara", "name": "Iara", "voice": "Sulafat", "tone": "Warm."},
                                      {"id": "lia", "name": "Lia", "voice": "Kore", "tone": "Direct."}])
    assert [d.text.splitlines()[0] for d in got] == ["*Lia's Msg*"], got


def test_the_merged_channel_row_resolves_the_local_persona_through_effective_persona():
    """The merge keeps the local `persona` on the merged channel row, and `slackpolicy.effective_persona` reads that row
    to give Lia, not the committed Iara."""
    committed_ws = [{"id": "T0CLIENT01", "class": "guest", "persona": "iara",
                     "channels": [{"id": GENERAL, "mode": "listen+speak", "speak": "draft", "persona": "iara",
                                   "announce": ["stg_release"]}]}]
    local_ws = [{"id": "T0CLIENT01", "channels": [{"id": GENERAL, "persona": "lia"}]}]
    committed, local = files(committed__workspaces=committed_ws, local__workspaces=local_ws,
                             committed__personas=[{"id": "iara", "name": "Iara", "voice": "Sulafat", "tone": "Warm."},
                                                  {"id": "lia", "name": "Lia", "voice": "Kore", "tone": "Direct."}])
    group = A._guest_workspaces(committed, local)[0]
    channel = group["channels"][0]
    assert channel["persona"] == "lia", "the merged row keeps the local persona"
    persona = P.effective_persona(committed, local, group["workspace"], channel)
    assert persona is not None and persona.name == "Lia", persona


def test_the_same_channel_id_under_two_guest_workspaces_gets_one_draft_each():
    """The dedup key is (workspace id, channel id): a second workspace's override is never dropped because the first
    workspace names the same channel id."""
    local_ws = [
        {"id": "T0CLIENT01", "class": "guest", "persona": "iara",
         "channels": [{"id": GENERAL, "mode": "listen+speak", "speak": "draft", "announce": ["stg_release"]}]},
        {"id": "T0CLIENT02", "class": "guest", "persona": "iara",
         "channels": [{"id": GENERAL, "mode": "listen+speak", "speak": "draft", "announce": ["stg_release"],
                       "templates": {"stg_release": "Second: {items}."}}]},
    ]
    got = drafts(STG, local__workspaces=local_ws)
    assert [d.channel for d in got] == [GENERAL, GENERAL], "each guest workspace gets its own draft"
    assert [body(d) for d in got] == ["Subiu para STG: a1b2c3 Merge the fix, d4e5f6 Land the doc.",
                                      "Second: a1b2c3 Merge the fix, d4e5f6 Land the doc."], got


def test_a_local_workspace_row_never_demotes_a_committed_guest_workspace():
    """The committed file fixes the workspace class; a local row that names `owned` cannot silence a guest workspace."""
    committed_ws = [{"id": "T0CLIENT01", "class": "guest", "persona": "iara",
                     "channels": [{"id": GENERAL, "mode": "listen+speak", "speak": "draft",
                                   "announce": ["stg_release"]}]}]
    local_ws = [{"id": "T0CLIENT01", "class": "owned", "channels": []}]
    got = drafts(STG, committed__workspaces=committed_ws, local__workspaces=local_ws)
    assert [d.channel for d in got] == [GENERAL], "a local row keeps the committed class"
