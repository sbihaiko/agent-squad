"""A release event as one draft in each `guest` channel that subscribes to it (SPEC 2682, story 71; #2715).

The promotion pass, or a project's own deploy step, emits one record through `release_events.py emit` (#2702). This module
maps that record to the drafts a project's `guest` channels get: one draft per channel whose `announce` list names the
event. The draft follows the writer's own rules (#2712): the tool is the connector's draft tool, the text carries the
persona mark, and it leaves through the `PreToolUse` hook. It names no thread, because an announcement is a channel
post, and its connector call comes from `slackwriter.channel_post`, which sends no thread at all.

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


def draft_for(committed, local, workspace, channel, record):
    """The one draft a channel gets for one record, or None when the channel gets nothing: it does not subscribe, it is
    `listen`, the event has no template, the rendered body is empty, or the channel resolves no persona. The draft names
    no thread: its connector call comes from `slackwriter.channel_post`."""
    event_id = str(record.get("id") or "")
    if not event_id or not subscribed(channel, event_id):
        return None
    if not slackpolicy.can_speak(channel):
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


def _over(base, row):
    """One row over another: a nested `templates` map merges key by key, so a local row that adds one event keeps the
    others; an `announce` list is the union by event id, in the committed order then the local additions, so a local row
    that names one event never drops a committed one; every other key, list and scalar alike, replaces the committed
    value."""
    merged = dict(base)
    for key, value in row.items():
        if key == "templates" and isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**merged[key], **value}
        elif key == "announce" and isinstance(value, list) and isinstance(merged.get(key), list):
            union, seen = list(merged[key]), {str(item) for item in merged[key]}
            for item in value:
                if str(item) not in seen:
                    seen.add(str(item))
                    union.append(item)
            merged[key] = union
        else:
            merged[key] = value
    return merged


def _merge_by_id(rows):
    """Rows of both documents as one list, one row per id: the committed row first, then the local row's own keys over
    it, so a local override wins and a key the local row omits stays as committed. The `templates` map merges per event
    key and the `announce` list unions per event id, so a local row that adds one event keeps the committed templates
    and events of the others. A row with no id never merges, and the first appearance of an id fixes its place in the
    list."""
    out, index = [], {}
    for row in rows:
        row_id = str(row.get("id") or "")
        if row_id and row_id in index:
            out[index[row_id]] = _over(out[index[row_id]], row)
        else:
            out.append(dict(row))
            if row_id:
                index[row_id] = len(out) - 1
    return out


def _guest_workspaces(committed, local):
    """One merged row per workspace id from both documents, with each workspace's channels merged in turn, so a local
    file overrides a committed field and adds a channel the committed file omits. The committed file fixes the
    workspace `class`: a local row cannot demote a `guest` workspace, because the committed file decides what the
    workspace is. The persona row a channel names still comes from the committed file first
    (`slackpolicy.effective_persona`)."""
    groups, index = [], {}
    for doc in (committed or {}, local or {}):
        for row in _rows(doc.get("workspaces")):
            workspace_id = str(row.get("id") or "")
            scalars = {key: value for key, value in row.items() if key != "channels"}
            channels = _rows(row.get("channels"))
            if workspace_id and workspace_id in index:
                group = groups[index[workspace_id]]
                fixed = group["workspace"].get("class")
                group["workspace"].update(scalars)
                if fixed is not None:
                    group["workspace"]["class"] = fixed
                group["channels"].extend(channels)
            else:
                groups.append({"workspace": scalars, "channels": list(channels)})
                if workspace_id:
                    index[workspace_id] = len(groups) - 1
    for group in groups:
        group["channels"] = _merge_by_id(group["channels"])
    return groups


def announcements(committed, local, record):
    """Every draft a project's `guest` channels get for one release record, one per channel, in the file's order. The
    two documents are merged per workspace id and per channel id, committed first and local over it, so one channel in
    one workspace yields one draft, whatever both files name. The dedup key is (workspace id, channel id): a channel id
    that a second guest workspace also names gets that workspace's own draft."""
    out, seen = [], set()
    for group in _guest_workspaces(committed, local):
        if str(group["workspace"].get("class") or "owned") != "guest":
            continue
        workspace_id = str(group["workspace"].get("id") or "")
        for channel in group["channels"]:
            channel_id = str(channel.get("id") or "")
            key = (workspace_id, channel_id)
            if not channel_id or key in seen:
                continue
            seen.add(key)
            draft = draft_for(committed, local, group["workspace"], channel, record)
            if draft is not None:
                out.append(draft)
    return out
