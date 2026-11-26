"""The personas of the roster projects, grouped by client and merged by id (SPEC 3009, issue #3010).

A project declares its personas in the committed `slack.yaml` and in the profile's `slack.local.yaml`. The client map
groups the projects of one customer. This module merges the two facts into the rows the Settings > Personas theme draws:
one row for each client and persona id, with the fields one declaration answers, every declaring project and its file,
the clients whose default the id is, and a drift flag when two projects of one client declare different fields.

The committed row wins over a local row with the same id. Drift lives inside one client alone, because a client answers
its own voice and tone. A persona only a local file declares reads read-only. A project no client names forms its own
group.

This module holds the rules. The hub route reads the documents and calls this module; the page draws what the route
answers (ADR-0137, ADR-0177).
"""


def _text(value):
    return "" if value is None else str(value)


def _doc(value):
    return value if isinstance(value, dict) else {}


def _persona_rows(doc):
    """The persona rows of one document, in file order. A row with no id and a row that is not a mapping name nothing."""
    value = _doc(doc).get("personas")
    return [row for row in (value if isinstance(value, list) else ())
            if isinstance(row, dict) and _text(row.get("id")).strip()]


def _fields(row):
    """One persona row as the row the theme draws: id, name, voice and tone. A row with no name answers its id."""
    pid = _text(row.get("id")).strip()
    return {"id": pid, "name": _text(row.get("name")).strip() or pid,
            "voice": _text(row.get("voice")).strip(), "tone": _text(row.get("tone")).strip()}


def declarations(seat):
    """The personas one project declares, in document order: the committed file first, then the local file's rows whose
    id the committed file does not name. A declaration carries the file it is written in and whether that file is the
    committed one."""
    committed, local = _doc(seat.get("committed")), _doc(seat.get("local"))
    cf, lf = _text(seat.get("cf")) or "slack.yaml", _text(seat.get("lf")) or "slack.local.yaml"
    out, named = [], set()
    for doc, file, is_committed in ((committed, cf, True), (local, lf, False)):
        for row in _persona_rows(doc):
            fields = _fields(row)
            if fields["id"] in named:
                continue
            named.add(fields["id"])
            out.append({**fields, "file": file, "committed": is_committed})
    return out


def _same_fields(a, b):
    return (a["name"], a["voice"], a["tone"]) == (b["name"], b["voice"], b["tone"])


def _merge(pairs, defaults):
    """The rows of one group: `pairs` is one `(project id, label, seat)` for each project of the group, in order. The
    first declaration of an id answers its fields; a later declaration of the same id in the same group adds its
    project and raises the drift flag when its fields differ."""
    rows, index = [], {}
    for project_id, label, seat in pairs:
        for decl in declarations(seat):
            row = index.get(decl["id"])
            if row is None:
                row = {"id": decl["id"], "name": decl["name"], "voice": decl["voice"], "tone": decl["tone"],
                       "projects": [], "default_of": list(defaults.get(decl["id"], [])),
                       "drift": False, "read_only": decl["committed"] is False}
                index[decl["id"]] = row
                rows.append(row)
            elif not _same_fields(row, decl):
                row["drift"] = True
            if decl["committed"]:
                row["read_only"] = False
            if all(p["id"] != project_id for p in row["projects"]):
                row["projects"].append({"id": project_id, "label": label, "file": decl["file"]})
    return rows


def groups(seats, clients):
    """The Personas groups: one for each client of the map, in map order, then one for each project no client names.

    A group carries its id, its name and its rows. A project the map names but the hub does not know is dropped, and a
    project a second client names keeps its first place, as the client map itself draws them (ADR-0150)."""
    by_id, order = {}, []
    for seat in seats or ():
        sid = _text(seat.get("id")).strip()
        if sid and sid not in by_id:
            by_id[sid] = seat
            order.append(sid)
    defaults = {}
    for client in clients or ():
        pid = _text((client.get("default_persona") or {}).get("id")).strip()
        if pid:
            defaults.setdefault(pid, []).append(client["id"])
    out, taken = [], set()
    for client in clients or ():
        pairs = []
        for entry in client.get("projects") or ():
            pid = entry["id"]
            if pid in taken or pid not in by_id:
                continue
            taken.add(pid)
            seat = by_id[pid]
            pairs.append((pid, _text(seat.get("label")) or pid, seat))
        out.append({"id": client["id"], "name": client["name"], "rows": _merge(pairs, defaults)})
    for sid in order:
        if sid in taken:
            continue
        seat = by_id[sid]
        out.append({"id": sid, "name": _text(seat.get("label")) or sid,
                    "rows": _merge([(sid, _text(seat.get("label")) or sid, seat)], defaults)})
    return out
