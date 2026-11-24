# One hub theme (#2742). hub.py compiles this file into its own module namespace, at its place in the hub's one
# namespace, so every name the hub defined before it -- and every name a later theme defines -- is a plain global.
# --- Settings > Communication, the read view (SPEC 2682 #2693). The theme reads the committed `slack.yaml` at the checkout
# root and the profile's `slack.local.yaml`, and draws the Now tiles and the sections Personas, Roles, Slack workspaces and
# Project events. Every resolved value comes from the policy module (#2684), and every field names the file it comes from;
# the hub holds no copy of a rule (ADR-0177). The view constructs and binds: it reads no file and opens no client (ADR-0137).
SLACK_YAML_REL = pathlib.Path("docs") / "squad" / "slack.yaml"

def slack_policy():
    """The one home of the Slack file rules (ADR-0177): the hub holds no copy of a rule."""
    (SP,) = load_squad(squad_roots(SQUAD_ROOT, os.environ.get("SG_CHECKOUT"), CHECKOUT, __file__), ("slackpolicy",))
    return SP

def slack_yaml_paths():
    """The committed `slack.yaml` at the checkout root and this profile's local file (SPEC 2682). A profile that names no
    checkout reads no committed file, and one that names no project reads no local file."""
    root = pathlib.Path(CHECKOUT) if CHECKOUT else None
    d = profiles_dir()
    project = os.environ.get("SG_PROJECT") or PROFILE_NAME or (root.name if root else "")
    local = slack_policy().local_path(str(d), project) if d is not None and project else None
    return (root / SLACK_YAML_REL if root else None), (pathlib.Path(local) if local else None)

def _slack_name(path, fallback):
    return pathlib.Path(path).name if path else fallback

def _slack_text(value):
    return "" if value is None else str(value)

def _slack_dicts(value):
    """The mappings of a list with the position each one holds in its own document, so the editor addresses a row by
    the place it holds in the file it writes (#2698), and a malformed entry never shifts another row's address."""
    return [(i, row) for i, row in enumerate(value or []) if isinstance(row, dict)] if isinstance(value, list) else []

def _slack_counts(n, word):
    return f"{n} {word}" if n == 1 else f"{n} {word}s"

def _slack_overview_shape(project, roles, events, personas, workspaces, defects, committed, local):
    """The one shape of the read view: the Now tiles, the project row and the four sections. A read that resolves
    nothing builds the same shape from empty lists, so a broken rule never costs the reader the Now tiles or the
    headings. `defects` carries the reasons, and the page states them in a banner above the sections."""
    cf, lf = _slack_name(committed, "slack.yaml"), _slack_name(local, "slack.local.yaml")
    project = dict(project, file=cf)
    owned = sum(1 for w in workspaces if w["class"] == "owned")
    guest = len(workspaces) - owned
    speak = sum(1 for w in workspaces for c in w["channels"] if c["mode"] == "listen+speak")
    subscribers = sum(1 for w in workspaces for c in w["channels"] if c["announce"])
    tone = lambda n: "ok" if n else "warn"   # noqa: E731  a count of zero needs a look
    now = [{"key": "Workspaces", "value": f"{len(workspaces)} · {owned} owned · {guest} guest", "tone": tone(len(workspaces))},
           {"key": "Channels", "value": f"{sum(len(w['channels']) for w in workspaces)} · {speak} speak",
            "tone": tone(speak)},
           {"key": "Personas", "value": f"{len(personas)} · default {project['persona_name']}"
                                        if project["persona_name"] else f"{len(personas)} · no default",
            "tone": tone(len(personas))},
           {"key": "Events", "value": f"{len(events)} · {_slack_counts(subscribers, 'channel')} subscribe",
            "tone": tone(len(events))},
           {"key": "Files", "value": f"{cf} · {lf}", "tone": "neutral"}]
    return {"err": None, "files": {"committed": cf, "local": lf}, "project": project, "now": now,
            "personas": personas, "roles": roles, "workspaces": workspaces, "events": events,
            "role_actions": [], "defects": [str(d) for d in defects], "voices": [], "previews": []}

def _slack_overview_empty(err, committed=None, local=None):
    """The read view of a theme that can read nothing: the same Now tiles and four empty sections, with the reason in
    the defects banner. `err` carries the message, and it is a message: it never replaces the page."""
    project = {"language": "en-US", "persona": None, "persona_name": None}
    return dict(_slack_overview_shape(project, [], [], [], [], [err] if err else [], committed, local), err=err,
                docs={"committed": {}, "local": {}})

def _slack_reply_previews(P, committed, local):
    """One row per effective channel: the four facts of its reply, resolved through the policy module. The local file
    fills a channel the commit does not name, and a channel both files name is drawn once, the committed row winning as
    it does at run time. The mark is the policy rule, and a channel with no persona carries empty fields, never another
    persona's name."""
    committed, local = committed or {}, local or {}
    order, groups = [], {}
    for di, doc in enumerate((committed, local)):
        for wi, ws in enumerate(P.rows(doc.get("workspaces"))):
            wid = _slack_text(ws.get("id")) or f"\0{di}:{wi}"
            if wid not in groups:
                order.append(wid)
                groups[wid] = {"ws": ws, "rows": []}   # the first document's workspace row: the commit when both name it
            groups[wid]["rows"].extend((di, ci, ch) for ci, ch in enumerate(P.rows(ws.get("channels"))))
    out = []
    for wid in order:
        ws = groups[wid]["ws"]
        label = _slack_text(ws.get("label") or ws.get("id"))
        seen = set()
        for di, ci, ch in groups[wid]["rows"]:
            cid = _slack_text(ch.get("id")) or f"\0{di}:{ci}"   # an id-less channel keys on its document and index
            if cid in seen:
                continue
            seen.add(cid)
            language, persona = P.effective(committed, local, ws, ch)
            name = _slack_text(ch.get("name") or ch.get("id"))
            out.append({"channel": f"{label}/#{name}", "mark": P.mark(persona), "language": language,
                        "tone": persona.tone if persona else "", "voice": persona.voice if persona else ""})
    return out

def slack_preview(committed=None, local=None):
    """The reply preview of the documents the view hands in, before the save: one row per effective channel with the
    mark, the language, the tone and the voice. Either document may be absent, so a draft that carries only the local
    overrides still previews; a value that is neither a mapping nor None is refused. The policy module resolves the
    rows; the hub holds no copy of a rule (ADR-0177)."""
    committed = {} if committed is None else committed
    local = {} if local is None else local
    if not isinstance(committed, dict) or not isinstance(local, dict):
        return {"err": "committed and local: each one a mapping or none", "previews": []}
    try: P = slack_policy()
    except Exception as e:
        return {"err": f"the Slack rules module did not resolve: {type(e).__name__}: {e}", "previews": []}
    return {"err": None, "previews": _slack_reply_previews(P, committed, local)}

# --- Settings > Communication, the persona sample (SPEC 2682 #2697). The sample button asks the hub for one voice and
# tone pair; the hub synthesizes once through the vendored TTS script, keeps the WAV in its state directory, and answers
# the same bytes from the cache on a second request. The policy module owns the voice list and the tone limit. The
# client is one seam, so a test hands in a fake Gemini client and no request reaches Gemini.
SLACK_SAMPLES = "slack-samples"   # in the loop state directory, outside every repository
SLACK_SAMPLE_TEXT = "This is how the persona sounds. Short sentences, no filler."
SLACK_SAMPLE_TIMEOUT = 90
SLACK_SAMPLE_LOCK_STALE = SLACK_SAMPLE_TIMEOUT + 30   # a lock older than this belongs to a killed request

def slack_samples_dir():
    """The sample cache: one folder in the loop state directory, outside every repository."""
    return pathlib.Path(STATE) / SLACK_SAMPLES

def _slack_normal_pair(voice, tone):
    """One voice and tone pair, normalized: a trailing space and a different case spell the same sound, so equivalent
    text is one pair, one cache key and one paid call. The voice is stripped; the caller resolves its registered
    spelling against the policy list."""
    return str(voice or "").strip(), str(tone or "").strip().casefold()

def slack_sample_key(voice, tone):
    """The cache key of one voice and tone pair: a hash of the normalized pair and the sample text, so a new script is
    a new key and the file name is stable and carries no tone text."""
    v, t = _slack_normal_pair(voice, tone)
    return hashlib.sha256(f"{v}\0{t}\0{SLACK_SAMPLE_TEXT}".encode()).hexdigest()[:32]

def _slack_wav_ok(path):
    """A cache hit needs a whole WAV: the RIFF/WAVE header and more than its twelve bytes. A cut file left by a killed
    synthesis is no hit, so the next request makes the sample again."""
    try:
        with open(path, "rb") as f:
            head = f.read(12)
        return head[:4] == b"RIFF" and head[8:12] == b"WAVE" and pathlib.Path(path).stat().st_size > 12
    except OSError:
        return False

def _slack_lock(path):
    """One exclusive lock file per key: the request that creates it synthesizes, and the rest wait for its WAV. A lock
    older than a synthesis plus a margin is stale, so a killed request never wedges its pair."""
    for _ in range(2):
        try:
            return os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            try:
                if time.time() - pathlib.Path(path).stat().st_mtime > SLACK_SAMPLE_LOCK_STALE:
                    pathlib.Path(path).unlink(); continue
            except OSError:
                pass
            return None
        except OSError:
            return None
    return None

def _slack_await_wav(path, seconds):
    """Wait for another request's synthesis to land its WAV, and answer whether the file is now whole."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if _slack_wav_ok(path): return True
        time.sleep(0.05)
    return _slack_wav_ok(path)

def _slack_tts_cli(voice, tone, out):
    """The real client: the vendored TTS script, found by name (ADR-0129), in its own process, so no key and no module
    state enters the hub. Returns (path, error)."""
    roots = squad_roots(SQUAD_ROOT, os.environ.get("SG_CHECKOUT"), CHECKOUT, __file__)
    try: (SKP,) = load_squad(roots, ("skillpath",))
    except Exception as e: return None, f"the skill path did not resolve: {type(e).__name__}: {e}"
    script = SKP.voice_script("tts.py")
    if script is None: return None, "the TTS script did not resolve"
    # One `--tone=<value>` element: a tone that begins with a dash (a bullet is normal typing) is then a value the
    # script's option loop reads, never a flag it eats or refuses.
    cmd = [sys.executable, str(script), SLACK_SAMPLE_TEXT, "--voice", voice, f"--tone={tone}", "--out", str(out)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=SLACK_SAMPLE_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as e:
        return None, f"{type(e).__name__}: {e}"
    if r.returncode or not pathlib.Path(out).is_file():
        return None, (r.stderr or r.stdout or "the script made no WAV").strip()[:200]
    return str(out), None

def slack_sample(body, client=None):
    """POST /api/slack/sample: the WAV of one voice and tone pair, made once and kept in the cache. A body that names a
    voice outside the policy list, or a tone over its limit, is refused with a message that names the key and calls
    nothing. One lock per pair makes exactly one synthesis under concurrent presses, the WAV lands whole by rename, and
    a cut file is no hit. The answer carries the audio as a data URL and states whether it came from the cache."""
    if not isinstance(body, dict): return {"err": "bad request"}
    raw_voice = str(body.get("voice") or "")
    voice, tone = _slack_normal_pair(raw_voice, body.get("tone"))
    try: P = slack_policy()
    except Exception as e:
        return {"err": f"the Slack rules module did not resolve: {type(e).__name__}: {e}"}
    voice = next((v for v in P.GEMINI_VOICES if v.casefold() == voice.casefold()), None)
    if voice is None: return {"err": f"personas[].voice: voice {raw_voice!r} is not a Gemini voice"}
    if len(tone) > P.TONE_MAX: return {"err": f"personas[].tone: a tone holds at most {P.TONE_MAX} characters ({len(tone)} given)"}
    key = slack_sample_key(voice, tone)
    d = slack_samples_dir()
    path = d / f"{key}.wav"
    cached = _slack_wav_ok(path)
    if not cached:
        try: d.mkdir(parents=True, exist_ok=True)
        except OSError as e: return {"err": f"the sample cache could not be made: {e.strerror or e}"}
        lock = d / f"{key}.lock"
        fd = _slack_lock(lock)
        if fd is None:
            cached = _slack_await_wav(path, SLACK_SAMPLE_TIMEOUT)   # another request is making it: never call twice
            if not cached: return {"err": "the sample is being made by another request; try again"}
        else:
            try:
                cached = _slack_wav_ok(path)   # the request that held the lock before may have finished it
                if not cached:
                    tmp = d / f".{key}.{os.getpid()}.{time.monotonic_ns()}.tmp.wav"
                    got, err = (client or _slack_tts_cli)(voice, tone, tmp)
                    if err or not got or not _slack_wav_ok(tmp):
                        try: tmp.unlink()
                        except OSError: pass
                        return {"err": f"the sample could not be made: {err or 'no WAV'}"}
                    try: os.replace(tmp, path)   # the WAV lands whole or not at all
                    except OSError as e:
                        try: tmp.unlink()
                        except OSError: pass
                        return {"err": f"the sample could not be kept: {e.strerror or e}"}
            finally:
                try: os.close(fd)
                except OSError: pass
                try: lock.unlink()
                except OSError: pass
    try: data = path.read_bytes()
    except OSError as e: return {"err": f"the sample could not be read: {e.strerror or e}"}
    return {"err": None, "url": "data:audio/wav;base64," + base64.b64encode(data).decode(), "cached": cached,
            "bytes": len(data), "voice": voice, "tone": tone}

# --- Settings > Communication, the tree (SPEC 2682 #2824). One read answers the effective state of every node:
# Project > Workspace > Channel. Every resolved value comes from the policy module (#2684); the route computes no rule
# and the view holds none (ADR-0137, ADR-0177). The Now block of a node carries the tiles; the view only draws them.
def _slack_project_label():
    return str(os.environ.get("SG_PROJECT") or PROFILE_NAME or "project")

def _slack_badge(text, tone="neutral", kind="class"):
    return {"text": _slack_text(text), "tone": tone, "kind": kind}

def _slack_tile(key, value, sub="", tone="neutral"):
    return {"key": key, "value": _slack_text(value), "sub": _slack_text(sub), "tone": tone}

def _slack_tree_workspaces(P, committed_doc, local_doc, cf, lf):
    """The workspaces of both files in file order, one row per id: the first document's row wins and a later row adds a
    channel it names, so one workspace is one node and never two. A row that names no id keys on its document and
    position, so it still reads and never takes another row's place."""
    groups, by_id = [], {}
    for doc, file in ((committed_doc, cf), (local_doc, lf)):
        for i, row in _slack_dicts(doc.get("workspaces")):
            wid = _slack_text(row.get("id")) or f"\0{file}:{i}"
            group = by_id.get(wid)
            if group is None:
                group = by_id[wid] = {"id": wid, "ws": row, "file": file, "index": i, "rows": [],
                                      "channels": [], "named": set()}
                groups.append(group)
            group["rows"].append((row, file, i))
            for ci, ch in _slack_dicts(row.get("channels")):
                cid = _slack_text(ch.get("id")) or f"\0{file}:{i}:{ci}"
                if cid in group["named"]:
                    continue
                group["named"].add(cid)
                group["channels"].append({"id": cid, "row": ch, "file": file, "index": ci, "ws": row})
    return groups

