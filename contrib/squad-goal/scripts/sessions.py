"""The Terminals list (#833): the one reader of the local session registry, the protected transcript reader and the one
redaction step. The hub's routes call it; nothing else reads a session record, a token or a transcript.
"""
import json, os, pathlib, re, stat, subprocess, sys, time

ADDRESS = pathlib.Path(__file__).resolve().parent / "address.sh"
SOCKET_DIRS = ("/tmp/cc-socks",)   # where Claude Code binds a session socket (docs/design/session-wire-protocol.md)
STATUSES = ("busy", "idle", "waiting")
SID = re.compile(r"[A-Za-z0-9-]{1,64}")
TAIL_BYTES = 256 * 1024
TURN_LIMIT = 200
PREVIEW_CHARS = 200
UID = os.getuid()
CREDENTIAL = re.compile(r"sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{16,}|Bearer\s+[A-Za-z0-9._~+/=-]+")
_LOGGED = set()

def claude_home(): return pathlib.Path.home() / ".claude"

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

def _read_under_home(parts, limit=None, end=None):
    """Bytes of a file under the hub's Claude folder, walked by descriptors: no link followed at any level, owner and mode
    checked on the descriptor opened. The `limit` bytes before `end` (the file's end when None). Also the offset of the first byte."""
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
        size = os.fstat(fd).st_size
        stop = size if end is None else min(end, size)
        start = max(0, stop - limit) if limit is not None else 0
        os.lseek(fd, start, os.SEEK_SET)
        chunks, left = [], stop - start
        while left > 0 and (chunk := os.read(fd, min(65536, left))):
            chunks.append(chunk); left -= len(chunk)
        return b"".join(chunks), start
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
    out = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True).stdout
    try: return int(time.mktime(time.strptime(" ".join(out.split()), "%a %b %d %H:%M:%S %Y")))
    except ValueError: return None

def _alive_since(pid, started):
    try: os.kill(pid, 0)
    except OSError: return False
    start = _proc_start(pid)
    return start is not None and 0 <= started // 1000 - start <= 30   # startedAt (ms) is written a few seconds after the process starts

def profiles_of(cwd):
    """The profiles whose repository the checkout at `cwd` is, resolved by the address seam (ADR-0138)."""
    repo = subprocess.run(["bash", "-c", '. "$1" >/dev/null 2>&1; sg_resolve_repo "$2"', "_", str(ADDRESS), cwd],
                          capture_output=True, text=True).stdout.strip()
    if not repo: return []
    rows = subprocess.run(["bash", str(ADDRESS), "--list"], capture_output=True, text=True).stdout.splitlines()
    return [slug for slug, r in (row.split("\t", 1) for row in rows if "\t" in row) if r.lower() == repo.lower()]

def read_last_message(cwd, sid, secrets=()):
    """The text of the last turn in the session's transcript, redacted and cut to a preview; None when it cannot be read."""
    if SID.fullmatch(sid) is None: return None
    try: raw, start = _read_under_home(["projects", re.sub(r"[^A-Za-z0-9]", "-", cwd), f"{sid}.jsonl"], limit=TAIL_BYTES)
    except OSError as e: _log(f"hub: transcript of session {sid} unreadable: {type(e).__name__}", secrets); return None
    lines = raw.decode(errors="replace").splitlines()[1 if start else 0:]
    last = ""
    for line in lines:
        try: turn = json.loads(line)
        except ValueError: continue
        content = (turn.get("message") or {}).get("content")
        text = content if isinstance(content, str) else " ".join(p.get("text", "") for p in content or [] if isinstance(p, dict))
        if text.strip(): last = text.strip()
    return redact(last, secrets)[:PREVIEW_CHARS]

def _turns_of(obj):
    kind, uid = obj.get("type"), str(obj.get("uuid") or "")
    content = (obj.get("message") or {}).get("content")
    if isinstance(content, str): content = [{"type": "text", "text": content}]
    out = []
    for p in content if isinstance(content, list) else []:
        if not isinstance(p, dict): continue
        text = p.get("text")
        if kind == "user" and p.get("type") == "text" and isinstance(text, str) and text: out.append(("owner", uid, text))
        elif kind == "assistant" and p.get("type") == "text" and isinstance(text, str) and text: out.append(("answer", uid, text))
        elif kind == "assistant" and p.get("type") == "tool_use": out.append(("tool", uid, f"tool: {p.get('name', '')}"))
    return out

