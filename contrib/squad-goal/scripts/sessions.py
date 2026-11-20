"""The Terminals list (#833): the one reader of the local session registry, the protected transcript reader and the one
redaction step. The hub's routes call it; nothing else reads a session record, a token or a transcript.
"""
import datetime, importlib.util, json, os, pathlib, re, stat, subprocess, sys, time

_PROCS = []

def _procs():
    """squad/procs.py, the one process reader, loaded by path: the repository this script sits in first, as caps.sh reads it,
    then SG_SQUAD, SG_CHECKOUT, else the tree it runs in. That file imports nothing from squad, so it loads alone under any python3 (#1508)."""
    if not _PROCS:
        name = "squad_procs"
        if hasattr(sys.modules.get(name), "snapshot"): _PROCS.append(sys.modules[name])   # never a foreign stub another loader left
        else:
            roots = [r for r in (str(pathlib.Path(__file__).resolve().parents[3]), os.environ.get("SG_SQUAD"), os.environ.get("SG_CHECKOUT"),
             *map(str, (pathlib.Path.cwd(), *pathlib.Path.cwd().parents))) if r]   # a copy outside the repo: the tree it runs in
            root = next((r for r in roots if (pathlib.Path(r) / "squad" / "procs.py").is_file()), roots[-1])
            spec = importlib.util.spec_from_file_location(name, pathlib.Path(root) / "squad" / "procs.py")
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod   # a dataclass in the module reads its own module by name while it loads
            spec.loader.exec_module(mod)
            _PROCS.append(mod)
    return _PROCS[0]

def _snapshot(pids):
    """{pid: Proc} for the pids; {} when the process table cannot be read."""
    mod = _procs()
    try: return mod.snapshot(pids)
    except mod.ReadError: return {}
import session_card

ADDRESS = pathlib.Path(__file__).resolve().parent / "address.sh"
SOCKET_DIRS = ("/tmp/cc-socks",)   # where Claude Code binds a session socket (docs/design/session-wire-protocol.md)
STATUSES = ("busy", "idle", "waiting")
SID = re.compile(r"[A-Za-z0-9-]{1,64}")
TAIL_BYTES = 256 * 1024
MAX_TAIL_BYTES = 16 * 1024 * 1024   # a session's own lines can be megabytes: the window grows until it holds a page (#1073)
PAGE_TURNS = 30                     # the text turns a page aims for; a tool line rides along and never fills the page
TURN_LIMIT = 200
PREVIEW_CHARS = 200
UID = os.getuid()
CREDENTIAL = re.compile(r"sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{16,}|Bearer\s+[A-Za-z0-9._~+/=-]+")
_LOGGED = set()

def claude_home():
    """The Claude folder this process reads: HOME when the environment names one, else the user's. Python 3.12 caches
    the `~` expansion, so a process that changes HOME still read the first one it saw (#1073)."""
    h = os.environ.get("HOME")
    return (pathlib.Path(h) if h else pathlib.Path.home()) / ".claude"

def redact(text, secrets=()):
    for s in secrets:
        if s: text = text.replace(s, "[redacted]")
    return CREDENTIAL.sub("[redacted]", text)

def _log(line, secrets=(), once=False):
    line = redact(line, secrets)
    if once and line in _LOGGED: return
    _LOGGED.add(line); print(line, file=sys.stderr, flush=True)

def _open_file(dir_fd, name):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_uid != UID or st.st_mode & 0o022:
        os.close(fd); raise PermissionError(f"{name} is not a private regular file")
    return fd

