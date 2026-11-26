# One hub theme (#2742). hub.py compiles this file into its own module namespace, at its place in the hub's one
# namespace, so every name the hub defined before it -- and every name a later theme defines -- is a plain global.
#
# --- Settings > Personas, the read and the write route (SPEC 3009, #3010, #3011, #3013, #3015). The route reads one
# seat for each roster project and the profile's local client map, and the persona rules module merges them (ADR-0137,
# ADR-0177). The page draws what the route answers. One edit (#3011), one add (#3013), and one chosen copy of a drifted
# row (#3015) write through the same route: the preview writes nothing, and the confirm writes, commits and lands each
# target project from its own worktree.

def personas_module():
    """The one home of the persona rules: the hub holds no copy of a rule."""
    roots = squad_roots(SQUAD_ROOT, os.environ.get("SG_CHECKOUT"), CHECKOUT, __file__)
    (PS,) = load_squad(roots, ("personas",))
    return PS


def personas_fields():
    """The choice a voice field offers and the rules a name and a tone field state: the Gemini voices alone, sorted, the
    policy module's tone limit, and the policy module's name pattern. The list, the limit and the pattern live in the
    policy module, so the hub states no rule of its own (ADR-0177). A policy read that fails answers no choice and no
    rule, and the page draws a plain field."""
    try:
        P = slack_policy()
        return sorted(P.GEMINI_VOICES), P.TONE_MAX, P.NAME.pattern
    except Exception as e:
        print(f"hub: the Slack rules module did not resolve: {type(e).__name__}: {e}", file=sys.stderr)
        return [], None, None


def _pr_newest_committed(seat):
    """The seat with the committed document the theme reads: the newer of the project's checkout file and the document
    the hub's own worktree holds committed on the landing branch for that project.

    The hub writes and lands a persona save from its own worktree, and the project's checkout follows the branch on its
    own schedule. So a read that took the checkout alone would keep drawing a drift the save already settled (#3015
    acceptance 5). The checkout wins once it is at least as new, so a later pull or a hand edit answers the theme.

    The hub's document is read from `origin/<land>` alone, never from the worktree's working file: a refused or unlanded
    save leaves the working file ahead of the branch, so the working file answers nothing. A worktree the hub holds no
    file in leaves the seat as it stands, and every failure keeps the checkout's document."""
    try:
        checkout_path = pathlib.Path(_slack_text(seat.get("committed_path")))
        wt, why = slack_worktree(loop=seat.get("checkout"), name=seat.get("id"))
        if not wt or why:
            return seat
        hub_path = pathlib.Path(wt) / SLACK_YAML_REL
        if not hub_path.is_file() or hub_path.stat().st_mtime <= checkout_path.stat().st_mtime:
            return seat
        out = _slack_git(wt, "show", f"refs/remotes/origin/{LAND}:{SLACK_YAML_REL}")
        if out.returncode:
            return seat
        try:
            doc = yaml.safe_load(out.stdout)
        except yaml.YAMLError:
            return seat
        if not isinstance(doc, dict) or not doc:
            return seat
        return {**seat, "committed": doc, "committed_path": hub_path, "cf": _slack_name(hub_path, "slack.yaml")}
    except (OSError, TypeError, ValueError):
        return seat


def personas_overview(seats=None, clients=None):
    """The groups the Personas theme draws: one group for each client of the map, then one for each project no client
    names, each with one row for each persona id its projects declare. A caller may hand the seats and the client map
    in; the route reads them from the roster and the profile's map.

    A roster read that fails answers its reason and no group, so a broken profile read never draws an empty roster as a
    roster with no persona. A client map that fails to read names no client, and the projects wait in their own
    groups. The answer carries the Gemini voices and the tone limit the theme's editor offers."""
    voices, tone_max, name_pattern = personas_fields()
    fields = {"voices": voices, "tone_max": tone_max, "name_pattern": name_pattern}
    errs = []
    if seats is None:
        seats, roster_err = _slack_roster_checked(0.0)
        if roster_err:
            return {"err": roster_err, "groups": [], **fields, "projects": []}
    seats = [_pr_newest_committed(seat) for seat in seats]
    projects = _pr_targets(seats or ())
    if clients is None:
        clients, map_err = _clients_map_read()
        if map_err:
            errs.append(map_err)
    # The route is unauthenticated: the page gets a stable message, and the exception with its paths stays in the log.
    try:
        PS = personas_module()
    except Exception as e:
        print(f"hub: the persona module did not resolve: {type(e).__name__}: {e}", file=sys.stderr)
        return {"err": "the persona rules are unavailable", "groups": [], **fields, "projects": projects}
    try:
        groups = PS.groups(seats, clients)
    except Exception as e:
        print(f"hub: the personas could not be merged: {type(e).__name__}: {e}", file=sys.stderr)
        return {"err": "the personas could not be merged", "groups": [], **fields, "projects": projects}
    return {"err": "; ".join(errs) or None, "groups": groups, **fields, "projects": projects}


