"""A release event as one post in each `owned` channel that subscribes to it (SPEC 2682, story 71; #2703).

The promotion pass, or a project's own deploy step, emits one record through `release_events.py emit` (#2702). This module
maps that record to the posts a project's `owned` channels get: one post per channel whose `announce` list names the
event. The bot of an `owned` Slack workspace posts under the channel persona's name and carries no mark, because the bot
identity itself says what it is.

The template is the channel's own `templates[<event>]` when it has one, else the project's `events` row for that event.
The record's fields fill it: `{items}` joins the items with a comma and a space, and `{id}`, `{key}` and `{ts}` carry the
record's own values. A placeholder the record does not carry stays as written, so a template that names another value
shows it instead of dropping it in silence.

A channel gets nothing when it does not subscribe, when it is `listen` (a read channel never receives a post), when the
event has no template, or when the channel has no id. A channel that resolves no persona posts as the app it is. A
`guest` channel is not this module's work: its notice is a draft through the write hook (#2715).

The module reads no clock and calls no network. Guards: `tests/test_slack_announce_owned.py`.
"""
import re
from typing import NamedTuple

from . import slackpolicy

_FIELD = re.compile(r"\{(\w+)\}")


class Post(NamedTuple):
    """One notice an `owned` channel gets: the channel id, the text, and the persona name the bot posts under."""
    channel: str
    text: str
    persona: str | None


def _rows(value):
    """The mappings of a list, so a file whose shape is wrong yields no row instead of raising."""
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def subscribed(channel, event_id):
    """True when the channel's `announce` list names the event."""
    announce = channel.get("announce")
    return str(event_id) in {str(x) for x in announce} if isinstance(announce, list) else False


def template(committed, channel, event_id):
    """The text template of one event for one channel: the channel's own override, else the project's row for the event.
    None when neither names a template. An override key is compared as text, so a YAML or JSON map that keys the event
    by a number still matches the event's id."""
    override = channel.get("templates")
    if isinstance(override, dict):
        keyed = {str(key): value for key, value in override.items()}
        if keyed.get(str(event_id)) is not None:
            return str(keyed[str(event_id)])
    for row in _rows((committed or {}).get("events")):
        if str(row.get("id")) == str(event_id) and row.get("template") is not None:
            return str(row["template"])
    return None


def render(template_text, record):
    """The template with the record's own fields filled. `{items}` joins the items in the record's order; `{id}`, `{key}`
    and `{ts}` carry the record's values. A placeholder the record does not carry stays as written."""
    values = {}
    if "items" in record:
        values["items"] = ", ".join(str(item) for item in record.get("items") or [])
    for key in ("id", "key", "ts"):
        if key in record:
            values[key] = record[key]
    return _FIELD.sub(lambda hit: str(values[hit.group(1)]) if hit.group(1) in values else hit.group(0),
                      str(template_text))


def post_for(committed, local, channel, record):
    """The one post a channel gets for one record, or None when the channel gets nothing: it does not subscribe, the
    channel is `listen` (or names no mode), the event has no template, the rendered body is empty, or the row has no id.
    Every other mode is writable, so a `speak` channel that subscribes gets its notice."""
    event_id = str(record.get("id") or "")
    if not event_id or not subscribed(channel, event_id):
        return None
    if str(channel.get("mode") or "listen") == "listen":
        return None
    text_template = template(committed, channel, event_id)
    if text_template is None:
        return None
    body = render(text_template, record).strip()
    if not body:
        return None
    channel_id = str(channel.get("id") or "")
    if not channel_id:
        return None
    persona = slackpolicy.identity(committed, local or {}, channel_id)[1]
    name = persona.name if persona is not None and persona.name else None
    return Post(channel_id, body, name)


def announcements(committed, local, record):
    """Every post a project's `owned` channels get for one release record, one per channel id, in the file's order.
    A channel id that two workspace rows repeat takes the first row's persona and override; later rows add nothing."""
    out, seen = [], set()
    for workspace in _rows((committed or {}).get("workspaces")):
        if str(workspace.get("class") or "owned") != "owned":
            continue
        for channel in _rows(workspace.get("channels")):
            post = post_for(committed, local, channel, record)
            if post is not None and post.channel not in seen:
                seen.add(post.channel)
                out.append(post)
    return out