def _read_under_home(parts, limit=None, end=None, with_mtime=False):
    """Bytes of a file under the hub's Claude folder, walked by descriptors: no link followed at any level, owner and mode
    checked on the descriptor opened. The `limit` bytes before `end` (the file's end when None). Also the offset of the first
    byte, and with `with_mtime` the file's mtime from the same descriptor."""
    *dirs, name = parts
    dfd = os.open(claude_home(), os.O_RDONLY | os.O_DIRECTORY)
    try:
        for d in dirs:
            nfd = os.open(d, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dfd)
            os.close(dfd); dfd = nfd
        fd = _open_file(dfd, name)
    finally:
        os.close(dfd)
    try:
        st = os.fstat(fd)
        size = st.st_size
        stop = size if end is None else min(end, size)
        start = max(0, stop - limit) if limit is not None else 0
        os.lseek(fd, start, os.SEEK_SET)
        chunks, left = [], stop - start
        while left > 0 and (chunk := os.read(fd, min(65536, left))):
            chunks.append(chunk); left -= len(chunk)
        return (b"".join(chunks), start, int(st.st_mtime)) if with_mtime else (b"".join(chunks), start)
    finally:
        os.close(fd)

def _secret(sid):
    try: return _read_under_home(["sessions", f"{sid}.token"])[0].decode(errors="replace").strip()
    except OSError: return ""

def _shape_ok(o):
    return (isinstance(o, dict) and isinstance(o.get("version"), str) and type(o.get("pid")) is int and o["pid"] > 0
            and type(o.get("startedAt")) is int and isinstance(o.get("sessionId"), str) and SID.fullmatch(o["sessionId"]) is not None
            and isinstance(o.get("cwd"), str) and os.path.isabs(o["cwd"]) and isinstance(o.get("name"), str) and len(o["name"]) <= 200
            and o.get("status") in STATUSES and isinstance(o.get("messagingSocketPath"), str))

def _records():
    folder = claude_home() / "sessions"
    try: names = sorted(os.listdir(folder))
    except OSError: return []
    out = []
    for name in names:
        if not name.endswith(".json"): continue
        try: raw = _read_under_home(["sessions", name])[0]
        except OSError: _log(f"hub: session record {name} refused (not a private file)", once=True); continue
        try: obj = json.loads(raw)
        except ValueError: obj = None
        if not _shape_ok(obj): _log(f"hub: session record {name} has an unknown shape, left out", once=True); continue
        out.append({**obj, "socket": obj["messagingSocketPath"]})
    return out

def _socket_ok(path):
    try: st = os.lstat(path)
    except OSError: return False
    if not stat.S_ISSOCK(st.st_mode) or st.st_uid != UID: return False
    allowed = {os.path.realpath(d) for d in (*SOCKET_DIRS, claude_home() / "sessions" / "sockets")}
    return os.path.realpath(os.path.dirname(path)) in allowed

def _proc_start(pid):
    p = _snapshot([pid]).get(pid)
    return None if p is None or p.start is None else int(p.start)

def _alive_since(pid, started, kind=None):
    try: os.kill(pid, 0)
    except OSError: return False
    start = _proc_start(pid)
    if start is None: return False
    if kind == "bg": return started // 1000 >= start - 1   # a background job claims a spare process started long before it (#1269); a reused pid starts after the record, and the same one-second slack as below covers the fractional boot
    # startedAt (ms) is written a few seconds after the process starts. One second of slack on the early side: the record
    # names a whole second while procs builds the start from the fractional boot, so the same process may read 1 s later
    # (#2596); a reused pid still starts past the window.
    return -1 <= started // 1000 - start <= 30

def profiles_dir():
    """The profiles the address seam reads: SG_PROFILES_DIR when the runner exports it, else the global skill's own, so a
    hub started without that variable still resolves a session instead of listing none."""
    d = os.environ.get("SG_PROFILES_DIR")
    if d: return d
    home = pathlib.Path.home() / ".claude" / "skills" / "squad-goal" / "scripts" / "profiles"
    return str(home) if home.is_dir() else ""

def profiles_of(cwd):
    """The profiles whose repository the checkout at `cwd` is, resolved by the address seam (ADR-0138)."""
    env = dict(os.environ)
    if profiles_dir(): env.setdefault("SG_PROFILES_DIR", profiles_dir())
    repo = subprocess.run(["bash", "-c", '. "$1" >/dev/null 2>&1; sg_resolve_repo "$2"', "_", str(ADDRESS), cwd],
                          capture_output=True, text=True, env=env).stdout.strip()
    if not repo: return []
    rows = subprocess.run(["bash", str(ADDRESS), "--list"], capture_output=True, text=True, env=env).stdout.splitlines()
    return [slug for slug, r in (row.split("\t", 1) for row in rows if "\t" in row) if r.lower() == repo.lower()]