def read_conversation(cwd, sid, secrets=(), before=None):
    """One page of the session's conversation, oldest first: the newest whole lines before the byte offset `before` (the end of
    the transcript when None), within TURN_LIMIT turns and one TAIL_BYTES window. `next` is the offset of the older page."""
    if SID.fullmatch(sid) is None: return None
    try: raw, start = _read_under_home(["projects", re.sub(r"[^A-Za-z0-9]", "-", cwd), f"{sid}.jsonl"], limit=TAIL_BYTES, end=before)
    except OSError as e: _log(f"hub: transcript of session {sid} unreadable: {type(e).__name__}", secrets); return None
    lines, at = [], start
    for i, piece in enumerate(raw.split(b"\n")):
        offset, at = at, at + len(piece) + 1
        if (i == 0 and start) or not piece: continue
        try: obj = json.loads(piece)
        except ValueError: continue
        if isinstance(obj, dict): lines.append((offset, _turns_of(obj)))
    kept, turns = [], 0
    for offset, found in reversed(lines):
        if kept and turns + len(found) > TURN_LIMIT: break
        kept.append((offset, found)); turns += len(found)
    kept.reverse()
    out = [{"id": uid, "role": role, "text": redact(text, secrets)} for _, found in kept for role, uid, text in found]
    cursor = kept[0][0] if kept else start
    return {"turns": out, "next": cursor or None}

def conversation(profile, sid, before=None, profiles_of=profiles_of):
    """One page of a live session's conversation when the session is this profile's; None for any other session."""
    for rec, secret in live_sessions(profile, profiles_of):
        if rec["sessionId"] == sid: return read_conversation(rec["cwd"], sid, [secret], before)
    return None

def live_sessions(profile, profiles_of=profiles_of):
    """Each record that passes every check and belongs to this profile, with its token: the one membership rule for the routes."""
    for rec in _records():
        sid, pid, cwd = rec["sessionId"], rec["pid"], rec["cwd"]
        if not _socket_ok(rec["socket"]) or not _alive_since(pid, rec["startedAt"]): continue
        found = profiles_of(cwd)
        if found != [profile]:
            if len(found) > 1: _log(f"hub: session {sid} matches profiles {', '.join(found)}, listed in none", once=True)
            continue
        yield rec, _secret(sid)

def list_sessions(profile, profiles_of=profiles_of):
    """This profile's live sessions: each record that passes every check, grouped by project, waiting sessions first."""
    rows = []
    for rec, secret in live_sessions(profile, profiles_of):
        sid, cwd = rec["sessionId"], rec["cwd"]
        rows.append({"project": os.path.basename(cwd.rstrip("/")) or cwd, "sid": sid, "name": rec["name"], "status": rec["status"],
                     "waiting": rec["status"] == "waiting", "last": read_last_message(cwd, sid, [secret]) or ""})
    groups = {}
    for r in sorted(rows, key=lambda r: (not r["waiting"], r["name"], r["sid"])):
        groups.setdefault(r["project"], []).append({k: r[k] for k in ("sid", "name", "status", "waiting", "last")})
    return {"waiting": sum(r["waiting"] for r in rows),
            "groups": [{"project": p, "sessions": s} for p, s in sorted(groups.items())]}

def project_sessions(profile, checkout, profiles_of=profiles_of):
    """The project's sessions (#898): this profile's live sessions whose working directory is the profile's checkout itself,
    by the one membership rule above. The overlay opens the one it finds, and asks the owner to pick when there are more."""
    home = os.path.realpath(checkout) if checkout else ""
    rows = [{"sid": rec["sessionId"], "name": rec["name"], "status": rec["status"]}
            for rec, _ in live_sessions(profile, profiles_of) if home and os.path.realpath(rec["cwd"]) == home]
    return sorted(rows, key=lambda r: (r["status"] != "waiting", r["name"], r["sid"]))
