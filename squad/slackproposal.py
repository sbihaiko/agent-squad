"""A guest request as a proposal to the owner (SPEC 2682, stories 61 and 62; #2718).

A role holder in a `guest` channel never starts work. The policy gives the request the kind `proposal`, and this module
turns the request into one `Proposal` the owner decides on. The action never runs before the owner approves, and an issue
is born only from the exact text the owner approved.

The owner's message carries a short token of the proposal, and the reply names that token, so a reply answers exactly
one of the pending proposals. The summary blanks every personal datum, including an address written with words instead
of signs, and it keeps a version, a date and an amount. An action the module has no vetted words for offers no
proposal at all.

Guards: `tests/test_slack_proposal.py`.
"""
import hashlib
import re
from typing import NamedTuple

from . import slackpolicy as S
from . import slackwriter as W

# The personal data a summary never carries: a mention (and the name in it), a Slack user id, an email address and a
# phone number. The request itself stays, because the owner decides on that text.
_MENTION = re.compile(r"<@[^>]*>")
_USER_ID = re.compile(r"\bU[A-Z0-9]{6,}\b")
# An address written with signs or with words: `ana@client.example`, `ana at client dot example` and the mixed forms.
# The local part may itself join words with `dot`, so `ana dot silva at client dot example` matches as one address.
_AT = r"(?:\s*@\s*|\s*\(\s*at\s*\)\s*|\s*\[\s*at\s*\]\s*|\s+at\s+)"
_DOT = r"(?:\s*\.\s*|\s*\(\s*dot\s*\)\s*|\s*\[\s*dot\s*\]\s*|\s+dot\s+)"
_EMAIL = re.compile(rf"(?<![\w.+-])[\w.+-]+(?:\s+dot\s+[\w.+-]+)*{_AT}[\w-]+(?:{_DOT}[\w-]+)+(?![\w-])",
                    re.IGNORECASE)
# A phone candidate: a run of digits joined by the separators a phone number uses, bounded so a word never enters it.
_PHONE = re.compile(r"(?<![\w+])\+?\(?\d[\d\s().-]{5,}\d(?![\w])")
# The forms that share a phone's shape and are not one: a version, a date and a short number.
_VERSION = re.compile(r"\d+(?:\.\d+){1,3}")
_ISO_DATE = re.compile(r"\d{4}[-./ ]\d{1,2}[-./ ]\d{1,2}")
_DATE = re.compile(r"\d{1,2}[-./ ]\d{1,2}[-./ ]\d{2,4}")
# The action in words the owner reads. A raw action id holds an underscore, and an underscore inside the one italic body
# breaks the writer's persona mark rule, so the summary never prints one. An action the table does not name offers no
# proposal, so an unvetted phrase never reaches the owner.
_ASK = {"request_work": "work", "request_investigation": "an investigation"}
# The proposal token of one reply: eight hex digits of its id, so the token holds no `:` of a Slack message id and the
# owner's reply names exactly one proposal.
_TOKEN = re.compile(r"[0-9a-f]{8}")


class Proposal(NamedTuple):
    """One guest request the owner decides on: its id, the action it asked for, the text the client wrote, the project
    the issue would go to, the guest channel, and the caller's own user id (personal data, never in the summary)."""
    id: str
    action: str
    text: str
    project: str
    channel: str
    requester: str


def token(proposal):
    """The eight hex digits that name one proposal in the owner's reply. The token comes from the proposal id, so two
    pending proposals never share one, and it holds no `:` or space to break the reply's token order."""
    return hashlib.sha1(str(proposal.id).encode("utf-8")).hexdigest()[:8]


def of(request, decision, line, project):
    """The `Proposal` of one request the policy decided as a proposal, or None when the decision is no proposal, its
    caller holds no role in the channel, its action has no vetted words for the owner, or the line names no id."""
    if str(getattr(decision, "kind", "")) != S.PROPOSAL or not getattr(decision, "role", None):
        return None
    action = str(getattr(request, "action", ""))
    if action not in _ASK:
        return None
    arguments = dict(getattr(request, "arguments", {}) or {})
    text = str(arguments.get("title") or arguments.get("question") or "")
    proposal_id = str((line or {}).get("id") or (line or {}).get("ts") or "")
    if not text.strip() or not proposal_id:
        return None
    return Proposal(proposal_id, action, text, str(project), str(request.channel),
                    str((line or {}).get("user") or ""))


def _phone(match):
    """One phone candidate as its placeholder, or kept unchanged when it is a version, a date or a short number. The
    separators of a phone never make the summary eat a version, a date or an amount."""
    text = match.group(0)
    digits = sum(ch.isdigit() for ch in text)
    if text.startswith("+") or "(" in text:
        return "[phone]" if digits >= 7 else text
    if digits < 7 or _VERSION.fullmatch(text) or _ISO_DATE.fullmatch(text) or _DATE.fullmatch(text):
        return text
    return "[phone]"


def clean(text):
    """The text with the personal data removed: a mention, a Slack user id, an email address and a phone number each
    become one placeholder. The requester's id never reaches the summary, whatever the client writes around it."""
    text = _MENTION.sub("[a person]", str(text or ""))
    text = _USER_ID.sub("[a person]", text)
    text = _EMAIL.sub("[email]", text)
    text = _PHONE.sub(_phone, text)
    # `*` and `_` are the two characters the persona mark rule reads, so the body never carries them.
    return text.replace("*", "").replace("_", " ")


