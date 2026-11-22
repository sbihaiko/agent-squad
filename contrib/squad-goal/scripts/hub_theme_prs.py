# One hub theme (#2742). hub.py compiles this file into its own module namespace, at its place in the hub's one
# namespace, so every name the hub defined before it -- and every name a later theme defines -- is a plain global.
ORDER = [column["name"] for column in BOARD_COLUMNS]
FLOW_BANDS = [*ORDER]   # the chart's bands: the columns in their order; each band's held cards travel with it (#1024)
def _marker_mtime(kind, pr, sha9):
    """mtime of a loop marker in auto/ (`launched-<pr>-<sha9>`, `rereview-<pr>-<sha9>`), i.e. when the loop acted on this head."""
    try: return (STATE / "auto" / f"{kind}-{pr}-{sha9}").stat().st_mtime
    except OSError: return None
def rereview_minutes():
    """The loop's own re-queue window (SG_REREVIEW_MINUTES, 20 min by default): how long a head waits for the next attempt."""
    try: return float(os.environ.get("SG_REREVIEW_MINUTES") or 20)
    except ValueError: return 20.0
def review_rows(pr):
    """review-times.tsv rows for PR `pr`: one per model attempt (timestamp, PR, model, seconds, exit code), written by review.sh."""
    rows = []
    try: lines = (STATE / "review-times.tsv").read_text(errors="ignore").splitlines()
    except OSError: return rows
    for l in lines:
        f = l.rstrip("\n").split("\t")
        if len(f) < 5 or f[1] != str(pr) or not f[3].isdigit() or not f[4].lstrip("-").isdigit(): continue
        try: ts = time.mktime(time.strptime(f[0].strip(), "%Y-%m-%dT%H:%M:%S"))
        except ValueError: continue
        rows.append({"ts": ts, "model": f[2], "secs": int(f[3]), "exit": int(f[4])})
    return rows
def review_attempts(pr, sha9):
    """The loop's review attempts on PR `pr`'s current head `sha9`, newest first. `launched-<pr>-<sha9>` (or a re-queue in
    `rereview-<pr>-<sha9>`) is the loop's record that it started work on this head: a row written before that belongs to the
    head that moved, so a new head leaves no attempts and the card goes back to `awaiting review`."""
    start = _marker_mtime("launched", pr, sha9)
    if start is None: start = _marker_mtime("rereview", pr, sha9)
    if start is None: return []
    att = [r for r in review_rows(pr) if r["ts"] >= start - 5]   # 5 s of slop: the row follows the marker, never precedes it
    for r in att: r["result"] = "passed" if r["exit"] == 0 else ("timed out" if r["exit"] == 142 else "failed")
    return sorted(att, key=lambda r: r["ts"], reverse=True)
def summary_lines(pr, sha9):
    """The run summaries of revci-<pr>.out for head `sha9`: the `#<pr> <sha9> ` namespace carries the run's summary line
    only, and a summary always carries `CI=`. The run's progress and terminal notices (CI_DISPATCH, SUPERSEDED,
    CI_TIMEOUT) are printed with a `NOTICE ` prefix instead, so no reader can take one for a result (#1767)."""
    try: ls = (STATE / f"revci-{pr}.out").read_text(errors="ignore").splitlines()
    except OSError: return []
    return [l for l in ls if l.startswith(f"#{pr} {sha9} ") and "CI=" in l]
def _verdict_of(pr, sha9):
    """The verdict of the last completed run of the review loop for this head, from the revci-<pr>.out summary line."""
    ls = summary_lines(pr, sha9)
    return (ls[-1].split("VERDICT:", 1)[1].strip() if ls and "VERDICT:" in ls[-1] else "")
