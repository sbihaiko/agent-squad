"""The local client map and its proposed writes (SPEC 2682, issue #2827).

A client groups the projects of one customer, across profiles. The map is a local file in the profile folder,
`clients.local.yaml`, and it never enters a repository: a client name and a client id stay out of every committed file.

A client has a default persona, and the map records for each project whether the project follows that default. A project
is written either as a mapping `{id, follows}` or as its plain id; a plain id follows the default, so a hand-edited map
that names no flag follows rather than keeps. A change of the default proposes one write to each project: a project that
follows the default has `project.persona` set to the new persona, but only when the project declares that persona; a
project the map records as not following keeps its own persona. The settings pane shows the follow state as a badge on
each project, so a shorthand entry never follows silently. The map adds no policy layer of its own: it only names which
`project.persona` value each project receives, and the write goes through the project's own save path.

This module holds the rules. The hub route reads the map and calls this module; the page draws what the route answers
(ADR-0137, ADR-0177).
"""
import copy
import pathlib
import re
import unicodedata

CLIENTS_FILE = "clients.local.yaml"

KEPT = "kept: the project set its own persona"
NOTHING = "nothing to write"
WRITE, KEPT_KIND, REFUSED, NONE = "write", "kept", "refused", "none"


def map_path(profile_dir):
    """The client map of one profile folder, or None when the folder is unknown. The file is local to the profile: it is
    never a file of a repository."""
    return pathlib.Path(profile_dir) / CLIENTS_FILE if profile_dir else None


def _rows(value):
    return [row for row in value or () if isinstance(row, dict)] if isinstance(value, list) else []


def _text(value):
    return "" if value is None else str(value)


def _persona(value):
    """One default-persona entry: a mapping `{id, name}`, or a plain string that is its own id and its own name."""
    if isinstance(value, dict):
        pid = _text(value.get("id")).strip()
        return {"id": pid, "name": _text(value.get("name")).strip() or pid} if pid else None
    pid = _text(value).strip()
    return {"id": pid, "name": pid} if pid else None


def normalize(doc):
    """The map as a list of clients: id, name, default persona and the projects it groups, each with its follow flag.
    A client with no id and a project with no id are dropped; they name nothing. A repeated project keeps its first
    place, so the tree and the proposal draw each project once."""
    clients, seen = [], set()
    for row in _rows((doc or {}).get("clients")):
        cid = _text(row.get("id")).strip()
        if not cid or cid in seen:
            continue
        seen.add(cid)
        projects, named = [], set()
        for project in row.get("projects") or ():
            if isinstance(project, dict):
                pid, follows = _text(project.get("id")).strip(), project.get("follows", True)
            else:
                pid, follows = _text(project).strip(), True
            if not pid or pid in named:
                continue
            named.add(pid)
            projects.append({"id": pid, "follows": follows is not False})
        clients.append({"id": cid, "name": _text(row.get("name")).strip() or cid,
                        "default_persona": _persona(row.get("default_persona")), "projects": projects})
    return clients


def groups(clients, project_ids):
    """The client groups over the projects the hub knows: each client's projects in map order. A project a client names
    but the hub does not know is dropped, and a project a second client names keeps its first place (ADR-0150: one
    project is one node). A project no client names is drawn nowhere (#2922): it waits in each client's Add project
    list, and the tree holds no synthetic group."""
    known = list(project_ids)
    taken = set()
    out = []
    for client in clients:
        projects = [{"id": p["id"], "follows": p["follows"]} for p in client["projects"]
                    if p["id"] in known and p["id"] not in taken]
        taken.update(p["id"] for p in projects)
        out.append({"id": client["id"], "name": client["name"], "default_persona": client["default_persona"],
                    "projects": projects})
    return out


def free_projects(clients, project_ids):
    """The projects no client names, in the hub's own order: every one of them waits in each client's Add project
    list (#2922)."""
    named = {p["id"] for client in clients for p in client["projects"]}
    return [pid for pid in project_ids if pid not in named]


def _project_id(entry):
    """One raw project entry of the map: its id, whichever form the row takes."""
    return (_text(entry.get("id")) if isinstance(entry, dict) else _text(entry)).strip()


def with_project(doc, client_id, project_id):
    """The raw map with one project added to one client as a follower of the client's default persona, written as the
    map's plain-id shorthand. Every other key and row keeps its place and its form. A client the map does not name, and
    a project the client already holds, leave the map unchanged."""
    out = copy.deepcopy(doc if isinstance(doc, dict) else {})
    wanted, pid = _text(client_id).strip(), _text(project_id).strip()
    rows = out.get("clients")
    for row in rows if isinstance(rows, list) else ():
        if isinstance(row, dict) and _text(row.get("id")).strip() == wanted:
            projects = row.get("projects")
            if not isinstance(projects, list):
                projects = row["projects"] = []
            if pid and pid not in {_project_id(p) for p in projects}:
                projects.append(pid)
            break
    return out


def without_project(doc, client_id, project_id):
    """The raw map with one project removed from one client. The project's own Slack files are no business of this
    map (#2922): only the client's row changes."""
    out = copy.deepcopy(doc if isinstance(doc, dict) else {})
    wanted, pid = _text(client_id).strip(), _text(project_id).strip()
    rows = out.get("clients")
    for row in rows if isinstance(rows, list) else ():
        if isinstance(row, dict) and _text(row.get("id")).strip() == wanted:
            projects = row.get("projects")
            if isinstance(projects, list):
                row["projects"] = [p for p in projects if _project_id(p) != pid]
            break
    return out


