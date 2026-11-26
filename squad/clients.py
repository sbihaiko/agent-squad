"""The local client map and its proposed writes (SPEC 2682, SPEC 3031).

A client groups the projects of one customer, across profiles. The map is a local file in the profile folder,
`clients.local.yaml`, and it never enters a repository: a client name and a client id stay out of every committed file.

A client has a default persona and a default language, and the map records for each project whether the project follows
each default. A project is written either as a mapping `{id, follows, follows_language}` or as its plain id; a plain id
follows both defaults, so a hand-edited map that names no flag follows rather than keeps, and the two flags stay apart:
a change of `follows_language` never changes `follows`, and the other way round. A change of a default proposes one
write to each project that follows it: a following project has `project.persona` or `project.language` set to the new
value; a project the map records as not following keeps its own value. The settings pane shows the follow state as a
badge on each project, so a shorthand entry never follows silently. The map adds no policy layer of its own: it only
names which `project.persona` or `project.language` value each project receives, and the write goes through the
project's own save path.

The two defaults differ on a refusal. A persona the project does not declare refuses its own write. A language write
reads every target first: an unknown project, a project with no Slack file and an unreadable file are named apart, and
a project whose files do not validate is refused while the others are written.

This module holds the rules. The hub route reads the map and calls this module; the page draws what the route answers
(ADR-0137, ADR-0177).
"""
import copy
import pathlib
import re
import unicodedata

CLIENTS_FILE = "clients.local.yaml"

KEPT = "kept: the project set its own persona"
KEPT_LANGUAGE = "kept: the project follows its own language"
NOTHING = "nothing to write"
NO_SLACK = "no Slack file: nothing to write"
WRITE, KEPT_KIND, REFUSED, NONE = "write", "kept", "refused", "none"
UNKNOWN = "unknown"


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


def _language(value):
    """One default-language entry: the tag as the map writes it, or None when it names none."""
    tag = _text(value).strip()
    return tag or None


def normalize(doc):
    """The map as a list of clients: id, name, default persona, default language and the projects it groups, each with
    its two follow flags. A client with no id and a project with no id are dropped; they name nothing. A repeated
    project keeps its first place, so the tree and the proposal draw each project once."""
    clients, seen = [], set()
    for row in _rows((doc or {}).get("clients")):
        cid = _text(row.get("id")).strip()
        if not cid or cid in seen:
            continue
        seen.add(cid)
        projects, named = [], set()
        for project in row.get("projects") or ():
            if isinstance(project, dict):
                pid = _text(project.get("id")).strip()
                follows, follows_language = project.get("follows", True), project.get("follows_language", True)
            else:
                pid, follows, follows_language = _text(project).strip(), True, True
            if not pid or pid in named:
                continue
            named.add(pid)
            projects.append({"id": pid, "follows": follows is not False,
                             "follows_language": follows_language is not False})
        clients.append({"id": cid, "name": _text(row.get("name")).strip() or cid,
                        "default_persona": _persona(row.get("default_persona")),
                        "default_language": _language(row.get("default_language")), "projects": projects})
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
        projects = [{"id": p["id"], "follows": p["follows"], "follows_language": p.get("follows_language", True)}
                    for p in client["projects"] if p["id"] in known and p["id"] not in taken]
        taken.update(p["id"] for p in projects)
        out.append({"id": client["id"], "name": client["name"], "default_persona": client["default_persona"],
                    "default_language": client.get("default_language"), "projects": projects})
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


def _after_language(committed, language):
    """The committed document with `project.language` set, and every other value as it stands. The project key is made
    when the committed file names none, so the committed value wins over the local file's."""
    after = copy.deepcopy(committed) if isinstance(committed, dict) else {}
    project = after.get("project")
    after["project"] = dict(project) if isinstance(project, dict) else {}
    after["project"]["language"] = language
    return after