def review_state(pr, sha9, now=None):
    """The card's review chip when the loop's latest attempt on this head did not pass; None keeps today's chip.
    A timeout (exit 142) names the model and the seconds it burned; any other failure names the model and keeps the reason
    (the run's own verdict, else the exit code) in the tooltip. The retry line comes from the loop's re-queue marker.
    Returns [css class, chip text, tooltip, retry line]."""
    att = review_attempts(pr, sha9)
    now = time.time() if now is None else now
    if not att or att[0]["exit"] == 0: return None
    last = att[0]
    reason = _verdict_of(pr, sha9) or f"exit {last['exit']}"
    chip = f"review timed out · {last['model']} · {last['secs']} s" if last["exit"] == 142 else "review failed"
    lines = [f"{a['model']} {a['secs']} s · {a['result']}" for a in att]   # newest first, so the card's tooltip reads top-down
    lines[0] += f" ({reason})"
    retry = ""
    mk = _marker_mtime("rereview", pr, sha9)
    if mk is not None:
        if last["ts"] < mk: retry = "retrying now"   # the re-queue already started the attempt this head is owed
        else:
            left = mk + rereview_minutes() * 60 - now
            retry = "retrying now" if left <= 0 else f"retry in {int(-(-left // 60))} min"
    return {"cls": "changes", "chip": chip, "tip": f"attempts on {sha9}, newest first:\n" + "\n".join(lines), "retry": retry}
GATE_SLOP = 5.0     # the gate line follows the pass stamp by less than a second; 5 s covers a coarse clock
WATCH_CYCLE = 90.0  # one watch.sh cycle: three 30 s sleeps
GATE_IDLE_AFTER = 3 * WATCH_CYCLE   # three cycles with no pass: the loop is not deciding anything, so no card may say it did
def _pass_epoch():
    """auto/.pass: the epoch of the loop's last pass, stamped at the top of every loop pass."""
    try: return float((STATE / "auto" / ".pass").read_text().strip())
    except (OSError, ValueError): return None
def _ago(secs): return f"{int(secs)} s" if secs < 90 else f"{int(round(secs / 60))} min"
def gate_line(n):
    """auto/gate-<n>: `<head sha9><TAB><epoch><TAB><reason><TAB><what it means>`, the pass's latest decision about this PR,
    overwritten at every exit so the file carries the latest reason and only it (#1766). None when the file is absent or
    unreadable — a half-written line cannot be seen, the pass writes it through a temp file + mv."""
    try: f = (STATE / "auto" / f"gate-{n}").read_text(errors="ignore").rstrip("\n").split("\t")
    except OSError: return None
    if len(f) != 4 or not f[1].isdigit(): return None
    return f[0], float(f[1]), f[2], f[3]
def gate_state(n, sha9, now=None):
    """The card's chip from the loop's own reason (#1771), as [css class, chip, tooltip]. The hub used to derive readiness
    from the review line, so a card could claim `ready to merge` for a head the loop was holding — the loop's verdict is
    already on disk, so the card reports it instead of guessing, and the two cannot disagree by construction. The reason in
    auto/gate-<n> is the loop's word only for the head it names, written during the pass auto/.pass stamps; `ready to merge`
    is reserved for a gate line that says the merge is going ahead. On any other head the chip says which honest state
    applies: `loop off` (auto/off), `loop idle` (no pass for three watch cycles), `new head, not seen yet`."""
    now = time.time() if now is None else now
    if (STATE / "auto" / "off").exists():
        return ["idle", "loop off", "auto/off is present: the loop is off, so no card can say what it decided"]
    p = _pass_epoch()
    if p is None or now - p > GATE_IDLE_AFTER:
        last = "has never run" if p is None else f"last ran {_ago(now - p)} ago"
        return ["idle", "loop idle", f"the loop {last} (auto/.pass, idle past {_ago(GATE_IDLE_AFTER)} = three watch cycles)"]
    g = gate_line(n)
    if not g or g[0] != sha9: return ["idle", "new head, not seen yet",
        f"the loop's last reason {'names no head' if not g else f'is for {g[0]}, not {sha9}'}: it has not judged this head"]
    if g[1] < p - GATE_SLOP: return ["idle", "new head, not seen yet",
        f"the loop's last reason is from the pass before this one ({g[0]}, written {_ago(now - g[1])} ago): the running pass has not judged this head"]
    reason, text = g[2], g[3]
    if reason in ("merged", "merging"): cls, chip = "ready", "ready to merge"   # the gate says the merge is going ahead
    elif reason.startswith("hold-"): cls, chip = "changes", "held: " + reason[5:]
    else: cls, chip = "running", reason   # the loop's own word for what it is doing on this head (reviewing, fix-round, ...)
    return [cls, chip, f"{reason}: {text}\n{g[0]} · written {_ago(now - g[1])} ago"]