def with_client(doc, client_id, name, persona_id=None, persona_name=None):
    """The raw map with one new client, and no project below it. A client id the map already names leaves the map
    unchanged."""
    out = copy.deepcopy(doc if isinstance(doc, dict) else {})
    cid = _text(client_id).strip()
    rows = out.get("clients")
    if not isinstance(rows, list):
        rows = out["clients"] = []
    if not cid or any(isinstance(r, dict) and _text(r.get("id")).strip() == cid for r in rows):
        return out
    row = {"id": cid, "name": _text(name).strip() or cid, "projects": []}
    pid = _text(persona_id).strip()
    if pid:
        row["default_persona"] = {"id": pid, "name": _text(persona_name).strip() or pid}
    rows.append(row)
    return out


def without_client(doc, client_id):
    """The raw map with one client removed, and every other client in its place. The projects it held stay in the hub;
    they wait now in each remaining client's Add project list (#2922)."""
    out = copy.deepcopy(doc if isinstance(doc, dict) else {})
    rows = out.get("clients")
    if isinstance(rows, list):
        wanted = _text(client_id).strip()
        out["clients"] = [r for r in rows if not (isinstance(r, dict) and _text(r.get("id")).strip() == wanted)]
    return out


def slug(text):
    """A client id from a name: the ASCII words of the name, lower-case, joined by a hyphen."""
    plain = unicodedata.normalize("NFKD", _text(text))
    return "-".join(re.findall(r"[a-z0-9]+", "".join(ch for ch in plain if not unicodedata.combining(ch)).lower()))


def new_client_id(clients, name):
    """A client id no client of the map holds: the name as a slug, then the same slug with the next free number."""
    base = slug(name) or "client"
    taken = {c["id"] for c in clients}
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def _declared(P, committed, local):
    """The persona ids one project declares, from its committed and its local file, in order."""
    out = []
    for doc in (committed or {}, local or {}):
        for row in P.rows(doc.get("personas")):
            pid = _text(row.get("id"))
            if pid and pid not in out:
                out.append(pid)
    return out


def _persona_name(P, committed, local, wanted):
    """The display name of one persona id in a project's files, else the id itself, else None."""
    if not wanted:
        return None
    for doc in (committed or {}, local or {}):
        for row in P.rows(doc.get("personas")):
            if _text(row.get("id")) == _text(wanted):
                return _text(row.get("name")) or _text(wanted)
    return _text(wanted)


def has_slack(committed, local):
    """A project has Slack when either file declares a non-empty list of workspaces or of personas; a pair that names
    only a version, a project block or a role is the project with no Slack."""
    for doc in (committed or {}, local or {}):
        if not isinstance(doc, dict):
            continue
        for key in ("workspaces", "personas"):
            value = doc.get(key)
            if isinstance(value, list) and value:
                return True
    return False


def _after(committed, persona_id):
    """The committed document with `project.persona` set, and every other value as it stands. The project key is made
    when the committed file names none, so the committed value wins over the local file's."""
    after = copy.deepcopy(committed) if isinstance(committed, dict) else {}
    project = after.get("project")
    after["project"] = dict(project) if isinstance(project, dict) else {}
    after["project"]["persona"] = persona_id
    return after


def proposal(P, client, projects, persona_id, persona_name=None):
    """One row for each project of the client, and the committed documents a save writes. `projects` maps a project id to
    `{"label", "committed", "local"}`.

    A project with no Slack file reads "nothing to write". A project the map records as not following the default reads
    "kept: the project set its own persona". A follower that does not declare the persona reads
    "refused: X is not declared in this project". A follower that already answers the persona reads "nothing to write".
    Every other follower reads "writes X", and its row carries the committed document a save writes."""
    name = persona_name or persona_id
    rows, writes = [], []
    for entry in client["projects"]:
        pid, follows = entry["id"], entry["follows"]
        seat = projects.get(pid) or {}
        committed, local = seat.get("committed") or {}, seat.get("local") or {}
        label = seat.get("label") or pid
        if not has_slack(committed, local):
            rows.append({"project": pid, "label": label, "now": "no Slack", "action": NONE, "result": NOTHING})
            continue
        current = P.project_settings(committed, local).get("persona")
        now = _persona_name(P, committed, local, current) or "none"
        if not follows:
            rows.append({"project": pid, "label": label, "now": now, "action": KEPT_KIND, "result": KEPT})
            continue
        if persona_id not in _declared(P, committed, local):
            rows.append({"project": pid, "label": label, "now": now, "action": REFUSED,
                         "result": f"refused: {name} is not declared in this project"})
            continue
        if _text(current) == _text(persona_id):
            rows.append({"project": pid, "label": label, "now": now, "action": NONE, "result": NOTHING})
            continue
        after = _after(committed, persona_id)
        rows.append({"project": pid, "label": label, "now": now, "action": WRITE, "result": f"writes {name}"})
        writes.append({"project": pid, "committed": after})
    return {"rows": rows, "writes": writes}


def with_default(doc, client_id, persona_id, persona_name=None):
    """The raw map with one client's default persona set to `{id, name}`. Every other key keeps its place and its form,
    so a save of the default never rewrites the projects or another client. A client the map does not name leaves the
    map unchanged."""
    out = copy.deepcopy(doc if isinstance(doc, dict) else {})
    for row in _rows(out.get("clients")):
        if _text(row.get("id")).strip() == _text(client_id).strip():
            pid = _text(persona_id).strip()
            row["default_persona"] = {"id": pid, "name": _text(persona_name).strip() or pid}
            break
    return out
