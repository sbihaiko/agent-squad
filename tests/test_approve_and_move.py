"""The `approve_and_move` action of the deterministic executor (SPEC 2682, stories 26 and 67; #2706).

A role holder approves the review of a card and moves it between columns after Todo. The action never moves a card to
Todo, never touches a spec, and never approves a breakdown into tickets (ADR-0142). A role that does not list the action
is refused. Each test runs one request through the executor and a fake board, so the calls the board received are the
proof of what the action did.

The decision of each case comes from the real policy module, so the grant the executor reads is the role's list and
never the executor's own rule.
"""
import copy
import json

from squad import slackboard as B
from squad import slackexecutor as X
from squad import slackpolicy as S

REPO = "acme/app"
WORKSPACE = "T0TEAM0001"
CHANNEL = "C0PROJ0001"
OWNER = "U0OWNER001"
PEER = "U0PEER0001"
MID = "C0PROJ0001:1762500100.000100"

COMMITTED = {
    "version": 1,
    "project": {"language": "en-US", "persona": "hal"},
    "personas": [{"id": "hal", "name": "HAL9K", "voice": "Kore", "tone": "Calm and direct."}],
    "roles": [
        {"id": "manager", "actions": ["status", "logs", "approve_and_move"]},
        {"id": "colleague", "actions": ["status"]},
    ],
    "workspaces": [
        {"id": WORKSPACE, "label": "team", "class": "owned", "app": "hal9k",
         "channels": [
             {"id": CHANNEL, "name": "proj", "mode": "listen+speak", "roles": ["manager", "colleague"]},
         ]},
    ],
}

LOCAL = {
    "version": 1,
    "members": [
        {"workspace": WORKSPACE, "channel": CHANNEL, "role": "manager", "users": [OWNER]},
        {"workspace": WORKSPACE, "channel": CHANNEL, "role": "colleague", "users": [PEER]},
    ],
}


def decision(user=OWNER):
    """One inbox decision of the real policy module for one message of the project channel."""
    committed, local = copy.deepcopy(COMMITTED), copy.deepcopy(LOCAL)
    event = {"kind": "message", "workspace": WORKSPACE, "channel": CHANNEL, "user": user}
    return S.decide(committed, local, event, now=None)


def line(user=OWNER, role="manager"):
    """One inbox line, as the operator session writes it: the source message, its caller, the channel and its role."""
    return {"id": MID, "ts": "1762500100.000100", "user": user, "channel": CHANNEL, "language": "en-US",
            "persona": "HAL9K", "role": role, "text": "approve the review of #2690 and move it to Batching"}


def request_text(arguments):
    """The JSON the operator session emits for one `approve_and_move` request."""
    return json.dumps({"action": "approve_and_move", "arguments": arguments, "project": REPO, "channel": CHANNEL,
                       "source": MID})


class FakeBoard:
    """A board in memory: the card of each number, and every call the executor made on it, in order."""

    def __init__(self, *cards):
        self.cards = {str(card.number): card for card in cards}
        self.calls = []

    def card(self, number):
        self.calls.append(("card", str(number)))
        return self.cards.get(str(number))

    def approve(self, number, kind="unstated"):
        self.calls.append(("approve", str(number), str(kind)))

    def move(self, number, column):
        self.calls.append(("move", str(number), str(column)))
        self.cards[str(number)] = self.cards[str(number)]._replace(column=str(column))

    def writes(self):
        """The calls that change the board: an approval or a move, and never the read of a card."""
        return [call for call in self.calls if call[0] in ("approve", "move")]


def run(board, arguments, user=OWNER, role=None):
    """One request through the executor, its real decision, its inbox line and the fake board."""
    request, why = X.parse(request_text(arguments))
    assert request is not None, why
    dec = decision(user)
    return X.execute(request, dec, line(user, role or dec.role), REPO, board=board)


def test_a_role_holder_can_approve_a_review():
    """Criterion (1): a manager lists `approve_and_move`, and the action approves the review of the card."""
    board = FakeBoard(B.Card("2690", "Reviewing"))
    out = run(board, {"card": "2690", "to": "Batching"})
    assert out.ran is True
    assert ("approve", "2690", "review") in board.writes()
    assert out.reply == {"path": "bot", "channel": CHANNEL, "text": "approved #2690, moved from Reviewing to Batching"}


def test_a_role_holder_can_move_a_card_between_columns_after_todo():
    """Criterion (2): the action moves the card forward, from a column after Todo to another one."""
    board = FakeBoard(B.Card("2690", "Reviewing"))
    out = run(board, {"card": "2690", "to": "Batching"})
    assert out.ran is True
    assert ("move", "2690", "Batching") in board.writes()
    assert board.cards["2690"].column == "Batching"


def test_a_card_only_moves_forward():
    """Criterion (2) and ADR-0143: a card never moves back, so Reviewing to Doing changes nothing."""
    board = FakeBoard(B.Card("2690", "Reviewing"))
    out = run(board, {"card": "2690", "to": "Doing"})
    assert out.ran is False
    assert out.record["error"].startswith("board:")
    assert board.writes() == []
    assert board.cards["2690"].column == "Reviewing"


def test_the_action_never_moves_a_card_to_todo():
    """Criterion (3): Todo starts work, so it stays with the owner, and the action never writes it."""
    board = FakeBoard(B.Card("2690", "Reviewing"))
    out = run(board, {"card": "2690", "to": "Todo"})
    assert out.ran is False
    assert out.record["error"].startswith("board:")
    assert "Todo" in out.record["error"]
    assert board.writes() == []
    assert board.cards["2690"].column == "Reviewing"