# A run's workspace sits under `runs/<run id>/` in its checkout, so the session's own checkout names the run that opened
# it (#1601, owner). A session outside a run folder names none.
RUN_DIR = re.compile(r"(?:^|/)runs/(run-\d{8}-\d{6}-[0-9a-f]{6})(?:/|$)")

def run_id_of(cwd):
    """The run that opened a session in `cwd`, from the run's own folder in its path; "" when the folder names none."""
    m = RUN_DIR.search("/" + str(cwd or "").replace(os.sep, "/").lstrip("/"))
    return m.group(1) if m else ""

def read_last_turn(cwd, sid, secrets=()):
    """The text of the last turn in the session's transcript, redacted and cut to a preview, and the transcript's mtime:
    the session's real last activity (#1601, owner). (None, None) when it cannot be read."""
    if SID.fullmatch(sid) is None: return None, None
    try: raw, start, mtime = _read_under_home(["projects", re.sub(r"[^A-Za-z0-9]", "-", cwd), f"{sid}.jsonl"], limit=TAIL_BYTES, with_mtime=True)
    except OSError as e: _log(f"hub: transcript of session {sid} unreadable: {type(e).__name__}", secrets); return None, None
    lines = raw.decode(errors="replace").splitlines()[1 if start else 0:]
    last = ""
    for line in lines:
        try: turn = json.loads(line)
        except ValueError: continue
        content = (turn.get("message") or {}).get("content")
        text = content if isinstance(content, str) else " ".join(p.get("text", "") for p in content or [] if isinstance(p, dict))
        if text.strip(): last = text.strip()
    return redact(last, secrets)[:PREVIEW_CHARS], mtime

def read_last_message(cwd, sid, secrets=()):
    """The text of the last turn in the session's transcript, redacted and cut to a preview; None when it cannot be read."""
    return read_last_turn(cwd, sid, secrets)[0]

HARNESS_TAGS = ("local-command-caveat", "command-name", "command-message", "command-args", "local-command-stdout",
                "task-notification")
_ONE_ENVELOPE = re.compile(r"\s*<(%s)\b.*?</\1>" % "|".join(HARNESS_TAGS), re.S | re.I)

HARNESS_OPENER = "This session is being continued from a previous conversation"

def _harness_only(text):
    """True when the whole message is what the harness wrote into the transcript itself: a command's record, its output, a
    background task's notification, or the summary a compact hands the next context. The owner typed none of it, so it is no
    turn of the talk (owner, 2026-11-01)."""
    if text.lstrip().startswith(HARNESS_OPENER): return True
    if re.fullmatch(r"\s*/[A-Za-z][\w:-]*\s*", text): return True   # a bare slash command is the harness echoing the call
    if "<system-reminder>" in text or "<tools-used-before-summary>" in text: return True
    rest = text
    while True:
        m = _ONE_ENVELOPE.match(rest)
        if m is None: return not rest.strip()
        rest = rest[m.end():]

def _turns_of(obj):
    kind, uid = obj.get("type"), str(obj.get("uuid") or "")
    at = obj.get("timestamp") if isinstance(obj.get("timestamp"), str) else ""   # the clock the bubble shows, as WhatsApp does
    # The model that answered: the harness names it on every assistant turn of the transcript, so the bubble says who spoke
    # where the owner's says "you" (owner, 2026-11-02).
    model = (obj.get("message") or {}).get("model") if kind == "assistant" else ""
    model = model if isinstance(model, str) else ""
    # A message the harness injects (a hook's feedback, a reminder) is stored as a user turn. It is not the owner typing:
    # the panel says System, so a bubble never puts words in the owner's mouth (owner, 2026-11-01).
    owner_role = "system" if obj.get("isMeta") is True else "owner"
    content = (obj.get("message") or {}).get("content")
    if isinstance(content, str): content = [{"type": "text", "text": content}]
    out = []
    for p in content if isinstance(content, list) else []:
        if not isinstance(p, dict): continue
        text = p.get("text")
        if kind == "user" and p.get("type") == "text" and isinstance(text, str) and text:
            if _harness_only(text): continue   # the harness's own record, never a turn of the talk
            out.append((owner_role, uid, text, at, ""))
        elif kind == "assistant" and p.get("type") == "text" and isinstance(text, str) and text: out.append(("answer", uid, text, at, model))
        # A tool call is not a turn of the conversation: a working session answers with hundreds of them, and they filled
        # the page while the owner's and the AI's words never arrived (#1073). The terminal stays their record.
    return out