def _slack_tree_channel_row(group, ch_id):
    """The one channel row of both documents, committed first and the local file over it, as `decide` merges them, so
    the class, the mode and the grant of the file that names them answer together."""
    merged = {}
    for row, file, i in group["rows"]:
        for k, candidate in _slack_dicts(row.get("channels")):
            if str(candidate.get("id")) == str(ch_id):
                merged.update(candidate)
    return merged

def _slack_tree_channel_icon(P, ws, merged, ch_id):
    """The channel's tree icon (#2843): the chat bubble of a guest channel or a direct message, the hash otherwise. A
    direct message carries no workspace class of its own, so its Slack `D` id decides."""
    guest = P.channel_class(ws, merged) == "guest"
    return "chat" if guest or str(ch_id).upper().startswith("D") else "hash"

def _slack_tree_permissions(P, committed_doc, local_doc, ws_id, ch_id, merged):
    """The Permissions section of one channel: each role of the project with its actions, whether the channel accepts
    it and its members. The distinct eligible people, the members the preview offers and a note for each person who
    holds two accepted roles come from the same rows. The policy decides every value; this read only arranges them."""
    accepted = [_slack_text(r) for r in merged.get("roles") or ()]
    own = [row for _, row in _slack_dicts(local_doc.get("members"))
           if (_slack_text(row.get("workspace")), _slack_text(row.get("channel"))) == (ws_id, ch_id)]
    roles = []
    for _, role in _slack_dicts(committed_doc.get("roles")):
        rid = _slack_text(role.get("id"))
        users = [str(u) for row in own if _slack_text(row.get("role")) == rid for u in row.get("users") or ()]
        roles.append({"id": rid, "actions": [_slack_text(a) for a in role.get("actions") or ()],
                      "accepted": rid in accepted, "members": users})
    now = dt.datetime.now(dt.UTC)
    held = {}
    for row in own:
        role = _slack_text(row.get("role"))
        if role not in accepted:
            continue
        for user in row.get("users") or ():
            names = held.setdefault(str(user), [])
            if role not in names:
                names.append(role)
    notes = []
    for user, names in held.items():
        if len(names) < 2:
            continue
        # The membership the policy takes comes from `decide`, the same resolution that answers a message, never from
        # a local list order (AC4).
        taken = P.decide(committed_doc, local_doc, {"workspace": ws_id, "channel": ch_id, "user": user,
                                                    "kind": "message"}, now).role or names[0]
        notes.append(f"{user} holds {' and '.join(names)}. The policy takes {taken}.")
    members = list(dict.fromkeys(P.resolved_members(local_doc, ws_id, ch_id, accepted)))
    return {"roles": roles, "eligible": len(members), "notes": notes, "members": members}
def _slack_tree_node_channel(P, committed_doc, local_doc, group, ch, node_id, refused, cf, lf):
    """One channel node: its badges and its Now tiles. Every value is the policy's: the resolution of the persona and
    the language, the decision for one eligible member's message, the transport badge and the announcement list. A
    channel of a refused workspace carries the refusal alone, never a send, bot or listen badge."""
    ws, ws_id, ch_id = group["ws"], group["id"], ch["id"]
    merged = _slack_tree_channel_row(group, ch_id)
    name = _slack_text(merged.get("name") or ch_id)
    announcement = [_slack_text(x) for x in merged.get("announce") or ()] if isinstance(merged.get("announce"), list) else []
    label = _slack_text(ws.get("label") or ws_id)
    icon = _slack_tree_channel_icon(P, ws, merged, ch_id)
    if refused:
        return {"id": node_id, "level": 4, "kind": "channel", "name": f"#{name}", "children": [], "icon": icon, "badges":
                [_slack_badge("refused", "err", "refused")], "now": [],
                "detail": {"title": f"#{name}", "sub": label, "text": "The workspace is refused, so this channel names no transport."}}
    resolution = P.resolution(committed_doc, local_doc, ws, merged)
    persona = resolution["persona"]
    members = P.resolved_members(local_doc, ws_id, ch_id, merged.get("roles") or ())
    decision = P.decide(committed_doc, local_doc, {"workspace": ws_id, "channel": ch_id,
                                                   "user": members[0] if members else ""}, dt.datetime.now(dt.UTC))
    badges = []
    if P.channel_class(ws, merged) != _slack_text(ws.get("class") or "owned"):
        badges.append(_slack_badge(P.channel_class(ws, merged)))
    badges.append(_slack_badge(P.transport(ws, merged), "neutral", "transport"))
    if not members:
        badges.append(_slack_badge("no one", "warn", "noone"))
    eligible = ("1 eligible person" if len(members) == 1 else f"{len(members)} eligible people") if members \
        else "nobody eligible"
    reply_value = P.reply_label(merged, decision)
    reply_sub = "listen mode" if reply_value == "never posts" else "nobody eligible to ask" if not members else ""
    now = [_slack_tile("Persona", persona.name if persona else "none", f"from {resolution['persona_source']}",
                       "neutral" if persona else "warn"),
           _slack_tile("Language", resolution["language"], f"from {resolution['language_source']}"),
           _slack_tile("A member's message", P.destination_label(decision), eligible,
                       "warn" if not members else "neutral"),
           _slack_tile("Reply", reply_value, reply_sub, "warn" if reply_value in ("none", "never posts") else "neutral"),
           _slack_tile("Announcements", "none" if not announcement else _slack_counts(len(announcement), "event"),
                       "as drafts for your review" if announcement else "",
                       "neutral" if announcement else "muted")]
    note = P.channel_note(merged, members, announcement)
    node = {"id": node_id, "level": 4, "kind": "channel", "name": f"#{name}", "children": [], "icon": icon,
            "badges": badges, "now": now, "ref": {"workspace": ws_id, "channel": ch_id},
            "permissions": _slack_tree_permissions(P, committed_doc, local_doc, ws_id, ch_id, merged),
            "detail": {"title": f"#{name}", "sub": f"{label} · {_slack_text(ch_id)}".strip(" ·"), "text": "",
                       "note": note}}
    # The detail pane edits the channel by its Slack id, and each field names the file that owns it (#2844).
    node["ids"] = {"workspace": ws_id, "channel": ch_id}
    node["settings"] = {"rows": _slack_tree_settings(P, committed_doc, local_doc, group, ch, merged, cf, lf)}
    return node


def _slack_tree_settings(P, committed_doc, local_doc, group, ch, merged, cf, lf):
    """The channel's setting rows (#2844). A presentation field (mode, persona, language, the accepted roles, announce
    and the Slack Connect mark) writes the file that holds the channel row; a delivery field (`speak`, `send_grant`,
    `draft_for`, every poll setting, transcription and the voice reply) writes the local file. Each row names its file,
    and a control carries the field name alone: the operation addresses the channel by its Slack id, never by its place
    in a list. Every value choice comes from the policy module, and the view only constructs and binds (ADR-0137)."""
    ws_id, ch_id = group["id"], ch["id"]
    owner_doc = "committed" if ch["file"] == cf else "local"
    owner_file = cf if owner_doc == "committed" else lf
    owner = _slack_channel_row_of(committed_doc if owner_doc == "committed" else local_doc, ws_id, ch_id) or {}
    local = _slack_channel_row_of(local_doc, ws_id, ch_id) or {}
    inherited = {"value": "", "label": "inherit"}

    def pick(field, value, options):
        return {"field": field, "kind": "select", "value": _slack_text(value), "options": options}

    def written(field, value):
        return {"field": field, "kind": "text", "value": _slack_text(value)}

    def names(field, values):
        return {"field": field, "kind": "names",
                "value": ", ".join(_slack_text(x) for x in values) if isinstance(values, list) else ""}

    def named(control, label):
        """The row draws one line per control, so a group with two controls names each one (#2844)."""
        return {**control, "label": label}

    # A persona option stages the persona id the policy names, never the row's place in the list: the hub would refuse a
    # counter, and the page would show no effect of the edit (#2846).
    personas = [{"value": _slack_text(row.get("id")), "label": _slack_text(row.get("name") or row.get("id"))}
                for i, row in _slack_dicts(committed_doc.get("personas")) if _slack_text(row.get("id"))]
    languages = [inherited] + [{"value": language, "label": language} for language in P.LANGUAGES]
    rows = [
        {"key": "mode", "label": "Mode", "doc": owner_doc, "file": owner_file,
         "controls": [pick("mode", owner.get("mode") or "listen", list(P.FIELD_MODES))]},
        {"key": "persona", "label": "Persona", "doc": owner_doc, "file": owner_file,
         "controls": [pick("persona", owner.get("persona") or "", [inherited] + personas)]},
        {"key": "language", "label": "Language", "doc": owner_doc, "file": owner_file,
         "controls": [pick("language", owner.get("language") or "", languages)]},
        {"key": "connect", "label": "Slack Connect", "doc": owner_doc, "file": owner_file,
         "controls": [{"field": "connect", "kind": "read", "value": "yes" if merged.get("connect") else "no"}]},
        {"key": "roles", "label": "Accepted roles", "doc": owner_doc, "file": owner_file,
         "controls": [names("roles", owner.get("roles"))]},
        {"key": "announce", "label": "Announce", "doc": owner_doc, "file": owner_file,
         "controls": [names("announce", owner.get("announce"))]},
    ]
    if P.channel_class(group["ws"], merged) == "guest":
        poll = local.get("poll") if isinstance(local.get("poll"), dict) else {}
        poll_mode = _slack_text(poll.get("mode") or "two_state")
        fields = (("floor", "Floor"), ("ceiling", "Ceiling"), ("factor", "Factor"),
                  ("idle_window", "Idle window")) if poll_mode == "exponential" \
            else (("fast", "Fast"), ("slow", "Slow"), ("quiet_after", "Quiet after"))
        watch = [named(pick("poll.mode", poll_mode, list(P.FIELD_POLL_MODES)), "Poll mode")]
        watch += [named(written(f"poll.{name}", poll.get(name) or ""), label) for name, label in fields]
        watch.append(named(written("poll.thread_open", poll.get("thread_open") or ""), "Thread open"))
        rows.insert(3, {"key": "delivery", "label": "Delivery", "doc": "local", "file": lf,
                        "controls": [named(pick("speak", local.get("speak") or "draft", list(P.FIELD_SPEAK)),
                                           "Answer as"),
                                     named(written("send_grant", local.get("send_grant") or ""), "Send grant")],
                        "note": f"granted {local['send_grant']} by the hub" if local.get("send_grant")
                        else "no grant: a reply leaves as a draft"})
        rows.insert(4, {"key": "draft_for", "label": "Always draft for", "doc": "local", "file": lf,
                        "controls": [names("draft_for", local.get("draft_for"))]})
        rows.insert(6, {"key": "watch", "label": "Watch", "doc": "local", "file": lf, "controls": watch,
                        "note": "a blank field keeps the policy default"})
        rows.insert(7, {"key": "audio", "label": "Audio", "doc": "local", "file": lf,
                        "controls": [named(pick("transcribe", local.get("transcribe") or "off",
                                                list(P.FIELD_TRANSCRIBE)), "Transcribe a clip"),
                                     {"field": "voice_reply", "kind": "check", "label": "Voice reply",
                                      "value": bool(local.get("voice_reply"))}]})
    return rows

