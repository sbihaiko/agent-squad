"""A release event as one draft in each `guest` channel that subscribes to it (SPEC 2682, story 71; #2715).

The promotion pass, or a project's own deploy step, emits one record through `release_events.py emit` (#2702). This module
maps that record to the drafts a project's `guest` channels get: one draft per channel whose `announce` list names the
event. The draft follows the writer's own rules (#2712): the tool is the connector's draft tool, the text carries the
persona mark, and it leaves through the `PreToolUse` hook. It names no thread, because an announcement is a channel post.

The template is the channel's own `templates[<event>]` when it has one, else the project's `events` row for that event.
The record's fields fill it: `{items}` joins the items with a comma and a space, and `{id}`, `{key}` and `{ts}` carry the
record's own values. A placeholder the record does not carry stays as written, so a template that names another value
shows it to the owner instead of dropping it in silence.

A channel gets nothing when it does not subscribe, when it is `listen` (a read channel never receives a post), when the
event has no template, or when the channel resolves no persona (no persona means no mark, so no write). An `owned`
channel posts as the bot and is not this module's work.

The module reads no clock and calls no network. Guards: `tests/test_slack_announce.py`.
"""
import re

from . import slackpolicy
from . import slackwriter as W

_FIELD = re.compile(r"\{(\w+)\}")


def _rows(value):
    """The mappings of a list, so a file whose shape is wrong yields no row instead of raising."""
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def subscribed(channel, event_id):
    """True when the channel's `announce` list names the event."""
    announce = channel.get("announce")
    return str(event_id) in {str(x) for x in announce} if isinstance(announce, list) else False


def template(committed, channel, event_id):
    """The text template of one event for one channel: the channel's own override, else the project's row for the event.
    None when neither names a template."""
    override = channel.get("templates")
    if isinstance(override, dict) and override.get(event_id) is not None:
        return str(override[event_id])
    for row in _rows((committed or {}).get("events")):
        if str(row.get("id")) == str(event_id) and row.get("template") is not None:
            return str(row["template"])
    return None


def render(template_text, record):
    """The template with the record's own fields filled. `{items}` joins the items in the record's order; `{id}`, `{key}`
    and `{ts}` carry the record's values. A placeholder the record does not carry stays as written."""
    values = {"id": record.get("id", ""), "key": record.get("key", ""), "ts": record.get("ts", ""),
              "items": ", ".join(str(item) for item in record.get("items") or [])}
    return _FIELD.sub(lambda hit: str(values[hit.group(1)]) if hit.group(1) in values else hit.group(0),
                      str(template_text))


def draft_for(committed, local, workspace, channel, record):
    """The one draft a channel gets for one record, or None when the channel gets nothing: it does not subscribe, it is
    `listen`, the event has no template, the rendered body is empty, or the channel resolves no persona."""
    event_id = str(record.get("id") or "")
    if not event_id or not subscribed(channel, event_id):
        return None
    if str(channel.get("mode") or "listen") != "listen+speak":
        return None
    text_template = template(committed, channel, event_id)
    if text_template is None:
        return None
    body = render(text_template, record).strip()
    if not body:
        return None
    persona = slackpolicy.effective_persona(committed, local, workspace, channel)
    name = getattr(persona, "name", None) if persona is not None else None
    if not name:
        return None
    channel_id = str(channel.get("id") or "")
    if not channel_id:
        return None
    return W.Draft(W.DRAFT_WORD, channel_id, "", W.compose(name, body), str(name))


def announcements(committed, local, record):
    """Every draft a project's `guest` channels get for one release record, one per channel, in the file's order. The
    committed file is read before the local one, and one channel id yields one draft, whatever both files name."""
    out, seen = [], set()
    for doc in (committed or {}, local or {}):
        for workspace in _rows(doc.get("workspaces")):
            if str(workspace.get("class") or "owned") != "guest":
                continue
            for channel in _rows(workspace.get("channels")):
                channel_id = str(channel.get("id") or "")
                if not channel_id or channel_id in seen:
                    continue
                draft = draft_for(committed, local, workspace, channel, record)
                if draft is not None:
                    seen.add(channel_id)
                    out.append(draft)
    return out
