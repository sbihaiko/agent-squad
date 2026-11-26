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
            return {"err": roster_err, "groups": [], **fields}
    if clients is None:
        clients, map_err = _clients_map_read()
        if map_err:
            errs.append(map_err)
    # The route is unauthenticated: the page gets a stable message, and the exception with its paths stays in the log.
    try:
        PS = personas_module()
    except Exception as e:
        print(f"hub: the persona module did not resolve: {type(e).__name__}: {e}", file=sys.stderr)
        return {"err": "the persona rules are unavailable", "groups": [], **fields}
    try:
        groups = PS.groups(seats, clients)
    except Exception as e:
        print(f"hub: the personas could not be merged: {type(e).__name__}: {e}", file=sys.stderr)
        return {"err": "the personas could not be merged", "groups": [], **fields}
    return {"err": "; ".join(errs) or None, "groups": groups, **fields}


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
    clients, merr = _clients_map_read()   # a rename names the client map, and a confirm writes it (#3012)
    try:
        plan = PS.edit(seats, pid, fields, targets, clients=clients)
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
    notes = list(plan.get("notes") or ())
    if merr:
        notes.append(f"the client map did not read: {merr}")
    # No `err` key on a success: the write routes answer 400 on a body that carries one (#3011).
    return {"persona": pid, "rows": rows, "confirm": confirm, "saved": saved, "partial": bool(refused),
            "notes": notes, "map": list(plan.get("map") or ())}