def summary(proposal):
    """The one-line summary of a proposal the owner reads: the action in words, and the request with its personal data
    removed. It never carries the requester's id or the guest channel id. An action with no vetted words has none."""
    ask = _ASK.get(proposal.action)
    if ask is None:
        return None
    return f"A role holder in a guest channel asks for {ask}: {clean(proposal.text)}"


def reply_line(proposal, destination):
    """The one reply line the owner sends back: the verb, the destination the owner approves, the proposal token, and
    an edit's exact text. It names the token, so the reply answers exactly one of the pending proposals."""
    token_ = token(proposal)
    return (f"Reply with one line: confirm {destination} {token_}, edit {destination} {token_}: <the exact text>, "
            f"or refuse {token_}.")


def dm(proposal, owner_dm, name, destination=None):
    """The proposal prepared for the owner's direct message, in the shape every guest text carries, or None when no
    persona names the mark. The body holds the summary and the reply line that names the proposal token. It is a
    draft: the owner decides whether it leaves."""
    if not name:
        return None
    body = summary(proposal)
    if body is None:
        return None
    body = f"{body}\n\n{reply_line(proposal, destination or proposal.project)}"
    return W.Draft(W.DRAFT_WORD, str(owner_dm), "", W.compose(name, body), str(name))


# The three owner decisions of one proposal. The whole set, so a reply outside it decides nothing.
CONFIRM, EDIT, REFUSE = "confirm", "edit", "refuse"
DECISIONS = (CONFIRM, EDIT, REFUSE)


class Answer(NamedTuple):
    """The owner's one reply to a proposal: the kind, the destination the reply names, the edited text of an `edit`,
    and the proposal token the reply names. A `confirm` or a `refuse` carries no text of its own."""
    kind: str
    destination: str
    text: str
    id: str = ""


class Approval(NamedTuple):
    """What the owner approved: the id of the proposal it answers, the exact text of the issue, its destination, and
    whether the owner edited the text. Only an `Approval` of the proposal itself reaches the issue source."""
    proposal: str
    text: str
    destination: str
    edited: bool = False


def _first(rest):
    """The leading token of a reply's remainder and what follows it, separated by one space."""
    head, _, tail = str(rest or "").strip().partition(" ")
    return head, tail.strip()


def parse_owner(reply, destinations):
    """The `Answer` of one owner reply, or None when the reply names no decision. The verb and the destination are the
    leading delimited tokens of the reply, and the proposal token follows them; the free text body is never scanned for
    a destination. A destination outside `destinations` parses as none, so the owner always approves the destination.

    `confirm <destination> <token>`, `edit <destination> <token>: <text>`, and `refuse <token>`."""
    words = str(reply or "").strip()
    verb, sep, rest = words.partition(" ")
    verb = verb.lower().rstrip(":")
    if verb not in DECISIONS:
        return None
    rest = rest.strip() if sep else ""
    if verb == REFUSE:
        token_, _ = _first(rest)
        return Answer(REFUSE, "", "", token_ if _TOKEN.fullmatch(token_) else "")
    destination, tail = _first(rest)
    destination = destination.rstrip(":,")
    known = {str(d) for d in destinations if d}
    if destination not in known:
        return Answer(verb, "", "", "")
    token_, text = "", ""
    head, _, body = tail.partition(":")
    if body and _TOKEN.fullmatch(head.strip()):
        token_, text = head.strip(), body.lstrip(" :|\t")
    elif _TOKEN.fullmatch(tail):
        token_ = tail
    elif verb == EDIT:
        text = tail
    return Answer(verb, destination, text, token_)


def approve(proposal, answer):
    """The `Approval` of one owner reply, or None. A `confirm` approves the client's exact text; an `edit` approves the
    owner's exact text; a `refuse`, a reply that names no proposal token, a token of another proposal, a reply with no
    destination, and an empty edited text each approve nothing."""
    if answer is None or answer.kind not in (CONFIRM, EDIT) or not answer.destination:
        return None
    if not answer.id or not proposal or answer.id != token(proposal):
        return None
    text = str(answer.text) if answer.kind == EDIT else str(proposal.text)
    if not text.strip():
        return None
    return Approval(str(proposal.id), text, answer.destination, answer.kind == EDIT)


def create(proposal, approval, issue, destinations):
    """Start the issue from one `Approval`, and return the issue source's answer. Anything else creates nothing: a raw
    proposal never reaches the source, and an `Approval` must answer this proposal, name a destination the owner may
    choose, and carry the client's text unchanged or the owner's own edited text. So no client content goes public
    before the owner's review."""
    if not isinstance(proposal, Proposal) or not isinstance(approval, Approval):
        return None
    if approval.proposal != proposal.id:
        return None
    if approval.destination not in {str(d) for d in destinations if d}:
        return None
    if not approval.text.strip():
        return None
    if not approval.edited and approval.text != proposal.text:
        return None
    return issue(approval.destination, approval.text)


__all__ = ["Answer", "Approval", "CONFIRM", "DECISIONS", "EDIT", "Proposal", "REFUSE", "approve", "clean", "create",
           "dm", "of", "parse_owner", "reply_line", "summary", "token"]
