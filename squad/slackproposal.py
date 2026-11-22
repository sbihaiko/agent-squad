"""A guest request as a proposal to the owner (SPEC 2682, stories 61 and 62; #2718).

A role holder in a `guest` channel never starts work. The policy gives the request the kind `proposal`, and this module
turns the request into one `Proposal` the owner decides on. The action never runs before the owner approves, and an issue
is born only from the exact text the owner approved.

Guards: `tests/test_slack_proposal.py`.
"""
import re
from typing import NamedTuple

from . import slackpolicy as S
from . import slackwriter as W

# The personal data a summary never carries: a mention (and the name in it), a Slack user id, an email address and a
# phone number. The request itself stays, because the owner decides on that text.
_MENTION = re.compile(r"<@[^>]*>")
_USER_ID = re.compile(r"\bU[A-Z0-9]{6,}\b")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PHONE = re.compile(r"\+?\d[\d\s().-]{6,}\d")
# The action in words the owner reads. A raw action id holds an underscore, and an underscore inside the one italic body
# breaks the writer's persona mark rule, so the summary never prints one.
_ASK = {"request_work": "work", "request_investigation": "an investigation"}


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
    if not text.strip():
        return None
    return Proposal(str((line or {}).get("id") or (line or {}).get("ts") or ""), str(request.action), text,
                    str(project), str(request.channel), str((line or {}).get("user") or ""))


def clean(text):
    """The text with the personal data removed: a mention, a Slack user id, an email address and a phone number each
    become one placeholder. The requester's id never reaches the summary, whatever the client writes around it."""
    text = _MENTION.sub("[a person]", str(text or ""))
    text = _USER_ID.sub("[a person]", text)
    text = _EMAIL.sub("[email]", text)
    text = _PHONE.sub("[phone]", text)
    # `*` and `_` are the two characters the persona mark rule reads, so the body never carries them.
    return text.replace("*", "").replace("_", " ")


def summary(proposal):
    """The one-line summary of a proposal the owner reads: the action in words, and the request with its personal data
    removed. It never carries the requester's id or the guest channel id."""
    ask = _ASK.get(proposal.action, str(proposal.action).replace("_", " "))
    return f"A role holder in a guest channel asks for {ask}: {clean(proposal.text)}"


def dm(proposal, owner_dm, name):
    """The proposal prepared for the owner's direct message, in the shape every guest text carries, or None when no
    persona names the mark. It is a draft: the owner decides whether it leaves."""
    if not name:
        return None
    return W.Draft(W.DRAFT_WORD, str(owner_dm), "", W.compose(name, summary(proposal)), str(name))


# The three owner decisions of one proposal. The whole set, so a reply outside it decides nothing.
CONFIRM, EDIT, REFUSE = "confirm", "edit", "refuse"
DECISIONS = (CONFIRM, EDIT, REFUSE)


class Answer(NamedTuple):
    """The owner's one reply to a proposal: the kind, the destination the reply names, and the edited text of an
    `edit`. A `confirm` or a `refuse` carries no text of its own."""
    kind: str
    destination: str
    text: str


class Approval(NamedTuple):
    """What the owner approved: the exact text of the issue and its destination. Only an `Approval` reaches the issue
    source, so a proposal that no owner approved is never born."""
    text: str
    destination: str


def parse_owner(reply, destinations):
    """The `Answer` of one owner reply, or None when the reply names no decision. The first word is the decision, and
    the destination is the one project id of `destinations` the reply names. A reply that names no destination, or one
    outside the list, approves nothing: the owner always approves the destination."""
    words = str(reply or "").strip()
    verb, _, rest = words.partition(" ")
    verb = verb.lower().strip(":")
    if verb not in DECISIONS:
        return None
    if verb == REFUSE:
        return Answer(REFUSE, "", "")
    # The longest match wins, because one destination id can be a prefix of another (`acme/app`, `acme/app2`).
    destination = next((str(d) for d in sorted(destinations, key=lambda d: len(str(d)), reverse=True)
                        if d and str(d) in rest), "")
    after = rest.replace(destination, "", 1).lstrip(" :|\t") if destination else ""
    return Answer(verb, destination, after)


def approve(proposal, answer):
    """The `Approval` of one owner reply, or None. A `confirm` approves the client's exact text; an `edit` approves the
    owner's exact text; a `refuse` and a reply with no destination approve nothing."""
    if answer is None or answer.kind not in (CONFIRM, EDIT) or not answer.destination:
        return None
    text = str(answer.text) if answer.kind == EDIT else str(proposal.text)
    if not text.strip():
        return None
    return Approval(text, answer.destination)


def create(approval, issue):
    """Start the issue from one `Approval`, and return the issue source's answer. Anything else creates nothing: a raw
    proposal never reaches the source, so no client content goes public before the owner's review."""
    if not isinstance(approval, Approval) or not approval.text.strip() or not approval.destination.strip():
        return None
    return issue(approval.destination, approval.text)


__all__ = ["Answer", "Approval", "CONFIRM", "DECISIONS", "EDIT", "Proposal", "REFUSE", "approve", "clean", "create",
           "dm", "of", "parse_owner", "summary"]