def language_proposal(P, client, projects, language, local_name="slack.local.yaml"):
    """One row for each project of the client, and the committed documents a save writes, for a change of the client
    default language. `projects` maps a project id to `{"label", "committed", "local", "read_defects"}`.

    A project the map names and the hub does not hold reads "unknown project". A project with a file that does not
    read reads its defect, before any write. A project with no Slack file reads "no Slack file". A project the map
    records as not following the language reads "kept: the project follows its own language". A follower that already
    answers the language reads "nothing to write". A follower whose files do not validate reads "refused: <defect>".
    Every other follower reads "writes X", and its row carries the committed document a save writes."""
    rows, writes = [], []
    for entry in client["projects"]:
        pid, follows = entry["id"], entry["follows_language"]
        seat = projects.get(pid)
        if seat is None:
            rows.append({"project": pid, "label": pid, "now": "", "action": UNKNOWN,
                         "result": f"unknown project: the hub holds no project {pid}"})
            continue
        label = seat.get("label") or pid
        committed, local = seat.get("committed") or {}, seat.get("local") or {}
        stored = P.project_settings(committed, local).get("stored_language")
        now = stored or "none"
        unreadable = [str(d) for d in seat.get("read_defects") or ()]
        if unreadable:
            rows.append({"project": pid, "label": label, "now": now, "action": REFUSED,
                         "result": f"refused: {unreadable[0]}"})
            continue
        if not has_slack(committed, local):
            rows.append({"project": pid, "label": label, "now": now, "action": NONE, "result": NO_SLACK})
            continue
        if not follows:
            rows.append({"project": pid, "label": label, "now": now, "action": KEPT_KIND, "result": KEPT_LANGUAGE})
            continue
        if _text(stored) == _text(language):
            rows.append({"project": pid, "label": label, "now": now, "action": NONE, "result": NOTHING})
            continue
        after = _after_language(committed, language)
        defects = list(P.validate(committed=after, local=local, local_name=local_name))
        if defects:
            rows.append({"project": pid, "label": label, "now": now, "action": REFUSED,
                         "result": f"refused: {defects[0]}"})
            continue
        rows.append({"project": pid, "label": label, "now": now, "action": WRITE, "result": f"writes {language}"})
        writes.append({"project": pid, "committed": after})
    return {"rows": rows, "writes": writes}


def _follow_flag(doc, client_id, project_id, key, follows):
    """The raw map with one follow flag of one project set. Every other key and row keeps its place and its form; the
    other follow flag is never touched. A client or a project the map does not name leaves the map unchanged."""
    out = copy.deepcopy(doc if isinstance(doc, dict) else {})
    wanted, pid = _text(client_id).strip(), _text(project_id).strip()
    for row in _rows(out.get("clients")):
        if not isinstance(row, dict) or _text(row.get("id")).strip() != wanted:
            continue
        projects = row.get("projects")
        if not isinstance(projects, list):
            continue
        for i, project in enumerate(projects):
            if _project_id(project) != pid:
                continue
            entry = dict(project) if isinstance(project, dict) else {"id": pid}
            entry[key] = follows is not False
            projects[i] = entry
            return out
    return out


def with_follow_language(doc, client_id, project_id, follows):
    """The raw map with one project's `follows_language` set. The persona `follows` flag keeps its value, so a change
    of one follow state never changes the other."""
    return _follow_flag(doc, client_id, project_id, "follows_language", follows)


def with_language_unfollow(doc, project_id):
    """The raw map with the project's `follows_language` off, in the client that holds it: a direct edit of a following
    project's language makes that project an exception. The persona `follows` flag keeps its value."""
    out = copy.deepcopy(doc if isinstance(doc, dict) else {})
    pid = _text(project_id).strip()
    for row in _rows(out.get("clients")):
        if not isinstance(row, dict):
            continue
        projects = row.get("projects")
        if not isinstance(projects, list):
            continue
        for i, project in enumerate(projects):
            if _project_id(project) != pid:
                continue
            entry = dict(project) if isinstance(project, dict) else {"id": pid}
            entry["follows_language"] = False
            projects[i] = entry
            return out
    return out


def with_default_language(doc, client_id, language):
    """The raw map with one client's default language set. Every other key keeps its place and its form, so a save of
    the default never rewrites the projects or another client. A client the map does not name leaves the map
    unchanged."""
    out = copy.deepcopy(doc if isinstance(doc, dict) else {})
    for row in _rows(out.get("clients")):
        if isinstance(row, dict) and _text(row.get("id")).strip() == _text(client_id).strip():
            tag = _language(language)
            if tag:
                row["default_language"] = tag
            else:
                row.pop("default_language", None)
            break
    return out


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
