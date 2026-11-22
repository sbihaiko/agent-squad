"""Guest proposals from role holders (SPEC 2682, stories 61 and 62; #2718).

A request from a role holder in a `guest` channel never runs the action. The policy decides it as a proposal, and the
proposal reaches the owner's direct message with a summary that holds no personal data. The owner confirms, edits, or
refuses it, and an issue is born only from the exact text the owner approved, in the destination the owner approved.

Every decision here comes from the real policy module over a real pair of files, so the expected answer never repeats
the proposal module's own rule. Every id and every text is a placeholder.
"""
import asyncio
import copy
import json
from datetime import datetime

from squad import slackexecutor as X
from squad import slackpolicy as S
from squad import slackproposal as P
from squad import slackwriter as W

REPO = "acme/app"
GUEST_TEAM = "T0CLIENT01"
GUEST_CHANNEL = "C0CLISUP01"
OWNER_DM = "D0OWNERDM1"
OWNER = "U0OWNER001"
CLIENT = "U0CLIENT01"
MID = "C0CLISUP01:1762500100.000100"
OTHER_MID = "C0CLISUP01:1762500200.000200"
CLIENT_TITLE = "Add a button that exports the report to PDF"
CLIENT_EMAIL = "ana.silva@client.example"
CLIENT_PHONE = "+55 11 98765-4321"
SUMMARY_MARK = "HAL9K"

COMMITTED = {
    "version": 1,
    "project": {"language": "en-US", "persona": "hal"},
    "personas": [{"id": "hal", "name": SUMMARY_MARK, "voice": "Kore", "tone": "Calm and direct."}],
    "roles": [{"id": "colleague", "actions": ["status", "request_work", "request_investigation"]}],
    "workspaces": [],
}

LOCAL = {
    "version": 1,
    "workspaces": [
        {"id": GUEST_TEAM, "label": "client-a", "class": "guest", "persona": "hal",
         "channels": [{"id": GUEST_CHANNEL, "name": "support", "mode": "listen+speak", "speak": "draft",
                       "roles": ["colleague"],
                       "poll": {"mode": "two_state", "fast": "1m", "slow": "10m", "quiet_after": "10m"}}]},
    ],
    "members": [{"workspace": GUEST_TEAM, "channel": GUEST_CHANNEL, "role": "colleague", "users": [CLIENT]}],
}


def decision(user=CLIENT, channel=GUEST_CHANNEL):
    """The real policy decision of one message of the guest channel."""
    event = {"kind": "message", "workspace": GUEST_TEAM, "channel": channel, "user": user}
    return S.decide(copy.deepcopy(COMMITTED), copy.deepcopy(LOCAL), event, now=datetime(2026, 11, 22, 12, 0, 0))


def line(user=CLIENT, text=CLIENT_TITLE, role="colleague", mid=MID):
    """One inbox line as the guest poller writes it: the message, its caller, the channel and the decision fields."""
    return {"id": mid, "ts": mid.partition(":")[2], "user": user, "channel": GUEST_CHANNEL, "language": "en-US",
            "persona": SUMMARY_MARK, "role": role, "text": text}


def proposal(text=CLIENT_TITLE, mid=MID):
    """One proposal from the real policy decision and the real request parse, so every case asks the same path."""
    request, why = X.parse(request_text(arguments={"title": text}))
    assert request is not None, why
    return P.of(request, decision(), line(text=text, mid=mid), REPO)


def request_text(action="request_work", arguments=None, project=REPO):
    body = {"action": action, "arguments": arguments if arguments is not None else {"title": CLIENT_TITLE},
            "project": project, "channel": GUEST_CHANNEL, "source": MID}
    return json.dumps(body)


def reply(verb, proposal_, text=None, destination=REPO):
    """The one reply line the owner DM asks for: the verb, the destination, the proposal token, then an edit's text."""
    tail = f": {text}" if text is not None else ""
    return f"{verb} {destination} {P.token(proposal_)}{tail}"


class Work:
    """The work source the executor would call for `request_work`. It records each call, so a case proves the action
    never ran for a proposal."""

    def __init__(self):
        self.calls = []

    def __call__(self, project, text):
        self.calls.append((project, text))
        return "issue #2705 opened"


class Issue:
    """The issue source `create` calls. It records the destination and the exact text, so a refused proposal proves it
    was never reached."""

    def __init__(self):
        self.calls = []

    def __call__(self, destination, text):
        self.calls.append((destination, text))
        return f"issue #2706 opened in {destination}"


# --- criterion 1: a request from a role holder in a guest channel gets decision `proposal` --------------------------