STRIP_MAX_AGE = 180.0   # the strip's own honesty window: past this the facts are no longer the poll's (#1772)

# One GraphQL query per poll for every open PR: the list, the head and base, the merge state, the labels, the review
# threads, the status rollup, the author and the issues it closes (#440), in place of a REST list plus two REST calls per
# PR every poll (#1772). Nothing in it is optional to the reader: a field the query did not return stays None and renders
# `?`, never a passing word.
OPEN_PR_QUERY = """query{repository(owner:"%s",name:"%s"){pullRequests(first:100,states:OPEN,orderBy:{field:UPDATED_AT,direction:DESC}){nodes{
number title isDraft updatedAt createdAt headRefOid baseRefName mergeStateStatus
author{login}
labels(first:30){nodes{name}}
closingIssuesReferences(first:5){nodes{number}}
reviewThreads(first:100){totalCount nodes{isResolved}}
commits(last:1){nodes{commit{committedDate statusCheckRollup{state contexts(first:100){nodes{__typename ... on CheckRun{databaseId conclusion status} ... on StatusContext{state}}}}}}}}}}}"""

LIVE_BAD = ("failure", "timed_out", "action_required", "startup_failure", "cancelled", "error")
LIVE_WAIT = ("queued", "in_progress", "pending", "waiting", "requested", "expected", "completed")
CI_WAIT = ("queued", "in_progress", "pending", "waiting", "requested")   # a check GitHub reports as unfinished
CI_QUEUED = ("queued",)          # ... and that no runner has picked up: a queue is not a run (#149)
CI_QUEUE_MAX = 20 * 60.0         # the timeout `.github/workflows/ci.yml` gives its job: still queued past it, no run started

def _live_states(rollup):
    """The states of the live rollup's contexts: a CheckRun reports its conclusion once it has one and its status before
    that, a StatusContext reports its state. None when the rollup itself is absent (unknown, not empty)."""
    if rollup is None: return None
    out = []
    for c in ((rollup.get("contexts") or {}).get("nodes") or []):
        v = c.get("conclusion") or c.get("status") or c.get("state")
        if v: out.append(str(v).lower())
    return out

def _live_rollup(states):
    """The live rollup in the same coarse words the loop's own `CI=` snapshot is read in: pass, fail, pending, none."""
    if states is None: return None
    if any(s in LIVE_BAD for s in states): return "fail"
    if any(s in LIVE_WAIT for s in states): return "pending"
    return "pass" if states else "none"

CLOSES_RE = re.compile(r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)", re.I)

def _title_closes(title):
    """The issue numbers a title names as closed (`Closes #12`, `Fixes #34`): the fallback for a plan whose query does not
    carry `closingIssuesReferences`, read from the title alone (#440). The body is not in the query, so it is never read."""
    return [int(m) for m in CLOSES_RE.findall(title or "")]

