#!/usr/bin/env python3
"""Extract real labeled skill requests and real fetched content from past Claude Code sessions.

Raw text goes to scripts/archive/jev_data_real/ (git ignores it). Only aggregate counts are printed.
Run: python3 scripts/archive/jev_real/extract.py
"""
import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent.parent
OUT = ROOT / "scripts" / "archive" / "jev_data_real"
PROJ = Path.home() / ".claude" / "projects"
SKIP_SESSIONS = {"7953ddf8-a8e6-483e-bc02-39a049f53378", "ccf39e32-d9a8-4e71-9d72-6ad4ee46c351"}  # this experiment's own sessions
HOUSEKEEPING = {"loop", "compact", "watch-runs", "model", "goal", "clear", "exit", "reload-plugins", "mcp", "usage", "effort",
                "config", "login", "plugin", "autonomy", "ask-matt", "subtask", "logout", "help", "resume", "status", "cost",
                "permissions", "hooks", "init", "memory", "review", "doctor", "agents", "ide", "vim", "fast", "context", "rename",
                "export", "copy", "rewind", "stop", "stats", "release-notes", "terminal-setup", "theme", "output-style", "btw"}
ALIAS = {"mattpocock-skills:handoff": "handoff", "claude-handoff": "handoff", "mattpocock-skills:grill-me": "grill-me"}
SECRET = [re.compile(p, re.I) for p in (
    r"sk-[A-Za-z0-9_\-]{10,}", r"bearer\s+[A-Za-z0-9._\-]{10,}", r"(api[_-]?key|token|secret|passw(or)?d|senha)\s*[:=]\s*\S+",
    r"\b[A-Za-z0-9+/_\-]{32,}\b", r"ghp_[A-Za-z0-9]{20,}", r"xox[baprs]-[A-Za-z0-9\-]{10,}")]


def scrub(t):
    for p in SECRET:
        t = p.sub("[REDACTED]", t)
    return t


def text_of(msg):
    c = msg.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        if any(isinstance(x, dict) and x.get("type") == "tool_result" for x in c):
            return None
        return "\n".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
    return None


def plain(t):
    """A plain human request: not meta, not system text."""
    if not t:
        return False
    s = t.lstrip()
    bad = ("<system-reminder", "<task-notification", "<command-", "<local-command", "[Request interrupted", "<fork-boilerplate",
           "Caveat:", "This session is being continued", "<user-prompt-submit-hook", "<bash-", "[SYSTEM", "Base directory for this skill")
    return not s.startswith(bad) and len(s.strip()) > 3


def norm(name):
    name = name.strip().lstrip("/")
    return ALIAS.get(name, name.split(":")[-1] if name.startswith("mattpocock-skills:") else name)


def sess_ok(p):
    return p.stem not in SKIP_SESSIONS


def walk(path):
    for line in open(path, errors="ignore"):
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("isSidechain"):
            continue
        yield d


