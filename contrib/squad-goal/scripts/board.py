#!/usr/bin/env python3
"""Move Project (v2) cards over REST only (no GraphQL quota): board.py <owner> <project> "<Status>" <issue#>...
       board.py --add <owner> <project> "<Status>" <issue#>...   add the issues to the board first (when missing)
Field/option ids are cached a day and the issue -> item id map 10 min (refetched on a miss) under $SG_STATE; one PATCH per card.
Touches $SG_STATE/board-dirty so the hub refreshes within 2 s."""
import json, os, pathlib, subprocess, sys, time
st = pathlib.Path(os.environ.get("SG_STATE", pathlib.Path.home() / ".cache/squad-goal")); st.mkdir(parents=True, exist_ok=True)
args = sys.argv[1:]; add = args[:1] == ["--add"]; args = args[1:] if add else args
owner, num, status, nums = args[0], args[1], args[2], [int(x) for x in args[3:]]
repo = os.environ.get("SG_REPO", "")
def api(*a, inp=None):
    r = subprocess.run(["gh", "api", *a], input=inp, capture_output=True, text=True)
    if r.returncode: raise RuntimeError(r.stderr.strip()[:200])
    return r.stdout
def cached(name, ttl, fn):
    f = st / name
    try:
        if time.time() - f.stat().st_mtime < ttl: return json.loads(f.read_text())
    except (OSError, ValueError): pass
    d = fn(); f.write_text(json.dumps(d)); return d
kind = cached(f"owner-kind-{owner}.json", 30 * 86400, lambda: "orgs" if json.loads(api(f"users/{owner}")).get("type") == "Organization" else "users")
base = f"/{kind}/{owner}/projectsV2/{num}"
def fields():
    s = next(x for x in json.loads(api(f"{base}/fields?per_page=100")) if x.get("name") == "Status")
    return {"id": s["id"], "opt": {o["name"]["raw"] if isinstance(o["name"], dict) else o["name"]: o["id"] for o in s["options"]}}
F = cached(f"rest-fields-{owner}-{num}.json", 86400, fields)
if status not in F["opt"]: (st / f"rest-fields-{owner}-{num}.json").unlink(missing_ok=True); F = fields()
if status not in F["opt"]: sys.exit(f"no Status option {status!r}")
def items():
    out = api("--paginate", f"{base}/items?per_page=100", "--jq", '.[]|select(.content.number)|[.content.number,.id,(.content.repository_url//"")]|@json')
    return {str(n): i for n, i, ru in (json.loads(l) for l in out.splitlines() if l.strip()) if not repo or ru.endswith("/" + repo)}
M = cached(f"rest-items-{owner}-{num}.json", 600, items)
if any(str(n) not in M for n in nums): M = items(); (st / f"rest-items-{owner}-{num}.json").write_text(json.dumps(M))
rc = 0
for n in nums:
    if str(n) not in M and add and repo:
        iid = json.loads(api(f"repos/{repo}/issues/{n}"))["id"]
        M[str(n)] = json.loads(api("-X", "POST", f"{base}/items", "--input", "-", inp=json.dumps({"type": "Issue", "id": iid})))["id"]
        (st / f"rest-items-{owner}-{num}.json").write_text(json.dumps(M))
    if str(n) not in M: print(f"#{n} not on the board", file=sys.stderr); rc = 1; continue
    try: api("-X", "PATCH", f"{base}/items/{M[str(n)]}", "--input", "-", inp=json.dumps({"fields": [{"id": F["id"], "value": F["opt"][status]}]})); print(f"#{n} -> {status}")
    except RuntimeError as e: print(f"#{n}: {e}", file=sys.stderr); rc = 1
(st / "board-dirty").touch(); sys.exit(rc)