def _slack_tree_node_workspace(P, committed_doc, local_doc, group, node_id, held, refused_keys, defects, cf, lf):
    """One workspace node: its class tile, its persona and language, the guest slot it holds, the validation defects
    that name it and its channels. A refused workspace states its one defect and its channels carry no transport."""
    ws, ws_id = group["ws"], group["id"]
    class_ = _slack_text(ws.get("class") or "owned")
    refused = (group["file"], group["index"]) in refused_keys
    has_guest_channel = any(P.channel_class(row, candidate) == "guest"
                            for row, file, i in group["rows"] for k, candidate in _slack_dicts(row.get("channels")))
    caption = "owned · contains guest channel" if class_ == "owned" and has_guest_channel else class_
    resolution = P.resolution(committed_doc, local_doc, ws)
    persona = resolution["persona"]
    slot = "refused" if refused else "holds it" if _slack_text(ws.get("label") or ws_id) == held else "—"
    now = [_slack_tile("Class", caption, "", "warn" if refused else "neutral"),
           _slack_tile("File", _slack_text(group["file"]), "the workspace row lives here"),
           _slack_tile("Persona", persona.name if persona else "none", f"from {resolution['persona_source']}",
                       "neutral" if persona else "warn"),
           _slack_tile("Language", resolution["language"], f"from {resolution['language_source']}"),
           _slack_tile("Guest slot", slot, "one guest workspace per machine",
                       "err" if refused else "neutral" if slot == "holds it" else "muted")]
    named_diags = [d for d in defects if d.file == group["file"]
                   and str(d.key).startswith(f"workspaces[{group['index']}]")]
    # `defects` serializes each policy diagnostic whole, key and reason; the detail pane shows the reason alone, in
    # plain words, in the error style (#2842).
    named = [str(d) for d in named_diags]
    defect_note = " ".join(str(d.reason) for d in named_diags)
    channels = [_slack_tree_node_channel(P, committed_doc, local_doc, group, ch, f"{node_id}.c{k}", refused, cf, lf)
                for k, ch in enumerate(group["channels"])]
    badges = [_slack_badge(class_)] + ([_slack_badge("refused", "err", "refused")] if refused else [])
    return {"id": node_id, "level": 3, "kind": "workspace", "name": _slack_text(ws.get("label") or ws_id),
            "state": "refused" if refused else "ok", "badges": badges, "now": now, "defects": named,
            "children": channels,
            "detail": {"title": _slack_text(ws.get("label") or ws_id), "sub": f"{class_} · {_slack_text(ws_id)}", "defect_note": defect_note,
                       "text": "A client workspace. The persona reads it through the connector and writes from your own "
                               "account with the persona mark; the bot never joins it." if class_ == "guest" else ""}}

SLACK_TREE_SETUP = ({"id": "owned", "label": "Add owned workspace"}, {"id": "guest", "label": "Add guest workspace"})

def _slack_tree_failed(message):
    """The tree of a read that could not resolve the policy: one Unassigned client with one error project, its Now tiles
    at zero, the reason as its one defect, and the two setup choices. A broken module never costs the reader the shape."""
    project = {"id": "c0.p0", "level": 2, "kind": "project", "name": _slack_project_label(), "state": "error",
               "badges": [_slack_badge("no Slack", "muted")], "children": [], "defects": [message],
               "now": [], "setup": list(SLACK_TREE_SETUP), "detail": {"title": "", "sub": "", "text": ""}}
    client = {"id": "c0", "level": 1, "kind": "client", "name": "Unassigned", "state": "error", "client": "",
              "default_persona": "", "badges": [], "children": [project], "defects": [], "setup": [],
              "now": [{"key": key, "value": "0", "tone": "neutral"} for key in ("Workspaces", "Channels", "Personas", "Events")],
              "persona_options": [], "detail": {"title": "Unassigned", "sub": "", "text": ""}}
    return {"err": message, "files": {"committed": "slack.yaml", "local": "slack.local.yaml"}, "defects": [message],
            "empty": True, "guest_slot": None, "notice": None, "clients": [client], "root": client,
            "counts": {"clients": 1, "projects": 1, "channels": 0}}


def slack_clients_module():
    """The one home of the client map rules (ADR-0177): the hub holds no copy of a rule."""
    roots = squad_roots(SQUAD_ROOT, os.environ.get("SG_CHECKOUT"), CHECKOUT, __file__)
    (CL,) = load_squad(roots, ("clients",))
    return CL


def clients_map_path():
    """The client map of the profile in force: a local file in the profile folder, never a file of a repository."""
    d = profiles_dir()
    return pathlib.Path(d) / "clients.local.yaml" if d is not None else None


def _clients_map_read():
    """(clients, error): the clients of the profile folder's map, normalized. An absent map groups every project under the
    one Unassigned node, which is the map of a fresh machine."""
    path = clients_map_path()
    if path is None: return [], "the profile folder did not resolve, so no client map was read"
    try: text = path.read_text()
    except FileNotFoundError: return [], None
    except OSError as e: return [], f"the client map could not be read: {e.strerror or e}"
    if not text.strip(): return [], None
    try: doc = yaml.safe_load(text)
    except yaml.YAMLError as e: return [], f"the client map is not YAML: {e}"
    try: CL = slack_clients_module()
    except Exception as e: return [], f"the client map module did not resolve: {type(e).__name__}: {e}"
    return CL.normalize(doc if isinstance(doc, dict) else {}), None


def _profiles_read():
    """(contexts, error): every profile the hub knows: slug, checkout and project, one line each. The seam (ADR-0138)
    reads each profile on its own; a profile it refuses is skipped, so one broken profile never costs the tree the rest.
    A seam that could not run answers an error, so a caller that writes tells a failed read from a machine with no
    profile."""
    here = pathlib.Path(__file__).resolve().parent
    script = ('. "$1/address.sh"; for n in $(sg_profiles); do sg_repo_ctx "$n" >/dev/null 2>&1 || continue; '
              'printf "%s\t%s\t%s\n" "$n" "$SG_CHECKOUT" "$SG_PROJECT"; done')
    env = {**os.environ}
    d = profiles_dir()
    if d is not None: env["SG_PROFILES_DIR"] = str(d)
    try:
        r = subprocess.run(["bash", "-c", script, "_", str(here)], capture_output=True, text=True, timeout=20, env=env)
    except subprocess.TimeoutExpired:
        return [], "the profile read timed out after 20 s"
    except Exception as e:
        return [], f"the profile read failed: {type(e).__name__}: {e}"
    if r.returncode:
        return [], f"the profile read failed: {(r.stderr or r.stdout or '').strip() or f'exit {r.returncode}'}"
    out = []
    for line in (r.stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0] and parts[1]:
            out.append({"slug": parts[0], "checkout": parts[1], "project": parts[2] if len(parts) > 2 else ""})
    return out, None


def _profiles_context():
    """Every profile the hub knows, for a read that draws the tree alone: a failed read answers no profile."""
    return _profiles_read()[0]


def _slack_seat(slug, checkout, project):
    """One project the tree draws: its profile slug, its two documents and the file names it states."""
    try: P = slack_policy()
    except Exception:
        return None
    cp = pathlib.Path(checkout) / SLACK_YAML_REL if checkout else None
    lp = None
    d = profiles_dir()
    if d is not None and (project or slug):
        try: raw = P.local_path(str(d), project or slug)
        except Exception: raw = None
        lp = pathlib.Path(raw) if raw else None
    committed, local, defects = P.documents(cp, lp)
    return {"id": slug, "label": slug, "checkout": checkout, "project": project or slug,
            "committed": committed, "local": local, "committed_path": cp, "local_path": lp,
            "read_defects": defects, "cf": _slack_name(cp, "slack.yaml"), "lf": _slack_name(lp, "slack.local.yaml")}


PREVIEW_ROSTER_SECONDS = 3.0   # a burst of previews of one change shares one profile read; a save always reads fresh
_ROSTER_CACHE = {"key": None, "at": 0.0, "seats": None}


def _roster_key():
    return (str(profiles_dir()), os.environ.get("SG_PROFILES_DIR") or "", os.environ.get("SG_CHECKOUT") or "")


def _slack_roster_checked(cache=0.0):
    """(seats, error): one seat per project the hub knows, in the seam's order, each profile once, and the error of a
    profile read that did not finish. `_profiles_read` runs a bash subprocess with a 20 s timeout, so a preview asks a
    short-lived cache; a save takes no cache and clears it."""
    key = _roster_key() if cache > 0 else None
    if cache > 0:
        ent = _ROSTER_CACHE
        if ent["seats"] is not None and ent["key"] == key and time.monotonic() - ent["at"] < cache:
            return ent["seats"], None
    contexts, err = _profiles_read()
    if err: return [], err
    seats, seen = [], set()
    for ctx in contexts:
        if ctx["slug"] in seen: continue
        seat = _slack_seat(ctx["slug"], ctx["checkout"], ctx["project"])
        if seat is not None:
            seen.add(ctx["slug"]); seats.append(seat)
    if cache > 0:
        _ROSTER_CACHE.update({"key": key, "at": time.monotonic(), "seats": seats})
    return seats, None


def _slack_roster(cache=0.0):
    """The seats of one tree read: a failed profile read answers no seat, and the tree names no project."""
    return _slack_roster_checked(cache)[0]


def _slack_roster_clear():
    _ROSTER_CACHE.update({"key": None, "at": 0.0, "seats": None})


def _slack_project_node(P, seat, node_id):
    """One project node of the tree: its badges, its Now tiles, its setup choices when it names no Slack file, and its
    workspaces below it. Every resolved value comes from the policy module; the route merges and counts."""
    committed_doc, local_doc, cf, lf = seat["committed"], seat["local"], seat["cf"], seat["lf"]
    defects = list(seat.get("read_defects") or []) + list(P.validate(committed_doc, local_doc, local_name=lf))
    refused_keys = set()
    for d in defects:
        hit = re.match(r"workspaces\[(\d+)\]\.id\Z", str(d.key))
        if hit and d.reason == P.SECOND_GUEST_REASON:
            refused_keys.add((d.file, int(hit.group(1))))
    groups = _slack_tree_workspaces(P, committed_doc, local_doc, cf, lf)
    local_rows = _slack_dicts(local_doc.get("workspaces"))
    guests = P.guest_workspaces(local_doc)
    held = _slack_text(local_rows[guests[0]][1].get("label") or local_rows[guests[0]][1].get("id")) if guests else None
    guest_slot = {"held": held, "count": len(guests), "line": f"Guest slot 1 of {len(guests)} · {held}"} if guests else None
    label = seat["label"]
    personas = _slack_dicts(committed_doc.get("personas"))
    events = _slack_dicts(committed_doc.get("events"))
    project = P.project_settings(committed_doc, local_doc)
    default_name = next((_slack_text(r.get("name")) for i, r in personas
                         if _slack_text(r.get("id")) == project["persona"]), None)
    nodes = [_slack_tree_node_workspace(P, committed_doc, local_doc, g, f"{node_id}.w{k}", held, refused_keys, defects, cf, lf)
             for k, g in enumerate(groups)]
    empty = not groups
    rows = [(_slack_tree_channel_row(g, ch["id"]), g) for g in groups for ch in g["channels"]]
    speak = sum(1 for merged, g in rows if P.can_speak(merged))
    subscribing = sum(1 for merged, g in rows if isinstance(merged.get("announce"), list) and merged.get("announce"))
    guest_nodes = [g for g in groups if any(P.guest_workspaces({"workspaces": [row]}) for row, file, i in g["rows"])]
    now = [] if empty else [   # a project count is a fact, never a passing state: every project tile stays neutral (#2841)
        _slack_tile("Workspaces", f"{len(groups)} · {len(groups) - len(guest_nodes)} owned · {len(guest_nodes)} guest"),
        _slack_tile("Channels", f"{len(rows)} · {speak} speak"),
        _slack_tile("Personas", f"{len(personas)} · default {default_name}" if default_name
                    else f"{len(personas)} · no default"),
        _slack_tile("Events", f"{len(events)} · {_slack_counts(subscribing, 'channel')} subscribe")]
    notice = {"title": f"{len(guests)} guest workspaces", "text": P.SECOND_GUEST_REASON} if len(guests) > 1 else None
    badges = [_slack_badge("no Slack", "muted")] if empty else ([_slack_badge(default_name)] if default_name else [])
    if notice: badges.append(_slack_badge(notice["title"], "err", "refused"))   # the banner also shows on the project's row
    node = {"id": node_id, "level": 2, "kind": "project", "name": label, "state": "empty" if empty else "ok",
            "badges": badges,
            "now": now, "children": nodes, "defects": [], "setup": list(SLACK_TREE_SETUP) if empty else [],
            "notice": notice, "guest_slot": guest_slot,
            "detail": {"title": label, "sub": "",
                       "text": "This project has no Slack files yet." if empty else ""}}
    return {"node": node, "with_slack": bool(committed_doc or local_doc), "channels": len(rows),
            "guest_slot": guest_slot, "notice": notice, "defects": defects}


