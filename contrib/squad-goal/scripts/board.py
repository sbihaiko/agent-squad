#!/usr/bin/env python3
"""Move Project (v2) cards over REST only (no GraphQL quota): board.py <owner> <project> "<Status>" <issue#>...
       board.py --add <owner> <project> "<Status>" <issue#>...   add the issues to the board first (when missing); idempotent: a refused add is re-read once
       board.py --run-fields <owner> <project> <issue#> <run> <run-started>
       board.py --clear-run <owner> <project> <issue#>
Field/option ids are cached a day and the issue -> item id map 10 min (refetched on a miss) under $SG_STATE; one PATCH per card,
and every Status write is read back.
A field is set by sending its value and cleared by sending null -- "To clear the field, set this to null" -- and an empty
string is a value the field refuses, answered "Invalid field values" (HTTP 400) (#101): a clear, and a Run field with nothing
to carry, are written as null, beside the field id they read.
A Status write is proven by the card, never by the answer to its PATCH: a field write can be answered ok and never land, or be
refused with HTTP 400 (#105). After the PATCH the card is read back, and the Status it carries must be the option written; a
card whose Status is unset is NOT written. SG_BOARD_READ_ATTEMPTS and SG_BOARD_READ_BACKOFF bound the retries (a backoff of 0
sleeps for no read), and a card no read then carries the Status is a READ FAILURE, named and exiting non-zero, never a card
written and never left in the state cache as one.
A miss on the item map re-reads the items with the same bounded backoff (#96): the project-items route is eventually consistent,
so a card written a moment ago is not always in the next read. A card the board answered "already exists" for and no read then
carried is a READ FAILURE, named by name, never "not on the board"; only a card no read ever carried, with nothing written for
it here, is absent.
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
def _num(name, default):   # a bad value in the environment must not kill a card move
    try: return float(os.environ.get(name, default))
    except ValueError: return float(default)
ATTEMPTS = max(1, int(_num("SG_BOARD_READ_ATTEMPTS", "4")))   # bounded: the items route is eventually consistent (#96)
BACKOFF = max(0.0, _num("SG_BOARD_READ_BACKOFF", "1"))        # the base delay, doubled per retry; 0 sleeps for no read
class ReadFailure(Exception):
    """A card the board answered "already there" for, which no read then carried: the read is behind the write, not the card gone."""
    def __init__(self, reads, count): super().__init__(f"the board read never carried it after {reads} reads ({count} items read); a read failure, not an absence")
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
def _save():
    (st / f"rest-items-{owner}-{num}.json").write_text(json.dumps(M))
def items():   # REST only: the items route is a bare array with no total, so name the count read and its source, never a number not read (#96)
    out = api("--paginate", f"{base}/items?per_page=100", "--jq", '.[]|select(.content.number)|[.content.number,.id,(.content.repository_url//"")]|@json')
    m = {str(n): i for n, i, ru in (json.loads(l) for l in out.splitlines() if l.strip()) if not repo or ru.endswith("/" + repo)}
    print(f"board.py: items read {len(m)}; project total: none on the REST items route (source: {base}/items, a bare array)", file=sys.stderr)
    return m
M = cached(f"rest-items-{owner}-{num}.json", 600, items)
def item_id(n, add_missing=False):
    """The one reader every caller uses (move, --add, --run-fields, --clear-run). A miss re-reads the items a bounded
    number of times with a growing delay: the items route is eventually consistent, so a card written a moment ago is
    not always in the next read (#96). None means a card no read ever carried and nothing was written for it here."""
    global M
    key = str(n)
    if key not in M: M = items(); _save()
    if key in M: return M[key]
    wrote = False   # an add that landed, or a refusal that proves the card is on the board: the write is done, the read is behind
    if add_missing and repo:
        iid = json.loads(api(f"repos/{repo}/issues/{n}"))["id"]
        try: M[key] = json.loads(api("-X", "POST", f"{base}/items", "--input", "-", inp=json.dumps({"type": "Issue", "id": iid})))["id"]; _save(); return M[key]
        except RuntimeError as e:
            if "already exists" not in str(e):   # the add itself was refused, not the read: re-read once to repair a stale cache, and only then is the refusal the add's
                M = items(); _save()
                if key in M: return M[key]
                raise
            wrote = True   # the refusal proves the card is there: only the read is behind it
    for k in range(ATTEMPTS):
        if k and BACKOFF: time.sleep(BACKOFF * (2 ** (k - 1)))
        M = items(); _save()
        if key in M: return M[key]
    if wrote: raise ReadFailure(ATTEMPTS, len(M))
    return None   # a read that never carried it, and nothing was written for it here: genuinely absent
def carried_status(n):
    """The Status option id the board carries on the card for n, "" when the card carries none (or no read carried the
    card). The read asks for the Status field by id: a single-select value is carried only when the field is asked for,
    the way janitor.py reads it, and without `fields[]` the route answers the title alone, so a Status could never be
    read back. This is the proof of a write (#105): the answer to a PATCH does not carry the field the board kept."""
    out = api("--paginate", f"{base}/items?per_page=100&fields[]={F['id']}", "--jq",
              '.[]|select(.content.number)|[.content.number,((.fields[]?|select(.name=="Status")|.value.id)//"")]|@json')
    return {str(n): str(i) for n, i in (json.loads(l) for l in out.splitlines() if l.strip())}.get(str(n), "")
if run_fields or clear_run:
    n = int(args[2])
    try: iid = item_id(n)
    except ReadFailure as e: print(f"#{n}: {e}", file=sys.stderr); sys.exit(1)
    if not iid: print(f"#{n} not on the board", file=sys.stderr); sys.exit(1)
    tf = cached(f"rest-text-fields-{owner}-{num}.json", 86400, text_fields)
    if "Run" not in tf or "Run started" not in tf:
        (st / f"rest-text-fields-{owner}-{num}.json").unlink(missing_ok=True); tf = text_fields()
    missing = [x for x in ("Run", "Run started") if x not in tf]
    if missing: sys.exit("no text field " + ", ".join(missing))
    run = None if clear_run else (args[3] or None); started = None if clear_run else ((args[4] if len(args) > 4 else "") or None)   # None is JSON null: the value that clears a field, where an empty string is one GitHub refuses (#101)
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
    except ReadFailure as e: print(f"#{n}: {e}", file=sys.stderr); rc = 1; continue   # the card is on the board and the read has not caught up: named, never "not on the board"
    except RuntimeError as e: print(f"#{n}: {e}", file=sys.stderr); rc = 1; continue   # the add GitHub refused twice, named: the caller logs it
    if not iid: print(f"#{n} not on the board", file=sys.stderr); rc = 1; continue
    want, refused, written = F["opt"][status], "", False
    for k in range(ATTEMPTS):   # the write is written again, then read back: a PATCH answered ok can be a write the card never carries (#105)
        if k and BACKOFF: time.sleep(BACKOFF * (2 ** (k - 1)))
        try: api("-X", "PATCH", f"{base}/items/{iid}", "--input", "-", inp=json.dumps({"fields": [{"id": F["id"], "value": want}]}))
        except RuntimeError as e: refused = str(e)   # the refusal is kept, not raised: the read then confirms it or disproves it
        if carried_status(n) == want: written = True; break
    if written:
        if refused: print(f"#{n}: {refused}; the card carries {status} and the read proves the write", file=sys.stderr)   # the note, never the verdict
        print(f"#{n} -> {status}")
    else:
        M.pop(str(n), None); (st / f"rest-items-{owner}-{num}.json").write_text(json.dumps(M))   # a card whose write is unproven is never cached as written
        print(f"#{n}: the board carries no {status!r} on it after {ATTEMPTS} reads" + (f" ({refused})" if refused else "") + "; a read failure, not a written card", file=sys.stderr); rc = 1
(st / "board-dirty").touch(); sys.exit(rc)