_PAGES = {}   # the last few pages, keyed by transcript size and mtime: the chat polls every few seconds
BIG_LINE = 512 * 1024   # a line this long is a thinking block or a tool result: it carries no turn of its own (#1073)

def _page_kept(raw, start):
    """The newest whole lines of one byte window, oldest first, within TURN_LIMIT turns."""
    lines, at = [], start
    for i, piece in enumerate(raw.split(b"\n")):
        offset, at = at, at + len(piece) + 1
        if (i == 0 and start) or not piece: continue
        if len(piece) > BIG_LINE and b'"type":"text"' not in piece and b'"type": "text"' not in piece: continue
        try: obj = json.loads(piece)
        except ValueError: continue
        if isinstance(obj, dict): lines.append((offset, _turns_of(obj)))
    kept, turns = [], 0
    for offset, found in reversed(lines):
        if kept and turns + len(found) > TURN_LIMIT: break
        kept.append((offset, found)); turns += len(found)
    kept.reverse()
    return kept

def permission_mode(cwd, sid, pid=None):
    """The permission mode the session last recorded in its transcript, else the one its own process was started with, else "".
    A sender that asserts the receiver's own mode is not held for approval: a hub that asserts none, into a session that
    bypasses, is (`docs/design/session-wire-protocol.md`; owner, 2026-11-01). The fallback matters because a session holds
    no mode line in its transcript until its first turn is written, and the owner reads the hub from the web, where a held
    message has no approval to reach it (#1081)."""
    if SID.fullmatch(sid or "") is None: return ""
    path = ["projects", re.sub(r"[^A-Za-z0-9]", "-", str(cwd)), f"{sid}.jsonl"]
    window = TAIL_BYTES
    while True:
        try: raw, start = _read_under_home(path, limit=window)
        except OSError: raw, start = b"", 0
        mode = _last_mode(raw)
        if mode or not start or window >= MAX_TAIL_BYTES: return mode or argv_mode(pid)
        window *= 2


def _last_mode(raw):
    for piece in reversed(raw.split(b"\n")):
        if b'"permissionMode"' not in piece: continue
        try: obj = json.loads(piece)
        except ValueError: continue
        mode = obj.get("permissionMode")
        if isinstance(mode, str) and mode: return mode
    return ""


ARGV_MODE = {"--dangerously-skip-permissions": "bypassPermissions", "--bypass-permissions": "bypassPermissions"}


def argv_of(pid):
    """The argv the process was started with, or (). `/proc` first: it has no column width and no external tool, so a flag
    past the `ps` column survives (a CI run cut a flag off and read the mode as empty); `ps -ww` answers elsewhere, wide."""
    try:
        raw = open(f"/proc/{int(pid)}/cmdline", "rb").read()
        if raw: return [a.decode("utf-8", "replace") for a in raw.split(b"\0") if a]
    except (OSError, ValueError):
        pass
    try:
        p = _snapshot([int(pid)]).get(int(pid))
    except (ValueError, TypeError):
        return []
    return p.command.split() if p else []


def argv_mode(pid):
    """The mode a live session's own argv names, or "". Read-only: one read of the process's argv, no signal, no write."""
    argv = argv_of(pid)
    for k, tok in enumerate(argv):
        if tok in ARGV_MODE: return ARGV_MODE[tok]
        if tok.startswith("--permission-mode="): return tok.split("=", 1)[1]
        if tok == "--permission-mode" and k + 1 < len(argv): return argv[k + 1]
    return ""

