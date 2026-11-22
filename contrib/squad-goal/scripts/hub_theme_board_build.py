# One hub theme (#2742). hub.py compiles this file into its own module namespace, at its place in the hub's one
# namespace, so every name the hub defined before it -- and every name a later theme defines -- is a plain global.
_HOLDERS_READ = {}   # the served board read every holder for its key just before this build, so the build reads none again
def board(moves=None):
    if not _HOLDERS_READ.get("v"): _HOLDER_CACHE.clear(); _ACTIVE_RUNS.clear()   # the memo answers for one board build, never across two: its fact is the run's liveness, which changes with no board read, so a later build asks again (#1790)
    overlay = overlay_on()   # one setting turns the overlay off: the served board is then the list read alone (#651)
    if moves is None: moves = read_moves() if overlay else {}   # the serve passes the record it already read, so one serve reads it once (#973); the record is read on every serve, so a page reload sees the same overlay (#650)
    window = pending_window()   # an entry older than the window expires even with no read agreeing (#651)
    agreed, expired = set(), {}   # expired: item id -> (card number, the recorded column, the column the read holds, the move's dispatch context)
    preds = predictions() if overlay else {}   # the writes the ledger holds pending: the board's next read, predicted (#1841)
    pred_defects, seen = [], set()   # seen: every issue number the cache carried this pass
    by, tally, drawn = {}, {}, []
    planned = spec_plan_parents(board_cache["items"])
    ends = {COLUMN["issue_closed"], COLUMN["abandoned"]}   # history by role: a closed status is never a column on any page (#969)
    flying, refused = flight_snapshot()   # the writes in flight draw in Todo, pending; a refusal names its reason on its card (#1300)
    flying = set(flying)
    taken = taken_map()   # the takeovers a card wears, read once per serve beside the holders it names
    holds = live_hand_holds()   # the issues a live hand hold names, read once per serve (#2501)
    for i in board_cache["items"]:
        seen.add(int(i["n"]))
        d = flight_draw(i) if i["n"] in flying else drawn_column(i, moves, window)   # the one overlay rule the promote route asks too (#829)
        pred = preds.get(int(i["n"]))
        pd = None
        if pred is not None:   # the writer's own fact outranks the cache, and a newer instant wins between two writers (#1841)
            closed_i = str(i.get("s")).lower() == "closed"
            read_col = _columns_module.view_column(i["st"], closed_i, i.get("l"), i.get("cr"))[0]
            pd = predicted_draw(pred, read_col, i.get("ot"), window, closed_i, i.get("l"), i.get("cr"))
            entry_at = move_time(d.get("entry")) if isinstance(d.get("entry"), dict) else None
            if pd is not None and (entry_at is None or pd["at"] >= entry_at):
                d = pd
            else: pd = None
        st, fz = d["st"], d["fz"]
        if d["state"] == "agree": agreed.add(str(i["i"]))
        elif d["state"] == "expired": expired[str(i["i"])] = (i["n"], d["recorded"], st, has_context(d["entry"]))
        row = {"n": i["n"], "t": i["t"], "c": i.get("c"), "l": i.get("l") or []}
        if d["state"] == "pending": row["pd"] = True   # the mark the page draws; the rule lives here, never in the view (ADR-0137)
        if pd is not None and pd["defect"]:   # a write no read carried within its bound: a named defect, never a silent card
            row["pdx"] = pd["defect"]
            pred_defects.append({"n": i["n"], "column": pd["recorded"], "by": pred["by"], "defect": pd["defect"],
                                 "at": pd["at"], "window": window})
        if i["n"] in refused and i["n"] not in flying: row["mr"] = refused[i["n"]]   # the writer's reason, on the card until the owner moves it again (#1300)
        if fz: row["fz"] = fz
        if _columns_module.SPEC_LABEL in row["l"]: row["sp"] = True   # a spec is an issue with the spec label: the Specs filter reads this
        if isinstance(i.get("pa"), int): row["pa"] = i["pa"]   # the parent spec the read carried (#829): the swimlane groups by it, with no read of its own (#890)
        # The owner's one move (#560): a card the rule marks promotable carries the arrow that starts it, a spec included:
        # the owner's move starts the spec and its open Backlog tickets follow (ADR-0142 section 6, #838). The rule lives
        # here, not in the page: the card renders the fact its row carries (ADR-0137). `pmt` names the column the move
        # goes to, so the page that drags the card (#829) holds no column list of its own.
        if promotable({**i, "st": st}):   # the column the board draws, which a proved move's overlay may have changed
            row["pm"] = True
            row["pmt"] = PROMOTE_TO
        if i["n"] in planned and _columns_module.SPEC_LABEL in row["l"] and _columns_module.view_column(st, False, i.get("l"))[0] == COLUMN["unscheduled"]:
            row["sb"] = True   # the start control: a Backlog spec whose plan holds a ticket (#1128); the count is asked on the press only
        if i.get("b"): row["b"] = i["b"]
        row["pr"] = board_priority.card_mark(i)   # the priority icon, tone and label the card wears; a card with no value is normal (#1981)
        with PRIORITY_LOCK:   # the owner's written level draws at once with its pending chip; a refusal keeps the read's value (#2289)
            want = PRIORITY_FLIGHT.get(i["n"])
            why = PRIORITY_REFUSED.get(i["n"])
        if want is not None: row["pr"] = board_priority.mark(want); row["prp"] = True
        if why is not None and why[2] == board_cache.get("t"): row["prr"] = why[1]   # a refusal lasts until the board is read again
        if isinstance(i.get("gt"), (int, float)) and i["gt"] > 0:
            row["gt"] = int(i["gt"])   # the card's Gate Tries, off the field the read carried (#1255)
            row["gchip"] = f"{_columns_module.TESTING} · {int(i['gt'])}"   # the chip text is a fact the row carries: no renderer names the column (#1255)
        if int(i["n"]) in holds: row["hh"] = True   # a live hand hold: the page draws its own chip, never a filter (#2501)
        sc = task_count(i)                       # the card's own count, a value: the page renders it and holds no rule
        if sc: row["sc"] = sc
        ho, hs = holder_fact(i)                  # the run that holds the card, read from the seam (#241); `hs` says whether its process lives (#1661)
        if ho: row["ho"], row["hs"] = ho, hs
        hc = holder_chip(i, taken, hs)           # the short text the chip wears: the holder, its live state, or a takeover's time
        if hc: row["hc"] = hc
        if (i.get("h") or {}).get("run"): row["hr"] = i["h"]["run"]   # the run id the sheet selects its cards by (#919)
        drawn.append((i["n"], st, row.get("pa"), label_wave(i.get("l") or []), _columns_module.SPEC_LABEL in row["l"]))
        if st in ends: tally[st] = tally.get(st, 0) + 1   # a closed row is never served: its count and the summaries carry it (#972)
        else: by.setdefault(st, []).append(row)
    # A card the cache lacks, whose write the ledger holds pending, is still drawn (#1841): the read has not carried the
    # write yet, and a prediction is never a silently missing card. The pass's own instant is the read's clock here, because
    # the whole read is what failed to carry the card; past the bound the card wears the defect's name.
    for n, pred in sorted(preds.items()):
        if n in seen: continue
        pd = predicted_draw(pred, None, board_cache.get("t"), window, False, [], None)
        if pd is None: continue
        row = {"n": n, "t": f"#{n}", "c": None, "l": [], "pd": True}
        if pd["defect"]:
            row["pdx"] = pd["defect"]
            pred_defects.append({"n": n, "column": pd["recorded"], "by": pred["by"], "defect": pd["defect"],
                                 "at": pd["at"], "window": window})
        if pd["st"] in ends: tally[pd["st"]] = tally.get(pd["st"], 0) + 1   # a card in a terminal column is counted, never served (#972)
        else: by.setdefault(pd["st"], []).append(row)
        drawn.append((n, pd["st"], None, None, False))
    # The reverse of `blocked by`, one map so the view holds no rule of its own (ADR-0137, #919): a card carries the cards
    # it blocks, and the sheet renders that served fact.
    allrows = [r for rs in by.values() for r in rs]
    blocks = {}
    for r in allrows:
        for b in r.get("b") or []: blocks.setdefault(b, []).append(r["n"])
    for r in allrows:
        if r["n"] in blocks: r["bl"] = sorted(blocks[r["n"]])
    names = ORDER + sorted(n for n in by if n not in ORDER)   # every shipped column shows, even at 0; a user column follows
    def wave(x):   # `wave:NN` label first, then issue number; cards with no wave go last
        w = label_wave(x["l"])
        return (10**9 if w is None else w, x["n"])
    groups = [{"name": n, "end": n in ends, "color": COLUMN_COLORS.get(n) or "", "n": tally.get(n, 0) if n in ends else len(by.get(n, [])), "items": sorted(by.get(n, []), key=wave)} for n in names]   # `color` is the column's own fill, so a viewer draws it without a palette (#1604)
    prs = sorted(board_cache["prs"], key=lambda p: p["u"], reverse=True)
    for x in prs:
        if x.get("br"): x.setdefault("gs", None); x.setdefault("pb", None); x["kind"] = _columns_module.BRANCH_KIND; continue   # a work branch has no review strip and no badge: its chip is its standing against dev
        x["ps"] = pr_state(x); x["gs"] = strip_instant(gate_strip(x, t=board_cache.get("t")), board_cache.get("t")); x["pb"] = pr_badge(x); x["kind"] = _columns_module.PR_KIND   # the chip, the R C T B H strip under it (#1772), the lane's badge (#247)
    # Every card names its repository (#1820): `r` is the full owner/name (what a link and the popup read), `rt` the tag
    # the card wears (MesenAI / agent-squad, or owner/repo when the short name names more than one repository).
    # Every row carries its own kind (#440): a pull request is `pr`, a work branch is `branch`. A work branch is served by
    # no view of the board (owner, 2026-10-16: the Kinds view went): it is read from the landing ledger and the issue card.
    cases = []   # the rows beside the axis, from the draft items the pass already read: the age is the pass's own clock against the pass's end
    for title in board_cache.get("cases") or []:
        m = TEST_CASE_TITLE.match(title)
        if m: cases.append({"issue": int(m[1]), "result": m[2], "commit": m[3], "ended": m[4]})   # an absolute instant: the page counts the age, so time passing never changes the body (#973)
    perr = "; ".join(board_cache.get("errs") or [])   # the repositories this pass could not read, named on the column
    # The lane carries the pull requests only (#440): a work branch has no lane and no other view. Each row names its own
    # kind, so the page reads a pull request by the row, never by the lane's name. An empty lane is still served, `n` 0.
    open_prs = [p for p in prs if p["kind"] == _columns_module.PR_KIND]
    # The open pull requests that close a card, on that card's row (#919): the sheet names the PR and its CI from served
    # facts, the same reverse lookup `blocks` makes, never from a rule in the view (ADR-0137).
    closes = {}
    for x in open_prs:
        for n in x.get("cl") or []:
            closes.setdefault(n, []).append({"n": x["n"], "t": x["t"], "st": (x.get("ps") or [None, None])[1] if isinstance(x.get("ps"), list) else None,
                                             "ci": x.get("rollup"), "r": x.get("r") or REPO})
    for g in groups:
        for r in g["items"]:
            if r["n"] in closes: r["prf"] = sorted(closes[r["n"]], key=lambda p: p["n"])
    # A count of one or more is a true lower bound even when a repository failed; a zero is a claim about every repository,
    # so beside a failed read it is `?`, the word the strip uses for a fact not read, never a count (#851).
    count = "?" if not open_prs and perr else len(open_prs)
    lane = {"name": COLUMN["pr_open"], "tracking": True, "n": count, "err": perr or None,
            "items": [{"n": p["n"], "t": p["t"], "u": p["u"], "ps": p["ps"], "gs": p.get("gs"), "pb": p.get("pb"),
                       "rollup": p.get("rollup"), "ci": p.get("ci"), "o": p.get("o"), "by": p.get("by"), "cl": p.get("cl"),
                       "r": p.get("r") or REPO, "rt": repo_tag(p.get("r") or REPO), "kind": p["kind"]} for p in open_prs]}
    if agreed or expired:
        _served_board["settling"] = True   # a build that settles entries is not kept: the next serve retries a drop that failed (#651, #973)
        dropped = drop_moves(agreed | set(expired))   # agreement settles an entry; the window settles one no read ever agreed with (#651)
        if dropped:
            with MOVE_LOCK:
                for item in dropped: PROVED.pop(item, None)   # the entry is gone: its proof goes with it (#1300)
        for item, (n, recorded, read, context) in expired.items():
            if dropped and item in dropped: report_expired(n, item, recorded, read, window, context)   # only a drop that really happened is reported, so a retry never repeats a line
    # A card the fast read has seen and the full read has not written yet is not drawn (#985): the count names it, so the
    # page never shows a board that silently lacks a card just added to the project.
    pending_new = {fd_int(fd) for fd in board_cache.get("fast_pending") or []} - {fd_int(i.get("fd")) for i in board_cache["items"]}
    pending_new.discard(None)
    if pred_defects: report_predictions(pred_defects)   # the loop's log names each one once, whatever the page does
    return {"t": board_cache["t"], "overdue": board_read_overdue(board_cache["t"]), "err": board_cache["err"] or board_stale(), "pending_new": len(pending_new), "pred": pred_defects, "parts": board_cache.get("parts", {}), "prs": lane, "groups": groups, "cases": cases, "done": COLUMN["issue_closed"], "labels": sorted({l for i in board_cache["items"] for l in (i.get("l") or [])}), "lc": board_cache.get("lc", {}), "limits": board_column_limits(), "sum": dict(board_summary(drawn), none=nospec_count(board_cache["items"], moves, window))}   # `limits` is the card limit of each column, shown as `n / max` (#2287); `done` names the delivered column, so the phone counts done tickets without naming a column (#1604); sum is the No spec lane's bar (owner)

