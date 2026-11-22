"""A guest request as a proposal to the owner (SPEC 2682, stories 61 and 62; #2718).

A role holder in a `guest` channel never starts work. The policy gives the request the kind `proposal`, and this module
turns the request into one `Proposal` the owner decides on. The action never runs before the owner approves, and an issue
is born only from the exact text the owner approved.

Guards: `tests/test_slack_proposal.py`.
"""
from typing import NamedTuple

from . import slackpolicy as S


class Proposal(NamedTuple):
    """One guest request the owner decides on: its id, the action it asked for, the text the client wrote, the project
    the issue would go to, the guest channel, and the caller's own user id (personal data, never in the summary)."""
    id: str
    action: str
    text: str
    project: str
    channel: str
    requester: str


def of(request, decision, line, project):
    """The `Proposal` of one request the policy decided as a proposal, or None when the decision is no proposal, or its
    caller holds no role in the channel."""
    if str(getattr(decision, "kind", "")) != S.PROPOSAL or not getattr(decision, "role", None):
        return None
    arguments = dict(getattr(request, "arguments", {}) or {})
    text = str(arguments.get("title") or arguments.get("question") or "")
    return Proposal(str((line or {}).get("id") or (line or {}).get("ts") or ""), str(request.action), text,
                    str(project), str(request.channel), str((line or {}).get("user") or ""))


__all__ = ["Proposal", "of"]