def prs_from_graphql(payload, repo=None):
    """One row per open PR out of the batched OPEN_PR_QUERY response, or None when the call returned nothing usable
    (failed, rate-limited, no repository): None means the caller keeps the last good rows, never that no PR is open
    (#1772). A fact the query did not return stays None, so the strip renders `?` for it. The row also carries the author
    (`by`) and the issues the PR closes (`cl`, from `closingIssuesReferences`, or the title when the plan lacks it) (#440).
    `repo` is the repository the payload was read from: it rides on every row as `r`, so a card links, opens and resolves
    its issue in its own repository (#1820)."""
    data = payload.get("data") if isinstance(payload, dict) else None
    nodes = (((data or {}).get("repository") or {}).get("pullRequests") or {}).get("nodes")
    if nodes is None: return None
    rows = []
    for nd in nodes:
        commit = ((nd.get("commits") or {}).get("nodes") or [{}])[-1].get("commit") or {}
        contexts = ((commit.get("statusCheckRollup") or {}).get("contexts") or {}).get("nodes") or []
        states = _live_states(commit.get("statusCheckRollup"))
        checks = [{"id": c.get("databaseId"), "status": c.get("status"), "conclusion": c.get("conclusion")} for c in contexts if c.get("__typename") == "CheckRun"]
        th = nd.get("reviewThreads")
        threads = None if th is None or (th.get("totalCount") or 0) > len(th.get("nodes") or []) \
            else sum(1 for t in (th.get("nodes") or []) if not t.get("isResolved"))
        labels = [l["name"] for l in ((nd.get("labels") or {}).get("nodes") or [])] if nd.get("labels") is not None else None
        ms = "" if (nd.get("mergeStateStatus") or "").upper() == "UNKNOWN" else (nd.get("mergeStateStatus") or "").lower()
        base = nd.get("baseRefName") or None
        cr = nd.get("closingIssuesReferences")   # the issues the PR closes, from the same query (#440); absent on a plan that does not carry it
        closes = [x["number"] for x in (cr.get("nodes") or []) if isinstance(x, dict) and isinstance(x.get("number"), int)] \
            if cr is not None else _title_closes(nd.get("title"))
        author = (nd.get("author") or {}).get("login")
        rows.append({"n": nd["number"], "t": nd.get("title") or "", "d": bool(nd.get("isDraft")), "u": nd.get("updatedAt") or "",
                     "o": nd.get("createdAt") or "", "h": nd.get("headRefOid") or "", "base": base, "ms": ms, "l": labels, "by": author, "cl": closes,
                     "th": threads, "ci": states, "checks": checks, "hc": commit.get("committedDate") or None, "rollup": _live_rollup(states), "r": repo,
                     "facts": not (states is None or threads is None or labels is None or not ms or not base)})
    return rows

