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
A path is compared in its normal form (paths below): "./squad/a.py" and "squad/x/../a.py" are one file (#703).
A cluster is capped at CL_MAX tickets (default 2): one child over many tickets of one large file is one run that dies
out_of_budget, so the cluster is split in ascending number order and no ticket is dropped (ADR-0158).
Prints one cluster per line, issue numbers ascending (the first is the primary), input order of clusters by primary."""
import json, os, posixpath, re, sys, time

PATH = re.compile(r"(?<![A-Za-z0-9_.~/-])(?:\.{1,2}/)*(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.(?:cs|cpp|h|hpp|py|sh|axaml|json|jsonl|md|yml|yaml|toml|xml|js|ts|html|css)\b")


def paths(body):
    """The repository-relative files a body names, in normal form: "./" and "x/.." fold away; a path that leaves the
    repository ("../x") is no repository file and is dropped."""
    out = set()
    for p in PATH.findall(body or ""):
        n = posixpath.normpath(p)
        if n != ".." and not n.startswith("../"):
            out.add(n)
    return out


def ts(s):
    try: return time.mktime(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ"))
    except (TypeError, ValueError): return None

def area(t):
    t = (t or "").strip()
    return t.split(":", 1)[0].strip().lower() if ":" in t else ""


def main():
    mode = os.environ.get("CL_MODE", "all")
    win = float(os.environ.get("CL_WINDOW_H", "6")) * 3600
    items = json.load(sys.stdin)
    par = {}
    def find(x):
        while par[x] != x: par[x] = par[par[x]]; x = par[x]
        return x
    def join(a, b): par[find(a)] = find(b)
    info = []
    for it in items:
        n = int(it["number"]); par[n] = n
        info.append((n, area(it.get("title")), ts(it.get("created_at")), paths(it.get("body"))))
    for i, (a, aa, at, af) in enumerate(info):
        for b, ba, bt, bf in info[i + 1:]:
            if mode != "files" and aa and aa == ba and at is not None and bt is not None and abs(at - bt) <= win: join(a, b)
            elif af & bf: join(a, b)
    groups = {}
    for n, *_ in info: groups.setdefault(find(n), []).append(n)
    # A cluster is capped (CL_MAX, default 2): one child over five tickets of one large file is one run that dies
    # `out_of_budget`, so the cluster is split in ascending number order and no ticket is dropped (ADR-0158).
    try: cap = int(os.environ.get("CL_MAX", "2"))
    except ValueError: cap = 2
    cap = cap if cap > 0 else 2
    for g in sorted((sorted(v) for v in groups.values()), key=lambda v: v[0]):
        for i in range(0, len(g), cap): print(" ".join(map(str, g[i:i + cap])))


if __name__ == "__main__":
    main()
