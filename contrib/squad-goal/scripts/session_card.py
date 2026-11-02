"""The question card a session raises (#1093): the session's open AskUserQuestion call, and the one message that answers it.

The card is read from the session's transcript, so its pending state belongs to the session and not to a tab. The answer
is plain text for the session's own send route (session_send.send); this module writes nothing.
"""
import hashlib, json

CARD_TOOL = "AskUserQuestion"
CLIENT_PREFIX = "card-"


class Refused(Exception):
    """An answer that sends nothing; the message names the cause."""


def pending(lines, redact=lambda s: s):
    """The newest question the session raised and no tool result has answered, as a card, or None."""
    raised, answered = {}, set()
    for obj in lines:
        content = (obj.get("message") or {}).get("content")
        for part in content if isinstance(content, list) else []:
            if not isinstance(part, dict): continue
            if part.get("type") == "tool_use" and part.get("name") == CARD_TOOL: raised[part.get("id")] = part.get("input")
            elif part.get("type") == "tool_result": answered.add(part.get("tool_use_id"))
    open_ids = [i for i in raised if i not in answered]
    if not open_ids: return None
    questions = _questions(raised[open_ids[-1]], redact)
    if not questions: return None
    return {"id": open_ids[-1], "questions": questions}


def _questions(tool_input, redact):
    asked = tool_input.get("questions") if isinstance(tool_input, dict) else None
    out = []
    for i, q in enumerate(asked if isinstance(asked, list) else []):
        if not isinstance(q, dict): continue
        options = q.get("options") if isinstance(q.get("options"), list) else []
        out.append({"header": redact(str(q.get("header") or f"Question {i + 1}")),
                    "body": redact(str(q.get("question") or "")),
                    "options": [{"label": redact(str(o.get("label") or "")), "description": redact(str(o.get("description") or ""))}
                                for o in options if isinstance(o, dict)]})
    return out


def answer(card, card_id, picks):
    """(text, client message id) for one pick per question, or Refused when the card is not open, a question is unanswered
    or a pick falls outside its options. The client id is fixed by the card and the picks, so a repeat sends nothing new."""
    if not card or card.get("id") != card_id: raise Refused("no open question card with that id")
    questions = card["questions"]
    if not isinstance(picks, list) or len(picks) != len(questions): raise Refused("answer every question of the card")
    unanswered = [f"{i + 1}. {q['header']}" for i, (q, p) in enumerate(zip(questions, picks))
                  if not isinstance(p, int) or isinstance(p, bool) or not 0 <= p < len(q["options"])]
    if unanswered: raise Refused("not answered: " + ", ".join(unanswered))
    text = "Answers: " + "; ".join(f"{i + 1}. {q['header']}: {q['options'][p]['label']}"
                                   for i, (q, p) in enumerate(zip(questions, picks)))
    return text, CLIENT_PREFIX + hashlib.sha256(json.dumps([card_id, picks]).encode()).hexdigest()[:32]
