#!/usr/bin/env python3
"""Move Project (v2) cards over REST only (no GraphQL quota): board.py <owner> <project> "<Status>" <issue#>...
       board.py --add <owner> <project> "<Status>" <issue#>...   add the issues to the board first (when missing); idempotent: a refused add is re-read once
       board.py --run-fields <owner> <project> <issue#> <run> <run-started>
       board.py --clear-run <owner> <project> <issue#>
Field/option ids are cached a day and the issue -> item id map 10 min (refetched on a miss) under $SG_STATE; one PATCH per card.
Touches $SG_STATE/board-dirty so the hub refreshes within 2 s."""
import json, os, pathlib, subprocess, sys, time
st = pathlib.Path(os.environ.get("SG_STATE", pathlib.Path.home() / ".cache/squad-goal")); st.mkdir(parents=True, exist_ok=True)
args = sys.argv[1:]; add = args[:1] == ["--add"]; run_fields = args[:1] == ["--run-fields"]; clear_run = args[:1] == ["--clear-run"]
args = args[1:] if add or run_fields or clear_run else args
owner, num = args[0], args[1]
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
def raw_fields():
    return json.loads(api(f"{base}/fields?per_page=100"))
def fields():
    fs = raw_fields(); s = next(x for x in fs if x.get("name") == "Status")
    return {"id": s["id"], "opt": {o["name"]["raw"] if isinstance(o["name"], dict) else o["name"]: o["id"] for o in s["options"]}}
def text_fields():
    fs = raw_fields()
    return {x.get("name"): x.get("id") for x in fs if x.get("name") in ("Run", "Run started")}
def items():
    out = api("--paginate", f"{base}/items?per_page=100", "--jq", '.[]|select(.content.number)|[.content.number,.id,(.content.repository_url//"")]|@json')
    return {str(n): i for n, i, ru in (json.loads(l) for l in out.splitlines() if l.strip()) if not repo or ru.endswith("/" + repo)}
M = cached(f"rest-items-{owner}-{num}.json", 600, items)
def item_id(n, add_missing=False):
    global M
    if str(n) not in M: M = items(); (st / f"rest-items-{owner}-{num}.json").write_text(json.dumps(M))
    if str(n) not in M and add_missing and repo:
        iid = json.loads(api(f"repos/{repo}/issues/{n}"))["id"]
        try: M[str(n)] = json.loads(api("-X", "POST", f"{base}/items", "--input", "-", inp=json.dumps({"type": "Issue", "id": iid})))["id"]
        except RuntimeError:   # the add is idempotent: a refused add is re-read once (a stale cache held no item that is there), and only a second miss is a failure
            M = items()
            if str(n) not in M: raise
        (st / f"rest-items-{owner}-{num}.json").write_text(json.dumps(M))
    return M.get(str(n))
if run_fields or clear_run:
    n = int(args[2]); iid = item_id(n)
    if not iid: print(f"#{n} not on the board", file=sys.stderr); sys.exit(1)
    tf = cached(f"rest-text-fields-{owner}-{num}.json", 86400, text_fields)
    if "Run" not in tf or "Run started" not in tf:
        (st / f"rest-text-fields-{owner}-{num}.json").unlink(missing_ok=True); tf = text_fields()
    missing = [x for x in ("Run", "Run started") if x not in tf]
    if missing: sys.exit("no text field " + ", ".join(missing))
    run = "" if clear_run else args[3]; started = "" if clear_run else (args[4] if len(args) > 4 else "")
    try: api("-X", "PATCH", f"{base}/items/{iid}", "--input", "-", inp=json.dumps({"fields": [{"id": tf["Run"], "value": run}, {"id": tf["Run started"], "value": started}]}))
    except RuntimeError as e: print(f"#{n}: {e}", file=sys.stderr); sys.exit(1)   # named on stderr, never swallowed: the caller logs the refusal it was given
    (st / "board-dirty").touch(); sys.exit(0)
status, nums = args[2], [int(x) for x in args[3:]]
F = cached(f"rest-fields-{owner}-{num}.json", 86400, fields)
if status not in F["opt"]: (st / f"rest-fields-{owner}-{num}.json").unlink(missing_ok=True); F = fields()
if status not in F["opt"]: sys.exit(f"no Status option {status!r}")
rc = 0
for n in nums:
    try: iid = item_id(n, add)
    except RuntimeError as e: print(f"#{n}: {e}", file=sys.stderr); rc = 1; continue   # the add GitHub refused twice, named: the caller logs it
    if not iid: print(f"#{n} not on the board", file=sys.stderr); rc = 1; continue
    try: api("-X", "PATCH", f"{base}/items/{iid}", "--input", "-", inp=json.dumps({"fields": [{"id": F["id"], "value": F["opt"][status]}]})); print(f"#{n} -> {status}")
    except RuntimeError as e: print(f"#{n}: {e}", file=sys.stderr); rc = 1
(st / "board-dirty").touch(); sys.exit(rc)
