"""Guest proposals from role holders (SPEC 2682, stories 61 and 62; #2718).

A request from a role holder in a `guest` channel never runs the action. The policy decides it as a proposal, and the
proposal reaches the owner's direct message with a summary that holds no personal data. The owner confirms, edits, or
refuses it, and an issue is born only from the exact text the owner approved, in the destination the owner approved.

Every decision here comes from the real policy module over a real pair of files, so the expected answer never repeats
the proposal module's own rule. Every id and every text is a placeholder.
"""
import copy
import json
from datetime import datetime

import pytest

from squad import slackexecutor as X
from squad import slackpolicy as S
from squad import slackproposal as P

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
