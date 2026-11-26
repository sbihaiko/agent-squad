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


import copy


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


EDIT_FIELDS = ("name", "voice", "tone")


def _declared_name(doc, persona_id):
    """The name the committed document declares for one persona id, or '' when it declares none."""
    pid = _text(persona_id).strip()
    for row in _persona_rows(doc):
        if _text(row.get("id")).strip() == pid:
            return _text(row.get("name")).strip()
    return ""


def with_fields(doc, persona_id, fields):
    """The committed document with one persona's name, voice and tone set to the fields the edit names. A field the edit
    does not name keeps its value, and every other key keeps its place and its form. None when the document declares no
    persona with that id: a persona only the local file declares is read-only."""
    out = copy.deepcopy(_doc(doc))
    rows = out.get("personas")
    pid = _text(persona_id).strip()
    for row in rows if isinstance(rows, list) else ():
        if isinstance(row, dict) and _text(row.get("id")).strip() == pid:
            for key in EDIT_FIELDS:
                if key in fields:
                    row[key] = _text(fields[key]).strip()
            return out
    return None


def _declared_persona_rows(doc):
    """The persona rows one document holds, as they stand there: a document that is not a mapping holds none."""
    rows = _doc(doc).get("personas")
    return list(rows) if isinstance(rows, list) else []


def _first_declared_name(seats, persona_id):
    """The first name any seat of the roster declares for one persona id, or '', for the copy a refusal states."""
    for seat in seats or ():
        name = _declared_name(seat.get("committed"), persona_id)
        if name:
            return name
    return ""


def without(doc, persona_id):
    """The committed document with the row of one persona id removed, and every other row and key in its place. None
    when the document declares no persona with that id: a persona only the local file declares is read-only."""
    rows = _declared_persona_rows(doc)
    pid = _text(persona_id).strip()
    kept, changed = [], False
    for row in rows:
        if isinstance(row, dict) and _text(row.get("id")).strip() == pid:
            changed = True   # every row of the id goes, so an id a document declares twice loses both rows
            continue
        kept.append(row)
    if not changed:
        return None
    out = copy.deepcopy(_doc(doc))
    out["personas"] = copy.deepcopy(kept)
    return out


def remove(seats, persona_id, targets, holders=None):
    """The plan of one persona removal: one row for each target project, and the committed document a confirmed save
    writes into each project it can remove the persona from.

    `holders` maps a project id to the places its files and its clients name the persona, from the policy module. A
    target with any holder is refused, and the refusal names every holder, even when the committed file does not declare
    the row. A target the roster does not hold, a project with no worktree, a file whose read answered a defect, and a
    project whose committed file does not declare the id are each refused by name. Every other target reads
    "removes <name>" and carries its document, so one refused project never stops the others."""
    pid = _text(persona_id).strip()
    held = holders or {}
    by_id = {}
    for seat in seats or ():
        sid = _text(seat.get("id")).strip()
        if sid and sid not in by_id:
            by_id[sid] = seat
    rows, writes = [], []
    for target in targets or ():
        tid = _text(target).strip()
        seat = by_id.get(tid)
        label = (_text(seat.get("label")) or tid) if seat else tid
        row = {"project": tid, "label": label, "file": _text(seat.get("cf")) if seat else ""}
        if seat is None:
            rows.append({**row, "result": "refused: the project is not in the roster"})
            continue
        if not _text(seat.get("checkout")).strip():
            rows.append({**row, "result": "refused: the project has no worktree"})
            continue
        defects = list(seat.get("read_defects") or ())
        if defects:
            rows.append({**row, "result": f"refused: the file does not read: {_text(defects[0])}"})
            continue
        places = [str(place) for place in (held.get(tid) or ()) if _text(place).strip()]
        if places:
            # A holder outranks a missing committed row: a place of either file names the persona, so the refusal names
            # the holders and never reports the row as undeclared (acceptance 6).
            rows.append({**row, "result": f"refused: held by {'; '.join(places)}"})
            continue
        changed = without(seat.get("committed"), pid)
        # The name comes from the target project alone: another project's declaration never names this project's persona.
        name = _declared_name(seat.get("committed"), pid) or pid
        if changed is None:
            rows.append({**row, "result": f"refused: {name} is not declared in this project's committed file"})
            continue
        rows.append({**row, "result": f"removes {name}"})
        writes.append({"project": tid, "committed": changed})
    return {"rows": rows, "writes": writes}


def edit(seats, persona_id, fields, targets):
    """The plan of one persona edit: one row for each target project, and the committed document a confirmed save writes
    into each project it can write.

    A target the roster does not hold, a project with no worktree, a file whose read answered a defect, and a project
    whose committed file does not declare the id are each refused by name. Every other target reads "writes <name>" and
    carries its document, so one refused project never stops the others."""
    pid = _text(persona_id).strip()
    by_id, order = {}, []
    for seat in seats or ():
        sid = _text(seat.get("id")).strip()
        if sid and sid not in by_id:
            by_id[sid] = seat
            order.append(sid)
    rows, writes = [], []
    # The name a refusing row states comes from any project that declares the id, so a project that does not declare
    # it still names the persona the edit was about.
    known = _first_declared_name(seats, pid)
    for target in targets or ():
        tid = _text(target).strip()
        seat = by_id.get(tid)
        label = (_text(seat.get("label")) or tid) if seat else tid
        row = {"project": tid, "label": label, "file": _text(seat.get("cf")) if seat else ""}
        if seat is None:
            rows.append({**row, "result": "refused: the project is not in the roster"})
            continue
        if not _text(seat.get("checkout")).strip():
            rows.append({**row, "result": "refused: the project has no worktree"})
            continue
        defects = list(seat.get("read_defects") or ())
        if defects:
            rows.append({**row, "result": f"refused: the file does not read: {_text(defects[0])}"})
            continue
        changed = with_fields(seat.get("committed"), pid, fields)
        name = _text(fields.get("name")).strip() or _declared_name(seat.get("committed"), pid) or known or pid
        if changed is None:
            rows.append({**row, "result": f"refused: {name} is not declared in this project's committed file"})
            continue
        rows.append({**row, "result": f"writes {name}"})
        writes.append({"project": tid, "committed": changed})
    return {"rows": rows, "writes": writes}


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