def _slack_persona_options(P, seats):
    """The persona ids and names the client's projects declare, each once, in project order: the choices a default
    persona may take."""
    out, seen = [], set()
    for seat in seats:
        for doc in (seat["committed"], seat["local"]):
            for _, row in _slack_dicts(doc.get("personas")):
                pid = _slack_text(row.get("id"))
                if pid and pid not in seen:
                    seen.add(pid)
                    out.append({"id": pid, "name": _slack_text(row.get("name")) or pid})
    return out


def _slack_clients_tree_for(P, seats):
    """The tree over the given seats: Client > Project > Workspace > Channel, the clients from the local map and one
    Unassigned node for the projects no client names."""
    try: CL = slack_clients_module()
    except Exception as e:
        return _slack_tree_failed(f"the client map module did not resolve: {type(e).__name__}: {e}")
    clients, merr = _clients_map_read()
    if merr: clients = []
    groups = CL.groups(clients, [s["id"] for s in seats])
    by_id = {s["id"]: s for s in seats}
    unknown = []   # a project the map names and no profile holds: the tree drops it, and names it under the tree
    for g in clients:
        for p in g["projects"]:
            if p["id"] not in by_id and p["id"] not in unknown: unknown.append(p["id"])
    nodes, all_defects, slots, channels = [], [], [], 0
    for i, g in enumerate(groups):
        children, with_slack = [], 0
        for j, entry in enumerate(g["projects"]):
            seat = by_id[entry["id"]]
            built = _slack_project_node(P, seat, f"c{i}.p{j}")
            built["node"]["badges"] = list(built["node"]["badges"]) + [
                _slack_badge("follows default", "muted", "follow") if entry["follows"]
                else _slack_badge("own persona", "neutral", "follow")]
            children.append(built["node"])
            with_slack += 1 if CL.has_slack(seat["committed"], seat["local"]) else 0
            channels += built["channels"]
            all_defects.extend(str(d) for d in built["defects"])
            if built["guest_slot"]: slots.append((built["guest_slot"], entry["id"]))
        personal = g.get("default_persona")
        seats_of = [by_id[e["id"]] for e in g["projects"]]
        node = {"id": f"c{i}", "level": 1, "kind": "client", "name": g["name"], "client": g["id"],
                "default_persona": personal["id"] if personal else "",
                "state": "empty" if not g["projects"] else "ok",
                "badges": [_slack_badge(personal["name"])] if personal else [], "defects": [],
                "now": [_slack_tile("Projects", str(len(g["projects"])), f"{with_slack} with Slack"),
                        _slack_tile("Default persona", personal["name"] if personal else "none",
                                    "client map" if personal else "")],
                "children": children, "setup": [],
                "persona_options": _slack_persona_options(P, seats_of),
                "detail": {"title": g["name"], "sub": "",
                           "text": "A client groups projects in the local client map, never in a repository. Changing its "
                                   "default persona proposes a write to each project's project.persona; a project that set "
                                   "its own persona keeps it."}}
        nodes.append(node)
    # The second-guest refusal is a per-project rule (#2770), so its banner sits on the project that carries it, never
    # across the tree; the guest-slot line is built only for a scope of one project.
    notice = None
    guest_slot = None
    if len(seats) == 1:
        guests = len(P.guest_workspaces(seats[0]["local"]))
        notice = {"title": f"{guests} guest workspaces", "text": P.SECOND_GUEST_REASON} if guests > 1 else None
        if slots:
            guest_slot = {**slots[0][0]}
    files = {"committed": seats[0]["cf"], "local": seats[0]["lf"]} if seats else {"committed": "slack.yaml", "local": "slack.local.yaml"}
    no_slack = not any(CL.has_slack(s["committed"], s["local"]) for s in seats)
    return {"err": None, "files": files, "defects": all_defects + ([merr] if merr else []), "empty": not seats or no_slack, "notice": notice,
            "guest_slot": guest_slot, "clients": nodes, "unknown_projects": unknown, "root": nodes[0] if nodes else None,
            "counts": {"clients": len(nodes), "projects": len(seats), "channels": channels}}


def _slack_tree_docs(P, committed_doc, local_doc, committed, local, read_defects=()):
    """The tree of two documents already in hand, so a staged edit's answer redraws the Now block before the save
    (#2846). The caller hands in the documents and the file names, never a rule (ADR-0137, ADR-0177)."""
    seat = {"id": _slack_project_label(), "label": _slack_project_label(), "checkout": None,
            "committed": committed_doc, "local": local_doc, "committed_path": committed, "local_path": local,
            "read_defects": list(read_defects),
            "cf": _slack_name(committed, "slack.yaml"), "lf": _slack_name(local, "slack.local.yaml")}
    return _slack_clients_tree_for(P, [seat])


def slack_tree(committed=None, local=None):
    """The tree of the Communication theme (SPEC 2682 #2824, #2827): Client > Project > Workspace > Channel, each node
    with its badges and its Now tiles. Called with no path, it reads the projects of every profile the hub knows; called
    with one project's two documents, it draws that project alone. Every resolved value comes from the policy module
    (#2684) and the client map module (#2827); this read merges the rows and counts them, and computes no rule."""
    if committed is not None or local is not None:
        try:
            P = slack_policy()
            committed_doc, local_doc, read_defects = P.documents(committed, local)
        except Exception as e:
            return _slack_tree_failed(f"the Slack rules module did not resolve: {type(e).__name__}: {e}")
        seat = {"id": _slack_project_label(), "label": _slack_project_label(), "checkout": None,
                "committed": committed_doc, "local": local_doc, "committed_path": committed, "local_path": local,
                "read_defects": read_defects,
                "cf": _slack_name(committed, "slack.yaml"), "lf": _slack_name(local, "slack.local.yaml")}
        return _slack_clients_tree_for(P, [seat])
    try: P = slack_policy()
    except Exception as e:
        return _slack_tree_failed(f"the Slack rules module did not resolve: {type(e).__name__}: {e}")
    return _slack_clients_tree_for(P, _slack_roster())


def _slack_flag(value):
    return str(value or "").lower() in ("1", "true", "yes")

def _slack_refused_keys(P, defects):
    """The (file, index) of each workspace row a second guest refuses, as the validator names it."""
    keys = set()
    for d in defects:
        hit = re.match(r"workspaces\[(\d+)\]\.id\Z", str(d.key))
        if hit and d.reason == P.SECOND_GUEST_REASON:
            keys.add((d.file, int(hit.group(1))))
    return keys

def slack_decide(query, committed=None, local=None):
    """GET /api/slack/decide: the sender preview of one channel. It runs `decide` on one message event built from the
    query (`workspace`, `channel`, `user`, `sender_type`, and the `own` flag) and returns the answer as text. It posts
    nothing. A refused workspace reads `not read` in every field, because the policy never reads it; an unknown
    workspace or channel reads `unknown`. The route binds the sender to the channel's own resolved members: a
    `member` must be one of them, a `stranger` must not be, and a `bot` posts as itself. It loads and validates the
    two documents once per request."""
    not_read = {"err": None, "decision": "not read", "role": "not read", "actions": "not read", "reply": "not read",
                "mark": "not read", "language": "not read"}
    unknown = {"err": None, "decision": "unknown", "role": "unknown", "actions": "unknown", "reply": "unknown",
               "mark": "unknown", "language": "unknown"}
    if committed is None and local is None:
        try:
            committed, local = slack_yaml_paths()
        except Exception as e:
            return {"err": f"the Slack files did not resolve: {type(e).__name__}: {e}"}
    try:
        P = slack_policy()
        committed_doc, local_doc, read_defects = P.documents(committed, local)
    except Exception as e:
        return {"err": f"the Slack rules module did not resolve: {type(e).__name__}: {e}"}
    cf, lf = _slack_name(committed, "slack.yaml"), _slack_name(local, "slack.local.yaml")
    defects = list(read_defects) + list(P.validate(committed_doc, local_doc, local_name=lf))
    refused_keys = _slack_refused_keys(P, defects)
    ws_id, ch_id = str(query.get("workspace") or ""), str(query.get("channel") or "")
    groups = _slack_tree_workspaces(P, committed_doc, local_doc, cf, lf)
    group = next((g for g in groups if g["id"] == ws_id), None)
    if group is None:
        return dict(unknown, err=f"unknown workspace {ws_id!r}")
    if (group["file"], group["index"]) in refused_keys:
        return not_read
    if not any(str(ch["id"]) == ch_id for ch in group["channels"]):
        return dict(unknown, err=f"unknown channel {ch_id!r} in workspace {ws_id!r}")
    merged = _slack_tree_channel_row(group, ch_id)
    members = list(dict.fromkeys(P.resolved_members(local_doc, ws_id, ch_id, merged.get("roles") or ())))
    sender_type, user = str(query.get("sender_type") or ""), str(query.get("user") or "")
    if sender_type not in ("member", "stranger", "bot"):
        return {"err": "sender_type must be member, stranger or bot"}
    if sender_type == "member" and user not in members:
        return {"err": f"the user {user!r} holds no accepted role in this channel"}
    if sender_type == "stranger" and user in members:
        return {"err": f"the user {user!r} is a member of this channel"}
    if sender_type == "bot" and user and user not in members:
        return {"err": f"the bot posts as a member, and {user!r} is none"}
    decision = P.decide(committed_doc, local_doc,
                        {"workspace": ws_id, "channel": ch_id, "user": user, "kind": "message",
                         "bot": sender_type == "bot", "own": _slack_flag(query.get("own"))},
                        dt.datetime.now(dt.UTC))
    return {"err": None, "decision": decision.kind, "role": decision.role or "none",
            "actions": ", ".join(decision.actions) or "none", "reply": P.reply_label(merged, decision),
            "mark": P.mark(decision.persona) or "none",
            "language": decision.language}

