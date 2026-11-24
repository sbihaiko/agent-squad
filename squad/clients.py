"""The local client map and its proposed writes (SPEC 2682, issue #2827).

A client groups the projects of one customer, across profiles. The map is a local file in the profile folder,
`clients.local.yaml`, and it never enters a repository: a client name and a client id stay out of every committed file.

A client has a default persona, and the map records for each project whether the project follows that default. A change
of the default proposes one write to each project: a project that follows the default has `project.persona` set to the
new persona, but only when the project declares that persona; a project the map records as not following keeps its own
persona. The map adds no policy layer of its own: it only names which `project.persona` value each project receives, and
the write goes through the project's own save path.

This module holds the rules. The hub route reads the map and calls this module; the page draws what the route answers
(ADR-0137, ADR-0177).
"""
import copy
import pathlib

CLIENTS_FILE = "clients.local.yaml"
UNASSIGNED_ID = "unassigned"
UNASSIGNED_NAME = "Unassigned"

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
    """The client groups over the projects the hub knows: each client's projects in map order, then one Unassigned group
    with the projects no client names. A project a client names but the hub does not know is dropped, and a project a
    second client names keeps its first place (ADR-0150: one project is one node)."""
    known = list(project_ids)
    taken = set()
    out = []
    for client in clients:
        projects = [{"id": p["id"], "follows": p["follows"]} for p in client["projects"]
                    if p["id"] in known and p["id"] not in taken]
        taken.update(p["id"] for p in projects)
        out.append({"id": client["id"], "name": client["name"], "default_persona": client["default_persona"],
                    "projects": projects})
    rest = [{"id": pid, "follows": True} for pid in known if pid not in taken]
    if rest:
        out.append({"id": UNASSIGNED_ID, "name": UNASSIGNED_NAME, "default_persona": None, "projects": rest})
    return out


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
    """A project has Slack when either file declares a key beyond `version`; a pair that names only a version is the
    project with no Slack."""
    return any(key != "version" for doc in (committed or {}, local or {}) for key in doc)


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
