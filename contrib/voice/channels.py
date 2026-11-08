"""Resolve the channel a project writes to: the one named, else the one closest to the repository's name.

Vendored from the `slack-hal9k` skill, so this repository runs and times the pipeline it ships. A caller finds it by name
with `python3 -m squad.skillpath --script channels.py` (squad/skillpath.py) and never by a path.

The rule: an argument wins. With none, the script reads the git repository the caller stands in (`git rev-parse
--show-toplevel`, else the name in the origin URL), builds the project slug from it, and scores every visible channel
against it — `proj-<slug>` exactly, then `<slug>`, then a name that carries the slug, then the word overlap and the
similarity ratio of the remaining names. The best score wins, and a score under MIN says nothing matched, so the caller
names the channel instead of writing into the wrong one.

A public channel the bot is not in is joined with `channels:join` when the app holds that scope, and reported when it does
not. A private channel is listed too, but the bot cannot join it: the caller is told to invite it. This module reads no
secret: the caller passes the token.
"""
import difflib
import json
import re
import subprocess

TIMEOUT = 20
MIN = 0.55       # below this, no channel is "the most similar one": the caller is told to name it
ID = re.compile(r"[CGD][A-Z0-9]{6,}")


def api(token, method, params=None, post=False):
    cmd = ["curl", "-sS", "--max-time", str(TIMEOUT), "-H", f"Authorization: Bearer {token}"]
    if post:
        cmd += ["-H", "Content-type: application/json", "-X", "POST"]
        if params is not None:
            cmd += ["-d", json.dumps(params)]
    cmd.append(f"https://slack.com/api/{method}")
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT + 5)
    try:
        return json.loads(out.stdout or "{}")
    except ValueError:
        return {"ok": False, "error": (out.stdout or out.stderr).strip()[:120]}


def repo_slug(cwd="."):
    """The repository's own name: the folder of the toplevel, else the last path in origin's URL. None outside git."""
    try:
        top = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        if top:
            return re.sub(r"\.git$", "", top.rsplit("/", 1)[-1]).lower()
        url = subprocess.run(["git", "-C", cwd, "remote", "get-url", "origin"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        if url:
            name = url.rstrip("/").rsplit("/", 1)[-1]
            name = name.rsplit(":", 1)[-1] if "/" not in name else name
            return re.sub(r"\.git$", "", name).lower()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def score(name, slug):
    """How close a channel name is to the repository: exact project names first, then containment, then similarity."""
    n = (name or "").lower()
    if not slug:
        return 0.0
    if n == f"proj-{slug}":
        return 1.0
    if n == slug:
        return 0.95
    if slug in n:
        return 0.9
    if n in slug and len(n) >= 4:
        return 0.85
    words = [w for w in re.split(r"[-_.]", slug) if len(w) > 2]
    hit = sum(1 for w in words if w in n)
    ratio = difflib.SequenceMatcher(None, n, slug).ratio()
    return max(0.75 * (hit / len(words)) if words else 0.0, 0.8 * ratio)


def resolve(token, wanted, project, cwd="."):
    """(channel_id, error). A named channel is returned as given; with none, the closest name to the repository wins."""
    if wanted and ID.fullmatch(wanted):
        return wanted, None
    d = api(token, "conversations.list?types=public_channel,private_channel&limit=1000")
    if not d.get("ok"):
        return None, f"cannot list channels: {d.get('error')}"
    chans = d.get("channels") or []
    if wanted:
        for c in chans:
            if c.get("name") == wanted:
                return _member(token, c, wanted)
        return None, f"no channel named {wanted}"
    slug = repo_slug(cwd) or (project or "").lower()
    project_slug = (project or "").lower()
    # The project name the caller already carries is a second spelling of the same repository (a slug may differ).
    best, best_s = None, 0.0
    for c in chans:
        s = max(score(c.get("name"), slug), score(c.get("name"), project_slug) if project_slug else 0.0)
        if s > best_s:
            best, best_s = c, s
    if not best or best_s < MIN:
        near = ", ".join(sorted(c.get("name") or "" for c in chans)[:8])
        return None, f"no channel resembles {slug!r} (best {best_s:.2f}); name one (visible: {near}...)"
    return _member(token, best, best.get("name"))


def _member(token, c, name):
    cid = c.get("id")
    if c.get("is_member"):
        return cid, None
    if c.get("is_private"):
        return cid, f"the bot is not in #{name}: run `/invite @<bot>` in it once (a private channel cannot be joined)"
    j = api(token, "conversations.join", {"channel": cid}, post=True)
    if j.get("ok"):
        return cid, None
    return cid, f"the bot is not in #{name}: run `/invite @<bot>` in it once ({j.get('error')})"