def _pr_targets(seats):
    """Every roster project as a target the add offers: its id, its label and its committed file. The add's project
    checkboxes and the existing row's Add to project list read this one list (#3013)."""
    out, seen = [], set()
    for seat in seats:
        sid = _slack_text(seat.get("id")).strip()
        if not sid or sid in seen:
            continue
        seen.add(sid)
        out.append({"id": sid, "label": _slack_text(seat.get("label")) or sid,
                    "file": _slack_text(seat.get("committed_path"))})
    return out


def _pr_edited_fields(P, fields):
    """(fields, error): the name, voice and tone a body names, the voice resolved to its registered spelling. A voice
    outside the Gemini list and a tone over the limit are refused here, with the limit in the message, before any
    project is read."""
    out = {}
    for key in ("name", "voice", "tone"):
        if key in fields:
            out[key] = _slack_text(fields.get(key)).strip()
    if not out:
        return None, "fields: a body that names at least one of name, voice and tone"
    if "name" in out:
        reason = P.name_reason(out["name"])
        if reason:
            return None, f"personas[].name: {reason}"
    if "voice" in out:
        voice = next((v for v in P.GEMINI_VOICES if v.casefold() == out["voice"].casefold()), None)
        if voice is None:
            return None, f"personas[].voice: voice {out['voice']!r} is not a Gemini voice"
        out["voice"] = voice
    if "tone" in out and len(out["tone"]) > P.TONE_MAX:
        return None, f"personas[].tone: a tone holds at most {P.TONE_MAX} characters ({len(out['tone'])} given)"
    return out, None


def _pr_save_result(out, name):
    """The row result of one project's save path answer: the landed commit, or the reason the save was refused. A commit
    the push left behind is named with its sha and the push's own text, so an owner can land it by hand."""
    err = out.get("err")
    if not err:
        sha = out.get("sha")
        return (f"wrote and pushed commit {sha}" if sha else
                f"nothing to write: this file already holds {name}")
    hit = re.search(r"left commit ([0-9a-f]{7,40})", str(err))
    if hit:
        return f"wrote commit {hit[1]} but not pushed: {err}"
    return f"refused: {err}"


def _pr_chosen_copy(P, personas, seats, pid, body):
    """(fields, targets, error): the fields of the copy the body chose and the projects that client's save writes into.

    A body that names `client` and `copy` picks one copy of a drifted row: the rules module answers the chosen copy's
    fields and the projects of that client alone that declare the id, so a project of another client is never a target
    (#3015). Any other body names the fields and the target projects itself (#3011)."""
    client_id = _slack_text(body.get("client")).strip()
    copy_project = _slack_text(body.get("copy")).strip()
    if not (client_id or copy_project):
        raw_fields = body.get("fields")
        if not isinstance(raw_fields, dict):
            return None, None, "fields: a mapping of name, voice and tone"
        targets = body.get("projects")
        if not isinstance(targets, list) or not targets:
            return None, None, "projects: a body that names at least one project"
        fields, ferr = _pr_edited_fields(P, raw_fields)
        return (None, None, ferr) if ferr else (fields, targets, None)
    if not (client_id and copy_project):
        return None, None, "copy: a body that names the client and the project whose copy to use"
    try:
        clients, merr = _clients_map_read()
    except Exception as e:
        return None, None, f"the client map did not resolve: {type(e).__name__}: {e}"
    if merr:
        return None, None, merr
    try:
        pick = personas.settle(seats, clients, pid, client_id, copy_project)
    except Exception as e:
        print(f"hub: the persona copy could not be chosen: {type(e).__name__}: {e}", file=sys.stderr)
        return None, None, "the persona copy could not be chosen"
    if pick.get("err"):
        return None, None, pick["err"]
    fields, ferr = _pr_edited_fields(P, pick["fields"])
    return (None, None, ferr) if ferr else (fields, pick["targets"], None)


