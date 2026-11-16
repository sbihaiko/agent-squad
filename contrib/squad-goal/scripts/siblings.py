#!/usr/bin/env python3
"""usage: siblings.py <issues.json> <candidate> [<picked>...]   (the open-issue list the frontier pass read, ghread shape)
       siblings.py record <a> <b> <path>...                     (SG_STATE set: a wave refused one child for sharing these paths)
The sibling filter of one run (ADR-0150): exit 1 and print the reason when the candidate would be a conflicting sibling of an
issue the run already picked, exit 0 when it may join. Deterministic: it reads the spec link and the issue bodies, no model.
  spec   the candidate and a picked issue are tickets of one spec: the spec's native sub-issues (sub_issues of an open issue
         in the list), a "SPEC #N" / "Parent: #N" line in the ticket body, or a "## Parent" heading whose next
         non-blank line is "#N" (the to-tickets shape; the REST open-issue list has no sub_issues field). One run takes one ticket of a spec.
  files  issues whose bodies name one repository-relative file (normal form, cluster.paths) are ONE child (cluster.py,
         CL_MODE=files), never two siblings; so tickets of one spec that name a common file share a child and pass.
A file no body names is checked at the entry, on the two diffs (wave.sh, #1434): the entry refuses a child whose diff shares a
path with a sibling's, and a diff it cannot read is a WAVE-ERROR, never a clean pass. Landing still handles what neither reads.
  pair   the wave records each pair it refused for shared paths (record, #1941) in $SG_STATE/shared-pairs.json. The filter refuses
         the pair while both issues are open; a closed issue leaves the open list, so its pair is inert, and nothing deletes the record."""
import json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cluster import paths  # noqa: E402

PAIRS = "shared-pairs.json"
SPEC = re.compile(r"(?i)\b(?:spec|parent)\b[ \t:]*#(\d+)|^#+[ \t]*parent[ \t:]*\s*?\n(?:[ \t]*\n)*[ \t]*#(\d+)\b", re.M)


def specs(n, by):
    """The specs issue n is a ticket of."""
    out = {s for s, it in by.items() if n in (it.get("sub_issues") or [])}
    out |= {int(a or b) for a, b in SPEC.findall(by.get(n, {}).get("body") or "")}
    out.discard(n)
    return out


def children(nums, by):
    """The run's children: the issues grouped by a common file, the rule cluster.py applies."""
    par = {n: n for n in nums}
    def find(x):
        while par[x] != x:
            par[x] = par[par[x]]; x = par[x]
        return x
    f = {n: paths(by.get(n, {}).get("body")) for n in nums}
    for i, a in enumerate(nums):
        for b in nums[i + 1:]:
            if f[a] & f[b]:
                par[find(a)] = find(b)
    return find


def pair_rows():
    """The shared-path pairs the waves recorded: [{"issues": [a, b], "paths": [...]}], in the loop state (#1941)."""
    state = os.environ.get("SG_STATE")
    if not state:
        return []
    try:
        with open(os.path.join(state, PAIRS)) as f:
            return [r for r in json.load(f) if isinstance(r, dict)]
    except (OSError, ValueError):
        return []


def conflict(cand, picked, by):
    child = children(picked + [cand], by)
    mine = specs(cand, by)
    recorded = {frozenset(r.get("issues", [])): r.get("paths", []) for r in pair_rows()}
    for p in picked:
        if child(p) == child(cand):
            continue
        paths_ = recorded.get(frozenset((cand, p)))
        if paths_ and cand in by and p in by:
            return f"a shared-path pair with #{p} (both change {', '.join(paths_)})"
        shared = mine & specs(p, by)
        if shared:
            return f"a sibling of #{p} in spec #{min(shared)}"
    return None


def record(argv):
    """record <a> <b> <path>...: a wave refused one child for sharing these paths with a sibling (#1941)."""
    state = os.environ.get("SG_STATE")
    if not state or len(argv) < 3:
        print("usage: siblings.py record <a> <b> <path>... (SG_STATE set)", file=sys.stderr); return 2
    a, b = sorted({int(argv[0]), int(argv[1])})
    if a == b:
        return 0
    rows = pair_rows()
    key = [a, b]
    paths_ = set(argv[2:])
    for r in rows:
        if sorted(r.get("issues", [])) == key:
            paths_ |= set(r.get("paths", []))
    rows = [r for r in rows if sorted(r.get("issues", [])) != key]
    rows.append({"issues": key, "paths": sorted(paths_)})
    target = os.path.join(state, PAIRS)
    tmp = f"{target}.{os.getpid()}.tmp"
    with open(tmp, "w") as f:
        json.dump(rows, f, indent=1); f.write("\n")
    os.replace(tmp, target)
    return 0


def main(argv):
    if argv[:1] == ["record"]:
        return record(argv[1:])
    if len(argv) < 2:
        print(__doc__, file=sys.stderr); return 2
    by = {int(it["number"]): it for it in json.load(open(argv[0])) if isinstance(it, dict) and "number" in it}
    why = conflict(int(argv[1]), [int(x) for x in argv[2:]], by)
    if why:
        print(why); return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