def test_a_request_from_a_role_holder_in_a_guest_channel_gets_decision_proposal():
    """Criterion (1): the policy names the kind `proposal`, the module builds the proposal, and no action runs."""
    dec = decision()
    assert dec.kind == S.PROPOSAL
    request, why = X.parse(request_text())
    assert request is not None, why
    proposal_ = P.of(request, dec, line(), REPO)
    assert proposal_ is not None
    assert proposal_.action == "request_work"
    assert proposal_.text == CLIENT_TITLE
    work = Work()
    out = X.execute(request, dec, line(), REPO, work=work)
    assert out.ran is False
    assert work.calls == []


# --- criterion 2: the proposal reaches the owner DM with a summary that holds no personal data ----------------------

def test_the_proposal_reaches_the_owner_dm_with_a_summary_that_holds_no_personal_data():
    """Criterion (2): the summary keeps the request and drops the caller's id, mention, email and phone, and the owner
    DM carries only that text through the writer's hook with the persona mark."""
    text = f"{CLIENT_TITLE} for {CLIENT_EMAIL}, call {CLIENT_PHONE}, from <@{CLIENT}>."
    summary = P.summary(proposal(text))
    assert CLIENT not in summary
    assert CLIENT_EMAIL not in summary
    assert "98765" not in summary
    assert CLIENT_TITLE in summary
    draft = P.dm(proposal(text), OWNER_DM, SUMMARY_MARK)
    assert draft.channel == OWNER_DM and draft.tool == W.DRAFT_WORD
    answer = asyncio.run(W.pre_write_tool_use(draft)({
        "tool_name": "mcp__slack__slack_send_message_draft", "tool_input": {}}, "t1", None))
    output = answer["hookSpecificOutput"]
    assert output["permissionDecision"] == "allow"
    assert output["updatedInput"]["channel"] == OWNER_DM
    sent = output["updatedInput"]["text"]
    assert sent == draft.text
    assert CLIENT not in sent
    assert CLIENT_EMAIL not in sent
    assert "98765" not in sent
    assert CLIENT_TITLE in sent
    assert P.token(proposal(text)) in sent


def test_the_summary_blanks_an_obfuscated_email_address():
    """Criterion (2): a person writes an address with words instead of signs, and the summary still never carries it."""
    text = "call ana dot silva at client dot example about the export"
    summary = P.summary(proposal(text))
    assert "silva" not in summary
    assert "client dot example" not in summary
    assert "about the export" in summary


def test_the_summary_keeps_a_version_a_date_and_an_amount():
    """Criterion (2): the personal-data patterns never eat a version, a date or an amount of the request."""
    text = "In version 10.15.7 of 2026-11-22 the report shows 1.234,56 euros"
    summary = P.summary(proposal(text))
    assert "10.15.7" in summary
    assert "2026-11-22" in summary
    assert "1.234,56" in summary


# --- criteria 3 to 6: the owner confirms, edits or refuses; nothing is born without approval -------------------------

def test_the_owner_can_confirm_the_proposal_with_its_destination():
    """Criterion (3): a `confirm` reply approves the client's text and the destination the owner named."""
    proposal_ = proposal()
    answer = P.parse_owner(reply("confirm", proposal_), [REPO])
    assert answer.id == P.token(proposal_)
    assert P.approve(proposal_, answer) == P.Approval(proposal_.id, CLIENT_TITLE, REPO, False)


def test_the_owner_can_edit_the_proposal_and_the_edit_is_the_approved_text():
    """Criterion (3) and (5): an `edit` reply replaces the text, and the edited text is what the owner approved."""
    proposal_ = proposal()
    answer = P.parse_owner(reply("edit", proposal_, "Add a CSV export button"), [REPO])
    assert P.approve(proposal_, answer) == P.Approval(proposal_.id, "Add a CSV export button", REPO, True)


def test_the_owner_can_refuse_the_proposal():
    """Criterion (3) and (4): a `refuse` reply approves nothing, and the issue source is never reached."""
    proposal_ = proposal()
    issue = Issue()
    answer = P.parse_owner(f"refuse {P.token(proposal_)}", [REPO])
    assert answer.kind == P.REFUSE
    assert P.approve(proposal_, answer) is None
    assert P.create(proposal_, None, issue, [REPO]) is None
    assert issue.calls == []


def test_a_refused_proposal_creates_nothing():
    """Criterion (4): the refusal ends at the owner's reply, so no issue is created and no work starts."""
    proposal_ = proposal()
    issue = Issue()
    refused = P.parse_owner(f"refuse {P.token(proposal_)}", [REPO])
    assert P.create(proposal_, P.approve(proposal_, refused), issue, [REPO]) is None
    assert issue.calls == []