def slack_overview(committed=None, local=None):
    """The read view of the Communication theme: the Now tiles, each persona with the channels that resolve to it, each
    role with its actions, each Slack workspace with its class and channels, and each project event with its template.
    Each field names the file it comes from. Called with no path, it reads the files of the profile in force."""
    explicit = committed is not None or local is not None
    if not explicit:
        try:
            committed, local = slack_yaml_paths()
        except Exception as e:
            return dict(_slack_overview_empty(f"the Slack rules module did not resolve: {type(e).__name__}: {e}"),
                        tree=_slack_tree_failed(str(e)))
    try:
        P = slack_policy()
        committed_doc, local_doc, defects = P.documents(committed, local)
    except Exception as e:
        return dict(_slack_overview_empty(f"the Slack rules module did not resolve: {type(e).__name__}: {e}", committed, local),
                    tree=_slack_tree_failed(str(e)))
    cf, lf = _slack_name(committed, "slack.yaml"), _slack_name(local, "slack.local.yaml")
    project = P.project_settings(committed_doc, local_doc)
    personas = [{"id": _slack_text(r.get("id")), "name": _slack_text(r.get("name")), "voice": _slack_text(r.get("voice")),
                 "tone": _slack_text(r.get("tone")), "file": cf, "channels": []}
                for r in P.rows(committed_doc.get("personas"))]
    by_id = {p["id"]: p for p in personas}
    project["persona_name"] = by_id.get(project["persona"], {}).get("name")
    roles = [{"id": _slack_text(r.get("id")), "file": cf,
              "actions": [_slack_text(a) for a in r.get("actions") or ()]}
             for r in P.rows(committed_doc.get("roles"))]
    events = [{"id": _slack_text(r.get("id")), "template": _slack_text(r.get("template")), "file": cf}
              for r in P.rows(committed_doc.get("events"))]
    workspaces = []
    for doc, file in ((committed_doc, cf), (local_doc, lf)):
        # `index` is the row's position in its own document: the editor writes the raw document (#2698), and a row is
        # addressed by that position, so two rows of the same name never take each other's place.
        for wi, row in _slack_dicts(doc.get("workspaces")):
            label = _slack_text(row.get("label") or row.get("id"))
            language, persona = P.effective(committed_doc, local_doc, row)
            channels = []
            for ci, ch in _slack_dicts(row.get("channels")):
                ch_language, ch_persona = P.effective(committed_doc, local_doc, row, ch)
                name = _slack_text(ch.get("name") or ch.get("id"))
                channels.append({"id": _slack_text(ch.get("id")), "name": name,
                                 "mode": _slack_text(ch.get("mode") or "listen"), "language": ch_language,
                                 "persona": ch_persona.id if ch_persona else None,
                                 "roles": [_slack_text(x) for x in ch.get("roles") or ()],
                                 "announce": [_slack_text(x) for x in ch.get("announce") or ()], "file": file,
                                 "index": ci})
                if ch_persona and ch_persona.id in by_id:
                    by_id[ch_persona.id]["channels"].append(f"{label}/#{name}")
            workspaces.append({"id": _slack_text(row.get("id")), "label": label,
                               "class": _slack_text(row.get("class") or "owned"), "language": language,
                               "persona": persona.id if persona else None, "file": file, "channels": channels,
                               "index": wi})
    out = _slack_overview_shape(project, roles, events, personas, workspaces, defects, committed, local)
    # The raw documents the view hand back on Save (#2694); the resolved rows above are what it draws.
    out["docs"] = {"committed": committed_doc, "local": local_doc}
    # The roles editor (#2699): the actions the executor runs, read from the policy module, never a copy in the view.
    out["role_actions"] = list(P.role_actions())
    # The persona editor (#2697): the voice list the policy module owns, and the reply of each channel before a save.
    out["voices"] = sorted(str(v) for v in P.GEMINI_VOICES)
    out["previews"] = _slack_reply_previews(P, committed_doc, local_doc)
    # The tree (#2824): one read of the same two files, node by node, from the policy module alone.
    out["tree"] = slack_tree(committed, local) if explicit else slack_tree()
    return out

# --- Settings > Communication, the save path (SPEC 2682 #2694). The theme saves the two files it reads: the profile's
# local file, written at once beside the profile, and the committed `slack.yaml`, written only in the hub's own worktree,
# committed alone and landed in the landing branch by fast-forward; the hub never writes the loop checkout. The saved sha
# goes to one local registry outside every repository. The rules stay in the policy module (ADR-0177): the hub validates,
# diffs, writes and lands, and holds no copy of a rule (ADR-0137).
SLACK_WT_ENV = "SG_SLACK_WORKTREE"   # the worktree the hub owns for the committed file: never the loop checkout
SLACK_WT_DIR = "slack-worktree"      # under the profile's state directory when the environment names no worktree (#2763)
SLACK_REGISTRY = "slack-saved.json"  # in the loop state directory, outside every repository

def slack_registry_path():
    """The saved-sha registry: one local file in the loop state directory, outside every repository."""
    return pathlib.Path(STATE) / SLACK_REGISTRY

def slack_worktree(loop=None, name=None):
    """(worktree, error): the hub's own worktree for the committed Slack file. `SG_SLACK_WORKTREE` names it, else the hub
    derives one per profile under its state directory, outside every checkout, so no owner sets a variable by hand and
    hubmux's one process keeps one worktree per profile (#2763). `name` splits the path per project, under the derived
    base and under a base the environment names too, so a client save over several projects lands each one from its own
    tree and never interleaves them. A worktree that is the loop checkout, or sits inside it, is refused."""
    raw = os.environ.get(SLACK_WT_ENV) or ""
    wt = pathlib.Path(raw) if raw else pathlib.Path(STATE) / SLACK_WT_DIR
    if name:
        wt = wt / str(name)
    loop = loop or os.environ.get("SG_CHECKOUT") or CHECKOUT or ""
    if loop:
        try:
            root, here = pathlib.Path(loop).resolve(), wt.resolve()
            if here == root or here.is_relative_to(root):
                return None, "the committed file may not be written in the loop checkout"
        except (OSError, ValueError):
            pass
    return wt, None

def _slack_git(wt, *args):
    return subprocess.run(["git", "-C", str(wt), *args], capture_output=True, text=True, timeout=120)

def _slack_repo(loop=None):
    """The repository the hub hangs its worktree from: the loop checkout, named by the environment or read at import."""
    return loop or os.environ.get("SG_CHECKOUT") or CHECKOUT or ""

def _slack_is_worktree(wt):
    r = _slack_git(wt, "rev-parse", "--is-inside-work-tree")
    return r.returncode == 0 and r.stdout.strip() == "true"

def _slack_make_worktree(wt, repo=None):
    """Hang the hub's worktree from the loop checkout at origin/<land>, detached, so the hub owns its own tree and never
    writes the checkout's. Returns an error, or None."""
    repo = _slack_repo(repo)
    if not repo:
        return (f"the hub's Slack worktree is absent and neither SG_CHECKOUT nor the hub's working directory names "
                f"the repository, so {wt} cannot be created")
    r = _slack_git(repo, "fetch", "-q", "origin", f"+refs/heads/{LAND}:refs/remotes/origin/{LAND}")
    if r.returncode:
        return f"the hub's Slack worktree could not be created at {wt}: git fetch failed: {(r.stderr or r.stdout).strip()}"
    if _slack_git(repo, "rev-parse", "--verify", "--quiet", f"origin/{LAND}").returncode:
        return f"the hub's Slack worktree could not be created at {wt}: the checkout has no origin/{LAND}"
    p = pathlib.Path(wt)
    if p.exists():
        if not p.is_dir() or any(p.iterdir()):
            return f"the hub's Slack worktree at {wt} is not a git worktree and holds files; move it aside"
        p.rmdir()
    p.parent.mkdir(parents=True, exist_ok=True)
    r = _slack_git(repo, "worktree", "add", "--detach", str(p), f"origin/{LAND}")
    if r.returncode:
        return f"the hub's Slack worktree could not be created at {wt}: {(r.stderr or r.stdout).strip()}"
    return None

def _slack_owned(wt):
    """True when `wt` is a worktree the hub derives for itself under its state directory, one per project or the one of
    the profile in force. The hub advances only those: a worktree the owner names with `SG_SLACK_WORKTREE` keeps its
    HEAD, because the hub did not make its commits (#2763)."""
    try: return pathlib.Path(wt).resolve().is_relative_to((pathlib.Path(STATE) / SLACK_WT_DIR).resolve())
    except (OSError, ValueError): return False


def _slack_sync(wt):
    """Bring the hub's own derived worktree to origin/<land> when it stands clean and behind, so a later landing in the
    branch never strands the next save (#2763). A worktree the owner names by hand is never advanced. A worktree that
    carries uncommitted work, or a commit of its own, is left as it stands and `_slack_land_check` refuses it by name.
    Returns an error, or None."""
    if not _slack_owned(wt): return None
    g = lambda *a: _slack_git(wt, *a)   # noqa: E731
    r = g("fetch", "-q", "origin", f"+refs/heads/{LAND}:refs/remotes/origin/{LAND}")
    if r.returncode: return f"git fetch failed: {(r.stderr or r.stdout).strip()}"
    base = g("rev-parse", f"origin/{LAND}")
    if base.returncode: return f"the worktree has no origin/{LAND} to land on"
    head = g("rev-parse", "HEAD")
    if not head.returncode and head.stdout.strip() == base.stdout.strip(): return None
    if g("status", "--porcelain").stdout.strip(): return None
    if g("merge-base", "--is-ancestor", "HEAD", f"origin/{LAND}").returncode: return None
    r = g("reset", "--hard", "-q", f"origin/{LAND}")
    if r.returncode: return f"the worktree could not advance to origin/{LAND}: {(r.stderr or r.stdout).strip()}"
    return None

def _slack_provision(wt, repo=None):
    """(error, fatal): the hub's worktree ready for a land: created from the loop checkout when it is absent, and
    advanced to origin/<land> when it stands clean and behind. A worktree that carries uncommitted work is fatal: no
    save can land from it, and the refusal comes before any file is written. A worktree that cannot be created or
    advanced is fatal only to a save that needs a commit. Returns (error, fatal)."""
    if not _slack_is_worktree(wt):
        err = _slack_make_worktree(wt, repo)
        if err: return err, False
    err = _slack_sync(wt)
    if err: return err, False
    if _slack_git(wt, "status", "--porcelain").stdout.strip():
        return f"the hub's worktree at {wt} has uncommitted work: clean it before the save", True
    return None, False

def _slack_read_doc(path):
    """The YAML document at `path`, or {} when it is absent or unreadable, so a diff against it never raises."""
    try: doc = yaml.safe_load(pathlib.Path(path).read_text())
    except (OSError, yaml.YAMLError): return {}
    return doc if isinstance(doc, dict) else {}

def _slack_yaml_text(doc):
    try: return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)
    except yaml.YAMLError: return ""

def _slack_yaml_lines_doc(before, doc):
    """The changed lines from one document to another: both sides pass through the YAML dumper first, so a reformat
    that changes no value is no change."""
    out_before, after = _slack_yaml_text(before), _slack_yaml_text(doc)
    return [line for line in difflib.unified_diff(out_before.splitlines(), after.splitlines(), lineterm="", n=0)
            if line[:1] in "+-" and line[:3] not in ("+++", "---")]

def _slack_yaml_lines(path, doc):
    """The changed lines from the YAML file at `path` to `doc`. A file the reader cannot parse is compared as its raw
    text, so a broken file still shows its change."""
    try: old_text = pathlib.Path(path).read_text()
    except OSError: old_text = ""
    try:
        old = yaml.safe_load(old_text)
        before = old if isinstance(old, dict) else {}
    except yaml.YAMLError:
        before = None
    if before is None:
        after = _slack_yaml_text(doc)
        return [line for line in difflib.unified_diff(old_text.splitlines(), after.splitlines(), lineterm="", n=0)
                if line[:1] in "+-" and line[:3] not in ("+++", "---")]
    return _slack_yaml_lines_doc(before, doc)

def _slack_atomic(path, text):
    """Whole or not at all: a temp file beside the target, then a rename (ADR-0030). Returns an error, or None."""
    p = pathlib.Path(path)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, p)
    except OSError as e:
        return f"{p.name} could not be written: {e.strerror or e}"
    finally:
        try: tmp.unlink()
        except OSError: pass
    return None

def _slack_land_check(wt):
    """(base sha, error): the fetch and branch check a land needs. It runs after `_slack_provision` has made the
    worktree clean and stands it on origin/<land>, so it asserts that shape alone; a worktree that carries other work
    is named by `_slack_provision` before any file is written."""
    g = lambda *a: _slack_git(wt, *a)   # noqa: E731
    r = g("fetch", "-q", "origin", f"+refs/heads/{LAND}:refs/remotes/origin/{LAND}")
    if r.returncode: return None, f"git fetch failed: {(r.stderr or r.stdout).strip()}"
    base, head = g("rev-parse", f"origin/{LAND}"), g("rev-parse", "HEAD")
    if base.returncode or head.returncode: return None, f"the worktree has no origin/{LAND} to land on"
    if head.stdout.strip() != base.stdout.strip():
        why = "" if _slack_owned(wt) else f": the hub never advances a worktree named by {SLACK_WT_ENV}"
        return None, (f"the hub's worktree is not one commit above origin/{LAND}{why}, so the save would carry "
                      f"other work")
    return base.stdout.strip(), None

def _slack_land(wt, rel, message, base=None):
    """Commit one file in the hub's worktree and push that commit to the landing branch by fast-forward: (sha, error).
    `base` is the sha `_slack_land_check` read before the save wrote anything, so a moved branch refuses before any
    write. A push that fails reports the commit and the file the save left in the worktree."""
    if base is None:
        base, err = _slack_land_check(wt)
        if err: return None, err
    g = lambda *a: _slack_git(wt, *a)   # noqa: E731
    r = g("add", "--", rel)
    if r.returncode: return None, f"git add failed: {(r.stderr or r.stdout).strip()}"
    r = g("commit", "-q", "-m", message, "--", rel)
    if r.returncode: return None, f"git commit failed: {(r.stderr or r.stdout).strip()}"
    if g("merge-base", "--is-ancestor", base, "HEAD").returncode:
        return None, f"the commit is not one above the base {base[:12]} the save checked"
    sha = g("rev-parse", "HEAD").stdout.strip()
    r = g("push", "-q", "origin", f"HEAD:refs/heads/{LAND}")
    if r.returncode:
        return None, (f"the push to {LAND} failed: {(r.stderr or r.stdout).strip()}; the save left commit {sha} "
                      f"in the hub worktree {wt} with {rel} written there, and the local file written")
    return sha, None

