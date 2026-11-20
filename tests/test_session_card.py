"""The question card a session raises (#1093): read from the session's transcript, answered through the session's own send route.

The card is the session's open AskUserQuestion call; an answer is a message the session's send route carries. No test here
opens a socket or reads a real session: the transcript is a list of lines and the send route is replaced. The phone page
carries no card surface (#2490): it reads no card and answers none, and the hub's route stays for the desktop overlay.
"""
import json
import pathlib
import re
import sys

import pytest

import tests.test_hub_mobile_move as move

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "contrib" / "squad-goal" / "scripts"))
import session_card  # noqa: E402

hub = move.hub


def _ask(tool_id, questions):
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": tool_id, "name": "AskUserQuestion",
                                                          "input": {"questions": questions}}]}}


def _answered(tool_id):
    return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tool_id, "content": "ok"}]}}


SCOPE = {"header": "Scope", "question": "Which scope?", "multiSelect": False,
         "options": [{"label": "Narrow", "description": "Only the hub"}, {"label": "Wide", "description": "Every surface"}]}
DEPTH = {"header": "Depth", "question": "How deep?", "multiSelect": False,
         "options": [{"label": "Shallow", "description": "a"}, {"label": "Deep", "description": "b"}]}
FIRST = _ask("toolu_A", [SCOPE, DEPTH])
PENDING_CARD = {"id": "toolu_A", "questions": [
    {"header": "Scope", "body": "Which scope?", "options": [{"label": "Narrow", "description": "Only the hub"},
                                                            {"label": "Wide", "description": "Every surface"}]},
    {"header": "Depth", "body": "How deep?", "options": [{"label": "Shallow", "description": "a"}, {"label": "Deep", "description": "b"}]}]}
PLAIN = {"type": "user", "message": {"content": "hello"}}


def test_the_pending_card_is_the_open_question_the_session_raised():
    assert session_card.pending([PLAIN, FIRST]) == PENDING_CARD


def test_an_answered_question_is_not_pending():
    assert session_card.pending([FIRST, _answered("toolu_A")]) is None


def test_a_transcript_with_no_question_has_no_card():
    assert session_card.pending([PLAIN]) is None


def test_the_latest_open_question_is_the_pending_card():
    second = _ask("toolu_B", [DEPTH])
    card = session_card.pending([FIRST, _answered("toolu_A"), second])
    assert card["id"] == "toolu_B"
    assert [q["header"] for q in card["questions"]] == ["Depth"]


def test_a_redacted_text_never_reaches_the_card():
    card = session_card.pending([FIRST], redact=lambda s: s.replace("Narrow", "[redacted]"))
    assert card["questions"][0]["options"][0]["label"] == "[redacted]"


def test_every_answered_question_travels_in_one_message():
    text, cid = session_card.answer(PENDING_CARD, "toolu_A", [1, 1])
    assert text == "Answers: 1. Scope: Wide; 2. Depth: Deep"
    assert re.fullmatch(r"[A-Za-z0-9-]{8,64}", cid)


def test_the_client_id_repeats_for_the_same_answer_and_changes_with_it():
    assert session_card.answer(PENDING_CARD, "toolu_A", [0, 1])[1] == session_card.answer(PENDING_CARD, "toolu_A", [0, 1])[1]
    assert session_card.answer(PENDING_CARD, "toolu_A", [0, 1])[1] != session_card.answer(PENDING_CARD, "toolu_A", [1, 1])[1]


def test_an_unanswered_question_is_refused_and_named():
    with pytest.raises(session_card.Refused, match=r"not answered: 2\. Depth"):
        session_card.answer(PENDING_CARD, "toolu_A", [1, None])


def test_a_short_answer_list_is_refused():
    with pytest.raises(session_card.Refused):
        session_card.answer(PENDING_CARD, "toolu_A", [1])


def test_an_answer_outside_the_options_is_refused():
    with pytest.raises(session_card.Refused):
        session_card.answer(PENDING_CARD, "toolu_A", [1, 5])


def test_an_answer_to_a_card_that_is_not_open_is_refused():
    with pytest.raises(session_card.Refused):
        session_card.answer(PENDING_CARD, "toolu_Z", [1, 1])
    with pytest.raises(session_card.Refused):
        session_card.answer(None, "toolu_A", [1, 1])


def test_the_hub_answers_through_the_session_send_route(hub, monkeypatch):
    sent = []
    monkeypatch.setattr(hub.sessions, "pending_card", lambda *a, **k: PENDING_CARD)
    monkeypatch.setattr(hub.session_send, "send", lambda profile, sid, text, cid, **k: sent.append((sid, text, cid)) or {"status": "sent", "reason": ""})
    out = hub.card_answer_payload({"sid": "s1", "card_id": "toolu_A", "answers": [1, 1]})
    assert out == {"status": "sent", "reason": ""}
    assert [(s, t) for s, t, _ in sent] == [("s1", "Answers: 1. Scope: Wide; 2. Depth: Deep")]


def test_the_page_reads_no_session_card_and_answers_none(hub):
    """The card surface left the phone page with the chat (#2490); the hub's route still answers for the overlay."""
    page = hub.MOBILE_PAGE
    assert "/api/sessions/card" not in page, "the phone page still names the card route"
    assert "loadCard(" not in page and "cardAnswer(" not in page, "the phone page still reads or answers a card"


def test_the_hub_refuses_an_unanswered_card_without_sending(hub, monkeypatch):
    sent = []
    monkeypatch.setattr(hub.sessions, "pending_card", lambda *a, **k: PENDING_CARD)
    monkeypatch.setattr(hub.session_send, "send", lambda *a, **k: sent.append(a) or {"status": "sent", "reason": ""})
    out = hub.card_answer_payload({"sid": "s1", "card_id": "toolu_A", "answers": [1, None]})
    assert out["status"] == "refused" and "2. Depth" in out["reason"]
    assert sent == []


def test_the_transcript_tail_of_a_session_holds_its_open_card(tmp_path, monkeypatch):

    import sessions
    monkeypatch.setenv("HOME", str(tmp_path))
    cwd = "/work/project"
    folder = tmp_path / ".claude" / "projects" / "-work-project"
    folder.mkdir(parents=True)
    lines = [PLAIN, FIRST]
    (folder / "sid-1.jsonl").write_text("\n".join(json.dumps(line) for line in lines) + "\n")
    (folder / "sid-1.jsonl").chmod(0o600)
    assert session_card.pending(sessions._transcript_tail(cwd, "sid-1")) == PENDING_CARD
