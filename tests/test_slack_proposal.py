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

import pytest

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


def line(user=CLIENT, text=CLIENT_TITLE, role="colleague"):
    """One inbox line as the guest poller writes it: the message, its caller, the channel and the decision fields."""
    return {"id": MID, "ts": "1762500100.000100", "user": user, "channel": GUEST_CHANNEL, "language": "en-US",
            "persona": SUMMARY_MARK, "role": role, "text": text}


def proposal(text=CLIENT_TITLE):
    """One proposal from the real policy decision and the real request parse, so every case asks the same path."""
    request, why = X.parse(request_text(arguments={"title": text}))
    assert request is not None, why
    return P.of(request, decision(), line(text=text), REPO)


def request_text(action="request_work", arguments=None, project=REPO):
    body = {"action": action, "arguments": arguments if arguments is not None else {"title": CLIENT_TITLE},
            "project": project, "channel": GUEST_CHANNEL, "source": MID}
    return json.dumps(body)


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
    proposal = P.of(request, dec, line(), REPO)
    assert proposal is not None
    assert proposal.action == "request_work"
    assert proposal.text == CLIENT_TITLE
    work = Work()
    out = X.execute(request, dec, line(), REPO, work=work)
    assert out.ran is False
    assert work.calls == []


# --- criterion 2: the proposal reaches the owner DM with a summary that holds no personal data ----------------------

def test_the_proposal_reaches_the_owner_dm_with_a_summary_that_holds_no_personal_data():
    """Criterion (2): the summary keeps the request and drops the caller's id, mention, email and phone; the owner DM
    carries it through the writer's hook with the persona mark."""
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
    assert output["updatedInput"]["text"] == draft.text


# --- criteria 3 to 6: the owner confirms, edits or refuses; nothing is born without approval -------------------------

def test_the_owner_can_confirm_the_proposal_with_its_destination():
    """Criterion (3): a `confirm` reply approves the client's text and the destination the owner named."""
    approved = P.approve(proposal(), P.parse_owner(f"confirm {REPO}", [REPO]))
    assert approved == P.Approval(CLIENT_TITLE, REPO)


def test_the_owner_can_edit_the_proposal_and_the_edit_is_the_approved_text():
    """Criterion (3) and (5): an `edit` reply replaces the text, and the edited text is what the owner approved."""
    approved = P.approve(proposal(), P.parse_owner(f"edit {REPO}: Add a CSV export button", [REPO]))
    assert approved == P.Approval("Add a CSV export button", REPO)


def test_the_owner_can_refuse_the_proposal():
    """Criterion (3) and (4): a `refuse` reply approves nothing, and the issue source is never reached."""
    issue = Issue()
    assert P.approve(proposal(), P.parse_owner("refuse", [REPO])) is None
    assert P.create(None, issue) is None
    assert issue.calls == []


def test_a_refused_proposal_creates_nothing():
    """Criterion (4): the refusal ends at the owner's reply, so no issue is created and no work starts."""
    issue = Issue()
    refused = P.parse_owner("refuse", [REPO])
    assert P.approve(proposal(), refused) is None
    assert P.create(P.approve(proposal(), refused), issue) is None
    assert issue.calls == []


def test_an_issue_carries_only_the_exact_text_the_owner_approved():
    """Criterion (5): the issue source receives the approved text unchanged, spaces, commas and dashes included."""
    edited = "Add a CSV export button (comma, dash - and two  spaces kept)"
    approved = P.approve(proposal(), P.parse_owner(f"edit {REPO}: {edited}", [REPO]))
    issue = Issue()
    answer = P.create(approved, issue)
    assert issue.calls == [(REPO, edited)]
    assert answer == f"issue #2706 opened in {REPO}"


def test_the_owner_approves_the_destination_of_the_issue():
    """Criterion (6): a reply that names no destination, or one outside the known list, approves nothing."""
    issue = Issue()
    assert P.approve(proposal(), P.parse_owner("confirm", [REPO])) is None
    outside = P.parse_owner("confirm acme/other", [REPO])
    assert P.approve(proposal(), outside) is None
    assert P.create(P.approve(proposal(), outside), issue) is None
    assert issue.calls == []


def test_a_proposal_no_owner_approved_creates_nothing():
    """Criterion (7): the raw proposal never reaches the issue source; only an `Approval` does."""
    issue = Issue()
    assert P.create(proposal(), issue) is None
    assert issue.calls == []
