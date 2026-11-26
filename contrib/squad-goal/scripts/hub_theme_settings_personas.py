# One hub theme (#2742). hub.py compiles this file into its own module namespace, at its place in the hub's one
# namespace, so every name the hub defined before it -- and every name a later theme defines -- is a plain global.
#
# --- Settings > Personas, the read route (SPEC 3009, #3010). The route reads one seat for each roster project and the
# profile's local client map, and the persona rules module merges them (ADR-0137, ADR-0177). The page draws what the
# route answers. Nothing here is editable yet.

def personas_module():
    """The one home of the persona rules: the hub holds no copy of a rule."""
    roots = squad_roots(SQUAD_ROOT, os.environ.get("SG_CHECKOUT"), CHECKOUT, __file__)
    (PS,) = load_squad(roots, ("personas",))
    return PS


def personas_fields():
    """The choice a voice field offers and the limit a tone field states: the Gemini voices alone, sorted, and the policy
    module's tone limit. The list and the limit live in the policy module, so the hub states no rule of its own
    (ADR-0177). A policy read that fails answers no choice and no limit, and the page draws a plain field."""
    try:
        P = slack_policy()
        return sorted(P.GEMINI_VOICES), P.TONE_MAX
    except Exception as e:
        print(f"hub: the Slack rules module did not resolve: {type(e).__name__}: {e}", file=sys.stderr)
        return [], None


def personas_overview(seats=None, clients=None):
    """The groups the Personas theme draws: one group for each client of the map, then one for each project no client
    names, each with one row for each persona id its projects declare. A caller may hand the seats and the client map
    in; the route reads them from the roster and the profile's map.

    A roster read that fails answers its reason and no group, so a broken profile read never draws an empty roster as a
    roster with no persona. A client map that fails to read names no client, and the projects wait in their own
    groups. The answer carries the Gemini voices and the tone limit the theme's editor offers."""
    voices, tone_max = personas_fields()
    errs = []
    if seats is None:
        seats, roster_err = _slack_roster_checked(0.0)
        if roster_err:
            return {"err": roster_err, "groups": [], "voices": voices, "tone_max": tone_max}
    if clients is None:
        clients, map_err = _clients_map_read()
        if map_err:
            errs.append(map_err)
    # The route is unauthenticated: the page gets a stable message, and the exception with its paths stays in the log.
    try:
        PS = personas_module()
    except Exception as e:
        print(f"hub: the persona module did not resolve: {type(e).__name__}: {e}", file=sys.stderr)
        return {"err": "the persona rules are unavailable", "groups": [], "voices": voices, "tone_max": tone_max}
    try:
        groups = PS.groups(seats, clients)
    except Exception as e:
        print(f"hub: the personas could not be merged: {type(e).__name__}: {e}", file=sys.stderr)
        return {"err": "the personas could not be merged", "groups": [], "voices": voices, "tone_max": tone_max}
    return {"err": "; ".join(errs) or None, "groups": groups, "voices": voices, "tone_max": tone_max}


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


def personas_edit(body):
    """POST /api/personas: one persona's edit over the target projects the body names. Without `confirm` it is the
    preview: the hub builds each project's document through the rules module and calls that project's save path with no
    confirm, so the answer names each file and the diff, and writes nothing. On confirm each project is written,
    committed and landed from its own worktree, and one refused project never stops the others."""
    if not isinstance(body, dict):
        return {"err": "bad request"}
    pid = _slack_text(body.get("persona")).strip()
    if not pid:
        return {"err": "persona: a body that names one persona id"}
    raw_fields = body.get("fields")
    if not isinstance(raw_fields, dict):
        return {"err": "fields: a mapping of name, voice and tone"}
    targets = body.get("projects")
    if not isinstance(targets, list) or not targets:
        return {"err": "projects: a body that names at least one project"}
    try:
        P = slack_policy()
    except Exception as e:
        return {"err": f"the Slack rules module did not resolve: {type(e).__name__}: {e}"}
    fields, ferr = _pr_edited_fields(P, raw_fields)
    if ferr:
        return {"err": ferr}
    seats, rerr = _slack_roster_checked(0.0)   # a save always reads the roster fresh, never a cached one
    if rerr:
        return {"err": rerr}
    try:
        PS = personas_module()
    except Exception as e:
        print(f"hub: the persona module did not resolve: {type(e).__name__}: {e}", file=sys.stderr)
        return {"err": "the persona rules are unavailable"}
    try:
        plan = PS.edit(seats, pid, fields, targets)
    except Exception as e:
        print(f"hub: the persona edit could not be planned: {type(e).__name__}: {e}", file=sys.stderr)
        return {"err": "the persona edit could not be planned"}
    by_id = {s["id"]: s for s in seats}
    writes = {w["project"]: w for w in plan["writes"]}
    confirm = body.get("confirm") is True
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
        result = (_pr_save_result(out, _slack_text(fields.get("name")).strip() or pid) if confirm
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