def read_conversation(cwd, sid, secrets=(), before=None):
    """One page of the session's conversation, oldest first: the newest whole lines before the byte offset `before` (the end of
    the transcript when None), within TURN_LIMIT turns. The window starts at TAIL_BYTES and doubles until the page holds
    PAGE_TURNS owner or answer turns (or MAX_TAIL_BYTES, or the start of the file): one transcript line can be megabytes, so
    a fixed window answered a handful of tool lines and no conversation at all (#1073). `next` is the older page's offset."""
    if SID.fullmatch(sid) is None: return None
    path = ["projects", re.sub(r"[^A-Za-z0-9]", "-", cwd), f"{sid}.jsonl"]
    full = claude_home().joinpath(*path)
    try: st = full.stat()
    except OSError: st = None
    key = (str(full), st.st_size if st else -1, st.st_mtime_ns if st else -1, before)
    if st and key in _PAGES: return _PAGES[key]   # the panel polls; a poll that meets no new byte re-reads nothing
    window = TAIL_BYTES
    while True:
        try: raw, start = _read_under_home(path, limit=window, end=before)
        except OSError as e: _log(f"hub: transcript of session {sid} unreadable: {type(e).__name__}", secrets); return None
        kept = _page_kept(raw, start)
        said = sum(1 for _, found in kept for role, *_ in found if role != "tool")
        if said >= PAGE_TURNS or not start or window >= MAX_TAIL_BYTES: break
        window *= 2
    out = [{"id": uid, "k": f"{offset}:{n}", "role": role, "text": redact(text, secrets), "at": at, "m": model}
           for offset, found in kept for n, (role, uid, text, at, model) in enumerate(found)]
    cursor = kept[0][0] if kept else start
    page = {"turns": out, "next": cursor or None}
    if st:
        _PAGES[key] = page
        while len(_PAGES) > 8: _PAGES.pop(next(iter(_PAGES)))
    return page


def _epoch(stamp):
    try: return datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError): return None

def _owner_turn_and_tokens(raw, start):
    """The epoch of the newest owner turn in one byte window, and the output tokens of the messages written after it, one count
    per message: a message is written as several lines that each repeat its usage. (None, None) when the window holds no turn."""
    tokens = {}
    for piece in reversed(raw.split(b"\n")[1 if start else 0:]):
        if not piece: continue
        try: obj = json.loads(piece)
        except ValueError: continue
        if not isinstance(obj, dict): continue
        if any(role == "owner" for role, *_ in _turns_of(obj)): return _epoch(obj.get("timestamp", "")), sum(tokens.values())
        msg = obj.get("message") or {}
        out = (msg.get("usage") or {}).get("output_tokens") if obj.get("type") == "assistant" else None
        if isinstance(out, int): tokens.setdefault(msg.get("id") or obj.get("uuid") or piece, out)
    return None, None

def read_activity(cwd, sid, status, secrets=(), now=None):
    """The live activity of a session: its record status, the whole seconds since its owner's last turn, and the output tokens
    its messages wrote since. Elapsed and tokens are None when the transcript holds no owner turn, never a guess."""
    unknown = {"status": status, "elapsed": None, "tokens": None}
    if SID.fullmatch(sid) is None: return unknown
    path = ["projects", re.sub(r"[^A-Za-z0-9]", "-", cwd), f"{sid}.jsonl"]
    window = TAIL_BYTES
    while True:
        try: raw, start = _read_under_home(path, limit=window)
        except OSError as e: _log(f"hub: transcript of session {sid} unreadable: {type(e).__name__}", secrets); return unknown
        since, tokens = _owner_turn_and_tokens(raw, start)
        if since is not None or not start or window >= MAX_TAIL_BYTES: break
        window *= 2
    if since is None: return unknown
    now = time.time() if now is None else now
    return {"status": status, "elapsed": max(0, int(now - since)), "tokens": tokens}