def test_an_issue_carries_only_the_exact_text_the_owner_approved():
    """Criterion (5): the issue source receives the approved text unchanged, spaces, commas and dashes included."""
    proposal_ = proposal()
    edited = "Add a CSV export button (comma, dash - and two  spaces kept)"
    approved = P.approve(proposal_, P.parse_owner(reply("edit", proposal_, edited), [REPO]))
    issue = Issue()
    answer = P.create(proposal_, approved, issue, [REPO])
    assert issue.calls == [(REPO, edited)]
    assert answer == f"issue #2706 opened in {REPO}"


def test_the_owner_approves_the_destination_of_the_issue():
    """Criterion (6): a reply that names no destination, or one outside the known list, approves nothing."""
    proposal_ = proposal()
    issue = Issue()
    no_destination = P.parse_owner(f"confirm {P.token(proposal_)}", [REPO])
    assert P.approve(proposal_, no_destination) is None
    outside = P.parse_owner(f"confirm acme/other {P.token(proposal_)}", [REPO])
    assert P.approve(proposal_, outside) is None
    assert P.create(proposal_, P.approve(proposal_, outside), issue, [REPO]) is None
    assert issue.calls == []


def test_the_owner_reply_names_the_whole_destination():
    """Criterion (6): one destination is never a prefix of another, so the reply approves the one it names."""
    destinations = ["acme/app", "acme/app2"]
    proposal_ = proposal(mid=OTHER_MID)
    answer = P.parse_owner(f"confirm acme/app2 {P.token(proposal_)}", destinations)
    assert P.approve(proposal_, answer) == P.Approval(OTHER_MID, CLIENT_TITLE, "acme/app2", False)


def test_an_edit_body_that_names_another_project_binds_the_leading_destination():
    """Criterion (6): the destination is the leading token of the reply, never a project id inside the edit body."""
    answer = P.parse_owner(f"edit {REPO}: see acme/app2 for the log", [REPO, "acme/app2"])
    assert answer.destination == REPO
    assert answer.text == "see acme/app2 for the log"


def test_a_proposal_no_owner_approved_creates_nothing():
    """Criterion (7): the raw proposal never reaches the issue source; only an `Approval` does."""
    proposal_ = proposal()
    issue = Issue()
    assert P.create(proposal_, proposal_, issue, [REPO]) is None
    assert issue.calls == []


def test_a_forged_approval_names_its_proposal_and_a_known_destination():
    """Criterion (7): a hand-built `Approval` for another proposal, or for a destination the owner did not approve,
    never opens an issue."""
    proposal_ = proposal()
    issue = Issue()
    assert P.create(proposal_, P.Approval(OTHER_MID, CLIENT_TITLE, REPO, False), issue, [REPO]) is None
    assert P.create(proposal_, P.Approval(MID, CLIENT_TITLE, "acme/other", False), issue, [REPO]) is None
    assert P.create(proposal_, P.Approval(MID, "Something the owner never wrote", REPO, False), issue, [REPO]) is None
    assert issue.calls == []


def test_the_reply_names_the_one_proposal_it_answers():
    """Criteria (3) and (5): two proposals wait, and a reply approves the one whose token it names, never the other."""
    first, second = proposal(mid=MID), proposal(mid=OTHER_MID)
    issue = Issue()
    answer = P.parse_owner(reply("confirm", second), [REPO])
    assert P.approve(first, answer) is None
    assert issue.calls == []
    approved = P.approve(second, answer)
    assert approved.proposal == OTHER_MID
    assert P.create(second, approved, issue, [REPO]) == f"issue #2706 opened in {REPO}"
    assert issue.calls == [(REPO, CLIENT_TITLE)]


def test_a_reply_that_names_no_proposal_approves_nothing():
    """Criteria (3) and (5): a reply without the proposal token names no proposal, so it approves nothing."""
    proposal_ = proposal()
    answer = P.parse_owner(f"confirm {REPO}", [REPO])
    assert answer.id == ""
    assert P.approve(proposal_, answer) is None


def test_a_request_with_no_text_becomes_no_proposal():
    """Criterion (1): a proposal carries one request text, so a request with none is not a proposal."""
    request, why = X.parse(request_text(arguments={"title": "   "}))
    assert request is not None, why
    assert P.of(request, decision(), line(text="   "), REPO) is None


def test_a_request_whose_action_has_no_owner_words_becomes_no_proposal():
    """Criterion (1): the owner reads one vetted phrase per action, so an action with none offers nothing."""
    request, why = X.parse(request_text(action="status", arguments={}))
    assert request is not None, why
    assert P.of(request, decision(), line(), REPO) is None