def test_the_action_never_lifts_a_card_out_of_todo():
    """Criterion (3) and ADR-0142: a card in Todo is the owner's go signal, so the action never touches it."""
    board = FakeBoard(B.Card("2690", "Todo"))
    out = run(board, {"card": "2690", "to": "Doing"})
    assert out.ran is False
    assert out.record["error"].startswith("board:")
    assert board.writes() == []


def test_the_action_never_touches_a_spec():
    """Criterion (4): a spec is a document, so the action neither approves it nor moves it."""
    board = FakeBoard(B.Card("2682", "Backlog", ("spec", "enhancement")))
    out = run(board, {"card": "2682", "to": "Batching"})
    assert out.ran is False
    assert out.record["error"].startswith("board:")
    assert "spec" in out.record["error"]
    assert board.writes() == []


def test_the_action_never_approves_a_breakdown_into_tickets():
    """Criterion (5) and ADR-0142: the action takes only a card and a column, so a request that names a breakdown is
    refused before the board is written."""
    board = FakeBoard(B.Card("2682", "Reviewing", ("spec",)))
    out = run(board, {"card": "2682", "to": "Batching", "breakdown": "2682"})
    assert out.ran is False
    assert out.record["error"].startswith("arguments:")
    assert board.writes() == []


def test_a_role_without_approve_and_move_is_refused():
    """Criterion (6): the colleague's role lists only `status`, so the action is refused and the board is untouched. The
    decision comes from the real policy, so its role and its action list are the ones the files give."""
    board = FakeBoard(B.Card("2690", "Reviewing"))
    dec = decision(PEER)
    assert dec.role == "colleague"
    assert X.APPROVE_AND_MOVE not in tuple(dec.actions)
    out = run(board, {"card": "2690", "to": "Batching"}, user=PEER)
    assert out.ran is False
    assert out.record["error"].startswith("grant:")
    assert board.writes() == []


def test_an_unknown_action_with_arguments_writes_no_board():
    """Finding (a): an action outside the five makes no board write, and its refusal names the action check."""
    board = FakeBoard(B.Card("2690", "Reviewing"))
    request, why = X.parse(json.dumps({"action": "approve_everything",
                                       "arguments": {"card": "2690", "to": "Batching"},
                                       "project": REPO, "channel": CHANNEL, "source": MID}))
    assert request is not None, why
    out = X.execute(request, decision(), line(), REPO, board=board)
    assert out.ran is False
    assert out.record["error"].startswith("action:")
    assert board.writes() == []


def test_a_board_request_missing_its_card_or_target_is_an_arguments_refusal():
    """Finding (a): a request that names no card or no target is a structured arguments refusal, never a KeyError."""
    for arguments in ({"to": "Batching"}, {"card": "2690"}):
        board = FakeBoard(B.Card("2690", "Reviewing"))
        out = run(board, arguments)
        assert out.ran is False, arguments
        assert out.record["error"].startswith("arguments:"), out.record["error"]
        assert board.writes() == []


def test_the_board_records_a_review_and_refuses_a_breakdown():
    """Criterion (5) and ADR-0142: the action approves a review only. The board records the kind it approved, and a
    breakdown is refused before any write, even for a card with no spec label that sits in a column after Todo."""
    board = FakeBoard(B.Card("2690", "Reviewing"))
    out = run(board, {"card": "2690", "to": "Batching"})
    assert out.ran is True
    assert board.writes() == [("approve", "2690", "review"), ("move", "2690", "Batching")]

    board = FakeBoard(B.Card("2682", "Reviewing"))
    text, why = B.approve_and_move(board, "2682", "Batching", kind="breakdown")
    assert text is None and why is not None
    assert "breakdown" in why
    assert board.writes() == []


def test_a_fake_board_proves_every_rule():
    """Criterion (7): each rule leaves the fake board unmoved, and the one allowed request writes exactly one approval
    and one move. The reasons are distinct, so no rule hides behind another."""
    rules = (
        ((), "2690", "Batching", "board"),
        ((B.Card("2690", "Reviewing"),), "2690", "Todo", "board"),
        ((B.Card("2690", "Reviewing"),), "2690", "Doing", "board"),
        ((B.Card("2690", "Reviewing"),), "2690", "Abandoned", "board"),
        ((B.Card("2690", "Reviewing"),), "2690", "Nope", "board"),
        ((B.Card("2690", "Todo"),), "2690", "Doing", "board"),
        ((B.Card("2682", "Reviewing", ("spec",)),), "2682", "Batching", "board"),
    )
    reasons = []
    for cards, number, to, check in rules:
        board = FakeBoard(*cards)
        out = run(board, {"card": number, "to": to})
        assert out.ran is False, f"#{number} to {to} ran"
        assert out.record["error"].startswith(f"{check}:"), out.record["error"]
        assert board.writes() == [], f"#{number} to {to} wrote the board"
        reasons.append(out.record["error"])
    assert len(set(reasons)) == len(reasons)

    board = FakeBoard(B.Card("2690", "Reviewing"))
    out = run(board, {"card": "2690", "to": "Batching", "breakdown": "2682"})
    assert out.ran is False and out.record["error"].startswith("arguments:")
    assert board.writes() == []

    board = FakeBoard(B.Card("2690", "Reviewing"))
    out = run(board, {"card": "2690", "to": "Batching"})
    assert out.ran is True
    assert board.writes() == [("approve", "2690", "review"), ("move", "2690", "Batching")]