def gh_door_read(*args):
    """One GitHub read through the door, `ghcall.sh` (ADR-0277): the answer on stdout, or an error carrying the door's
    own line -- GitHub's refusal, or a local failure before GitHub reached a verdict (#252). A read that did not answer
    never reaches a parser, so the column names GitHub's message instead of the first line of its error body."""
    door = pathlib.Path(__file__).resolve().parent / "ghcall.sh"
    try:
        p = subprocess.run(["bash", "-c", '. "$0"; "$@"', str(door), *args], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(f"the GitHub read did not finish: {e}") from e
    if p.returncode: raise RuntimeError(" ".join(p.stdout.split()) or f"the GitHub read exited {p.returncode}")
    return p.stdout

LAND = os.environ.get("SG_LAND_BRANCH") or "dev"   # the branch a work branch lands in: a branch is open while it holds commits this one lacks
WORK_BRANCH = re.compile(r"(?:fix|gui)/(\d+)(?:\D.*)?$")

_compares = {}   # (repo, landing sha, branch sha) -> the compare answer the rows read (#524)

def open_branches_of(repo):
    """The open work branches of ONE repository: every remote `fix|gui/<n>-...` branch with commits the landing branch lacks.
    A row is `{"n": issue, "t": branch, "u": date of its newest commit, "ps": "ahead N", "br": True, "r": repo}`. No pull
    request is read: the branch list and one compare per work branch, through the door."""
    rows = []
    branches = json.loads(gh_door_read("gh_api", f"repos/{repo}/branches?per_page=100") or "[]")
    land = next((b["commit"]["sha"] for b in branches if b.get("name") == LAND), None)
    for b in branches:
        m = WORK_BRANCH.match(b.get("name") or "")
        if not m: continue
        # A compare is a function of the two tips: kept by both shas, it is read again only when one moves (#524). One per
        # work branch on every pass spent the REST quota.
        key = (repo, land, b["commit"]["sha"])
        cmp = _compares.get(key) if land else None
        if cmp is None:
            cmp = json.loads(gh_door_read("gh_api", f"repos/{repo}/compare/{LAND}...{b['commit']['sha']}") or "{}")
            if land:
                for k in [k for k in _compares if k[0] == repo and k[1] != land]: del _compares[k]   # the landing tip moved: the old answers never return
                _compares[key] = {"status": cmp.get("status"), "ahead_by": cmp.get("ahead_by"), "commits": (cmp.get("commits") or [])[-1:]}
        if cmp.get("status") not in ("ahead", "diverged"): continue   # the tip is in the landing branch: the branch is landed, not open
        last = ((cmp.get("commits") or [{}])[-1].get("commit") or {}).get("committer") or {}
        rows.append({"n": int(m.group(1)), "t": b["name"], "u": last.get("date") or "", "o": "", "h": b["commit"]["sha"],
                     "ps": f"ahead {cmp.get('ahead_by', 0)}", "br": True, "r": repo})
    return rows

def _door_rollup(check):
    """The live rollup of a pull request from the door's check counts: the words `_live_rollup` gives, or None when unread."""
    if not check or check.get("state") is None: return None
    if check.get("failed"): return "fail"
    if check.get("pending"): return "pending"
    return "pass" if check.get("total") else "none"

def open_prs_of(repo):
    """The open pull requests of ONE repository, through the door's open-list read (ADR-0277): one aggregate request. A
    refusal or a failure raises, so the caller keeps that repository's last good rows instead of an empty lane (#851).
    A fact the door's read does not return stays None, so the page draws `?` for it and never a passing word."""
    r = ghread.read(f"repos/{repo}/pulls?state=open&per_page=100")
    if not r.is_answer: raise RuntimeError(r.line())
    return [{"n": p["number"], "t": p.get("title") or "", "d": bool(p.get("draft")), "u": "", "o": "",
             "h": (p.get("head") or {}).get("sha") or "", "base": (p.get("base") or {}).get("ref") or None, "ms": "",
             "l": [l.get("name") for l in p.get("labels") or []], "by": None, "cl": None, "th": None, "ci": None, "checks": [],
             "hc": None, "rollup": _door_rollup(p.get("check_rollup")), "r": repo, "facts": False} for p in r.answer]

def open_rows_of(repo):
    """Every open row of ONE repository: its open work branches and its open pull requests (#851)."""
    return open_branches_of(repo) + open_prs_of(repo)

def _landed_ledger():
    """The rows `branchland.sh` keeps for each branch it put in the landing branch: epoch, branch, landed sha, issue."""
    try: lines = (STATE / "landed.tsv").read_text().splitlines()
    except OSError: return []
    return [f for f in (l.split("\t") for l in lines) if len(f) >= 4 and f[0].isdigit() and f[3].isdigit()]

def landed_branches_of(repo):
    """The latest page of the branches landed in the landing branch, from the ledger (a landed branch is deleted, so the
    remote no longer lists it). Each row names the branch and the landed sha, and carries the landing time as `c`.

    The board draws no lane for these rows (#384): a landed branch is read from its issue card, Done in dev. The pass
    keeps them for its own last-landing fact and for the run a merge already delivered."""
    return [{"n": int(f[3]), "t": f"{f[1]} · {f[2][:7]}", "c": dt.datetime.fromtimestamp(int(f[0]), dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "m": True, "r": repo} for f in _landed_ledger()[-100:]]

def prs_pass(fetch_open=None, fetch_landed=None, repos=None):
    """One read of the open branch lane and of the landing ledger for EVERY repository the hub is told to read (#1820),
    in place of the single-repository read the column carried before it. Each repository is read on its own, through the
    two fetchers it is given (the seam the tests stub), and contributes its own rows -- each row named by `r`.

    A repository the hub cannot read adds its error to the returned `errs` and keeps the last good rows of ITS OWN from
    the cache: an unreachable repository is an error on the lane, never a silently empty one, and never a reason to
    drop the rows of the repository that answered. Returns (rows, landed, errs)."""
    fetch_open = fetch_open or open_rows_of
    fetch_landed = fetch_landed or landed_branches_of
    repos = REPOS if repos is None else repos
    last_prs, last_landed = board_cache.get("prs") or [], board_cache.get("landed") or []
    prs, landed, errs = [], [], []
    for r in repos:
        try:
            rows = fetch_open(r)
            if rows is None: raise ValueError("the open-branch query returned nothing; the last good rows stay in place")
            rows_landed = fetch_landed(r) or []
        except Exception as e:
            errs.append(f"{r}: {str(e)}")   # the whole message: the door's refusal line carries GitHub's own words (#252)
            prs += [x for x in last_prs if x.get("r") == r]     # this repository's own last good rows, and no other's
            landed += [x for x in last_landed if x.get("r") == r]
            continue
        prs += rows; landed += rows_landed
    return prs, landed, errs

def _ci_state(tok):
    """The coarse state of a `CI=` snapshot (`success:9`, `failure:1,success:8`, `none`, `skipped`): pass, fail, pending, none."""
    parts = [p.split(":")[0] for p in (tok or "").split(",") if p]
    if not parts: return "?"
    if any(p in LIVE_BAD for p in parts): return "fail"
    if any(p in LIVE_WAIT for p in parts): return "pending"
    return "pass" if "success" in parts else "none"

def ci_snapshot(n, sha9):
    """The loop's own `CI=` word for this head, out of the run's summary line; None when it printed none for this head."""
    ls = summary_lines(n, sha9)
    return ls[-1].split("CI=", 1)[1].split()[0] if ls and "CI=" in ls[-1] else None

def _verdict_token(v):
    """`OK`, `CHG`, `BLK` for the loop's verdict text, `?` for a word this strip does not know."""
    if v.startswith("APPROVE"): return "OK"
    if v.startswith("CHANGES"): return "CHG"
    if v.startswith("BLOCKED"): return "BLK"
    return "?"

_REVFORMAT = {}
def _revformat():
    """revformat.py, the module that reads a finding's severity off the section it sits in (#1777). The strip asks the
    same module the merge path asks, so a debt-only CHANGES cannot be called blocking on one surface and merged on the other."""
    if "m" not in _REVFORMAT:
        try: _REVFORMAT["m"] = importlib.import_module("revformat")
        except Exception:
            spec = importlib.util.spec_from_file_location("revformat", pathlib.Path(__file__).resolve().parent / "revformat.py")
            m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); _REVFORMAT["m"] = m
    return _REVFORMAT["m"]

def review_debt_only(n):
    """True when the review the loop wrote for this PR raises DEBT only (`blocking=0 debt>0`). The loop's own passes treat one
    as an approval, so no card may render it as blocking (#1772). A module that cannot answer is not an answer:
    no debt claim without it."""
    try: review = (STATE / f"review-{n}.md").read_text(errors="ignore")
    except OSError: return False
    if not review.strip(): return False
    try: c = _revformat().count(review)
    except Exception: return False
    return c.get("Blocking", 0) == 0 and c.get("Debt", 0) > 0

def _summary_any_head(n):
    """Every summary line the loop printed for this PR, whatever head it names."""
    try: ls = (STATE / f"revci-{n}.out").read_text(errors="ignore").splitlines()
    except OSError: return []
    return [l for l in ls if l.startswith(f"#{n} ") and "CI=" in l]

def review_cell(n, sha9):
    """The R cell: the review verdict *on this head* (#1772). `-` = the loop has not judged this head, and the tooltip
    says which head its last word was about; `...` = it queued a review for this head; `debt` = a debt-only CHANGES."""
    v = _verdict_of(n, sha9)
    if v:
        tok = _verdict_token(v)
        if tok == "CHG" and review_debt_only(n): tok = "debt"
        tip = f"the loop's review of this head ({sha9}): VERDICT: {v}"
        if tok == "debt": tip += " -- a debt-only CHANGES: the loop's own passes treat it as an approval, so it does not block"
        return tok, tip
    old = _summary_any_head(n)
    if old:
        osha, oline = old[-1].split()[1], old[-1]
        ov = oline.split("VERDICT:", 1)[1].strip() if "VERDICT:" in oline else ""
        if osha != sha9 and ov.startswith("APPROVE"):
            return "-", f"the loop's APPROVE is on {osha}, an older head (this head is {sha9}): an APPROVE is a word about the head it names"
        if osha != sha9: return "-", f"the loop's last review named {osha}, not this head ({sha9})"
    if (STATE / "auto" / f"launched-{n}-{sha9}").exists():
        return "...", f"the loop queued a review for {sha9} (auto/launched-{n}-{sha9}) and has printed no verdict yet"
    return "-", f"the loop has not reviewed this head ({sha9})"

def gate_strip(x, t=None, now=None):
    """The five-cell strip under the card's chip: R C T B H, each cell its own word and tooltip (#1772).

    R the review verdict on this head, C the loop's own CI snapshot for it (marked `live!=` when GitHub's live rollup
    disagrees), T the live unresolved review threads, B base and merge state, H labels and holds. A fact the batched
    query did not return renders `?` -- unknown never reads as passing -- and the whole strip is `old` past 180 s of
    `t` (the poll's stamp), so a failed poll greys out instead of standing in as a fresh one."""
    now = time.time() if now is None else now
    n, sha9 = x["n"], (x.get("h") or "")[:9]
    age = int(now - (t or 0))
    rv, rtip = review_cell(n, sha9)
    snap = ci_snapshot(n, sha9)
    if snap is None:
        cv = ("?", "the loop has printed no CI snapshot for this head" +
              (f"; GitHub's live rollup says {x['rollup']}" if x.get("rollup") else "; GitHub's live rollup is not in this poll either"))
    elif x.get("rollup") and _ci_state(snap) != x["rollup"]:
        cv = (f"{snap} live!={x['rollup']}", f"the loop's CI snapshot for {sha9} is {snap}; GitHub's live rollup says {x['rollup']} for the same head, so they disagree")
    else:
        cv = (snap, f"the loop's own CI snapshot for {sha9} (the head it was taken on), from the run's summary line")
    th = x.get("th")
    tv = ("?", "the batched query did not return this PR's review threads") if th is None else \
         (str(th), f"{th} unresolved review threads on GitHub (resolved ones are not counted)")
    base, ms = x.get("base") or "?", x.get("ms")
    bv = ("?", "GitHub has not computed the merge state for this head" if x.get("facts") is not False else "the batched query did not return this PR") \
        if not ms else (f"{base}·{ms}", f"base {base}, merge state {ms}")
    labels, holds = x.get("l"), []
    g = gate_line(n)
    if g and g[2].startswith("hold-"): holds.append("hold:" + g[2][5:])
    if x.get("d"): holds.append("draft")
    if labels is None:
        hv = ("?", "the batched query did not return this PR's labels")
    else:
        shown = ", ".join(labels[:3]) + (f" +{len(labels) - 3}" if len(labels) > 3 else "")
        hv = ("-", "no labels, no hold")
        if labels or holds: hv = (" ".join(filter(None, [shown, "·".join(holds)])), f"labels: {', '.join(labels) or 'none'}; holds: {'·'.join(holds) or 'none'}")
    return {"cells": [{"k": "R", "v": rv, "tip": rtip}, {"k": "C", "v": cv[0], "tip": cv[1]}, {"k": "T", "v": tv[0], "tip": tv[1]},
                      {"k": "B", "v": bv[0], "tip": bv[1]}, {"k": "H", "v": hv[0], "tip": hv[1]}],
            "age": age, "old": age > STRIP_MAX_AGE}

_CI_STATE = {}
def _ci_state_module():
    """The no-step rule shared with the landing gate."""
    if "m" not in _CI_STATE:
        spec = importlib.util.spec_from_file_location("ci_state", pathlib.Path(__file__).resolve().parent / "ci_state.py")
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); _CI_STATE["m"] = module
    return _CI_STATE["m"]