def conversation(profile, sid, before=None, profiles_of=profiles_of):
    """One page of a live session's conversation with its live activity, when the session is this profile's; None otherwise."""
    for rec, secret in live_sessions(profile, profiles_of):
        if rec["sessionId"] != sid: continue
        page = read_conversation(rec["cwd"], sid, [secret], before)
        if page is None: return None
        return {**page, "activity": read_activity(rec["cwd"], sid, rec["status"], [secret])}
    return None

def pending_card(profile, sid, profiles_of=profiles_of):
    """The question card a live session of this profile raised and has not had answered, or None (#1093). A session blocked on
    its question writes nothing after it, so the transcript's tail holds the card."""
    for rec, secret in live_sessions(profile, profiles_of):
        if rec["sessionId"] != sid: continue
        return session_card.pending(_transcript_tail(rec["cwd"], sid), lambda s: redact(s, [secret]))
    return None

def _transcript_tail(cwd, sid):
    path = ["projects", re.sub(r"[^A-Za-z0-9]", "-", cwd), f"{sid}.jsonl"]
    try: raw, start = _read_under_home(path, limit=TAIL_BYTES)
    except OSError as e: _log(f"hub: transcript of session {sid} unreadable: {type(e).__name__}"); return []
    lines = []
    for i, piece in enumerate(raw.split(b"\n")):
        if i == 0 and start: continue
        try: obj = json.loads(piece)
        except ValueError: continue
        if isinstance(obj, dict): lines.append(obj)
    return lines

def live_sessions(profile, profiles_of=profiles_of, seen=None):
    """Each record that passes every check and belongs to this profile, with its token: the one membership rule for the routes.
    `seen` is told every live record as the hub sees it, so the mode of a session can be recorded once while a read still
    answers it (#1298)."""
    alive = []
    for rec in _records():
        sid, pid, cwd = rec["sessionId"], rec["pid"], rec["cwd"]
        if not _socket_ok(rec["socket"]) or not _alive_since(pid, rec["startedAt"], rec.get("kind")): continue
        found = profiles_of(cwd)
        if found != [profile]:
            if len(found) > 1: _log(f"hub: session {sid} matches profiles {', '.join(found)}, listed in none", once=True)
            continue
        alive.append(rec)
    alive_sids = {rec["sessionId"] for rec in alive}
    for rec in alive:
        parked = rec.get("parkedJobId")
        if isinstance(parked, str) and parked in alive_sids:
            continue   # a front that parked its work in a live job is an empty shell (#1269)
        if seen is not None:
            seen(rec)
        yield rec, _secret(rec["sessionId"])

def list_sessions(profile, profiles_of=profiles_of, seen=None):
    """This profile's live sessions: each record that passes every check, grouped by project, waiting sessions first."""
    rows = []
    for rec, secret in live_sessions(profile, profiles_of, seen=seen):
        sid, cwd = rec["sessionId"], rec["cwd"]
        last, at = read_last_turn(cwd, sid, [secret])
        rows.append({"project": os.path.basename(cwd.rstrip("/")) or cwd, "sid": sid, "name": rec["name"], "status": rec["status"],
                     "waiting": rec["status"] == "waiting", "last": last or "", "t": at, "run": run_id_of(cwd)})
    groups = {}
    for r in sorted(rows, key=lambda r: (not r["waiting"], r["name"], r["sid"])):
        groups.setdefault(r["project"], []).append({k: r[k] for k in ("sid", "name", "status", "waiting", "last", "t", "run")})
    return {"waiting": sum(r["waiting"] for r in rows),
            "groups": [{"project": p, "sessions": s} for p, s in sorted(groups.items())]}

def project_sessions(profile, checkout, profiles_of=profiles_of):
    """The project's sessions (#898): this profile's live sessions whose working directory is the profile's checkout itself,
    by the one membership rule above. The overlay opens the one it finds, and asks the owner to pick when there are more."""
    home = os.path.realpath(checkout) if checkout else ""
    rows = [{"sid": rec["sessionId"], "name": rec["name"], "status": rec["status"]}
            for rec, _ in live_sessions(profile, profiles_of) if home and os.path.realpath(rec["cwd"]) == home]
    return sorted(rows, key=lambda r: (r["status"] != "waiting", r["name"], r["sid"]))