def _pr_apply(seats, plan, confirm, name):
    """(rows, saved): each planned row with the save path's own answer. A row with a write calls that project's save
    path from the project's own worktree, with no confirm for the preview and with it for the save; one refused project
    never stops the others. `name` is the persona's name, so a refusal or a written file names it."""
    by_id = {s["id"]: s for s in seats}
    writes = {w["project"]: w for w in plan["writes"]}
    rows, saved = [], []
    for row in plan["rows"]:
        write = writes.get(row["project"])
        if write is None:
            rows.append(row)
            continue
        seat = by_id[row["project"]]
        payload = {"committed": write["committed"], "local": _slack_read_doc(seat["local_path"])}
        if confirm:
            payload["confirm"] = True
        out = slack_files_set(payload, committed=seat["committed_path"], local=seat["local_path"],
                              loop=seat["checkout"], name=seat["id"])
        files = out.get("files") or {}
        result = (_pr_save_result(out, name) if confirm
                  else (row["result"] if not out.get("err") else f"refused: {out['err']}"))
        rows.append({**row, "file": files.get("committed") or row["file"], "result": result,
                     "diff": out.get("diff")})
        if confirm and not out.get("err"):
            saved.append({"project": row["project"], "sha": out.get("sha")})
    return rows, saved


def personas_edit(body):
    """POST /api/personas: one persona's edit over the target projects the body names, or one chosen copy of a drifted
    row over that client's projects alone (#3015). Without `confirm` it is the preview: the hub builds each project's
    document through the rules module and calls that project's save path with no confirm, so the answer names each file
    and the diff, and writes nothing. On confirm each project is written, committed and landed from its own worktree,
    and one refused project never stops the others."""
    if not isinstance(body, dict):
        return {"err": "bad request"}
    pid = _slack_text(body.get("persona")).strip()
    if not pid:
        return {"err": "persona: a body that names one persona id"}
    try:
        P = slack_policy()
    except Exception as e:
        return {"err": f"the Slack rules module did not resolve: {type(e).__name__}: {e}"}
    seats, rerr = _slack_roster_checked(0.0)   # a save always reads the roster fresh, never a cached one
    if rerr:
        return {"err": rerr}
    try:
        PS = personas_module()
    except Exception as e:
        print(f"hub: the persona module did not resolve: {type(e).__name__}: {e}", file=sys.stderr)
        return {"err": "the persona rules are unavailable"}
    fields, targets, perr = _pr_chosen_copy(P, PS, seats, pid, body)
    if perr:
        return {"err": perr}
    clients, merr = _clients_map_read()   # a rename names the client map, and a confirm writes it (#3012)
    try:
        plan = PS.edit(seats, pid, fields, targets, clients=clients)
    except Exception as e:
        print(f"hub: the persona edit could not be planned: {type(e).__name__}: {e}", file=sys.stderr)
        return {"err": "the persona edit could not be planned"}
    confirm = body.get("confirm") is True
    rows, saved = _pr_apply(seats, plan, confirm, _slack_text(fields.get("name")).strip() or pid)
    map_rows = list(plan.get("map") or ())
    if confirm:
        _slack_roster_clear()   # a write changed a project's files, so the next read reads the roster fresh
        # A rename stores the new name in each client default that holds the persona (#3012), so the client badge and
        # tile never show the old name. The map is its own store: it writes whether or not a project file was saved,
        # and only a refused map write leaves its row unwritten.
        new_name = _slack_text(fields.get("name")).strip()
        written = []
        for row in map_rows:
            werr = _clients_default_write(row["client"], pid, new_name)
            written.append({**row, "result": f"refused: {werr}" if werr else
                                             f"stored {new_name} as the default name"})
        map_rows = written
    refused = [r for r in rows if r["result"].startswith("refused:")]
    refused += [r for r in map_rows if r["result"].startswith("refused:")]
    notes = list(plan.get("notes") or ())
    if merr:
        notes.append(f"the client map did not read: {merr}")
    # No `err` key on a success: the write routes answer 400 on a body that carries one (#3011).
    return {"persona": pid, "rows": rows, "confirm": confirm, "saved": saved, "partial": bool(refused),
            "notes": notes, "map": map_rows}

