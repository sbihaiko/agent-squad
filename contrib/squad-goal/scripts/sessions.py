"""The Terminals list (#833): the one reader of the local session registry, the protected transcript reader and the one
redaction step. The hub's routes call it; nothing else reads a session record, a token or a transcript.
"""
import json, os, pathlib, re, stat, subprocess, sys, time

ADDRESS = pathlib.Path(__file__).resolve().parent / "address.sh"
RECORD_VERSION = 1
STATUSES = ("busy", "idle", "waiting")
SID = re.compile(r"[A-Za-z0-9-]{1,64}")
TAIL_BYTES = 256 * 1024
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

def _read_under_home(parts, limit=None):
    """Bytes of a file under the hub's Claude folder, walked by descriptors: no link followed at any level, owner and mode
    checked on the descriptor opened. The last `limit` bytes when the file is longer; the flag says it was cut."""
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
        cut = limit is not None and size > limit
        if cut: os.lseek(fd, size - limit, os.SEEK_SET)
        chunks = []
        while chunk := os.read(fd, 65536): chunks.append(chunk)
        return b"".join(chunks), cut
    finally:
        os.close(fd)

def _secret(sid):
    try: return _read_under_home(["sessions", f"{sid}.token"])[0].decode(errors="replace").strip()
    except OSError: return ""

def _shape_ok(o):
    return (isinstance(o, dict) and o.get("version") == RECORD_VERSION and type(o.get("pid")) is int and o["pid"] > 0
            and type(o.get("startedAt")) is int and isinstance(o.get("sessionId"), str) and SID.fullmatch(o["sessionId"]) is not None
            and isinstance(o.get("cwd"), str) and os.path.isabs(o["cwd"]) and isinstance(o.get("name"), str) and len(o["name"]) <= 200
            and o.get("status") in STATUSES and isinstance(o.get("socket"), str))

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
        out.append(obj)
    return out

def _socket_ok(path):
    try: st = os.lstat(path)
    except OSError: return False
    if not stat.S_ISSOCK(st.st_mode) or st.st_uid != UID: return False
    return os.path.realpath(path) == path and os.path.dirname(path) == os.path.realpath(claude_home() / "sessions" / "sockets")

def _proc_start(pid):
    out = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True).stdout
    try: return int(time.mktime(time.strptime(" ".join(out.split()), "%a %b %d %H:%M:%S %Y")))
    except ValueError: return None

def _alive_since(pid, started):
    try: os.kill(pid, 0)
    except OSError: return False
    return _proc_start(pid) == started

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
    try: raw, cut = _read_under_home(["projects", re.sub(r"[^A-Za-z0-9]", "-", cwd), f"{sid}.jsonl"], limit=TAIL_BYTES)
    except OSError as e: _log(f"hub: transcript of session {sid} unreadable: {type(e).__name__}", secrets); return None
    lines = raw.decode(errors="replace").splitlines()[1 if cut else 0:]
    last = ""
    for line in lines:
        try: turn = json.loads(line)
        except ValueError: continue
        content = (turn.get("message") or {}).get("content")
        text = content if isinstance(content, str) else " ".join(p.get("text", "") for p in content or [] if isinstance(p, dict))
        if text.strip(): last = text.strip()
    return redact(last, secrets)[:PREVIEW_CHARS]

def list_sessions(profile, profiles_of=profiles_of):
    """This profile's live sessions: each record that passes every check, grouped by project, waiting sessions first."""
    rows = []
    for rec in _records():
        sid, pid, cwd = rec["sessionId"], rec["pid"], rec["cwd"]
        if not _socket_ok(rec["socket"]) or not _alive_since(pid, rec["startedAt"]): continue
        found = profiles_of(cwd)
        if found != [profile]:
            if len(found) > 1: _log(f"hub: session {sid} matches profiles {', '.join(found)}, listed in none", once=True)
            continue
        secret = _secret(sid)
        rows.append({"project": os.path.basename(cwd.rstrip("/")) or cwd, "sid": sid, "name": rec["name"], "status": rec["status"],
                     "waiting": rec["status"] == "waiting", "last": read_last_message(cwd, sid, [secret]) or ""})
    groups = {}
    for r in sorted(rows, key=lambda r: (not r["waiting"], r["name"], r["sid"])):
        groups.setdefault(r["project"], []).append({k: r[k] for k in ("sid", "name", "status", "waiting", "last")})
    return {"waiting": sum(r["waiting"] for r in rows),
            "groups": [{"project": p, "sessions": s} for p, s in sorted(groups.items())]}