def ci_cannot_run(x):
    """The platform stop of a failed check, with its own annotation where GitHub supplied one."""
    repo = x.get("r") or REPO
    for check in x.get("checks") or []:
        ident = check.get("id")
        if not ident or check.get("conclusion") not in ("failure", "startup_failure"): continue
        try:
            job = gh_json(f"repos/{repo}/actions/jobs/{ident}")
            detail = gh_json(f"repos/{repo}/check-runs/{ident}")
            if _ci_state_module().cannot_run(job, detail): return _ci_state_module().reason(detail)
        except Exception: continue
    return None

def _head_pushed_age(x):
    """The seconds since the head commit was pushed, from GitHub's own `committedDate` on the row; None when the query
    did not return it. A fact nobody has is unknown, so it never carries a stop (ADR-0018)."""
    at = x.get("hc")
    if not at: return None
    try: return time.time() - dt.datetime.fromisoformat(str(at).replace("Z", "+00:00")).timestamp()
    except ValueError: return None

def pr_state(x):
    """One word for an open PR, from GitHub facts (CI, mergeable state) and the squad's review files (revci-<pr>.out, auto/launched-*).
    Worst first: draft, conflict, ci-failed, changes, review-timeout/failed, ci-cannot-run, ci-waiting, reviewing, ready,
    merged-soon, awaiting-review.
    Everything the card claims about the loop's own decision comes from auto/gate-<pr> instead of being derived (#1771).
    A row the batched query did not fill in says so instead of falling through to a passing word (#1772), and a debt-only
    CHANGES is the loop's to decide, not a block: the loop's own passes treat one as an approval.
    The CI chip reads the word GitHub reports (#149): a check that is `queued` says queued, never that a run is in
    progress -- `queued` and `in_progress` are two different words, and only a runner that picked the check up makes one
    of them a run. A head whose unfinished checks are all still queued past the timeout the ci job gives itself cannot
    be answered at all, and the chip says that instead of implying progress."""
    n = x["n"]; sha9 = (x.get("h") or "")[:9]
    if (STATE / "auto" / "off").exists(): return gate_state(n, sha9)   # the loop is off: no card reports a verdict of its own
    if x.get("d"): return ["draft", "draft"]
    ci = x.get("ci") or []
    if x.get("facts") is False: return ["idle", "facts unknown",
        "the batched open-PR query did not return this PR, so its CI, merge state and threads are unknown -- not passing"]
    if x.get("ms") == "dirty": return ["conflict", "merge conflict"]
    cannot = ci_cannot_run(x)
    if cannot is not None: return ["changes", "CI cannot run: account", cannot or "GitHub reported that the job never started"]
    if any(c in ("failure", "timed_out", "action_required", "startup_failure") for c in ci): return ["broke", "CI failed"]
    line = (summary_lines(n, sha9) or [""])[-1]   # a notice (NOTICE #<pr> <sha9> ...) is not a result: see summary_lines
    if "CHANGES" in line:
        if review_debt_only(n): return gate_state(n, sha9)   # a debt-only CHANGES is not blocking: the loop's own passes treat it as an approval (#1772)
        return ["changes", "changes requested"]
    rs = review_state(n, sha9)   # an attempt on this head failed or timed out: that is what the card is waiting on, not `awaiting review`
    if rs: return [rs["cls"], rs["chip"], rs["tip"], rs["retry"]]
    if "BLOCKED" in line: return ["broke", "review blocked"]
    wait = [c for c in ci if c in CI_WAIT]   # the checks GitHub reports as unfinished
    if wait:
        if all(c in CI_QUEUED for c in wait):   # not one runner has picked a check up: a queue, not a run
            age = _head_pushed_age(x)
            if age is not None and age > CI_QUEUE_MAX:
                return ["broke", "CI cannot run", f"the head {sha9} was pushed {_ago(age)} ago and every unfinished check on it is still queued, past the {int(CI_QUEUE_MAX / 60)} min the job gives itself: no run ever started, so this head answers nothing"]
            return ["running", "CI queued", f"every unfinished check on {sha9} reads queued, not a run in progress: no runner has picked one up" + (f" (head pushed {_ago(age)} ago)" if age is not None else " (the query returned no head date)")]
        return ["running", "CI running"]
    if not line and (STATE / "auto" / f"launched-{n}-{sha9}").exists(): return ["running", "reviewing"]
    if "APPROVE" in line: return gate_state(n, sha9)   # a green review line is not the loop's verdict: the gate file is
    return ["idle", "awaiting review"]
def pr_badge(x):
    """The one state a pull request's lane card wears as a badge (#247): conflict, checks red, approved or green, in that
    precedence; None while nothing is settled. Approved is the review loop's verdict on this head, never a guess."""
    if x.get("ms") == "dirty": return "conflict"
    ci = x.get("ci") or []
    if any(c in LIVE_BAD for c in ci): return "checks red"
    if "APPROVE" in (summary_lines(x["n"], (x.get("h") or "")[:9]) or [""])[-1]: return "approved"
    if ci and x.get("rollup") == "pass": return "green"
    return None
