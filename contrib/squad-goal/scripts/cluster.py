#!/usr/bin/env python3
"""usage: cluster.py < issues.json   (a JSON list of {"number", "title", "body", "created_at"}, the gh REST issue shape)
Groups issues that probably share one root cause, so the loop dispatches ONE child and ONE PR per cause, not per symptom.
Two issues join a cluster when either holds:
  area   their titles share the area prefix (text before the first ":", e.g. "play-pad-only-home") and they were
         created within CL_WINDOW_H hours (default 6) of each other: one failing batch files one issue per step;
  files  their bodies name at least one common repo path (e.g. UI/Logic/TestHook/TestHookScreens.cs): their fixes
         touch the same slice and would conflict as separate PRs.
CL_MODE=files keeps only the files rule (the frontier: tickets cut from one spec share an area and a creation time
without sharing a cause).
Prints one cluster per line, issue numbers ascending (the first is the primary), input order of clusters by primary."""
import json, os, re, sys, time

PATH = re.compile(r"\b(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.(?:cs|cpp|h|hpp|py|sh|axaml|json|md|yml|yaml|xml|js|ts)\b")
mode = os.environ.get("CL_MODE", "all")
win = float(os.environ.get("CL_WINDOW_H", "6")) * 3600

def ts(s):
    try: return time.mktime(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ"))
    except (TypeError, ValueError): return None

def area(t):
    t = (t or "").strip()
    return t.split(":", 1)[0].strip().lower() if ":" in t else ""

items = json.load(sys.stdin)
par = {}
def find(x):
    while par[x] != x: par[x] = par[par[x]]; x = par[x]
    return x
def join(a, b): par[find(a)] = find(b)
info = []
for it in items:
    n = int(it["number"]); par[n] = n
    info.append((n, area(it.get("title")), ts(it.get("created_at")), set(PATH.findall(it.get("body") or ""))))
for i, (a, aa, at, af) in enumerate(info):
    for b, ba, bt, bf in info[i + 1:]:
        if mode != "files" and aa and aa == ba and at is not None and bt is not None and abs(at - bt) <= win: join(a, b)
        elif af & bf: join(a, b)
groups = {}
for n, *_ in info: groups.setdefault(find(n), []).append(n)
for g in sorted((sorted(v) for v in groups.values()), key=lambda v: v[0]): print(" ".join(map(str, g)))