def main():
    items, fetched = [], []
    files_cmd = subprocess.run(["rg", "-l", "<command-name>/", "--glob", "*.jsonl", str(PROJ)], capture_output=True, text=True).stdout.split()
    files_skill = subprocess.run(["rg", "-l", r'"name":"Skill"', "--glob", "*.jsonl", str(PROJ)], capture_output=True, text=True).stdout.split()
    files_fetch = subprocess.run(["rg", "-l", r'"name":"(WebFetch|WebSearch)"', "--glob", "*.jsonl", str(PROJ)], capture_output=True, text=True).stdout.split()
    files_cmd = [Path(f) for f in files_cmd if "/subagents/" not in f and sess_ok(Path(f))]
    files_skill = [Path(f) for f in files_skill if "/subagents/" not in f and sess_ok(Path(f))]
    files_fetch = [Path(f) for f in files_fetch if "/subagents/" not in f and sess_ok(Path(f))]
    # S1: slash commands the owner typed
    for f in files_cmd:
        msgs = [d for d in walk(f) if d.get("type") == "user" and not d.get("isMeta")]
        for i, d in enumerate(msgs):
            t = text_of(d.get("message", {}))
            if not t or "<command-name>" not in t[:200]:
                continue
            m = re.search(r"<command-name>\s*/?([^<\s]+)\s*</command-name>", t)
            if not m:
                continue
            label = norm(m.group(1))
            if label in HOUSEKEEPING:
                continue
            a = re.search(r"<command-args>(.*?)</command-args>", t, re.S)
            req = a.group(1).strip() if a else ""
            if not req:
                for n in msgs[i + 1:i + 4]:
                    nt = text_of(n.get("message", {}))
                    if plain(nt):
                        req = nt.strip()
                        break
            if req:
                items.append({"slice": "S1", "label": label, "ts": d.get("timestamp", ""), "sess": f.stem, "text": req[:2000]})
    # S2: natural-language request followed by an agent Skill call
    for f in files_skill:
        last = None
        for d in walk(f):
            msg = d.get("message", {})
            if d.get("type") == "user" and not d.get("isMeta"):
                t = text_of(msg)
                if plain(t):
                    if last and len(last[0]) > 25:
                        items.append({"slice": "S0", "label": "none", "ts": last[1], "sess": f.stem, "text": last[0][:2000]})
                    last = (t.strip(), d.get("timestamp", ""))
            elif d.get("type") == "assistant" and last:
                for x in msg.get("content", []) if isinstance(msg.get("content"), list) else []:
                    if isinstance(x, dict) and x.get("type") == "tool_use" and x.get("name") == "Skill":
                        sk = norm(str(x.get("input", {}).get("skill", "")))
                        if sk and sk not in HOUSEKEEPING:
                            items.append({"slice": "S2", "label": sk, "ts": last[1], "sess": f.stem, "text": last[0][:2000]})
                        last = None
                        break
    # fetched content: WebFetch and WebSearch results
    for f in files_fetch:
        ids = {}
        for d in walk(f):
            msg = d.get("message", {})
            c = msg.get("content")
            if not isinstance(c, list):
                continue
            for x in c:
                if not isinstance(x, dict):
                    continue
                if x.get("type") == "tool_use" and x.get("name") in ("WebFetch", "WebSearch"):
                    ids[x["id"]] = x["name"]
                if x.get("type") == "tool_result" and x.get("tool_use_id") in ids:
                    body = x.get("content")
                    if isinstance(body, list):
                        body = "\n".join(y.get("text", "") for y in body if isinstance(y, dict))
                    if isinstance(body, str) and len(body) > 300:
                        fetched.append({"tool": ids[x["tool_use_id"]], "ts": d.get("timestamp", ""), "sess": f.stem, "text": body[:2500]})
    # scrub, dedupe, drop empties
    def finish(rows):
        seen, out = set(), []
        for r in rows:
            r["text"] = scrub(r["text"]).strip()
            if not r["text"]:
                continue
            r["id"] = hashlib.sha1(r["text"].encode()).hexdigest()[:12]
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            out.append(r)
        return sorted(out, key=lambda r: r["ts"])
    items, fetched = finish(items), finish(fetched)
    s0 = sorted([r for r in items if r["slice"] == "S0"], key=lambda r: r["id"])[:120]
    items = sorted([r for r in items if r["slice"] != "S0"] + s0, key=lambda r: r["ts"])
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "requests.json").write_text(json.dumps(items, indent=1))
    (OUT / "fetched.json").write_text(json.dumps(fetched, indent=1))
    from collections import Counter
    for s in ("S0", "S1", "S2"):
        c = Counter(r["label"] for r in items if r["slice"] == s)
        print(s, sum(c.values()), dict(c.most_common()))
    print("fetched", len(fetched), dict(Counter(r["tool"] for r in fetched)))


if __name__ == "__main__":
    main()