def _pr_project_id(entry):
    """The project id one entry of a client's `projects` names: its `id` for a mapping, and the text itself for a bare
    id, so a raw map and the normalized map answer the same holder (#3014)."""
    return _slack_text(entry.get("id") if isinstance(entry, dict) else entry).strip()


def _pr_holding_clients(clients, project_id):
    """One `(client id, default persona id)` for each client of the map that holds the project, so a client of another
    project never answers a holder and never blocks the removal (#3014)."""
    out = []
    for client in clients or ():
        held = any(_pr_project_id(entry) == project_id
                   for entry in client.get("projects") or ())
        default = _slack_text((client.get("default_persona") or {}).get("id")).strip()
        if held and default:
            out.append((client["id"], default))
    return out


def personas_remove(body):
    """POST /api/personas/remove: one persona removed from the target projects the body names. Without `confirm` it is
    the preview: the hub asks the policy module for each project's holders of the persona, builds the committed
    document without its row through the rules module, and calls that project's save path with no confirm, so the
    answer names each file and the diff, and writes nothing. A project whose files or clients name the persona is
    refused, and the refusal names every holder. On confirm each project is written, committed and landed from its own
    worktree, and one refused project never stops the others."""
    if not isinstance(body, dict):
        return {"err": "bad request"}
    pid = _slack_text(body.get("persona")).strip()
    if not pid:
        return {"err": "persona: a body that names one persona id"}
    targets = body.get("projects")
    if not isinstance(targets, list) or not targets:
        return {"err": "projects: a body that names at least one project"}
    # The contract: `confirm` is the boolean true or absent. Any other value is refused here, so a preview never
    # answers as a success for a caller that meant to confirm with 1 or the text `true`.
    if "confirm" in body and not isinstance(body["confirm"], bool):
        return {"err": "confirm: true or absent"}
    try:
        P = slack_policy()
    except Exception as e:
        return {"err": f"the Slack rules module did not resolve: {type(e).__name__}: {e}"}
    seats, rerr = _slack_roster_checked(0.0)   # a save always reads the roster fresh, never a cached one
    if rerr:
        return {"err": rerr}
    clients = _clients_map_read()[0]
    try:
        PS = personas_module()
    except Exception as e:
        print(f"hub: the persona module did not resolve: {type(e).__name__}: {e}", file=sys.stderr)
        return {"err": "the persona rules are unavailable"}
    by_id = {s["id"]: s for s in seats}
    holders = {}
    for target in targets:
        seat = by_id.get(_slack_text(target).strip())
        if seat is None or seat.get("read_defects"):
            # A seat whose file did not read answers no holder: the rules module reports the file defect first, so the
            # hub never asks the policy module about a document that does not read.
            continue
        holders[seat["id"]] = P.persona_holders(
            pid, seat.get("committed"), seat.get("local"),
            clients=_pr_holding_clients(clients, seat["id"]),
            files=(seat.get("cf") or "slack.yaml", seat.get("lf") or "slack.local.yaml"))
    try:
        plan = PS.remove(seats, pid, targets, holders)
    except Exception as e:
        print(f"hub: the persona removal could not be planned: {type(e).__name__}: {e}", file=sys.stderr)
        return {"err": "the persona removal could not be planned"}
    writes = {w["project"]: w for w in plan["writes"]}
    confirm = body.get("confirm") is True
    rows, saved = [], []
    for row in plan["rows"]:
        write = writes.get(row["project"])
        if write is None:
            rows.append(row)
            continue
        seat = by_id[row["project"]]
        name = row["result"][len("removes "):] if row["result"].startswith("removes ") else pid
        payload = {"committed": write["committed"], "local": _slack_read_doc(seat["local_path"])}
        if confirm:
            payload["confirm"] = True
        out = slack_files_set(payload, committed=seat["committed_path"], local=seat["local_path"],
                              loop=seat["checkout"], name=seat["id"])
        files = out.get("files") or {}
        result = (_pr_save_result(out, name) if confirm
                  else (row["result"] if not out.get("err") else f"refused: {out['err']}"))
        rows.append({**row, "file": files.get("committed") or row["file"], "result": result,
                     "diff": out.get("diff")})
        if confirm and not out.get("err"):
            saved.append({"project": row["project"], "sha": out.get("sha")})
    if confirm:
        _slack_roster_clear()   # a write changed a project's files, so the next read reads the roster fresh
    refused = [r for r in rows if r["result"].startswith("refused:")]
    # No `err` key on a success: the write routes answer 400 on a body that carries one (#3011).
    return {"persona": pid, "rows": rows, "confirm": confirm, "saved": saved, "partial": bool(refused)}