def _slack_registry_data():
    try: data = json.loads(slack_registry_path().read_text())
    except (OSError, ValueError): data = {}
    return data if isinstance(data, dict) else {}

def _slack_record(path, sha, committed=True):
    """Record a save in the local registry, keyed by the file it saved: the landed sha of a committed save, or the last
    landed sha of a save that changed only the local file. Returns an error, or None."""
    p = slack_registry_path()
    data = _slack_registry_data()
    if sha is None:
        last = data.get(str(path)) if isinstance(data.get(str(path)), dict) else {}
        sha = last.get("sha")
    data[str(path)] = {"sha": sha, "at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                       "committed": bool(committed)}
    return _slack_atomic(p, json.dumps(data, indent=2, sort_keys=True) + "\n")

def _slack_base(paths):
    """The files a preview read, each with its size and time, so the page can tell that a file changed after its
    preview and ask again before it confirms."""
    out = {}
    for p in paths:
        try:
            st = pathlib.Path(p).stat()
            out[str(p)] = {"size": st.st_size, "mtime": int(st.st_mtime)}
        except (OSError, TypeError):
            out[str(p)] = None
    return out

def slack_files_set(body, committed=None, local=None, loop=None, name=None):
    """POST /api/slack/files for the Communication theme: validate, diff, then write. With `confirm` false it answers the diff
    of both files and writes nothing. On confirm it writes the local file atomically in the profile folder, and writes,
    commits and lands the committed file in the hub's own worktree. Returns the diff, the landed sha, and both files
    read back, so the change applies at once."""
    if not isinstance(body, dict): return {"err": "bad request"}
    if committed is None and local is None: committed, local = slack_yaml_paths()
    if local is None: return {"err": "no profile in force: SG_PROFILE and SG_PROFILES_DIR name none"}
    new_c, new_l = body.get("committed"), body.get("local")
    if not isinstance(new_c, dict) or not isinstance(new_l, dict):
        return {"err": "committed and local: each one mapping"}
    if not committed and new_c:
        return {"err": "the committed file needs the checkout: set SG_CHECKOUT"}
    try: P = slack_policy()
    except Exception as e:
        return {"err": f"the Slack rules module did not resolve: {type(e).__name__}: {e}"}
    others, siblings = [], []
    for other in sorted(pathlib.Path(local).parent.glob("*slack.local.yaml")):
        if other.resolve() == pathlib.Path(local).resolve(): continue
        siblings.append(other)
        try: doc = yaml.safe_load(other.read_text())
        except (OSError, yaml.YAMLError): continue
        if isinstance(doc, dict): others.append((other.name, doc))
    try: defects = P.validate(committed=new_c, local=new_l, others=others, local_name=pathlib.Path(local).name)
    except Exception as e:
        return {"err": f"the Slack rules module did not resolve: {type(e).__name__}: {e}"}
    if defects: return {"err": str(defects[0]), "defects": [str(d) for d in defects]}
    wt, why = slack_worktree(loop=loop, name=name)
    target = wt / SLACK_YAML_REL if wt else pathlib.Path(committed)
    files = {"committed": str(target), "local": str(local)}
    diff = {"committed": _slack_yaml_lines(target if target.exists() else committed, new_c),
            "local": _slack_yaml_lines(local, new_l)}
    if body.get("confirm") is not True:
        return {"diff": diff, "files": files, "base": _slack_base([target, committed, local] + siblings)}
    out = {"diff": diff, "files": files}
    needs_commit = bool(diff["committed"])
    base, prov_note, fresh = None, None, False
    if why:
        if needs_commit: return {"err": why, "diff": diff, "files": files}
    else:
        # No half save: the worktree the land needs is prepared and checked clean before either file is written. The
        # committed diff is then read again from the file the synced worktree holds now, so the change the owner
        # approves and the commit that lands are both measured against the branch as it stands, never a stale copy
        # (#2763). A save that lands no committed change is named in its answer, never a silent local-only write.
        perr, fatal = _slack_provision(wt, loop)
        if fatal or (perr and needs_commit): return {"err": perr, "diff": diff, "files": files}
        if perr:
            prov_note = f"the committed worktree was not prepared, so this save wrote the local file alone: {perr}"
        else:
            base_doc = _slack_read_doc(target)
            diff["committed"] = _slack_yaml_lines_doc(base_doc, new_c)
            needs_commit, fresh = bool(diff["committed"]), True
        if needs_commit:
            base, err = _slack_land_check(wt)
            if err: return {"err": err, "diff": diff, "files": files}
    err = _slack_atomic(local, _slack_yaml_text(new_l))
    if err: return {"err": err}
    if needs_commit:
        err = _slack_atomic(target, _slack_yaml_text(new_c))
        if err: return {"err": f"{err}; the local file was written", "diff": diff, "files": files}
        sha, err = _slack_land(wt, SLACK_YAML_REL, "Save the Communication Slack file", base=base)
        if err: return {"err": err, "diff": diff, "files": files, "local_applied": True}
        out["sha"] = sha
        warn = _slack_record(target, sha)
    else:
        # No committed change: the branch already holds the requested document, or the save changed the local file
        # alone. The answer says so, and the registry keeps the last landed sha and its time.
        if fresh: out["committed_unchanged"] = True
        warn = _slack_record(target, None, committed=False)
    notes = [n for n in (prov_note, f"the save state was not recorded in the registry: {warn}" if warn else None) if n]
    if notes: out["warn"] = " ".join(notes)
    try:
        out["documents"] = {"committed": yaml.safe_load(target.read_text()) if target.exists() else {},
                            "local": yaml.safe_load(pathlib.Path(local).read_text())}
    except (OSError, yaml.YAMLError):
        out["documents"] = {"committed": {}, "local": new_l}
    return out

def slack_files_post(body):
    """POST /api/slack/files: the theme's one write route. A body that carries an `op` is the editor's one operation
    (#2696); any other body is the save path's pair of documents (#2694). The old per-profile route is retired (#2701)."""
    if isinstance(body, dict) and "op" in body: return slack_docs_edit(body)
    return slack_files_set(body)

# --- Settings > Communication, the client default and its proposed writes (SPEC 2682 #2827). A client groups the projects
# of one customer in the local client map. A change of its default persona proposes one write to each project, shown as a
# table before the save; the save writes `project.persona` only in the rows that read "writes X", each one through its own
# project's save path (#2694). The rules of the rows live in the client map module and the policy module (ADR-0177); this
# route only resolves the seats and calls them (ADR-0137).
def _slack_client_plan(body, cache=0.0):
    """(plan): one row for each project of the named client, the refused rows with their text, and the committed
    documents a confirmed save writes, with the two files of each project so the save reaches it. An unknown client, an
    unnamed persona or a map that does not read answers its one error. A preview passes a short cache window so a burst
    of requests from one change shares one profile read."""
    if not isinstance(body, dict): return {"err": "bad request"}
    try: CL = slack_clients_module()
    except Exception as e: return {"err": f"the client map module did not resolve: {type(e).__name__}: {e}"}
    clients, merr = _clients_map_read()
    if merr: return {"err": merr}
    cid = _slack_text(body.get("client")).strip()
    client = next((c for c in clients if c["id"] == cid), None)
    if client is None: return {"err": f"the client map names no client {cid!r}" if cid else "client: a body that names one"}
    pid = _slack_text(body.get("persona")).strip()
    if not pid: return {"err": "persona: a body that names one persona id"}
    name = _slack_text(body.get("persona_name")).strip()
    if not name:
        personal = client.get("default_persona") or {}
        name = personal.get("name") if personal.get("id") == pid else ""
    name = name or pid
    try: P = slack_policy()
    except Exception as e: return {"err": f"the Slack rules module did not resolve: {type(e).__name__}: {e}"}
    roster, rerr = _slack_roster_checked(cache)
    if rerr: return {"err": rerr}   # a roster the hub never read is no empty client: a save never reports over it
    seats = {s["id"]: s for s in roster}
    projects = {sid: {"label": s["label"], "committed": s["committed"], "local": s["local"],
                      "committed_path": str(s["committed_path"]) if s["committed_path"] else None,
                      "local_path": str(s["local_path"]) if s["local_path"] else None,
                      "loop": s["checkout"], "slug": s["id"]}
                for sid, s in seats.items()}
    plan = CL.proposal(P, client, projects, pid, name)
    writes = [{**w, **{k: projects[w["project"]][k] for k in ("committed_path", "local_path", "loop", "slug")}}
              for w in plan["writes"] if w["project"] in projects]
    refused = [{"project": r["project"], "label": r.get("label") or r["project"], "result": r["result"]}
               for r in plan["rows"] if r["action"] == CL.REFUSED]
    return {"err": None, "client": client["id"], "persona": pid, "persona_name": name,
            "rows": plan["rows"], "writes": writes, "refused": refused, "partial": bool(refused),
            "proposal_saved": False}


def slack_clients_preview(body):
    """POST /api/slack/clients without `confirm`: the table of proposed writes and nothing written. The roster read is
    cached for a few seconds, so a change of the default never fans out one profile read per request."""
    return _slack_client_plan(body, cache=PREVIEW_ROSTER_SECONDS)


def slack_clients_save(body):
    """POST /api/slack/clients: the preview when `confirm` is not true; on confirm, each row that reads "writes X" writes
    `project.persona` through that project's own save path, and every other row writes nothing."""
    plan = _slack_client_plan(body)
    if plan.get("err") or body.get("confirm") is not True: return plan
    saved, errors = [], []
    for w in plan["writes"]:
        local = w["local_path"]
        if not local: errors.append(f"{w['project']}: no profile file to save through"); continue
        out = slack_files_set({"committed": w["committed"], "local": _slack_read_doc(local), "confirm": True},
                              committed=w["committed_path"], local=local, loop=w["loop"], name=w["slug"])
        if out.get("err"): errors.append(f"{w['project']}: {out['err']}")
        else: saved.append({"project": w["project"], "sha": out.get("sha")})
    _slack_roster_clear()   # a write changed a project's files, so the next preview reads the roster fresh
    partial = bool(plan.get("refused")) or bool(errors)
    plan = {**plan, "saved": saved, "partial": partial, "proposal_saved": not partial}
    if errors: plan["err"] = "; ".join(errors)
    return plan


def slack_clients_post(body):
    """POST /api/slack/clients: the client default's preview or save, one route (#2827)."""
    if isinstance(body, dict) and body.get("confirm") is True: return slack_clients_save(body)
    if isinstance(body, dict) and body.get("save") is True: return slack_clients_save(body)
    return slack_clients_preview(body)

# --- Settings > Communication, the editor of workspaces and channels (SPEC 2682 #2696). The theme hands one operation
# over the two raw documents, and the hub applies it: it places a workspace in the file its class names, refuses a guest
# id in the committed file and a second guest workspace, and returns the documents the save path (#2694) then writes.
# The view holds no rule (ADR-0137) and the policy module holds the refusal text (ADR-0177).
SLACK_WS_FIELDS = ("id", "label", "class", "app", "language", "persona")
SLACK_CH_FIELDS = ("id", "name", "mode", "language", "persona", "roles", "announce", "reply")
# The roles and members editor (#2699): a role carries its id, its actions and its deploy list, and it lives in the
# committed file; a member carries its Slack workspace, channel, role and user ids, and it lives in the local file
# alone (ADR-0177). The file a row lives in is a placement rule here; the value's own rule stays in the policy module,
# which validates the edited documents below.
SLACK_ROLE_FIELDS = ("id", "actions", "deploy")
SLACK_MEMBER_FIELDS = ("workspace", "channel", "role", "users")
ROLE_IN_COMMITTED = "the roles of the project live in the committed file"
MEMBER_IN_LOCAL = "the members of a role live in the local file"
SLACK_GUEST_FIELDS = ("speak", "transcribe", "voice_reply", "poll", "draft_for", "send_grant")
SLACK_POLL_FIELDS = ("mode", "fast", "slow", "quiet_after", "thread_open", "floor", "ceiling", "factor", "idle_window")
SECOND_GUEST_SAY = "second guest workspace is refused"   # the phrase the refusal matches when the module names no reason

def _slack_doc_name(doc, committed, local):
    return "slack.yaml" if doc == "committed" else pathlib.Path(local).name if local else "slack.local.yaml"

def _slack_siblings(local):
    """The other projects' local files beside this one, as `(name, document)`: the second-guest rule is summed over the
    machine. A file the reader cannot open is no answer here; `squad slack check` names it."""
    out = []
    if not local: return out
    for other in sorted(pathlib.Path(local).parent.glob("*slack.local.yaml")):
        if other.resolve() == pathlib.Path(local).resolve(): continue
        try: doc = yaml.safe_load(other.read_text())
        except (OSError, yaml.YAMLError): continue
        if isinstance(doc, dict): out.append((other.name, doc))
    return out

def _slack_defects(P, committed, local, local_name, others):
    """Every defect of the two documents, as the policy module names them. A module that raises answers its own
    exception to the caller, which names it: a failed check never passes for a clean file."""
    return list(P.validate(committed=committed, local=local, others=others, local_name=local_name))


def _slack_key_shape(key):
    """A key path with every row index removed: `workspaces[0].channels[1].speak` reads `workspaces[].channels[].speak`,
    so a defect that keeps its row keeps its identity when the edit shifts the index of that row."""
    return re.sub(r"\[\d+\]", "[]", key)

def _slack_split_defects(before, after):
    """(new, held): the defects the edit added, and the ones the files already carried. A pre-existing defect is matched
    by its file, its row-free key and its reason, and each `before` defect covers one `after` defect, so an edit that
    inserts or moves a row never blames a defect it did not cause (issue #2696)."""
    spare = {}
    for d in before:
        shape = (d.file, _slack_key_shape(d.key), d.reason)
        spare[shape] = spare.get(shape, 0) + 1
    new, held = [], []
    for d in after:
        shape = (d.file, _slack_key_shape(d.key), d.reason)
        if spare.get(shape, 0) > 0:
            spare[shape] -= 1
            held.append(d)
        else:
            new.append(d)
    return new, held

def _slack_rows_at(doc, path):
    """The row a path names: `["workspaces", i, "channels", j]`. A missing row answers None, never an exception."""
    node = doc
    for step in path:
        if isinstance(step, int):
            if not isinstance(node, list) or not 0 <= step < len(node): return None
            node = node[step]
        else:
            if not isinstance(node, dict): return None
            node = node.get(step)
    return node

def _slack_op_field(path):
    return str(path[-1]) if path else ""

def _slack_key(path):
    """A key path as the validator names it: `workspaces[0].channels[0].speak`."""
    out = ""
    for step in path:
        out += f"[{step}]" if isinstance(step, int) else (f".{step}" if out else str(step))
    return out

def _slack_ws_class(doc, index):
    ws = _slack_rows_at(doc, ["workspaces", index])
    return str(ws.get("class", "owned")) if isinstance(ws, dict) else ""

def _slack_permit(P, documents, doc, path, field, value):
    """(ok, error): an editor field belongs to the document its row lives in, and a guest field belongs to a guest
    channel alone. The class is read from the document the path names, never from the other file at the same index.
    The key the error names is the one the owner edited, and the policy module decides the value (ADR-0177)."""
    committed, local = documents["committed"], documents["local"]
    key = _slack_key(path)
    if path[:1] != ["workspaces"]:
        return False, "workspaces: an editor path starts at workspaces"
    if len(path) >= 2 and isinstance(path[1], int):
        if len(path) >= 3 and path[2] == "channels":
            if field in set(SLACK_WS_FIELDS) - set(SLACK_CH_FIELDS):
                return False, f"{_slack_doc_name(doc, committed, local)}: {key}: {field} belongs to a workspace"
            if field not in SLACK_CH_FIELDS + SLACK_GUEST_FIELDS and not field.startswith("poll."):
                return False, f"{_slack_doc_name(doc, committed, local)}: {key}: unknown field {field!r}"
            if field.startswith("poll.") and field.split(".", 1)[1] not in SLACK_POLL_FIELDS:
                return False, f"{_slack_doc_name(doc, committed, local)}: {key}: unknown poll field {field.split('.', 1)[1]!r}"
            if field in SLACK_GUEST_FIELDS or field.startswith("poll."):
                ws_row = _slack_rows_at(documents[doc], path[:2])
                if not isinstance(ws_row, dict):
                    return False, f"{_slack_doc_name(doc, committed, local)}: {key}: the workspace does not exist"
                ch_row = _slack_rows_at(documents[doc], path[:4]) if len(path) >= 4 else None
                # The channel's own class wins over its workspace's (#2770), so a guest channel inside an owned
                # workspace takes the guest fields too.
                cls = P.channel_class(ws_row, ch_row if isinstance(ch_row, dict) else None)
                if cls != "guest":
                    return False, f"{_slack_doc_name(doc, committed, local)}: {key}: {field} belongs to a guest channel"
                if doc != "local":
                    return False, f"{_slack_doc_name(doc, committed, local)}: {key}: the guest channel belongs in the local file"
            return _slack_value(P, documents, doc, path, field, value, committed, local)
        if len(path) == 3 and field not in SLACK_WS_FIELDS:
            return False, f"{_slack_doc_name(doc, committed, local)}: {key}: unknown field {field!r}"
    return _slack_value(P, documents, doc, path, field, value, committed, local)


def _slack_value(P, documents, doc, path, field, value, committed, local):
    """The policy module's answer for one value: (True, None), or (False, the refusal it names)."""
    hit = P.validate_field(documents, doc, path, field, value)
    if not hit:
        return True, None
    return False, f"{_slack_doc_name(doc, committed, local)}: {hit[0]}: {hit[1]}"


def _slack_channel_row_of(doc, ws_id, ch_id):
    """The channel row a document holds under one Slack id, or None. The row is found by the id, never by its place in
    the list, so a list that changed order answers the same row (SPEC 2682 #2844)."""
    for _, ws in _slack_dicts(doc.get("workspaces")):
        if str(ws.get("id")) != str(ws_id):
            continue
        for _, ch in _slack_dicts(ws.get("channels")):
            if str(ch.get("id")) == str(ch_id):
                return ch
    return None


def _slack_field_hits(docs, ch_id):
    """Every row of both documents that carries one Slack channel id: `(doc, workspace index, channel index, workspace
    row, channel row)`, the committed file first. A channel id is the address; the list's order is never one."""
    out = []
    for doc in ("committed", "local"):
        for wi, ws in _slack_dicts(docs[doc].get("workspaces")):
            for ci, ch in _slack_dicts(ws.get("channels")):
                if str(ch.get("id")) == str(ch_id):
                    out.append({"doc": doc, "wi": wi, "ci": ci, "ws": ws, "ch": ch})
    return out


def _slack_field_value(field, value):
    """(value, reason): the shape one tree-pane value needs before the policy module sees it. A list field takes a list
    of names or the raw text the owner typed, which the hub splits; the voice reply is a boolean; a plain field is a
    string. The format rule stays here, never in the view (ADR-0137)."""
    if field in ("roles", "announce", "draft_for"):
        if isinstance(value, str):
            value = [x for x in re.split(r"[,\s]+", value) if x]
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            return None, f"{field} is a list of names"
        return value, None
    if field == "voice_reply":
        if not isinstance(value, bool):
            return None, "voice_reply is true or false"
        return value, None
    if field == "poll" and isinstance(value, dict):
        return value, None
    if not isinstance(value, str):
        return None, f"{field} is a string"
    return value, None


def _slack_place_field(P, op, docs, local_name):
    """One tree-pane edit: the operation names the channel by its Slack id and the field it writes, and the hub finds the
    row that carries that id. A delivery field (`SLACK_GUEST_FIELDS` and every `poll.*`) writes the local file, where a
    `send_grant` is valid; a presentation field writes the file that holds the channel row. A local row that names the
    channel yet writes none of its fields is created only when a delivery field needs it, so the row it writes is the
    row that carries the id. Returns (error, changed)."""
    ch_id, field, value = _slack_text(op.get("channel")).strip(), _slack_text(op.get("field")), op.get("value")
    if not ch_id:
        return "the operation names the Slack id of the channel", False
    if not field:
        return f"the channel {ch_id}: the operation names the field it writes", False
    hits = _slack_field_hits(docs, ch_id)
    if not hits:
        return f"the channel {ch_id} is in neither file", False
    committed_hit = next((h for h in hits if h["doc"] == "committed"), None)
    local_hit = next((h for h in hits if h["doc"] == "local"), None)
    delivery = field in SLACK_GUEST_FIELDS or field == "poll" or field.startswith("poll.")
    doc = "local" if delivery or committed_hit is None else "committed"
    name = _slack_doc_name(doc, None, local_name)
    if field not in SLACK_CH_FIELDS + SLACK_GUEST_FIELDS and field != "poll" and not field.startswith("poll."):
        return f"{name}: the channel {ch_id}: unknown field {field!r}", False
    if field.startswith("poll.") and field.split(".", 1)[1] not in SLACK_POLL_FIELDS:
        return f"{name}: the channel {ch_id}: unknown poll field {field.split('.', 1)[1]!r}", False
    ok_value, why = _slack_field_value(field, value)
    if why:
        return f"{name}: the channel {ch_id}: {why}", False
    if delivery:
        # The channel's own class wins over its workspace's, so a guest channel inside an owned workspace takes the
        # delivery fields too (#2770). The address is the local row that carries the id, never an index.
        cls = P.channel_class((local_hit or committed_hit)["ws"], local_hit["ch"] if local_hit else None)
        if cls != "guest":
            return f"{name}: the channel {ch_id}: {field} belongs to a guest channel", False
    if doc == "local":
        ws_id = _slack_text((local_hit or committed_hit)["ws"].get("id")).strip()
        if not ws_id:
            return f"{name}: the workspace carries no id, so the local file cannot address its channel", False
        target = docs["local"]
        if not isinstance(target.get("workspaces"), list):
            target["workspaces"] = []
        wi = next((i for i, ws in _slack_dicts(target.get("workspaces")) if str(ws.get("id")) == ws_id), None)
        if wi is None:
            target["workspaces"].append({"id": ws_id})
            wi = len(target["workspaces"]) - 1
        ws_row = _slack_rows_at(target, ["workspaces", wi])
        if not isinstance(ws_row.get("channels"), list):
            ws_row["channels"] = []
        ci = next((i for i, ch in _slack_dicts(ws_row.get("channels")) if str(ch.get("id")) == ch_id), None)
        if ci is None:
            ws_row["channels"].append({"id": ch_id})
            ci = len(ws_row["channels"]) - 1
    else:
        wi, ci = committed_hit["wi"], committed_hit["ci"]
    path = ["workspaces", wi, "channels", ci, field]
    row = _slack_rows_at(docs[doc], path[:-1])
    if not isinstance(row, dict):
        return f"{name}: the channel {ch_id}: the edited row does not exist", False
    hit = P.validate_field(docs, doc, path, field, ok_value)
    if hit:
        return f"{name}: {hit[0]}: {hit[1]}", False
    if field.startswith("poll."):
        poll = dict(row.get("poll")) if isinstance(row.get("poll"), dict) else {}
        poll[field.split(".", 1)[1]] = ok_value
        row["poll"] = poll
    elif ok_value == "" and field in ("language", "persona", "app"):
        row.pop(field, None)   # an empty select inherits, so the key leaves the file
    elif field in ("roles", "announce", "draft_for") and not ok_value:
        row.pop(field, None)   # an empty list leaves no key behind
    else:
        row[field] = ok_value
    return None, True

def _slack_row_value_ok(path, value, field):
    """(ok, message, value): the shape one edited value needs before the policy module sees it. A list field takes a
    list of names, or the raw field the owner typed, which the hub splits: the format rule stays here, never in the
    view (ADR-0137). A plain field takes a string."""
    key = _slack_key(path)
    if field in ("actions", "deploy", "users"):
        if isinstance(value, str):
            value = [x for x in re.split(r"[,\s]+", value) if x]
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            return False, f"{key}: {field} is a list of names", value
    elif not isinstance(value, str):
        return False, f"{key}: {field} is a string", value
    return True, None, value


def _slack_key_taken(rows, row, key, skip=None):
    """Whether another row of the list carries the same value under `key`; `skip` is the row this operation edits. A
    blank value is never a duplicate, because a row with no key is refused by its own rule."""
    want = str(row.get(key) or "").strip()
    if not want: return False
    for i, other in enumerate(rows):
        if i == skip or not isinstance(other, dict): continue
        if str(other.get(key) or "").strip() == want: return True
    return False


def _slack_place_rows(kind, path, value, doc, fields, file_name, root, blank, id_required=False, unique_key=None):
    """One operation over a plain row list: a role in the committed file, or a member in the local one (#2699). The
    Add control starts `blank`, a field outside `fields` is refused, and an edited value's shape is checked before
    the policy module judges its content. The row is built from a fresh mapping, so a refused operation never rewrites
    the request it was handed. `unique_key` is the row's key, and no two rows share it. The list is created by an Add
    alone, so a refused edit never leaves an empty root key behind. Returns (error, changed, touched), where `touched`
    is the index of the row the operation leaves in place, and None for a Remove."""
    rows = doc.get(root)
    if kind == "add":
        if path != [root]:
            return f"{file_name}: {root}: the operation adds a row of {root}", False, None
        if not isinstance(value, dict):
            return f"{file_name}: {root}: the added row is a mapping", False, None
        unknown = [k for k in value if k not in fields]
        if unknown:
            return f"{file_name}: {root}: {root} has no field {unknown[0]!r}", False, None
        if rows is not None and not isinstance(rows, list):
            return f"{file_name}: {root}: the {root} are a list", False, None
        row = dict(blank)
        for field in fields:
            if field not in value: continue
            ok, why, checked = _slack_row_value_ok([root, len(rows or ()), field], value[field], field)
            if not ok: return f"{file_name}: {why}", False, None
            row[field] = checked
        if id_required and not str(row.get("id") or "").strip():
            return f"{file_name}: {root}: the role names its id", False, None
        if unique_key and _slack_key_taken(rows or (), row, unique_key):
            return (f"{file_name}: {root}: the {unique_key} {str(row.get(unique_key))!r} is already taken",
                    False, None)
        if rows is None:
            doc[root] = rows = []
        rows.append(row)
        return None, True, len(rows) - 1
    if not isinstance(rows, list):
        return f"{file_name}: {root}: the {root} are a list", False, None
    if kind == "remove":
        if len(path) != 2 or not isinstance(path[1], int) or not 0 <= path[1] < len(rows):
            return f"{file_name}: the removed row does not exist", False, None
        rows.pop(path[1])
        return None, True, None
    if len(path) != 3 or not isinstance(path[1], int) or not 0 <= path[1] < len(rows):
        return f"{file_name}: the edited row does not exist", False, None
    field = str(path[2])
    if field not in fields:
        return f"{file_name}: {_slack_key(path[:-1])}: {root} has no field {field!r}", False, None
    ok, why, checked = _slack_row_value_ok(path, value, field)
    if not ok: return f"{file_name}: {why}", False, None
    if id_required and field == "id" and not checked.strip():
        return f"{file_name}: {_slack_key(path)}: the role names its id", False, None
    row = rows[path[1]]
    if not isinstance(row, dict):
        return f"{file_name}: {_slack_key(path[:-1])}: the row is a mapping", False, None
    if unique_key and field == unique_key and _slack_key_taken(rows, {field: checked}, unique_key, skip=path[1]):
        return f"{file_name}: {_slack_key(path)}: the {unique_key} {checked!r} is already taken", False, None
    row[field] = checked
    return None, True, path[1]


def _slack_member_row_check(docs, index, local_name):
    """A member names the channel of the Slack workspace it names. The two ids are read from both documents, so a row
    never pairs one workspace with another workspace's channel. The check reads the one row the operation leaves in
    place, never every row: a stale sibling never blocks an edit or a removal of another row (issue #2699)."""
    row = _slack_rows_at(docs["local"], ["members", index])
    if not isinstance(row, dict): return None
    wid = str(row.get("workspace") or "").strip()
    cid = str(row.get("channel") or "").strip()
    if wid and not cid:
        return f"{local_name}: members[{index}].channel: a member names its channel"
    if not wid: return None
    teams = {}
    for doc in (docs["committed"], docs["local"]):
        for _, ws in _slack_dicts(doc.get("workspaces")):
            team = str(ws.get("id") or "").strip()
            if team:
                teams.setdefault(team, set()).update(
                    str(ch.get("id")).strip() for _, ch in _slack_dicts(ws.get("channels")) if ch.get("id"))
    if wid not in teams:
        return f"{local_name}: members[{index}].workspace: the workspace does not exist"
    if cid not in teams[wid]:
        return f"{local_name}: members[{index}].channel: the channel is not in workspace {wid}"
    return None


def _slack_place(P, op, docs, local_name):
    """Apply one operation to the two documents, and answer an error when the class places the row elsewhere or the
    operation breaks a policy rule. Returns (error, changed)."""
    kind, doc = op.get("kind"), op.get("doc")
    path, value = op.get("path"), op.get("value")
    if kind == "field":
        # The tree pane addresses a channel by its Slack id and lets the hub name the file: a delivery field writes the
        # local file, a presentation field the file that holds the row (SPEC 2682 #2844).
        return _slack_place_field(P, op, docs, local_name)
    if kind not in ("add", "set", "remove") or doc not in ("committed", "local"):
        return "the operation names add, set, remove or field and the file it edits", False
    if not isinstance(path, list) or not path or path[0] not in ("workspaces", "roles", "members"):
        return "the operation names a path from workspaces, roles or members", False
    if path[0] == "roles":
        if doc != "committed":
            return f"slack.yaml: {ROLE_IN_COMMITTED}", False
        err, changed, _touched = _slack_place_rows(kind, path, value, docs["committed"], SLACK_ROLE_FIELDS,
                                                   "slack.yaml", "roles", {"id": "", "actions": []},
                                                   id_required=True, unique_key="id")
        return err, changed
    if path[0] == "members":
        if doc != "local":
            return f"{local_name}: {MEMBER_IN_LOCAL}", False
        err, changed, touched = _slack_place_rows(kind, path, value, docs["local"], SLACK_MEMBER_FIELDS, local_name,
                                                  "members",
                                                  {"workspace": "", "channel": "", "role": "", "users": []})
        if err: return err, False
        if touched is None: return None, changed
        return _slack_member_row_check(docs, touched, local_name), changed
    target = docs[doc]
    if kind == "add":
        if not isinstance(value, dict):
            return "the added row is a mapping", False
        if path == ["workspaces"]:
            cls = str(value.get("class", "owned"))
            if cls not in ("owned", "guest"):
                return "slack.yaml: workspaces[].class: the class is owned or guest", False
            if not value.get("id"):
                return "slack.yaml: workspaces[].id: a workspace names its team id", False
            placed = "local" if cls == "guest" else "committed"
            if placed == "local": value.pop("app", None)
            docs[placed].setdefault("workspaces", []).append(value)
            return None, True
        if len(path) == 3 and path[2] == "channels":
            if not value.get("id"):
                return f"{local_name}: workspaces[].channels[].id: a channel names its Slack id", False
            ws = _slack_rows_at(target, ["workspaces", path[1]])
            if not isinstance(ws, dict):
                return "workspaces: the workspace does not exist", False
            value.setdefault("mode", "listen")
            ws.setdefault("channels", []).append(value)
            return None, True
        return "the operation adds a workspace or a channel", False
    if kind == "remove":
        parent = _slack_rows_at(target, path[:-1])
        if not isinstance(parent, list) or not isinstance(path[-1], int) or not 0 <= path[-1] < len(parent):
            return "the removed row does not exist", False
        parent.pop(path[-1])
        return None, True
    # set: the class decides the file, and the guest fields belong to a guest channel
    field = _slack_op_field(path)
    if field == "class":
        row = _slack_rows_at(target, path[:-1])
        if not isinstance(row, dict):
            return f"{_slack_doc_name(doc, None, local_name)}: {_slack_key(path)}: the workspace does not exist", False
        hit = P.validate_field(docs, doc, path, field, value)
        if hit:
            return f"{_slack_doc_name(doc, None, local_name)}: {hit[0]}: {hit[1]}", False
        cls = str(value)
        if doc == "committed" and cls == "guest":
            rows = target.get("workspaces")
            ws = _slack_rows_at(target, ["workspaces", path[1]])
            if not isinstance(rows, list) or not isinstance(path[1], int) or not isinstance(ws, dict) \
                    or not 0 <= path[1] < len(rows):
                return f"slack.yaml: {_slack_key(path)}: the workspace does not exist", False
            rows.pop(path[1])
            ws.pop("app", None)
            ws["class"] = "guest"
            docs["local"].setdefault("workspaces", []).append(ws)
            return None, True
        if doc == "local" and cls == "owned":
            return f"{local_name}: {_slack_key(path)}: {P.GUEST_TEAM_REASON}", False
        row["class"] = cls
        return None, True
    ok, err = _slack_permit(P, docs, doc, path, field, value)
    if not ok: return err, False
    row = _slack_rows_at(target, path[:-1])
    if not isinstance(row, dict):
        return "the edited row does not exist", False
    if field.startswith("poll."):
        poll = row.get("poll")
        if not isinstance(poll, dict): poll = {}
        poll[field.split(".", 1)[1]] = value
        row["poll"] = poll
    elif value == "" and field in ("language", "persona", "app"):
        row.pop(field, None)   # the empty choice in the select inherits, so the key leaves the file
    else:
        row[field] = value
    return None, True

def slack_docs_edit(body, committed=None, local=None):
    """POST /api/slack/files: one edit of a workspace or a channel over the two raw documents. A body that omits
    `committed` or `local` reads that document from its own path, so the edit answers a document of this project; a body
    that sends a document of the wrong shape is refused. The answer carries the two documents it used, or the one
    refusal the owner repairs. Nothing is written here; the class places the row in the file it belongs to, and the
    policy module decides every refusal (ADR-0177)."""
    if not isinstance(body, dict): return {"err": "bad request"}
    op = body.get("op")
    if not isinstance(op, dict): return {"err": "the operation is a mapping"}
    if committed is None and local is None: committed, local = slack_yaml_paths()
    local_name = body.get("local_name") or (pathlib.Path(local).name if local else "slack.local.yaml")
    try: P = slack_policy()
    except Exception as e:
        return {"err": f"the Slack rules module did not resolve: {type(e).__name__}: {e}"}
    docs = {"committed": body.get("committed"), "local": body.get("local")}
    if docs["committed"] is None or docs["local"] is None:
        try: read_c, read_l, _unread = P.documents(committed, local)
        except Exception as e:
            return {"err": f"the Slack rules module answered {type(e).__name__}: {e}"}
        if docs["committed"] is None: docs["committed"] = read_c
        if docs["local"] is None: docs["local"] = read_l
    if not isinstance(docs["committed"], dict) or not isinstance(docs["local"], dict):
        return {"err": "committed and local: each one mapping"}
    others = _slack_siblings(local)
    try:
        before = _slack_defects(P, docs["committed"], docs["local"], local_name, others)
    except Exception as e:
        return {"err": f"the Slack rules module answered {type(e).__name__}: {e}"}
    # The draft is a copy, never a YAML round trip: the round trip can rewrite a scalar's type, and the edit keeps every
    # value exactly as the document holds it.
    draft = copy.deepcopy(docs)
    err, changed = _slack_place(P, op, draft, local_name)
    if err: return {"err": err, "committed": docs["committed"], "local": docs["local"]}
    try:
        after = _slack_defects(P, draft["committed"], draft["local"], local_name, others)
    except Exception as e:
        return {"err": f"the Slack rules module answered {type(e).__name__}: {e}"}
    # The save path writes both documents the editor answers with, so every defect either file carries blocks the edit.
    # The edit's own new defects are named apart from the ones the files already carried: comparing texts alone would
    # let an edit through while the documents stay invalid (ADR-0177 decision 4).
    ours = {"slack.yaml", local_name}
    new, held = _slack_split_defects(before, after)
    held = [d for d in held if d.file in ours]
    blocking = new + held
    if blocking:
        second = next((d for d in blocking if _slack_second_guest(P, d)), None)
        return {"err": f"One guest Slack workspace at a time: {second}" if second else str(blocking[0]),
                "defects": [str(d) for d in blocking], "new_defects": [str(d) for d in new],
                "held_defects": [str(d) for d in held],
                "committed": docs["committed"], "local": docs["local"]}
    # The tree of the staged documents, through the policy module, so the page redraws the Now block before the save
    # (#2846). The files are the ones the edit was handed, so no chip moves under the owner's eyes.
    tree = _slack_tree_docs(P, draft["committed"], draft["local"], _slack_name(committed, "slack.yaml"), local_name)
    return {"committed": draft["committed"], "local": draft["local"], "err": None, "defects": [],
            "new_defects": [], "held_defects": [],
            "documents": {"committed": draft["committed"], "local": draft["local"]}, "tree": tree}

def _slack_second_guest(P, defect):
    """Whether one defect is the second-guest refusal. The policy module names its own reason, and a module that names
    none still answers to the phrase the validator uses, so the mockup sentence never depends on one attribute."""
    reason = getattr(P, "SECOND_GUEST_REASON", None) or SECOND_GUEST_SAY
    return reason in defect.reason