def _pr_add_fields(P, raw, PS):
    """(persona, error): the id, name, voice and tone of the persona an add names. The id is the one given, else the slug
    of the name. A body that names no name, a voice outside the Gemini list and a tone over the limit are refused here,
    with the limit in the message, before any project is read (#3013)."""
    name = _slack_text(raw.get("name")).strip()
    if not name:
        return None, "persona.name: a body that names the persona's name"
    pid = _slack_text(raw.get("id")).strip() or PS.persona_id(name)
    if not pid:
        return None, "persona.name: a name that derives an id"
    out = {"id": pid, "name": name}
    voice = _slack_text(raw.get("voice")).strip()
    if not voice:
        return None, "persona.voice: a body that names the persona's voice"
    hit = next((v for v in P.GEMINI_VOICES if v.casefold() == voice.casefold()), None)
    if hit is None:
        return None, f"persona.voice: voice {voice!r} is not a Gemini voice"
    out["voice"] = hit
    tone = _slack_text(raw.get("tone")).strip()
    if len(tone) > P.TONE_MAX:
        return None, f"persona.tone: a tone holds at most {P.TONE_MAX} characters ({len(tone)} given)"
    out["tone"] = tone
    return out, None


def personas_add(body):
    """POST /api/personas with `op: add`: one new persona row, or one existing persona, written into each target
    project's committed file (#3013). Without `confirm` it is the preview: the hub builds each project's document
    through the rules module and calls that project's save path with no confirm, so the answer names each file and the
    diff, and writes nothing. On confirm each project is written, committed and landed from its own worktree, and one
    refused project never stops the others."""
    if not isinstance(body, dict):
        return {"err": "bad request"}
    raw = body.get("persona")
    if not isinstance(raw, dict):
        return {"err": "persona: a mapping of name, voice and tone"}
    targets = body.get("projects")
    if not isinstance(targets, list) or not targets:
        return {"err": "projects: a body that names at least one project"}
    try:
        P = slack_policy()
    except Exception as e:
        return {"err": f"the Slack rules module did not resolve: {type(e).__name__}: {e}"}
    try:
        PS = personas_module()
    except Exception as e:
        print(f"hub: the persona module did not resolve: {type(e).__name__}: {e}", file=sys.stderr)
        return {"err": "the persona rules are unavailable"}
    persona, ferr = _pr_add_fields(P, raw, PS)
    if ferr:
        return {"err": ferr}
    seats, rerr = _slack_roster_checked(0.0)   # a save always reads the roster fresh, never a cached one
    if rerr:
        return {"err": rerr}
    try:
        plan = PS.add(seats, persona, targets)
    except Exception as e:
        print(f"hub: the persona add could not be planned: {type(e).__name__}: {e}", file=sys.stderr)
        return {"err": "the persona add could not be planned"}
    confirm = body.get("confirm") is True
    rows, saved = _pr_apply(seats, plan, confirm, persona["name"])
    if confirm:
        _slack_roster_clear()   # a write changed a project's files, so the next read reads the roster fresh
    refused = [r for r in rows if r["result"].startswith("refused:")]
    return {"persona": persona["id"], "name": persona["name"], "rows": rows, "confirm": confirm,
            "saved": saved, "partial": bool(refused)}


def personas_post(body):
    """POST /api/personas: a body that carries `op: add` is the add (#3013); any other body is the edit (#3011). The
    page's preview and its confirmed save reach both through this one route, so the write routes stay one list."""
    return personas_add(body) if isinstance(body, dict) and body.get("op") == "add" else personas_edit(body)
