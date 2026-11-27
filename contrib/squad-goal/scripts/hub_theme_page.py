# One hub theme (#2742). hub.py compiles this file into its own module namespace, at its place in the hub's one
# namespace, so every name the hub defined before it -- and every name a later theme defines -- is a plain global.

_issue_cache = {}
def gh_json(path, timeout=30):
    """One `gh api` read as JSON, or None when it returned nothing (the one seam the issue panel reads through)."""
    return json.loads(subprocess.run(["gh", "api", path], capture_output=True, text=True, timeout=timeout).stdout or "null")

def gh_graphql(query, timeout=30, **vars):
    """One GraphQL read as JSON, or None when it answered nothing (`-F` carries the typed variables, so `number` is an
    Int): the one seam the rendered issue body reads through, and it goes through the door like every other read
    (ADR-0277), so a refusal is never read as an answer."""
    try:
        r = ghread.graphql_read(query, vars, timeout=timeout, pass_name="hub")
        return r.answer if r.is_answer else None
    except (OSError, subprocess.TimeoutExpired, ValueError): return None

# REST carries no rendered body, so the sheet reads GitHub's own HTML through GraphQL (X-Frame-Options keeps the page from
# framing GitHub itself). One read carries the body and the last comment for an issue or a pull request (#919).
ISSUE_HTML_QUERY = ("query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){"
                    "issueOrPullRequest(number:$number){"
                    "... on Issue{bodyHTML comments(last:1){totalCount nodes{author{login} createdAt bodyHTML url}}}"
                    "... on PullRequest{bodyHTML comments(last:1){totalCount nodes{author{login} createdAt bodyHTML url}}}}}}")
def issue_html(n, repo=None):
    """GitHub's rendered HTML for the issue's body and its last comment (#919): `body`, `total`, and `last`, or `{}` when
    the read failed. A failed rendered read never fails the issue read: the sheet falls back to the plain body."""
    repo = repo if repo in REPOS else REPO
    owner, _, name = repo.partition("/")
    p = gh_graphql(ISSUE_HTML_QUERY, owner=owner, name=name, number=int(n)) or {}
    node = (((p.get("data") or {}).get("repository") or {}).get("issueOrPullRequest")) or {}
    if not node: return {}
    cm = node.get("comments") or {}
    nodes = [x for x in (cm.get("nodes") or []) if isinstance(x, dict)]
    last = nodes[-1] if nodes else None
    d = {"body": render_html(node.get("bodyHTML") or ""), "total": cm.get("totalCount") or 0, "last": None}
    if last:
        d["last"] = {"by": (last.get("author") or {}).get("login") or "", "at": last.get("createdAt") or "",
                     "html": render_html(last.get("bodyHTML") or ""), "url": last.get("url") or ""}
    return d

# The rendered body is GitHub's own HTML, so the hub keeps its structure in its own type and colors and allows only what
# cannot run: one allow-list, on this side of the wire, so neither page holds a sanitizer of its own (ADR-0137, #919).
HTML_TAGS = frozenset("details summary hr br b i strong em code kbd sub sup p div span ul ol li table thead tbody tr th td "
                      "h1 h2 h3 h4 h5 h6 blockquote pre a img del s u input".split())
HTML_ATTR = {"a": ("href",), "img": ("src", "alt", "width", "height"), "td": ("colspan", "rowspan", "align"),
             "th": ("colspan", "rowspan", "align"), "details": ("open",), "input": ("type", "checked", "disabled")}
HTML_VOID = frozenset(("br", "hr", "img", "input"))
HTML_SCAFFOLD = re.compile(r"(?is)<(script|style|svg|iframe|object|embed|video|audio)\b[^>]*>.*?</\1\s*>")
HTML_COMMENT = re.compile(r"(?s)<!--.*?-->")
HTML_TAG = re.compile(r"(?s)<(/?)([a-zA-Z][a-zA-Z0-9]*)((?:\s[^<>]*?)?)\s*/?>")
HTML_PAIR = re.compile(r"""([a-zA-Z-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
HTML_BARE = re.compile(r"(?<![=\w-])(checked|disabled|open)(?=[\s/>]|$)")
def render_html(src):
    """GitHub's rendered body passed through the allow-list: the tags it renders keep their structure, a class or style
    never rides, a relative or `javascript:` link is dropped, and a task-list checkbox keeps its look. Anything else is
    its text alone, and a `<` that opens no whole tag is escaped, so a body never reaches the page with a script, a frame or
    a handler."""
    t = HTML_COMMENT.sub("", HTML_SCAFFOLD.sub("", src or ""))
    def one(m):
        close, name, attrs = bool(m.group(1)), (m.group(2) or "").lower(), m.group(3) or ""
        if name not in HTML_TAGS: return ""
        if close: return "" if name in HTML_VOID else f"</{name}>"
        kept = []
        for am in HTML_PAIR.finditer(attrs):
            k, v = am.group(1).lower(), am.group(2) if am.group(2) is not None else am.group(3)
            if k not in HTML_ATTR.get(name, ()): continue
            if k in ("href", "src") and not re.match(r"(?i)^https?://", v): continue
            if k == "type" and v.lower() != "checkbox": continue
            kept.append(f' {k}="{html.escape(v, quote=True)}"')
        for bare in HTML_BARE.finditer(attrs):
            if bare.group(1) in HTML_ATTR.get(name, ()): kept.append(" " + bare.group(1))
        kept = list(dict.fromkeys(kept))
        if name == "a" and any(k.startswith(" href=") for k in kept): kept += [' target="_blank" rel="noopener"']
        if name == "img": kept += [' referrerpolicy="no-referrer" loading="lazy"']
        return f"<{name}{''.join(kept)}>"
    out, last = [], 0
    for m in HTML_TAG.finditer(t):
        out.append(t[last:m.start()].replace("<", "&lt;"))
        out.append(one(m))
        last = m.end()
    out.append(t[last:].replace("<", "&lt;"))
    return "".join(out)
def issue_detail(n, repo=None):
    """Issue (or PR) body + the last 5 comments, fetched on click and cached 60 s. n is an int, never interpolated raw.
    `repo` is the repository the card belongs to (#1820): a number is only a number, and PR #1821 of the tooling
    repository is not PR #1821 of the emulator -- a repository the hub was not told to read is never read, so the panel
    resolves a card in its own repository or in the profile's one."""
    repo = repo if repo in REPOS else REPO
    hit = _issue_cache.get((repo, n))
    if hit and time.time() - hit[0] < 60: return hit[1]
    g = gh_json
    it = g(f"repos/{repo}/issues/{n}")
    if not it or "number" not in it: return {"err": "not found"}
    cm = g(f"repos/{repo}/issues/{n}/comments?per_page=100") or []
    d = {"n": n, "r": repo, "title": it["title"], "state": it["state"], "pr": "pull_request" in it, "url": it["html_url"], "by": it["user"]["login"],
         "created": it["created_at"], "updated": it["updated_at"], "labels": [l["name"] for l in it["labels"]], "body": (it.get("body") or "")[:3000],
         "total": len(cm), "comments": [{"by": c["user"]["login"], "at": c["created_at"], "body": (c.get("body") or "")[:1200]} for c in cm[-5:]],
         "html": "", "last": None}   # the rendered body and last comment are read below, on open (#919)
    if d["pr"]:   # a PR also gets its branch, size, merge state, CI summary and the latest review verdicts
        p = g(f"repos/{repo}/pulls/{n}") or {}
        ck = (g(f"repos/{repo}/commits/{p['head']['sha']}/check-runs?per_page=100") or {}).get("check_runs", []) if p.get("head") else []
        cnt = {}
        for c in ck: k = c.get("conclusion") or c["status"]; cnt[k] = cnt.get(k, 0) + 1
        rv = {}
        for r in g(f"repos/{repo}/pulls/{n}/reviews?per_page=100") or []:
            if r["state"] != "COMMENTED": rv[r["user"]["login"]] = r["state"]
        d["pull"] = {"head": p.get("head", {}).get("ref"), "base": p.get("base", {}).get("ref"), "add": p.get("additions"), "del": p.get("deletions"), "files": p.get("changed_files"),
                     "draft": p.get("draft"), "merged": bool(p.get("merged_at")), "mstate": p.get("mergeable_state"), "checks": cnt, "reviews": rv}
        if d["pull"]["merged"]: d["state"] = "merged"
    try:
        h = issue_html(n, repo)
        d["html"], d["last"] = h.get("body") or "", h.get("last")
    except Exception:
        pass   # a failed rendered read is a plain body in the sheet, never a failed issue read (#919)
    _issue_cache[(repo, n)] = (time.time(), d); return d

PRS = HUBDIR / "hub-prs.json"
try: prs_cache = json.loads(PRS.read_text())     # last good PR data survives a restart
except Exception: prs_cache = {"t": 0, "rows": [], "err": None}
def refresh_closed_prs():
    if time.time() - prs_cache.get("read", 0) < LABELS_EVERY or _under_floor("closed pull request read"): return
    f = lambda s: dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    rows = [{"n": p["number"], "merged": p["merged_at"], "min": round((f(p["merged_at"]) - f(p["created_at"])).total_seconds() / 60, 1)}
            for p in gh_list(f"repos/{REPO}/pulls", "state=closed&sort=updated&direction=desc") if p.get("merged_at") and p["number"] >= 1115]
    prs_cache["read"] = time.time()
    if rows:
        prs_cache.update(t=time.time(), rows=sorted(rows, key=lambda r: r["merged"]), err=None)
        _tmp = _temp_name(PRS); _tmp.write_text(json.dumps(prs_cache)); os.replace(_tmp, PRS)
def refresh_prs():
    """The closed-PR read every ten minutes, and only while a page reads this profile (#1790). Its sleep is not cut
    short by a page read: the read is ten minutes old at worst, and a page that opens and closes would otherwise read
    again each time."""
    while True:
        if read_wanted():
            try: refresh_closed_prs()
            except Exception as e: prs_cache["err"] = str(e)[:80]
        time.sleep(600)

def speed():
    rows = prs_cache["rows"]; cut = BASE["cutoff"]
    def agg(v): return {"n": len(v), "median": round(st.median(v), 1), "mean": round(st.mean(v), 1), "max": round(max(v), 1), "over60": sum(x > 60 for x in v)} if v else {"n": 0}
    after = [r["min"] for r in rows if r["merged"] >= cut]
    rounds = []
    for f in glob.glob(str(STATE / "auto/rounds-*")):
        try: rounds.append(len([x for x in open(f) if x.strip()]))
        except OSError: pass
    kills = []
    try: kills = [int(x.split()[0]) for x in open(HUBDIR / "hub-kills.log") if x.strip()]
    except OSError: pass
    openp = []; now_ts = time.time()   # epoch arithmetic: dt.UTC needs 3.11 and this must run on the system python3 too
    for x in board_cache.get("prs", []):
        try: openp.append({"n": x["n"], "min": round((now_ts - dt.datetime.fromisoformat(x["o"].replace("Z", "+00:00")).timestamp()) / 60)})
        except Exception: pass
    openp.sort(key=lambda r: -r["min"])
    done = [r["dur"] for r in all_runs() if r["done"] and r["dur"] > 0]
    today = [r["dur"] for r in all_runs() if r["done"] and r["dur"] > 0 and time.localtime(r["t0"])[:3] == time.localtime()[:3]]
    return {"baseline": BASE, "pr": {"open": openp[:8], "after": agg(after), "series": [{"n": r["n"], "min": r["min"], "after": r["merged"] >= cut} for r in rows[-40:]], "stale": bool(prs_cache["err"]) or time.time() - prs_cache["t"] > 1800},
            "runs": {"today_median": round(st.median(today), 1) if today else None, "all_median": round(st.median(done), 1) if done else None},
            "rounds": {"prs": len(rounds), "mean": round(st.mean(rounds), 2) if rounds else None}, "kills": {"count": len(kills), "last": max(kills) if kills else None}}

# The state table is shared with the dashboard build and the phone reads its badge fields from the hub's run payload.
# `mark` is one of the four chip-size shapes; a border texture is never a mark (#1137).
RUN_STATES = json.loads((pathlib.Path(__file__).resolve().parent / "run-states.json").read_text())   # beside the reader, so the live copy reads it too (ADR-0141)
MARK_GLYPHS = {"filled": "●", "hollow": "○", "bar": "▌", "none": ""}   # the four marks of the one state table, drawn before the word
def run_state_css(r):
    state = r["state"].replace(" ", "-")
    rule = f".b.{state}{{color:var(--{r['color']});{r['css']}}}"
    return rule + (f'.b.{state}::before{{content:"{MARK_GLYPHS[r["mark"]]} "}}' if r["mark"] != "none" else "")
# The chip the state table paints, shared by the desktop badge and the phone's run row (#1142): one base, then one rule
# per state. A waiting pass is no state of the table, and the outcome classes stand beside it (#598).
RUN_STATE_BASE_CSS = """.b{display:inline-block;padding:0 6px;border-radius:var(--radius-lg);font-size:var(--fs-caption);border:1px solid}
.b.waiting{color:var(--wait);border-style:dashed}
.landed,.pass,.ok{color:var(--ok)}"""

WEB_TOKENS = (pathlib.Path(__file__).resolve().parent / "web-tokens.css").read_text()
# The installed app's colours are the token file's values: a manifest is no stylesheet, so it reads no variable (#1142).
BG_COLOR = re.search(r"--bg:\s*([^;]+);", WEB_TOKENS).group(1).strip()
PAGE = r"""<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Hub</title>
<script>if(matchMedia("(max-width: 600px)").matches&&localStorage.getItem("squad-view")!=="desktop")location.replace(location.pathname.replace(/\/?$/,"/")+"mobile")</script>
<style>
/*WEB_TOKENS*/
:root{--col-w:150px}   /* the docked strip's column geometry: the one place its width is set (#1791); a column holding no cards is not drawn at all, so it reserves none (#1809) */
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:var(--fs-body)/1.55 var(--font-ui);display:flex;flex-direction:column;height:100vh}
#win{flex:1;min-height:0;display:flex;overflow:hidden}
#side{width:280px;padding:12px;border-right:1px solid var(--line);overflow:auto;background:var(--panel);flex:none}#content{flex:1;min-width:0;overflow:auto}.pane{display:none;padding:12px 16px}.pane.on{display:block}.pane>h2{font-size:var(--fs-caption);text-transform:uppercase;letter-spacing:.06em;color:var(--mute);margin:4px 0 8px}
#top{position:sticky;top:0;background:linear-gradient(var(--panel),var(--bg));padding:10px 10px 10px 12px;border-bottom:2px solid var(--acc);border-left:3px solid var(--acc);z-index:2;box-shadow:0 2px 8px var(--shadow-soft)}#top>b{font-size:var(--fs-caption);letter-spacing:.12em;color:var(--acc)}
#top .row{display:flex;gap:4px;align-items:center;flex-wrap:wrap;margin-top:6px}
button{background:var(--panel);color:var(--fg);border:1px solid var(--line);border-radius:var(--radius-md);padding:4px 12px;cursor:pointer;font:inherit}button:hover{border-color:var(--line-strong)}
button.on{border-color:var(--acc);color:var(--acc)}
.kd{margin-right:5px;font-size:var(--fs-caption)}.r{padding:7px 10px;border-top:1px solid var(--line);cursor:pointer;display:flex;gap:6px;justify-content:space-between}.r:hover{background:var(--card)}
.r .st{font-size:var(--fs-caption);color:var(--ink-body);opacity:.8;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:240px}
/* The share a gate counted (#80): how much of the suite passed, so a red gate still shows the work it got to. The full
   share reads as good, a partial one as in progress; a run no gate counted carries no chip at all, never a 0%. */
.r .sh{color:var(--warn);font-variant-numeric:tabular-nums}.r .sh.done{color:var(--ok)}
.r .dot{width:7px;height:7px;border-radius:var(--radius-full);background:var(--run);flex:none;display:inline-block}
.r .slot{margin-left:0;flex:none;font-size:var(--fs-caption);color:var(--mute);white-space:nowrap;font-variant-numeric:tabular-nums}
.r .slot.quiet,.r .slot.wait{color:var(--warn)}
.r .slot.need{color:var(--err)}
.r .dot.quiet,.r .dot.wait{background:var(--warn)}
.r .dot.need{background:var(--err)}
.r .stg{color:var(--mute);font-size:var(--fs-caption);white-space:nowrap;flex:1 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis}
.r.show{background:var(--raise);box-shadow:inset 3px 0 0 var(--acc)}.r.pinned .pn{color:var(--acc)}
.pn{color:var(--mute);flex:none}
/*RUN_STATE_BASE_CSS*/
/*RUN_STATE_CSS*/
#bread{color:var(--mute)}#bread.stale{color:var(--warn)}#bbody.stale{opacity:.45;filter:grayscale(1)}
.m{color:var(--mute);font-size:var(--fs-caption);margin-top:2px}
#q{width:100%;background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:var(--radius-sm);padding:3px 6px;font:inherit;margin-top:6px}
.sp:is(details){border:1px solid var(--line);border-radius:var(--radius-lg);margin-bottom:10px;background:var(--panel);font-size:var(--fs-caption)}.sp>summary{list-style:none;display:flex;align-items:center;gap:6px;min-height:36px;padding:8px 10px;font-size:var(--fs-body);font-weight:600;color:var(--fg);cursor:pointer;user-select:none}
.sp>summary::-webkit-details-marker{display:none}.sp>summary::before{content:"▸";flex:none;width:10px;color:var(--mute);font-weight:400}.sp[open]>summary::before{content:"▾"}.sp>summary:hover{background:var(--bubble)}.sp>summary:focus-visible{box-shadow:inset 0 0 0 1px var(--acc)}
.sp>summary .sr{margin-left:auto;font-weight:400;color:var(--mute);font-size:var(--fs-caption);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}#lrd>summary{white-space:nowrap}#lrd>summary .sr{max-width:62%}.sp .sb{padding:4px 10px 10px}
.sp h4{display:flex;align-items:baseline;justify-content:space-between;gap:4px;margin:10px 0 6px;font-size:var(--fs-caption);color:var(--fg);font-weight:600;white-space:nowrap}.sp h4 small{font-weight:400;color:var(--mute);font-size:var(--fs-caption);white-space:nowrap}
.tot{display:grid;grid-template-columns:repeat(3,1fr);gap:4px}.tot div{background:var(--inset);border:1px solid var(--line);border-radius:var(--radius-md);padding:4px 6px;font-size:var(--fs-caption);color:var(--mute)}.tot b{display:block;font-size:var(--fs-body);color:var(--fg);font-weight:600}.tot b.m{color:var(--mute);font-size:var(--fs-caption);line-height:1.5}
.tot .cash,.cash{grid-column:1/-1;background:none;border:0;padding:0;margin:2px 0 0;font-size:var(--fs-caption);color:var(--mute);line-height:1.35}
#src,#per{display:inline-flex;flex-wrap:nowrap;gap:2px;margin:0}#src button{padding:1px 5px;font-size:var(--fs-caption)}#per button{padding:1px 3px;font-size:var(--fs-caption)}
.kr{display:grid;grid-template-columns:minmax(0,1fr) auto;grid-auto-rows:min-content;column-gap:8px;row-gap:2px;align-items:baseline;padding:5px 0;border-bottom:1px solid var(--line);font-size:var(--fs-field);min-width:0}.kr:last-child{border:0}.kr .l{grid-row:1/3;min-width:0;font-size:var(--fs-caption);line-height:1.3;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.kr .l small{display:inline;color:var(--mute);font-size:var(--fs-caption)}.kr b{grid-column:2;justify-self:end;font-size:var(--fs-body);font-weight:600;line-height:1}.kr b small{font-size:var(--fs-caption);font-weight:400;color:var(--mute)}.kr b.m{color:var(--mute)}.kr b.w{color:var(--warn)}
.kr .d{grid-column:2;justify-self:end;font-size:var(--fs-caption);padding:0;white-space:nowrap;color:var(--mute)}.kr .d .good{color:var(--ok)}.kr .d .bad{color:var(--err)}
#kpi h4{flex-wrap:wrap;white-space:normal}
#kpi h4 .warnl{margin:0 0 0 auto;text-align:right;white-space:normal}
#kpi .kr .l{grid-row:1;white-space:normal;overflow:visible;text-overflow:clip}
#kpi .kr .d{grid-column:1/-1;grid-row:2;justify-self:start}
.mtg{min-height:32px;min-width:32px;padding:2px 10px;border:1px solid var(--line);border-radius:var(--radius-md);background:var(--inset);color:var(--fg);font-size:var(--fs-caption);cursor:pointer}.mtg.off{text-decoration:line-through;color:var(--mute)}.mt-row{display:flex;flex-wrap:wrap;gap:6px;align-items:center;padding:4px 0}.mt-row .mn{flex:1 1 140px;min-width:0}.mdiff{border-top:1px dashed var(--line);margin-top:8px;padding-top:6px}.mtg:focus-visible{outline:2px solid var(--acc);outline-offset:2px}@media (max-width:600px){.mtg{min-height:44px;min-width:44px}}
#mbody{padding:0}.mc{background:var(--inset);border:1px solid var(--line);border-radius:var(--radius-lg);padding:10px;margin:6px 0 8px}.mc .mt{font-size:var(--fs-caption);font-weight:600;color:var(--fg);margin:0 0 2px}.mc .mx{font-size:var(--fs-caption);color:var(--mute);margin:0 0 8px;line-height:1.35}
.mr{display:grid;grid-template-columns:22px minmax(0,1fr);align-items:center;gap:5px 8px;padding:7px 8px;border:1px solid var(--line);border-radius:var(--radius-md);background:var(--panel);margin-bottom:5px;font-size:var(--fs-caption)}.mr.p{border-color:var(--tint-ok-line);background:var(--tint-ok)}.mr .rk{flex:none;width:22px;height:22px;border-radius:var(--radius-lg);background:var(--card);color:var(--mute);font-size:var(--fs-caption);font-weight:600;display:flex;align-items:center;justify-content:center}.mr.p .rk{background:var(--ok);color:var(--on-tone)}
.mr .mn{min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;line-height:1.3;font-size:var(--fs-caption);color:var(--fg)}.mr .ct{grid-column:1/3;display:flex;flex-wrap:wrap;gap:3px;align-items:center;min-width:0}.mr .mi{font-size:var(--fs-caption);color:var(--mute);margin-left:4px}.mr .ur{color:var(--warn)}.mr .pv{flex:1 1 auto;min-width:0;font-size:var(--fs-caption);color:var(--mute);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.mr .pv b{color:var(--ok);font-weight:600}.mr .el{flex:none;font-size:var(--fs-caption);color:var(--mute);margin-left:2px}.mr .mn small{display:block;font-size:var(--fs-caption);color:var(--mute)}.mr .ef{flex:none;width:84px;background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:var(--radius-sm);font:inherit;font-size:var(--fs-field);height:var(--ctl-h);padding:0 2px;cursor:pointer;text-overflow:ellipsis}.mr .ef.set{color:var(--warn);border-color:var(--tint-warn-line)}.mr .hr{flex:1 1 22ch;width:22ch;min-width:9ch;background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:var(--radius-sm);font:inherit;font-size:var(--fs-field);height:var(--ctl-h);padding:0 6px;text-align:left;text-overflow:ellipsis}.mr label.el:has(.hr){flex:0 1 auto;display:flex;align-items:center;gap:4px;min-width:0}.mr .hr::placeholder{color:var(--mute)}.mr .hr:focus{outline:var(--focus-w) solid var(--focus);border-color:var(--acc)}.mr .hr.set{color:var(--warn);border-color:var(--tint-warn-line)}
.mr .mb{flex:none;display:flex;gap:2px;margin-left:4px}.mr .mb button{min-width:24px;height:var(--ctl-h);padding:0;font-size:var(--fs-caption);line-height:1}.mr .mb button:hover:not(:disabled){background:var(--hover);border-color:var(--line-strong)}.mr .mb button:disabled{opacity:.25;cursor:default}.mr .mb button.x:hover:not(:disabled){color:var(--err);border-color:var(--err)}
/* The switch (#1819): a state, so it reads as a word, not a glyph. Off de-emphasises the row and marks the rank badge
   -- the order itself is untouched, which is the point of the switch. */
.mr .mb button.sw{min-width:30px;font-size:var(--fs-caption);letter-spacing:.02em}.mr .mb button.sw.off{color:var(--warn);border-color:var(--tint-warn-line);background:var(--tint-warn)}
.mr .bk{grid-column:2;justify-self:start;font-size:var(--fs-caption);color:var(--warn);border:1px solid var(--warn);border-radius:var(--radius-sm);padding:0 5px}.mr.off .mn,.mr.off .pv,.mr.off .el{opacity:.55}.mr.off .rk{background:var(--tint-warn);color:var(--warn)}.mr.off .ef,.mr.off .hr{opacity:.75}
.madd{display:flex;gap:5px;margin-top:4px}.madd input{flex:1;min-width:0;height:var(--ctl-h);background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:var(--radius-sm);padding:0 8px;font:inherit;font-size:var(--fs-field)}.madd input:focus{outline:var(--focus-w) solid var(--focus);border-color:var(--acc)}.madd button{height:var(--ctl-h);padding:0 12px}
.msave{position:sticky;bottom:0;display:flex;gap:6px;align-items:center;flex-wrap:wrap;padding:8px 0 0;margin:12px 0 0;background:var(--panel);border-top:1px solid var(--line);font-size:var(--fs-caption);z-index:1}.msave.dirty{background:var(--tint-warn);border-top-color:var(--tint-warn-line)}.msave button{height:var(--ctl-h);padding:0 12px}.msave button#msv:not(:disabled){background:var(--warn);color:var(--on-tone);border-color:var(--warn);font-weight:600}.msave button:disabled{opacity:.35;cursor:default}
.msave .st{flex:1 1 100%;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--mute)}.msave .st.err{color:var(--err)}.msave .st .dot{display:inline-block;width:8px;height:8px;border-radius:var(--radius-sm);background:var(--warn);margin-right:5px;vertical-align:middle}.msave .st.okm{color:var(--ok)}.sub{display:flex;flex-direction:column;gap:6px;margin:8px 0;padding:8px 10px;background:var(--inset);border:1px solid var(--line);border-radius:var(--radius-lg);font-size:var(--fs-caption)}
.sub .hd{display:flex;align-items:center;gap:6px;margin-bottom:2px}.sub .nm{font-weight:600;font-size:var(--fs-caption);flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.sub .sv{font-weight:600;font-size:var(--fs-body)}.sub .sv.good{color:var(--ok)}.sub .sv.bad{color:var(--err)}.sub .sv.none{color:var(--mute);font-size:var(--fs-caption);font-weight:400}
.sub .qw{display:flex;flex-direction:column;gap:3px}.sub .ql{display:flex;justify-content:space-between;align-items:baseline;gap:8px;font-size:var(--fs-caption);color:var(--mute)}.sub .ql b{color:var(--fg);font-weight:600;font-size:var(--fs-caption)}.sub .ql .rst{white-space:nowrap;font-size:var(--fs-caption)}
.sub .qw.sm .ql{font-size:var(--fs-caption)}.sub .qw.sm .ql b{font-size:var(--fs-caption);color:var(--ink-soft)}.sub .qw .tr{position:relative;height:8px;border-radius:var(--radius-sm);background:var(--card)}.sub .qw.sm .tr{height:4px;border-radius:var(--radius-sm)}.sub .qw .fl{height:100%;border-radius:var(--radius-sm);transition:width var(--motion-duration-long) var(--motion-entry-easing)}
.sub .fl.lv-ok{background:var(--mute)}.sub .fl.lv-warn{background:var(--warn)}.sub .fl.lv-err{background:var(--err)}.sub .fl.lv-est{background:var(--acc)}.sub .qw .tk{position:absolute;top:-3px;bottom:-3px;width:2px;background:var(--fg);border-radius:var(--radius-sm)}
.sub .fr{display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-top:-1px}.sub .fs{color:var(--mute);font-size:var(--fs-caption);margin-left:auto}.sub .chip{display:inline-block;padding:0 7px;border-radius:var(--radius-lg);font-size:var(--fs-caption);font-weight:600;line-height:16px}.sub .chip.err{background:var(--tint-err);color:var(--err);border:1px solid var(--tint-err-line)}
.sub .xx{padding:1px 7px;border-radius:var(--radius-lg);background:var(--tint-ok);color:var(--ok);font-size:var(--fs-caption);margin-left:5px}.sub .xx.bad{background:var(--tint-err);color:var(--err)}
.sub .pr{display:flex;gap:6px;align-items:center;color:var(--mute);font-size:var(--fs-caption)}.sub select{flex:1;min-width:0;max-width:100%}.sub input{padding:3px 6px;font-size:var(--fs-field);min-width:0}.sub input[data-f=amount]{flex:1}.sub input[data-f=cur]{width:56px;text-transform:uppercase}
.sub .dt{color:var(--mute);font-size:var(--fs-caption);line-height:1.4}.sub .dt b{color:var(--fg);font-weight:600}.sub .warn{color:var(--warn);font-size:var(--fs-caption)}
.mb{margin-top:2px}.mb .lab{display:flex;justify-content:space-between;font-size:var(--fs-caption);color:var(--mute);margin-bottom:3px}.mb .lab b{color:var(--fg);font-weight:600}.mb .tr{position:relative;height:9px;border-radius:var(--radius-sm);background:var(--card);overflow:visible}.mb .fl{height:100%;border-radius:var(--radius-sm);background:var(--warn)}.mb .fl.good{background:var(--ok)}.mb .fl.bad{background:var(--err)}.mb .fl.warnq{background:var(--acc)}.mb .tk{position:absolute;top:-3px;bottom:-3px;width:2px;background:var(--fg);border-radius:var(--radius-sm)}.mb .sc2{display:flex;justify-content:space-between;font-size:var(--fs-caption);color:var(--mute);margin-top:2px}
.subx{color:var(--mute);font-size:var(--fs-caption);line-height:1.45;margin:8px 0 4px}
.pie{display:flex;flex-direction:column;gap:6px;align-items:stretch}.pie svg{flex:none;width:112px;height:112px;margin:0 auto}.pie .lg{flex:1;min-width:0;font-size:var(--fs-caption)}.pie .lg div{display:flex;align-items:center;gap:6px;padding:2px 0}.pie .lg i{width:9px;height:9px;border-radius:var(--radius-sm);flex:none}.pie .ln{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.pie .lg b{font-weight:600}.pie .lg .usd{color:var(--mute);font-variant-numeric:tabular-nums;white-space:nowrap}
.pie .lg .sh{color:var(--mute);font-variant-numeric:tabular-nums;white-space:nowrap;text-align:right;min-width:3ch}.pie .lg div:focus-visible{outline:var(--focus-w) solid var(--focus);outline-offset:1px}.pie .lg div.hl{background:var(--card)}.pie .lg .lgx{display:block;padding:0 0 0 15px}.pie .lg summary{display:flex;align-items:center;gap:6px;padding:2px 0;cursor:pointer;list-style:none}.pie .lg summary::-webkit-details-marker{display:none}.sp #piet{font-size:var(--fs-caption);margin:-4px 0 6px}.pie [data-k].hl{stroke:var(--fg);stroke-width:2}.pie.dim [data-k]:not(.hl){opacity:.35}.warnl{margin-top:4px;font-size:var(--fs-caption);color:var(--warn)}
.up{color:var(--err)}.dn{color:var(--ok)}svg{display:block;width:100%}
#main{flex:1;min-width:0;display:grid;gap:4px;padding:4px}
.cell{display:flex;flex-direction:column;min-height:0;min-width:0}.cell small{color:var(--mute);padding:2px 4px;display:flex;justify-content:space-between}.cell .snap{flex:1;min-height:0;display:flex;align-items:center;justify-content:center;text-align:center;padding:12px;overflow:hidden;color:var(--mute);border:1px solid var(--line);background:var(--bg)}.cell[tabindex]{cursor:pointer}.cell[tabindex]:hover .snap,.cell[tabindex]:focus-visible .snap{border-color:var(--acc);color:var(--fg,inherit)}
#board{position:relative;width:auto}
#bhead{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:12px}#bhead>label{display:inline-flex;align-items:center;gap:8px}#bhead label.hck{border:1px solid var(--line);border-radius:var(--radius-md);padding:0 12px 0 10px;min-height:32px;background:var(--panel);cursor:pointer}#bhead label.hck input{margin:0;accent-color:var(--acc)}#bt:empty{display:none}
#flowov{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius-lg);display:flex;flex-direction:column}#flowov[hidden]{display:none}
/*PROFILER_CSS*/
#flowtabs{display:inline-flex;border:1px solid var(--line-strong);border-radius:var(--radius-lg);overflow:hidden;margin:0 0 12px}#flowtabs button{border:0;border-radius:0;min-height:32px}#flowtabs button.on{background:var(--acc);color:var(--bg)}
.fchip{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line-strong);border-radius:var(--radius-lg);padding:0 8px;font-size:var(--fs-body);color:var(--ink-soft)}.fchip button{border:0;background:none;color:var(--acc);font:inherit;cursor:pointer;padding:0}
#flowb{padding:10px;font-size:var(--fs-body)}#flowb svg{width:100%;height:auto;display:block;margin:6px 0;user-select:none;touch-action:pan-y}#flowb .fnote,#flowb .fhead{color:var(--mute);margin:4px 0}#flowb .fhead b{color:var(--fg,inherit)}#flowb .flstat{color:var(--mute);margin:6px 0 0}#flowb .ltgl{color:var(--mute);font-weight:600;margin-right:4px}
#flowb .flg{margin-right:10px;white-space:nowrap}.ltlg{display:flex;flex-wrap:wrap;gap:4px 18px;white-space:normal}.ltlg span{display:inline-flex;align-items:baseline;gap:6px;white-space:nowrap}.ltlg b{font-weight:600}.ltlg em{font-style:normal;color:var(--mute)}.ltlg i{display:inline-block;width:18px;height:6px;border-radius:var(--radius-sm)}.ltlg i.lthollow{width:10px;height:10px;border-radius:var(--radius-full);background:var(--chart-bg);border:1px solid var(--chart-ink)}.ltlg i.ltfilled{width:10px;height:10px;border-radius:var(--radius-full);background:var(--chart-ink)}.ltlg i.ltroll{height:3px;background:var(--chart-bar)}.ltlg i.ltband{background:var(--chart-series-3);opacity:.28}.ltlg i.ltsolid{height:2px;border-radius:0;background:var(--chart-series-8)}.ltlg i.ltdash{height:2px;border-radius:0;background:repeating-linear-gradient(90deg,var(--chart-ok) 0 6px,transparent 6px 9px)}.ltlg i.ltdot{height:2px;border-radius:0;background:repeating-linear-gradient(90deg,var(--chart-series-5) 0 2px,transparent 2px 6px)}#flowb .flg i{display:inline-block;width:9px;height:9px;margin-right:4px;vertical-align:middle;border-radius:var(--radius-sm)}#flowb .ltmore>summary{cursor:pointer;color:var(--mute);margin:2px 0 6px}#flowb .ltrow{display:flex;flex-wrap:wrap;align-items:center;gap:4px 10px}#flowb .ltrow .ltmore>summary{margin:0}.ltplot{display:flex;flex-direction:column;min-height:0}.ltplot>.ltlg{flex:none;margin:0 0 2px;justify-content:flex-end;max-width:100%}/* the series legend heads the plot in a band of its own, never over a mark (#2171) */
#flowb .ltcard{display:flex;flex-direction:column;min-height:0;overflow:auto}#flowb .ltcard .ltplot{flex:1 1 auto;min-height:0;display:flex;flex-direction:column}#flowb .ltcard .ltplot>svg{flex:1 1 0;width:100%;margin:0;min-height:0}/* the plot box is the card's remainder, and the plot fills what the legend's band leaves (#2171) */
#bbody .ah{display:flex;justify-content:space-between;gap:10px;align-items:center;padding:8px 10px;border-bottom:1px solid var(--line);cursor:pointer;font-weight:600;position:sticky;top:34px;background:var(--card);z-index:1}   /* the same gap as the docked header: `Todo0` is never a reading, in either rendering (#1809) */
#bbody .ah .n{background:var(--line);border-radius:var(--radius-lg);padding:0 8px;font-weight:400}
#bbody .ah i{display:inline-block;width:8px;height:8px;border-radius:var(--radius-full);margin-right:7px;background:var(--mute)}
#bbody .it{margin:6px 0;padding:var(--card-gap-y) var(--card-gap-x);border:1px solid var(--line-strong);border-top:3px solid var(--lc,var(--mute));border-radius:var(--radius-lg);background:var(--card);line-height:1.4;font-size:var(--fs-caption);white-space:normal;overflow-wrap:anywhere;display:flex;flex-wrap:wrap;align-items:center;gap:4px 6px;transition:border-color var(--motion-duration-short) var(--motion-entry-easing)}
#bbody .it .ft{display:flex;justify-content:flex-end;gap:6px;margin-top:6px}#bbody .age,#bbody .tsk{display:inline-block;padding:0 6px;border-radius:var(--radius-lg);font-size:var(--fs-caption);font-variant-numeric:tabular-nums;color:var(--mute);border:1px solid var(--line)}#bbody .age.warm{color:var(--on-tint);background:color-mix(in srgb,var(--warn) var(--tint-alpha),var(--card));border-color:var(--tint-warn-line)}#bbody .age.old{color:var(--on-tint);background:color-mix(in srgb,var(--err) var(--tint-alpha),var(--card));border-color:var(--tint-err-line)}
#bbody .pbg{display:inline-block;margin-left:6px;padding:0 7px;border-radius:var(--radius-lg);font-size:var(--fs-caption);font-weight:600;border:1px solid}#bbody .pbg.conflict,#bbody .pbg.checks-red{color:var(--on-tint);background:color-mix(in srgb,var(--err) var(--tint-alpha),var(--card));border-color:var(--tint-err-line)}#bbody .pbg.green{color:var(--on-tint);background:color-mix(in srgb,var(--ok) var(--tint-alpha),var(--card));border-color:var(--tint-ok-line)}#bbody .pbg.approved{color:var(--ok);border-color:var(--ok);background:var(--tint-ok)}   /* the lane badge (#247) */
#bbody .fz{display:inline-block;margin-left:6px;padding:0 6px;border-radius:var(--radius-lg);font-size:var(--fs-caption);color:var(--warn);border:1px dashed var(--warn)}   /* the freeze marker: the card keeps its column */
#bbody .it .pnd{display:inline-block;margin-left:6px;padding:0 6px;border-radius:var(--radius-lg);font-size:var(--fs-caption);font-weight:600;letter-spacing:.05em;text-transform:uppercase;color:var(--on-tone);background:var(--warn);border:1px solid var(--warn)}   /* the proved move the board read has not carried yet (#650) */
   /* the two modes that leave the specs out of the way, or ask for them: the one that draws every card is the resting colour (#456) */
#bbody .pst{display:inline-block;margin-left:6px;padding:0 7px;border-radius:var(--radius-lg);font-size:var(--fs-caption);font-weight:600;vertical-align:1px;border:1px solid;max-width:100%;overflow-wrap:anywhere}
#bbody .pst.ready{color:var(--ok);border-color:var(--tint-ok-line);background:var(--tint-ok)}#bbody .pst.running{color:var(--acc);border-color:var(--tint-acc-line);background:var(--tint-acc)}
#bbody .pst.changes{color:var(--warn);border-color:var(--tint-warn-line);background:var(--tint-warn)}#bbody .pst.broke,#bbody .pst.conflict{color:var(--err);border-color:var(--tint-err-line);background:var(--tint-err)}
#bbody .pst.idle,#bbody .pst.draft{color:var(--mute);border-color:var(--line);background:var(--inset)}
#bbody .gstrip{display:flex;gap:4px;flex-wrap:wrap;margin-top:3px;font-size:var(--fs-caption);color:var(--mute)}
#bbody .gstrip.old{opacity:.45;filter:grayscale(1)}
#bbody .gc{border:1px solid var(--line);border-radius:var(--radius-md);padding:0 5px;white-space:nowrap;background:var(--inset)}
#bbody .gc b{color:var(--fg);font-weight:600;margin-left:3px}
#bbody .gc.ok{background:color-mix(in srgb,var(--ok) var(--tint-alpha),var(--card))}#bbody .gc.warn{background:color-mix(in srgb,var(--warn) var(--tint-alpha),var(--card))}#bbody .gc.bad{background:color-mix(in srgb,var(--err) var(--tint-alpha),var(--card))}#bbody .gc.ok b,#bbody .gc.warn b,#bbody .gc.bad b{color:var(--on-tint)}
#bbody .gc.debt{background:color-mix(in srgb,var(--acc) var(--tint-alpha),var(--card))}#bbody .gc.debt b{color:var(--on-tint)}#bbody .gc.unk{border-color:var(--tint-warn-line)}#bbody .gc.unk b{color:var(--warn)}
#bbody .it:hover{border-color:var(--edge);border-top-color:var(--lc,var(--mute))}
#bbody .it .id.ghl{opacity:1;font-weight:600}#bbody .it .id svg{margin-left:6px;vertical-align:-1px}.it .id.ghl:hover{opacity:1;color:var(--acc)}.it .id{order:0;margin:0;background:color-mix(in srgb,var(--lc,var(--acc)) 12%,transparent);border:1px solid color-mix(in srgb,var(--lc,var(--acc)) 38%,transparent);border-radius:var(--radius-lg);padding:1px 7px;color:var(--lc,var(--acc));font:600 var(--fs-body) var(--font-mono);font-variant-numeric:tabular-nums;white-space:nowrap;text-decoration:none}

#bbody .it .lbs{display:block;margin-top:4px}#bbody .it .lbs span{display:inline-block;background:var(--lb,var(--line));border:1px solid transparent;border-radius:var(--radius-lg);padding:0 6px;margin:0 3px 2px 0;font-size:var(--fs-caption);color:var(--ink-body)}#bbody .it .lbs span.on{border-color:var(--warn);color:var(--fg)}
#bbody .it .id:hover{color:var(--acc)}#bbody .it .tt{order:2;flex:1 1 100%;font-size:var(--fs-body);color:var(--fg)}#bbody .it>*{order:3}#bbody .it>.id{order:-2}#bbody .it>.fz{order:-1}#bbody .it>.lchip[data-k=spec]{order:1;margin-left:auto}#bbody .it>.tt{order:2}#bbody .it>.lchip,#bbody .it>.bugc,#bbody .it>.rpo,#bbody .it>.pbg,#bbody .it>.cir,#bbody .it>.fz,#bbody .it>.pnd,#bbody .it>.pst{order:0;margin:0}#bbody .it>.dt{float:none}#bbody .it>.gstrip,#bbody .it>.blk,#bbody .it>.clos,#bbody .it>.prr,#bbody .it>.perr,#bbody .it>.ft{flex:1 1 100%;margin-top:2px}#bbody .it .hld{flex:1 1 100%;font-size:var(--fs-caption);color:var(--mute);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}#bbody .it .hld::before{content:"\25CF ";color:var(--s5)}#bbody .cell .it .lbs{display:none}
#bbody .prio{font-weight:600;margin-right:4px}#bbody .prio.red{color:var(--err)}#bbody .prio.yellow{color:var(--warn)}#bbody .prio.blue{color:var(--acc)}#bbody .pinh{font-size:var(--fs-caption);color:var(--mute);margin-right:4px}
#bbody .lchip{border-radius:var(--radius-lg);padding:0 8px;font-size:var(--fs-caption);font-weight:600;border:1px solid var(--line-strong);line-height:20px;white-space:nowrap;color:var(--mute)}#bbody .lchip[data-k=spec]{color:var(--mute);border-color:var(--line);font-weight:400}#bbody .it>.fz{order:-1}
#bbody .it .pmp{display:inline-block;margin-top:4px;font:inherit;font-size:var(--fs-caption);padding:0 7px;border-radius:var(--radius-lg);cursor:pointer;color:var(--acc);background:var(--inset);border:1px solid var(--line)}#bbody .it .pmp:hover{border-color:var(--acc)}#bbody .it .pmp:disabled{opacity:.5;cursor:default}#bbody .bmv{display:inline-block;margin-left:8px;min-height:32px;padding:0 10px;font:inherit;font-size:var(--fs-caption);border-radius:var(--radius-lg);cursor:pointer;color:var(--acc);background:var(--inset);border:1px solid var(--line)}#bbody .bmv:disabled{opacity:.5;cursor:default}#bbody .it .spb{display:inline-block;margin-top:4px;margin-left:6px;font:inherit;font-size:var(--fs-caption);padding:0 7px;border-radius:var(--radius-lg);cursor:pointer;color:var(--acc);background:var(--inset);border:1px solid var(--line)}#bbody .it .spb:hover{border-color:var(--acc)}#bbody .it .syncb{display:inline-block;margin-top:4px;margin-left:6px;font:inherit;font-size:var(--fs-caption);padding:0 7px;border-radius:var(--radius-lg);cursor:pointer;color:var(--mute);background:var(--inset);border:1px solid var(--line)}#bbody .it .syncb:hover{border-color:var(--acc);color:var(--acc)}#bbody .it .syncb:disabled{opacity:.5;cursor:default}#bbody .syncage{display:inline-block;margin-top:4px;padding:0 6px;border-radius:var(--radius-lg);font-size:var(--fs-caption);font-variant-numeric:tabular-nums;color:var(--acc);border:1px solid var(--tint-acc-line);background:var(--tint-acc)}#spdlg{margin:6px 0;padding:8px 10px;border-radius:var(--radius-md);border:1px solid var(--acc);background:var(--inset);font-size:var(--fs-body)}#spdlg button{margin:6px 6px 0 0;min-height:32px;padding:0 10px;font:inherit;font-size:var(--fs-caption);border-radius:var(--radius-lg);cursor:pointer;color:var(--acc);background:var(--inset);border:1px solid var(--line)}#spnote{margin:4px 0;font-size:var(--fs-caption);color:var(--ok)}   /* the owner's move, on a Backlog card only (#560) */
#bbody .it .prr{display:block;margin-top:2px;font-size:var(--fs-caption);color:var(--mute);max-width:100%;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}#bbody .it .prr.pwr{color:var(--err)}#bbody .it{position:relative}#bbody .prio.pbtn{cursor:pointer;border-radius:4px;padding:0 2px}#bbody .prio.pbtn:hover,#bbody .prio.pbtn:focus-visible{outline:1px solid var(--acc)}#bbody .prd{position:absolute;z-index:40;top:1.6em;left:6px;margin:2px 0 0;padding:4px;list-style:none;background:var(--inset);border:1px solid var(--line);border-radius:var(--radius-md);box-shadow:0 4px 14px var(--shadow-soft);min-width:132px}#bbody .prd [role=option]{padding:5px 8px;border-radius:4px;cursor:pointer;font-size:var(--fs-caption)}#bbody .prd [role=option][aria-selected=true]{background:var(--tint-acc);font-weight:600}#bbody .prd [role=option]:focus{outline:1px solid var(--acc)}#bbody .prp{display:inline-block;margin-left:6px;padding:0 6px;border-radius:var(--radius-lg);font-size:var(--fs-caption);font-weight:600;letter-spacing:.05em;text-transform:uppercase;color:var(--on-tone);background:var(--warn)}   /* a Priority write not proved yet (#2289) */
#bbody .it .blk{display:block;margin-top:4px;font-size:var(--fs-caption);color:var(--mute);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}#bbody .it .blk a{color:var(--acc);text-decoration:none}
#bbody .prs .it .cir{display:inline-block;margin-left:6px;font-size:var(--fs-caption);letter-spacing:.04em;text-transform:uppercase;color:var(--cc);border:1px solid color-mix(in srgb,var(--cc) 45%,transparent);border-radius:var(--radius-xl);padding:0 5px;white-space:nowrap}   /* the pull request's own CI rollup (#440) */#bbody .prs .it .cir.pass{color:var(--on-tint);background:color-mix(in srgb,var(--ok) var(--tint-alpha),var(--card));border-color:var(--tint-ok-line)}#bbody .prs .it .cir.fail{color:var(--on-tint);background:color-mix(in srgb,var(--err) var(--tint-alpha),var(--card));border-color:var(--tint-err-line)}#bbody .prs .it .cir.pending{color:var(--on-tint);background:color-mix(in srgb,var(--run) var(--tint-alpha),var(--card));border-color:var(--tint-run-line)}
#bbody .prs .it .au{color:var(--mute);font-size:var(--fs-caption)}   /* the author the pull request was opened by (#440) */
#bbody .prs .it .clos{display:block;margin-top:2px;font-size:var(--fs-caption);color:var(--mute)}#bbody .prs .it .clos a{color:var(--acc);text-decoration:none}   /* the issues the pull request closes (#440) */
#bbody .it.m{border-style:dashed;text-align:center;background:none;box-shadow:none}
#bbody .pad{height:6px}#bbody .sc{max-height:58vh;overflow:auto}#bbody .it .dt{float:right;color:var(--mute);font-size:var(--fs-caption)}#bbody .it .hld{color:var(--mute);font-size:var(--fs-caption)}
/* The ticket sheet (#919): a side panel on the desktop that pushes the page instead of covering it, so the board keeps
   the card in view. One panel, one read: the card data rides the card, the body and the last comment come from the route. */
#tk{position:fixed;top:0;right:0;bottom:0;width:420px;background:var(--panel);border-left:1px solid var(--line);box-shadow:-12px 0 40px var(--scrim);display:none;flex-direction:column;z-index:50}
#tk.on{display:flex}
body.tkopen #win{margin-right:420px}
#tk>header{display:flex;gap:8px;align-items:center;padding:10px 12px;border-bottom:1px solid var(--line);flex-wrap:wrap}
#tk>header .sp{flex:1}
#tk>footer{display:flex;gap:8px;padding:10px 12px;border-top:1px solid var(--line);flex-wrap:wrap}
#tk>footer button,#tk>footer a{min-height:32px}
#tk a.btn{display:inline-flex;align-items:center;padding:3px 12px;border:1px solid var(--line);border-radius:var(--radius-md);color:var(--fg);text-decoration:none}
#tk .chip{border-radius:var(--radius-lg);padding:0 7px;font-size:var(--fs-caption);font-weight:600;border:1px solid var(--line);color:var(--mute)}
#tk .chip.spec{border-color:var(--acc);color:var(--acc)}
#tkset{display:flex;gap:6px;flex-wrap:wrap;padding:8px 12px 0}#tkset:empty{display:none}#tkset button.on{border-color:var(--acc);color:var(--acc)}
#tknav{display:flex;gap:6px;align-items:center}#tknav button{min-width:36px}
#tk .row{display:flex;gap:8px;font-size:var(--fs-caption);color:var(--mute);margin:6px 0}#tk .row b{min-width:84px;font-weight:600}#tk .row span{color:var(--fg)}
#tk .lab{display:inline-block;border:1px solid var(--line);border-radius:var(--radius-lg);padding:0 7px;margin:0 4px 4px 0}
#tk .sec{font-size:var(--fs-caption);text-transform:uppercase;letter-spacing:.06em;color:var(--mute);margin:14px 0 4px}
#tk .tkbody{background:var(--bg);border-radius:var(--radius-md);padding:10px 12px;margin-top:6px}
#tkb{flex:1;min-height:0;overflow:auto;padding:12px 16px}
#tkb .tkbody input[type=checkbox]{transform:scale(1.05);transform-origin:left center;vertical-align:-2px;margin:0 6px 0 0;accent-color:var(--ok);pointer-events:none}
#tkb h2{margin:0 0 6px;font-size:var(--fs-body);line-height:1.35}#tkb .meta{color:var(--mute);font-size:var(--fs-caption);margin-bottom:8px}
#tkb .lb{display:inline-block;background:var(--line);border-radius:var(--radius-lg);padding:0 8px;margin:0 4px 4px 0;font-size:var(--fs-caption)}
#tkb pre{white-space:pre-wrap;overflow-wrap:anywhere;font:var(--fs-caption)/1.45 var(--font-mono);background:var(--inset);border:1px solid var(--line);border-radius:var(--radius-md);padding:8px 10px;margin:6px 0}
#tkb h3{font-size:var(--fs-caption);color:var(--mute);margin:14px 0 4px}#tkb .cm{border-left:3px solid var(--acc);padding-left:8px;margin:8px 0}
#tkb .bar button{min-width:28px}#tkb .bar button:disabled{opacity:.35;cursor:default}#tkb .bar{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}#tkb a.gh{color:var(--acc);font-weight:600}
#bbody .it{cursor:pointer}#bbody .bu{font:inherit;font-size:var(--fs-caption);margin-left:6px;padding:0 6px;border:1px solid currentColor;border-radius:var(--radius-sm);background:none;color:inherit;cursor:pointer}#bbody .it.hit{box-shadow:0 0 0 2px var(--warn)}#bbody .it.hl{box-shadow:0 0 0 2px var(--acc)}#bbody .it.dim{opacity:.35}#main .r.hlr{outline:2px solid var(--acc);outline-offset:-2px}.hlrel{color:var(--acc);font-size:var(--fs-caption);margin-left:6px}.hlrel[hidden]{display:none}.ghl{color:var(--mute);margin-left:6px;display:inline-flex;flex:none;align-items:center;opacity:.65;text-decoration:none}.ghl svg{width:13px;height:13px}.ghl:hover,.ghl:focus-visible{opacity:1;color:var(--fg)}   /* the cards the sheet's Highlight related marks (#919) */
#tkb h2{font:600 var(--fs-heading)/1.35 -apple-system,system-ui,sans-serif;margin:4px 0 8px}
#tkb .md{font:var(--fs-body)/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;color:var(--fg);overflow-wrap:anywhere}
#tkb .md>:first-child{margin-top:0}#tkb .md p{margin:.6em 0}#tkb .md h2,#tkb .md h3,#tkb .md h4,#tkb .md h5{font-weight:600;margin:1.3em 0 .4em;line-height:1.3;color:var(--ink-strong)}
#tkb .md h2{font-size:var(--fs-body);border-bottom:1px solid var(--line);padding-bottom:4px}#tkb .md h3{font-size:var(--fs-body)}#tkb .md h4,#tkb .md h5{font-size:var(--fs-body);color:var(--mute)}
#tkb .md ul,#tkb .md ol{margin:.5em 0;padding-left:1.4em}#tkb .md li{margin:.25em 0}#tkb .md li:has(.ck){list-style:none;margin-left:-1.2em}
#tkb .md code{font:var(--fs-caption) var(--font-mono);background:var(--code-bg);border-radius:var(--radius-sm);padding:1px 5px}
#tkb .md pre{background:var(--inset);border:1px solid var(--line);border-radius:var(--radius-md);padding:10px 12px;margin:.7em 0;overflow:auto}#tkb .md pre code{background:none;padding:0;white-space:pre}
#tkb .md a{color:var(--acc);text-decoration:none}#tkb .md a:hover{text-decoration:underline}#tkb .md hr{border:0;border-top:1px solid var(--line);margin:1.2em 0}
#tkb .md blockquote{margin:.7em 0;padding:2px 12px;border-left:3px solid var(--line);color:var(--mute)}
#tkb .md table{border-collapse:collapse;margin:.7em 0;font-size:var(--fs-caption)}#tkb .md th,#tkb .md td{border:1px solid var(--line);padding:4px 10px;text-align:left}#tkb .md th{background:var(--card)}
#tkb .md details{border:1px solid var(--line);border-radius:var(--radius-md);margin:.6em 0;padding:2px 12px;background:var(--panel)}#tkb .md details[open]{padding-bottom:8px}
#tkb .md summary{cursor:pointer;font-weight:600;padding:4px 0;color:var(--ink-strong)}#tkb .md img{max-width:100%;border-radius:var(--radius-sm)}#tkb .md kbd{font:var(--fs-caption) var(--font-mono);border:1px solid var(--line);border-bottom-width:2px;border-radius:var(--radius-sm);padding:0 5px;background:var(--code-bg)}
#tkb .md blockquote.alert{border-left-width:3px;background:var(--card);border-radius:0 var(--radius-md) var(--radius-md) 0;color:var(--fg)}#tkb .md .at{font-size:var(--fs-caption);font-weight:600;letter-spacing:.06em;text-transform:uppercase;margin-top:4px}
#tkb .md .alert.note{border-color:var(--acc)}#tkb .md .alert.tip{border-color:var(--ok)}#tkb .md .alert.important{border-color:var(--alert-important)}#tkb .md .alert.warning{border-color:var(--warn)}#tkb .md .alert.caution{border-color:var(--err)}
#tkb .md .alert.warning .at{color:var(--warn)}#tkb .md .alert.caution .at{color:var(--err)}#tkb .md .alert.note .at{color:var(--acc)}#tkb .md .alert.tip .at{color:var(--ok)}#tkb .md .alert.important .at{color:var(--alert-important)}
#tkb .md blockquote>:first-child{margin-top:.3em}#tkb .md blockquote>:last-child{margin-bottom:.3em}
#tkb .ck{display:inline-block;width:13px;height:13px;border:2px solid var(--mute);border-radius:var(--radius-sm);margin-right:8px;vertical-align:-2px;position:relative}
#tkb .ck.on{background:var(--ok);border-color:var(--ok)}#tkb .ck.on::after{content:"";position:absolute;left:3px;top:0;width:3px;height:7px;border:solid var(--bg);border-width:0 2px 2px 0;transform:rotate(45deg)}
#tkb .cm{border:1px solid var(--line);border-left:3px solid var(--acc);border-radius:var(--radius-lg);background:var(--card);padding:8px 14px 10px;margin:10px 0}#tkb .cm .meta{margin-bottom:2px}
#tkb .prbar{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0 8px}#tkb .pill{font-size:var(--fs-caption);border:1px solid var(--line);background:var(--card);border-radius:var(--radius-lg);padding:1px 9px}#tkb .pill.ok{border-color:var(--tint-ok-line);color:var(--ok)}#tkb .pill.bad{border-color:var(--tint-err-line);color:var(--err)}
#tkb h3.sec{font-size:var(--fs-caption);text-transform:uppercase;letter-spacing:.06em;color:var(--mute);margin:20px 0 4px}
body.dockb #bbody{display:flex;overflow-x:auto;min-height:320px}

body.dockb #bbody>.col{flex:1 1 0;min-width:var(--col-w);display:flex;flex-direction:column;height:100%;border-right:1px solid var(--line);min-height:0}
body.dockb .ch{display:flex;justify-content:space-between;gap:10px;align-items:center;padding:8px 10px;border-bottom:1px solid var(--line);font-weight:600;background:var(--card);flex:none}   /* the gap is stated: without it a header that is asked to pack its spans reads `Todo0` (#1809) */
body.dockb .col>.sc{flex:1 1 0;min-height:0;max-height:none;overflow-y:auto;overscroll-behavior:contain}
.prs{background:linear-gradient(color-mix(in srgb,var(--cc) 8%,transparent),color-mix(in srgb,var(--cc) 3%,transparent))}   /* the open-PR lane wears the colour colOf gave it, faint, so a tracking lane never looks like a column of work */
#bbody .acc.prs{border-bottom:2px solid var(--cc);margin-bottom:4px}
#bbody .prs>.ah,#bbody .prs>.ch{background:color-mix(in srgb,var(--cc) 12%,var(--bg));color:var(--cc);border-bottom:1px solid color-mix(in srgb,var(--cc) 40%,transparent);letter-spacing:.04em;text-transform:uppercase}
#bbody .prs .it{background:color-mix(in srgb,var(--cc) 8%,var(--inset));border-radius:var(--radius-lg)}
#bbody .prs .it .id{background:color-mix(in srgb,var(--cc) 13%,transparent);border-color:var(--cc);color:var(--cc)}
#bbody .prs .it .rpo{font-size:var(--fs-caption);letter-spacing:.04em;text-transform:uppercase;color:var(--cc);border:1px solid color-mix(in srgb,var(--cc) 45%,transparent);border-radius:var(--radius-xl);padding:0 5px;white-space:nowrap}   /* the tag naming the card's repository (#1820) */
#bbody .perr{margin:0 0 6px;padding:4px 6px;font-size:var(--fs-caption);line-height:1.35;border-radius:var(--radius-lg);overflow-wrap:anywhere;color:var(--err-strong);background:color-mix(in srgb,var(--err) 12%,var(--inset));border:1px solid color-mix(in srgb,var(--err) 40%,transparent)}   /* the unreadable repository, on the column (#1820) */
body.dockb #bbody>.col.prs{box-shadow:inset 0 2px 0 var(--cc);flex:1.15 1 0}
#bcases:empty{display:none}#bcases{display:flex;flex-wrap:wrap;gap:4px 10px;padding:3px 8px;font-size:var(--fs-caption);border-bottom:1px solid var(--line)}#bcases .tcrow{display:flex;gap:6px;align-items:baseline}#bcases .tcrow.fail .tr{color:var(--err)}#bcases .tcrow.pass .tr{color:var(--ok)}   /* a test case row, beside the axis (#306) */
body.dockb #bbody>.col.lane+.col:not(.lane){border-left:3px double var(--line)}#bbody .acc.lane+.acc:not(.lane){margin-top:10px}   /* the PR lanes sit ahead of the axis: a rule marks where the columns start (#247) */
body.dockb #bbody>.col.prcol{background:linear-gradient(color-mix(in srgb,var(--cc) 8%,transparent),color-mix(in srgb,var(--cc) 3%,transparent));box-shadow:inset 0 2px 0 var(--cc);flex:1.15 1 0}
body.dockb .sec{display:flex;flex-direction:column;min-height:0;flex:none}body.dockb .sec.open{flex:1 1 0}body.dockb .sec:not(.open)>.sc{display:none}
body.dockb .sec>.ch{cursor:pointer;border-top:1px solid var(--tint-run-line)}body.dockb .sec:first-child>.ch{border-top:0}
.eye{cursor:pointer;margin-left:8px;opacity:.55;display:inline-flex;vertical-align:middle}.eye:hover{opacity:1}#bbody .acc.hidcol>.ah{opacity:.45}#bbody .acc:not(.open)>.sc,#bbody .acc:not(.open)>.pad{display:none}
.hidbar{flex:none;align-self:stretch;padding:8px 10px;color:var(--mute);font-size:var(--fs-caption);display:flex;flex-direction:column;gap:4px;border-right:1px solid var(--line);min-width:120px;max-width:120px;min-height:0;overflow:hidden}   /* the hidden: strip, the docked row's last child: align-self:stretch is what lets it be divided by height. Both ends of the width are pinned and the strip clips, so a long user-created column name cannot widen the column now that the label sits on its own line (#1792) */
.hidbar .chip{flex:1 1 0;min-height:0;overflow:hidden;display:flex;flex-direction:column;justify-content:center;align-items:center;text-align:center;padding:2px 8px;background:var(--line);border:1px solid var(--line-strong);border-radius:var(--radius-lg);cursor:pointer;color:var(--ink-soft)}   /* one full-height tile per hidden column: flex:1 1 0 splits the strip's height equally between however many rows there are, so two rows are half each and eight are an eighth (#1792). The chip's own colour is the one #1809's quiet lines are measured against: the name inside the tile wears the caption's --mute (#1793), so the chip keeps the fill and the text colour the two kinds are told apart by */
.hidbar .chip:hover{background:var(--hover);border-color:var(--line-strong)}
.hidbar .chip .lbl{flex:0 1 auto;min-height:0;overflow:hidden;max-width:100%;font-size:var(--fs-caption);line-height:16px;color:var(--fg);text-overflow:ellipsis;white-space:nowrap}   /* the name is the tile's caption: it ellipsizes, it never wraps and its font never shrinks. It is flex:0 1 auto and the number is flex:none, so when a row is shorter than its content the name is the one clipped away */
.hidbar .chip b{flex:none;font-size:var(--fs-display);line-height:20px;font-weight:600;letter-spacing:-.02em;font-variant-numeric:tabular-nums;color:var(--acc)}   /* the number is what the tile is for, and it is never the element that gets cut. --acc is the fallback for a column the table below does not name. These rules are scoped to .hidbar on purpose: a bare .chip would also repaint the sidebar's .sub .chip */
/* The strip's own mapping from column to category, one rule per category: the colour says what the count means and
   never how big it is, because magnitude would light up the two archival piles. The hexes are #1793's, under the
   names the table gives them; the open branch lane's var(--acc) is --acc, so it is written as the token it is. */
/*HID_CHIP_RULES*/
.hidbar .hidsep{border-top:1px solid var(--line);margin:3px 0 1px}   /* the divider the auto-hidden columns sit under: below everything the user hid (#1809) */
.hidbar .hidline{display:flex;justify-content:space-between;gap:10px;align-items:center;padding:0 2px 0 6px;border-left:2px solid var(--cc,var(--mute));color:var(--mute);font-weight:400}   /* a column hidden because it is empty, not because the user asked: no chip fill and a dimmer text than the chip's var(--ink-muted), with a 2px tick in the column's own colour -- the value the header's dot carries (#1793) -- so the two kinds are never confusable at a glance (#1809) */
iframe{flex:1;width:100%;border:1px solid var(--line);background:var(--bg);min-height:0}
.load{display:inline-block;margin-left:4px;padding:0 5px;border-radius:var(--radius-lg);background:var(--tint-warn);color:var(--warn);font-size:var(--fs-caption);border:1px solid var(--tint-warn-line)}
#who{padding:3px 12px;color:var(--mute);border-bottom:1px solid var(--line)}#who:empty{display:none}
/* The project switcher (#876): one link per project, its live count and worst alert as text; one click switches. */
#psw{display:none;position:absolute;top:calc(100% - 4px);left:16px;z-index:55;min-width:340px;flex-direction:column;gap:2px;background:var(--panel);border:1px solid var(--line);border-radius:var(--radius-lg);box-shadow:0 12px 40px var(--shadow-deep);padding:6px;font-size:var(--fs-body)}#psw.on{display:flex}
#psw a{color:var(--fg);text-decoration:none;white-space:nowrap;display:flex;gap:10px;align-items:center;padding:8px 10px;border-radius:var(--radius-lg);min-height:36px}#psw a:hover{background:var(--raise)}
#psw a[aria-current=page]{color:var(--fg);font-weight:600}#psw .repo,#psw .psw-m{color:var(--mute)}#psw .psw-all{color:var(--ok)}
/* Settings > Spend & limits (#926): the guard's switch, cap and factor, the burn it measures, and the diff before a write. */
#lbody select,#lbody input{background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:var(--radius-sm);font:inherit;font-size:var(--fs-field);height:var(--ctl-h);padding:0 6px;min-width:0;max-width:100%}
#lbody input{width:104px;text-align:right;font-variant-numeric:tabular-nums}
#lbody .lv{grid-column:2;justify-self:end;min-width:0;max-width:100%;text-align:right;overflow-wrap:anywhere;color:var(--mute);font-size:var(--fs-caption)}
#lbody .ldiff{margin:8px 0 0;padding:8px 10px;background:var(--tint-warn);border:1px solid var(--tint-warn-line);border-radius:var(--radius-md);font-size:var(--fs-caption)}
#lbody .ldiff b{display:block;margin-bottom:4px;color:var(--warn)}#lbody .ldiff .ln{font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
#lbody .lact{display:flex;gap:6px;margin-top:6px;flex-wrap:wrap}#lbody .lact button{height:var(--ctl-h);padding:0 12px}
/* A field saves on its own (#2433): the check mark sits beside the field, the group's one line names the saved value and
   its age, and a value the hub refused paints its field and states why. */
#lbody .lok{color:var(--ok);font-weight:600;margin-left:6px}#lbody .lbad{color:var(--err-strong);font-weight:600}
#lbody input.bad,#lbody select.bad{border-color:var(--err);box-shadow:0 0 0 1px var(--err)}
#lbody .klok{margin:6px 0 0;font-size:var(--fs-caption);color:var(--mute)}#lbody .setblk>.note .lok{color:var(--ok)}
/* Settings > Communication (#924): a channel or a person is one row. A row is a grid on a desktop and stacks on a phone, and
   each switch carries its own text label. */
#sbody select,#sbody input{background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:var(--radius-sm);font:inherit;font-size:var(--fs-field);height:var(--ctl-h);padding:0 6px;min-width:0;max-width:100%}
#sbody input[type=number]{width:72px}
#sbody .slt{display:grid;gap:4px;margin:4px 0 8px}
.slc{display:grid;grid-template-columns:minmax(0,1.6fr) repeat(5,minmax(0,1fr)) auto;gap:6px;align-items:center;padding:4px 0;border-bottom:1px solid var(--line);font-size:var(--fs-caption)}
.slc.slh{font-weight:600;color:var(--mute);border-bottom:0}
.slc.off b{text-decoration:line-through;color:var(--mute)}
.slc .c1{min-width:0;overflow-wrap:anywhere}
.slc small{display:block;color:var(--warn)}
.slc .swt{display:inline-flex;gap:4px;align-items:center;min-height:32px}
.slc .sev{display:inline-flex;gap:2px;align-items:center;margin-right:6px}
#sbody .slad{display:flex;flex-wrap:wrap;gap:6px;margin:4px 0}
#sbody .sdiff{margin:8px 0 0;padding:8px 10px;background:var(--tint-warn);border:1px solid var(--tint-warn-line);border-radius:var(--radius-md);font-size:var(--fs-caption)}
#sbody .sdiff b{display:block;margin-bottom:4px;color:var(--warn)}#sbody .sdiff .ln{overflow-wrap:anywhere}
#sbody .sact{display:flex;gap:6px;margin-top:6px;flex-wrap:wrap}#sbody .sact button{height:var(--ctl-h);padding:0 12px}
/* The Communication read view (SPEC 2682 #2693): each section states its name and the file its rows come from, and every
   field is text. The file tag is one quiet chip beside the field, never a second colour of its own. */
#sbody .slfile{display:inline-block;margin-left:6px;padding:0 6px;border:1px solid var(--line);border-radius:var(--radius-sm);color:var(--mute);font-size:var(--fs-caption);font-weight:400;text-transform:none;letter-spacing:0;vertical-align:middle;white-space:nowrap}
#sbody .slrow{display:grid;grid-template-columns:minmax(0,1fr) auto auto;gap:8px;align-items:center;padding:5px 0;border-bottom:1px solid var(--line);font-size:var(--fs-body)}#sbody .slrow .slfile{justify-self:start}   /* the file chip keeps its own width in the row's grid, never stretched across it */
#sbody .slrow .sl{color:var(--fg)}#sbody .slrow .sc{justify-self:end;color:var(--mute)}
#sbody .slsec{margin:14px 0 0}
#sbody .slsec>h4{margin:0 0 6px;font-size:var(--fs-body);font-weight:600;color:var(--fg);flex-wrap:wrap}   /* a narrow panel drops the file chips to a second line rather than clipping them */
#sbody .slfiles{display:inline-flex;flex-wrap:wrap;gap:6px}#sbody .slfiles .slfile{margin-left:0}
#sbody .slws{display:flex;align-items:center;flex-wrap:wrap;gap:6px;margin:10px 0 4px;font-size:var(--fs-caption);font-weight:600;color:var(--fg)}
#sbody .sltag{padding:0 6px;border-radius:var(--radius-sm);border:1px solid var(--line);font-weight:400;text-transform:none;letter-spacing:0}
#sbody .sltag.owned{color:var(--ok)}#sbody .sltag.guest{color:var(--warn);border-color:var(--tint-warn-line)}
/* The Communication tree (SPEC 2682 #2824, #2827): Client > Project > Workspace > Channel on the left, the selected
   node's Now block on the right. A badge sits beside its node, a long channel name is cut with an ellipsis and keeps its
   whole text in the tooltip and the accessible name, and a phone folds the tree away after a selection so two tiles stay
   in view. A client holds its default persona and the proposed writes of a change. */
#sbody .slttop{display:grid;grid-template-columns:minmax(260px,400px) minmax(0,1fr);gap:16px;align-items:start}   /* the tree pane holds a channel name and its badges whole, as the mock draws it (#2842); a deep row still reads its name (#2903) */
#sbody .sltcol{min-width:0;padding-right:12px;border-right:1px solid var(--line)}
#sbody .sltnotice{margin:0 0 8px;padding:8px 10px;border:1px solid var(--tint-err-line);border-left:3px solid var(--err);border-radius:var(--radius-md);background:var(--tint-err);color:var(--fg);font-size:var(--fs-caption);line-height:1.4}
#sbody .sltguest{margin:0 0 8px;padding:6px 10px;background:var(--inset);border:1px solid var(--line);border-radius:var(--radius-md);font-size:var(--fs-caption);color:var(--mute)}
#sbody .sltguest b{color:var(--fg)}
#sbody .sltguest.err{background:var(--tint-err);border-color:var(--tint-err-line);color:var(--err-strong)}   /* two guest slots, one served: the line is an alert */
#sbody .sltguest.err b{color:var(--err-strong)}
#sbody .sltguest.err{display:flex;gap:8px;align-items:flex-start;line-height:1.4}
#sbody .sltguest.err .sltgi{flex:none;line-height:1.4}   /* the alert's warning glyph sits beside its two-line sentence */
#sbody .sltree{display:flex;flex-direction:column;gap:1px;min-width:0}
#sbody .slti{display:flex;align-items:flex-start;gap:6px;min-width:0;min-height:32px;padding:5px 8px;line-height:22px;border-radius:var(--radius-sm);color:var(--fg);cursor:pointer}
#sbody .slti:hover{background:var(--inset)}
/* The indent guides (mockup, #2843): one rule per ancestor level, drawn by an empty span of the row, so the line ends at
   the last child of the subtree and never runs past it. The client level (SPEC 2682 #2827) reads bolder than a project. */
#sbody .sltg{flex:0 0 12px;align-self:stretch;margin:-5px 2px -5px 0}   /* the guide runs the full row, through the padding, so the line has no gap between rows */
#sbody .sltg.more{border-left:1px solid var(--line)}
#sbody .slti.lv1{font-weight:600}
#sbody .slti.sel{background:var(--inset);outline:1px solid var(--acc);outline-offset:-1px}
#sbody .slti:focus-visible{outline:2px solid var(--acc);outline-offset:-2px}
#sbody .sltarrow{flex:0 0 12px;color:var(--mute);font-size:var(--fs-caption);line-height:22px}
#sbody .sltkind{flex:0 0 14px;width:14px;height:14px;margin-top:4px;color:var(--mute)}   /* centred on the first line, beside the name */
/* The name comes first (#2903): name and badges share one wrapping line, so badges that do not fit move under the
   name, and the name is cut only when it alone is wider than the row. */
#sbody .sltmain{display:flex;flex-wrap:wrap;align-items:center;column-gap:8px;row-gap:3px;flex:1 1 auto;min-width:0}
#sbody .sltname{flex:1 1 auto;min-width:0;max-width:100%;overflow-wrap:anywhere}
#sbody .sltbadges{display:flex;flex-wrap:wrap;gap:4px;flex:0 0 auto;margin-left:auto;min-width:0;line-height:20px}   /* a badge keeps its whole word: the name gives way, never the badge (#2842) */
#sbody .sltbadges .sltb{flex:0 0 auto;white-space:nowrap}   /* a badge is one word, never cut to a letter */
#sbody .sltb{padding:0 6px;border:1px solid var(--line);border-radius:var(--radius-sm);color:var(--mute);font-size:var(--fs-caption);white-space:nowrap}
#sbody .sltb.err{color:var(--err);border-color:var(--err)}
#sbody .sltb.warn{color:var(--warn);border-color:var(--tint-warn-line)}
#sbody .sltb.muted{color:var(--mute)}
/* The detail pane is its own width container (#2845): a row keys on the pane it sits in, never on the whole Settings
   column, so a long inherited label or a poll value is never cut in a narrow pane. */
#sbody .sltdetail{min-width:0;container-type:inline-size}
#sbody .sltcrumb{display:flex;flex-wrap:wrap;gap:2px;align-items:center;font-size:var(--fs-caption);color:var(--mute)}
#sbody .sltcrumb button{padding:0 4px;background:none;border:0;color:var(--mute);font:inherit;cursor:pointer}
#sbody .sltcrumb button:hover{color:var(--fg)}#sbody .sltcrumb .sp{color:var(--mute)}
#sbody .slth{margin:6px 0 2px;font-size:var(--fs-display);font-weight:600;line-height:1.25;color:var(--fg);overflow-wrap:anywhere}   /* the node title is the largest text of the pane (#2841) */
#sbody .sltsub{margin:0 0 8px;font-size:var(--fs-caption);color:var(--mute)}
/* The channel conversation (#2917): a person's message on the left, the persona's on the right, a plain fact between. */
#sbody .slconv{display:flex;flex-direction:column;gap:8px;max-width:640px}
#sbody .slcb{display:flex;flex-direction:column;gap:2px;padding:8px 12px;border-radius:var(--radius-xl);max-width:85%;font-size:var(--fs-body);color:var(--fg)}
#sbody .slcb.in{align-self:flex-start;background:var(--inset);border:1px solid var(--line)}
#sbody .slcb.out{align-self:flex-end;background:var(--bubble);border:1px solid var(--bubble-line)}
#sbody .slcb.out.draft{border-style:dashed}
#sbody .slcb.sys{align-self:center;max-width:100%;padding:2px 8px;background:none;border:0;color:var(--mute);font-size:var(--fs-caption);text-align:center}
#sbody .slcbn{font-size:var(--fs-caption);color:var(--mute);font-weight:600}
#sbody .slcdet{margin-top:10px}#sbody .slcdet summary{cursor:pointer;color:var(--mute);font-size:var(--fs-caption)}
#sbody .slcdet .setblk{margin-top:8px}
/* The channel verdict answers first (#2912): one sentence above the Now tiles, in the body size. The copy-id control
   keeps the Slack id reachable with no id in the subtitle. */
#sbody .sltverdict{margin:0 0 12px;font-size:var(--fs-body);font-weight:600;color:var(--fg);max-width:72ch}
#sbody .sltids{margin:-4px 0 8px}#sbody .sltcopy{padding:0 8px;font-size:var(--fs-caption)}   /* the copy button keeps the token control height: 32px on the desktop and 44px on the phone */
#sbody .sltopen{margin-left:8px;font-size:var(--fs-caption)}#sbody .sltchadd{margin:16px 0 0}#sbody .sltchadd .slad{display:flex;gap:8px;flex-wrap:wrap}#sbody .sltrm{margin:20px 0 0;padding-top:12px;border-top:1px solid var(--line)}   /* #2918 */
#sbody .slttext{margin:0 0 8px;font-size:var(--fs-body);color:var(--mute);max-width:72ch}
#tab-set #sbody .sltnow{grid-template-columns:repeat(auto-fit,minmax(120px,1fr));max-width:1080px}   /* a count the block does not name still fills one row; the named rules below set the exact columns (#2841). Both ids outrank the generic two-column `.now` */
#tab-set #sbody .sltn5{grid-template-columns:repeat(5,minmax(0,1fr))}   /* a channel's five tiles take one row at a desktop width (#2841) */
#tab-set #sbody .sltn4{grid-template-columns:repeat(4,minmax(0,1fr))}   /* a workspace and the project carry four tiles: one row (#2841) */
#tab-set #sbody .sltnow .tile .tk{color:var(--mute)}   /* the label is a muted caption (#2841) */
#tab-set #sbody .sltnow .tile .tv{font-size:var(--fs-body);font-weight:600;line-height:1.3;overflow-wrap:break-word;word-break:normal;hyphens:none}   /* the value is bold and breaks between words alone (#2841) */
#sbody .sltnow .tile .tss{display:block;margin-top:2px;font-size:var(--fs-caption);color:var(--mute)}
#sbody .sltnote{display:flex;gap:8px;margin:8px 0 0;padding:8px 10px;background:var(--inset);border:1px solid var(--line);border-radius:var(--radius-md);font-size:var(--fs-caption);color:var(--ink-soft)}
#sbody .sltnote .sltni{color:var(--mute)}   /* the info glyph of the note under the tiles */
#sbody .sltdef{margin:6px 0;padding:6px 10px;background:var(--tint-err);border:1px solid var(--tint-err-line);border-radius:var(--radius-md);color:var(--err-strong)}   /* a refused node's defect reads as an alert, in plain words */
#sbody .sltempty{margin:10px 0;padding:12px 14px;border:1px dashed var(--line-strong);border-radius:var(--radius-md)}   /* the empty project's setup box */
#sbody .sltacts{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:8px 0}
#sbody .sltclient{margin:10px 0 0;padding:10px;background:var(--card);border:1px solid var(--line);border-radius:var(--radius-md)}
#sbody .sltch{margin:0 0 4px;font-size:var(--fs-body);font-weight:600;color:var(--fg)}
#sbody .sltclient .sltlab{display:inline-flex;gap:6px;align-items:center;font-size:var(--fs-caption);color:var(--mute)}
#sbody .sltclient select{height:var(--ctl-h);padding:0 8px;background:var(--panel);color:var(--fg);border:1px solid var(--edge);border-radius:var(--radius-sm);font:inherit;font-size:var(--fs-caption)}
#sbody .sltclient .slt2{width:100%}
#sbody .sltsethead{display:flex;align-items:center;justify-content:space-between;gap:8px;margin:14px 0 6px;padding-bottom:4px;border-bottom:1px solid var(--line)}
#sbody .sltsethead .blkh{margin:0}
#sbody .sltrow{display:grid;grid-template-columns:minmax(120px,240px) minmax(0,1fr);gap:8px;align-items:center;padding:5px 0;border-bottom:1px solid var(--line);font-size:var(--fs-body)}
#sbody .sltrow .sl{color:var(--fg)}#sbody .sltrow .sc{justify-self:start;min-width:0}
#sbody .sltprophead{display:flex;align-items:center;justify-content:space-between;gap:8px;margin:14px 0 4px}
#sbody .sltprophead .blkh{margin:0}
#sbody .slr.ok{color:var(--ok)}#sbody .slr.err{color:var(--err)}#sbody .slr.muted{color:var(--mute)}
#sbody .sltprojs{display:flex;flex-wrap:wrap;gap:6px;margin:4px 0 0}
#sbody .sltproj{display:inline-flex;align-items:center;gap:6px;padding:6px 10px;background:var(--inset);border:1px solid var(--line);border-radius:var(--radius-md);font-size:var(--fs-caption);color:var(--fg)}
#sbody .sltback{display:none;margin:0 0 8px}
/* The channel settings of the tree detail (#2844): one row per setting, the controls, and the file that owns the row. */
#sbody .slsetrow{display:grid;grid-template-columns:minmax(96px,150px) minmax(0,1fr) auto;gap:10px;align-items:center;padding:6px 0;border-bottom:1px solid var(--line);font-size:var(--fs-body)}
#sbody .slsetrow .slsetk{color:var(--mute);min-width:0;overflow-wrap:anywhere}
#sbody .slsetc{display:flex;flex-wrap:wrap;gap:8px;align-items:center;min-width:0}
#sbody .slsetc .slsetctl{max-width:100%;min-width:0}
#sbody .slsetc input[type=text].slsetctl{width:100%;max-width:260px}
#sbody .slsetrow .slfile{justify-self:end;margin-left:0}
#sbody .slsetnote{margin:2px 0 0;font-size:var(--fs-caption);color:var(--mute);overflow-wrap:anywhere}
#sbody .slsetval{color:var(--fg);overflow-wrap:anywhere}
/* A panel narrower than 700px has no room for the tree and the detail side by side: the tree alone shows until the
   first selection, then the detail alone with its back button, the phone's own rule at the panel's width (#2843). The
   flag `sltshow` means "the detail alone is shown": a pick sets it true only on a narrow panel, and the back button
   clears it, so a wide panel keeps both columns. A row stacks its label over its control below 560px, so the chip and
   the control keep their room in a narrow pane. */
@container slpanels (max-width:700px){#sbody .slttop{grid-template-columns:minmax(0,1fr)}#sbody .slttop:not(.sltshow) .sltdetail{display:none}
 #sbody .slttop.sltshow .sltcol{display:none}#sbody .sltcol{border-right:0;padding-right:0}#sbody .sltback{display:inline-flex}
 #tab-set #sbody .sltnow{grid-template-columns:repeat(3,minmax(0,1fr))}#tab-set #sbody .sltn4{grid-template-columns:repeat(4,minmax(0,1fr))}}   /* a folded detail keeps three tiles, so every label stays whole; a four-tile block still fills one row (#2841) */
@container (min-width:701px){#sbody .slttop.sltshow .sltcol{display:block}}
@container (max-width:700px){#sbody .slsetc .slsetctl{width:100%;max-width:320px}}
@container (max-width:560px){#sbody .slsetrow{grid-template-columns:minmax(0,1fr) auto;grid-template-areas:"k f" "c c"}
 #sbody .slsetrow .slsetk{grid-area:k}#sbody .slsetrow .slfile{grid-area:f}#sbody .slsetrow .slsetc{grid-area:c}
 #sbody .slsetc input[type=text].slsetctl{max-width:none}}
@media (max-width:600px){#tab-set #sbody .slttop{grid-template-columns:minmax(0,1fr)}#tab-set #sbody .slttop:not(.sltshow) .sltdetail{display:none}#tab-set #sbody .slttop.sltshow .sltcol{display:none}#tab-set #sbody .sltcol{border-right:0;padding-right:0}#tab-set #sbody .sltback{display:inline-flex}#tab-set #sbody .sltnow{grid-template-columns:1fr}#tab-set #sbody .sltn4{grid-template-columns:1fr}#tab-set #sbody .slti{min-height:44px}#tab-set #sbody .slsetrow{grid-template-columns:minmax(0,1fr) auto;grid-template-areas:"k f" "c c"}#tab-set #sbody .slsetrow .slsetk{grid-area:k}#tab-set #sbody .slsetrow .slfile{grid-area:f}#tab-set #sbody .slsetrow .slsetc{grid-area:c}#tab-set #sbody .slsetc input[type=text].slsetctl{width:100%;max-width:none}#tab-set #sbody .slsetc .slsetctl{min-height:44px}}
#sbody .sltjump{margin:0 0 8px;font-size:var(--fs-caption)}
#sbody .sltjump a{color:var(--mute)}#sbody .sltjump a:hover{color:var(--fg)}
#sbody .slperm{width:100%;border-collapse:collapse;font-size:var(--fs-body);margin:4px 0 8px}
#sbody .slperm th{font-size:var(--fs-caption);font-weight:400;color:var(--mute);text-align:left;padding:4px 8px;border-bottom:1px solid var(--line)}
#sbody .slperm td{padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top;overflow-wrap:anywhere}
#sbody .slperm td.no{color:var(--mute)}
#sbody .slpvbox{border:1px solid var(--line);border-radius:var(--radius-sm);padding:8px;margin-top:4px}
#sbody .slpvnow{margin-top:8px}
/* The workspace and channel editor (SPEC 2682 #2696): one detail opens under its row, one label per field, and the id
   lists (roles, announce, draft_for) as one comma-separated line. The panel stacks the fields, so no field is clipped. */
#sbody .slwsdet,#sbody .slchdet{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:8px;margin:6px 0 10px;padding:10px;background:var(--card);border:1px solid var(--line);border-radius:var(--radius-md)}
#sbody .slf{display:flex;flex-direction:column;gap:3px;min-width:0;font-size:var(--fs-caption);color:var(--mute)}
#sbody .slf span{font-weight:600}
#sbody .slf input,#sbody .slf select{width:100%;height:var(--ctl-h);padding:0 8px;background:var(--panel);color:var(--fg);border:1px solid var(--edge);border-radius:var(--radius-sm);font:inherit;font-size:var(--fs-caption)}
#sbody .slchk{display:flex;align-items:center;gap:6px;min-height:var(--ctl-h);font-size:var(--fs-caption);color:var(--fg)}
/* The roles and members editor (SPEC 2682 #2699): a role and a member are one row of labelled fields. A wide panel keeps
   the fields on one line, the actions wrap beside each other, and the user ids field keeps its whole value. A narrow
   panel stacks each field under its own label, so no value is clipped. */
#sbody .slroledet .slevr{display:grid;grid-template-columns:minmax(140px,16ch) minmax(200px,1fr) minmax(120px,16ch) auto;gap:8px;align-items:start;padding:6px 0;border-bottom:1px solid var(--line)}
#sbody .slmembdet .slevr{display:grid;grid-template-columns:minmax(130px,1fr) minmax(130px,1fr) minmax(150px,1.2fr) minmax(110px,.8fr) auto;gap:8px;align-items:center;padding:6px 0;border-bottom:1px solid var(--line)}
#sbody .slroledet .slevr [role=cell],#sbody .slmembdet .slevr [role=cell]{display:flex;flex-wrap:wrap;gap:4px 14px;align-items:center;min-width:0}
#sbody .slroledet .slevr input:not([type=checkbox]),#sbody .slroledet .slevr select,#sbody .slmembdet .slevr input:not([type=checkbox]),#sbody .slmembdet .slevr select{width:100%;min-width:0}
#sbody .slacts{display:flex;flex-wrap:wrap;gap:4px 14px;align-items:center;min-width:0}
#sbody .slad input[data-slroleid],#sbody .slad input[data-slmus]{flex:1 1 220px}
#sbody .slad input[data-slmwho]{flex:1 1 220px}#sbody .slwho{display:flex;flex-wrap:wrap;gap:4px 12px;align-items:baseline;min-width:0}#sbody .slperson{overflow-wrap:anywhere}#sbody .slwhonote{flex-basis:100%;font-size:var(--fs-caption)}#sbody .sladv summary{color:var(--mute);cursor:pointer;font-size:var(--fs-caption)}#sbody .slwhomsg{margin:4px 0 0}#sbody .slwhomsg.err{color:var(--err)}#sbody .slwhopick{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}
#sbody .slad select[data-slmws]{flex:1 1 190px}   /* a phone puts the workspace on its own line, so its team id is never cut */
#sbody .slad select[data-slmch]{flex:1 1 240px}
#sbody .slad select[data-slmrole]{flex:1 1 120px}
#sbody .slmembdet .slalias{display:flex;flex-direction:column;align-items:flex-start;gap:4px;min-width:0}
#sbody .slmembdet .slalias input{width:100%;min-width:0}
/* The members block reads its own width, not the window's (#2911): a wide window with a narrow settings panel stacks each
   member field under its label, so no select is cut. */
#sbody .slmembdet{container-type:inline-size}
@container (max-width:760px){#sbody .slmembdet .slevr{grid-template-columns:1fr}
 #sbody .slmembdet .slevr [role=cell]{display:grid;grid-template-columns:92px minmax(0,1fr);gap:6px;align-items:start}
 #sbody .slmembdet .slevr [role=cell][data-l]::before{content:attr(data-l);color:var(--mute);font-weight:600}}
@media (max-width:1240px){#sbody .slroledet .slevr,#sbody .slmembdet .slevr{grid-template-columns:1fr}
 #sbody .slroledet .slevr [role=cell],#sbody .slmembdet .slevr [role=cell]{display:grid;grid-template-columns:92px minmax(0,1fr);gap:6px;align-items:start}
 #sbody .slroledet .slevr [role=cell][data-l]::before,#sbody .slmembdet .slevr [role=cell][data-l]::before{content:attr(data-l);color:var(--mute);font-weight:600}
 #sbody .slroledet .slevr .slacts{flex-direction:column;flex-wrap:nowrap;align-items:flex-start;gap:2px}
 #sbody .slroledet .slevr .slchk{min-width:0;white-space:normal}
 #sbody .slmembdet .slevr [role=cell][data-l="User ids"]{grid-template-columns:1fr}}
/* The project's Personas section lists the declared personas and the reply preview states four facts with no control;
   the Personas theme edits a persona (#3036). */
#sbody .slhs{margin:12px 0 4px;font-size:var(--fs-caption);font-weight:600;color:var(--fg)}
#sbody .slf textarea{width:100%;min-height:64px;padding:6px;background:var(--panel);color:var(--fg);border:1px solid var(--edge);border-radius:var(--radius-sm);font:inherit;font-size:var(--fs-caption);resize:vertical}
#sbody .slf small{color:var(--mute);font-size:var(--fs-caption)}
#sbody .slf button{height:var(--ctl-h);padding:0 12px}
#sbody .slf audio{max-width:100%}
#sbody .slact{grid-column:1/-1;display:flex;gap:6px}#sbody .slact button{height:var(--ctl-h);padding:0 12px}
#sbody .slsched{grid-column:1/-1;margin:0;color:var(--mute);font-size:var(--fs-caption)}
@media (max-width:1100px){#sbody .slt2 td.sldet{display:block}}   /* a stacked table keeps the detail on the full row, never in the 112 px label column */
#sbody .slws{flex-wrap:wrap}#sbody .slws button{height:32px;padding:0 10px;margin-left:auto}
#sbody .slt2{width:100%;border-collapse:collapse;font-size:var(--fs-caption)}
#sbody .slt2 th{text-align:left;color:var(--mute);font-weight:600;padding:4px 8px 4px 0;border-bottom:1px solid var(--line)}
#sbody .slt2 td{padding:5px 8px 5px 0;border-bottom:1px solid var(--line);color:var(--fg);vertical-align:top;overflow-wrap:anywhere}
#sbody .slt2 .nw{white-space:nowrap}   /* a voice, a mode, a language tag or a persona id never breaks inside the word */
#sbody .slt2 td button{white-space:nowrap;justify-self:start;min-width:0}   /* the Personas table's Edit button stays on one line and inside its cell when the row stacks (#2841) */
@media (max-width:1100px){#sbody .slt2,#sbody .slt2 tbody,#sbody .slt2 tr{display:block}#sbody .slt2 thead{display:none}#sbody .slt2 tr{padding:4px 0;border-bottom:1px solid var(--line)}#sbody .slt2 td{display:grid;grid-template-columns:112px minmax(0,1fr);gap:8px;border:0;padding:2px 0}#sbody .slt2 td[data-l]::before{content:attr(data-l);color:var(--mute);font-weight:600}#sbody .slt2 .nw{white-space:normal}}   /* a panel too narrow for the table stacks each cell under its own label, so no column is clipped */
@media (max-width:600px){.slc{grid-template-columns:1fr}.slc.slh{display:none}.slc [data-l]:not(.swc)::before{content:attr(data-l) ": ";color:var(--mute)}.slc .swt{min-height:44px}}
#tab-set #prbody .prgrp{margin:0 0 16px}#tab-set #prbody .prgrp h4{margin:0 0 6px;font-size:var(--fs-body);color:var(--fg)}
#tab-set #prbody .ptab{width:100%;border-collapse:collapse;font-size:var(--fs-caption)}
#tab-set #prbody .ptab th{text-align:left;color:var(--mute);font-weight:600;padding:4px 8px 4px 0;border-bottom:1px solid var(--line)}
#tab-set #prbody .ptab td{padding:5px 8px 5px 0;border-bottom:1px solid var(--line);color:var(--fg);vertical-align:top;overflow-wrap:break-word;word-break:normal;hyphens:none}   /* a long file name breaks between words alone, so no word is cut and no letter is hyphenated */
#tab-set #prbody .ptab .nw{white-space:nowrap}#tab-set #prbody .ptab .pv{min-width:0}
#tab-set #prbody .prfile{display:inline-block;margin-left:6px;padding:0 6px;border:1px solid var(--line);border-radius:var(--radius-sm);color:var(--mute);font-weight:400}   /* the file reads as a chip, as the Slack theme draws it, so the reader tells a name from the file it lives in */
#tab-set #prbody .pflag{display:inline-block;margin:0 4px 2px 0;padding:0 7px;border-radius:var(--radius-lg);border:1px solid var(--line);color:var(--mute);font-size:var(--fs-caption);font-weight:600}
#tab-set #prbody .pflag.err{border-color:var(--tint-err-line);background:var(--tint-err);color:var(--err)}
#tab-set #prbody .prf{display:block;margin:0 0 8px}#tab-set #prbody .prf input[type=checkbox]{margin-right:4px}
#tab-set #prbody .prbar{display:flex;gap:6px;flex-wrap:wrap;margin:8px 0}
#tab-set #prbody .pract{display:flex;gap:6px;flex-wrap:wrap}#tab-set #prbody .pract button{white-space:nowrap}   /* Edit and Add to project sit on one line while the cell allows */
#tab-set #prbody .prflags{display:block;margin-top:4px}#tab-set #prbody .prproj{display:block}#tab-set #prbody .prproj+.prproj{margin-top:4px}
#tab-set #prbody .prproj a{color:var(--fg);white-space:nowrap}#tab-set #prbody .ptab td[data-l="Tone"]{max-width:56ch}#tab-set #prbody .ptab td[data-l="Actions"]{width:1%}#tab-set #prbody .prproj a:hover{color:var(--acc)}
#tab-set #prbody tr.prfocus>td{background:var(--tint-acc)}   /* the row a Slack link opened */
#tab-set #prbody .ptab tr.preditrow>td{display:table-cell;padding:10px 12px 12px;background:var(--panel);border:1px solid var(--edge);border-radius:var(--radius-md)}
#tab-set #prbody .prf textarea{display:block;width:100%;max-width:640px;min-height:72px;margin-top:4px;padding:6px;background:var(--panel);color:var(--fg);border:1px solid var(--edge);border-radius:var(--radius-sm);font:inherit;resize:vertical}
#tab-set #prbody .prf input:not([type=checkbox]):not([type=radio]),#tab-set #prbody .prf select{display:block;margin-top:4px;min-width:220px}
#tab-set #prbody .prf small,#tab-set #prbody .prf .m{color:var(--mute);font-size:var(--fs-caption)}
#tab-set #prbody fieldset.prf{border:1px solid var(--line);border-radius:var(--radius-sm);padding:6px 10px}#tab-set #prbody fieldset.prf label{display:flex;align-items:center;flex-wrap:wrap;gap:4px;margin:4px 0}#tab-set #prbody fieldset.prf .prfile{margin-left:2px}
@media (max-width:600px){#tab-set #prbody .ptab,#tab-set #prbody .ptab tbody,#tab-set #prbody .ptab tr{display:block}#tab-set #prbody .ptab thead{display:none}#tab-set #prbody .ptab tr{padding:6px 0;border-bottom:1px solid var(--line)}#tab-set #prbody .ptab td{display:grid;grid-template-columns:112px minmax(0,1fr);gap:8px;border:0;padding:2px 0}#tab-set #prbody .ptab td[data-l]::before{content:attr(data-l);color:var(--mute);font-weight:600}#tab-set #prbody .ptab .nw{white-space:normal}#tab-set #prbody .ptab tbody tr.preditrow>td{display:block;padding:10px}#tab-set #prbody .ptab td[data-l="Tone"]{max-width:none}#tab-set #prbody .ptab td[data-l="Actions"]{width:auto}}   /* a phone stacks each field under its own label, so no column is clipped and no word is cut (#3010) */
@container slpanels (max-width:760px){#tab-set #prbody .ptab,#tab-set #prbody .ptab tbody,#tab-set #prbody .ptab tr{display:block}#tab-set #prbody .ptab thead{display:none}#tab-set #prbody .ptab tr{padding:6px 0;border-bottom:1px solid var(--line)}#tab-set #prbody .ptab td{display:grid;grid-template-columns:112px minmax(0,1fr);gap:8px;border:0;padding:2px 0}#tab-set #prbody .ptab td[data-l]::before{content:attr(data-l);color:var(--mute);font-weight:600}#tab-set #prbody .ptab .nw{white-space:normal}#tab-set #prbody .ptab tbody tr.preditrow>td{display:block;padding:10px}#tab-set #prbody .ptab td[data-l="Tone"]{max-width:none}#tab-set #prbody .ptab td[data-l="Actions"]{width:auto}}   /* a narrow settings column stacks the rows too, as the Slack tree does under 700 px: the Tone column never shrinks to one word a line (#3036) */
/* A phone forced into the desktop view (#926): one column, a full-width sidebar, and nothing wider than 390 px. The docked
   board lays the window out as a two-column grid of its own (`body.dockb`), so the phone states both layouts: on a 390 px
   screen the 300 px sidebar and the board column are what a reader has to scroll sideways to reach. */
@media (max-width:600px){body{height:auto}body.tkopen #win{margin-right:0}body.tkopen #board{width:auto}#win{display:block;overflow:visible}body.dockb #win{display:block;grid-template-columns:none;grid-template-rows:none}#side{width:auto;border-right:0;border-bottom:1px solid var(--line)}body.dockb #side{grid-row:auto;grid-column:auto}#main{min-height:50vh}body.dockb #main{grid-column:auto;grid-row:auto}#board{width:auto}body.dockb #board{grid-column:auto;grid-row:auto;height:60vh}#lbody input{width:92px}}
/* The page frame of the mock (#889): the header with its tabs, and the sidebar cards. */
/* The loop switch (#923): the strip's draining and starting states and the stop confirm. The Monitor carries the switch
   and its row (#1071); the Loop theme left Settings (#2515), and its change list lives in the Loop tab, drawn as the
   Spend & limits Changes list is. */
#lopaud{margin:18px 0 0;padding:12px 0 0;border-top:1px solid var(--line)}
#lopaud details.aud>summary{display:flex;align-items:center;gap:6px;min-height:32px;cursor:pointer;font-size:var(--fs-body);color:var(--fg);list-style:none}
#lopaud details.aud>summary::-webkit-details-marker{display:none}#lopaud details.aud>summary::before{content:"\25B8";color:var(--mute)}#lopaud details.aud[open]>summary::before{content:"\25BE"}
#lopaud details.aud>summary .m{color:var(--mute)}
#lopaud ul.laud{list-style:none;margin:4px 0 0;padding:0;font-size:var(--fs-caption);color:var(--mute)}#lopaud ul.laud li{padding:3px 0;border-bottom:1px solid var(--line);overflow-wrap:anywhere}#lopaud ul.laud li:last-child{border:0}
#lopaud ul.laud b{color:var(--fg);font-weight:600;font-variant-numeric:tabular-nums}
#accb .kr b{font-weight:600;font-size:var(--fs-caption);line-height:1.3;text-align:right}#accb .ok{color:var(--ok)}#accb .no{color:var(--warn)}#accb p{color:var(--mute);margin:8px 0 0}
.sdev{padding:8px 0 0;border-top:1px solid var(--line)}.sdev h4{margin:0 0 4px;font-size:var(--fs-caption);color:var(--mute)}.sdev a{margin-right:10px}.sdev p{margin:6px 0 0}
#ldg{background:var(--panel);color:var(--fg);border:1px solid var(--edge);border-radius:var(--radius-lg);padding:16px;width:min(520px,92vw)}#ldg::backdrop{background:var(--scrim)}#ldg .lrad{display:flex;gap:8px;align-items:flex-start;padding:8px 0;cursor:pointer}#ldg .lrad small{display:block;color:var(--mute)}#ldg ul{margin:4px 0 0;padding-left:18px}#ldg .lact{display:flex;gap:6px;margin-top:10px}
header.top{position:relative;display:flex;align-items:center;gap:12px;padding:10px 16px;border-bottom:1px solid var(--line);flex:none}
header.top h1{font-size:var(--fs-display);margin:0}header.top .spx{flex:1}
header.top .pill{font-size:var(--fs-caption);color:var(--mute)}
#pj{font:inherit;font-weight:600;background:none;border:1px solid transparent;padding:2px 8px;min-height:36px}#pj:hover{border-color:var(--line)}
.tabs{display:flex;gap:4px}.tabs button{white-space:nowrap}.tabs button.on{border-color:var(--acc);color:var(--acc)}
.rkt{display:flex;gap:4px;border-bottom:1px solid var(--line);margin:0 0 12px}.rkt button{background:none;border:0;border-bottom:2px solid transparent;border-radius:0;padding:6px 10px;min-height:36px;color:var(--mute);font-weight:600}.rkt button.on{color:var(--fg);border-bottom-color:var(--acc)}.rkt button:hover{color:var(--fg)}
#tab-runs #main{min-height:60vh}
#side .sp .sb{padding:0 10px 10px}#side .sp>summary::before{content:"\25B8";color:var(--mute);width:10px}#side .sp[open]>summary::before{content:"\25BE"}
#side .sp>summary .sr{font-size:var(--fs-caption)}
#lrun .lr{display:grid;grid-template-columns:auto 1fr auto;align-items:center;gap:3px 6px;border:1px solid var(--line);border-radius:var(--radius-lg);margin-bottom:6px;background:var(--card);min-width:0;padding:6px 8px;color:var(--fg);cursor:default}   /* one short row per item (#2507): the icon, the id with its chips, and the age; the chips join the id's line when they fit and stack under it when they do not */
#lrun .lr:hover{border-color:var(--line-strong)}#lrun .lr[data-id]{cursor:pointer}
#lrun .lr .lic{grid-column:1;flex:none;font-size:var(--fs-caption);line-height:1}
#lrun .lr .lm{grid-column:2;display:flex;flex-wrap:wrap;align-items:center;gap:3px 6px;min-width:0}
#lrun .lr.stack{align-items:start}#lrun .lr.stack .lm{flex-direction:column;align-items:flex-start}   /* the chips did not fit the id's line: they take their own line, under the id, and the icon and the age stay on the id's line (#2507) */
#lrun .lr .lid{color:var(--lc,var(--fg));font-weight:600;white-space:nowrap;text-decoration:none;font-size:var(--fs-caption)}
#lrun .lr a.lid{color:var(--acc)}#lrun .lr a.lid:hover{text-decoration:underline}
#lrun .lr .lchips{display:flex;flex-wrap:wrap;align-items:center;gap:3px;min-width:0}
#lrun .lr .lage{grid-column:3;color:var(--mute);font-size:var(--fs-caption);white-space:nowrap;font-variant-numeric:tabular-nums}
#lrun .lch{border:1px solid var(--line-strong);border-radius:var(--radius-full);padding:0 6px;font-size:var(--fs-caption);line-height:16px;white-space:nowrap;color:var(--mute);background:transparent}
#lrun .lch.done{color:var(--ok);border-color:var(--ok)}
#lrun .lch.current{color:var(--warn);border-color:var(--warn)}
#lrun .lch.later{color:var(--mute);border-color:var(--line);opacity:.75}
#lrun .haleye,#lrd .haleye{position:relative;display:inline-block;width:14px;height:14px;border-radius:9999px;background:radial-gradient(circle closest-side,rgb(255 240 176) 0 5%,rgb(255 74 24) 12%,rgb(224 18 6) 24%,rgb(138 4 0) 40%,rgb(46 0 0) 56%,rgb(5 0 0) 72%,rgb(0 0 0) 77%,transparent 78%),linear-gradient(160deg,rgb(195 200 206) 0%,rgb(93 99 107) 35%,rgb(27 30 34) 65%,rgb(108 114 122) 100%);box-shadow:0 0 0 1px rgb(0 0 0),inset 0 0 0 1px rgba(255 255 255 / .15)}   /* the HAL9k hold's red eye in a dark ring (#2572): drawn in CSS, no image file, at the size of the other row icons */
#lrun .haleye::after,#lrd .haleye::after{content:"";position:absolute;left:30%;top:24%;width:16%;height:10%;border-radius:9999px;background:rgba(255 255 255 / .55);transform:rotate(-30deg);filter:blur(.4px)}
#lrun .lslot{border:1px solid var(--line-strong);border-radius:var(--radius-full);padding:0 6px;font-size:var(--fs-caption);line-height:16px;white-space:nowrap;color:var(--ink-soft)}
#lrun .lr.red{border-color:var(--err)}#lrun .lr.red .lid,#lrun .lr.red .lic{color:var(--err)}
#lrun .lr.quiet{box-shadow:inset 2px 0 0 var(--warn)}#lrun .lr.quiet.show{box-shadow:inset 3px 0 0 var(--acc),inset 5px 0 0 var(--warn)}
#lrd>summary .sr{display:flex;gap:6px;align-items:center}#lrd .lpill{display:inline-flex;align-items:center;gap:3px;border:1px solid var(--line-strong);border-radius:var(--radius-full);padding:0 7px;font-size:var(--fs-caption);line-height:16px;color:var(--ink-soft)}#lrd .lpill b{color:var(--fg);font-variant-numeric:tabular-nums}
#lrun .lf{margin-top:2px;color:var(--mute);font-size:var(--fs-caption);text-align:right}
#flowb svg{max-width:none}#tab-set details.sp{max-width:1120px}
/*BOARD_LANES_CSS*/
/*RUN_SHEET_CSS*/
/* the Runs tab: one list, Live then Ended today (#1698, #1770) */
#main .r{display:flex;gap:8px;align-items:baseline;justify-content:flex-start;padding:8px 10px;border:1px solid var(--line);border-radius:var(--radius-lg);margin-bottom:8px;background:var(--card);cursor:pointer}#main .r:hover,#main .r:focus-visible{border-color:var(--acc)}#main h3{display:flex;align-items:baseline;gap:8px;font-size:var(--fs-caption);text-transform:uppercase;letter-spacing:.06em;color:var(--mute);margin:12px 0 6px}#main h3 .sr{margin-left:auto;font-weight:400;text-transform:none;letter-spacing:0}
#main .r .m{margin-left:auto;white-space:nowrap}
#main .r>div{flex:1;min-width:0;display:flex;flex-wrap:wrap;align-items:baseline;gap:2px 8px}#main .r .st{order:3;flex-basis:100%;max-width:none}#main .pn{position:static;opacity:1;pointer-events:auto}#main .r.cc:not([data-rid]){cursor:default}
#bhead select{min-height:32px;padding:4px 12px;font-size:var(--fs-body);border-radius:var(--radius-md)}#bhead label.hck input{width:18px;aspect-ratio:1;padding:0}
#bbody .it>.hld{order:1;flex:0 0 auto;margin:0;font-size:var(--fs-caption);font-weight:600;color:var(--s5);white-space:nowrap}#bbody .it>.hld::before{content:"\25CF";margin-right:4px;color:var(--s5)}
.isub{display:grid;grid-template-columns:minmax(0,1fr);gap:2px;margin:0 0 10px}.isub .srow{display:grid;grid-template-columns:minmax(0,1fr) auto;grid-template-areas:"n p" "b r";align-items:center;gap:3px 8px;padding:5px 0;font-size:var(--fs-caption);border-bottom:1px solid var(--line)}.isub .srow:last-child{border-bottom:0}.isub .sn{grid-area:n;color:var(--fg);font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.isub .sp2{grid-area:p;text-align:right;font-variant-numeric:tabular-nums;color:var(--fg);font-weight:600}.isub .sb2{grid-area:b;height:6px;border-radius:var(--radius-sm);background:var(--line);overflow:hidden}.isub .sb2 i{display:block;height:100%;border-radius:var(--radius-sm);background:var(--ok)}.isub .srow.warn .sb2 i{background:var(--warn)}.isub .srow.red .sb2 i{background:var(--err)}.isub .srow.na .sb2{background:repeating-linear-gradient(90deg,var(--line) 0 3px,transparent 3px 7px)}.isub .srow.na .sp2{color:var(--mute)}.isub .srow.na{grid-template-areas:"n p" "b b" "r r"}.isub .srow.na .sr2{white-space:normal;text-align:left;overflow-wrap:anywhere}.isub .sr2{grid-area:r;text-align:right;color:var(--mute);font-variant-numeric:tabular-nums;white-space:nowrap}
#tab-set .sp .sb{padding:0 12px 12px}
/* Settings (#913): a left theme list, one theme at a time, one Save bar; on the phone a select replaces the list and rows stack. */
#tab-set .swrap{display:grid;grid-template-columns:200px minmax(0,1fr);gap:16px;max-width:1120px;align-items:start}#tab-set .spanels{min-width:0;container-type:inline-size;container-name:slpanels}   /* #2540: the themes answer their own column width with a container query */
#tab-set .snav{display:flex;flex-direction:column;gap:4px;position:sticky;top:6px;max-height:calc(100vh - 130px);overflow:auto}#tab-set .snav button{text-align:left;padding:8px 10px;border:1px solid var(--line);border-radius:var(--radius-md);background:var(--panel);color:var(--fg);min-height:44px}
#tab-set .snav button small{display:block;color:var(--mute);font-size:var(--fs-caption)}#tab-set .snav button.on{border-color:var(--acc);background:var(--bubble)}
#tab-set .snav button small.err,.sp>summary .sr.err{color:var(--err);font-weight:600}   /* a state that needs a look: the same err tone in both places (#1282) */
#tab-set .ssel{display:none}#tab-set details.sp:not(.on){display:none}
/* #2907: the Slack tree and its detail pane use the width a collapsed sidebar frees; every other theme keeps its cap */
#tab-set .swrap:has(#slk.on){max-width:none}#tab-set details#slk{max-width:none}#tab-set:has(#slk.on) #sbar{max-width:none}
#tab-set .swrap:has(#prs.on){max-width:none}#tab-set details#prs{max-width:none}#tab-set:has(#prs.on) #sbar{max-width:none}   /* #3036: the Personas table takes the same free width, so its Tone column is not squeezed */
#sbar{position:sticky;bottom:0;display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:8px 12px;margin-top:12px;max-width:1120px;background:var(--panel);border-top:1px solid var(--line);z-index:2}#sbar[hidden]{display:none}
#sbar.dirty{background:var(--tint-warn);border-top-color:var(--tint-warn-line)}#sbar button{height:var(--ctl-h);padding:0 12px}#sbar.dirty #ssave{background:var(--warn);color:var(--on-tone)}#schg{color:var(--mute);font-size:var(--fs-caption)}#schg.err{color:var(--err);font-weight:600}#schg.ok{color:var(--ok)}
#sdlg{background:var(--panel);color:var(--fg);border:1px solid var(--edge);border-radius:var(--radius-lg);max-width:min(720px,94vw)}#sdpre{white-space:pre-wrap;font-size:var(--fs-caption);max-height:50vh;overflow:auto}#sdlg .sact{display:flex;gap:8px}
/* Settings (#1278): every theme draws the same two blocks. Now holds read-only facts as tiles; Settings holds editable rows
   only, label left and control right. Tones are the three the hub names plus the neutral one it names for a value it cannot
   verify, so a tile never invents a ladder of its own. The tailnet view is read only: every control under the panels is off
   and the reason sits above them. */
#tab-set .blkh{font-size:var(--fs-caption);text-transform:uppercase;letter-spacing:.06em;color:var(--mute);margin:0 0 4px}
#tab-set .setblk{margin:0 0 14px}
#tab-set .sllegend{margin:0 0 12px}#tab-set .sllegend>.blkh{display:block;margin:10px 0 6px;font-size:var(--fs-caption);font-weight:600;color:var(--fg);text-transform:none;letter-spacing:0}   /* Roles reads as a sibling of Channels and People, not as a block label */#tab-set .sllegend dl{margin:0;padding:0}#tab-set .sllegend dt{font-weight:600;font-size:var(--fs-caption);color:var(--fg);margin:2px 0 0}#tab-set .sllegend dd{margin:0;color:var(--mute);font-size:var(--fs-caption)}
#tab-set .now{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px;margin:0 0 10px}
#tab-set .tile{background:var(--inset);border:1px solid var(--line);border-radius:var(--radius-md);padding:6px 8px;min-width:0;font-size:var(--fs-caption)}
#tab-set .tile .tk{display:block;color:var(--mute);font-weight:600}
#tab-set .tile .tv{display:block;color:var(--fg);font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
#tab-set .tile.ok .tv{color:var(--ok)}#tab-set .tile.warn .tv{color:var(--warn)}#tab-set .tile.err .tv{color:var(--err)}#tab-set .tile.neutral .tv{color:var(--fg)}#tab-set .tile.muted{opacity:.45}#tab-set .tile.muted .tv{color:var(--mute)}
#tab-set .setrow{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px;align-items:center;padding:5px 0;border-bottom:1px solid var(--line);min-width:0}
#tab-set .setrow .sl{min-width:0;font-size:var(--fs-body);color:var(--fg)}
#tab-set .setrow .sc{justify-self:end;min-width:0;max-width:100%}
#tab-set .setrow .sc small{color:var(--mute);font-size:var(--fs-caption)}
/* Settings > Spend & limits · Kanban (#2512): the group is the spec's four-column table, so every row lines up with its
   header and no label wraps into its field at 1000 px wide. */
#tab-set .khead{display:flex;align-items:baseline;gap:8px;font-size:var(--fs-body);font-weight:600;color:var(--fg);text-transform:none;letter-spacing:0}
#tab-set .khead .ksub{font-size:var(--fs-body);font-weight:400;color:var(--mute);text-transform:none;letter-spacing:0}
#tab-set .khead #kanchip,#tab-set .khead #slotchip{margin-left:auto;font-size:var(--fs-body);font-weight:600;text-transform:none;letter-spacing:0;white-space:nowrap}
#tab-set .khead #kanchip.ok,#tab-set .khead #slotchip.ok{color:var(--ok)}#tab-set .khead #kanchip.err,#tab-set .khead #slotchip.err{color:var(--warn)}
#tab-set .ktab{width:100%;border-collapse:collapse;table-layout:fixed;font-size:var(--fs-body)}
#tab-set .ktab .kcol1{width:18%}#tab-set .ktab .kcol2{width:24%}#tab-set .ktab .kcol3{width:20%}#tab-set .ktab .kcol4{width:38%}
#tab-set .ktab thead th{text-align:left;color:var(--mute);font-weight:600;font-size:var(--fs-caption);padding:0 10px 6px 0;border-bottom:1px solid var(--line);white-space:nowrap}
#tab-set .ktab tbody th,#tab-set .ktab tbody td{text-align:left;font-weight:400;padding:9px 10px 9px 0;border-bottom:1px solid var(--line);vertical-align:middle;min-width:0}
#tab-set .ktab tbody th{white-space:nowrap;font-size:var(--fs-body);color:var(--fg)}
#tab-set .ktab .kl input{width:100%;max-width:110px;box-sizing:border-box;font-size:var(--fs-caption);padding:0 6px;text-align:left}
#tab-set .ktab .kn{white-space:nowrap}
#tab-set .ktab .ks{color:var(--fg);font-size:var(--fs-body);line-height:1.35;overflow-wrap:anywhere}
/* #2513: a fixed layout with a pinned width per column, so a wide Slots cell never shrinks the label column at 1000 px;
   the Slots cell reserves a 10 px gap on its left, so the "N of M" text never touches the cap field. */
#tab-set .stab{width:100%;border-collapse:collapse;table-layout:fixed;font-size:var(--fs-body);margin:6px 0 4px}
#tab-set .stab .scol1{width:25%}#tab-set .stab .scol2{width:23%}#tab-set .stab .scol3{width:24%}#tab-set .stab .scol4{width:28%}
#tab-set .stab thead th{text-align:left;color:var(--mute);font-weight:600;font-size:var(--fs-caption);padding:0 10px 6px 0;border-bottom:1px solid var(--line);white-space:nowrap}
#tab-set .stab tbody th,#tab-set .stab tbody td{text-align:left;font-weight:400;padding:9px 10px 9px 0;border-bottom:1px solid var(--line);vertical-align:middle;min-width:0;overflow-wrap:anywhere}
#tab-set .stab tbody th{white-space:normal;overflow-wrap:anywhere;color:var(--fg)}
#tab-set .stab td.scol3{padding-left:10px}
#tab-set .stab .slotnone{white-space:nowrap}
#tab-set .stab input{width:100%;max-width:96px;box-sizing:border-box;font-size:var(--fs-caption);padding:0 6px}
#tab-set .stab .slotuse{display:flex;align-items:center;gap:8px;min-width:0}
#tab-set .stab .slotbar{display:block;flex:0 1 96px;min-width:0;height:8px;border-radius:4px;background:var(--line);overflow:hidden}   /* #2540: the bar yields before the "N of M" text reaches the cap field */
#tab-set .stab .slotbar i{display:block;height:100%;background:var(--acc)}
#tab-set .stab .slotbar.full i{background:var(--warn)}
#tab-set .stab .slotnow{flex:none;min-width:0;white-space:nowrap;font-variant-numeric:tabular-nums}   /* the bar yields, never the text: a shrunk text box would spill into the cap field */
#tab-set .stab .slothold{line-height:1.6}
#tab-set .stab .slothold .chip{display:inline-block;text-decoration:none;color:var(--acc);white-space:nowrap;margin:0 4px 2px 0}
/* #2540: at a narrow Settings column the Held by cell takes its own row below the first three cells, so the gate line
   wraps in the full table width. The three pinned widths stay, and no label clips its cell. */
@container (max-width:700px){
#tab-set .stab{display:block}
#tab-set .stab thead,#tab-set .stab tbody{display:block}
#tab-set .stab tr{display:grid;grid-template-columns:25% 23% 24%;align-items:center}
#tab-set .stab .scol1,#tab-set .stab .scol2,#tab-set .stab .scol3,#tab-set .stab .scol4{width:auto}   /* the track names the width, so a cell never takes 25% of its own track */
#tab-set .stab thead th.scol4{display:none}
#tab-set .stab tbody th,#tab-set .stab tbody td{border-bottom:0}
#tab-set .stab tbody tr{border-bottom:1px solid var(--line)}
#tab-set .stab tbody td.scol4{grid-column:1/-1;padding-top:2px}
}
/* #2540: under 340 px of column the three cells no longer fit one line: the label takes the row, and the bar and the
   cap field share the line below it, so the cap field never shrinks under its value. */
@container (max-width:340px){
#tab-set .stab tr{grid-template-columns:minmax(0,1fr) auto}
#tab-set .stab thead{display:none}
#tab-set .stab tbody th{grid-column:1/-1}
}
/* Settings polish: under 520 px of column the Kanban table reads as one line per column, the name, the count and the
   limit field, with what the limit stops on the line below, so no name, count or placeholder is cut. */
@container (max-width:520px){
#tab-set .ktab,#tab-set .ktab tbody{display:block}
#tab-set .ktab colgroup,#tab-set .ktab thead{display:none}
#tab-set .ktab tbody tr{display:grid;grid-template-columns:minmax(0,1fr) auto 104px;align-items:center;column-gap:12px;padding:9px 0;border-bottom:1px solid var(--line)}
#tab-set .ktab tbody th,#tab-set .ktab tbody td{padding:0;border-bottom:0}
#tab-set .ktab .kl input{max-width:none}
#tab-set .ktab td.ks{grid-column:1/-1;padding-top:4px;color:var(--mute);font-size:var(--fs-caption)}
#tab-set .ktab .kbar{width:48px}
}
#tab-set .setrow .sl::first-letter{text-transform:uppercase}
.sub .pr input[data-f=amount]{flex:1 1 auto;min-width:0}.sub .pr input[data-f=cur]{flex:none;width:8ch}
#tab-set .kbar{display:inline-block;vertical-align:middle;width:calc(100% - 58px);min-width:24px;max-width:120px;height:8px;border-radius:var(--radius-sm);background:var(--line);overflow:hidden}
#tab-set .kbar .kfill{display:block;height:100%;background:var(--ok)}
#tab-set .kbar .kfill.full{background:var(--warn)}
#tab-set .knum{margin-left:0;font-variant-numeric:tabular-nums;color:var(--fg);white-space:nowrap}
#tab-set .kbar+.knum{margin-left:8px}
#tab-set input,#tab-set select{font-size:var(--fs-body);height:var(--ctl-h)}
#tab-set textarea{font-size:var(--fs-body);min-height:var(--ctl-h)}
#tab-set .ro{color:var(--warn);background:var(--tint-warn);border:1px solid var(--tint-warn-line);border-radius:var(--radius-md);padding:6px 10px;margin:0 0 10px;max-width:1120px}#tab-set .ro[hidden]{display:none}
@media (max-width:600px){#tab-set .now{grid-template-columns:1fr}}
/* Settings > Spend & limits · Tokens (#2510): the spec mockup draws one heading with its state chip at the right, four
   Now tiles in one row with the value larger than its label, fields in one column, and a toggle switch. These rules are
   scoped to `#lbody`, so the other themes keep the shared shape above. */
#tab-set #lbody .tokhead{display:flex;align-items:center;gap:8px;margin:0 0 10px;font-size:var(--fs-heading);font-weight:600;text-transform:none;letter-spacing:0;color:var(--fg)}
#tab-set #lbody .chip{display:inline-block;margin-left:auto;padding:0 7px;border-radius:var(--radius-lg);border:1px solid var(--line);color:var(--mute);font-size:var(--fs-caption);font-weight:600;letter-spacing:0;text-transform:none}
#tab-set #lbody .chip.err{color:var(--err);border-color:var(--err)}
#tab-set #lbody .now{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}
#tab-set #lbody .tile .tv{font-size:var(--fs-heading);font-weight:600;line-height:1.25}
#tab-set #lbody .setrow{grid-template-columns:minmax(120px,1fr) minmax(0,2.4fr);gap:12px}
#tab-set #lbody .setrow .sc{justify-self:stretch;display:flex;align-items:center;gap:8px;flex-wrap:wrap;min-width:0}
#lbody .setrow .lf{flex:0 0 auto;display:flex;align-items:center;gap:4px;min-width:104px}
#lbody .setrow .lf input:not([type=checkbox]){width:104px}
#lbody .setrow .lunit{flex:none;color:var(--mute);font-size:var(--fs-caption)}
#lbody .setrow .kstop{flex:1 1 0;min-width:0;text-align:left}
#lbody .swt{display:inline-flex;align-items:center;gap:8px}
#lbody input[type=checkbox]{-webkit-appearance:none;appearance:none;margin:0;width:52px;height:32px;padding:0;border:1px solid var(--line);border-radius:var(--radius-full);cursor:pointer;flex:none;background-color:var(--edge);background-image:radial-gradient(circle at 16px 50%,var(--highlight) 0 12px,transparent 13px)}
#lbody input[type=checkbox]:checked{background-color:var(--ok);background-image:radial-gradient(circle at calc(100% - 16px) 50%,var(--highlight) 0 12px,transparent 13px)}
#lbody input[type=checkbox]:focus-visible{outline:2px solid var(--acc);outline-offset:2px}
#lbody .chg{margin:6px 0 0;padding-top:6px;border-top:1px solid var(--line)}
#lbody .chg summary{display:flex;align-items:center;gap:6px;min-height:32px;cursor:pointer;font-size:var(--fs-body);color:var(--fg);list-style:none}
#lbody .chg summary::-webkit-details-marker{display:none}
#lbody .chg summary::before{content:"\25B8";color:var(--mute)}
#lbody .chg[open] summary::before{content:"\25BE"}
#lbody .chg summary .m{color:var(--mute)}
#lbody .chg ul{list-style:none;margin:4px 0 0;padding:0;font-size:var(--fs-caption);color:var(--mute)}
#lbody .chg li{padding:3px 0;border-bottom:1px solid var(--line)}
#lbody .chg li:last-child{border-bottom:0}
#lbody .chg .cat{font-variant-numeric:tabular-nums}
#tab-set #lbody .tile{padding:6px 8px}
@media (max-width:1200px){#tab-set #lbody .now{gap:6px;grid-template-columns:repeat(2,minmax(0,1fr))}#tab-set #lbody .setrow{grid-template-columns:minmax(0,1fr)}#tab-set #lbody .tile .tv{font-size:var(--fs-body)}}
@media (max-width:820px){#tab-set #lbody .now{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (max-width:600px){#tab-set #lbody .now{grid-template-columns:1fr}}
.mtab{width:100%;border-collapse:collapse;font-size:var(--fs-caption);margin-bottom:8px}.mtab th{text-align:left;color:var(--mute);font-weight:600;padding:4px 6px;border-bottom:1px solid var(--line)}.mtab td{padding:4px 6px;border-bottom:1px solid var(--line)}.mtab tr.off .mn{text-decoration:line-through;color:var(--mute)}
.stc{display:inline-block;font-size:var(--fs-caption);border:1px solid var(--line);border-radius:var(--radius-sm);padding:0 5px;color:var(--mute)}.stc.off{color:var(--warn);border-color:var(--tint-warn-line)}.stc.lim{color:var(--err);border-color:var(--err)}.mr.off .mn{text-decoration:line-through}.mr.rmk .mn,.mr.rmk .pv{text-decoration:line-through;opacity:.55}
.madd select{flex:1;min-width:0;height:var(--ctl-h);background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:var(--radius-sm);font:inherit;font-size:var(--fs-caption)}#mbody .lead{margin:4px 0 8px}#mbody details.hlp{margin-top:10px}#mbody details.hlp summary{cursor:pointer;color:var(--acc)}
@media (max-width:600px){#tab-set .swrap{display:block}#tab-set .snav{display:none}#tab-set .ssel{display:flex;gap:8px;align-items:center;margin-bottom:8px}header.top{flex-wrap:wrap}.tabs{order:3;flex:1 0 100%;overflow-x:auto}#tab-set .ssel select{min-height:44px;flex:1}.mr .ct{flex-direction:column;align-items:flex-start}.mr label.el:has(.hr){align-self:stretch}.mr .hr{width:auto}.mr .mb button,.mtg,.madd select,.mr .ef,.mr .hr,#sbar button{min-height:44px;min-width:44px}.mtab th:nth-child(2),.mtab td:nth-child(2){display:none}}
#mobile-link{color:var(--acc)}
/* INST-CSS{ */
/* Monitor (#983): calm flight instruments in the sidebar. Three gauges, three annunciator tiles, two rows. No motion
   but the needle's own short move, and none at all under reduced motion. */
#inst .igs{display:flex;justify-content:space-between;gap:4px;margin:2px 0 8px}#inst .ig{display:flex;flex-direction:column;align-items:center;flex:1;min-width:0}
#inst .ig svg{display:block}#inst .ig .gt{fill:none;stroke:var(--line);stroke-width:3;stroke-linecap:round}#inst .ig .gm{stroke:var(--mute);stroke-width:1;stroke-linecap:round}#inst .ig .gf{fill:none;stroke-width:3;stroke-linecap:round}
#inst .ig .gn{stroke:var(--fg);stroke-width:1.5;stroke-linecap:round;transform-origin:30px 32px;transition:transform var(--motion-duration-long) var(--motion-entry-easing)}#inst .ig .gh0{fill:var(--fg)}
#inst .ig.ldred .gf,#inst.warn .ig.ldred .gf,#inst .ig.ldred .gn{stroke:var(--err)}
#inst .gv{font-size:var(--fs-caption);color:var(--mute);font-variant-numeric:tabular-nums;white-space:nowrap}#inst .gv b{color:var(--fg);font-weight:600}#inst.warn .gv b{color:var(--warn)}#inst .ig.ldred .gv b{color:var(--err)}
#inst .ian{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:8px}#inst .ian:empty{display:none}#inst .ian .at:first-child{grid-column:1/-1}#inst .atm{display:block;color:var(--mute);font-variant-numeric:tabular-nums}
#inst .at{background:var(--inset);border:1px solid var(--line);border-radius:var(--radius-md);padding:5px 7px;min-width:0;font-size:var(--fs-caption)}
#inst .at .ah{display:flex;align-items:center;gap:6px;color:var(--mute);font-weight:600}#inst .at .dot{width:7px;height:7px;border-radius:var(--radius-full);flex:none;background:var(--mute)}
#inst .at.ok .dot{background:var(--ok)}#inst .at.err .dot{background:var(--err)}#inst .at.info .dot{background:var(--acc)}
#inst .at .av{display:block;color:var(--fg);font-variant-numeric:tabular-nums;overflow-wrap:anywhere}#inst .at.err .av{color:var(--err)}#inst .at a{color:var(--acc);text-decoration:none}#inst .at a:hover{text-decoration:underline}
#inst .at details>summary{cursor:pointer;color:var(--mute)}#inst .at details>ul{margin:4px 0 0;padding:0 0 0 14px;color:var(--mute);overflow-wrap:anywhere}
#inst .irw{display:flex;justify-content:space-between;align-items:baseline;gap:8px;padding:3px 0;border-top:1px solid var(--line);font-size:var(--fs-caption);color:var(--mute)}
#inst .irw b{color:var(--fg);font-weight:600;font-variant-numeric:tabular-nums;text-align:right;min-width:0;overflow-wrap:anywhere}#inst .irw b.bad{color:var(--warn)}
/* The Monitor's loop switch (#923, #1234): the phone serves this block alone, so the switch is styled here and not only
   in the sidebar block. #1071 moved the switch into the Monitor and left its styles beside the Settings panel, so the
   desktop drew a bare button and the phone drew one with no styles at all. The Monitor carries the only switch (#2515). */
#inst .ls.draining,#inst .ls.starting{color:var(--warn)}#inst .ls{font-weight:600}
#inst .lsw{position:relative;flex:none;width:52px;height:32px;padding:0;border:0;border-radius:var(--radius-xl);background:var(--edge);cursor:pointer;transition:background var(--motion-duration-short) var(--motion-entry-easing)}
#inst .lsw[aria-checked=true]{background:var(--ok)}#inst .lsw.dra[aria-checked=true]{background:var(--warn)}
#inst .lsw i{position:absolute;top:3px;left:3px;width:26px;height:26px;border-radius:var(--radius-full);background:var(--highlight);transition:transform var(--motion-duration-short) var(--motion-entry-easing)}
#inst .lsw[aria-checked=true] i{transform:translateX(20px)}
#inst .lsw:disabled{opacity:.6;cursor:default}#inst .lsw:focus-visible{outline:2px solid var(--acc);outline-offset:2px}
#inst .ls.on{color:var(--ok)}#inst .ls.off{color:var(--warn)}
/* The Monitor's row is a flight instrument, not a settings block: no inset box, a tight gap, and the note under the row.
   The row closes the card (owner): a hairline separates the switch from the instruments above it. */
#inst .lnow{display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:8px 0 0;margin:8px 0 0;border:0;border-top:1px solid var(--line);border-radius:0;background:none}
#inst .lnow small{display:block;color:var(--mute)}#inst .lnow button{margin-left:auto}
#inst .lerr{color:var(--err);font-size:var(--fs-caption);margin:4px 0 0}#inst .lwhy{color:var(--warn);font-size:var(--fs-caption);margin:4px 0 0}
/* The Throughput panel (#2662): these rules sat outside this block while the phone imports this block alone, so the
   phone drew the card's markup as bare text and its median and maximum ran together. They name the card, where the
   panel sits, because every rule of this block does. One block, one set of rules. */
#inst #ispeed .spd{border:1px solid var(--line);border-radius:var(--radius-lg);padding:8px 10px;margin-bottom:8px;background:var(--card)}#inst #ispeed .spdh{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:6px}#inst #ispeed .spdt{font-size:var(--fs-caption);font-weight:600}#inst #ispeed .spdt.up{color:var(--ok)}#inst #ispeed .spdt.down{color:var(--err)}#inst #ispeed .spdt.flat{color:var(--mute)}#inst #ispeed .spdt.wait{color:var(--mute)}#inst #ispeed .spr{position:relative;height:6px;border-radius:var(--radius-lg);background:var(--raise);margin-top:6px}#inst #ispeed .spr i{position:absolute;top:-3px;bottom:-3px;width:2px;background:var(--fg)}#inst #ispeed .spr .spt{left:0;top:0;bottom:0;width:0;border-radius:var(--radius-lg);background:var(--mute)}#inst #ispeed .spr .spt.up{background:var(--ok)}#inst #ispeed .spr .spt.down{background:var(--err)}#inst #ispeed .spl{display:flex;justify-content:space-between;font-size:var(--fs-caption);color:var(--mute);margin-top:3px}#inst #ispeed .spn{font-size:var(--fs-caption);color:var(--mute);margin-top:4px}#inst #ispeed .spn b{color:var(--fg);font-size:var(--fs-body)}#inst #ispeed .spw{display:grid;grid-template-columns:minmax(0,auto) minmax(0,1fr);gap:2px 8px;align-items:baseline;margin-top:6px;font-size:var(--fs-caption);min-width:0}#inst #ispeed .spwl{font-weight:600;color:var(--mute)}#inst #ispeed .spwv{color:var(--fg);text-align:right;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}#inst #ispeed .spw.ok .spwl{color:var(--ok)}#inst #ispeed .spw.warn .spwl{color:var(--warn)}#inst #ispeed .spw.err .spwl{color:var(--err)}#inst #ispeed .spwt{grid-column:1/-1;display:block;height:4px;border-radius:var(--radius-lg);background:var(--raise);overflow:hidden}#inst #ispeed .spwt i{display:block;height:100%;background:var(--ok)}#inst #ispeed .spw.warn .spwt i{background:var(--warn)}#inst #ispeed .spw.err .spwt i{background:var(--err)}#inst #ispeed .spw.mute .spwt i{background:var(--mute)}#inst #ispeed .spw.spec .spwl{color:var(--acc)}#inst #ispeed .spw.spec .spwt i{background:var(--acc)}#inst #ispeed .spws{grid-column:1/-1;font-size:calc(var(--fs-caption) - 2px);color:var(--mute)}#inst #ispeed .spage{font-size:var(--fs-caption);color:var(--warn);margin-top:6px}
/* }INST-CSS */
/* SIDEBAR-CSS{ */
/* The Monitor bar folds away (#1159): one chevron at its leading edge slides the whole bar left on close and back right
   on open, so the board takes the freed width. A shut bar leaves a 28 px rail at the left edge. The chevron is a child
   of the bar, so it rides the leading edge and never crosses the board. */
#sbtn{display:none}
@media (min-width:601px){
#side{position:relative;padding:0;overflow:hidden;display:flex;flex-direction:column;box-sizing:border-box;transition:width var(--motion-duration-enter) var(--motion-entry-easing)}
#sbbody{flex:1 1 auto;min-height:0;width:100%;overflow:auto;padding:34px 12px 12px;box-sizing:border-box;transition:transform var(--motion-duration-enter) var(--motion-entry-easing),visibility 0s}
#sbtn{display:flex;position:absolute;top:12px;right:4px;z-index:1;width:20px;height:32px;align-items:center;justify-content:center;padding:0;border:0;border-radius:0;background:none;color:var(--mute);cursor:pointer}
#sbtn:hover{color:var(--fg)}#sbtn:focus-visible{outline:var(--focus-w) solid var(--focus);outline-offset:2px}
#sbtn svg{width:12px;height:12px}
html.sbshut #side{width:28px;transition-timing-function:var(--motion-exit-easing)}
html.sbshut #sbbody{transform:translateX(-100%);visibility:hidden;transition-timing-function:var(--motion-exit-easing);transition-delay:0s,var(--motion-duration-enter)}   /* the body of a shut bar is wider than the 28 px rail: hidden once it slid away, so no clipped text shows in the rail and no hidden link takes focus (#3036) */
html.sbshut #sbtn svg{transform:scaleX(-1)}
}
/* }SIDEBAR-CSS */
</style>
<div id=who>__WHO__</div>
<script>// One read per URL at a time (#917): a timer fires on a fixed beat whether or not its last read came back, and a slow route
// (models, stats) under load then filled the browser's six connections, so no read finished and the page drew nothing.
// A read already in flight is shared: the caller gets a clone of the same answer.
(()=>{const f=window.fetch.bind(window),live=new Map();window.fetch=(u,o)=>{if(o&&o.method&&o.method!=="GET"||o&&o.signal)return f(u,o);
 const k=String(u);if(!live.has(k))live.set(k,f(u,o).finally(()=>live.delete(k)));return live.get(k).then(r=>r.clone())}})();</script>
<header class=top><h1><button id=pj aria-haspopup=menu aria-expanded=false><span id=pjn></span> &#9662;</button></h1><div id=psw aria-label="Projects"></div><div class=tabs role=tablist><button data-t=board role=tab>Kanban</button><button data-t=runs role=tab>Runs</button><button data-t=flow role=tab>Flow</button><button data-t=prof role=tab>Loop</button><button data-t=set role=tab>Settings</button></div><span class=spx></span>
<span class=pill id=bread></span></header>
<script id=sideRule>function sideState(saved,wide){return saved==="shut"&&!!wide}function sideNext(saved,wide){return sideState(saved,wide)?"open":"shut"}</script>
<div id=win>
<script id=sbboot>try{if(sideState(JSON.parse(localStorage.hub||"{}").side,matchMedia("(min-width: 601px)").matches))document.documentElement.classList.add("sbshut")}catch(e){}</script>
<aside id=side><button id=sbtn type=button aria-label="Collapse the Monitor bar" aria-controls=side aria-expanded=true title="Collapse the Monitor bar"><svg viewBox="0 0 12 12" aria-hidden=true focusable=false><path d="M8 2L4 6l4 4" fill=none stroke=currentColor stroke-width=1.6 stroke-linecap=round stroke-linejoin=round /></svg></button><div id=sbbody>
<details id=inst class=sp open><summary>Monitor<span class=sr id=instsr></span></summary><div class=sb id=ibody><div class=igs id=igs title="machine load: read in the hub process every few seconds; ? means the hub cannot see that source"></div><div id=ispeed></div><div class=ian id=ian></div><div id=iloop></div></div></details>
<details id=usage class=sp><summary>Usage</summary><div class=sb><div class="igs isub" id=isub title="subscriptions: the used share of the weekly window and the days to its reset"></div></div></details><details id=stats class=sp><summary>Spend<span class=sr id=stsr></span></summary><div class=sb><h4>Tokens over time <span class=row id=per></span></h4><div id=chart></div><h4>Model tokens · this month</h4><div class=m id=piet>in + out</div><div id=pie></div></div></details>
<details id=lrd class=sp open><summary>Live work<span class=sr id=lrs></span></summary><div class=sb id=lrun></div></details>
</div></aside>
<main id=content>
<section id=tab-board class="pane on" aria-label=Board>
<div id=board><div id=bhead><button id=lcall title="collapse every swimlane">Collapse all</button> <button id=lexp title="expand every swimlane">Expand all</button> <label class=hck title="hide the swimlanes whose every ticket is closed"><input type=checkbox id=lhd> Hide done lanes</label> <span id=lhc></span><span id=sse class=m hidden></span><span id=bt class=m></span><span id=bsync class=m></span> <span id=hlrel class=hlrel hidden></span></div><div id=bcases></div><div id=bbody></div></div>
</section>
<section id=tab-runs class=pane aria-label=Runs><div id=main></div></section>
<section id=tab-flow class=pane aria-label="Flow charts">
<div class=rkt id=flowtabs><button id=bflow title="the development flow: the cards in each column over time, from the flow history (SPEC 111)">Cumulative Flow</button><button id=blead title="the lead time of the closed cards, opened to closed (SPEC 111)">Lead Time</button></div>
<div id=flowov hidden><div id=flowb></div></div>
</section>
<section id=tab-prof class=pane aria-label=Loop><div id=profb></div><div id=lopaud></div></section>
<dialog id=ldg aria-label="Stop the loop"><form method=dialog id=ldgb></form></dialog>
<section id=tab-set class=pane aria-label=Settings><h2>Settings</h2>
<p class=ro id=ro hidden></p>
<div class=swrap><nav class=snav aria-label="Settings themes"><button data-sp=lim>Spend &amp; limits<small id=limnav></small></button><button data-sp=sub>Subscriptions<small id=subnav>no plan chosen</small></button><button data-sp=mdl>Models &amp; chains<small id=mdlnav>no models</small></button><button data-sp=slk>Slack<small id=slknav>not connected</small></button><button data-sp=prs>Personas<small id=prsnav>&#8212;</small></button><button data-sp=acc>Access<small id=accnav>&#8212;</small></button></nav>
<div class=spanels><label class=ssel>Theme <select id=ssel aria-label="Settings theme"><option value=lim>Spend &amp; limits</option><option value=sub>Subscriptions</option><option value=mdl>Models &amp; chains</option><option value=slk>Slack</option><option value=prs>Personas</option><option value=acc>Access</option></select></label>
<details id=lim class=sp data-sp=lim open><summary>Spend &amp; limits<span class=sr id=limsr></span></summary><div class=sb id=lbody></div></details>
<details id=sub class=sp data-sp=sub><summary>Subscriptions<span class=sr id=subsr>no plan chosen</span></summary><div class=sb><div id=subnow></div><div id=subs></div></div></details>
<details id=mdl class=sp data-sp=mdl><summary>Models &amp; chains<span class=sr id=mdlsr>no models</span></summary><div class=sb id=mbody></div></details>
<details id=slk class=sp data-sp=slk><summary>Slack<span class=sr id=slksr>not connected</span></summary><div class=sb id=sbody></div></details>
<details id=prs class=sp data-sp=prs><summary>Personas<span class=sr id=prssr>&#8212;</span></summary><div class=sb id=prbody></div></details>
<details id=acc class=sp data-sp=acc><summary>Access<span class=sr id=accsr>&#8212;</span></summary><div class=sb><div id=accb></div><div class=sdev><h4>This device</h4><a id=mobile-link href="#">mobile view</a><p class=note>The phone check runs <code>/agent-squad:start-mobile</code> on this machine.</p></div><script>document.getElementById("mobile-link").href=location.pathname.replace(/\/?$/,"/")+"mobile";document.getElementById("mobile-link").onclick=()=>localStorage.removeItem("squad-view")</script></div></details>
</div></div>
<div class=sbar id=sbar><button id=ssave>Save…</button><button id=sdisc>Discard changes</button><span id=schg>No change</span><button id=sundo hidden>Undo</button><button id=sretry hidden>Retry</button></div>
<dialog id=sdlg aria-label="Confirm the settings change"><b>Save these settings?</b><pre id=sdpre></pre><p class=m>Written to the project files. Runs already started keep the chains they recorded.</p><div class=sact><button id=sdok>Confirm and save</button><button id=sdno>Back</button></div></dialog>
</section>
</main>
</div>
<div id=tk role=dialog aria-label=Ticket><header><span id=tkchips></span><span class=sp></span><span id=tknav></span><button id=tkx aria-label=Close>✕</button></header><div id=tkset></div><div id=tkb></div><footer><a class=btn id=tkgh target=_blank rel=noopener>Open on GitHub ↗</a><button id=tktodo>→ Todo</button><button id=tksync title="re-read this card from GitHub now: the sheet and the row behind it draw from the same answer">Sync</button><button id=tkrel>Highlight related</button><span id=tksyncnote class=m></span></footer></div>
<div id=rk role=dialog aria-label=Run><header><span id=rkchips></span><span class=sp></span><button id=rkx aria-label=Close>✕</button></header><div id=rkb></div><footer><a class=btn id=rkgh target=_blank rel=noopener>Open full log ↗</a><button id=rkpause>Pause run</button></footer></div>
<script>/*SWITCHER_JS*/
{const swMe=(location.pathname.match(/^\/([^/?#]+)/)||[])[1]||"";switcherPoll(d=>switcherPaint(document.getElementById("psw"),d,swMe,"/"));}</script>
<script>
{const p=(location.pathname.match(/^\/([^/?#]+)/)||[])[1]||"";document.title=p?`Hub | ${decodeURIComponent(p)}`:"Hub";}   // the tab names the profile it shows, so two open hubs are told apart
const $=s=>document.querySelector(s),ls=(()=>{try{return JSON.parse(localStorage.hub||"{}")||{}}catch(e){return {}}})();   // a page that cannot reach the device's storage still draws: the read is never the page's death
const HUB_REPO=/*HUB_REPO*/;
let SPECLC=new Map();   // issue number -> its spec colour, filled by the board read so the live-runs card can wear it
let allRuns=[],runs=[],q=ls.q||"",per=ls.per||"day",src=ls.src||"both",N=1,pins=new Set(),slots=[],hl=null,boardRows=[],ENDED=[];   // ENDED: the rows of /api/ended (#984)
const save=()=>{try{localStorage.hub=JSON.stringify({...ls,pins:[...pins],q,per,src})}catch(e){}};
// SIDEBAR{
const sideBtn=$("#sbtn"),sideBody=$("#sbbody"),sideWide=matchMedia("(min-width: 601px)");
function sidePaint(shut){const name=shut?"Open the Monitor bar":"Collapse the Monitor bar";document.documentElement.classList.toggle("sbshut",shut);sideBtn.setAttribute("aria-expanded",String(!shut));sideBtn.setAttribute("aria-label",name);sideBtn.title=name;sideBody.inert=shut}
function sideShow(saved){sidePaint(sideState(saved,sideWide.matches))}
sideBtn.addEventListener("click",()=>{ls.side=sideNext(ls.side,sideWide.matches);save();sideShow(ls.side)});
sideWide.addEventListener("change",()=>sideShow(ls.side));
sideShow(ls.side);
// }SIDEBAR
const usd=v=>v==null?"—":v>=100?"$"+Math.round(v):"$"+v.toFixed(2);
const age=s=>s<90?s+"s":s<5400?Math.round(s/60)+"m":Math.round(s/3600)+"h";
// a run's name says what it is for: "fix PR #1202 · #1178", "implement #1179, #1182", "dispatch #31, #25"; the hub computes
// that string (hub.run_label) and the run id moves to the detail line -- the page renders the value, it holds no rule of its own
const label=r=>r.label||("run "+String(r.id||"").slice(-6));
// where the label's issue came from, or why the run has none: the request names it; the transcript is only the fallback when the request is missing
const labelTip=r=>{if((r.issues||[]).length)return r.issue_src==="transcript"?"issue read from the transcript: request.txt is missing":"issue named by the run's request";
  return r.issue_why==="request-missing"?"no issue: the request is missing (no request.txt in the run folder)":r.issue_why==="request-no-issue"?"no issue: the request names no issue":""};
const sub=r=>{const t=r.titles||{},ks=(r.issues||[]).filter(i=>+i!==r.fix&&t[i]);return ks.length===1?t[ks[0]]:ks.length?ks.map(i=>"#"+i+" "+t[i].split(" ").slice(0,4).join(" ")).join(" · "):""};
function plan(){ // which runs fill the N slots: pinned first, then most recently updated; slots stay stable, top slot has hysteresis
  const byId=new Map(runs.map(r=>[r.id,r])),want=[];
  runs.filter(r=>pins.has(r.id)).forEach(r=>want.push(r.id));
  const open=runs.filter(r=>!pins.has(r.id)&&r.rank===0);
  // live runs auto-fill; with none running, fall back to the most recently finished (landed) ones
  (open.length?open:runs.filter(r=>!pins.has(r.id)&&r.rank===1)).forEach(r=>want.push(r.id));
  const top=want.slice(0,N);
  slots=slots.filter(id=>byId.has(id));
  // keep placed runs that are still wanted; for a lone unpinned current top, only follow a newer run after 30s of lead
  const keep=slots.filter(id=>top.includes(id));
  if(N===1&&slots[0]&&!pins.size&&byId.has(slots[0])&&top[0]!==slots[0]&&byId.get(slots[0]).age-byId.get(top[0]).age<30)return;
  const next=[];for(let i=0;i<N;i++){const cur=slots[i];next[i]=(cur&&top.includes(cur))?cur:null}
  top.forEach(id=>{if(!next.includes(id)){const j=next.indexOf(null);if(j>=0)next[j]=id}});
  slots=next.filter(Boolean);
}
let GROUPS=[];   // the Live group, exactly as /api/runs answers it: key, label, text, title and empty (#1277)
const LOAD_MS=Number("__LOADMS__")||5000;   // the sampler's cadence, substituted when the page is served; the fallback keeps a slice of this script evaluable on its own
function loadMark(r){return r.cpu?`<span class=load title="this run owns ${r.cpu}% of this machine's CPU right now">cpu ${r.cpu}%</span>`:""}   // the card answers "because of whom?"; the strip answers "is it saturated?"
// The strip's cells (#1816). One line of text could not carry a per-attribute colour, so the page builds the three cells
// from the numbers `/api/load` already serves and paints each on its own. These mirror the server's `pct_cell` — the same
// order, the same ` · `, the same rounding — so the reading still reads exactly as it read; only a cell at the machine's
// ceiling is red, and that is `LOAD_ERR`, substituted when the page is served. A source the hub cannot see stays `?`:
// a missing reading is not a high one. Red sits above the older, machine-wide amber of `#inst.warn`, and never replaces it.
const LOAD_ERR=Number("__LOADERR__");
const RO=("__READONLY__"==="true"),RO_WHY="__ROWHY__";   // #1278: a tailnet reader sees every control disabled and why
function ldNum(v){if(v===null||v===undefined||v===""||typeof v==="boolean")return null;const f=Number(v);return(f!==f||f<0)?null:Math.min(f,100)}
function ldRound(f){const fl=Math.floor(f),fr=f-fl;return fr>.5?fl+1:fr<.5?fl:fl+(fl%2)}   // half to even, exactly as `:.0f` rounds
function ldCell(v){const f=ldNum(v);return{t:f===null?"?":ldRound(f)+"%",red:f!==null&&f>=LOAD_ERR}}
function ldCells(d){return[["CPU","cpu"],["RAM","ram"],["GPU","gpu"]].map(p=>{const c=ldCell((d||{})[p[1]]);return{k:p[0],t:c.t,red:c.red}})}
// Monitor (#983): the card the Settings strip became, drawn as calm flight instruments from what `/api/load` already
// serves: the three load numbers and the facts the sampler last read. Each figure is HTML text; the SVGs only draw and are
// hidden from a screen reader. A tile's state is read off its fact, so the hub keeps deciding the tone and the text.
// INST{
function gaugeGeom(v){const f=ldNum(v),k=f!==null,p=k?f:0,a=Math.PI*p/100;return{known:k,deg:Math.round(-90+1.8*p),x:Math.round((30-26*Math.cos(a))*10)/10,y:Math.round((32-26*Math.sin(a))*10)/10}}   // the shipped scale: a half circle swept 0 to 100, never re-scaled (#1141)
const gaugeMark=p=>{const a=Math.PI*p/100;return [30-23*Math.cos(a),32-23*Math.sin(a),30-18*Math.cos(a),32-18*Math.sin(a)].map(n=>Math.round(n*10)/10)};   // a mark sits inside the arc, so it never covers the sweep's tone (#1141)
function gaugeSvg(v,id){const g=gaugeGeom(v),gid=`g-${id||"load"}`;   // one grid: the track is line, the sweep runs ok through warn to err, the needle is fg (SPEC 1123 #1141)
 return `<svg width=60 height=36 viewBox="0 0 60 36" aria-hidden="true" focusable="false"><defs><linearGradient id=${gid} x1=4 y1=0 x2=56 y2=0 gradientUnits=userSpaceOnUse><stop offset=0 stop-color="var(--ok)"/><stop offset=.5 stop-color="var(--warn)"/><stop offset=1 stop-color="var(--err)"/></linearGradient></defs><path class=gt d="M4 32 A26 26 0 0 1 56 32"/>${[0,25,50,75,100].map(p=>{const [x1,y1,x2,y2]=gaugeMark(p);return `<line class=gm x1=${x1} y1=${y1} x2=${x2} y2=${y2} />`}).join("")}<path class=gf stroke="${g.known?`url(#${gid})`:"none"}" d="M4 32 A26 26 0 0 1 ${g.x} ${g.y}"/><line class=gn x1=30 y1=32 x2=30 y2=10 style="transform:rotate(${g.deg}deg)"${g.known?"":" visibility=hidden"}/><circle class=gh0 cx=30 cy=32 r=2 /></svg>`}
function subsGauges(d){const xs=(d||{}).subs||[];if(!xs.length)return"";
 // One bar per plan: how much of the weekly window is spent, and how long until it resets. The needle and the half-circle
 // are gone with the #inst/#isub scope bug that painted them black (#Astra, 2026-11-01): a bar reads at a glance where a
 // dial needed a caption, and four of them stack at 380 px. The bar and its text are shared by the sidebar card and the
 // Subscriptions theme, which draws the same row from the same answer (#1281).
 const reset=s=>{if(!s.resets)return esc(s.why||"no reading");const d=(s.resets-Date.now()/1000)/86400;
   return d<=0?"resets now":d>=1?`resets in ${d.toFixed(1)} d`:`resets in ${Math.max(1,Math.round(d*24))} h`};
 return xs.map(s=>{const p=typeof s.pct==="number"?s.pct:null,cls=p===null?" na":p>=90?" red":p>=75?" warn":"";
   return `<div class="srow${cls}"${s.window?` title="${iq("share of the "+s.window+" window")}"`:""}>`+
    `<span class=sn>${esc(s.label)}</span><b class=sp2>${p===null?"?":fpct(p)+"%"}</b>`+
    `<span class=sb2><i style="width:${p===null?0:Math.max(0,Math.min(100,p))}%"></i></span>`+
    `<small class=sr2>${esc(reset(s))}</small></div>`}).join("")}
function instGauges(d){return ldCells(d).map(c=>`<div class="ig${c.red?" ldred":""}">${gaugeSvg((d||{})[c.k.toLowerCase()],c.k)}<span class=gv><b>${c.k}</b> ${esc(c.t)}</span></div>`).join("")}
const instFact=(facts,k)=>(facts||[]).find(f=>f&&f.key===k);
function instTile(k,f,lead){if(!f)return{k,text:"no reading",tone:"neutral"};const p=(f.parts||[])[0]||{},t=String(f.text||"").replace(lead,"").trim()||"?";return{k,text:t,tone:p.tone==="ok"||p.tone==="err"?p.tone:"neutral",tip:f.tip||null,detail:f.detail||null,times:p.times||null}}
function instTiles(facts){return[]}   // the Monitor draws no tile: the owner removed the Gate tile (#1260); the gate fact stays in the footer
const iq=t=>esc(t).replace(/"/g,"&quot;");
function tileHtml(t){const v=t.href?esc(t.text).replace(/PR #\d+/,x=>`<a target=_blank rel=noopener href="${iq(t.href)}">${x}</a>`):esc(t.text),
 d=(t.detail||[]).length?`<details><summary>${t.detail.length} failed</summary><ul>${t.detail.map(x=>`<li>${esc(x)}</li>`).join("")}</ul></details>`:"";
 return `<div class="at ${t.tone}"${t.tip?` title="${iq(t.tip)}"`:""}><span class=ah><span class=dot aria-hidden="true"></span>${esc(t.k)}</span><span class=av>${v}</span>${t.times?`<span class=atm>${esc(t.times)}</span>`:""}${d}</div>`}
function instSum(facts){const n=instTiles(facts).filter(x=>x.tone==="err").length;return n?n+" fail":""}   // no tile, so no count; a red gate stays in the footer (#1260)
function speedRow(sp,specs){const spc=specs||(sp||{}).specs||{};
 // A row with nothing to count is not drawn: no label, no text, no bar (#2285). The hero line and its bar always are.
 const spec=(()=>{if(!(spc.open>0))return "";
  const v=esc(spc.value);
  const pb=spc.bar?`<span class=spwt><i style="width:${100*Math.max(0,Math.min(1,Number(spc.frac)||0))}%"></i></span>`:"";
  const tip=spc.tip?esc(spc.tip).replace(/"/g,"&quot;"):"";
  return `<div class="spw spec" role=img aria-label="Specs: ${v}"${tip?` title="${tip}"`:""}><span class=spwl>Specs</span><span class=spwv>${v}</span>`+pb+`</div>`})();
 if(!sp){return `<div class=spd><div class=spdh><b>Throughput</b></div><div class=spn>no history yet</div>`+spec+`</div>`}
 const hi=Math.max(sp.max||0,sp.window||0,1),at=v=>Math.max(0,Math.min(100,100*(v||0)/hi)),hrs=+(sp.window_secs/3600).toFixed(2);
 const tag=sp.trend==="up"?["\u25B2","accelerating","up"]:sp.trend==="down"?["\u25BC","slowing","down"]:sp.trend==="flat"?["\u25AC","steady","flat"]:["\u25CB","first day","wait"];
 const none=!sp.day,med=sp.median==null?"unknown":sp.median;
 const bar=none?"":`<div class=spr role=img aria-label="cards closed in the last ${hrs} h: ${sp.window}, median ${med}, maximum ${sp.max}">`
  +(sp.median==null?"":`<i class=spmed style="left:${at(sp.median)}%" title="median ${sp.median}"></i>`)+`<i class="spt ${tag[2]}" style="width:${at(sp.window)}%"></i></div>`
  +`<div class=spl><span>median ${med}</span><span>max ${sp.max}</span></div>`;
 const hero=none?`<div class=spn>no history yet</div>`:`<div class=spn><b>${sp.window}</b> cards closed in the last ${hrs} h</div>`;
 const row=(cls,name,r)=>{r=r||{};const of=r.total;
  if(!of)return "";   // nothing to count, so nothing is drawn (#2285)
  const v=esc(r.value),w=Math.max(0,Math.min(100,100*r.frac));
  return `<div class="spw ${cls}" role=img aria-label="${name}: ${v}"><span class=spwl>${name}</span><span class=spwv>${v}</span><span class=spwt><i style="width:${w}%"></i></span></div>`};
 const arr=sp.arrivals||{}, bd=sp.board||{}, hst=sp.history||{};   // each age names its read (#1661): the board item read, the history read, the labels off the item read
 const stale=bd.stale?`<div class=spage>\u00b7 board items read ${Math.floor(bd.age_secs/60)} m ago${bd.overdue?" (overdue)":""}</div>`:"";
 const hist=hst.stale?`<div class=spage>\u00b7 history read ${Math.floor(hst.age_secs/60)} m ago</div>`:"";
 const lab=sp.labels||{}, lnote=lab.stale?`<div class=spage>\u00b7 labels from the board read ${Math.floor(lab.age_secs/60)} min old</div>`:"";
 return `<div class=spd><div class=spdh><b>Throughput</b><span class="spdt ${tag[2]}">${tag[0]} ${tag[1]}</span></div>`+hero+bar
  +row("ok","Tasks",sp.tasks)+row("warn","Bugs",arr.bugs)+row("err","Loop-bugs",arr.loop_bugs)+spec+stale+hist+lnote+`</div>`}
function instUsage(facts){const f=instFact(facts,"burn");if(!f||!f.text)return null;const p=String(f.text).split(" · ");return{tok:p[0].replace(/^tokens\s*/,""),calls:p.slice(1).join(" · ")}}
let instLast={};
function instSet(id,h){const e=$("#"+id);if(e&&instLast[id]!==h){instLast[id]=h;e.innerHTML=h}}
function instDraw(d){const c=$("#inst");if(!c)return;const facts=(d||{}).facts||[];c.classList.toggle("warn",!!(d||{}).warn);
 const gs=$("#igs");if(gs&&!gs.children.length)gs.innerHTML=instGauges(d);
 else if(gs)ldCells(d).forEach((x,i)=>{const n=gs.children[i],g=gaugeGeom((d||{})[x.k.toLowerCase()]);if(!n)return;n.classList.toggle("ldred",x.red);
  const ln=n.querySelector(".gn"),f=n.querySelector(".gf"),v=n.querySelector(".gv");
  if(ln){ln.style.transform=`rotate(${g.deg}deg)`;ln.setAttribute("visibility",g.known?"visible":"hidden")}if(f){f.setAttribute("d",`M4 32 A26 26 0 0 1 ${g.x} ${g.y}`);f.setAttribute("stroke",g.known?`url(#g-${x.k})`:"none")}if(v)v.innerHTML=`<b>${x.k}</b> ${esc(x.t)}`});
 instSet("ian",instTiles(facts).map(tileHtml).join(""));instSet("isub",subsGauges(d||{}));instSet("ispeed",speedRow((d||{}).speed,(d||{}).specs));
 const u=instUsage(facts);instSet("burn",u?`<span>Tokens · 24 h</span><b class=bad>${esc(u.tok)}</b>`:"<span>Tokens · 24 h</span><b>no reading</b>");
 const s=$("#instsr");if(s)s.textContent=instSum(facts);
}
// }INST
async function loadPoll(){try{const d=await (await fetch("/api/load")).json();instDraw(d)}catch(e){}}
// runs of one issue share the issue label ("#1281"), and today three runs can carry it at once: the list and the panel header
// then read the same twice. Disambiguate only the labels that repeat, with the run's start time (run-YYYYMMDD-HHMMSS-xxxx).
const short=r=>{const m=/(\d{8})-(\d{2})(\d{2})\d{2}/.exec(r.id||"");return m?m[2]+":"+m[3]:""};   // run-…-HHMMSS-xxxx and test-<n>-YYYYMMDD-HHMMSS both carry it
const name=r=>{const l=label(r);return runs.filter(x=>label(x)===l).length>1&&short(r)?`${l} · ${short(r)}`:l};
// The Monitor's Live work card (#1276, #2507): a bounded glance of the runs live now and the work held by hand. The header
// is two pills -- the runs live now and the cards held by hand -- and never a cost. Each row is one short line: an icon, the
// issue id or the item's name, its chips and its age. A hold row is the issue as a link to its issue, with the five delivery
// chips (gate, dev, stg, main, publish) in order. A suite row wears the microscope, its name, the slots it holds and its age.
// A stopped driver is one red row. The pid, a script path, a driver name, a holder and a full title live only in the row's
// tooltip. The footer is the board read's age. A run row keeps its `data-id`, so the card's own handler opens the run sheet.
const LIVE_ROWS=4;
const LIVE_QUIET=300;   // a running run that has not written for five minutes reads `quiet`, never a fresh age
function liveCard(live,shown){const IC={run:"🏃",hand:"✋",microscope:"🔬",eye:"<span class=haleye></span>"},MK={done:"✓",current:"⏳",later:""};   // the icons a row wears (#2507): a loop run, a hand hold, a suite or gate the machine runs, the HAL9k hold's red eye (#2572); and a delivery stage: done, in hand, still ahead
 const runs=live.filter(r=>r.kind!=="hold"),n=runs.length,rows=(liveWork&&liveWork.lines)||[];
 const holds=[...new Map([...live.filter(r=>r.kind==="hold"),...rows.filter(t=>t.kind==="hold")].map(x=>[x.id,x.icon])).values()];   // a hold is work by hand, never a live run (#2168); the run list and the rows name one hold by one id (#2523)
 const nEye=holds.filter(i=>i==="eye").length,nHand=holds.length-nEye;   // each carrier's own count, off the icon the server sent (#2572)
 const pill=(g,c,t)=>c?`<span class=lpill title="${t}">${g} <b>${c}</b></span>`:"";   // a count of zero hides its pill (#2572)
 const head=`<span class=lpill title="the runs live now">🏃 <b>${n}</b></span>`+pill(IC.eye,nEye,"the HAL9k holds")+pill("✋",nHand,"the Manual holds");   // the summary's heading row carries the counts, once, never the cost (#2507, #2523)
 const at=a=>a==null?"":`<span class=lage>${esc(age(a))}</span>`,chips=c=>`<span class=lchips>${c}</span>`;
 const item=t=>{const ic=IC[t.icon]||"",tt=t.title?` title="${iq(t.title)}"`:"";
   if(t.kind==="hold"){const ch=(t.stages||[]).map(s=>`<span class="lch ${esc(s.state)}" title="${iq(s.name+": "+s.state)}">${esc(s.name)} ${MK[s.state]||""}</span>`).join("");   // the five delivery chips, in the order the hub names them
     const id=t.issue!=null?(t.repo?`<a class=lid href="https://github.com/${esc(t.repo)}/issues/${t.issue}" target=_blank rel=noopener>#${esc(t.issue)}</a>`:`<span class=lid>#${esc(t.issue)}</span>`):`<span class=lid>${esc(t.text)}</span>`;
     return `<div class=lr${tt}><span class=lic>${ic||"✋"}</span><span class=lm>${id}${ch?chips(ch):""}</span>${at(t.age)}</div>`}   // no chip row for an empty stage list (#2572); the holder and the full title live only in the tooltip (#2507)
   const id=`<span class=lid>${esc(t.text)}</span>`,red=t.red?" red":"";
   if(t.kind==="gate"){const c=t.slots==null?"":`<span class=lslot title="the gate slots this suite holds">${t.slots===1?"1 slot":`${t.slots} slots`}</span>`;   // the suite's name, its slot count and its age (#2507)
     return `<div class="lr${red}"${tt}><span class=lic>${ic||"🔬"}</span><span class=lm>${id}${chips(c)}</span>${at(t.age)}</div>`}
   if(t.kind==="stopped")return `<div class="lr${red} stopped"${tt}><span class=lic>${ic||"🏃"}</span><span class=lm>${id}</span>${at(t.age)}</div>`;   // a lane whose record names a dead process is one red row (#2507)
   if(t.kind==="run"||t.kind==="hand")return `<div class=lr${tt}><span class=lic>${ic}</span><span class=lm>${id}</span>${at(t.age)}</div>`;
   return `<div class=m${tt}>${esc(t.text)}</div>`};   // the loop line, the hub's own empty state and a failed read
 const drawn=runs.slice().sort((a,b)=>b.age-a.age).slice(0,LIVE_ROWS);   // a hand hold is its own row below, never also a run row (#1713)
 const own=r=>rows.filter(t=>t.kind==="gate"&&t.run===r.id).map(item).join("");   // a gate is drawn beside the row of the run it checks (#1713): by the run id its row carries, never a substring of its text (#1697)
 const extra=rows.filter(t=>!(t.kind==="run"&&live.some(r=>r.id===t.id))&&!(t.kind==="gate"&&drawn.some(r=>r.id===t.run))).map(item).join("");   // every other row is drawn once: a run row already drawn is not drawn twice, by identity (#1697)
 const board=(liveWork&&liveWork.boardAge!=null)?`<div class=lf>board read ${age(liveWork.boardAge)} ago</div>`:"";   // the footer: the age of the board read the hand-work rows came from (#1697)
 const body=drawn.map(r=>liveRow(r,shown)+own(r)).join("")+extra;   // the page's own run rows, each with its gate, and the hub's other rows, each drawn once
 return{head,html:(body||(liveWork?"":"<div class=m>no live work</div>"))+board}}   // the empty state is the hub's own tagged row, never the page's: it names one only before the first read (#1697)
// The liveness slot answers one question: is this run moving? `quiet` is a run that stopped writing, `no record` a run whose
// record cannot be read, `starting` a run that has not written yet; otherwise the number is how long since its last write.
// The dot mirrors it. A run that is not live (a verdict, needs-human, waiting) is never in this card: it reads in the Runs tab.
function liveSlot(r){
 if(r.state==="unreadable")return{k:"wait",text:"no record"};   // an unreadable record is not a fresh one: it says so, never "0s ago"
 if(r.state==="starting")return{k:"wait",text:"starting"};
 if(r.age>=LIVE_QUIET)return{k:"quiet",text:"quiet "+Math.round(r.age/60)+"m"};
 return{k:"",text:age(r.age)+" ago"}}
// The issue title is one line; a spec's `[SPEC <n>] ` prefix is the spec's name, not the ticket's, so it is stripped here.
const liveTitle=r=>{const t=sub(r)||"";return t.replace(/^\[SPEC \d+\] /,"")};
// The record names its node in the graph's own words (`route.router.1`, `review_gate`); the row says it in a person's, short
// enough to share one line with the label: `router 1`, `review`. A node the map does not know keeps its own name.
const liveStage=r=>{const n=r.stage||"";if(!n)return"";const m=/^route\.router\.(\d+)$/.exec(n);return m?"router "+m[1]:n.replace(/_gate$/,"")};
// A live run in one short row (#2507): the run icon, the issue id or the run's name, the node it is at, and its age. The
// full label, the issue title, the model and the load live in the tooltip. A quiet run, an unreadable record and a run that
// has not written yet keep their own words in the age's place, never a fresh age. The row keeps its `data-id`, so the card's
// handler opens the run sheet, and its quiet edge says the run stopped writing (#1276).
function liveRow(r,shown){
 const s=liveSlot(r),on=shown&&(shown.has?shown.has(r.id):shown.includes(r.id))?` show`:"";
 const st=r.cpu?"":liveStage(r),bid=(r.issues||[])[0],lc=bid==null?"":((typeof SPECLC!=="undefined"&&SPECLC&&SPECLC.get(String(bid)))||"");   // the number wears its spec colour, as the board card does (#1429); one diagnostic shares the row: a run that owns CPU shows `cpu N%`, never both (#1276)
 const lab=bid!=null?`#${esc(bid)}`:esc(name(r).replace(/^implement /,"")),tip=[name(r),liveTitle(r),r.model?`model ${r.model_label||r.model}`:"",r.cpu?`cpu ${r.cpu}%`:""].filter(Boolean).join(" · ");
 const node=st?`<span class=lslot title="the node the run is at">${esc(st)}</span>`:"",act=` data-id="${esc(r.id)}" tabindex=0 role=button`;
 return `<div class="lr${on}${s.k?" "+s.k:""}"${act}${tip?` title="${iq(tip)}"`:""}${lc?` style="--lc:${lc}"`:""}><span class=lic>🏃</span><span class=lm><span class=lid>${lab}</span><span class=lchips>${node}${loadMark(r)}</span></span><span class=lage>${esc(s.k?s.text:age(r.age))}</span></div>`
}
function drawList(){const c=liveCard(runs.filter(r=>r.rank===0),new Set(slots)),el=$("#lrun");el.innerHTML=c.html;$("#lrs").innerHTML=c.head;
  [...el.querySelectorAll(".lr")].forEach(r=>{const lid=r.querySelector(".lid"),ch=r.querySelector(".lchips");if(lid&&ch&&ch.children.length&&ch.offsetHeight>lid.offsetHeight+2)r.classList.add("stack")})}   // the sidebar card: a bounded glance of the runs that are live now (#1276); a row whose chips do not fit the id's line stacks them under the id (#2507)
// The Runs tab lists the live runs and the runs ended today; a row opens the run sheet, never an embedded dashboard (#984).
// Live draws each live row once, in the full form (#1770): the childcc pass first, then the live runs and the hand holds,
// under one heading that carries the occupancy the runs answer built. The age bands left the page: Ended today is the history.
function drawMain(){if(TAB!=="runs")return;
  const shown=new Set(slots),cc=allRuns.filter(r=>r.kind==="childcc"),live=runs.filter(r=>r.rank===0||r.running),ids=new Set(live.map(r=>r.id)),ended=ENDED.filter(r=>!ids.has(r.id));
  const g=GROUPS.find(x=>x.key==="running"),occ=g&&g.text?`<span class=sr title="${iq(g.title||"")}">${esc(g.text)}</span>`:"";
  const rows=cc.map(r=>ccRow(r,shown)).concat(live.map(r=>liveRunRow(r,shown)));
  const row=r=>`<div class=r data-rid="${esc(r.id)}" tabindex=0 role=button><span class="b ${esc(r.state)}">${esc(r.badge||r.state)}</span><b>${esc(name(r))}</b><span class=m>${usd(r.spend)} · last write ${age(r.age)} ago</span></div>`;
  $("#main").innerHTML=(rows.length?`<h3>Live${occ}</h3>`+rows.join(""):"")+(ended.length?"<h3>Ended today</h3>"+ended.map(row).join(""):"")||"<div class=m>No run is live and none ended today.</div>"}
// The childcc pass is a worker, never a run (#598): its row opens the run it holds, and none when the list does not carry it (#689).
function ccRow(r,shown){const h=r.held&&runs.some(x=>x.id===r.held)?r.held:null;
  return `<div class="r cc${h&&shown.has(h)?" show":""}"${h?` data-rid="${esc(h)}" tabindex=0 role=button title="open ${esc(h)}"`:""}><div><span class="b ${r.state}"${r.tip?` title="${esc(r.tip)}"`:""}>${esc(r.badge)}</span> <b>${esc(r.label)}</b>${r.sub?`<div class=st title="${iq(r.sub)}">${esc(r.sub)}</div>`:""}${(r.lines||[]).map(l=>`<div class=st>${esc(l)}</div>`).join("")}${r.live?`<div class=m>age ${age(r.age)}</div>`:""}</div></div>`}
// A live run in the full form: its state, label, issue title, the newest gate's share, spend, minutes, last write, id and port.
// A hand hold has no run, so it opens no sheet (#1713).
function liveRunRow(r,shown){const act=r.kind==="hold"?"":` data-rid="${esc(r.id)}" tabindex=0 role=button`;
  return `<div class="r${shown.has(r.id)?" show":""}"${act}><div>${r.kind==="test"?`<span class=kd title="GUI test run">🧪</span>`:""}<span class="b ${r.state}"${r.tip?` title="${esc(r.tip)}"`:""}>${esc(r.badge||r.state)}</span> <b title="${iq(labelTip(r))}">${esc(name(r))}</b>${loadMark(r)}${!(r.issues||[]).length&&r.issue_why&&!r.fix&&!r.url?`<div class=st>${esc(labelTip(r))}</div>`:""}${sub(r)?`<div class=st title="${iq(sub(r))}">${esc(sub(r))}</div>`:""}<div class=m>${r.share?`<span class="sh${r.share.pct===100?" done":""}" title="the newest gate that counted ran ${r.share.total} tests, ${r.share.passed} passed">${r.share.passed}/${r.share.total} · ${r.share.pct}%</span> · `:""}${usd(r.spend)} · ${Math.round(r.dur||0)}m · last write ${age(r.age)} ago · ${esc(String(r.id).slice(-6))}${r.port?` · :${esc(r.port)}`:""}</div></div><span class=pn title="shown in the panel">${pins.has(r.id)?"●":"○"}</span></div>`}
function refresh(){plan();drawList();paintHL();drawMain();rkFollow()}
function applyFilter(){runs=allRuns.filter(r=>r.kind!=="childcc")}   // the childcc pass is drawn as the first Live row, never a pane (#598, #1770)
let known=null;   // run ids already seen: a run that appears running after the first poll is selected on its own (pinned, its group opened)
function selectNew(){const ids=allRuns.filter(r=>r.kind!=="childcc").map(r=>r.id);if(known===null){known=new Set(ids);return}
  const fresh=allRuns.filter(r=>r.kind!=="childcc"&&!known.has(r.id)&&r.rank===0);ids.forEach(i=>known.add(i));
  if(!fresh.length)return;const id=fresh.sort((x,y)=>x.age-y.age)[0].id;
  if(N===1)pins.clear();else if(pins.size>=N)pins.delete([...pins][0]);pins.add(id);save()}   // a run starting moves the pins, never the accordions
async function poll(){try{const d=await (await fetch("/api/runs?groups=1")).json();allRuns=Array.isArray(d)?d:d.runs||[];GROUPS=Array.isArray(d)?[]:d.groups||[];selectNew();applyFilter();refresh()}catch(e){}livePoll()}   // a hub older than the page ignores the query and answers the bare array: the runs still read, and the Live heading stays honestly empty (#1277); the live read is its own statement, so a failed runs read never skips it (#1697)
function barChart(rows,W=280,H=64,L=44){const n=rows.length,vals=rows.map(r=>r.tokens==null?null:(r.tokens||0)+(r.op||0)).filter(v=>v!=null),mx=Math.max(...vals,1),bw=W/Math.max(n,1),base=12+H,xl=t=>String(t).replace(/^\d{4}-/,"");
  const y=v=>base-v/mx*H,wd=Math.max(bw-2,1),tot=r=>(r.tokens||0)+(r.op||0);
  const bars=rows.map((r,i)=>{const x=L+i*bw+1,cx=x+wd/2,op=r.op||0,loop=r.tokens==null?0:r.tokens;
    const title=`${xl(r.t)}: ${r.tokens==null?"no read":tk(loop)+" tokens"+(op?` \u00b7 ${tk(op)} operator`:"")} \u00b7 ${r.runs} runs`;   // the loop's own count stays readable, the operator's names itself (#1756)
    if(r.tokens==null)return{kind:"missing",i,x,w:wd,cx,y:base,h:0,title};
    if(r.tokens===0&&!op)return{kind:"zero",i,x,w:wd,cx,y:base-2,h:2,title};
    return{kind:"bar",i,x,w:wd,cx,y:y(tot(r)),h:tot(r)/mx*H,op,oy:y(loop),oh:loop?loop/mx*H:0,zeroLoop:loop===0,top:tot(r)>=mx,title}});
  const im=vals.length?rows.findIndex(r=>r.tokens!=null&&tot(r)===mx):-1,named=im>=0?bars[im]:null;
  return{W,H,L,base,bars,hasOp:rows.some(r=>(r.op||0)>0),ticks:[0,mx/2,mx].map(v=>({v,y:y(v),label:tk(v)})),
    max:named?{bar:im,x:named.cx,y:named.y-4,anchor:named.cx>L+W*.75?"end":named.cx<L+W*.25?"start":"middle",text:"max "+tk(mx)}:null,
    first:n?xl(rows[0].t):"",now:"now"}}
function bars(rows){if(!rows.length)return"";const c=barChart(rows),{W,H,L,base}=c;   // tokens are the work: the chart never counts dollars
  return `<svg viewBox="0 0 ${L+W} ${base+14}" role=img aria-label="tokens per interval">`
    +`<text x="${L}" y="10" fill="var(--chart-axis)" style="font-size:var(--fs-body)">tokens</text>`   // the family's title treatment: inside the plot, at the left edge, at body size (#1141)
    +c.ticks.map(t=>`<line x1="${L}" y1="${t.y}" x2="${L+W}" y2="${t.y}" stroke="var(--chart-grid)" stroke-dasharray="3 2"/><text x="${L-4}" y="${t.y+4}" fill="var(--chart-axis)" style="font-size:var(--fs-body)" text-anchor="end">${t.label}</text>`).join("")
    +c.bars.map(b=>b.kind==="missing"?`<line class=miss x1="${b.x}" y1="${base}" x2="${b.x+b.w}" y2="${base}" stroke="var(--chart-axis)" stroke-dasharray="2 2"><title>${esc(b.title)}</title></line>`
      :b.kind==="zero"?`<rect class=zero x="${b.x}" y="${b.y}" width="${b.w}" height="${b.h}" style="fill:var(--chart-axis)"><title>${esc(b.title)}</title></rect>`
      :`<rect class=bar x="${b.x}" y="${b.oy}" width="${b.w}" height="${b.oh}" fill="${b.top?"var(--chart-bar-max)":"var(--chart-bar)"}"><title>${esc(b.title)}</title></rect>`   // the loop's segment
       +(b.zeroLoop?`<rect class=zero x="${b.x}" y="${base-2}" width="${b.w}" height="2" style="fill:var(--chart-axis)"><title>${esc(b.title)}</title></rect>`:"")   // the loop worked none of it: its zero still shows under the operator's segment (#1756)
       +(b.op?`<rect class="bar op" x="${b.x}" y="${b.y}" width="${b.w}" height="${b.h-b.oh}" fill="var(--chart-series-2)"><title>${esc(b.title)}</title></rect>`:"")).join("")   // the operator's segment, stacked above it (#1756)
    +(c.max?`<text x="${c.max.x}" y="${c.max.y}" fill="var(--chart-ok)" style="font-size:var(--fs-body)" text-anchor="${c.max.anchor}">${c.max.text}</text>`:"")
    +`<text x="${L}" y="${base+12}" fill="var(--chart-axis)" style="font-size:var(--fs-body)">${c.first}</text><text x="${L+W}" y="${base+12}" fill="var(--chart-axis)" style="font-size:var(--fs-body)" text-anchor="end">${c.now}</text></svg>`
    +(c.hasOp?`<p class="flg ltlg"><span><i style="background:var(--chart-bar)"></i>loop</span><span><i style="background:var(--chart-series-2)"></i>operator</span></p>`:"")}   // the legend tells the two consumers apart, and only when a bucket draws the operator's series (#1756)
function cmp(v,base,unit,lowerBetter=true){const d=Math.round((v-base)/base*100),good=lowerBetter?d<0:d>0,dir=d===0?"":lowerBetter?(d<0?"faster":"slower"):(d>0?"more":"fewer"),head=`was ${base}${unit?" "+unit:""}`,pct=d===0?"":`${Math.abs(d)}% ${dir}`;
  return {text:pct?`${head} \u00b7 ${pct}`:head,head,pct,dir,good}}
function kr(label,sub,v,unit,base,lowerBetter=true){if(v==null)return`<div class=kr><span class=l>${label}${sub?`<small>${sub}</small>`:""}</span><b>—</b></div>`;
  const u=unit?String(unit).trim():"";
  let chip="";if(base){const d=Math.round((v-base)/base*100),good=lowerBetter?d<0:d>0;
    const pct=`<span class="${good?"good":"bad"}">${Math.abs(d)}% ${lowerBetter?(d<0?"faster":"slower"):(d>0?"more":"fewer")}</span>`;
    chip=d===0?`<span class=d>was ${base}${u?" "+u:""}</span>`:`<span class=d>was ${base}${u?" "+u:""} · ${pct}</span>`}
  return `<div class=kr><span class=l>${label}${sub?`<small>${sub}</small>`:""}</span><b${v===0?" class=m":""}>${v}${u?`<small>${u}</small>`:""}</b>${chip}</div>`}
function openRows(o){return `<h4>Open branches <small>oldest first</small></h4>`
  +(o&&o.length?o.map((x,i)=>`<div class=kr><span class=l>#${x.n} · <b${x.min>=30?" class=w":""}>${x.min}</b> min${i===0?" (warns at 30)":""}</span></div>`).join("")
   :`<div class=kr><span class=l>none open</span></div>`)}
// SETV{ the settings view's pure rules (#913): the reset text, the add list, the diff before save. Run in node by tests.
function addOpts(sug,chain,isOff){return (sug||[]).filter(s=>!(chain||[]).includes(s.id)&&!isOff(s.id))}
function chainDesc(d,k){return (d[k]||[]).map(m=>{const e=((d.efforts||{})[k]||{})[m]||"",h=((d.hours||{})[k]||{})[m]||"";return [m,e&&e!=="medium"?e:"",h||"always"].filter(Boolean).join(" · ")}).join(" → ")}
function mdlDiffLines(old,cur,lab){const T="Models & chains · ",out=[],ids=[...new Set([...(old.models||[]),...(cur.models||[])].map(r=>r.id))];
 for(const m of ids){const a=!!(old.model_off||{})[m],b=!!(cur.model_off||{})[m];if(a!==b)out.push(`- ${T}model ${m}: ${a?"off":"on"}`,`+ ${T}model ${m}: ${b?"off":"on"}`)}
 for(const k of Object.keys(lab)){const a=chainDesc(old,k),b=chainDesc(cur,k);if(a!==b)out.push(`- ${T}${lab[k]}: ${a||"(empty)"}`,`+ ${T}${lab[k]}: ${b||"(empty)"}`)}
 return out}
function themeLine(theme,l){return l.startsWith(theme+" · ")?l:theme+" · "+l}
function colCount(n,max){return max==null?String(n):n+" / "+max}   // a column header's count: `n` with no limit, `n / max` with one (#2287)
// }SETV
let mdl=null,mdlSrv="",mdlMsg="",mdlRmk={};const MLAB={coding_models:"Coding",review_models:"Review",vision_models:"Vision",router_models:"Router"},MDESC={coding_models:"Writes the code in squad runs.",review_models:"Reviews each PR independently.",vision_models:"Looks at screenshots and images.",router_models:"The models the router runs on, tried in order."},EFF=["low","medium","high","max"];
/* ✕ marks a row; the row stays, struck, until Save takes it out of the chain with its effort and hours (#913). */
function mdlRmToggle(k,m){const id=k+"|"+m;if(mdlRmk[id])delete mdlRmk[id];else mdlRmk[id]=true}
const mdlRmd=(k,m)=>!!mdlRmk[k+"|"+m];
/* What Save writes: the panel without its marked rows and without empty keys. Dirty means a diff line, never a byte. */
function mdlPrune(o){for(const g of ["efforts","hours"]){o[g]=o[g]||{};for(const k of Object.keys(o[g]))if(!Object.keys(o[g][k]||{}).length)delete o[g][k]}
 o.model_off=o.model_off||{};for(const m of Object.keys(o.model_off))if(!o.model_off[m])delete o.model_off[m];return o}
function mdlOut(){const o=JSON.parse(JSON.stringify(mdl));for(const k of Object.keys(MLAB)){const keep=[];for(const m of o[k]||[]){if(!mdlRmd(k,m)){keep.push(m);continue}
 for(const g of ["efforts","hours"])if(o[g]&&o[g][k])delete o[g][k][m]}o[k]=keep}return mdlPrune(o)}
function mdlChanged(){return !!mdl&&mdlDiffLines(JSON.parse(mdlSrv),mdlOut(),MLAB).length>0}
function mdlEff(k,m){return (mdl.efforts&&mdl.efforts[k]&&mdl.efforts[k][m])||""}
function mdlSetEff(k,m,v){mdl.efforts=mdl.efforts||{};mdl.efforts[k]=mdl.efforts[k]||{};if(EFF.includes(v)&&v!=="medium")mdl.efforts[k][m]=v;else delete mdl.efforts[k][m]}
const HRP=["22-07","07-22","20-08","18-06","mon-fri 00-01,04-06,10-24 UTC; sat-sun 00-24 UTC"],HRD="(mon|tue|wed|thu|fri|sat|sun)",HRR="([01]\\d|2[0-3])\\s*-\\s*([01]\\d|2[0-4])",HRC=`((${HRD}(\\s*-\\s*${HRD})?(\\s*,\\s*${HRD}(\\s*-\\s*${HRD})?)*)\\s+)?${HRR}(\\s*,\\s*${HRR})*(\\s+utc)?`,HRX=new RegExp(`^\\s*${HRC}(\\s*;\\s*${HRC})*\\s*$`,"i");
function mdlHrs(k,m){return (mdl.hours&&mdl.hours[k]&&mdl.hours[k][m])||""}
function mdlSetHrs(k,m,v){v=(v||"").trim();mdl.hours=mdl.hours||{};mdl.hours[k]=mdl.hours[k]||{};if(HRX.test(v)&&v.slice(0,2)!==v.slice(3))mdl.hours[k][m]=v;else delete mdl.hours[k][m]}
/* The chain row's old switch (#1819) is read only now: a file that holds it shows "off (chain)" (SPEC 889 #920). */
function mdlOff(k,m){return !!(mdl.off&&mdl.off[k]&&mdl.off[k][m])}
/* The model's switch, one per model for every chain (SPEC 889 #920). The table stages it until Save; a chip writes it at once. */
function mdlSetOffM(m,v){mdl.model_off=mdl.model_off||{};if(v)mdl.model_off[m]=true;else delete mdl.model_off[m]}
/* What a model box shows is the hub's answer, model_box() (#677, ADR-0137): the registry label and its route, or
   "unregistered". The page holds no provider rule. An id just typed into an Add box has no answer until the save. */
const MBOX0={llm:null,route:null,registered:null,text:"resolved on save"};
function mdlBox(k,m){const b=mdl.boxes||{};return (b[k]||{})[m]||(b.known||{})[m]||MBOX0}
function mdlSub(x){if(!x.sub)return"";const q=x.quota||{},when=q.spent&&q.until_text?`, resets ${esc(q.until_text)}`:"";return`<span class="${q.spent?"bk":"el"}" title="${esc(x.sub.label)}${q.reason?" · "+esc(q.reason):""}">${esc(x.sub.label)}${q.spent?" · spent"+when:""}</span>`}
function mdlName(x,m){return x.llm?esc(x.llm):esc(m)}   // the id stays in the row title; only an unregistered row shows it (#687)
function mdlRoute(x,dflt){return x.registered===false?`<span class=ur>unregistered</span>`:esc(x.route||(x.registered?dflt:x.text))}
/* The model switch (SPEC 889 #920): one boolean per model, written by the chips and by the table through POST /api/models.
   The chain row's old switch is read only: a file that holds it shows "off (chain)" and the panel writes none. */
function mdlOffM(m){return !!(mdl.model_off&&mdl.model_off[m])}
function mdlChip(st){return st==="on"?"":`<span class="stc${String(st).startsWith("off")?" off":" lim"}">${esc(String(st).startsWith("off")?"off":st)}</span>`}   // a state chip shows only at limit or off (#913)
function mdlTable(){const rows=mdl.models||[];return `<h3>Models</h3><table class=mtab><thead><tr><th>Model</th><th>Subscription</th><th>State</th><th>On</th></tr></thead><tbody>`+rows.map(r=>{const of=mdlOffM(r.id),x=mdlBox("known",r.id);return`<tr class="${of?"off":""}"><td class=mn title="${esc(r.id)} · ${esc(x.text)}">${r.label?esc(r.label):mdlName(x,r.id)}</td><td>${mdlSub(r)}</td><td>${mdlChip(of?"off (model)":r.state)}</td><td><button class="mtg${of?" off":""}" data-mt="${esc(r.id)}" aria-pressed="${!of}" aria-label="${esc(r.id)} on" title="${of?"off (model): press to switch it on in every chain":"on: press to switch it off in every chain"}">${of?"off":"on"}</button></td></tr>`}).join("")+`</tbody></table>`}
const MHELP=`<details class=hlp><summary>How it works</summary><ul><li><b>Order</b>: ↑ ↓ move a model; ✕ marks it for removal: the row stays, struck through, until Save, and ↺ keeps it; "+ add a model…" puts one back.</li><li><b>Off</b> is set once per model and applies in every chain. An off model is skipped where a chain still lists it (the row stays, struck through, and ✕ still removes it) and is not offered in "+ add a model…". Switching it on restores every row.</li><li><b>Hours</b> are set per chain row: <code>HH-HH</code> local, may wrap midnight (<code>22-07</code>); empty means always. A row may also name days and UTC, clauses joined by <code>;</code>: <code>mon-fri 00-01,04-06,10-24 UTC; sat-sun 00-24 UTC</code> is DeepSeek off-peak. Outside its hours the row is skipped like off, and the next row runs.</li><li><b>Effort</b> is low to max, per chain row; medium is the default.</li><li><b>Save</b>, at the foot of Settings, shows the change (- before, + after) and writes only when you confirm.</li></ul></details>`;
function mdlRender(){const b=$("#mbody");if(!mdl){b.innerHTML="<span class=m>Loading the data.</span>";return}
 const c=mdl.counts||{on:0,at_limit:0},lc=mdl.last_coding||(mdl.coding_models||[])[0]||"",lx=lc?mdlBox("coding_models",lc):MBOX0;
 setChip("mdl",`${c.on} on · ${c.at_limit} at limit`);   // a settings fact, never the last run's model (#1278)

 const ch=mdl.chain||{},chOut=ch.out||[],chHit=(m)=>chOut.find(o=>o.until_text&&o.model.split(':').pop()===String(m).split(':').pop()),chTag=(m)=>{const o=chHit(m);return o?`<span class=bk title="${esc(o.reason)}">${esc(o.reason)}, resets ${esc(o.until_text)}</span>`:''},chBan=ch.banner?`<div class="mx err" role=alert>${esc(ch.banner)}</div>`:(ch.notice?`<div class=mx>${esc(ch.notice)}</div>`:"");
 const rd=mdl.repo_diff||[],rdBan=rd.length?`<div class="mx err" role=status><b>The repository chains differ from the live ones.</b> Run models-apply.sh to apply them.${rd.map(d=>`<div class=mx>${esc(MLAB[d.chain+"_models"]||d.chain)}: repository ${esc(d.repo.join(", "))} · live ${esc(d.live.join(", "))}</div>`).join("")}</div>`:"";
 b.innerHTML=chBan+rdBan+setNow("mdl",[
  setTile("Models on",c.on,c.on?"ok":"neutral"),
  setTile("At limit",c.at_limit,c.at_limit?"err":"ok"),
  setTile("Last coding",lx.llm?`${lx.llm} · ${lc}`:lc,"neutral"),
  setTile("Repository chains",rd.length?`${rd.length} differ`:"in sync",rd.length?"err":"ok")])
 +setSettings("mdl",[`<datalist id=hrp>${HRP.map(v=>`<option value="${v}">`).join("")}</datalist>`+mdlTable()+MHELP+`<h3>Chains</h3>`
 +Object.keys(MLAB).map(k=>`<div class=mc><div class=mt>${MLAB[k]}</div><div class=mx>${MDESC[k]}</div>`+mdl[k].map((m,i)=>{const ef=mdlEff(k,m),hr=mdlHrs(k,m),x=mdlBox(k,m),cof=mdlOff(k,m),mof=mdlOffM(m),o=(mdl.outside&&mdl.outside[k]||{})[m],st=((mdl.models||[]).find(z=>z.id===m)||{}).state;
  const rmk=mdlRmd(k,m),live=mdl[k].filter(z=>!mdlRmd(k,z)).length;
  return`<div class="mr${cof||mof?" off":""}${rmk?" rmk":""}" data-row="${k}|${esc(m)}"><span class=rk>${i+1}</span><span class=mn title="${esc(m)} · ${esc(x.text)}">${mdlName(x,m)}</span><span class=ct><span class=pv title="${esc(x.text)}">${mdlRoute(x,"")}${mdlSub(x)}</span>${mof?`<span class="stc off">off (model)</span>`:cof?`<span class="stc off">off (chain)</span>`:""}${st==="at limit"?`<span class="stc lim">at limit</span>`:""}${k==="review_models"?chTag(m):""}${o?`<span class=bk title="this row runs only inside its hours">outside hours ${esc(o)}</span>`:""}<label class=el>effort <select class="ef${ef?" set":""}" data-ef="${k}|${esc(m)}" aria-label="${esc(m)}: effort" title="effort for this model (medium is the default)">${EFF.map(e=>`<option value="${e==="medium"?"":e}"${(ef||"medium")===e?" selected":""}>${e}</option>`).join("")}</select></label><label class=el>hours <input class="hr${hr?" set":""}" list=hrp data-hr="${k}|${esc(m)}" value="${esc(hr)}" placeholder="always" aria-label="${esc(m)}: hours" title="hours this model may run (HH-HH local, wraps midnight; or days, ranges and UTC; empty = always)"></label><span class=mb><button data-mv="${k}|${i}|-1" ${i?"":"disabled"} aria-label="move up" title="move up">↑</button><button data-mv="${k}|${i}|1" ${i<mdl[k].length-1?"":"disabled"} aria-label="move down" title="move down">↓</button><button class=x data-rm="${k}|${i}" ${rmk||live>1?"":"disabled"} aria-label="${rmk?"undo remove":"remove"} ${esc(m)}" title="${rmk?"keep this model in the chain":"remove from chain on Save"}">${rmk?"↺":"✕"}</button></span></span></div>`}).join("")
  +`<div class=madd><select data-add="${k}" aria-label="${MLAB[k]}: add a model"><option value="">+ add a model…</option>${addOpts((mdl.suggest||{})[k],mdl[k],mdlOffM).map(s=>`<option value="${esc(s.id)}">${esc(s.text)}</option>`).join("")}</select></div></div>`).join("")])
  +(mdlMsg?`<div class="mx${mdlMsg.startsWith("!")?" err":""}" role=status>${esc(mdlMsg.replace(/^!/,""))}</div>`:"");
 if(typeof setBar==="function")setBar()}
async function mdlLoad(){try{mdl=await (await fetch("/api/models")).json();mdlSrv=JSON.stringify(mdl);mdlRmk={};mdlRender()}catch(e){}}
/* One write route for both places (SPEC 889 #920): a chip flips at once; the table stages its switch until Save. */
async function mdlPost(body){const r=await fetch("/api/models",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});return r.json()}
$("#mbody").addEventListener("change",e=>{const t=e.target;if(!mdl)return;mdlMsg="";if(t.dataset.ef){const[k,m]=t.dataset.ef.split("|");mdlSetEff(k,m,t.value)}
 else if(t.dataset.hr){const[k,m]=t.dataset.hr.split("|"),v=t.value.trim();if(v&&!HRX.test(v))mdlMsg="!hours are HH-HH, for example 22-07, or days, ranges and UTC: sat-sun 00-24 UTC";mdlSetHrs(k,m,v)}
 else if(t.dataset.add){const k=t.dataset.add,v=t.value;if(v&&!mdl[k].includes(v)&&!mdlOffM(v))mdl[k].push(v)}
 else return;mdlRender()});
$("#mbody").addEventListener("click",e=>{const t=e.target.closest("button");if(!t||!mdl)return;mdlMsg="";
 if(t.dataset.mt){const m=t.dataset.mt;mdlSetOffM(m,!mdlOffM(m))}
 else if(t.dataset.mv){const[k,i,d]=t.dataset.mv.split("|"),a=mdl[k],j=+i+ +d;[a[i],a[j]]=[a[j],a[i]]}
 else if(t.dataset.rm){const[k,i]=t.dataset.rm.split("|");mdlRmToggle(k,mdl[k][+i])}
 else return;
 mdlRender()});
/* Settings > Spend & limits (#926): the token guard's on/off switch, its daily cap and its pause factor, with the burn
   it measures and whether dispatch is paused now. The page renders and asks; the hub validates, writes the profile and
   records the change (ADR-0137), and the guard reads it on its next pass with no restart. */
let limS=null,limMsg="";   // the last state the hub saved; a typed value lives only in its own field

/* Spend & limits saves each field on its own, with no button (#2433). A valid edit posts once, on the field's blur or
   after a short pause in typing; the field then shows a check mark, its group shows one line naming the saved value and
   the line's age, and a value the hub refuses marks the field, writes nothing and leaves the saved state in force.
   A write carries the saved state with only the edited field changed, so a refused value in another field never rides
   along. The page writes and renders; the hub validates, writes atomically and records the change (ADR-0030, ADR-0137). */
const LIM_FIELD={limg:"guard",limc:"cap",limf:"factor"},LIM_NAME={guard:"token guard",cap:"daily token cap",factor:"pause factor"};
const LIM_ID={guard:"#limg",cap:"#limc",factor:"#limf"};
let limOK="",limVal="",limAt=0,limBad="",limBadMsg="",limTmr={},limPauseMs=700,limSent={},limChain=Promise.resolve();   // limSent holds the value each field last posted, so a timer save and a later blur send one request; limTmr holds one timer per field, so a pause in one field never drops another field's edit; limChain runs one write at a time, so the next body carries the value the last write saved (#2433)
const limKName=k=>LIM_NAME[k]||k;
const limMark=k=>limOK===k?`<span class=lok id="limmk-${k}" aria-label=saved>✓</span>`:"";   // the mark carries an id, so a save patches it instead of redrawing its input (#2433)
const limBadCls=k=>limBad===k?" class=bad":"";
const limAgeTxt=now=>`${span(((now||Date.now())-limAt)/1000)} ago`;
function limLine(){if(limBad)return `<span class=lbad aria-hidden=true>!</span> ${esc(limBadMsg||limKName(limBad)+" was not saved")}`;
 if(!limOK)return "";return `<span class=lok aria-hidden=true>✓</span> Saved · ${esc(limKName(limOK))} ${esc(limVal)} · <span id=limage>${limAgeTxt()}</span>`}
/* The Tokens group's Changes list (#2510): one collapsed line that names the count, and the records the ledger holds
   under it, newest first. The hub reads the ledger; the page only prints what it is given. */
function limChangesHtml(cs){cs=Array.isArray(cs)?cs:[];
 return `<details class=chg><summary>Changes <span class=m>${cs.length}</span></summary>`
  +(cs.length?`<ul>${cs.map(r=>`<li><span class=cat>${esc(r.at||"")}</span> ${esc(r.text||"")}</li>`).join("")}</ul>`
             :`<p class=m>No change recorded.</p>`)+`</details>`}
function limTick(now){const el=$("#limage");if(el&&limOK)el.textContent=limAgeTxt(now)}   // the age moves on with the clock, with no new write
let limAgeTmr=null;   // the age ticker runs only while a saved value is shown in a visible theme (#2433)
const limShown=()=>{const p=$("#lim");return !p||p.open===undefined||!!p.open};   // a collapsed theme ticks nothing
function limTickStop(){if(limAgeTmr){clearInterval(limAgeTmr);limAgeTmr=null}}
function limTickStart(){if(limAgeTmr||!limOK||!limShown())return;
 limAgeTmr=setInterval(()=>{if(!limOK||!limShown()){limTickStop();return}limTick()},1000);if(limAgeTmr&&limAgeTmr.unref)limAgeTmr.unref()}
const limTickSync=()=>{limOK?limTickStart():limTickStop()};
/* The value the reader typed in the field's own input, or null when the page has not drawn it yet. */
const limTyped=k=>{if(k==="guard"){const g=$("#limg");return g?limG():null}   // the guard is a switch: its checked state is the word the hub reads (#2434)
 const el=$(LIM_ID[k]);return el&&typeof el.value==="string"?el.value:null};
async function limSend(body){const r=await fetch("/api/limits",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});return r.json()}
function limWrite(which,send=limSend){const run=()=>limWriteNow(which,send),p=limChain.then(run,run);limChain=p.catch(()=>{});return p}   // the writes queue, so a second field's body carries the value the first one saved (#2433)
async function limWriteNow(which,send=limSend){if(RO)return {skip:true};if(which.startsWith("kan:"))return kanWrite(which.slice(4),send);   // a read-only view writes nothing, and the Kanban row carries the same send, so a test's stub reaches it (#2433)
 if(which.startsWith("slot:"))return slotPost(true);   // a slot cap saves on its own, like the limit fields (#2435)
 const cur=limS||{},vals={guard:String(cur.guard??""),cap:String(cur.cap??""),factor:String(cur.factor??"")},typed=limTyped(which);
 if(typed!==null)vals[which]=which==="cap"&&limCapParse(typed)!=null?String(limCapParse(typed)):typed;   // a cap leaves as the whole integer its units denote; text the parse refuses stays raw, for the hub to refuse (#2433)
 if(limSent[which]===vals[which]){if(limBad===which){limBad="";limBadMsg="";limRender()}return {skip:true}}   // the last write posted this value already, so a timer save and then a blur send one request (#2433)
 let d;
 try{d=await send({...vals,confirm:true})}catch(x){d={err:"save failed"}}
 if(d.err){limBad=which;limBadMsg=d.err;limOK="";limAt=0;delete limSent[which]}   // a refused value is never remembered as posted
 else{limS=d.state||limS;limBad="";limBadMsg="";limOK=which;limVal=String((d.state||vals)[which]);limAt=Date.now();limSent[which]=vals[which]}
 limRender();if(typeof setBar==="function")setBar();return d}
function limQueue(which,send){if(RO)return;clearTimeout(limTmr[which]);limTmr[which]=setTimeout(()=>{delete limTmr[which];limWrite(which,send).catch(()=>{})},limPauseMs)}   // each field holds its own timer (#2433)

/* Settings > Spend & limits · Kanban (#2432): the card limit of each active board column, inside one theme with the token
   guard. The page renders and asks; the hub validates, shows the diff and writes the project settings file on confirm
   (ADR-0137). A blank field is unlimited. The frontier reads Doing, the branch review pass reads Reviewing, and the
   Kanban header shows the count against the limit. */
// KAN{ the Kanban sub-group's pure rules (#2432): its values, its dirty test, its chip and its html. Run in node by tests.
let kanS=null,kanDraft=null,kanMsg="",kanOK="",kanVal="",kanAt=0,kanBad="",kanBadMsg="",kanSent={},kanRef={},kanDiff=[],kanChain=Promise.resolve(),kanStaged={},kanBusy=false,kanGen=0;   // kanSent holds the value each column last posted, so a timer save and a later blur send one request; kanRef holds the value the hub refused, so a later pause never resends it; kanStaged names each column whose field holds an edit the owner has not saved, so no refresh infers it from a snapshot (#2433, #2541)
const kanClone=o=>JSON.parse(JSON.stringify(o));
function kanStage(name,raw){const c=kanDraft&&kanDraft.find(x=>x.name===name);if(!c)return false;c.max_cards=raw===""?null:Number(raw);kanStaged[name]=true;kanMsg="";return true}   // the input marks its column staged; only a landed or refused write clears the mark (#2541)
const kanCols=()=>kanDraft||(kanS&&kanS.columns)||[];
const kanLimits=()=>Object.fromEntries(kanCols().map(c=>[c.name,c.max_cards==null?"none":String(c.max_cards)]));
const kanLims=c=>Object.fromEntries(c.map(x=>[x.name,x.max_cards==null?"none":String(x.max_cards)]));
const kanBody=name=>{const out=kanLims((kanS&&kanS.columns)||[]),c=kanDraft&&kanDraft.find(x=>x.name===name);   // the payload is the last saved state, with only the column being saved overlaid, so another drafted column never rides along (#2433)
 if(c)out[name]=c.max_cards==null?"none":String(c.max_cards);return out};
const kanDirty=()=>!!kanS&&!!kanDraft&&JSON.stringify(kanDraft)!==JSON.stringify(kanS.columns);
const kanFull=()=>kanCols().filter(c=>c.max_cards!=null&&c.count!=null&&c.count>=c.max_cards).map(c=>c.name);
const kanChip=()=>{const f=kanFull();return kanS&&kanS.read===false?"cards not read yet":!f.length?(kanCols().some(c=>c.max_cards!=null)?"under its limit":"no limit"):f.length===1?`${f[0]} at its limit`:`${f.join(", ")} at their limit`};
const kanPct=c=>c.max_cards?Math.max(0,Math.min(100,Math.round(100*c.count/c.max_cards))):0;
const kanFile=()=>{const f=(kanS&&(kanS.file_label||kanS.file))||"";   // the footer names the project file by its repository path, derived from the payload's own path when no label rides along (#2512)
 const p=String(f).split(String.fromCharCode(92)).join("/"),i=p.lastIndexOf("/docs/");
 return i>=0?p.slice(i+1):p.startsWith("docs/")?p:p.slice(p.lastIndexOf("/")+1)};
const kanBar=c=>`<span class=kbar role=img aria-label="${c.count} of ${c.max_cards}"><span class="kfill${c.count>=c.max_cards?" full":""}" style="width:${kanPct(c)}%"></span></span><b class=knum>${c.count} of ${c.max_cards}</b>`;   // a limited column shows its bar and N of M (#2512)
const kanNow=c=>c.count==null?`<b class=knum>—</b>`:c.max_cards==null?`<b class=knum>${c.count}</b>`:kanBar(c);
const kanRow=c=>`<tr data-kanrow="${esc(c.name)}"><th scope=row class=kc>${esc(c.name)}</th><td class=kn>${kanNow(c)}</td>`
 +`<td class=kl><input data-kan="${esc(c.name)}" type=number min=1 max=100 step=1 inputmode=numeric value="${c.max_cards==null?"":c.max_cards}" placeholder=unlimited aria-label="${esc(c.name)} card limit"${kanBad===c.name?" class=bad aria-invalid=true":""}></td>`
 +`<td class=ks>${esc(c.stops||"no pass reads it yet")}${kanOK===c.name?" <span class=lok aria-label=saved>✓</span>":""}</td></tr>`;   // one aligned row of the table the spec draws (#2512)
const kanLine=()=>kanBad?`<span class=lbad aria-hidden=true>!</span> ${esc(kanBadMsg||kanBad+" was not saved")}`
 :kanOK?`<span class=lok aria-hidden=true>✓</span> Saved · ${esc(kanOK)} ${esc(kanVal)} · <span id=kanage>${span((Date.now()-kanAt)/1000)} ago</span>`+(kanDiff.length?` · ${esc(kanDiff.join(" · "))}`:""):kanS&&kanS.read===false?"Cards now: board items not read yet.":"";   // the line also carries the diff the write landed, the confirm ADR-0137 asks for (#2433)
function kanTick(now){const el=$("#kanage");if(el&&kanOK)el.textContent=span(((now||Date.now())-kanAt)/1000)+" ago"}   // a Kanban save's age moves with the clock, like the limits line (#2433)
let kanAgeTmr=null;
function kanTickStop(){if(kanAgeTmr){clearInterval(kanAgeTmr);kanAgeTmr=null}}
function kanTickStart(){if(kanAgeTmr||!kanOK||!limShown())return;
 kanAgeTmr=setInterval(()=>{if(!kanOK||!limShown()){kanTickStop();return}kanTick()},1000);if(kanAgeTmr&&kanAgeTmr.unref)kanAgeTmr.unref()}
const kanTickSync=()=>{kanOK?kanTickStart():kanTickStop()};
function kanWrite(name,send){const run=()=>kanWriteNow(name,send),p=kanChain.then(run,run);kanChain=p.catch(()=>{});return p}
async function kanWriteNow(name,send){if(RO)return {skip:true};if(!kanDraft)return;
 const body=kanBody(name),val=String(body[name]??"");
 if(kanSent[name]===val)return {skip:true};   // the last write posted this column's value already (#2433)
 if(kanBad===name&&kanRef[name]===val)return {skip:true};   // the hub refused this value already, so a later pause never resends it (#2433)
 let d;try{d=send?await send({columns:body,confirm:true}):await (await fetch("/api/columns",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({columns:body,confirm:true})})).json()}catch(x){d={err:"save failed"}}
 if(d.err){kanBad=name;kanBadMsg=d.err;kanOK="";kanRef[name]=val;delete kanStaged[name];delete kanSent[name];clearTimeout(limTmr["kan:"+name]);delete limTmr["kan:"+name]}   // a refused column keeps its mark and its value in the draft; its timer is cleared so no pause resends the same body; the mark is no longer staged, so a refresh reads the server again (#2433, #2541)
 else{kanS=d.state||kanS;const st=(kanS&&kanS.columns)||[],landed=st.find(x=>x.name===name);delete kanStaged[name];   // the column landed: the draft follows the server again (#2541)
  kanDraft=kanCols().map(c=>{const s=st.find(x=>x.name===c.name);return s?{...c,max_cards:c.name===name?s.max_cards:c.max_cards,count:s.count}:c});   // the landed column folds into the draft, so another column's staged edit stays and its count moves on (#2433)
  kanBad="";kanBadMsg="";delete kanRef[name];kanOK=name;kanVal=String(landed&&landed.max_cards!=null?landed.max_cards:body[name]??"");kanAt=Date.now();kanSent[name]=kanVal;kanDiff=Array.isArray(d.diff)?d.diff:[]}   // the column remembers the value the write landed (#2433)
 limRender();if(typeof setBar==="function")setBar();return d}
async function kanRefresh(get){if(!get){if(kanBusy)return false;kanBusy=true}   // one read of the columns at a time, so a slow tick never stacks on the next (#2541)
 const gen=++kanGen;let s;try{s=await (get?get():fetch("/api/columns").then(r=>r.json()))}catch(e){s=null}
 finally{if(!get)kanBusy=false}
 if(!s||s.err||gen!==kanGen)return false;   // an older response never overwrites a newer read (#2541)
 const cur=kanCols();
 kanDraft=(s.columns||[]).map(x=>{const c=cur.find(y=>y.name===x.name);return kanStaged[x.name]&&c?{...x,max_cards:c.max_cards}:x});   // a later board read moves the counts and the stops; a staged limit rides over it (#2541)
 cur.forEach(c=>{if(kanStaged[c.name]&&!kanDraft.some(x=>x.name===c.name))kanDraft.push({...c})});   // a staged column the payload lacks still keeps its row, never dropped by a partial read (#2541)
 kanS=s;return true}
function kanCounts(){document.querySelectorAll("#lbody tr[data-kanrow]").forEach(tr=>{const c=kanCols().find(x=>x.name===tr.dataset.kanrow),cell=tr.querySelector(".kn");if(c&&cell)cell.innerHTML=kanNow(c)})}   // the cells alone move, so the field the owner is typing in keeps its value and its focus (#2541)
async function kanPoll(){const f=document.activeElement,typing=!!f&&f.tagName==="INPUT"&&!!f.closest&&!!f.closest("#lbody");
 if(document.hidden||RO||!(await kanRefresh()))return;if(typing)kanCounts();else limRender()}   // a focused field keeps its value and its selection: the poll moves the count cells alone (#2541)
function kanHtml(){if(!kanS)return `<div class=setblk data-blk=kanban data-theme=lim><h3 class=blkh>Kanban</h3><span class=m>${esc(kanMsg.replace(/^!/,"")||"Loading the data.")}</span></div>`;   // a failed read says so here, never a sub-group that silently vanishes
 return `<div class=setblk data-blk=kanban data-theme=lim><h3 class="blkh khead"><span>Kanban <small class=ksub>· cards · this board</small></span> <small id=kanchip class=${kanS&&kanS.read===false?"":kanFull().length?"err":"ok"}>${esc(kanChip())}</small></h3>`
  +`<table class=ktab><colgroup><col class=kcol1><col class=kcol2><col class=kcol3><col class=kcol4></colgroup><thead><tr><th scope=col>Column</th><th scope=col>Cards now</th><th scope=col>Card limit</th><th scope=col>What the limit stops</th></tr></thead><tbody>`
  +kanCols().map(kanRow).join("")+`</tbody></table>`
  +`<p class=note>Empty field = unlimited. Closed and Abandoned hold finished cards and take no limit. File: <code>${esc(kanFile())}</code></p></div>`
  +(kanMsg?`<div class="mx${kanMsg.startsWith("!")?" err":""}" role=status>${esc(kanMsg.replace(/^!/,""))}</div>`:"")
  +(kanLine()?`<div class=klok role=status>${kanLine()}</div>`:"")}
// }KAN
/* Settings > Spend & limits · Machine slots (#2435): the slots of this machine, across every project. The page renders
   the read and asks; the hub validates, shows the diff and writes the project settings file on confirm (ADR-0137). The
   runs cap and the gate-worker cap are editable; children and reviews stay in the file. */
// SLOT{ the Machine slots sub-group's pure rules (#2435): its values, its dirty test, its chip, its gate line and its html. Run in node by tests.
let slotS=null,slotDraft=null,slotMsg="",slotDiff=null;
const slotClone=o=>JSON.parse(JSON.stringify(o));
const slotNow=()=>slotDraft||slotS||{};
const slotLabel=k=>k==="runs"?"Runs":"Gate workers";
const slotMax=k=>{const c=slotS&&slotS.ceiling,n=c&&c[k];return typeof n==="number"&&n>=1?n:1000};   // the plugin ceiling the read names, else the catalog's high bound
const slotWhy=(k,raw)=>{const t=String(raw==null?"":raw).trim(),n=/^\d+$/.test(t)?Number(t):0;
 return n>=1&&n<=slotMax(k)?"":`the cap is an integer from 1 to ${slotMax(k)}`};
const slotStage=(k,raw)=>{const why=slotWhy(k,raw);if(why){slotMsg="!"+slotLabel(k)+": "+why;slotSay();return false}
 slotDraft[k].cap=Number(String(raw).trim());slotMsg="";slotDiff=null;slotSay();return true};   // a refused cap never reaches the draft the Save would write
const slotVal=k=>String(slotNow()[k].cap);
const slotCaps=()=>({runs:slotVal("runs"),gates:slotVal("gates")});
const slotDirty=()=>!!slotS&&!!slotDraft&&(slotVal("runs")!==String(slotS.runs.cap)||slotVal("gates")!==String(slotS.gates.cap));
const slotFull=a=>!!a&&a.in_use>=a.cap;
const slotChip=(s=slotS)=>{s=s||{};return slotFull(s.runs)?["runs full","err"]:slotFull(s.gates)?["gate workers full","err"]:["under the caps","ok"]};
const slotHeld=h=>h.issues&&h.issues.length?h.issues.map(n=>`<a class=chip href="https://github.com/__REPO__/issues/${esc(n)}" target=_blank rel=noopener>#${esc(n)}</a>`).join(" "):`<small class="m slotnone">no issue named</small>`;
const slotRun=g=>g.gates_running?`${g.gates_running} gate${g.gates_running===1?"":"s"} running`:g.in_use?"no loop gate running":"none running";
const slotGateLine=g=>`a gate takes ${g.slots_per_gate} slots (${g.workers} asked, cap ${g.cap}) = ${g.gates_at_once} ${g.gates_at_once===1?"gate":"gates"} at a time · ${slotRun(g)}`;
const slotRow=(name,label,use,cap,held)=>`<tr><th scope=row class=scol1>${label}</th><td class=scol2><span class=slotuse><span class="slotbar${use>=cap?" full":""}"><i style="width:${cap?Math.min(100,100*use/cap):0}%"></i></span><span class=slotnow>${use} of ${cap}</span></span></td><td class=scol3>${slotCap(name)}</td><td class="slothold scol4">${held}</td></tr>`;
const slotCap=name=>`<input data-slot=${name} type=number min=1 max="${slotMax(name)}" step=1 inputmode=numeric value="${slotNow()[name].cap}" aria-label="${name==="runs"?"runs cap":"gate workers cap"}">`;
const slotSay=()=>{const e=$("#slotmsg");if(!e)return;e.textContent=slotMsg.replace(/^!/,"");e.className="mx"+(slotMsg.startsWith("!")?" err":"")};
function slotsHtml(){const t=slotChip();
 if(!slotS)return `<div class=setblk data-blk=slots data-theme=lim><h3 class="blkh khead"><span>Machine slots <small class=ksub>· this machine · all projects</small></span></h3><span class=m>${esc(slotMsg.replace(/^!/,"")||"Loading the data.")}</span></div>`;   // a failed read says so here, never a sub-group that silently vanishes
 const s=slotNow(),r=s.runs,g=s.gates;
 return `<div class=setblk data-blk=slots data-theme=lim><h3 class="blkh khead"><span>Machine slots <small class=ksub>· this machine · all projects</small></span> <small id=slotchip class=${t[1]}>${esc(t[0])}</small></h3>`
  +`<table class=stab><thead><tr><th scope=col class=scol1>Slot</th><th scope=col class=scol2>In use</th><th scope=col class=scol3>Slots</th><th scope=col class=scol4>Held by</th></tr></thead><tbody>`
  +slotRow("runs","Runs",r.in_use,r.cap,r.holders.length?r.holders.map(slotHeld).join(" "):`<small class=m>none running</small>`)
  +slotRow("gates","Gate workers",g.in_use,g.cap,`${esc(slotGateLine(g))}${g.holders.length?" · held by "+esc(g.holders.filter((h,i,a)=>a.indexOf(h)===i).join(", ")):""}`)
  +`</tbody></table>`
  +`<p class=note>A run carries one or more cards, so Runs and Doing count different things. Gate workers also serve child gates, day suites and hand gates.</p>`
  +`<p class=note>File only: children ${esc(s.children)} · reviews ${esc(s.reviews)}, in <code>${esc(s.file_label||"docs/squad/defaults.yaml")}</code>. A resume refuses when this file changed.</p>`
  +(slotDiff&&slotDiff.length?`<div class="mx" role=status>${slotDiff.map(l=>esc(l)).join(" · ")}</div>`:"")
  +`<div class="mx${slotMsg.startsWith("!")?" err":""}" id=slotmsg role=status>${esc(slotMsg.replace(/^!/,""))}</div></div>`}
// }SLOT
const limN=v=>{if(v==null)return "—";const t=tk(v);   // a reading under 1000 is whole; a unit reading never keeps a trailing zero: 125B, not 125.00B (#2510)
 return /[KMBT]$/.test(t)&&t.includes(".")?t.replace(/0+([KMBT])$/,"$1").replace(/\.([KMBT])$/,"$1"):t};
const LIM_UNITS=[["T",1e12],["B",1e9],["M",1e6],["K",1e3]],LIM_SCALE={K:1e3,M:1e6,B:1e9,T:1e12};
const limCapFmt=n=>{const x=Number(n);if(n===""||n==null||!Number.isFinite(x))return String(n??"");   // a cap reads 1.5M, 250M or 1T, never a row of zeros (#2434)
 for(const[u,d]of LIM_UNITS)if(x>=d)return String(+(x/d).toFixed(2))+u;return String(x)};
const limCapParse=s=>{const t=String(s??"").trim(),m=t.match(/^(\d+(?:\.\d+)?)\s*([KMBT])$/i);   // the field takes the same units back (#2434)
 const x=m?+m[1]*LIM_SCALE[m[2].toUpperCase()]:/^\d+$/.test(t)?+t:NaN;return Number.isInteger(x)&&x>0?x:null};
const limBurn=(cap,factor,wh)=>{const c=limCapParse(cap),f=+factor;   // the hourly burn that pauses dispatch: factor x the cap's hourly share (#2434)
 return c==null||!Number.isFinite(f)||f<=0||!(wh>0)?null:Math.floor(Math.floor(c/wh)*f)};
const limG=()=>{const e=$("#limg");return e?(e.checked?"on":"off"):""};   // the guard is a switch: its checked state is the word the hub reads (#2434)
const limBusy=()=>{const a=typeof document!=="undefined"?document.activeElement:null;if(a&&a.id&&Object.keys(LIM_ID).some(k=>LIM_ID[k]==="#"+a.id))return true;return Object.keys(limTmr).length>0};   // a focused field, or any pending timer, holds text the page has not saved (#2433)
function limPatch(){for(const k of Object.keys(LIM_ID)){const m=$("#limmk-"+k);   // the mark moves in place, so a focused input keeps what it holds (#2433)
  if(limOK!==k){if(m&&m.remove)m.remove();else if(m)m.hidden=true;continue}   // the field that lost its mark loses the element, never a hidden leftover
  if(m&&m.remove){m.hidden=false;continue}
  const el=$(LIM_ID[k]),row=el&&el.parentNode;if(row&&row.insertAdjacentHTML)row.insertAdjacentHTML("beforeend",limMark(k))}
 const st=$("#limstat");if(st)st.innerHTML=limLine()}   // and so does the saved line (#2433)
function limRender(){const b=$("#lbody");if(!b)return;if(!limS){b.innerHTML="<span class=m>Loading the data.</span>";limTickSync();kanTickSync();return}
 const s=limS,v=s,off=s.guard==="off",badVal=limBad?limTyped(limBad):null;   // a refused field keeps the typed value and its error mark until a write succeeds or the Discard resets it (#2433)
 const chipTxt=off?"guard off · never pauses":s.paused?"paused":"on",chipCls=s.paused?"err":"";   // a paused dispatch is the chip's err tone, not a muted word (#1282); a guard off never pauses (#2434)
 if(limBusy()){const tc=$("#limtok");if(tc){tc.textContent=chipTxt;tc.className="chip"+(chipCls?" "+chipCls:"")}limPatch();if(typeof setBar==="function")setBar();limTickSync();kanTickSync();return}   // a field with unsent text keeps its input; the chip, the mark and the line move in place (#2433)
 // The Tokens group draws its own heading and state chip (#2510), then the four Now tiles on one row, then the fields.
 let h=`<div class=setblk data-blk=tokens data-theme=lim><h3 class="blkh tokhead">Tokens <span id=limtok class="chip${chipCls?" "+chipCls:""}">${esc(chipTxt)}</span></h3></div>`
 +setNow("lim",[
  setTile("Dispatch",s.paused?"paused":"running",s.paused?"err":"ok"),
  setTile("Last hour",limN(s.hour_burn)+" tokens","neutral"),
  setTile("Last "+s.window_h+" h",limN(s.window_burn)+" tokens","neutral"),
  setTile("Pauses over",off?"— guard off":limN(s.pause_line)+" tokens/h",off?"muted":"neutral"),   // no pause line exists while the guard is off (#2434)
 ])
 +setSettings("lim",[
  setRow("Token guard",`<span class=lf><label class=swt><input id=limg type=checkbox role=switch aria-label="Token guard"${limBadCls("guard")}${(limBad==="guard"&&badVal!==null?badVal==="on":v.guard==="on")?" checked":""}></label>${limMark("guard")}</span><small class=kstop>Off: dispatch never pauses on token burn.</small>`),
  setRow("Daily token cap",`<span class=lf><input id=limc type=text inputmode=text spellcheck=false value="${esc(limBad==="cap"&&badVal!==null?badVal:limCapFmt(v.cap))}" aria-label="Daily token cap"${limBadCls("cap")}${limBad==="cap"?" aria-invalid=true":""}></span><small class=lunit>tokens</small>${limMark("cap")}<small class=kstop>Accepts 6M, 250M, 1T. Read only while the guard is on.</small>`),
  setRow("Pause factor",`<span class=lf><input id=limf type=number min=0.1 step=0.5 inputmode=decimal value="${limBad==="factor"&&badVal!==null?esc(badVal):v.factor}" aria-label="Pause factor"${limBadCls("factor")}${limBad==="factor"?" aria-invalid=true":""}></span><small class=lunit>×</small>${limMark("factor")}<small class=kstop>Pauses while the last hour burns over ${esc(String(+v.factor))} × (cap ÷ ${esc(String(s.window_h))}) = ${esc(limN(limBurn(v.cap,v.factor,s.window_h)))} tokens/h.</small>`),
 ])   // the settings block carries no note here: the profile line, the Changes list and the saved line follow it (#2510)
 +`<p class=note>Profile: <code title="${esc(s.file||"none in force")}">${esc((s.profile||"none in force").split("/").pop())}</code></p>`   // the row stays short: the file name, with the full path (the read's own `file`) on hover (#2434)
 +limChangesHtml(s.changes)
 +`<p class=note><span id=limstat>${limLine()}</span></p>`;   // one line: the saved value and its age, or what the hub refused (#2433); the id lets a save patch it in place
 if(limMsg)h+=`<div class="mx${limMsg.startsWith("!")?" err":""}" role=status>${esc(limMsg.replace(/^!/,""))}</div>`;
 h+=kanHtml();   // the Kanban sub-group below the token rows (#2432)
 h+=slotsHtml();   // the Machine slots sub-group below Kanban (#2435)
 b.innerHTML=h;if(typeof setBar==="function")setBar();limTickSync();kanTickSync()}
async function limLoad(){try{limS=await (await fetch("/api/limits")).json();limMsg=""}catch(e){limMsg="!load failed"}   // the theme loads with the page, so its chip never reads a dash (#2432)
 try{kanS=await (await fetch("/api/columns")).json();kanDraft=kanClone(kanS.columns||[]);kanStaged={};kanMsg=kanS.err?"!"+kanS.err:""}catch(e){kanMsg="!load failed"}
 try{slotS=await (await fetch("/api/slots")).json();slotDraft=slotClone(slotS);slotDiff=null;slotMsg=slotS.err?"!"+slotS.err:""}catch(e){slotMsg="!load failed"}
 limRender()}
setInterval(()=>{if(limShown())kanPoll()},5000);   // the board's read reaches the Kanban counts within one poll (#2541)
async function slotPost(confirm){try{const r=await fetch("/api/slots",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({...slotCaps(),confirm})}),d=await r.json();
 if(d.err){slotMsg="!"+d.err;slotDiff=null;if(slotS)slotDraft=slotClone(slotS)}   // a refused payload never stays staged, so the next Save sends the server's value
 else if(confirm){slotS=d.state||slotS;slotDraft=slotClone(slotS);slotDiff=null;slotMsg=(d.diff||[]).length?"saved · the next run and gate read it":"nothing changed"}
 else if((d.diff||[]).length)slotDiff=d.diff;else{slotDraft=slotClone(slotS);slotDiff=null;slotMsg="nothing changed"}}catch(x){slotMsg="!save failed"}
 limRender();return d}
$("#lbody").addEventListener("input",e=>{limMsg="";const n=e.target.dataset&&e.target.dataset.kan,sp=e.target.dataset&&e.target.dataset.slot;   // a Kanban row, a slot cap or a limit field saves on its own (#2432, #2433, #2435)
 if(n&&kanDraft&&kanStage(n,e.target.value))limQueue("kan:"+n)
 else if(sp&&slotDraft){if(slotStage(sp,e.target.value))limQueue("slot:"+sp)}   // a cleared or out-of-range cap is refused here, never queued for a write the hub would refuse (#2435)
 else if(e.target.id&&LIM_FIELD[e.target.id])limQueue(LIM_FIELD[e.target.id]);   // a pause in typing saves the field (#2433)
 setBar()});
$("#lbody").addEventListener("change",e=>{const n=e.target.dataset&&e.target.dataset.kan,sp=e.target.dataset&&e.target.dataset.slot,key=n?"kan:"+n:sp?"slot:"+sp:(e.target.id&&LIM_FIELD[e.target.id])||null;   // the blur saves its own field at once and leaves every other field's timer alone (#2433)
 if(!key)return;
 if(LIM_FIELD[e.target.id]==="factor"&&e.target.value===""&&+((limS||{}).factor||0)!==0)return;   // a phone reports a partly typed number as empty: no change, the pause timer keeps the text (#2433)
 clearTimeout(limTmr[key]);
 delete limTmr[key];
 limWrite(key).catch(()=>{})});
/* Settings > Communication (SPEC 2682): the theme reads the committed `slack.yaml` and the profile's `slack.local.yaml`
   and saves both over one route. The view constructs and binds; the hub validates, diffs and writes (ADR-0137,
   ADR-0177). The hub holds no token. The old per-profile settings file and its route are retired (#2701). */
let slS=null,slD=null,slMsg="",slSavedDocs=null,slDocs=null,slOpen="",slPv=null;   // slS: the read view; slDocs: the two raw files the save hands back (#2694); slOpen: the open workspace or channel detail (#2696); slPOpen: the open persona detail (#2697)
const SL_MODE=["listen","listen+speak"],SL_SPEAK=["draft","send"],SL_TR=["off","local","gemini"],SL_PMOD=["two_state","exponential"];
const SL_INHERIT="inherit";
const slA=t=>esc(t).replace(/"/g,"&quot;");
const slClone=o=>JSON.parse(JSON.stringify(o));
const slDirty=()=>!!slDocs&&JSON.stringify(slDocs)!==JSON.stringify(slSavedDocs);
const slOpts=(list,cur)=>list.map(x=>`<option${x===cur?" selected":""}>${slA(x)}</option>`).join("");
const slPlural=(n,w)=>`${n} ${w}${n===1?"":"s"}`;
const slStatusText=r=>{if(!r)return "";
 const c=(r.tree&&r.tree.counts)||null;   // the tree counts what every profile the hub knows holds (#2827)
 return c?`${slPlural(c.clients,"client")} · ${slPlural(c.projects,"project")} · ${slPlural(c.channels,"channel")}`
  :`${slPlural((r.workspaces||[]).length,"workspace")} · ${(r.workspaces||[]).reduce((n,w)=>n+((w.channels||[]).length),0)} channels`};   // one text for the chip and the nav line (#1284)   // one text for the chip and the nav line (#1284)
/* The read view over the two Slack files (SPEC 2682 #2693): the Now tiles and the sections Personas, Roles, Slack
   workspaces and Project events. The view constructs and binds: it holds no rule of resolution and reads no file
   (ADR-0137); the hub hands every resolved value in, and each row names the file it comes from. */
let slR=null;
const slTag=f=>`<span class=slfile title="this value is written in ${slA(f)}">${esc(f)}</span>`;
const slSec=(name,file,head,rows,empty)=>`<section class=slsec><h4>${esc(name)} ${slTag(file)}</h4>${rows.length
 ?`<table class=slt2><thead><tr>${head.map(x=>`<th>${esc(x)}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table>`
 :`<p class=m>${esc(empty)}</p>`}</section>`;
/* The events editor (SPEC 2682 #2698). The owner declares the project events and one template for each, the announce
   list of each channel, and a template override for one event in one channel. Every edit writes the raw committed
   document the save path posts (#2694), and the policy module refuses an unknown event and a token (ADR-0177). A row is
   addressed by the place it holds in its own document, and every edit names the document it writes: `c` for the
   committed file, `l` for the local one. */
const slEvDoc=()=>{const c=(slDocs||{}).committed;return c&&Array.isArray(c.events)?c.events:[]};
const slEvId=i=>{const e=slEvDoc()[i];return e?String(e.id==null?"":e.id):""};
function slEvAdd(id){const v=String(id||"").trim(),c=(slDocs||{}).committed;if(!v||!c)return false;
 if(!Array.isArray(c.events))c.events=[];
 if(c.events.some(e=>e&&String(e.id)===v))return false;c.events.push({id:v,template:""});return true}
function slEvRemove(i){const c=(slDocs||{}).committed;if(!c||!Array.isArray(c.events)||!c.events[i])return;c.events.splice(i,1)}
function slEvRename(i,to){const ev=slEvDoc()[i];if(!ev)return;const was=String(ev.id==null?"":ev.id);ev.id=to;
 if(!was||was===to)return;   // the id is the key announce lists and override maps hold, so a rename carries them along
 for(const d of [(slDocs||{}).committed||{},(slDocs||{}).local||{}])
  for(const w of Array.isArray(d.workspaces)?d.workspaces:[]){if(!w||typeof w!=="object")continue;
   for(const ch of Array.isArray(w.channels)?w.channels:[]){if(!ch||typeof ch!=="object")continue;
    if(Array.isArray(ch.announce)&&ch.announce.some(x=>String(x)===was))ch.announce=ch.announce.map(x=>String(x)===was?to:x);
    if(ch.templates&&typeof ch.templates==="object"&&was in ch.templates){ch.templates[to]=ch.templates[was];delete ch.templates[was]}}}}
function slChanAt(f,w,c){const d=f==="l"?(slDocs||{}).local:(slDocs||{}).committed||{};
 const ws=Array.isArray(d.workspaces)&&d.workspaces[w];return ws&&Array.isArray(ws.channels)?ws.channels[c]:null}
function slAnnSet(f,w,c,i,on){const ch=slChanAt(f,w,c),id=slEvId(i);if(!ch||typeof ch!=="object"||!id)return;
 const list=Array.isArray(ch.announce)?ch.announce.slice():[],at=list.map(String).indexOf(id);
 if(on&&at<0)list.push(id);else if(!on&&at>=0)list.splice(at,1);
 if(list.length)ch.announce=list;else delete ch.announce}
function slAnnOverride(f,w,c,i,text){const ch=slChanAt(f,w,c),id=slEvId(i);if(!ch||typeof ch!=="object"||!id)return;
 const map=ch.templates&&typeof ch.templates==="object"?ch.templates:{};
 if(String(text||"").trim())map[id]=text;else delete map[id];
 if(Object.keys(map).length)ch.templates=map;else delete ch.templates}
const slEvChannels=()=>{const r=slR,out=[];
 for(const w of (r&&r.workspaces)||[])for(const c of w.channels)out.push({f:w.file===r.files.local?"l":"c",w:w.index,c:c.index,ch:c,wl:w.label});
 return out};
function slEvHtml(){const evs=slEvDoc();
 if(!(slDocs&&slDocs.committed)){const rows=((slR&&slR.events)||[]);   // a read with no documents stays read-only
  return rows.length?`<table class=slt2><thead><tr><th>Event</th><th>Template</th></tr></thead><tbody>${rows.map(x=>`<tr><td><b>${esc(x.id)}</b></td><td><code>${esc(x.template)}</code></td></tr>`).join("")}</tbody></table>`
   :`<p class=m>${esc("No event: the agent announces nothing.")}</p>`}
 const chans=slEvChannels(),announces=id=>{let n=0;for(const x of chans){const ch=slChanAt(x.f,x.w,x.c)||{};if(Array.isArray(ch.announce)&&ch.announce.map(String).indexOf(id)>=0)n++}return n};
 const rows=evs.map((ev,i)=>{const id=String(ev&&ev.id!=null?ev.id:""),n=slA(id||"event");
  const subs=chans.map(x=>{const ch=slChanAt(x.f,x.w,x.c)||{},
   on=Array.isArray(ch.announce)&&ch.announce.map(String).indexOf(id)>=0,
   ov=ch.templates&&typeof ch.templates==="object"&&ch.templates[id]!=null?String(ch.templates[id]):"";
   return `<tr><td>#${esc(x.ch.name)} <span class=m>${esc(x.wl)}</span> <span class=slfile>${esc(x.ch.file)}</span></td>`
   +`<td><label class=swt><input type=checkbox data-sl="an:${x.f}:${x.w}:${x.c}:${i}"${on?" checked":""} aria-label="${slA(x.ch.name)}: announces ${n}"> announces</label></td>`
   +`<td><input data-sl="ov:${x.f}:${x.w}:${x.c}:${i}" value="${slA(ov)}" placeholder="the project template" aria-label="${slA(x.ch.name)}: template for ${n}"></td></tr>`}).join("");
  return `<div class=slev><div class=slevr role=row><span class=c1 role=cell><input data-sl="ev:${i}:id" value="${slA(id)}" aria-label="event ${i+1} id" size=16><button data-slevrm="${i}" aria-label="remove event ${n}">Remove</button></span>`
   +`<span role=cell data-l="template"><textarea data-sl="ev:${i}:tpl" rows=2 aria-label="${n} template">${esc(ev&&ev.template!=null?String(ev.template):"")}</textarea></span></div>`
   +`<details class=slov><summary>Channels of ${esc(id||"this event")} <span class=m>${announces(id)} announce</span></summary>`
   +`<table class=slt2><thead><tr><th>Channel</th><th>Announce</th><th>Template override</th></tr></thead><tbody>${subs}</tbody></table></details></div>`}).join("");
 return (rows||`<p class=m>${esc("No event: the agent announces nothing.")}</p>`)
  +`<div class=slad><input id=slevnew placeholder="event id" aria-label="event to add"><button id=slevadd>Add event</button></div>`}
function slReadHtml(r){if(!r)return "";
 const cf=r.files.committed,lf=r.files.local,cell=c=>Array.isArray(c)?`<td data-l="${slA(c[0])}">${c[1]}</td>`:`<td>${c}</td>`,
 row=cells=>`<tr>${cells.map(cell).join("")}</tr>`,join=(xs,or)=>xs.length?xs.map(x=>esc(x)).join(", "):or,   // a narrow screen stacks a cell under its own label (data-l): a column loses no word
 ban=(r.defects||[]).length?`<p class="mx err" role=status>${r.defects.map(d=>esc(d)).join(" ")}</p>`:"";   // a defect is a message above the sections, never a page of its own
 let h=`<div class=setblk data-blk=read data-theme=slk>${ban}`;   // the project node's detail holds this block, under its own Settings (#2906)
 h+=slPersonaSection(r);
 h+=(slDocs?`<section class=slsec><h4>Roles <span class=slfiles>${slTag(cf)} ${slTag(lf)}</span></h4>${slRolesEditHtml(r)}</section>`
  :slSec("Roles",cf,["Role","Actions"],
   r.roles.map(x=>row([["Role",`<b>${esc(x.id)}</b>`],["Actions",join(x.actions,"no action")]])),
   "No role: only the owner can trigger the agent."));
 h+=`<section class=slsec><h4>Slack workspaces <span class=slfiles>${slTag(cf)} ${slTag(lf)}</span></h4>`
 +(slDocs?slEditHtml():slWsReadHtml(r,join))+`</section>`;
 h+=`<section class=slsec><h4>Project events ${slTag(cf)}</h4>${slEvHtml()}</section>`;   // the one editable section (#2698)
 return h+`</div>`}
/* The Slack detail references the personas and never edits one (#3036): the Personas theme edits a persona's name,
   voice and tone once for every client, and the tree's own Persona settings choose one for the project, a workspace or a
   channel. The section lists what the project declares, and each name opens its row in the Personas theme. */
const slDoc=(d,k)=>((d&&d[k])||{});
const slPersonaLists=d=>slDoc(d,"committed").personas||[];
function slPreviewSection(){const pv=slPv||(slR&&slR.previews)||[];
 return `<h5 class=slhs>Reply preview</h5>`+(pv.length?`<table class=slt2><thead><tr><th>Channel</th><th>Mark</th><th>Language</th><th>Tone</th><th>Voice</th></tr></thead><tbody>`
 +pv.map(p=>`<tr><td data-l="Channel"><span class=nw>${esc(p.channel)}</span></td><td data-l="Mark"><span class=nw>${esc(p.mark||"none")}</span></td><td data-l="Language"><span class=nw>${esc(p.language)}</span></td><td data-l="Tone">${esc(p.tone||"none")}</td><td data-l="Voice"><span class=nw>${esc(p.voice||"none")}</span></td></tr>`).join("")
 +`</tbody></table>`:`<p class=m>${esc("No channel: the agent replies nowhere.")}</p>`)}
function slPersonaSection(r){const cf=r.files.committed,ps=r.personas||[],lists=slPersonaLists(slDocs),use=lists.length?lists:ps,byId={};
 for(const p of ps)byId[String(p.id)]=p;
 const proj=(slTHere()||{}).name||"";
 const rows=use.map(p=>{const o=byId[String(p.id)]||{},used=(o.channels||[]).length?o.channels.map(esc).join(", "):"no channel";
  return `<tr><td data-l="Persona"><span class=nw><a href="#set-prs" data-sp=prs data-prfocus="${slA(p.id)}" data-prproj="${slA(proj)}" title="${slA("Edit in the Personas theme")}"><b>${esc(p.name)}</b></a> <span class=m>${esc(p.id)}</span></span></td><td data-l="Voice"><span class=nw>${esc(p.voice)}</span></td><td data-l="Used by">${used}</td></tr>`});
 return `<section class=slsec><h4>Personas ${slTag(cf)}</h4>`
 +`<p class=sltjump>${esc("Names, voices and tones are edited in the ")}<a href="#set-prs" data-sp=prs>${esc("Personas theme")}</a>${esc(". The Persona setting of the project, a workspace or a channel chooses one.")}</p>`
 +(use.length?`<table class=slt2><thead><tr><th>Persona</th><th>Voice</th><th>Used by</th></tr></thead><tbody>${rows.join("")}</tbody></table>`:`<p class=m>${esc("No persona: the project declares no identity.")}</p>`)
 +slPreviewSection()+`</section>`}
const slReadTiles=r=>!r?[]:r.now.map(t=>setTile(t.key,t.value,t.tone));
/* The read-only workspace rows (#2693): a theme that could not read the raw documents draws these alone. With the
   documents in hand, the editor draws the same rows and adds the controls (#2696). The two tables draw one header list:
   the read table keeps the base columns, and the editor adds the guest columns and the Edit column at the end. */
const slChHeads=["Channel","Mode","Language","Persona","Roles","Announce"];
const slEditHeads=guest=>(guest?["Channel","Mode","Language","Persona","Answers","Polling","Roles","Announce"]:slChHeads).concat("");
function slWsReadHtml(r,join){return (r.workspaces.length?"":`<p class=m>${esc("No Slack workspace: the agent reads no channel.")}</p>`)
 +r.workspaces.map(w=>`<h5 class=slws>${esc(w.label)} <span class="sltag ${slA(w.class)}">${esc(w.class)}</span> ${slTag(w.file)}</h5>`
  +(w.channels.length?`<table class=slt2><thead><tr>${slChHeads.map(x=>`<th>${x}</th>`).join("")}</tr></thead><tbody>`
   +w.channels.map(c=>`<tr><td data-l="Channel">#${esc(c.name)} <span class=m>${esc(c.id)}</span></td><td data-l="Mode"><span class=nw>${esc(c.mode)}</span></td><td data-l="Language"><span class=nw>${esc(c.language)}</span></td><td data-l="Persona"><span class=nw>${esc(c.persona||"none")}</span></td><td data-l="Roles">${join(c.roles,"owner only")}</td><td data-l="Announce">${join(c.announce,"never posts")}</td></tr>`).join("")
   +`</tbody></table>`:`<p class=m>${esc("No channel in this Slack workspace.")}</p>`)).join("")}
function slKeepFocus(id){if(!id||typeof document==="undefined"||!document.getElementById)return false;const el=document.getElementById(id);if(el&&el.focus){el.focus();return true}return false}   // the control the owner changed keeps the keyboard (#2846)
function slRender(){const b=$("#sbody");if(!b)return;if(!slR&&!slS){b.innerHTML="<span class=m>Loading the data.</span>";return}
 const st=slStatusText(slR);setChip("slk",st);const nv=$("#slknav");if(nv)nv.textContent=st;   // a status: the render writes the one text to the chip and the nav line (#1284)
 if(!slT&&slR&&slR.tree)slT=slR.tree;   // a fresh read plants the tree; a staged edit keeps its own until the save (#2846)
 if(slT&&!slTNode(slTsel))slTsel=(slTRoots()[0]||{}).id||"";   // a reload that dropped the selected node falls back to the first client
 const keep=(typeof document!=="undefined"&&document.activeElement&&document.activeElement.id)||"";   // a redraw replaces the pane's markup, so the control that had the keyboard takes it back (#2846)
 let h=slTreeHtml()+(slTHere()?"":slReadHtml(slR));   // the project's personas, roles and events live in its own node (#2906)
 if(slMsg&&!slDocs)h+=`<div class="mx${slMsg.startsWith("!")?" err":""}" role=status>${esc(slMsg.replace(/^!/,""))}</div>`;   // with the documents in hand the editor carries the message (#2696)
 b.innerHTML=h;slTBind();if(slTF||!slKeepFocus(keep))slTFocus();slAutoSchedule();slBarSync()}   // each edit saves by itself (#2908)
/* The Communication tree (SPEC 2682 #2824, #2827): Client > Project > Workspace > Channel, with the selected node's state
   in a Now block of tiles. The hub answers every value from the policy module and the client map module; the view
   constructs, binds and moves the focus, and holds no rule of the Slack files (ADR-0137, ADR-0177). One tab stop, Arrow
   keys, Home and End. */
let slT=null,slTsel="",slTopen={},slTF="",slTshow=false,slProposeT=null,slConfirm=null;   // slConfirm: the hub's one question before a remove or a delete (#2922)
function slTRoots(){if(!slT)return [];return slT.clients||(slT.root?[slT.root]:[])}
function slTHere(){const walk=n=>!n?null:n.kind==="project"&&n.here?n:((n.children||[]).map(walk).find(Boolean)||null);
 for(const r of slTRoots()){const hit=walk(r);if(hit)return hit}return null}
/* A project node's own files (#2906): the page's project carries its personas, roles, workspaces and events under its
   Settings; another project names the page that edits them, so no section reads as global. */
function slTProject(n){if(!n||n.kind!=="project")return "";
 if(n.here)return slReadHtml(slR);
 // Its Persona and Language are edited on this node (#3033); its persona definitions are edited once for every client
 // in the Personas theme, and its roles and events stay on its own page (#3016).
 return n.hub?`<p class=slttext>${esc("Its personas are edited in the ")}<a href="#set-prs" data-sp=prs>${esc("Personas theme")}</a>${esc(". Its roles and events are edited on ")}<a href="${slA(n.hub)}">${esc(n.name)}${esc("'s page")}</a>.</p>`:""}
const slTNode=id=>{const find=n=>!n?null:n.id===id?n:((n.children||[]).map(find).find(Boolean)||null);
 for(const r of slTRoots()){const hit=find(r);if(hit)return hit}return null};
const slTOpen=n=>!!(n.children&&n.children.length)&&slTopen[n.id]!==false;
const slTPath=id=>{const path=[];const find=(n,acc)=>{if(n.id===id){path.push(...acc,n);return true}
 return (n.children||[]).some(c=>find(c,acc.concat([n])))};for(const r of slTRoots())if(find(r,[]))break;return path};
function slTFlat(){const out=[],walk=(n,parent)=>{out.push({id:n.id,parent:parent,kids:!!(n.children&&n.children.length),open:slTOpen(n)});
 if(slTOpen(n))for(const c of n.children||[])walk(c,n.id)};for(const r of slTRoots())walk(r,null);return out}
function slTreeKey(f,cur,key){const i=f.findIndex(x=>x.id===cur),n=f[i]||{},last=f.length-1;
 if(key==="ArrowDown")return i>=0&&i<last?{focus:f[i+1].id}:null;   // the next visible node, or nothing at the end
 if(key==="ArrowUp")return i>0?{focus:f[i-1].id}:null;
 if(key==="Home")return f.length?{focus:f[0].id}:null;
 if(key==="End")return f.length?{focus:f[last].id}:null;
 if(key==="ArrowRight"){if(!n.kids)return null;if(!n.open)return {focus:cur,toggle:cur};const k=f[i+1];return k&&k.parent===cur?{focus:k.id}:null}
 if(key==="ArrowLeft"){if(n.kids&&n.open)return {focus:cur,toggle:cur};return n.parent?{focus:n.parent}:null}
 return null}
function slTFlush(){if(slAutoT)slAutoSave();if(slProposeT){clearTimeout(slProposeT);slProposeT=null;slPropose(slTsel,true)}}   // leaving a node saves its pending edit at once (#2908)
function slTGo(r){if(!r)return;if(r.focus&&r.focus!==slTsel){slTFlush();slConfirm=null}   // a question belongs to its pane, never to the next one (#2922)
 if(r.toggle)slTopen[r.toggle]=!slTOpen(slTNode(r.toggle));   // an untouched node reads as open, so the fold must flip that reading
 if(r.focus)slTsel=r.focus;slTF=r.focus||slTsel;slTshow=false;slRender()}
function slTPick(id){if(id!==slTsel){slTFlush();slConfirm=null}   // a question belongs to its pane, never to the next one (#2922)
 slTsel=id;slTF=id;slTshow=slTNarrow();slRender()}   // only a narrow panel folds the tree on a pick
function slTKey(e){const r=slTreeKey(slTFlat(),slTsel,e.key);if(!r)return;e.preventDefault();e.stopPropagation();slTGo(r)}
const slTNarrow=()=>{const el=typeof document!=="undefined"&&document.getElementById&&document.getElementById("sbody");
 return !!el&&!!el.getBoundingClientRect&&el.getBoundingClientRect().width<=700};
function slTBack(){slTshow=false;slRender();const el=typeof document!=="undefined"&&document.getElementById&&document.getElementById("slt_"+slTsel);if(el&&el.focus)el.focus()}
function slTFocus(){const id=slTF||slTsel;slTF="";if(typeof document==="undefined"||!document.getElementById)return;
 const el=document.getElementById("slt_"+id);
 if(el&&el.focus&&(!el.getClientRects||el.getClientRects().length)){el.focus();return}   // a folded item has no box and takes no focus
 const d=document.getElementById("sltdetail");if(d&&d.focus)d.focus()}   // a fold, a pick and a crumb keep the keyboard on the tree or the detail pane
function slSetup(kind){const el=typeof document!=="undefined"&&document.querySelector&&document.querySelector("[data-slwsclass]");
 if(el){el.value=kind;if(el.scrollIntoView)el.scrollIntoView({block:"center"});el.focus()}else{slMsg="!The workspace editor opens when the Slack files are readable.";slRender()}}
function slTBind(){if(typeof document==="undefined"||!document.querySelectorAll)return;
 for(const el of document.querySelectorAll("[data-slt]"))el.addEventListener("click",e=>{const id=el.getAttribute("data-slt"),n=slTNode(id);
  if(e.target&&e.target.closest&&e.target.closest(".sltarrow")&&n&&n.children&&n.children.length){slTGo({focus:id,toggle:id});return}slTPick(id)});
 for(const el of document.querySelectorAll("[data-slt-setup]"))el.addEventListener("click",()=>slSetup(el.getAttribute("data-slt-setup")));
 for(const el of document.querySelectorAll("[data-slcask]"))el.addEventListener("click",()=>slMapAsk(el.getAttribute("data-slcask")));   // one question before a remove or a delete (#2922)
 for(const el of document.querySelectorAll("[data-slcconfirm]"))el.addEventListener("click",()=>slMapConfirm());
 for(const el of document.querySelectorAll("[data-slccancel]"))el.addEventListener("click",()=>slMapKeep());
 for(const el of document.querySelectorAll("[data-slcaddproj]"))el.addEventListener("click",()=>slMapAddProject(el.getAttribute("data-slcaddproj")));
 for(const el of document.querySelectorAll("[data-slcadd]"))el.addEventListener("click",()=>slMapAddClient());
 const sel=typeof document!=="undefined"&&document.getElementById&&document.getElementById("slclpersona");
 if(sel)sel.addEventListener("change",slClientPick);   // a client's default saves its project writes by itself (#2908)
 for(const el of document.querySelectorAll("[data-slt-tree]"))el.addEventListener("click",slTBack);
 for(const el of document.querySelectorAll("[data-slt-copy]"))el.addEventListener("click",()=>{const id=el.dataset.sltCopy;   // #2912
  const say=ok=>{el.textContent=ok?"copied":"copy failed";setTimeout(()=>{el.textContent="copy id"},1500);return ok};
  const legacy=()=>{try{const a=document.createElement("textarea");a.value=id;a.setAttribute("readonly","");a.style.position="fixed";a.style.opacity="0";document.body.appendChild(a);a.select();const ok=!!(document.execCommand&&document.execCommand("copy"));document.body.removeChild(a);return say(ok)}catch(e){return say(false)}};   // a plain http hub has no clipboard API, so the copy still runs
  try{if(navigator.clipboard&&navigator.clipboard.writeText)return void navigator.clipboard.writeText(id).then(()=>say(true),legacy)}catch(e){}
  legacy()});
 for(const el of document.querySelectorAll("[data-slp-sender]"))el.addEventListener("change",()=>slPvAsk(el.getAttribute("data-slp-sender"),el.value));
 for(const el of document.querySelectorAll("[data-slt-perm]"))el.addEventListener("click",e=>{e.preventDefault();slPermGo()});
 const tree=typeof document!=="undefined"&&document.getElementById&&document.getElementById("sltree");if(tree)tree.addEventListener("keydown",slTKey)}
const slTBadge=b=>`<span class="sltb ${esc(b.tone||"neutral")}">${esc(b.text)}</span>`;
/* The level icons (mockup, #2843): an office building for the project and a folder for a workspace, both drawn in the
   row's own colour. A channel shows the chat bubble of a guest channel or a direct message and the hash otherwise; the
   hub names which one in the node's `icon`. */
const slTKinds={
 project:`<svg class=sltkind viewBox="0 0 16 16" aria-hidden=true fill=none stroke=currentColor stroke-width=1.3 stroke-linejoin=round><path d="M2.5 13.5V3h4.5v10.5"/><path d="M7 13.5V6h6.5v7.5"/><path d="M1.5 13.5h13"/><path d="M4 5.5h1M4 8h1M4 10.5h1M9.5 8.5h1M9.5 11h1"/></svg>`,
 workspace:`<svg class=sltkind viewBox="0 0 16 16" aria-hidden=true fill=none stroke=currentColor stroke-width=1.3 stroke-linejoin=round><path d="M2 4.7A1.7 1.7 0 0 1 3.7 3h2.6l1.6 2h4.4A1.7 1.7 0 0 1 14 6.7v4.6A1.7 1.7 0 0 1 12.3 13H3.7A1.7 1.7 0 0 1 2 11.3z"/></svg>`,
 chat:`<svg class=sltkind viewBox="0 0 16 16" aria-hidden=true fill=none stroke=currentColor stroke-width=1.3 stroke-linejoin=round><path d="M13.5 8.4a4.7 4.7 0 0 1-5.1 4.6 5.7 5.7 0 0 1-1.7-.3L3.3 14l.8-2.5A4.4 4.4 0 0 1 2.9 8.4 4.7 4.7 0 0 1 8 3.8a4.7 4.7 0 0 1 5.5 4.6z"/></svg>`,
 hash:`<span class=sltkind aria-hidden=true>#</span>`,
 client:`<svg class=sltkind viewBox="0 0 16 16" aria-hidden=true fill=none stroke=currentColor stroke-width=1.3 stroke-linejoin=round><path d="M2.5 5.5h11v8h-11z"/><path d="M6 5.5V4a1 1 0 0 1 1-1h2a1 1 0 0 1 1 1v1.5"/><path d="M2.5 9h11"/></svg>`};
const slTGlyph=n=>n.kind==="channel"?(n.icon==="chat"?slTKinds.chat:slTKinds.hash):n.kind==="workspace"?slTKinds.workspace:n.kind==="client"?slTKinds.client:slTKinds.project;
/* The indent guides of one row, one per ancestor level: the line of a level draws only while that ancestor has another
   sibling after it, so a subtree's rule ends at its last child and never runs past it (mockup, #2843). */
const slTGuides=more=>more.map(m=>`<span class="sltg${m?" more":""}" aria-hidden=true></span>`).join("");
/* #2955: the hub's own Slack read names each mapped channel its workspace does not list, in the hub's words; the tree
   draws that sentence as the channel's badge, so a channel Slack cannot reach never reads as a live one. */
function slTGone(n){if(!n||n.kind!=="channel"||!n.ids)return null;
 const l=slChList[String(n.ids.workspace||"")],list=l&&Array.isArray(l.unreachable)?l.unreachable:[];
 const hit=list.find(g=>String(g.id)===String(n.ids.channel));
 return hit?{text:String(hit.text||""),tone:"err",kind:"unreachable"}:null}
function slTItem(n,cur,more){const open=slTOpen(n),kids=!!(n.children&&n.children.length),sel=n.id===cur;
 const gone=slTGone(n),badges=(n.badges||[]).concat(gone?[gone]:[]);
 const label=[n.name].concat(badges.map(b=>b.text)).join(", ");   // the accessible name carries the badges
 return `<div role=treeitem id="slt_${slA(n.id)}" aria-level="${n.level}" data-slt="${slA(n.id)}"${kids?` aria-expanded="${open?"true":"false"}"`:""}${sel?' aria-selected="true" tabindex="0"':' tabindex="-1"'} aria-label="${slA(label)}" class="slti lv${n.level}${sel?" sel":""}">`
 +slTGuides(more||[])+`<span class=sltarrow aria-hidden=true>${kids?(open?"▾":"▸"):""}</span>${slTGlyph(n)}`
 +`<span class=sltmain><span class=sltname title="${slA(n.name)}">${esc(n.icon==="hash"?String(n.name).replace(/^#/,""):n.name)}</span><span class=sltbadges>${badges.map(slTBadge).join("")}</span></span></div>`}
function slTConv(n){const bubble=x=>`<div class="slcb ${slA(x.side)}${x.tone?" "+slA(x.tone):""}">${x.who?`<span class=slcbn>${esc(x.who)}</span>`:""}<span class=slcbt>${esc(x.text)}</span></div>`;
 return `<div class=setblk data-blk=now data-theme=slk><h3 class=blkh>What happens here</h3><div class=slconv>${n.conversation.map(bubble).join("")}</div>`
 +`<details class=slcdet><summary>Details</summary>${slTTiles(n)}${slTNoteHtml(n)}</details></div>`}
// A channel tells its conversation first and keeps its tiles under Details (#2917); every other node shows its tiles.
function slTNow(n){return Array.isArray(n.conversation)&&n.conversation.length?slTConv(n):slTTiles(n)}
function slTTiles(n){const tiles=(n.now||[]).map(t=>`<div class="tile ${esc(t.tone||"neutral")}"><span class=tk>${esc(t.key)}</span><span class=tv>${esc(t.value)}</span>${t.sub?`<span class=tss>${esc(t.sub)}</span>`:""}</div>`);
 const cols=Math.min(tiles.length,5);   // the tile count names the row's columns: five for a channel, four for a workspace (#2841)
 return tiles.length?`<div class=setblk data-blk=now data-theme=slk><h3 class=blkh>Now</h3><div class="now sltnow sltn${cols}">${tiles.join("")}</div></div>`:""}
// A channel that tells its conversation keeps its note under Details, beside the tiles it explains (#2917).
function slTNote(n){return Array.isArray(n.conversation)&&n.conversation.length?"":slTNoteHtml(n)}
function slTNoteHtml(n){const t=(n.detail||{}).note;   /* the channel's one sentence under its tiles (#2842) */
 return t?`<p class=sltnote><span class=sltni aria-hidden=true>ⓘ</span> ${esc(t)}</p>`:""}
/* The guest slot line (#2842): one plain line when the machine serves one guest workspace, and an alert with its title
   and its sentence when a second one is refused. Every value is text. */
function slTSlot(s){if(!s)return "";
 return s.tone==="err"&&s.title
  ?`<div class="sltguest ${slTTone(s.tone)}" role=status><span class=sltgi aria-hidden=true>⚠</span><div><b>${esc(s.title)}</b><br>${esc(s.text||"")}</div></div>`
  :(s.line?`<p class="sltguest ${slTTone(s.tone)}">${esc(s.line)}</p>`:"")}
function slTSetup(n){const setup=n.setup||[];if(!setup.length)return "";
 return `<div class=sltempty><p class=slttext>${esc((n.detail||{}).text||"This project has no Slack files yet.")}</p><div class=sltacts><span>${esc("Start with a workspace:")}</span>`
 +setup.map(c=>`<button type=button data-slt-setup="${slA(c.id)}">${esc(c.label)}</button>`).join("")+`</div>`
 +`<p class="m">${esc("owned: the bot posts in your workspace · guest: the persona writes from your account in a client's")}</p></div>`}
function slClientPick(){clearTimeout(slProposeT);slProposeT=setTimeout(()=>{slProposeT=null;slPropose(slTsel,true)},SL_COMMIT_MS);slBarSync()}   // one burst of picks is one save of each project (#2908)
const slTRefused=n=>{const bad=(n.proposal||[]).filter(r=>r.action==="refused");if(!bad.length)return "";   // only a project the save could not write is named (#2908)
 return `<div class="mx err sltbad" role=status><b>${esc("Not written")}</b><ul>${bad.map(r=>`<li>${esc(r.label||r.project)}: ${esc(String(r.result||"").replace(/^refused: /,""))}</li>`).join("")}</ul></div>`};
const slTClient=n=>{if(n.kind!=="client"||n.own)return "";   // the page of one unnamed project draws no client map chrome (#2922)
 const opts=n.persona_options||[],cur=n.default_persona||"";
 const sel=`<select id=slclpersona aria-label="${slA("Default persona")}">${opts.map(o=>`<option value="${slA(o.id)}"${o.id===cur?" selected":""}>${esc(o.name)}</option>`).join("")}</select>`;
 // The Add project control lists the hub's free projects by name, and Delete client asks the hub's one question (#2922).
 const free=n.client?(n.add_projects||[]):[];   // a node the map does not name has no map to change (#2922)
 const add=!n.client?"":(free.length
  ?`<div class="slad sltadd"><select id=slclproject aria-label="${slA("Project to add")}">${free.map(p=>`<option value="${slA(p.id)}">${esc(p.name)}</option>`).join("")}</select><button type=button data-slcaddproj="${slA(n.id)}">${esc("Add project")}</button></div>`
  :`<p class=m>${esc("No project waits to be added.")}</p>`);
 const projects=`<div class=setblk><h3 class=blkh>${esc("Projects")}</h3><div class=sltprojs>`+(n.children||[]).map(p=>`<span class=sltproj>${slTGlyph(p)}${esc(p.name)}${(p.badges||[]).map(slTBadge).join("")}</span>`).join("")+`</div>`+add+`</div>`;
 const del=n.ask?`<div class=sltacts><button type=button class=sltdanger data-slcask="${slA(n.id)}">${esc("Delete client")}</button></div>`:"";
 return `<div class=sltsethead><h3 class=blkh>${esc("Settings")}</h3><span class=slfile title="${slA("the profile folder")}">clients.local.yaml</span></div>`
 +`<div class=sltrow><span class=sl>${esc("Default persona")}</span><span class=sc>${sel}</span></div>`
 +`<p class=sltjump><a href="#set-prs" data-sp=prs>${esc("Open the Personas theme")}</a></p>`   // the theme edits the personas of every client (#3016)
 +slTRefused(n)+projects+del};
/* The project's one control (#2922): Remove from client. The question it shows is the hub's, and it names the project
   and the client. */
const slTClientActions=n=>n.kind!=="project"||!n.ask?""
 :`<div class=sltacts><button type=button data-slcask="${slA(n.id)}">${esc("Remove from client")}</button></div>`;
/* The one question a change asks (#2922): the hub's text, with Confirm and Keep. The question a node carries needs no
   post; the one the route answers for Add project or Add client lands on the node the owner is looking at. The question
   is bound to the id of the node it belongs to, so a switch to another node hides it, and the tree's empty pane draws the
   one question of a map with no client, where no node carries it (#2922). */
const slTConfirm=n=>{const c=slConfirm;if(!c)return "";if(c.id&&(!n||c.id!==n.id))return "";
 return `<div class="mx sltconfirm" role=alertdialog aria-label="${slA("Confirm")}"><p>${esc(c.text)}</p>`
 +`<div class=sltacts><button type=button data-slcconfirm>${esc("Confirm")}</button><button type=button data-slccancel>${esc("Keep")}</button></div></div>`};
/* The Add client control of the tree (#2922): a name and a default persona, and the hub makes the client from them. */
const slTNewPersonas=()=>(slT&&slT.new_client_personas)||[];
function slTAddClient(){const pers=slTNewPersonas();
 // A hub whose projects declare no persona draws the name alone: an empty select would offer a default persona that
 // does not exist, and the client joins the map with none (#2922).
 const sel=pers.length?`<select id=slcpersona aria-label="${slA("Default persona")}">${pers.map(o=>`<option value="${slA(o.id)}">${esc(o.name)}</option>`).join("")}</select>`:"";
 return `<div class="slad sltnew"><input id=slcname aria-label="${slA("Client name")}" placeholder="${slA("client name")}">`
 +sel+`<button type=button data-slcadd>${esc("Add client")}</button></div>`}
function slTDetail(){const n=slTNode(slTsel)||slTRoots()[0];if(!n)return "";
 const path=slTPath(n.id),crumb=path.map((x,i)=>`${i?'<span class=sp aria-hidden=true>/</span>':""}<button type=button data-slt="${slA(x.id)}">${esc(x.name)}</button>`).join("");
 const badges=(n.badges||[]).map(slTBadge).join(""),dnote=(n.detail||{}).defect_note||[],
  reasons=dnote.length?dnote:(n.defects||[]),
  defects=reasons.map(d=>`<p class="mx err sltdef" role=status>${esc(d)}</p>`).join("");   /* one plain reason per line (#2842) */
 const notice=n.notice?`<div class="mx err sltnotice" role=status><b>${esc(n.notice.title)}</b><br>${esc(n.notice.text)}</div>`:"";   // the second guest is bannered beside its project (#2827)
 return `<button type=button class=sltback data-slt-tree>${esc("Show the tree")}</button>`
 +`<nav class=sltcrumb aria-label="Breadcrumb">${crumb}</nav>`
 +(n.permissions?`<p class=sltjump><a href="#slperm" data-slt-perm>Permissions</a></p>`:"")
 +`<h3 class=slth>${esc(n.name)} ${badges}</h3>`
 +`<p class=sltsub>${esc((n.detail||{}).sub||"")}</p>`+(n.kind==="channel"&&n.ids&&n.ids.channel?`<p class=sltids><button type=button class=sltcopy data-slt-copy="${slA(n.ids.channel)}" title="${slA("Copy the Slack channel id")}">copy id</button>${n.open?` <a class=sltopen href="${slA(n.open)}" target=_blank rel=noopener>Open in Slack</a>`:""}</p>`:"")+((n.detail||{}).verdict?`<p class=sltverdict>${esc(n.detail.verdict)}</p>`:"")+`${notice}${defects}`+slTConfirm(n)
 +((n.detail||{}).text&&!(n.setup||[]).length?`<p class=slttext>${esc(n.detail.text)}</p>`:"")+slTSetup(n)+slTNow(n)+slTNote(n)+slTClient(n)+slTClientActions(n)+slTElsewhere(n)+slTChAdd(n)+slTSettings(n)+slTProject(n)+slTPerms(n)+(n.elsewhere?"":slTPreview(n))+slTChRm(n)}
/* A node of another profile's project is read-only here: the page edits its own project's files only (#2919). The
   workspace and the channel edit their Persona and Language here, so a channel names only its other settings as edited
   on the project's page (#3034). */
const slTElsewhere=n=>n&&n.elsewhere&&n.kind!=="project"?`<p class=slttext>${esc(n.kind==="channel"?"Its other settings are edited on ":"Edited on ")}<a href="${slA(n.elsewhere)}">${esc((slTPath(n.id).find(x=>x.kind==="project")||{}).name||"its project")}${esc("'s page")}</a>.</p>`:"";
/* Add and remove a channel from its own tree node (#2918). The hub names the row each edit writes and lists the
   workspace's channels by name, the ones the files name left out; the view draws the list and sends the edit. */
let slChList={},slChAsk={};
function slChNeed(a){if(typeof fetch!=="function"||!a)return;const ws=String(a.workspace||"");if(!ws||slChAsk[ws]||slChList[ws])return;slChAsk[ws]=true;
 fetch("/api/slack/channels",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({workspace:ws,named:a.named||[]})})
 .then(r=>r.json()).then(o=>{slChList[ws]=o||{err:"the channel list could not be read",manual:true,channels:[]};slChAsk[ws]=false;slRender()})
 .catch(()=>{slChList[ws]={err:"the channel list could not be read",manual:true,channels:[]};slChAsk[ws]=false;slRender()})}
/* The tree asks which mapped channels Slack still lists (#2955) and fills the add pane's list once the tree is read,
   never while it is painted: a render writes nothing, so a paint never opens a request of its own. The add target asks
   too, so a workspace that offers the pane and no read target still reads its list (#2957). */
function slChTree(){const walk=n=>{if(!n)return;if(n.kind==="workspace"){if(n.channel_read)slChNeed(n.channel_read);
 if(n.channel_add)slChNeed(n.channel_add)}(n.children||[]).forEach(walk)};slTRoots().forEach(walk)}
function slTChAdd(n){const a=n&&n.kind==="workspace"&&n.channel_add;if(!a)return "";const key=`${a.doc}:${a.ws}`,l=slChList[String(a.workspace||"")];
 const list=l&&Array.isArray(l.channels)?l.channels:[];
 const pick=list.length?`<div class=slad><select data-sltchpick="${slA(key)}" aria-label="channel to add">${list.map(c=>`<option value="${slA(c.id)}">#${esc(c.name)}</option>`).join("")}</select><button type=button data-sltaddch="${slA(key)}">Add channel</button></div>`
  :l&&!l.err?`<p class=m>Every channel this connection reads is already here.</p>`:!l?`<p class=m>Reading the channels…</p>`:"";
 const err=l&&l.err?`<p class="mx err" role=status>${esc(l.err)}</p>`:"";
 const ids=l&&l.manual?`<details class=sladv><summary>Slack ids</summary>${slAddCh(a.doc,a.ws)}</details>`:"";
 return `<section class="setblk sltchadd" aria-label="Add channel"><h3 class=blkh>Channels</h3>${pick}${err}${ids}</section>`}
function slTChRm(n){const r=n&&n.kind==="channel"&&n.channel_remove;if(!r)return "";
 return `<p class=sltrm><button type=button data-sltrmch="${slA(`${r.doc}:${r.ws}:${r.index}`)}" data-sltrmname="${slA(r.name)}">Remove channel</button></p>`}
/* The channel settings of the detail pane (#2844, #2845): one row per field, and the row names the file it writes in a
   chip. The hub decides the file, the controls and the refusal (ADR-0137); a control carries the channel's Slack id and
   the field, never the row's place in a list, so the save writes the row that carries the id. A control shows the value
   the staged document holds, and a poll field names the policy's default until the channel names its own (#2845). */
const slTOpt=o=>typeof o==="string"?{value:o,label:o}
 :{value:String((o||{}).value==null?"":o.value),label:String((o||{}).label==null?(o||{}).value:o.label)};
function slFieldOp(ch,field,el){const v=el&&el.type==="checkbox"?!!el.checked:(el&&el.value!==undefined?el.value:"");
 const k=String(ch);   // `project` and `ws:<id>` name a row above a channel; a Slack id never takes either shape (#2901)
 if(k==="project")return {kind:"field",scope:"project",field:String(field),value:v};
 if(k.indexOf("ws:")===0)return {kind:"field",scope:"workspace",workspace:k.slice(3),field:String(field),value:v};
 return {kind:"field",channel:k,field:String(field),value:v}}
/* The project a node belongs to (#3033): the nearest ancestor that names an edit, so a workspace or a channel below
   another profile's project reads that project's own file, never the page's. */
const slPoOwner=n=>{const path=n&&n.id?slTPath(n.id):[n];
 for(let i=path.length-1;i>=0;i--)if(path[i]&&path[i].edit&&path[i].edit.project)return path[i];return null};
function slTSettingVal(n,row,c){const f=String(c.field||""),ch=String((n.ids||{}).channel||""),sc=String(c.scope||""),
 // A node of another project draws the value its own staged documents hold (#3033), never the page's own files.
 owner=slPoOwner(n),docs=owner?slPoDocs(owner.edit.project):slDocsCur();
 if(sc==="project"){const p=(docs.committed||{}).project;return p&&p[f]!==undefined&&p[f]!==null?String(p[f]):""}
 if(sc.indexOf("ws:")===0){const id=sc.slice(3);
  for(const d of ["committed","local"])for(const w of Array.isArray((docs[d]||{}).workspaces)?docs[d].workspaces:[])
   if(w&&String(w.id)===id&&w[f]!==undefined&&w[f]!==null)return Array.isArray(w[f])?w[f].join(", "):String(w[f]);
  return ""}
 const doc=docs[row.doc];
 if(doc)for(const w of Array.isArray(doc.workspaces)?doc.workspaces:[])for(const x of Array.isArray(w&&w.channels)?w.channels:[])
  if(x&&String(x.id)===ch){const v=f.indexOf("poll.")===0?(x.poll||{})[f.slice(5)]:x[f];
   return v===undefined||v===null?"":Array.isArray(v)?v.join(", "):f==="voice_reply"?!!v:String(v)}
 return c.value===undefined||c.value===null?"":String(c.value)}
function slTSettingCtl(n,row,c){const ch=String((n.ids||{}).channel||""),sc=String(c.scope||""),f=String(c.field||""),
 tgt=ch||sc,   // the channel id, `project` or `ws:<id>`: the target the hub named for the row (#2901)
 own=slPoOwner(n),proj=own?String(own.edit.project):"",
 at=tgt&&f?`data-slf="${slA(tgt)}|${slA(f)}"${proj?` data-slproj="${slA(proj)}"`:""}`:"",   // an edit of another project names it (#3033)
 lab=slA(c.label||row.label||f),v=slTSettingVal(n,row,c),
 sv=v===undefined||v===null?"":String(v),
 id=`slf_${tgt}_${f}`,   // a stable id, so a redraw puts the keyboard back on the control the owner changed (#2846)
 ph=c.default?` placeholder="default: ${slA(c.default)}"`:(f.indexOf("poll.")===0?` placeholder="the policy default"`:"");
 if(c.kind==="read")return `<span class=slsetval>${esc(c.value||"no")}</span>`;
 if(c.kind==="check")return `<label class=slchk><input type=checkbox class=slsetctl id="${slA(id)}" ${at} aria-label="${lab}"${v?" checked":""}> ${esc(c.label||f)}</label>`;
 if(c.kind==="select")return `<select class=slsetctl id="${slA(id)}" ${at} aria-label="${lab}">`+(c.options||[]).map(slTOpt)
  .map(o=>`<option value="${slA(o.value)}"${o.value===sv?" selected":""}>${esc(o.label)}</option>`).join("")+`</select>`;
 return `<input type=text class=slsetctl id="${slA(id)}" ${at} aria-label="${lab}" value="${slA(sv)}"${ph}>`}
function slTSettings(n){const rows=((n.settings||{}).rows)||[];if(!rows.length)return "";
 return `<div class=setblk data-blk=settings data-theme=slk><h3 class=blkh>Settings</h3>`
 +rows.map(r=>{const cs=r.controls||[];
  return (cs.length?cs:[{kind:"read",value:""}]).map(c=>`<div class=slsetrow data-slrow="${slA(r.key)}">`
   +`<span class=slsetk>${esc(c.label||r.label)}</span><span class=slsetc>${slTSettingCtl(n,r,c)}</span>${r.file?slTag(r.file):""}</div>`).join("")
  +(r.note?`<p class=slsetnote>${esc(r.note)}</p>`:"")}).join("")+`</div>`}
/* The Permissions section and the sender preview of a channel (SPEC 2682 #2826). The rows, the counts and the
   decision are the hub's; the view draws them and asks the one read route for a preview, which posts nothing. */
let slSnd=null;   // the last sender preview: its channel node, its sender and its answer
const slPvSenders=n=>[...((n.permissions||{}).members||[]).map(u=>({v:u,t:u,kind:"member"})),{v:"stranger",t:"someone with no membership",kind:"stranger"},{v:"bot",t:"a bot",kind:"bot"},{v:"own",t:"the owner's own post",kind:"member",own:true}];
function slTPerms(n){const p=n.permissions;if(!p)return "";
 const rows=(p.roles||[]).map(r=>`<tr><td class=${r.accepted?"yes":"no"}>${r.accepted?"yes":"no"}</td><td>${esc(r.id)}</td><td>${esc((r.actions||[]).join(", ")||"none")}</td><td>${esc((r.members||[]).join(", ")||"none")}</td></tr>`).join("");
 const count=`${p.eligible} distinct ${p.eligible===1?"person":"people"} eligible`;
 return `<div class=setblk data-blk=perm data-theme=slk><h3 class=blkh id=slperm tabindex="-1">Permissions</h3>`
 +`<table class=slperm aria-label="Permissions of this channel"><thead><tr><th scope=col>Accepted</th><th scope=col>Role</th><th scope=col>May ask for</th><th scope=col>Members (user ids)</th></tr></thead><tbody>${rows}</tbody></table>`
 +`<p class=m>${esc(count)}</p>${(p.notes||[]).map(x=>`<p class=m>${esc(x)}</p>`).join("")}</div>`}
function slTPreview(n){const p=n.permissions;if(!p||!n.ref)return "";
 const senders=slPvSenders(n),known=!!(slSnd&&slSnd.node===n.id&&senders.some(s=>s.v===slSnd.sender));
 const cur=known?slSnd.sender:senders[0].v;   // a sender the channel no longer offers falls back to its first option
 const opts=senders.map(s=>`<option value="${slA(s.v)}"${s.v===cur?" selected":""}>${esc(s.t)}</option>`).join("");
 const got=slSnd&&slSnd.node===n.id&&slSnd.sender===cur?slSnd.data:null;
 const cells=g=>[["Decision",g.decision],["Role",g.role],["Actions",g.actions],["Reply path",g.reply],["Mark",g.mark],["Language",g.language],["Tone",g.tone]].map(([k,v])=>`<div class=tile><span class=tk>${esc(k)}</span><span class=tv>${esc(v)}</span></div>`).join("");
 const out=got&&got.err?`<p class="mx err" role=status>${esc(got.err)}</p>`:got?`<div class="now sltnow slpvnow">${cells(got)}</div>`:(slSnd&&slSnd.node===n.id&&slSnd.sender===cur?`<p class=m>Reading the decision.</p>`:"");
 return `<div class=setblk data-blk=preview data-theme=slk><h3 class=blkh>Try a sender</h3><div class=slpvbox>`
 +`<label class=slf><span>Sender</span><select data-slp-sender="${slA(n.id)}" aria-label="Sender">${opts}</select></label>${out}</div></div>`}
async function slPvAsk(id,sender){const n=slTNode(id);if(!n||!n.ref)return;
 const opt=slPvSenders(n).find(s=>s.v===sender)||{kind:"stranger"};
 const q=new URLSearchParams({workspace:n.ref.workspace,channel:n.ref.channel,sender_type:opt.kind});
 if(opt.kind==="member")q.set("user",opt.own?((n.permissions||{}).members||[])[0]||"":sender);
 if(opt.own)q.set("own","1");
 slSnd={node:id,sender,data:null};slPvShow();
 let data;try{data=await (await fetch("/api/slack/decide?"+q)).json()}catch(e){data={err:"The preview did not answer."}}
 if(slSnd&&slSnd.node===id&&slSnd.sender===sender){slSnd={node:id,sender,data};slPvShow()}}
function slPvShow(){slRender();const el=typeof document!=="undefined"&&document.querySelector&&document.querySelector("[data-slp-sender]");if(el&&el.focus)el.focus()}
function slPermGo(){const el=typeof document!=="undefined"&&document.getElementById&&document.getElementById("slperm");if(!el)return;if(el.focus)el.focus({preventScroll:true});const r=el.getBoundingClientRect?el.getBoundingClientRect():null;const vh=globalThis.innerHeight||0;if(el.scrollIntoView&&(!r||r.top<0||r.bottom>vh))el.scrollIntoView({block:"start"})}
function slTreeHtml(){const roots=slTRoots(),T=slT||{},out=[],cur=slTsel;
 const walk=(n,more)=>{out.push(slTItem(n,cur,more));if(!slTOpen(n))return;const kids=n.children||[];
  kids.forEach((c,i)=>walk(c,more.concat([i<kids.length-1])))};for(const r of roots)walk(r,[]);
 const lost=(Array.isArray(T.unknown_projects)&&T.unknown_projects.length)
  ?`<p class="m sltunknown" role=status>${esc("Not on this hub: "+T.unknown_projects.join(", "))}</p>`:"";   // a named project the hub does not hold is never dropped in silence (#2827)
 return `<div class="slttop${slTshow&&roots.length?" sltshow":""}"><div class=sltcol>`   // an empty tree keeps its column: Add client is the one way out of it (#2922)
 +(T.notice?`<div class="mx err sltnotice" role=status><b>${esc(T.notice.title)}</b><br>${esc(T.notice.text)}</div>`:"")
 +slTSlot(T.guest_slot)+slTAddClient()+(roots.length?"":slTConfirm(null))   // the empty tree has no detail pane, so the question sits beside its control, which a narrow panel always shows (#2922)
 +`<div role=tree aria-label="Slack tree" class=sltree id=sltree>${out.join("")}</div>${lost}</div>`
 +`<div class=sltdetail id=sltdetail tabindex="-1">${roots.length?slTDetail():`<p class=m>${esc("No project in the client map yet.")}</p>`}</div></div>`}
function slPropose(id,confirm){const n=slTNode(id);if(!n)return;
 const el=typeof document!=="undefined"&&document.getElementById&&document.getElementById("slclpersona");
 const pid=(el&&el.value)||n.default_persona||"";
 const opt=(n.persona_options||[]).find(o=>o.id===pid)||((el&&el.options&&el.options[el.selectedIndex])||{});
 const pname=String(opt.name||opt.text||pid),body={client:n.client||"",persona:pid,persona_name:pname};   // the pane's own label, never the raw id (#2827)
 if(confirm)body.confirm=true;slSaving=!!confirm;slMsg="";slBarSync();
 const fail=m=>{slSaving=false;slMsg="!"+m;slRetryFn=()=>slPropose(id,true);slRender()};
 fetch("/api/slack/clients",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)})
 .then(r=>{if(r.status===404||r.status>=500){fail("the client route is not available on this hub");return null}return r.json()})
 .then(o=>{if(o===null)return;if(!o||o.err){fail((o&&o.err)||"the client default could not be saved");return}
  slSaving=false;slRetryFn=null;if(o.rows)n.proposal=o.rows;
  if(confirm){n.default_persona=pid;slMsg=o.partial?"saved in part · a project was not written":"saved"}   // the refused projects are named in the pane (#2908)
  slRender()})
 .catch(()=>fail("the client default could not be saved: the hub did not answer"))}
/* The client map's own changes (#2922): add a project, remove a project, add a client, delete a client. Every change is
   one confirmed POST to the client route, and the hub writes the map and answers; the view then reads the tree again,
   so what it draws is the hub's answer. The list, the targets and the question are the hub's (ADR-0137, ADR-0177). */
function slMapAsk(id){const n=slTNode(id);slConfirm=n&&n.ask?{id:n.id,text:n.ask.text,body:n.ask}:null;slRender()}   // the one question a remove or a delete asks
function slMapKeep(){slConfirm=null;slRender()}
/* One change posts its body as it stands, so the hub answers its one question before any write; the confirm posts the
   same body with the flag (#2922). A body that carries `confirm` on the first click would skip the question. One change
   is in flight at a time: a click while the save runs is dropped, so it never overwrites the pending question or drops
   an ask the owner already issued (#2922). `at` names the node the question belongs to: the control's own node, so a
   stale selection never draws the question on another pane. */
function slMapPost(body,at){if(slSaving)return;   // one change at a time, never a second over the pending question
 const node=at||slTsel||(slTRoots()[0]||{}).id||"";   // the pane the question belongs to, captured before the answer
 slSaving=true;slMsg="";slBarSync();
 const fail=m=>{slSaving=false;slMsg="!"+m;slRender()};
 fetch("/api/slack/clients",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)})
 .then(r=>{if(r.status===404||r.status>=500){fail("the client route is not available on this hub");return null}return r.json()})
 .then(o=>{if(o===null)return;if(!o||o.err){fail((o&&o.err)||"the client map was not saved");return}
  if(o.saved===false&&o.ask){slSaving=false;if(slTNode(node))slTsel=node;   // the question moves the pane to its own node
   slConfirm={id:node,text:o.ask,body:body};slRender();return}   // the hub's question, drawn before the write
  slSaving=false;slConfirm=null;slMsg="saved";slLoad()})   // the tree is read again, so the drawn tree is the hub's
 .catch(()=>fail("the client map was not saved: the hub did not answer"))}
function slMapConfirm(){if(slSaving)return;if(slConfirm)slMapPost(Object.assign({},slConfirm.body,{confirm:true}),slConfirm.id)}
function slMapAddProject(id){const el=typeof document!=="undefined"&&document.getElementById&&document.getElementById("slclproject");
 if(el&&el.value)slMapPost({op:"add_project",client:(slTNode(id)||{}).client||"",project:el.value},id)}
function slMapAddClient(){const d=typeof document!=="undefined"?document:null,
 name=d&&d.getElementById&&d.getElementById("slcname"),sel=d&&d.getElementById&&d.getElementById("slcpersona"),
 typed=name?String(name.value||"").trim():"";if(!typed)return;   // a name is what makes a client (#2922)
 const picked=sel&&sel.options&&sel.options[sel.selectedIndex];
 slMapPost({op:"add_client",name:typed,persona:sel?String(sel.value||""):"",persona_name:String((picked&&picked.text)||(sel&&sel.value)||"")})}
/* A tone becomes a class token only from a fixed set, and only a set each CSS rule draws: `err` has its rule, and any
   other tone is neutral, never a token of its own (#2842). */
const slTTone=t=>t==="err"?t:"neutral";
function slEdit(k,t){const p=k.split(":"),v=t.type==="checkbox"?t.checked:t.value;
 if(p[0]==="ev"&&p[2]==="id")slEvRename(+p[1],v);
 else if(p[0]==="ev"&&p[2]==="tpl"){const e=slEvDoc()[+p[1]];if(e)e.template=v}
 else if(p[0]==="an")slAnnSet(p[1],+p[2],+p[3],+p[4],v);
 else if(p[0]==="ov")slAnnOverride(p[1],+p[2],+p[3],+p[4],v)}
/* The workspace and channel editor (SPEC 2682 #2696): one detail opens in place, and every change is one operation the
   hub applies to the two raw documents. The view draws the fields and binds them; the class, the file and every refusal
   are the hub's (ADR-0137). A `guest` workspace and its channels live in the local file alone (ADR-0177). Its constants
   stay above `slEdit`, so a test that slices the editor and the page together declares each one once. */
const slDocName=d=>d==="local"?((slR&&slR.files&&slR.files.local)||"slack.local.yaml"):((slR&&slR.files&&slR.files.committed)||"slack.yaml");
/* The documents the editor works on: a read that answered none falls back to the set the last save kept, never to an
   empty pair, so an edit is never applied to a document the owner did not see. */
const slDocsCur=()=>slDocs||slSavedDocs||{committed:{},local:{}};
const slWs=d=>{const doc=slDocsCur()[d]||{};const list=Array.isArray(doc.workspaces)?doc.workspaces:(doc.workspaces=[]);return list};
const slList=(d,k)=>((slDocsCur()[d]||{})[k]||[]).map(x=>x&&x.id).filter(Boolean);
const slLang=cur=>`<option value=""${cur?"":" selected"}>inherit</option>`+(((slR&&slR.languages)||[]).map(x=>`<option value="${slA(x)}"${x===cur?" selected":""}>${slA(x)}</option>`).join(""));   // the policy's languages (#2845)
const slPollDef=(mode,name)=>((((slR&&slR.poll_defaults)||{})[mode]||{})[name]||"");
const slPollPh=(mode,name)=>{const v=slPollDef(mode,name);return v?"default: "+v:""};   // the policy's poll default (#2845)
const slPollMode=()=>(slR&&slR.poll_default_mode)||"";   // the policy's default poll mode (#2845)
const slPersona=cur=>`<option value=""${cur?"":" selected"}>inherit</option>`+slList("committed","personas").map(x=>`<option value="${slA(x)}"${x===cur?" selected":""}>${slA(x)}</option>`).join("");
const slF=(attr,label,ctl)=>`<label class=slf><span>${esc(label)}</span>${ctl.replace("data-sle=",`data-sle="${attr}"`).replace('aria-label="Slack field"',`aria-label="${slA(label)}"`)}</label>`;
const slInput=(attr,label,value,ph)=>slF(attr,label,`<input type=text data-sle= aria-label="Slack field" value="${slA(value||"")}"${ph?` placeholder="${slA(ph)}"`:""}>`);
const slCheck=(attr,label,on)=>`<label class=slchk><input type=checkbox data-sle="${attr}" aria-label="${slA(label)}"${on?" checked":""}> ${esc(label)}</label>`;
const slWsFields=(d,i)=>{const w=slWs(d)[i]||{},g=w.class==="guest",at=f=>`${d}:${i}::${f}`;
 return `<div class="sle slwsdet" role=group aria-label="workspace fields">`
 +slInput(at("id"),"Id",w.id)+slInput(at("label"),"Label",w.label)
 +slF(at("class"),"Class",`<select data-sle= aria-label="Slack field"><option value=owned${g?"":" selected"}>owned</option><option value=guest${g?" selected":""}>guest</option></select>`)
 +slInput(at("app"),"App",w.app)
 +slF(at("language"),"Language",`<select data-sle= aria-label="Slack field">${slLang(w.language)}</select>`)
 +slF(at("persona"),"Persona",`<select data-sle= aria-label="Slack field">${slPersona(w.persona)}</select>`)
 +`<span class=slact><button data-slrmws="${d}:${i}">Remove workspace</button></span></div>`};
const slChFields=(d,i,j)=>{const ws=slWs(d)[i]||{},c=(ws.channels||[])[j]||{},g=ws.class==="guest",at=f=>`${d}:${i}:${j}:${f}`,poll=c.poll||{};
 let h=`<div class="sle slchdet" role=group aria-label="channel fields">`
 +slInput(at("id"),"Id",c.id)+slInput(at("name"),"Name",c.name)
 +slF(at("mode"),"Mode",`<select data-sle= aria-label="Slack field">${slOpts(SL_MODE,c.mode||"listen")}</select>`)
 +slF(at("language"),"Language",`<select data-sle= aria-label="Slack field">${slLang(c.language)}</select>`)
 +slF(at("persona"),"Persona",`<select data-sle= aria-label="Slack field">${slPersona(c.persona)}</select>`)
 +slInput(at("roles"),"Roles",(c.roles||[]).join(", "))+slInput(at("announce"),"Announce",(c.announce||[]).join(", "));
 if(g){
  h+=slF(at("speak"),"Answer as",`<select data-sle= aria-label="Slack field">${slOpts(SL_SPEAK,c.speak||"draft")}</select>`)
  +slF(at("transcribe"),"Transcribe a clip",`<select data-sle= aria-label="Slack field">${slOpts(SL_TR,c.transcribe||"off")}</select>`)
  +slCheck(at("voice_reply"),"Voice reply",!!c.voice_reply)
  +slF(at("poll.mode"),"Poll mode",`<select data-sle= aria-label="Slack field">${slOpts(SL_PMOD,poll.mode||slPollMode())}</select>`);
  const pm=poll.mode||slPollMode();
  h+=(pm==="exponential")
   ?slInput(at("poll.floor"),"Floor",poll.floor,slPollPh(pm,"floor"))+slInput(at("poll.ceiling"),"Ceiling",poll.ceiling,slPollPh(pm,"ceiling"))
    +slInput(at("poll.factor"),"Factor",poll.factor,slPollPh(pm,"factor"))+slInput(at("poll.idle_window"),"Idle window",poll.idle_window,slPollPh(pm,"idle_window"))
   :slInput(at("poll.fast"),"Fast",poll.fast,slPollPh(pm,"fast"))+slInput(at("poll.slow"),"Slow",poll.slow,slPollPh(pm,"slow"))
    +slInput(at("poll.quiet_after"),"Quiet after",poll.quiet_after,slPollPh(pm,"quiet_after"));
  h+=slInput(at("poll.thread_open"),"Thread open",poll.thread_open,slPollPh(pm,"thread_open"))+slInput(at("draft_for"),"Draft for",(c.draft_for||[]).join(", "));
 }
 return h+`<span class=slact><button data-slrmch="${d}:${i}:${j}">Remove channel</button></span></div>`};
const slAddCh=(d,i)=>`<div class=slad><input data-slci="${d}:${i}" placeholder="channel id" aria-label="new channel id"><input data-slcn="${d}:${i}" placeholder="name" aria-label="new channel name"><button data-sladdch="${d}:${i}">Add channel</button></div>`;
const slAddWs=()=>`<div class=slad><select data-slwsclass aria-label="new workspace class"><option value=owned>owned · app installed</option><option value=guest>guest · connector</option></select><input data-slwsid placeholder="team id" aria-label="new workspace id"><input data-slwslabel placeholder="label" aria-label="new workspace label"><input data-slwsapp placeholder="app" aria-label="new workspace app"><button data-sladdws>Add Slack workspace</button></div>`;
const slOpenBtn=(key,label)=>`<button data-slopen="${key}">${slOpen===key?"Close":"Edit"}</button>`;
/* The roles and members editor (SPEC 2682 #2699). The owner edits a role's id, its actions, its deploy list and its
   tone, and the members of a role in one format: Slack workspace, channel, role and user ids. The actions come from the hub's read,
   never a copy in the view, and every rule stays with the hub and the policy module (ADR-0177): the edit posts one
   operation, and the hub refuses an action outside the list, a member that is not a user id, a channel outside the
   member's workspace, and `prod` in a deploy list. A role lives in the committed file and a member in the local one, so
   the operation names the file it edits. The raw user id and deploy fields travel whole; the hub splits them. */
const slDocRows=(d,root)=>{const doc=slDocsCur()[d]||{},rows=doc[root];return Array.isArray(rows)?rows:[]};
const slRoleRows=()=>slDocRows("committed","roles");
const slMemberRows=()=>slDocRows("local","members");
const slWsIds=()=>{const out=[];for(const d of [slDocsCur().committed||{},slDocsCur().local||{}])
 for(const w of (Array.isArray(d.workspaces)?d.workspaces:[]))if(w&&w.id&&out.indexOf(String(w.id))<0)out.push(String(w.id));return out};
const slChIdsOf=wsId=>{const out=[];for(const d of [slDocsCur().committed||{},slDocsCur().local||{}])
 for(const w of (Array.isArray(d.workspaces)?d.workspaces:[])){if(!w||String(w.id)!==String(wsId))continue;
  for(const c of (Array.isArray(w.channels)?w.channels:[]))if(c&&c.id&&out.indexOf(String(c.id))<0)out.push(String(c.id))}return out};
const slChannelsOf=wsId=>{const out=[];for(const d of [slDocsCur().committed||{},slDocsCur().local||{}])
 for(const w of (Array.isArray(d.workspaces)?d.workspaces:[]))if(w&&String(w.id)===String(wsId))for(const c of (Array.isArray(w.channels)?w.channels:[]))if(c&&c.id)out.push(c);return out};
const slWsOpt=id=>{let lab="";for(const d of [slDocsCur().committed||{},slDocsCur().local||{}])
 for(const w of (Array.isArray(d.workspaces)?d.workspaces:[]))if(!lab&&w&&String(w.id)===String(id)&&w.label)lab=String(w.label);return lab||id};   // a person reads a workspace by its label, never its id (#2911)
const slChOpt=(wsId,id)=>{const c=slChannelsOf(wsId).find(x=>String(x.id)===String(id));return c&&c.name?`#${c.name}`:id};
const slOptsV=(ids,labelOf,cur)=>ids.map(x=>`<option value="${slA(x)}"${x===cur?" selected":""}>${slA(labelOf(x))}</option>`).join("");
const slRoleIds=()=>slRoleRows().map(x=>String((x||{}).id||"")).filter(Boolean);
const slActionList=()=>Array.isArray(slR&&slR.role_actions)?slR.role_actions:[];
const slDeployList=x=>Array.isArray(x)?x:(x&&typeof x==="object"&&Array.isArray(x.environments)?x.environments:[]);
const slRoleEditRow=(x,i)=>{const on=Array.isArray(x&&x.actions)?x.actions:[],acts=slActionList(),n=i+1,mute=!!(x&&x.ignore===true),off=mute?" disabled":"";   // an ignore role grants nothing, so its actions and deploy are off (#2929)
 return `<div class="sle slroledet" role=group aria-label="role ${n} fields"><div class=slevr role=row>`
 +`<span role=cell data-l="Role"><input data-sle="role:${i}:id" value="${slA((x&&x.id)||"")}" size=16 aria-label="role ${n}: id"></span>`
 +`<span role=cell data-l="Actions"><span class=slacts>${acts.map(a=>`<label class=slchk><input type=checkbox data-sle="role:${i}:act:${slA(a)}"${on.indexOf(a)>=0?" checked":""}${off} aria-label="role ${n}: ${slA(a)}"> ${esc(a)}</label>`).join("")}</span></span>`
 +`<span role=cell data-l="Deploy"><input data-sle="role:${i}:deploy" value="${slA(slDeployList(x&&x.deploy).join(", "))}" placeholder="stg"${off} aria-label="role ${n}: deploy"></span>`
 +`<span role=cell data-l="Tone"><input data-sle="role:${i}:tone" value="${slA((x&&x.tone)||"")}" placeholder="inherit"${off} aria-label="role ${n}: tone"></span>`
 +`<span role=cell data-l="Ignore"><label class=slchk><input type=checkbox data-sle="role:${i}:ignore"${mute?" checked":""} aria-label="role ${n}: ignore all messages"> Ignore all messages</label></span>`
 +`<span role=cell><button data-slrmrole="${i}" aria-label="remove role ${n}">Remove</button></span></div></div>`};
/* Members by name (#2911): a Slack user knows a person by email or @name, never by a user id. The row shows each
   person's display name and @name, which the hub reads from the workspace's own connection, and the add row takes an
   email or an @name that the hub resolves to the id the local file stores. A workspace this host has no connection to
   keeps a raw id field under "Slack ids", the one place an id shows. */
let slPeople={},slPeopleAsk={},slPeopleManual={},slWhoMsg="",slWhoPick=[];
const slPersonOf=(ws,id)=>(slPeople[ws]||{})[id]||null;
function slPeopleNeed(){if(typeof fetch!=="function")return;const want={};
 for(const m of slMemberRows()){const ws=String((m&&m.workspace)||"");for(const u of (Array.isArray(m&&m.users)?m.users:[]))if(!slPersonOf(ws,u))(want[ws]=want[ws]||[]).push(String(u))}
 for(const ws of Object.keys(want)){if(slPeopleAsk[ws])continue;slPeopleAsk[ws]=true;   // one ask per workspace per page view
  fetch("/api/slack/people",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({workspace:ws,ids:want[ws]})})
  .then(r=>r.json()).then(o=>{if(o&&o.manual)slPeopleManual[ws]=true;slPeople[ws]={...(slPeople[ws]||{}),...((o&&o.people)||{})};slRender()}).catch(()=>{})}}
const slPersonChip=(ws,u,m,k,n)=>{const p=slPersonOf(ws,u);   // an unread person keeps the row's alias only when the row names one person
 return p?`<span class=slperson><b>${esc(p.name||p.handle)}</b>${p.handle?` <span class=m>@${esc(p.handle)}</span>`:""}</span>`
  :`<span class=slperson><b>${esc(n===1&&m&&m.alias?m.alias:n===1?"A Slack member":"Slack member "+(k+1))}</b></span>`};
const slMemberEditRow=(m,i)=>{const n=i+1,at=f=>`member:${i}:${f}`,ws=String(m.workspace||""),us=Array.isArray(m.users)?m.users:[];
 const ids=slPeopleManual[ws]?`<details class=sladv><summary>${esc("Slack ids")}</summary><input data-sle="${at("users")}" value="${slA(us.join(", "))}" aria-label="member ${n}: Slack ids"></details>`:"";
 return `<div class="sle slmembdet" role=group aria-label="member ${n} fields"><div class=slevr role=row>`
 +`<span role=cell data-l="Person"><span class=slwho>${us.length?us.map((u,k)=>slPersonChip(ws,u,m,k,us.length)).join(""):`<span class=m>${esc("nobody yet")}</span>`}`
 +(us.length&&us.some(u=>!slPersonOf(ws,u))?`<span class="m slwhonote">${esc(slPeopleManual[ws]?"names not readable on this host":"reading the names")}</span>`:"")+`${ids}</span></span>`
 +`<span role=cell data-l="Slack workspace"><select data-sle="${at("workspace")}" aria-label="member ${n}: Slack workspace">${slOptsV(slWsIds(),slWsOpt,m.workspace)}</select></span>`
 +`<span role=cell data-l="Channel"><select data-sle="${at("channel")}" aria-label="member ${n}: channel">${slOptsV(slChIdsOf(m.workspace),c=>slChOpt(m.workspace,c),m.channel)}</select></span>`
 +`<span role=cell data-l="Role"><select data-sle="${at("role")}" aria-label="member ${n}: role">${slOpts(slRoleIds(),m.role)}</select></span>`
 +`<span role=cell><button data-slrmmem="${i}" aria-label="remove member ${n}">Remove</button></span></div></div>`};
function slRolesEditHtml(r){const rs=slRoleRows(),ms=slMemberRows(),cf=r.files.committed,lf=r.files.local;
 let h=`<h5 class=slws>Roles ${slTag(cf)}</h5>`;
 h+=rs.length?rs.map(slRoleEditRow).join(""):`<p class=m>${esc("No role: only the owner can trigger the agent.")}</p>`;
 h+=`<div class=slad><input data-slroleid placeholder="role id" aria-label="new role id"><button data-sladdrole>Add role</button></div>`;
 h+=`<h5 class=slws>Members ${slTag(lf)}</h5>`;
 h+=ms.length?ms.map(slMemberEditRow).join(""):`<p class=m>${esc("No member: the roles name no user.")}</p>`;
 const w0=slWsIds()[0]||"";slPeopleNeed();
 const pick=slWhoPick.length?`<div class=slwhopick role=group aria-label="${slA("Choose the person")}">${slWhoPick.map(p=>`<button type=button data-slmpick="${slA(p.id)}">${esc(p.name||p.handle)} <span class=m>@${esc(p.handle)}</span></button>`).join("")}</div>`:"";
 const manual=slPeopleManual[w0]?`<details class=sladv><summary>${esc("Slack ids")}</summary><input data-slmus aria-label="new member Slack id"></details>`:"";
 return h+`<div class=slad><input data-slmwho value="${esc(slWhoQ)}" placeholder="email or @name" aria-label="new member: email or @name">`
 +`<select data-slmws aria-label="new member Slack workspace">${slOptsV(slWsIds(),slWsOpt,"")}</select>`
 +`<select data-slmch aria-label="new member channel">${slOptsV(slChIdsOf(w0),c=>slChOpt(w0,c),"")}</select>`
 +`<select data-slmrole aria-label="new member role">${slOpts(slRoleIds(),"")}</select>`
 +`<button data-sladdmem>Add member</button>${manual}</div>`
 +(slWhoMsg?`<p class="slwhomsg${slWhoMsg.startsWith("!")?" err":""}" role=status>${esc(slWhoMsg.replace(/^!/,""))}</p>`:"")+pick}
let slWhoAt=null,slWhoQ="";   // slWhoQ: the text the owner typed, kept until a person is added   // the workspace, channel and role the add row held when the owner asked
function slMemberAdd(at,p){slPeople[at.workspace]={...(slPeople[at.workspace]||{}),[p.id]:p};slWhoMsg="";slWhoPick=[];slWhoAt=null;slWhoQ="";
 slOp({kind:"add",doc:"local",path:["members"],value:{workspace:at.workspace,channel:at.channel,role:at.role,users:p.id,alias:p.handle||p.name||""}})}
function slMemberAddAsk(){const g=s=>{const el=document.querySelector(s);return el?String(el.value||"").trim():""};
 const at={workspace:g("[data-slmws]"),channel:g("[data-slmch]"),role:g("[data-slmrole]")},who=g("[data-slmwho]"),raw=g("[data-slmus]");
 if(raw){slMemberAdd(at,{id:raw,handle:"",name:""});return}   // the one raw path: a workspace this host cannot read
 if(!who){slWhoMsg="!Type an email or an @name.";slRender();return}
 slWhoQ=who;slWhoMsg="Looking up "+who;slWhoPick=[];slWhoAt=at;slRender();
 fetch("/api/slack/people",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({workspace:at.workspace,q:who})})
 .then(r=>r.json()).then(o=>{if(o&&o.manual)slPeopleManual[at.workspace]=true;const ms=(o&&o.matches)||[];
  if(ms.length===1){slMemberAdd(at,ms[0]);return}
  if(ms.length>1){slWhoPick=ms;slWhoMsg="Several people match. Choose one.";slRender();return}
  slWhoMsg="!"+((o&&o.err)||"No Slack user matches");slRender()})
 .catch(()=>{slWhoMsg="!The hub did not answer.";slRender()})}
function slDocOp(attr,el){const p=attr.split(":"),v=el.type==="checkbox"?!!el.checked:el.value;
 if(p[0]==="role"||p[0]==="member"){const i=+p[1];   // a role edits the committed file and a member the local one (#2699)
  if(p[0]==="role"&&p[2]==="ignore"){const set=(f,x)=>({kind:"set",doc:"committed",path:["roles",i,f],value:x});   // an ignore role grants nothing (#2929)
   return v?[set("actions",[]),set("deploy",[]),set("ignore",true)]:set("ignore",false)}
  if(p[0]==="role"&&p[2]==="act"){const a=p.slice(3).join(":"),row=slRoleRows()[i]||{},cur=Array.isArray(row.actions)?row.actions.slice():[];
   const next=v?(cur.indexOf(a)>=0?cur:cur.concat([a])):cur.filter(x=>x!==a);
   return {kind:"set",doc:"committed",path:["roles",i,"actions"],value:next}}
  return {kind:"set",doc:p[0]==="role"?"committed":"local",path:[p[0]==="role"?"roles":"members",i,p[2]],value:v}}
 const field=p[3],op={kind:"set",doc:p[0],path:["workspaces",+p[1]]};
 if(p[2]==="")op.path.push(field);else op.path.push("channels",+p[2],field);
 if(["roles","announce","draft_for"].includes(field))op.value=String(v).split(",").map(x=>x.trim()).filter(Boolean);else op.value=v;
 return op}
function slEditHtml(){const docs=[["committed",slDocsCur().committed],["local",slDocsCur().local]];
 let h=slMsg?`<div class="mx${slMsg.startsWith("!")?" err":""}" role=status>${esc(slMsg.replace(/^!/,""))}</div>`:"";
 docs.forEach(([d,doc])=>{const list=(doc&&Array.isArray(doc.workspaces))?doc.workspaces:[];
  h+=list.map((w,i)=>{const key=`${d}:${i}`,wsopen=slOpen===key,chans=Array.isArray(w.channels)?w.channels:[],
   tag=`<span class="sltag ${slA(w.class||"owned")}">${esc(w.class||"owned")}</span>`,
   how=w.class==="guest"?"claude.ai connector · your account":"app "+esc(w.app||"none");
   let s=`<h5 class=slws>${esc(w.label||w.id||"workspace")} ${tag} <span class=m>${how}</span> ${slTag(slDocName(d))} ${slOpenBtn(key)}</h5>`;
   if(wsopen)s+=slWsFields(d,i);
   const guest=w.class==="guest",heads=slEditHeads(guest);
   s+=`<table class=slt2><thead><tr>${heads.map(x=>`<th>${x}</th>`).join("")}</tr></thead><tbody>`;
   s+=chans.map((c,j)=>{const ck=`${key}:${j}`,p=c.poll||{},
    pollTxt=p.mode==="exponential"?`exp · ${p.floor||"?"} → ${p.ceiling||"?"}`:(p.fast||p.slow)?`${p.fast||"?"} → ${p.slow||"?"}`:(slPollMode()||"two_state");   // a named value alone: the default is the policy's (#2845)
    return `<tr><td data-l="Channel">#${esc(c.name||c.id)} <span class=m>${esc(c.id)}</span></td><td data-l="Mode"><span class=nw>${esc(c.mode||"listen")}</span></td>`
    +`<td data-l="Language"><span class=nw>${esc(c.language||"inherit")}</span></td><td data-l="Persona"><span class=nw>${esc(c.persona||"inherit")}</span></td>`
    +(guest?`<td data-l="Answers"><span class=nw>${esc(c.speak||"draft")}</span></td><td data-l="Polling"><span class=nw>${esc(pollTxt)}</span></td>`:"")
    +`<td data-l="Roles">${(c.roles||[]).length?esc((c.roles||[]).join(", ")):"owner only"}</td><td data-l="Announce">${(c.announce||[]).length?esc((c.announce||[]).join(", ")):"never posts"}</td>`
    +`<td>${slOpenBtn(ck)}</td></tr>`+(slOpen===ck?`<tr><td class=sldet colspan=${heads.length}>${slChFields(d,i,j)}</td></tr>`:"")}).join("");
   s+=`</tbody></table>`+(chans.length?"":`<p class=m>${esc("No channel in this Slack workspace.")}</p>`)+slAddCh(d,i);
   return s}).join("")});
 return h+slAddWs()}
async function slDocEdit(attr,el){const op=slDocOp(attr,el);if(!op)return;
 for(const o of [].concat(op)){await slOp(o);if(slMsg.startsWith("!"))break}}   // a list of ops runs in order and stops at the first refusal
async function slOp(op){try{const r=await fetch("/api/slack/files",{method:"POST",headers:{"Content-Type":"application/json"},
 body:JSON.stringify({committed:slDocsCur().committed,local:slDocsCur().local,local_name:slDocName("local"),op})}),d=await r.json();
 if(d.err){slMsg="!"+d.err;slOpen=""}else{slDocs={committed:d.committed,local:d.local};if(d.tree)slT=d.tree;slMsg=""}}   // the Now block follows the staged documents (#2846)
 catch(x){slMsg="!the edit failed"}slRender();slChTree()}   // an edit may plant a new workspace, whose add pane needs its list (#2957)
/* Another profile's project node (#3033): the node names the project its Persona and Language edit writes, the hub
   reads and stages that project's own two files, and the save posts the project with only its own documents. Each
   project keeps its own pending save, refusal, retry and undo, so a switch of project never redirects one. The hub
   holds every rule (ADR-0137, ADR-0177); this view binds the edit to a project and hands the documents back. */
let slPo={};
function slPoSt(id){id=String(id);return slPo[id]||(slPo[id]={id,docs:null,saved:null,timer:null,j:"",saving:false,
 undoing:false,msg:"",undo:null,undoAt:0,retry:null,chain:Promise.resolve()})}
const slPoDocs=id=>{const s=slPo[String(id)];return (s&&s.docs)||{committed:{},local:{}}};
const slPoDirty=id=>{const s=slPo[String(id)];return !!s&&!!s.docs&&JSON.stringify(s.docs)!==JSON.stringify(s.saved||{})};
function slPoSet(id,docs){const s=slPoSt(id);s.docs=slClone(docs)}
function slPoField(id,field,el,target){const s=slPoSt(id);   // target: `project`, `ws:<id>` or a channel id (#2901, #3034)
 const op=slFieldOp(target||"project",field,el);
 s.chain=s.chain.then(()=>slPoOp(s,op)).catch(()=>{s.msg="!the edit failed";slRender();slBarSync()})}
async function slPoOp(s,op){try{const d=await(await fetch("/api/slack/files",{method:"POST",headers:{"Content-Type":"application/json"},
 body:JSON.stringify({project:s.id,op})})).json();
 if(d.err){s.msg="!"+d.err;s.retry=null;slRender();slBarSync();return}
 const before=JSON.stringify(s.docs||{});s.docs={committed:d.committed,local:d.local};s.msg="";
 if(d.tree)slT=d.tree;   // the redrawn tree carries the values a row inherits, so a channel follows its workspace (#3034)
 slRender();if(JSON.stringify(s.docs)!==before)slPoSchedule(s.id);slBarSync()}
 catch(x){s.msg="!the edit failed";slRender()}}
function slPoSchedule(id){const s=slPoSt(id);if(!s.docs)return;const j=JSON.stringify(s.docs);
 if(j===s.j)return;s.j=j;clearTimeout(s.timer);   // a refused document waits for Retry, never a burst of retries
 const only=JSON.stringify(s.docs.committed)===JSON.stringify((s.saved||{}).committed);
 s.timer=setTimeout(()=>{s.timer=null;slPoSave(id)},only?SL_LOCAL_MS:SL_COMMIT_MS)}   // the same debounce as the page's own project
async function slPoSave(id){const s=slPoSt(id);if(!s.docs||!slPoDirty(id))return;if(s.saving)return;
 if(JSON.stringify(s.docs)===JSON.stringify(s.saved))return;   // the branch already holds this document: no commit, no claim (#3033)
 const sent=JSON.stringify(s.docs),prev=s.saved?slClone(s.saved):null,undoing=!!s.undoing;   // Undo's own save offers no second Undo
 s.undoing=false;s.saving=true;s.msg="";slBarSync();
 try{const d=await(await fetch("/api/slack/files",{method:"POST",headers:{"Content-Type":"application/json"},
  body:JSON.stringify({project:s.id,committed:s.docs.committed,local:s.docs.local,confirm:true})})).json();
  if(d.err){s.msg="!"+d.err;s.undo=null;s.retry=()=>{s.failJ="";slPoSave(id)};slRetryFn=s.retry}
  else{s.saved=JSON.parse(sent);if(JSON.stringify(s.docs)===sent)s.docs=JSON.parse(sent);s.retry=null;slRetryFn=null;
   if(d.sha&&!undoing){s.msg="saved";slUndo={project:id,docs:prev};slUndoAt=Date.now()}   // only a landed commit offers Undo
   else{if(slUndo&&slUndo.project===id)slUndo=null;s.msg=""}}}   // no sha: the document was already there, so the page claims no save
 catch(x){s.msg="!save failed"}finally{s.saving=false}
 slRender();slBarSync()}
const slPoIdOf=n=>n&&n.edit&&n.edit.project?String(n.edit.project):"";
function slPoSel(){const n=slTNode(slTsel),id=slPoIdOf(n);return id?slPoSt(id):null}
const slAddWsOp=()=>slOp({kind:"add",doc:"committed",path:["workspaces"],
 value:{id:($("[data-slwsid]")||{}).value,label:($("[data-slwslabel]")||{}).value,class:($("[data-slwsclass]")||{}).value,app:($("[data-slwsapp]")||{}).value}});
const slAddChOp=(d,i)=>slOp({kind:"add",doc:d,path:["workspaces",i,"channels"],
 value:{id:(document.querySelector(`[data-slci="${d}:${i}"]`)||{}).value,name:(document.querySelector(`[data-slcn="${d}:${i}"]`)||{}).value}});
/* The save path over the two Slack files (#2694): the view hands both raw documents back, the hub validates them, shows
   the diff of each file and, on confirm, writes the local file and lands the committed one in dev. */
const slGroups=d=>!d?[]:[{file:((d.files||{}).committed)||"slack.yaml",lines:(((d.diff||{}).committed)||[]).map(l=>themeLine("Slack",l))},
 {file:((d.files||{}).local)||"4.slack.local.yaml",lines:(((d.diff||{}).local)||[]).map(l=>themeLine("Slack",l))}];
const slAsk=async()=>{try{const r=await(await fetch("/api/slack/files",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({committed:slDocsCur().committed,local:slDocsCur().local,confirm:false})})).json();
 return r.err?[{file:(slR&&slR.files&&slR.files.committed)||"slack.yaml",lines:["! Communication · "+r.err]}]:slGroups(r)}catch(x){return[{file:"slack.yaml",lines:["! Communication · the diff could not be read"]}]}};
/* The reply preview and the voice sample ask the hub for the draft documents, so a change shows before the save (#2697).
   The hub resolves the mark, the language, the tone and the voice through the policy module, and it makes the sample once
   per voice and tone pair and keeps the WAV in its cache. */
async function slPreviewAsk(){try{const r=await(await fetch("/api/slack/preview",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({committed:slDocsCur().committed,local:slDocsCur().local})})).json();
 slPv=r&&r.previews?r.previews:null}catch(x){slPv=null}}
const slPreviewRefresh=()=>{slPv=null;slRender();slPreviewAsk().then(()=>slRender())};
/* Autosave (#2908): every edit saves by itself, so the page shows no Save, no Discard and no "not saved". An edit of the
   local file saves at once; an edit of the committed file waits two seconds after the last edit of a burst, so a burst
   lands as one commit. Leaving the node saves a pending edit at once. An edit made during a save is kept and saves after
   it. The bar keeps one status line: Undo for a short while after a save, and Retry after a refusal. */
const SL_LOCAL_MS=300,SL_COMMIT_MS=2000,SL_UNDO_MS=15000;
let slAutoT=null,slAutoJ="",slFailJ="",slSaving=false,slAgain=false,slUndo=null,slUndoAt=0,slRetryFn=null;
const slBarSync=()=>{if(typeof setBar==="function")setBar()};
function slAutoSchedule(){if(!slDirty()||(typeof RO!=="undefined"&&RO)||typeof slPost!=="function")return;const j=JSON.stringify(slDocsCur());   // a view slice with no save path schedules nothing
 if(j===slFailJ||(slAutoT&&j===slAutoJ))return;   // a render with no new edit never pushes the save back, and a refused file waits for Retry
 slAutoJ=j;clearTimeout(slAutoT);const only=JSON.stringify(slDocs.committed)===JSON.stringify((slSavedDocs||{}).committed);
 slAutoT=setTimeout(slAutoSave,only?SL_LOCAL_MS:SL_COMMIT_MS)}
async function slAutoSave(){clearTimeout(slAutoT);slAutoT=null;slAutoJ="";if(!slDirty())return;if(slSaving){slAgain=true;return}
 slSaving=true;const prev=slClone(slSavedDocs),sent=JSON.stringify(slDocsCur());slMsg="";slFailJ="";slBarSync();
 try{await slPost(true)}finally{slSaving=false}
 if(slMsg.startsWith("!")){slUndo=null;slFailJ=sent;slRetryFn=()=>{slFailJ="";slAutoSave()}}
 else{slUndo=prev;slUndoAt=Date.now();slRetryFn=null;setTimeout(slBarSync,SL_UNDO_MS+50)}
 if(slAgain||slDirty()){slAgain=false;slAutoSchedule()}slBarSync()}
function slUndoLast(){if(!slUndo)return;
 if(slUndo.project){const id=slUndo.project,docs=slUndo.docs;slUndo=null;const s=slPoSt(id);   // Undo puts back the documents of the project the edit came from (#3033)
  s.docs=slClone(docs);s.failJ="";s.undoing=true;slPoSave(id);return}   // Undo's own save re-offers no Undo (#3033)
 slDocs=slClone(slUndo);slUndo=null;slT=null;slAutoSave()}
const slBarState=()=>{const o=slPoSel();
 if(o){if(o.saving)return {text:"Saving…",undo:false,retry:false,err:false};
  if(o.msg&&o.msg[0]==="!")return {text:"Not saved: "+o.msg.slice(1),undo:false,retry:!!o.retry,err:true};
  if(o.timer)return {text:"Saving in a moment",undo:false,retry:false,err:false};
  return o.msg?{text:"Slack: saved by itself",undo:false,retry:false,err:false}
   :{text:"Slack: each edit saves by itself",undo:false,retry:false,err:false}}
 if(slSaving)return {text:"Saving…",undo:false,retry:false,err:false};
 if(slMsg.startsWith("!"))return {text:"Not saved: "+slMsg.slice(1),undo:false,retry:!!slRetryFn,err:true};
 if(slAutoT||slProposeT)return {text:"Saving in a moment",undo:false,retry:false,err:false};
 const undo=!!slUndo&&Date.now()-slUndoAt<SL_UNDO_MS;
 return slMsg?{text:slMsg[0].toUpperCase()+slMsg.slice(1),undo,retry:false,err:false}
  :{text:"Slack: each edit saves by itself",undo:false,retry:false,err:false}};
function slDiscard(){slDocs=slClone(slSavedDocs);slT=slClone((slR&&slR.tree)||slT);slMsg="";slRender()}   // Discard puts back the values the page read last, the Now block included (#2846)
async function slLoad(){try{const r=await fetch("/api/slack/files");slR=await r.json();slS=slR;slDocs=slClone((slR&&slR.docs)||slSavedDocs||{committed:{},local:{}});slSavedDocs=slClone(slDocs);slT=(slR&&slR.tree)||null;slD=null;slMsg="";slRender();slChTree()}
 catch(e){slMsg="!load failed";slR=null;slRender()}}
async function slPost(confirm){const sent=JSON.stringify({committed:slDocsCur().committed,local:slDocsCur().local});
 const mine=()=>JSON.stringify({committed:slDocsCur().committed,local:slDocsCur().local})===sent;   // an edit made during the save is kept (#2908)
 try{const r=await fetch("/api/slack/files",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({...JSON.parse(sent),confirm})}),d=await r.json();
 slPv=null;   // a save or a reject ends the draft's reply preview: the read view answers for the files
 if(d.err){slD=null;slMsg="!"+d.err}
 else if(confirm){slSavedDocs=JSON.parse(sent);if(mine())slDocs=slClone(slSavedDocs);slD=null;slMsg="saved";   // the commit is the hub's business, not the owner's (#2908)
  try{const v=await(await fetch("/api/slack/files")).json();slR=v;slSavedDocs=slClone((v&&v.docs)||slSavedDocs||{});
   if(mine()||!slDocs){slDocs=slClone(slSavedDocs);slT=(v&&v.tree)||slT}}catch(x){}}   // the read view follows the save (#2698), and the tree follows it too (#2846)
 else{const g=slGroups(d);slD=g.some(x=>x.lines.length)?g:null;slMsg=slD?"":"nothing changed"}}catch(x){slMsg="!save failed"}
 slRender();slChTree()}
$("#sbody").addEventListener("change",e=>{const dt=e.target.dataset||{};
 if(dt.slf&&(slDocs||slSavedDocs)){const s=String(dt.slf),i=s.indexOf("|");
  if(dt.slproj){slPoField(dt.slproj,s.slice(i+1),e.target,s.slice(0,i));return}   // one field of another project's node, bound to that project (#3033, #3034)
  slOp(slFieldOp(s.slice(0,i),s.slice(i+1),e.target));return}   // one field of the tree's channel pane (#2844)
 const s=dt.sle;
 if(s&&(slDocs||slSavedDocs)){slDocEdit(s,e.target);return}
 const k=dt.sl;if(!k)return;slD=null;slMsg="";slEdit(k,e.target);slRender()});
$("#sbody").addEventListener("click",e=>{const b=e.target.closest("button");
 if(b&&(slDocs||slSavedDocs)){const dt=b.dataset||{};
  const o=dt.slopen;
  if(o!==undefined){slOpen=slOpen===o?"":o;slRender();return}
  if(b.dataset.sladdch){slAddChOp(...b.dataset.sladdch.split(":"));return}
  if(dt.sltaddch){const[d,i]=dt.sltaddch.split(":"),sel=document.querySelector(`[data-sltchpick="${dt.sltaddch}"]`),id=sel?String(sel.value||""):"";
   const ws=Object.keys(slChList).find(w=>((slChList[w]||{}).channels||[]).some(c=>c.id===id)),c=ws?slChList[ws].channels.find(x=>x.id===id):null;
   if(c){delete slChList[ws];slOp({kind:"add",doc:d,path:["workspaces",+i,"channels"],value:{id:c.id,name:c.name}})}return}   // #2918
  if(dt.sltrmch){const[d,i,j]=dt.sltrmch.split(":");
   if(typeof confirm!=="function"||confirm(`Remove ${dt.sltrmname||"this channel"}? The channel leaves the Slack file and its settings go with it.`)){
    slChList={};slTsel=String(slTsel).replace(/\.c\d+$/,"");slOp({kind:"remove",doc:d,path:["workspaces",+i,"channels",+j]})}return}
  if(b.dataset.sladdws!==undefined){slAddWsOp();return}
  if(b.dataset.slrmch){const[d,i,j]=b.dataset.slrmch.split(":");slOp({kind:"remove",doc:d,path:["workspaces",+i,"channels",+j]});return}
  if(b.dataset.slrmws){const[d,i]=b.dataset.slrmws.split(":");slOp({kind:"remove",doc:d,path:["workspaces",+i]});return}
  if(dt.sladdrole!==undefined){const el=document.querySelector("[data-slroleid]");
   slOp({kind:"add",doc:"committed",path:["roles"],value:{id:el?String(el.value||""):"",actions:[]}});return}   // #2699
  if(dt.slrmrole!==undefined){slOp({kind:"remove",doc:"committed",path:["roles",+dt.slrmrole]});return}
  if(dt.sladdmem!==undefined){slMemberAddAsk();return}   // the person is named by email or @name (#2911)
  if(dt.slmpick!==undefined){const p=slWhoPick.find(x=>x.id===dt.slmpick);if(p&&slWhoAt)slMemberAdd(slWhoAt,p);return}
  if(dt.slrmmem!==undefined){slOp({kind:"remove",doc:"local",path:["members",+dt.slrmmem]});return}}
 if(!b)return;
 if(b.dataset.slevrm!==undefined){slEvRemove(+b.dataset.slevrm);slD=null;slRender()}
 else if(b.id==="slevadd"){if(slEvAdd($("#slevnew").value)){slD=null;slRender()}}
 });
/* Settings > Personas (SPEC 3009, #3010, #3011): the personas of every roster project, grouped by client and merged by
   id inside a client. The view constructs, binds and delegates: the hub merges the rows and answers them at /api/personas,
   and the view holds no rule (ADR-0137). An edit stages in the page and posts the persona id, the changed fields and the
   target projects; the preview posts with no confirm and writes nothing, and the confirm is the theme's one write. The
   theme never saves by itself. */
// PRS{
let prS=null,prMsg="",prEd=null,prPlan=null,prPMsg="",prNew=null,prAddPlan=null,prAddMsg="",prFocus=null;   // prFocus: the row a Slack link opened (#3036)
async function prLoad(){try{prS=await (await fetch("/api/personas")).json();prMsg=prS.err?"!"+prS.err:""}catch(e){prMsg="!load failed"}prRender();prReveal()}
function prReveal(){if(!prFocus||typeof document==="undefined")return;const tr=document.querySelector("#prbody tr.prfocus");
 if(tr&&tr.scrollIntoView)tr.scrollIntoView({block:"center"})}
const prTxt=t=>esc(t==null?"":String(t));
const prPlural=(n,w)=>`${n} ${w}${n===1?"":"s"}`;
const prFile=t=>prTxt(String(t==null?"":t).split("/").pop()).replace(/\./g,".<wbr>");   // the file name alone, as the rows name it (#3036); a long one breaks after a dot, never inside a word
const prRows=()=>((prS||{}).groups||[]).flatMap(g=>g.rows||[]);
/* A persona id is local to its projects, so two clients may each hold a row with one id (#3028): a button names its
   client group, and the lookup asks for that group's row. A button with no client keeps the first match by id. */
const prRowAt=(id,client)=>{const gs=((prS||{}).groups||[]).filter(g=>client==null||String(g.id)===String(client));
 for(const g of gs){const r=(g.rows||[]).find(x=>String(x.id)===String(id));if(r)return r}return null};
function prStartEdit(id,client){const r=prRowAt(id,client);if(!r||r.read_only)return;
 prEd={mode:"fields",client,id:r.id,name:r.name,voice:r.voice,tone:r.tone,projects:(r.projects||[]).map(p=>p.id),all:(r.projects||[]).slice()};
 prNew=null;prPlan=null;prPMsg="";prRender()}
/* A drifted row offers one copy for each project that declares the id (#3015): the choice starts an edit that names the
   client and the copy, and the hub alone decides the targets, so a project of another client is never written. */
function prCopyStart(client,project){const g=((prS||{}).groups||[]).find(x=>String(x.id)===String(client));if(!g)return;
 const r=(g.rows||[]).find(x=>(x.copies||[]).some(c=>String(c.project)===String(project)));if(!r)return;
 const cp=(r.copies||[]).find(c=>String(c.project)===String(project));if(!cp)return;
 prEd={mode:"copy",id:r.id,name:cp.name,voice:cp.voice,tone:cp.tone,client:g.id,copy:cp.project,copies:(r.copies||[]).slice()};
 prNew=null;prPlan=null;prPMsg="";prRender()}
function prStopEdit(){prEd=null;prPlan=null;prPMsg="";prRender()}
function prBody(confirm){if(prEd.mode==="copy")return JSON.stringify({persona:prEd.id,client:prEd.client,copy:prEd.copy,
 confirm:!!confirm});
 return JSON.stringify({persona:prEd.id,fields:{name:prEd.name,voice:prEd.voice,tone:prEd.tone},
 projects:prEd.projects,confirm:!!confirm})}
async function prPost(confirm){if(!prEd)return;
 prPMsg=confirm?"saving":"reading the preview";prPlan=null;prRender();
 try{const d=await(await fetch("/api/personas",{method:"POST",headers:{"Content-Type":"application/json"},body:prBody(confirm)})).json();
  if(d.err)prPMsg="!"+d.err;
  else if(confirm){prPMsg=d.partial?"saved: some projects were refused":"saved: each project wrote and pushed";prEd=null;prRender();prLoad();return}
  else{prPlan=d;prPMsg=d.partial?"the preview names each refused project":"the preview writes nothing"}
 }catch(e){prPMsg="!the edit could not be posted"}
 prRender()}
async function prSample(){if(!prEd)return;
 prPMsg="the sample is being made";prRender();
 try{const r=await(await fetch("/api/slack/sample",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({voice:prEd.voice,tone:prEd.tone})})).json();
  if(r.err)prPMsg="!"+r.err;
  else{prPMsg=r.cached?"the same voice and tone: the cached sample plays":"a new voice and tone: the sample is made once and cached now";
   try{const a=new Audio(r.url),p=a.play();
    if(p&&p.catch)p.catch(()=>{prPMsg="the browser refused the sound: press Play"})}catch(x){}}}
 catch(x){prPMsg="!the sample could not be fetched"}
 prRender()}
function prPlanHtml(){const plan=prPlan||{},
 rows=Array.isArray(plan.rows)?plan.rows:[],notes=Array.isArray(plan.notes)?plan.notes:[],map=Array.isArray(plan.map)?plan.map:[];
 if(!rows.length&&!notes.length&&!map.length)return"";   // a plan of another shape draws nothing rather than throw
 const notesHtml=notes.length?`<ul class=prnotes id=prnotes>${notes.map(n=>`<li>${prTxt(n)}</li>`).join("")}</ul>`:"";
 const mapHtml=map.length?`<table class="ptab" id=prmap><thead><tr><th>Client</th><th>Result</th></tr></thead><tbody>`
  +map.map(r=>`<tr><td data-l="Client">${prTxt(r.label)}</td><td data-l="Result">${prTxt(r.result)}</td></tr>`).join("")+`</tbody></table>`:"";
 return notesHtml+`<table class="ptab" id=prplan><thead><tr><th>Project</th><th>File</th><th>Result</th></tr></thead><tbody>`
  +rows.map(r=>`<tr><td data-l="Project">${prTxt(r.project)}</td><td data-l="File"><span class=prfile>${prFile(r.file)}</span></td><td data-l="Result">${prTxt(r.result)}</td></tr>`).join("")+`</tbody></table>`+mapHtml}
/* The add (#3013): a new persona with a name, a voice, a tone and the target projects, or an existing row added to a
   project that does not declare it. It posts `op: add` through the edit's own route and holds its own preview, so the
   theme's one write stays the confirm. */
const prSlug=t=>String(t==null?"":String(t)).toLowerCase().replace(/[^a-z0-9]+/g,"-").replace(/^-+|-+$/g,"");
const prAddId=()=>prNew?(prNew.id||prSlug(prNew.name)):"";
function prStartAdd(id,client){const all=((prS||{}).projects)||[];
 if(id==null){prNew={name:"",voice:((prS||{}).voices||[])[0]||"",tone:"",projects:[]}}
 else{const r=prRowAt(id,client);if(!r)return;prNew={client};const held=new Set((r.projects||[]).map(p=>String(p.id)));   // the projects that already declare it
  prNew={client,id:r.id,name:r.name,voice:r.voice,tone:r.tone,committed:{name:r.name,voice:r.voice,tone:r.tone},projects:all.map(p=>String(p.id)).filter(p=>!held.has(p))}}
 prEd=null;prAddPlan=null;prAddMsg="";prRender()}
function prStopAdd(){prNew=null;prAddPlan=null;prAddMsg="";prRender()}
/* The add's body (#3013): a new persona posts the fields the owner typed; an existing persona posts the row's committed
   name, voice and tone verbatim, so the target project agrees with the source and no edit reaches the add. */
function prAddBody(confirm){const f=prNew.id&&prNew.committed?prNew.committed:{name:prNew.name,voice:prNew.voice,tone:prNew.tone};
 return JSON.stringify({op:"add",persona:{id:prAddId(),name:f.name,voice:f.voice,tone:f.tone},
 projects:prNew.projects,confirm:!!confirm})}
async function prAddPost(confirm){if(!prNew)return;
 prAddMsg=confirm?"saving":"reading the preview";prAddPlan=null;prRender();
 try{const d=await(await fetch("/api/personas",{method:"POST",headers:{"Content-Type":"application/json"},body:prAddBody(confirm)})).json();
  if(d.err)prAddMsg="!"+d.err;
  else if(confirm){prAddMsg=d.partial?"saved: some projects were refused":"saved: each project wrote and pushed";prNew=null;prRender();prLoad();return}
  else{prAddPlan=d;prAddMsg=d.partial?"the preview names each refused project":"the preview writes nothing"}
 }catch(e){prAddMsg="!the add could not be posted"}
 prRender()}
/* Play sample (#3013): the sample route takes a voice and a tone alone (#2697), caches one WAV per normalized pair, and
   answers it again from that cache, so the add's name and projects never reach it: the pair is the whole request. */
async function prAddSample(){if(!prNew)return;
 prAddMsg="the sample is being made";prRender();
 try{const r=await(await fetch("/api/slack/sample",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({voice:prNew.voice,tone:prNew.tone})})).json();
  if(r.err)prAddMsg="!"+r.err;
  else{prAddMsg=r.cached?"the same voice and tone: the cached sample plays":"a new voice and tone: the sample is made once and cached now";
   try{const a=new Audio(r.url),p=a.play();
    if(p&&p.catch)p.catch(()=>{prAddMsg="the browser refused the sound: press Play"})}catch(x){}}}
 catch(x){prAddMsg="!the sample could not be fetched"}
 prRender()}
function prAddPlanHtml(){const rows=((prAddPlan||{}).rows)||[];if(!rows.length)return"";   // no preview yet: no empty table (#3036)
 return `<table class="ptab" id=praddplan><thead><tr><th>Project</th><th>File</th><th>Result</th></tr></thead><tbody>`
  +rows.map(r=>`<tr><td data-l="Project">${prTxt(r.project)}</td><td data-l="File"><span class=prfile>${prFile(r.file)}</span></td><td data-l="Result">${prTxt(r.result)}</td></tr>`).join("")+`</tbody></table>`}
function prAddEditor(){const d=prNew;if(!d)return"";const voices=(prS||{}).voices||[],max=(prS||{}).tone_max;
 const limit=max==null?"the policy's":prTxt(max),all=((prS||{}).projects)||[];
 const title=d.id?`Add ${prTxt(d.name)} to another project`:"New persona";
 // An existing persona keeps the fields its source project declares: the add copies them verbatim and draws no editable
 // field, so no edit can make the target disagree with the source (#3013).
 const fields=d.id
  ?`<p class=prf>Name <b>${esc(d.name)}</b></p>`
   +`<p class=prf>Voice <b>${prTxt(d.voice)}</b></p>`
   +`<p class=prf>Tone <b>${prTxt(d.tone)}</b> <span class=m>an existing persona keeps its name, voice and tone</span></p>`
  :`<label class=prf>Name <input id="prnewname" value="${esc(d.name)}"></label>`
   +`<label class=prf>Voice <select id="prnewvoice">${voices.map(v=>`<option${String(v)===String(d.voice)?" selected":""}>${prTxt(v)}</option>`).join("")}</select></label>`
   +`<label class=prf>Tone <span class=m>at most ${limit} characters</span><textarea id="prnewtone" rows=3${max==null?"":` maxlength="${max}"`}>${esc(d.tone)}</textarea></label>`;
 return `<section class=prgrp id=pradd><h4>${title}</h4>`
 +fields
 +`<fieldset class=prf><legend>Projects</legend>${all.map(p=>`<label><input type=checkbox data-praddproj="${prTxt(p.id)}"${d.projects.includes(String(p.id))?" checked":""}> ${prTxt(p.label)} <span class=prfile>${prFile(p.file)}</span></label>`).join("")}</fieldset>`
 +`<div class=prbar><button data-pradd-preview>Preview</button> <button data-pradd-sample>Play sample</button> <button data-pradd-confirm>Confirm</button> <button data-pradd-cancel>Cancel</button></div>`
 +(prAddMsg?`<p class="mx" role=status>${prTxt(prAddMsg.replace(/^!/,""))}</p>`:"")+prAddPlanHtml()+`</section>`}
function prEditor(){const d=prEd;if(!d)return"";const voices=(prS||{}).voices||[],max=(prS||{}).tone_max,pat=(prS||{}).name_pattern;
 const limit=max==null?"the policy's":prTxt(max);
 if(d.mode==="copy")return `<section class=prgrp id=predit><h4>Use a copy of ${prTxt(d.id)}</h4>`
  +`<fieldset class=prf><legend>Copy</legend>${(d.copies||[]).map(c=>`<label><input type=radio name=prcopy data-prcopy="${prTxt(c.project)}"${String(c.project)===String(d.copy)?" checked":""}> ${prTxt(c.label)} <span class=prfile>${prFile(c.file)}</span> — <b>${prTxt(c.name)}</b> · ${prTxt(c.voice)} · ${prTxt(c.tone)}</label>`).join("")}</fieldset>`
  +`<p class=m>The preview writes the chosen copy into each project of this client that declares ${prTxt(d.id)}.</p>`
  +`<div class=prbar><button data-predit-preview>Preview</button> <button data-predit-confirm>Confirm</button> <button data-predit-cancel>Cancel</button></div>`
  +(prPMsg?`<p class="mx" role=status>${prTxt(prPMsg.replace(/^!/,""))}</p>`:"")+prPlanHtml()+`</section>`;
 return `<section class=prgrp id=predit><h4>Edit ${prTxt(d.name)}</h4>`
 +`<label class=prf>Name <input id=prname value="${esc(d.name)}"${pat?` pattern="${esc(pat)}" title="letters, digits, space, period, hyphen and underscore, at most 32"`:""}></label>`
 +`<label class=prf>Voice <select id=prvoice>${voices.map(v=>`<option${String(v)===String(d.voice)?" selected":""}>${prTxt(v)}</option>`).join("")}</select></label>`
 +`<label class=prf>Tone <span class=m>at most ${limit} characters</span><textarea id=prtone rows=3${max==null?"":` maxlength="${max}"`}>${esc(d.tone)}</textarea></label>`
 +`<fieldset class=prf><legend>Projects</legend>${(d.all||[]).map(p=>`<label><input type=checkbox data-prproj="${prTxt(p.id)}"${d.projects.includes(p.id)?" checked":""}> ${prTxt(p.label)} <span class=prfile>${prFile(p.file)}</span></label>`).join("")}</fieldset>`
 +`<div class=prbar><button data-predit-preview>Preview</button> <button data-predit-sample>Play sample</button> <button data-predit-confirm>Confirm</button> <button data-predit-cancel>Cancel</button></div>`
 +(prPMsg?`<p class="mx" role=status>${prTxt(prPMsg.replace(/^!/,""))}</p>`:"")+prPlanHtml()+`</section>`}
/* A row (#3036): the flags sit under the name, so no column stays empty; each project opens its node in the Slack tree;
   a default names its client by the name the groups carry; Edit and Add to project share one line. */
const prClientName=id=>{const g=((prS||{}).groups||[]).find(x=>String(x.id)===String(id));return g?g.name:id};
function prRow(r,client){
 const cell=(label,body)=>`<td data-l="${label}"><span class=pv>${body}</span></td>`;   // one item for the value: a bare text node would take the label's own grid cell
 const copies=r.drift?(r.copies||[]).map(c=>`<span class=prcopy><b>${prTxt(c.name)}</b> <span class=m>${prTxt(c.voice)} · ${prTxt(c.tone)}</span> <button data-prcopy="${prTxt(c.project)}" data-prclient="${prTxt(client)}">Use this copy</button></span>`).join("<br>"):"";
 const projects=((r.projects||[]).map(p=>`<span class=prproj><a href="#set-slk" data-sp=slk data-slproj="${prTxt(p.label)}" title="Open in the Slack tree">${prTxt(p.label)}</a> <span class=prfile>${prFile(p.file)}</span></span>`).join("")||"&mdash;")+(copies?`<br>${copies}`:"");
 const flags=[r.drift?'<span class="pflag err">drift</span>':"",r.read_only?"<span class=pflag>read-only</span>":""].join("");
 const edit=r.read_only?"":`<button data-predit="${prTxt(r.id)}" data-prclient="${prTxt(client)}">Edit</button>`;   // a local-only persona is read-only
 const add=`<button data-praddrow="${prTxt(r.id)}" data-prclient="${prTxt(client)}">Add to project</button>`;   // the same persona reaches a project that does not declare it (#3013)
 const name=`<b>${prTxt(r.name)}</b>${String(r.id)!==String(r.name)?` <span class=m>${prTxt(r.id)}</span>`:""}`+(flags?`<span class=prflags>${flags}</span>`:"");
 return `<tr${prFocused(r,client)?" class=prfocus":""}><td data-l="Persona"><span class=pv>${name}</span></td>`
 +cell("Voice",`<span class=nw>${prTxt(r.voice)||"&mdash;"}</span>`)+cell("Tone",prTxt(r.tone)||"&mdash;")
 +cell("Projects",projects)+cell("Default of",(r.default_of||[]).map(c=>prTxt(prClientName(c))).join(", ")||"&mdash;")
 +`<td data-l="Actions"><span class="pv pract">${edit}${add}</span></td></tr>`}
/* The row a Slack link names (#3036): the persona id inside the group whose row declares that project. */
const prFocused=(r,client)=>!!prFocus&&String(r.id)===String(prFocus.id)&&(!prFocus.project||(r.projects||[]).some(p=>String(p.label)===String(prFocus.project)));
/* The editor opens under the row it edits (#3036), so the reader never looks for it at the top of the theme. */
const prOpenHere=(d,r,client)=>!!d&&d.id!=null&&String(d.id)===String(r.id)&&(d.client==null||String(d.client)===String(client));
function prGroupRows(g){return g.rows.map(r=>{let h=prRow(r,g.id);
  if(prOpenHere(prEd,r,g.id))h+=`<tr class=preditrow><td colspan=6>${prEditor()}</td></tr>`;
  else if(prOpenHere(prNew,r,g.id))h+=`<tr class=preditrow><td colspan=6>${prAddEditor()}</td></tr>`;
  return h}).join("")}
function prRender(){const el=$("#prbody");if(!el)return;
 const groups=((prS||{}).groups||[]).filter(g=>(g.rows||[]).length),rows=groups.reduce((n,g)=>n+g.rows.length,0);
 const txt=rows?`${prPlural(rows,"persona")} · ${prPlural(groups.length,"client")}`:"no persona";
 for(const sel of ["#prsnav","#prssr"]){const el=$(sel);if(el)el.textContent=txt}   // the nav line and the panel's own chip read one text
 const ban=prMsg?`<p class="mx err" role=status>${prTxt(prMsg.replace(/^!/,""))}</p>`:"";
 const fresh=prNew&&prNew.id==null;   // a new persona belongs to no row, so its editor opens above the groups
 const bar=(prEd||prNew)?"":`<div class=prbar><button data-pradd>New persona</button></div>`;   // the one door to a new persona (#3013)
 el.innerHTML=ban+bar+(fresh?prAddEditor():"")+(groups.length?groups.map(g=>`<section class=prgrp><h4>${prTxt(g.name)}</h4><table class=ptab><thead><tr><th>Persona</th><th>Voice</th><th>Tone</th><th>Projects</th><th>Default of</th><th aria-label="Actions"></th></tr></thead><tbody>${prGroupRows(g)}</tbody></table></section>`).join("")
  :`<p class=m>${prTxt("No persona: no roster project declares one.")}</p>`)}
// }PRS
/* The Personas editor's own events (#3011): a field stages its value and posts nothing, so no edit of the theme writes
   before the confirm. The preview and the confirm are the two buttons that post, and the confirm is the one write. */
$("#prbody").addEventListener("click",e=>{const b=e.target.closest("button");if(!b)return;const dt=b.dataset||{};
 if(dt.predit){prStartEdit(dt.predit,dt.prclient);return}
 if(dt.prcopy!==undefined&&dt.prclient!==undefined){prCopyStart(dt.prclient,dt.prcopy);return}
 if(dt.preditPreview!==undefined){prPost(false);return}
 if(dt.preditConfirm!==undefined){prPost(true);return}
 if(dt.preditSample!==undefined){prSample();return}
 if(dt.preditCancel!==undefined){prStopEdit();return}
 if(dt.pradd!==undefined){prStartAdd(null);return}   // a new persona (#3013)
 if(dt.praddrow){prStartAdd(dt.praddrow,dt.prclient);return}     // an existing persona to another project (#3013)
 if(dt.praddPreview!==undefined){prAddPost(false);return}
 if(dt.praddConfirm!==undefined){prAddPost(true);return}
 if(dt.praddSample!==undefined){prAddSample();return}
 if(dt.praddCancel!==undefined){prStopAdd()}});
$("#prbody").addEventListener("input",e=>{const t=e.target;
 if(prEd){if(t.id==="prname"){prEd.name=t.value;return}
  if(t.id==="prtone"){prEd.tone=t.value;return}}
 if(prNew){if(t.id==="prnewname"){prNew.name=t.value;return}
  if(t.id==="prnewtone"){prNew.tone=t.value}}});
$("#prbody").addEventListener("change",e=>{const t=e.target,dt=t.dataset||{};
 if(prEd&&dt.prcopy!==undefined&&prEd.mode==="copy"){const c=(prEd.copies||[]).find(x=>String(x.project)===String(dt.prcopy));
  if(c){prEd.copy=c.project;prEd.name=c.name;prEd.voice=c.voice;prEd.tone=c.tone;prRender()}return}
 if(prEd){if(t.id==="prvoice"){prEd.voice=t.value;return}
  if(dt.prproj!==undefined){const ids=new Set(prEd.projects);if(t.checked)ids.add(dt.prproj);else ids.delete(dt.prproj);
   prEd.projects=[...ids];return}}
 if(prNew){if(t.id==="prnewvoice"){prNew.voice=t.value;return}
  if(dt.praddproj!==undefined){const ids=new Set(prNew.projects);if(t.checked)ids.add(dt.praddproj);else ids.delete(dt.praddproj);
   prNew.projects=[...ids]}}});
/* Settings (#913): a left theme list (a select on the phone) shows one theme at a time, and one page-level Save shows
   the diff of every theme with a change, each line naming its theme, and writes only after the confirm. Spend & limits
   writes each field on its own and keeps no button (#2433). Access has no Save: the bar says so. */
const STH={lim:"Spend & limits",sub:"Subscriptions",mdl:"Models & chains",slk:"Slack",prs:"Personas",acc:"Access"};
const SET_FIRST="lim";   // the first theme left: a saved Loop link and a stored Loop theme open it (#2515)
const SET_SAVE=["mdl","slk","sub"];   // the themes the one Save writes; Access and Spend & limits stage no file here
let sTheme=STH[ls.sth]?ls.sth:SET_FIRST,sPend=null;   // a removed theme opens the first one left (#2515)
let subS=null,subDraft=null,subData=null,subSrv="",subMsg="";   // the plans: the saved answer, the staged draft, and the last read (#1281)
const mdlDirty=()=>mdlChanged();
const subDirty=()=>!!subDraft&&subSrv!==JSON.stringify(subDraft);   // Subscriptions stages each change: the Save asks the hub for a diff (#1281)
const setDirty=()=>[["mdl",mdlDirty()],["sub",subDirty()]].filter(x=>x[1]).map(x=>x[0]);
/* The Save bar is always visible (#1278): a read-only theme reads "nothing to save here" with both buttons disabled,
   never a bar that disappears and leaves the reader guessing whether anything was lost. Spend & limits hides the two
   buttons there, because it writes each field on its own and a button would name a write that already happened (#2433). */
function setBar(){const bar=$("#sbar");if(!bar)return;const d=setDirty(),ro=RO||!SET_SAVE.includes(sTheme),solo=sTheme==="lim"||sTheme==="slk"||sTheme==="prs";bar.hidden=false;
 const sb=sTheme==="slk"&&!RO&&typeof slBarState==="function"?slBarState():null;   // Slack saves each edit by itself (#2908)
 $("#sundo").hidden=!(sb&&sb.undo);$("#sretry").hidden=!(sb&&sb.retry);$("#schg").classList.toggle("err",!!(sb&&sb.err));
 bar.classList.toggle("dirty",d.length>0&&!ro&&!solo);
 $("#ssave").hidden=solo;$("#sdisc").hidden=solo;
 $("#ssave").disabled=ro||!d.length;$("#sdisc").disabled=ro||!d.length;
 $("#schg").textContent=RO?RO_WHY:sb?sb.text:sTheme==="slk"?"Slack: each edit saves by itself":sTheme==="prs"?"Personas: each edit saves on its Confirm, after its preview":solo?"Spend & limits: each field saves as you set it":!SET_SAVE.includes(sTheme)?"nothing to save here":d.length?"Unsaved: "+d.map(k=>STH[k]).join(", "):`${STH[sTheme]}: no change`}
function setTheme(sp){if(!STH[sp])sp=SET_FIRST;sTheme=sp;ls.sth=sp;save();
 document.querySelectorAll("#tab-set details.sp").forEach(e=>{const on=e.dataset.sp===sp;e.classList.toggle("on",on);
  if(on&&!e.open)e.open=true;else if(!on&&e.open)e.open=false});   // one theme shows at a time (#1278)
 document.querySelectorAll(".snav button").forEach(b=>b.classList.toggle("on",b.dataset.sp===sp));$("#ssel").value=sp;setBar();
 if(sp==="lim"){limTickStart();kanTickStart()}else{limTickStop();kanTickStop()}   // the age tickers run only while the loaded theme is on screen (#2433)
 if(sp==="prs"||!prS)prLoad()}   // the nav line names the personas before the theme is opened (#3036)
$(".snav").addEventListener("click",e=>{const b=e.target.closest("button[data-sp]");if(b){prFocus=null;setTheme(b.dataset.sp)}});   // the theme list opens a theme with no row named
$("#ssel").addEventListener("change",e=>setTheme(e.target.value));
document.addEventListener("click",e=>{const a=e.target.closest&&e.target.closest("a[data-sp]");if(!a)return;
 e.preventDefault();const dt=a.dataset||{};
 if(dt.sp==="prs")prFocus=dt.prfocus?{id:dt.prfocus,project:dt.prproj||""}:null;   // a persona name opens its own row (#3036)
 if(dt.sp==="slk"&&dt.slproj!==undefined)slTGoProject(dt.slproj);   // a project of a persona opens its node in the Slack tree (#3036)
 setTab("set");setTheme(dt.sp)});   // #1278: a theme named inside another theme opens that theme
/* The Slack tree node of a project, by the name the Personas theme shows (#3036). A name the tree does not hold keeps
   the current selection. */
function slTGoProject(name){const walk=n=>!n?null:n.kind==="project"&&String(n.name)===String(name)?n:((n.children||[]).map(walk).find(Boolean)||null);
 for(const r of slTRoots()){const hit=walk(r);if(hit){if(hit.id!==slTsel){slTFlush();slConfirm=null}slTsel=hit.id;slTF=hit.id;slTshow=slTNarrow();
  for(const x of slTPath(hit.id))slTopen[x.id]=true;slRender();return true}}return false}
const setAskOne=async(url,body,theme,file)=>{try{const r=await (await fetch(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({...body,confirm:false})})).json();
 return r.err?{file,lines:["! "+themeLine(theme,r.err)]}:{file,lines:(r.diff||[]).map(l=>themeLine(theme,l))}}catch(x){return{file,lines:["! "+theme+" · the diff could not be read"]}}};
/* The Save dialog groups its lines by the file they would touch (#1278), each group headed by the path the server named. */
$("#ssave").onclick=async()=>{const d=setDirty(),groups=[];sPend=d;
 if(d.includes("mdl"))groups.push({file:(mdl&&mdl.file)||"(the project files)",lines:mdlDiffLines(JSON.parse(mdlSrv),mdlOut(),MLAB)});
 if(d.includes("sub"))groups.push(await setAskOne("/api/plans",subPayload(),"Subscriptions",(subData&&subData.file)||"(the plans file)"));
 const bad=groups.some(g=>g.lines.some(l=>l.startsWith("!"))),any=groups.some(g=>g.lines.length);
 $("#sdpre").textContent=diffByFile(groups)||"No change to save.";$("#sdok").disabled=!any||bad;$("#sdlg").showModal()};
$("#sdno").onclick=()=>{sPend=null;$("#sdlg").close()};
$("#sdok").onclick=async()=>{const d=sPend||[];sPend=null;$("#sdlg").close();
 if(d.includes("mdl")){try{const r=await mdlPost(mdlOut());if(r.err)mdlMsg="!"+r.err;else{mdl=r;mdlSrv=JSON.stringify(r);mdlRmk={};mdlMsg="saved · new runs use it"}}catch(x){mdlMsg="!save failed"}mdlRender()}
 if(d.includes("sub")){const r=await subPost(true);subMsg=r.err?"!"+r.err:"saved";subRender()}
 setBar();barSay([mdlMsg,kanMsg,slotMsg,slMsg,subMsg])};
// The verdict of a save belongs where the Save button is: the panel's own line sits at the end of a long scrolling
// column, so a refusal read there as "nothing happened" (owner, 2026-11-01).
const barSay=ms=>{const b=$("#schg");if(!b)return;const say=ms.filter(Boolean);if(!say.length)return;
 const bad=say.some(m=>m.startsWith("!"));b.textContent=say.map(m=>m.replace(/^!/,"")).join(" · ");
 b.classList.toggle("err",bad);b.classList.toggle("ok",!bad)};
// The explicit revert drops a refused value and redraws the saved state (#2433).
$("#sdisc").onclick=()=>{if(mdl){mdl=JSON.parse(mdlSrv);mdlRmk={};mdlMsg="";mdlRender()}if(limS){limMsg="";limBad="";limBadMsg="";limSent={};limTmr={};limRender()}
 if(subS){subDraft=subClone(subS);subSrv=JSON.stringify(subS);subMsg="";subRender()}
 if(kanS){kanDraft=kanClone(kanS.columns||[]);kanMsg="";kanSent={};kanRef={};kanBad="";kanBadMsg="";kanDiff=[];kanStaged={};limRender()}
 if(slotS){slotDraft=slotClone(slotS);slotDiff=null;slotMsg="";limRender()}setBar()};
$("#sundo").onclick=()=>slUndoLast();$("#sretry").onclick=()=>{const f=slRetryFn;slRetryFn=null;if(f)f()};
// A pending Slack edit or a save in flight asks the browser's leave prompt before the page closes (#2908). A page run with no
// window, as a test runs it in node, skips the prompt (#2914).
if(typeof window!=="undefined"&&typeof window.addEventListener==="function")window.addEventListener("beforeunload",e=>{if(slDirty()||slSaving||slProposeT){e.preventDefault();e.returnValue=""}});
/* accordions: each of the five answers its own header and leaves the others alone; the open set is stored like the pins */
const ACC=["stats","mdl","lim","slk"];   // only the cards the page carries: a name with no element used to kill the whole script (#1032)
let openAcc=new Set(Array.isArray(ls.acc)?ls.acc.filter(i=>ACC.includes(i)):[]);   // the old single-id string is ignored, not trusted, and not fatal
const saveAcc=()=>{ls.acc=[...openAcc];save()};
ACC.forEach(i=>{const e=$("#"+i);if(!e)return;e.addEventListener("toggle",()=>{if(e.open)openAcc.add(i);else openAcc.delete(i);saveAcc();
 if(i==="mdl"&&e.open){mdlLoad()}else if(i==="lim"&&e.open){limLoad()}else if(i==="slk"&&e.open){slLoad()}});
 if(openAcc.has(i))e.open=true});   // a panel stored open loads through its own toggle, once (#1278)
/* A refused write gets every control disabled and the reason stated (#1278); the observer keeps a render from re-enabling
   what it drew after the first pass. The loop switch is the hub's to gate: it follows the `write` field, never this flag. */
if(RO){const ro=$("#ro");if(ro){ro.hidden=false;ro.textContent=RO_WHY}roWatch($("#tab-set"))}
setTheme(sTheme);
if(!$("#mdl").open)mdlLoad();   // the models panel answers on boot while it is closed; an open panel already loaded (#1278)
if(!$("#lim").open)limLoad();   // Spend & limits answers on boot too, so its chip and its Kanban sub-group never read a dash (#2432)
const PCOL=["var(--chart-series-1)","var(--chart-series-2)","var(--chart-series-3)","var(--chart-series-4)","var(--chart-series-5)","var(--chart-series-6)","var(--chart-series-7)","var(--chart-series-8)"],POCOL="var(--chart-series-other)",tk=n=>n>=1e9?(n/1e9).toFixed(2)+"B":n>=1e6?(n/1e6).toFixed(1)+"M":n>=1e3?(n/1e3).toFixed(0)+"K":""+n;   // "other" keeps its own role, out of the model palette, so the fold never wears a model's colour (#2503)
const TOP=3;
function pie(ms){
 // One slice per model NAME, never per spelling: one model answers under several (deepseek-flash, deepseek-flash[1m],
 // claude-deepseek-flash), and keying the rows on the raw id drew the same label three times (#1750).
 const byName=new Map();
 for(const m of ms){const v=m.month_tokens;if(!(v>0))continue;
  const nm=m.box&&m.box.registered===false?m.model+" · unregistered":(m.box&&m.box.llm)||m.model;
  const k=(m.consumer==="operator"?"operator · ":"")+nm;   // the operator session's calls are their own slices, never the loop's (#1756)
  const r=byName.get(k)||{n:k,v:0,ids:[],routes:[]};
  r.v+=v;
  if(!r.ids.includes(m.model))r.ids.push(m.model);
  for(const rt of (m.box&&m.box.routes)||[])if(!r.routes.includes(rt))r.routes.push(rt);
  byName.set(k,r)}
 const rows=[...byName.values()].sort((a,b)=>b.v-a.v);if(!rows.length)return"<span class=m>no tokens this month</span>";
 const tot=rows.reduce((a,m)=>a+m.v,0),keep=rows.slice(0,TOP),fold=rows.slice(TOP);
 const sl=keep.map(m=>({k:m.n,n:m.n,v:m.v,t:`${m.n} (${m.ids.join(", ")})${m.routes.length?" via "+m.routes.join(", "):""}: ${tk(m.v)} tokens`}));
 if(fold.length){const v=fold.reduce((a,m)=>a+m.v,0);sl.push({k:"other",n:`other · ${fold.length} models`,v,t:fold.map(m=>`${m.n}: ${tk(m.v)} tokens`).join("\n")})}
 const GONE=new Set(),ROLE=new Map(),col=k=>{   // a model keeps its colour across renders, never takes the fold's role, and never shares a role with another slice (#2503)
  if(k==="other")return POCOL;
  if(!ROLE.has(k)){let i=[...String(k)].reduce((h,c)=>(h*31+c.charCodeAt(0))>>>0,0)%PCOL.length,n=0;
   while(GONE.has(PCOL[i])&&n++<PCOL.length)i=(i+1)%PCOL.length;   // two names on one bucket take the next free role
   GONE.add(PCOL[i]);ROLE.set(k,PCOL[i])}
  return ROLE.get(k)};
 const dot=(k,n,v)=>`<i style="background:${col(k)}"></i><span class=ln>${esc(n)}</span><span class=usd>${tk(v)}</span><span class=sh>${Math.round(v/tot*100)}%</span>`;   // the rows are built outside the page template: a nested backtick there breaks the pie matcher in tests
 sl.forEach(x=>col(x.k));   // the drawn slices claim their roles before the fold does, so a full palette never makes two arcs one colour (#2503)
 const subRows=fold.map(m=>`<div>${dot(m.n,m.n,m.v)}</div>`).join("");   // built outside the legend template: a nested backtick there breaks the pie matcher in tests
 const legendRows=sl.map(x=>x.k==="other"
   ?`<details class=lgo data-k="other"><summary tabindex=0 title="${esc(x.t)}">${dot(x.k,x.n,x.v)}</summary><div class=lgx>${subRows}</div></details>`
   :`<div tabindex=0 data-k="${esc(x.k)}" title="${esc(x.t)}">${dot(x.k,x.n,x.v)}</div>`).join("");
 let a0=-Math.PI/2;const R=70,C=80,arc=sl.map((x,i)=>{const a1=a0+x.v/tot*2*Math.PI,big=a1-a0>Math.PI?1:0,p=sl.length===1?`<circle cx=${C} cy=${C} r=${R} data-k="${esc(x.k)}" fill="${col(x.k)}"><title>${esc(x.t)}</title></circle>`:`<path d="M${C} ${C}L${C+R*Math.cos(a0)} ${C+R*Math.sin(a0)}A${R} ${R} 0 ${big} 1 ${C+R*Math.cos(a1)} ${C+R*Math.sin(a1)}Z" data-k="${esc(x.k)}" fill="${col(x.k)}" stroke="var(--chart-bg)" stroke-width=1><title>${esc(x.t)}</title></path>`;a0=a1;return p}).join("");
 // The hole carries the total and its caption (#2503): the hole is painted wide enough for the whole line, and the
 // caption reads in full contrast, so no character vanishes where it meets a slice.
 return `<div class=pie><svg viewBox="0 0 160 160" width=128 height=128>${arc}<circle cx=${C} cy=${C} r=48 fill="var(--chart-bg)"/><text x=${C} y=${C-1} text-anchor=middle fill="var(--fg)" style="font-size:var(--fs-caption)" font-weight=600>${tk(tot)}</text><text x=${C} y=${C+12} text-anchor=middle fill="var(--fg)" style="font-size:var(--fs-caption)">tokens · month</text></svg><div class=lg>${legendRows}</div></div>`}
function hlPie(k){document.querySelectorAll("#pie [data-k]").forEach(el=>el.classList.toggle("hl",!!k&&el.dataset.k===k));document.querySelector("#pie .pie")?.classList.toggle("dim",!!k)}
$("#pie").addEventListener("mouseover",e=>{const el=e.target.closest("[data-k]");hlPie(el?el.dataset.k:"")});
$("#pie").addEventListener("mouseout",e=>{if(!$("#pie").contains(e.relatedTarget))hlPie("")});
$("#pie").addEventListener("focusin",e=>{const el=e.target.closest("[data-k]");hlPie(el?el.dataset.k:"")});
$("#pie").addEventListener("focusout",()=>hlPie(""));
const qcol=p=>`hsl(${Math.round(120*(1-Math.min(Math.max(+p||0,0),100)/100))} 65% 42%)`;   // a quota bar fills green when empty and red when full; only the fill takes colour (owner)
const lvl=p=>p>=90?"lv-err":p>=60?"lv-warn":"lv-ok";
const fpct=p=>p<10&&p>0?p.toFixed(1):String(Math.round(p));   // carried into the phone's Monitor (#1632)
const qbar=(label,pct,resets,sm,tip)=>{const left=resets?resets-Date.now()/1000:null,when=left==null?"":(left<=0?"resets now":"resets in "+span(left));
 return `<div class="qw${sm?" sm":""}"><div class=ql><span>${esc(label)} <b>${fpct(pct)}%</b></span><span class=rst>${esc(when)}${resets?" · "+esc(moment(resets)):""}</span></div><div class=tr title="${esc(tip)}"><div class="fl ${lvl(pct)}" style="width:${Math.min(pct,100)}%;background:${qcol(pct)}"></div></div></div>`};
const autoq=a=>{const QT="Quota used, read from the provider's own CLI. 100% = quota exhausted.",aged=a.asof?span(Date.now()/1000-a.asof)+" ago":"";if(a.unavailable)return `<div class=fr><span class="chip err">unavailable</span><span class=fs title="${esc(a.err)}">${esc(a.err)}</span>${a.asof?`<span class=fs>· last probed ${aged}</span>`:""}</div>`;return a.windows.map((w,i)=>qbar(w.label,w.pct,w.resets,i>0,QT)).join("")+`<div class=fr>${a.reached?`<span class="chip err">limit reached</span>`:""}<span class=fs>CLI · ${aged}</span></div>`};
const CUR={"R$":"BRL","U$":"USD","US$":"USD","$":"USD","€":"EUR","£":"GBP","¥":"JPY"},curOf=v=>{v=v.trim().toUpperCase();return CUR[v]||v};
const subClone=v=>JSON.parse(JSON.stringify(v));
const subPayload=()=>{const out={};   // only what changed, so a quota the owner typed alone travels as a quota, never as a plan rewrite
 for(const[k,o]of Object.entries(subDraft||{})){const a=subS?subS[k]:null;if(JSON.stringify(o)===JSON.stringify(a))continue;
  out[k]=o&&a&&o.plan===a.plan&&o.amount===a.amount&&o.currency===a.currency?{quota:o.quota??null,qwin:o.qwin??null}:o}
 return out};
function subRender(d){d=d||subData;if(!d||!$("#subs"))return;
 const lims=(d.gauges||[]).filter(g=>typeof g.pct==="number"&&g.pct>=100),any=Object.values(d.subs).some(v=>v.saved!=null);
 setChip("sub",lims.length?`${lims.length} at limit`:(any||d.api?"all under limit":"no plan chosen"))   // the chip says what needs a look; the prices stay in the settings block
 $("#subnow").innerHTML=setNow("sub",[
  setTile("At limit",`${lims.length}`,lims.length?"err":"ok"),
  setTile("Cash this month",any||d.api?usd(d.total):"no plan chosen","neutral"),
  setTile("Month passed",Math.round((d.frac||0)*100)+"%","neutral"),
  setTile("Plans file",d.file||"?","neutral","The one file Save writes: one JSON entry per subscription.")]);   // the theme says where it writes (#1281)
 $("#subs").innerHTML=setSettings("sub",[`<div class=subx>Per subscription: quota used, as each provider's CLI reports it. With no reading, the bar is the API-equivalent usage vs the plan price.</div><datalist id=curs>${["USD","BRL","EUR","GBP","ARS","MXN","CAD","JPY"].map(c=>`<option value=${c}>`).join("")}</datalist>`+Object.entries(d.subs).map(([k,v])=>{const pl=(subDraft||{})[k],ok=pl!=null,pr=d.presets[k],custom=pl&&pl.plan==="custom";
  const sel=`<select data-plan=${k} data-f=plan aria-label="${k} plan"><option value="" ${pl?"":"selected"}>choose a plan…</option>${Object.entries(pr).map(([i,p])=>`<option value=${i} ${pl&&pl.plan===i?"selected":""}>${esc(p.name)} · US$${p.usd}</option>`).join("")}<option value=custom ${custom?"selected":""}>Custom amount…</option></select>`;
  const cust=custom?`<span class=pr><span>amount</span><input data-plan=${k} data-f=amount type=number min=0 step=0.01 value="${pl.amount}" aria-label="${k} custom amount"> <input data-plan=${k} data-f=cur list=curs value="${esc(pl.currency)}" placeholder="BRL" aria-label="${k} currency" title="R$, U$, € or any 3-letter code"></span>`:"";
  const note=v.noRate?`<span class=warn>no exchange rate for ${esc(pl.currency)} right now</span>`:(pl&&pl.currency!=="USD"&&v.usd!=null?`<span class=dt>≈ <b>US$${v.usd}</b> / month · ${(+v.rate).toFixed(2)} ${esc(pl.currency)} per US$</span>`:"");
  const gauge=(d.gauges||[]).find(g=>g.key===k),bar=gauge?`<div class=isub>${subsGauges({subs:[gauge]})}</div>`:"";   // the sidebar usage card's own bar and text, from the same answer (#1281)
  const win=pl&&pl.qwin||"",man=!pl?""   // the hand-entered quota follows the saved plan, never the live window: with a plan saved the owner can correct or clear the stored quota even when the CLI reports one (#1281)
   :`<div class=pr><span>quota used</span><input data-plan=${k} data-f=quota type=number min=0 max=100 step=1 value="${pl.quota??""}" placeholder="%" aria-label="${k} quota used" style="width:60px"><input data-plan=${k} data-f=qwin value="${esc(win)}" placeholder="window, e.g. weekly · all models" aria-label="${k} quota window"maxlength=40 style="flex:1;min-width:0"></div>`;
  return `<div class=sub><div class=hd><span class=nm>${esc(v.name)}</span>${ok?"":`<span class="sv none">choose a plan</span>`}</div><div class=pr>${sel}</div>${cust}${note}${bar}${man}</div>`}).join("")]);
 setBar()}   // a staged change arms the one Save at once, never on the next poll (#1281)
async function subPost(confirm){const r=await (await fetch("/api/plans",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({...subPayload(),confirm:!!confirm})})).json();
 if(confirm&&!r.err){subDraft=null;subSrv="";await pollSubs()}   // the written answer is the new baseline, so the bar clears
 return r}
async function pollSubs(){try{const d=await (await fetch("/api/subs")).json(),ae=document.activeElement;
 if(ae&&ae.closest&&ae.closest("#subs")&&ae.tagName==="INPUT")return;   // a field being typed is never repainted
 subData=d;const saved={};for(const[k,v]of Object.entries(d.subs))saved[k]=v.plan||null;
 if(!subDirty()){subS=saved;subSrv=JSON.stringify(saved);subDraft=subClone(saved)}   // a staged draft is the reader's: a poll never overwrites it (#1281)
 subRender(d)}catch(e){console.error(e)}}
$("#subs").addEventListener("change",e=>{const k=e.target.dataset.plan;if(!k||!subDraft)return;const row=e.target.closest(".sub");
 const f=e.target.dataset.f,pick=row.querySelector("[data-f=plan]").value;let v;
 if(f==="quota"||f==="qwin"){const x=row.querySelector("[data-f=quota]").value,w=row.querySelector("[data-f=qwin]").value.trim();v={...(subDraft[k]||{plan:"custom",amount:0,currency:"USD"}),quota:x===""?null:+x,qwin:w||null}}
 else if(f==="plan"){v=pick===""?null:pick==="custom"?{plan:"custom",amount:0,currency:"USD"}:{plan:pick}}
 else{const a=row.querySelector("[data-f=amount]"),c=row.querySelector("[data-f=cur]");c.value=curOf(c.value);v={plan:"custom",amount:+a.value||0,currency:c.value}}
 subDraft[k]=v;subMsg="";subRender()});   // a change stages; the one Save asks for the diff and writes on confirm (#1281)
// ---- subscriptions slice end: the helpers above are read by the Subscriptions theme (#1281); the stats poller follows
async function pollStats(){try{pollSubs();
  const st=await (await fetch("/api/stats?period="+per+"&src="+src)).json();
  $("#per").innerHTML=["hour","day","month"].map(p=>`<button data-p=${p} class="${p===per?"on":""}">per ${p}</button>`).join("");
  document.querySelectorAll("#per button").forEach(b=>b.onclick=()=>{per=b.dataset.p;save();pollStats()});
  {const p=st.pace,u=n=>n==null?"no runs":tk(n);   // the hub computed the readings; the page prints them in tokens (#1267)
   // the loop's own reading says so; the operator session's rides beside it (#1756)
   $("#stsr").textContent=`${u(p.median_tokens)} tokens/h median, ${p.active_hours} of ${p.window_hours} h with runs (last ${p.window_hours} h) · ${u(p.last60_tokens)} tokens last 60 min`+(p.unpriced?` · ${p.unpriced} unpriced call${p.unpriced>1?"s":""} (tokens counted)`:"")+` · ${tk(st.totals.tok_today)} loop tokens today`+(st.operator&&st.operator.tokens_today?` · operator ${tk(st.operator.tokens_today)} tokens today`:``);
  $("#chart").innerHTML=bars(st.buckets);$("#pie").innerHTML=pie(st.models||[]);}
  // The Speed block and the open-branches list left the panel (owner, 2026-11-01): the panel leads with the throughput, the
  // subscription gauges, the spend chart and the token ring. The PR speed figures stay on the hub's own reading, not here.
}catch(e){console.warn("stats panel:",e)}}
const pollIfVisible=fn=>()=>{if(!document.hidden)fn()};   // a hidden tab asks nothing (#2440); every hub read goes through it
pollStats();setInterval(pollIfVisible(pollStats),30000);
poll();setInterval(pollIfVisible(poll),3000);
loadPoll();setInterval(pollIfVisible(loadPoll),LOAD_MS);   // the window owns the reading: one timer and one /api/load per tick; the countdown ticks in place between readings, and its node is aria-live=off, so no tick is announced
const bo=ls.bo||{},saveB=()=>{ls.bo=bo;try{localStorage.hub=JSON.stringify({...ls,pins:[...pins],q,per,bo})}catch(e){}};
bo.dock="bottom";   // the board always docks at the bottom
dropRemoved(bo);   // a saved state from a control the board no longer has is dropped (#899)
const sizeBoard=()=>{$("#board").style.height=""};   // the board is one pane of the Board tab: its height is the page's, never a dragged one (#889)
const paintBoard=()=>{document.body.classList.add("dockb");sizeBoard()};
const applyDock=paintBoard;
paintBoard();
const isPR=n=>n===COLNAME.pr_open;   // the one lane beside the axis: open work branches, for tracking only (#384)
const esc=t=>String(t).replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
const OKTAG=new Set("details summary hr br b i strong em code kbd sub sup p div span ul ol li table thead tbody tr th td h1 h2 h3 h4 h5 h6 blockquote pre a img del s u".split(" ")),VOID=new Set(["br","hr","img"]);
const OKATTR={a:["href"],img:["src","alt","width","height"],td:["colspan","rowspan","align"],th:["colspan","rowspan","align"],details:["open"]};
function safeTag(close,name,attrs,self){name=name.toLowerCase();if(!OKTAG.has(name))return null;if(close)return VOID.has(name)?"":`</${name}>`;
let out="";for(const m of attrs.matchAll(/([a-zA-Z-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+)))?/g)){const k=m[1].toLowerCase(),v=m[2]??m[3]??m[4]??"";if(!(OKATTR[name]||[]).includes(k))continue;
if((k==="href"||k==="src")&&!/^https?:\/\//i.test(v))continue;out+=k==="open"?" open":` ${k}="${v.replace(/&/g,"&amp;").replace(/"/g,"&quot;").replace(/</g,"&lt;")}"`}
if(name==="a")out+=' target=_blank rel=noopener';if(name==="img")out+=' referrerpolicy=no-referrer loading=lazy';return `<${name}${out}>`}
function md(src){const keep=[],hold=h=>"\u0001"+(keep.push(h)-1)+"\u0001";
// 1) code fences and a small, attribute-filtered HTML allow-list are set aside before escaping; comments are dropped, anything else is shown as text
let t=(src||"").replace(/\r/g,"").replace(/<!--[\s\S]*?-->/g,"").replace(/```[^\n]*\n([\s\S]*?)```/g,(_,c)=>hold("<pre><code>"+esc(c)+"</code></pre>"));
t=t.replace(/<(\/?)([a-zA-Z][a-zA-Z0-9]*)((?:\s[^<>]*?)?)\s*\/?>/g,(all,c,n,a)=>{const r=safeTag(!!c,n,a);return r===null?all:hold(r)});
t=esc(t);
let irepo="__REPO__";   // the repository the open card belongs to: a `Refs #N` in its markdown links in THAT repository (#1820)
const inl=x=>x.replace(/`([^`\n]+)`/g,"<code>$1</code>").replace(/\*\*([^*\n]+)\*\*/g,"<b>$1</b>").replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,;:]|$)/g,"$1<i>$2</i>").replace(/\[([^\]\n]+)\]\((https?:[^)\s]+)\)/g,'<a target=_blank rel=noopener href="$2">$1</a>').replace(/(^|[\s(])#(\d{1,6})\b/g,`$1<a target=_blank rel=noopener href="https://github.com/${irepo}/issues/$2">#$2</a>`);
const onlyHold=l=>/^\s*(\u0001\d+\u0001\s*)+$/.test(l);
function block(txt){const out=[],L=txt.split("\n");let i=0;
while(i<L.length){const l=L[i];let m;
if(onlyHold(l)){out.push(l.trim());i++}
else if(m=l.match(/^(#{1,4})\s+(.*)$/)){out.push(`<h${m[1].length+1}>${inl(m[2])}</h${m[1].length+1}>`);i++}
else if(/^\s*(-{3,}|\*{3,})\s*$/.test(l)){out.push("<hr>");i++}
else if(/^\s*([-*]|\d+\.)\s+/.test(l)){const ord=/^\s*\d+\./.test(l),it=[];while(i<L.length&&(m=L[i].match(/^\s*(?:[-*]|\d+\.)\s+(.*)$/))){let c=m[1],ck="";const k=c.match(/^\[( |x|X)\]\s+(.*)$/);if(k){ck=`<span class="ck${k[1]===" "?"":" on"}"></span>`;c=k[2]}it.push(`<li>${ck}${inl(c)}</li>`);i++}out.push(`<${ord?"ol":"ul"}>${it.join("")}</${ord?"ol":"ul"}>`)}
else if(/^&gt;\s?/.test(l)){const q=[];while(i<L.length&&/^&gt;\s?/.test(L[i])){q.push(L[i].replace(/^&gt;\s?/,""));i++}
let kind="";const am=(q[0]||"").match(/^\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*$/i);if(am){kind=am[1].toLowerCase();q.shift()}
out.push(`<blockquote${kind?` class="alert ${kind}"`:""}>${kind?`<div class=at>${kind}</div>`:""}${block(q.join("\n"))}</blockquote>`)}
else if(/^\|.*\|\s*$/.test(l)){const rows=[];while(i<L.length&&/^\|.*\|\s*$/.test(L[i])){rows.push(L[i]);i++}const cells=r=>r.replace(/^\||\|\s*$/g,"").split("|").map(c=>inl(c.trim())),sep=rows[1]&&/^[\s|:-]+$/.test(rows[1]);
out.push("<table>"+rows.filter((_,j)=>!(sep&&j===1)).map((r,j)=>"<tr>"+cells(r).map(c=>sep&&j===0?`<th>${c}</th>`:`<td>${c}</td>`).join("")+"</tr>").join("")+"</table>")}
else if(!l.trim())i++;
else{const p=[];while(i<L.length&&L[i].trim()&&!onlyHold(L[i])&&!/^(#{1,4}\s|\s*([-*]|\d+\.)\s|&gt;|\|.*\||\s*-{3,}\s*$)/.test(L[i])){p.push(inl(L[i]));i++}out.push("<p>"+p.join("<br>")+"</p>")}}
return out.join("")}
let html=block(t);for(let k=0;k<3;k++)html=html.replace(/\u0001(\d+)\u0001/g,(_,n)=>keep[+n]);return html.replace(/<p>(<summary>[\s\S]*?<\/summary>)<\/p>/g,"$1")}
const tsheet=$("#tk"),when=t=>t.slice(0,16).replace("T"," ");
// The card data the page already holds rides the card (`data-*`); the sheet reads it, and the body and the last comment
// come from `/api/issue` on open. An empty array encodes as `%5B%5D`, so a card with no blockers decodes to `[]`.
const encArr=a=>encodeURIComponent(JSON.stringify(a||[])),decArr=s=>{try{return JSON.parse(decodeURIComponent(s||"%5B%5D"))||[]}catch(e){return []}};
const decQ=v=>{try{return decodeURIComponent(v||"")}catch(e){return v||""}};
const cardOf=el=>el&&el.dataset?{n:+el.dataset.n,r:el.dataset.r||"__REPO__",col:decQ(el.dataset.col),pm:el.dataset.pm==="1",
  b:decArr(decQ(el.dataset.b)),bl:decArr(decQ(el.dataset.bl)),ho:decQ(el.dataset.ho),hr:decQ(el.dataset.hr),prf:decArr(decQ(el.dataset.prf))}:null;
const boardCards=()=>[...document.querySelectorAll("#bbody .it[data-n]")];
let nav=[],cur=0,irepo="__REPO__";   // nav carries [number, repository] pairs: a number alone does not name a card now that two repositories fill the column (#1820)
const ni=()=>nav.findIndex(x=>x[0]===cur&&x[1]===irepo);
function openCard(n,r){const el=boardCards().find(x=>+x.dataset.n===+n&&(x.dataset.r||"__REPO__")===(r||"__REPO__"));if(el)showIssue(n,r,el);else{nav=[[+n,r||"__REPO__"]];showIssue(n,r)}}   // a blocker link opens the same sheet, never a new tab
const step=k=>{const i=ni()+k;if(i>=0&&i<nav.length)showIssue(nav[i][0],nav[i][1])};
// One close rule: the panel goes, the page widens, and the selection the run or the column drew is cleared.
function closeSheet(){tsheet.classList.remove("on");document.body.classList.remove("tkopen");document.querySelectorAll("#bbody .it.hit").forEach(x=>x.classList.remove("hit"));nav=[];cur=0;irepo="__REPO__";$("#tkset").innerHTML="";$("#tknav").innerHTML="";if(hl&&hl.run==null)hl=null;paintHL();escBoard()}
async function showIssue(n,r,el){cur=+n;irepo=r||"__REPO__";hl={n:+n,r:irepo,kind:boardRows.some(x=>x.kind==="pr"&&+x.n===+n&&x.r===irepo)?"pr":"issue"};paintHL();
 // A card a run holds steps through that run's cards; any other card steps through its column (the selection the board shows).
 if(el){const c=cardOf(el),set=c.hr?boardCards().filter(x=>(x.dataset.hr||"")===c.hr):[...el.closest(".sc").querySelectorAll(".it[data-n]")];nav=set.map(x=>[+x.dataset.n,x.dataset.r||"__REPO__"])}
 if(!nav.length)nav=[[cur,irepo]];
 tsheet.classList.add("on");document.body.classList.add("tkopen");$("#tksyncnote").textContent="";$("#tkb").innerHTML="<span class=m>Loading issue #"+n+".</span>";
try{const d=await (await fetch("/api/issue?n="+n+(r?"&r="+encodeURIComponent(r):""))).json();if(d.err){$("#tkb").textContent=d.err;return}
const c=cardOf(el)||cardOf(boardCards().find(x=>+x.dataset.n===+n&&(x.dataset.r||"__REPO__")===irepo))||{n:+n,r:irepo,col:"",pm:false,b:[],bl:[],ho:"",hr:"",prf:[]};
$("#tkchips").innerHTML=`${(d.labels||[]).includes("spec")?'<span class="chip spec">SPEC</span>':""}<span class=m>#${d.n}${d.r&&d.r!=="__REPO__"?" · "+esc(d.r):""}</span>`;
const k=ni();
$("#tknav").innerHTML=nav.length>1?`<button id=tkp aria-label="previous card" ${k>0?"":"disabled"}>‹</button><span class=m>${k+1} of ${nav.length}</span><button id=tkn aria-label="next card" ${k<nav.length-1?"":"disabled"}>›</button>`:"";
$("#tkset").innerHTML=nav.length>1?nav.map(([m,rr])=>`<button data-o="${m}" data-r="${rr}" class="${m===cur&&rr===irepo?"on":""}">#${m}</button>`).join(""):"";
$("#tkb").innerHTML=`<h2>${esc(d.title)}</h2>
<div id=tkfacts>${syncFactsHtml({r:irepo,col:c.col,labels:d.labels||[],b:c.b,bl:c.bl,prf:c.prf,ho:c.ho})}</div>
<div class=row><b>Opened</b><span>${when(d.created)} by ${esc(d.by)}</span></div>
<div class=sec>Issue</div>${d.html?`<div class="md tkbody">${d.html}</div>`:`<div class="md tkbody">${d.body?md(d.body):"<span class=m>(no description)</span>"}</div>`}
<div class=sec>Last comment</div>${d.last?`<div class="cm md"><div class=meta>${esc(d.last.by)} · ${when(d.last.at)}</div><div class=md>${d.last.html||(d.last.by?"":"")}</div></div>`:"<span class=m>no comments</span>"}`;
$("#tktodo").hidden=!(c.pm&&!d.pr);   // the owner's go signal, only for a card the board marks promotable (ADR-0142)
$("#tkgh").href=/^https:\/\/github\.com\//.test(d.url||"")?d.url:"#";
$("#tkb").scrollTop=0;
const shown=boardCards().find(x=>+x.dataset.n===+n&&(x.dataset.r||"__REPO__")===irepo);if(shown)shown.scrollIntoView({block:"nearest",inline:"nearest",behavior:"smooth"});
}catch(e){$("#tkb").textContent="failed to load"}}
tsheet.addEventListener("click",e=>{if(e.target.id==="tkp")step(-1);else if(e.target.id==="tkn")step(1);const b=e.target.closest("#tkset button");if(b)openCard(+b.dataset.o,b.dataset.r)});
$("#tkb").addEventListener("click",e=>{const a=e.target.closest("a[data-o]");if(a){e.preventDefault();openCard(+a.dataset.o,a.dataset.r||irepo)}});
$("#tkx").onclick=closeSheet;
$("#tkrel").onclick=()=>{const on=!nav.every(([m,rr])=>boardCards().some(x=>+x.dataset.n===m&&(x.dataset.r||"__REPO__")===rr&&x.classList.contains("hit")));nav.forEach(([m,rr])=>boardCards().forEach(x=>{if(+x.dataset.n===m&&(x.dataset.r||"__REPO__")===rr)x.classList.toggle("hit",on)}))};
$("#tktodo").onclick=()=>{const b=boardCards().find(x=>+x.dataset.n===cur&&(x.dataset.r||"__REPO__")===irepo),p=b&&b.querySelector(".pmp");if(p)p.click()};   // the one write the sheet makes is the card's own move
/*RUN_SHEET*/
// The run sheet (#984): the ticket sheet's shape, for a run. It reads the run row (/api/runs, /api/ended) and the run's record
// through the /dash/ proxy; an ended run with no dashboard server gets one from /ended/ first. Its one write is the pause
// request, the run dashboard's own control route (ADR-0128). The full run dashboard opens in a new tab.
const rsheet=$("#rk");let RK=null;   // RK: {id, row, rec, tab, f, paused}
const rkRow=id=>allRuns.find(r=>r.id===id)||ENDED.find(r=>r.id===id)||null;
const rkLive=r=>!!r&&(r.rank===0||!!r.running);
function rkPaint(force){if(!RK||!RK.row)return;const m=rkModel(RK.row,RK.rec||[]),c=m.chips;   // force: the person acted; a poll paints only what changed
 const ch=$("#rkchips"),chips=`<span class="chip ${c.state==="live"?"live":""}">${c.state==="live"?"&#9679; live":"ended"}</span><span class=chip title="${esc(c.id)}">${esc(c.id)}</span><span class=chip>${esc(c.cost)} · ${esc(c.el)}</span>`;if(ch.innerHTML!==chips)ch.innerHTML=chips;
 rkRender($("#rkb"),RK.rec?rkHtml(m,RK.tab,RK.f):`<h2>${esc(m.label||m.id)}</h2><p class=m>${RK.err?esc(RK.err):"Loading the record."}</p>`,document,force);
 $("#rkgh").href=rkLive(RK.row)&&RK.row.port?"/dash/"+encodeURIComponent(RK.id)+"/":"/ended/"+encodeURIComponent(RK.id);
 const p=$("#rkpause");p.textContent=rkLive(RK.row)?(RK.paused?"Resume run":"Pause run"):"Open its record";
 p.title=RK.note||(rkLive(RK.row)?(RK.paused?"a pause is requested: the run holds at its next node boundary (ADR-0128)":"request a pause: the run holds at its next node boundary (ADR-0128)"):"the full run dashboard, with the record and its replay")}
async function rkLoad(){if(!RK)return;const id=RK.id;
 try{if(!(RK.row&&RK.row.port)&&!RK.opened){RK.opened=true;const o=await fetch("/ended/"+encodeURIComponent(id));if(!o.ok){RK.err="no dashboard server for this run";rkPaint();return}}
  const t=await (await fetch("/dash/"+encodeURIComponent(id)+"/run/record.jsonl")).text();if(!RK||RK.id!==id)return;
  RK.rec=t.split("\n").map(l=>{try{return JSON.parse(l)}catch(e){return null}}).filter(Boolean);   // a torn last line is left out: the valid prefix is read
  if(rkLive(RK.row)){try{const c=await (await fetch("/dash/"+encodeURIComponent(id)+"/run/control.json")).json();RK.paused=!!c.whole}catch(e){}}
 }catch(e){if(RK&&RK.id===id&&!RK.rec)RK.err="the record did not load"}
 if(RK&&RK.id===id)rkPaint()}
function openRun(id){const row=rkRow(id);if(!row)return;if(tsheet.classList.contains("on"))closeSheet();
 RK={id,row,rec:null,tab:"ov",f:{},paused:false,at:Date.now()};rsheet.classList.add("on");document.body.classList.add("tkopen");hl={run:id};paintHL();const b=$("#rkb");b.rkHtml=null;b.scrollTop=0;rkPaint(true);rkLoad()}
function closeRun(){rsheet.classList.remove("on");RK=null;if(!tsheet.classList.contains("on"))document.body.classList.remove("tkopen");if(hl&&hl.run!=null)hl=null;paintHL()}
// The poll keeps the row fresh; a live run's record is reread every few seconds while the sheet is open.
function rkFollow(){if(!RK)return;const r=rkRow(RK.id);if(r)RK.row=r;if(rkLive(RK.row)&&Date.now()-RK.at>4000){RK.at=Date.now();rkLoad()}else rkPaint()}
$("#rkx").onclick=closeRun;
// A control's action (rkAct, rkKey in run-sheet.js): a link opens its ticket, the rest sets the tab and the filters.
function rkApply(a){if(a.o){closeRun();openCard(a.o);return}RK.tab=a.st.tab;RK.f=a.st.f;rkPaint(true)}
$("#rkb").addEventListener("click",e=>{if(!RK)return;const a=rkAct(RK,e.target);if(a){if(a.o||e.target.closest("[data-rkt]"))e.preventDefault();rkApply(a)}});
$("#rkb").addEventListener("change",e=>{const s=e.target.closest("select[data-rkf]");if(s&&RK){RK.f={...RK.f,[s.dataset.rkf]:s.value};rkPaint(true)}});
$("#rkb").addEventListener("keydown",e=>{const a=rkKey(RK||{},e.target,e.key);if(a&&RK){e.preventDefault();rkApply(a)}});   // a node or a model row filters from the keyboard too
$("#rkpause").onclick=async()=>{if(!RK)return;if(!rkLive(RK.row)){window.open($("#rkgh").href,"_blank","noopener");return}
 const id=RK.id,action=RK.paused?"resume":"pause",p=$("#rkpause");p.disabled=true;
 try{const r=await fetch("/dash/"+encodeURIComponent(id)+"/run/control",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action})});
  if(RK&&RK.id===id){RK.note="";if(r.ok)RK.paused=action==="pause";else RK.note=r.status===409?"the run has ended: there is nothing to pause":"the run refused the request ("+r.status+")"}}
 catch(e){if(RK)RK.note="the run did not answer"}
 p.disabled=false;rkPaint(true);livePoll()};   // a pause or a resume changes the run's live row: read it now (#1697)
addEventListener("keydown",e=>{const w=rkEscape(e.key,rsheet.classList.contains("on"),tsheet.classList.contains("on"));if(w==="run")closeRun();else if(w==="ticket")closeSheet();if(e.key==="Escape")return;
 if(!tsheet.classList.contains("on")||/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName))return;
 if(e.key==="ArrowRight"||e.key==="j")step(1);if(e.key==="ArrowLeft"||e.key==="k")step(-1)});
document.addEventListener("click",e=>{const c=e.target.closest("#bbody .it");if(!c||e.target.closest("a")||e.target.closest("button")||c.classList.contains("m"))return;showIssue(c.dataset.n,c.dataset.r,c)});   // a card opens the sheet in place: the hub is never left
const hm=location.hash.match(/issue=(\d+)/);if(hm)openCard(+hm[1]);   // deep link: /#issue=1171
document.addEventListener("click",e=>{const h=e.target.closest("#bbody .ah");if(!h||e.target.closest("[data-eye]"))return;const g=h.parentElement.dataset.g;if(g===COLNAME.pr_open)bo.prOpen=bo.prOpen===false;else bo.open=g;saveB();pollBoard()});
document.addEventListener("click",e=>{const t=e.target.closest("[data-eye]");if(!t)return;e.preventDefault();e.stopPropagation();const g=t.dataset.eye;
 if(t.closest(".hidline")){const k=new Set(bo.kept||[]);k.add(g);bo.kept=[...k]}   // the hidden: strip's line: show the column again and keep it shown, even while it holds nothing (#1809)
 else{const h=new Set(hidCols()),k=new Set(bo.kept||[]);if(h.has(g))h.delete(g);else{h.add(g);k.delete(g)}bo.hidden=[...h];bo.kept=[...k]}   // the header's eye: the user's own hidden set, and the keep it ends
 saveB();pollBoard()},true);
const lim={},scr={};
function restore(){document.querySelectorAll("#bbody .sc").forEach(e=>{e.scrollTop=scr[e.dataset.g]||0;e.onscroll=()=>{scr[e.dataset.g]=e.scrollTop;if(e.scrollTop+e.clientHeight>=e.scrollHeight-40&&e.querySelector(".it.m")&&!lim.busy){const g=e.dataset.g;lim[g]=(lim[g]||25)+25;pollBoard()}}})}
// One colour per board column, resolved here and nowhere else: the header's dot, the card's --cc border and the
// hidden: strip's number all call colOf(name, columnNames). The nine named columns are the board's own — the eight
// shipped statuses plus the one open-PR lane — and the flow reads left to right: grey (not started), yellow (being
// coded), violet (built), pink (gating), green (finished), amber for the pile nobody has triaged, red for the one
// colour that means look here, and blue for the open-PR lane. Any other name is a Status option the Project gained
// later — a user column — and takes the next colour of the reserved ring, by its alphabetical position among those user
// columns. The name always stands beside the colour (column header, chip label), so the colour is a second cue and never
// the only one — which is what lets the ring cycle.
const COLORS=/*BOARD_COLORS*/;
const COLNAME=/*BOARD_NAMES*/;const BOARD_VOCAB={todo:COLNAME.issue_todo,doing:/*BOARD_DOING*/,review:/*BOARD_REVIEW*/};   // the columns' names by the fact that sets them: the board's script holds no column name of its own
const hidCols=()=>bo.hidden??[];
const RING=["var(--chart-series-6)","var(--chart-series-2)","var(--chart-series-4)","var(--chart-series-3)"];   // reserved for the columns the user creates, and never one of the named columns
const colOf=(n,names)=>{const u=(names||[]).filter(x=>!Object.hasOwn(COLORS,x)).sort(),i=u.indexOf(n);return COLORS[n]||RING[(i<0?0:i)%RING.length]};
const caseRows=cs=>(cs||[]).map(c=>{const ag=Math.max(0,Math.round((Date.now()-Date.parse(c.ended))/1000)),a=ag<3600?Math.max(1,Math.round(ag/60))+"m":ag<172800?Math.round(ag/3600)+"h":Math.round(ag/86400)+"d";return `<div class="tcrow ${c.result==="fail"?"fail":"pass"}" data-key="test case:${c.issue}@${esc(c.commit)}"><span class=tr>${esc(c.result)}</span><span class=id>#${c.issue}</span><span class=ta>${a}</span><code class=tm>${esc(c.commit)}</code></div>`}).join("");   // one row for each test case item, in the model's order: a strip beside the axis, never a column, and the pane reads no cache of its own
// flow-charts:
let FLOWK=null,FLOWD={};   // each chart's answer from its own route, by name: its numbers and the instant its data was read (#971)
let flowW=800,flowH=340;const flowP=36,flowHMin=340,flowHMax=720;   // both follow the window: the width from the pane, the height from its height, between a floor and a ceiling (#889, #1144)
const flowHeight=h=>{const v=(h*0.55)|0;return v<flowHMin?flowHMin:v>flowHMax?flowHMax:v};   // the plot height is layout, never a series (#1144)
const ltPaneW=()=>{const e=ltEl("#flowb"),w=e&&e.clientWidth?e.clientWidth:0,v=w?w-20:0;return v<120?120:(v|0)};   // the pane's width now, less its padding: the chart fills whatever width the window gives it, with no fixed box (SPEC 2498, #2504; #2171)
const ltWinH=()=>typeof window!=="undefined"&&Number(window.innerHeight)>0?Number(window.innerHeight):0;   // the window the page stands in, or zero when it states no height: a page with no height sizes no read (#2171)
const ltEl=sel=>typeof document!=="undefined"&&document.querySelector?document.querySelector(sel):null;   // one element of the page, or none where no page stands (#2171)
const ltBox=sel=>{const e=ltEl(sel);return e&&e.getBoundingClientRect?e.getBoundingClientRect():null};   // one element's laid-out box, or null before the page lays it out (#2171)
const ltPadV=e=>{if(!e||typeof getComputedStyle!=="function")return 0;const s=getComputedStyle(e);return (parseFloat(s&&s.paddingTop)||0)+(parseFloat(s&&s.paddingBottom)||0)};   // the pane's own vertical padding, measured (#2171)
const ltRoom=()=>{const w=ltWinH(),e=ltEl("#flowb"),r=e&&e.getBoundingClientRect?e.getBoundingClientRect():null;if(!(w>0)||!r||!(r.height>0))return 0;const room=(w-r.top-ltPadV(e))|0;return room>0?room:0};   // the space the pane really has: from its laid-out top to the window's floor, less its own padding (#2171)
const ltStatusH=()=>{const r=ltBox("#flowb .flstat");return r&&r.height>0?(r.height+.5|0):0};   // the status line under the card, measured: the card leaves it its row (#2171)
const ltCardPx=()=>{const room=ltRoom();const h=room>0?room-ltStatusH():ltWinH();return h>0?h:0};   // the lead card's height: the room the pane has, never the window less a fixed chrome estimate (#2171)
const ltPlotH=()=>{const h=(LT.fitH||ltCardPx()||0)-2*ltP;return h<60?60:h};   // the plot's drawn height: the svg box the page measured, or the card's room before that, less the viewBox's own top and bottom margin (#2171)
const flowX=(i,n)=>n<2?flowP:flowP+i*(flowW-2*flowP)/(n-1);
const flowY=(v,max)=>max?flowH-flowP-v*(flowH-2*flowP)/max:flowH-flowP;
const flowAxis=(max,unit)=>`<line x1=${flowP} y1=${flowH-flowP} x2=${flowW-flowP} y2=${flowH-flowP} stroke=var(--chart-grid)></line><text x=${flowP-6} y=${flowY(max,max)+4} text-anchor=end fill=var(--chart-axis)>${max} ${unit}</text>`;
const cfdAxis=c=>{const n=c.days.length,base=flowH-flowP;   // every number and date arrives from the hub; the page places a mark and a label for each (#896), one axis style in mute at the box size with the title inside the plot (#1141)
  const ys=c.yticks.map(v=>{const y=flowY(v,c.ymax);return `<line x1=${flowP-4} y1=${y} x2=${flowP} y2=${y} stroke=var(--chart-axis)></line><text x=${flowP-7} y=${y+4} text-anchor=end fill=var(--chart-axis)>${v}</text>`}).join("");
  const xs=c.xticks.map(t=>{const x=flowX(t.i,n);return `<line x1=${x} y1=${base} x2=${x} y2=${base+4} stroke=var(--chart-axis)></line><text x=${x} y=${base+17} text-anchor=middle fill=var(--chart-axis)>${esc(t.day)}</text>`}).join("");
  return `<line x1=${flowP} y1=${flowP-10} x2=${flowP} y2=${base} stroke=var(--chart-grid)></line><line x1=${flowP} y1=${base} x2=${flowW-flowP} y2=${base} stroke=var(--chart-grid)></line>${ys}${xs}`+
    `<text x=${flowP} y=${flowP+12} text-anchor=start fill=var(--chart-axis)>cards</text>`+
    `<text x=${flowW-flowP} y=${base+31} text-anchor=end fill=var(--chart-axis)>day (${esc(c.zone)})</text>`;};
const coverNote=v=>!v?"":`<p class=fnote>cards read ${v.read} of ${v.total}, ${v.complete} complete, last refresh ${v.last?esc(v.last):"never"}</p>`;
const CFD_R=[["7","7 days"],["30","30 days"],["90","90 days"],["all","All"]];   // the cumulative flow's range views (#901)
const CFD_TYPES=[["all","All"],["spec","Specs"],["task","Tasks"],["bug","Bugs"]];   // the cumulative flow's Type: All is every card (#1356)
const flowFilter=()=>{try{const s=JSON.parse(localStorage.getItem("squad-flow")||"{}")||{};return {ty:CFD_TYPES.some(([k])=>k===s.ty)?s.ty:"task",hidden:Array.isArray(s.hidden)?s.hidden.filter(n=>typeof n==="string"):[]}}catch(e){return {ty:"task",hidden:[]}}};   // a filter the page cannot read is its default, never an error; the Type opens on the tasks default the route always sent (#1356)
const flowSave=()=>{try{localStorage.setItem("squad-flow",JSON.stringify({ty:CF.ty,hidden:CF.hidden}))}catch(e){}};   // the two filters live in the page's own key, apart from the Board record (#1356)
let CF={r:"all",...flowFilter()};   // the cumulative flow's range (the whole history at first: the chart opens on zero), its Type and the states the reader hid, restored from the page key (#1356)

const cfdShown=c=>{const keep=c.bands.filter(b=>!CF.hidden.includes(b.name)),stacked=c.bands.every(b=>b.lower&&b.upper);
  if(keep.length===c.bands.length&&stacked)return c.bands;   // every band shows, with the hub's own stack: the page reads no count (#1149)
  const base={};let at=null;   // the cards in the bands below; the last visible band sits on the floor (#1356)
  for(let i=keep.length-1;i>=0;i--){const b=keep[i],zero=b.count.map(()=>0);base[b.name]=at===null?zero:at;
    at=(at===null?zero:at).map((v,k)=>v+(b.count[k]||0))}
  return keep.map(b=>({...b,lower:base[b.name],upper:base[b.name].map((v,k)=>v+(b.count[k]||0)),
    held_upper:base[b.name].map((v,k)=>v+(b.held[k]||0))}))};   // a hidden state drops its band and the rest re-stack from their counts with no gap; a payload with no hub stack stacks the same way (#1356)
const cfdStates=c=>{const names=c.bands.map(b=>b.name),n=CF.hidden.length;   // one checkbox per board-defined column, never a state the payload does not carry (#1356)
  return `<details class=fsel id=cfds><summary>States: ${n?`${names.length-n} of ${names.length}`:"all"}</summary><div class=fpop>${names.map(v=>`<label><input type=checkbox data-state="${esc(v)}"${CF.hidden.includes(v)?"":" checked"}> ${esc(v)}</label>`).join("")}</div></details>`};
const flowCFD=c=>{const names=c.bands.map(b=>b.name),kept=CF.hidden.filter(h=>names.includes(h));
  if(kept.length!==CF.hidden.length){CF.hidden=kept;flowSave()}   // a stored state the board no longer names leaves the page key, so the saved set holds board-defined states only (#1356)
  const sel=`<label class=fhead>Range <select id=cfdr>${CFD_R.map(([k,v])=>`<option value=${k}${CF.r===k?" selected":""}>${v}</option>`).join("")}</select></label>`+
    `<label class=fhead>Flow type <select id=cfdy>${CFD_TYPES.map(([k,v])=>`<option value=${k}${CF.ty===k?" selected":""}>${v}</option>`).join("")}</select></label>`+cfdStates(c);
  if(!c.days.length)return sel+"<p class=fnote>the flow history has no sample yet</p>";
  const n=c.days.length,cw=(flowW-2*flowP)/(n<2?1:n),shown=cfdShown(c);
  const runs=b=>{const out=[];let cur=null;c.days.forEach((_,i)=>{if(b.upper[i]===null){cur=null;return}if(!cur){cur=[];out.push(cur)}cur.push(i)});return out};
  const band=(b,run)=>{const one=run.length===1,h=3;   // a one-point run is a mark 2h wide, never a zero-width line (#894)
    const up=run.flatMap(i=>one?[[flowX(i,n)-h,b.upper[i]],[flowX(i,n)+h,b.upper[i]]]:[[flowX(i,n),b.upper[i]]]);
    const low=[...run].reverse().flatMap(i=>one?[[flowX(i,n)+h,b.lower[i]],[flowX(i,n)-h,b.lower[i]]]:[[flowX(i,n),b.lower[i]]]);
    return [...up,...low].map(([x,v])=>`${x},${flowY(v,c.ymax)}`).join(" ");};
  const polys=b=>runs(b).map(run=>`<polygon points="${band(b,run)}" fill="${COLORS[b.name]}" fill-opacity=".85"><title>${esc(b.name)}</title></polygon>`).join("");
  const heldBand=b=>({name:b.name,lower:b.lower,upper:b.held_upper});
  const heldPolys=b=>b.held&&b.held.some(v=>v>0)?runs(heldBand(b)).map(run=>`<polygon points="${band(heldBand(b),run)}" fill="var(--chart-held)" fill-opacity=".45"><title>${esc(b.name)} held</title></polygon>`).join(""):"";
  const fig=v=>v===null||v===undefined?"none":esc(String(v));
  const vis=new Set(shown.map(b=>b.name));
  const hover=(c.reads||[]).map((r,i)=>{const parts=r.counts.filter(k=>vis.has(k.name)).map(k=>`${esc(k.name)} ${fig(k.count)}`).join(", ");   // a day's counts, WIP and throughput, on hover: the hub's record for the day, never a series index (#901, #1149); a hidden state is not named (#1356)
    return `<rect x=${flowX(i,n)-cw/2} y=${flowP-10} width=${cw} height=${flowH-2*flowP+10} fill=transparent><title>${esc(r.day)}: ${parts} · WIP ${fig(r.wip)} (whole board) · throughput ${fig(r.throughput)} (whole board)</title></rect>`;}).join("");   // WIP and throughput are the hub's whole-board figures, whatever states are hidden (#1356)
  const legend=`<p>${shown.map(b=>`<span class=flg><i style="background:${COLORS[b.name]}"></i>${esc(b.name)}</span>`).join("")}</p>`;
  const L=c.last,v=c.coverage;   // the hub chose the last day and counted its WIP and throughput; the page prints them (#1149)
  const notes=(L?`${esc(L.day)}: WIP <b>${fig(L.wip)}</b> (whole board), throughput <b>${fig(L.throughput)}</b> (whole board) · `:"")+`history from ${esc(c.start)} ${esc(c.zone)}`+
    (v?` · cards read ${fig(v.read)} of ${fig(v.total)}, ${fig(v.complete)} complete, last refresh ${v.last?esc(v.last):"never"}`:"")+
    (c.legacy?` · ${fig(c.legacy)} ${c.legacy===1?"day comes":"days come"} from before the kind, spec, wave and type history: they answer none of these`:"")+
    ` · a darker stripe at a band's base is its held cards${CF.hidden.length?" · held cards in hidden states are not drawn":""}`;
  return sel+`<svg viewBox="0 0 ${flowW} ${flowH}">${shown.map(polys).join("")}${shown.map(heldPolys).join("")}${cfdAxis(c)}${hover}</svg>`+legend+`<p class=fnote>${notes}</p>`;};   // the legend, then one line of notes (#1149)
const ltEx=(exc,cards,total,words,reasons)=>{const R=reasons||{},named=exc.map(e=>`${e.count} ${R[e.reason]||e.reason}`+((cards||{})[e.reason]?.length?": "+cards[e.reason].map(n=>"#"+n).join(", "):"")).join("; "),said=words||exc.map(e=>`${e.count} ${R[e.reason]||e.reason}`).join("; ");return `<em class=ltex${named?` title="${esc(named)}"`:""}>excluded ${total}${said?`: ${esc(said)}`:""}</em>`};   // the one exclusion total with its reason beside its count (#1145, #1248, #1354); its wording is the payload's, one source in Python; its hover names the cards
const ltExOf=c=>ltEx(Object.entries(c.excluded||{}).map(([r,n])=>({reason:r,count:n})),c.excluded_cards,c.excluded_total,c.excluded_text,c.excluded_reasons);
const ltGateRole=()=>COLORS[COLNAME.gating]||"var(--chart-series-other)";   // gate time keeps the Testing column's palette role (#1256)
const LT_TYPES=[["spec","Specs"],["task","Tasks"],["bug","Bugs"]],LT_START="/*LT_START*/";   // the start the hub's payload measures from (#1247)
let LT={tf:"12h",ty:"bug",pick:null,zc:null,last:null,tried:false,err:null,fitted:false,fitH:0,cardH:0,reqH:null,defer:false,rr:0,zr:null,rng:null,tok:0,noclick:false,a:LT_START,b:"",cols:[],band:true,md:true,p85:true,win:"full"};   // a refused first read holds its own error; the fit flag keeps one redraw per paint, the measured box its height and reqH the height the last read asked for (#2171); a fresh page opens on the past 12 hours (#2029), Bugs and Todo; the chart's Start and End restore from their own key when the lead chart opens (#1275), while the timeframe and the Type stay the visit's choice (#1247); the y axis is always log (#1355); the cluster listed under the chart, and a drag zoom: the hub's chart over zr=[from,to] (#900, #985); the deviation band shows at first and the display may hide it, the choice held in this state across a redraw (#1332, #1353); the average, the median and p85 all show at first, and each is a toggle (#1332)
const ltSpan=(r,a,b)=>{const t0=Date.parse(r.start),t1=Date.parse(r.end),at=f=>new Date(t0+f*(t1-t0)).toISOString();return [at(a<0?0:a),at(b>1?1:b)]};   // plot fractions to the dates they name (#900)
const LT_TF=[["12h","Past 12 hours"],["1","Past 24 hours"],["2","Past 48 hours"],["3","Past 72 hours"],["7","Last week"],["14","Past 2 weeks"],["30","Past month"],["all","All"]];
const CHART_KEY="squad-chart",chartRange=()=>{try{const s=JSON.parse(localStorage.getItem(CHART_KEY)||"{}")||{},r={};if(typeof s.b==="string")r.b=s.b;return r}catch(e){return {}}},chartSave=()=>{try{localStorage.setItem(CHART_KEY,JSON.stringify({b:LT.b}))}catch(e){}};let CHART_R=chartRange();   // the chart key owns the End only: the Start opens on Todo every visit, and a reader's own start holds for that visit (owner ask)
const ltP=64;   // a duration label ("17h 36m") is wider than a count, and the old margin cut every tick to its tail (#Astra review)
const ltGap=16;   // the least vertical space between two gutter tag baselines, one body line (#1332)
const ltTextW=s=>9*String(s).length;   // a tag's estimated painted width at the body size: a loose nine units per character, so the gutter holds the longest tag (#1332)
let LT_FRAME;const ltX=x=>ltP+x*LT_FRAME.width,ltY=y=>ltP+LT_FRAME.height-y*LT_FRAME.height;
const ltTag=(v,col,dash)=>v&&v.y!=null?{ly:ltY(v.y),name:v.label,text:v.text,col,dash}:null;   // one reference tag: its name, its value and its line's paint (#1332)
const ltGutter=(items,top,h,gap)=>{const out=items.slice().sort((a,b)=>a.ly-b.ly),n=out.length;   // lay the tags out in baseline units, inside the plot's own top and height (#1332)
  if(!n)return out;
  if(n>1&&(n-1)*gap>h){const step=h/(n-1);out.forEach((t,i)=>t.ty=top+i*step);return out}   // a frame too short for the block spreads the tags evenly across it (#1332)
  let at=top;
  for(const t of out){t.ty=t.ly>at?t.ly:at;at=t.ty+gap}
  const over=out[n-1].ty-(top+h);   // a block that runs past the floor slides up as a whole
  if(over>0){for(const t of out)t.ty=t.ty-over;let below=null;
    for(let i=n-1;i>=0;i--){const hi=below===null?out[i].ty:below-gap;out[i].ty=out[i].ty>hi?hi:out[i].ty;below=out[i].ty}
    const down=top-out[0].ty;if(down>0)for(const t of out)t.ty=t.ty+down}   // a block that runs past the ceiling slides down again
  return out};
const ltLine=(v,col,dash)=>v&&v.y!=null?`<line x1=${ltP} x2=${ltP+LT_FRAME.width} y1=${ltY(v.y)} y2=${ltY(v.y)} stroke="${col}" stroke-width=1${dash?` stroke-dasharray="${dash}"`:""}><title>${esc(v.label)} ${esc(v.text)}</title></line>`:"";   // one 1px reference line, patterned so it reads in grayscale (#1332)
const ltKM=c=>c.survival?`<p class="fnote ltkm">${esc(c.survival.text)} · ${esc(c.wip.text)}${c.gate?` · ${esc(c.gate.label)} ${esc(c.gate.text)}`:""}</p>`:"";   // the hub's survival reading, work in progress and gate time as one text metric, in its words (#1250, #1304)
const ltV=v=>v?esc(v.text):"?";   // a summary value's text, or a question mark where it is not estimable
const LT_MONTHS=["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
const ltDay=s=>{const d=new Date(s);return `${LT_MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}`};
const ltStamp=s=>{const d=new Date(s);return `${ltDay(s)} ${String(d.getUTCHours()).padStart(2,"0")}:${String(d.getUTCMinutes()).padStart(2,"0")}`};
const ltRangeText=r=>{if(!r)return "";if(r.text)return r.text;const a=Date.parse(r.start),b=Date.parse(r.end);if(!(a<b))return "";return b-a<=3*864e5?`${ltStamp(r.start)} to ${ltStamp(r.end)} UTC`:`${ltDay(r.start)} to ${ltDay(r.end)} UTC`};   // a payload's own text, or the range its ends name (#1334)
const ltChart=(c,cov,sum)=>{LT_FRAME={width:c.plot_frame.width,height:ltPlotH()};const n=c.dots.length,base=ltP+LT_FRAME.height,ink="var(--chart-axis)",mute="var(--chart-axis)";   // one axis style: tick text and the title in mute, never a mark's ink (#1141)
  const grp=(name,body)=>`<span class=ltgrp><b class=ltgl>${name}</b> ${body}</span>`,backTo=LT_TF.find(([k])=>k===LT.tf)[1];
  const fitMode=LT.win==="fit"&&!!c.fit,printed=fitMode?ltRangeText(c.fit):ltRangeText(c.range);   // the range control switches what this one payload holds (#1334)
  const ctl=`<div class="fhead ltrow">`+
      `<label>Timeframe <select id=ltr>${LT_TF.map(([k,v])=>`<option value=${k}${LT.tf===k?" selected":""}>${v}</option>`).join("")}</select></label> `+
      `<label>Lead type <select id=lty>${LT_TYPES.map(([k,v])=>`<option value=${k}${LT.ty===k?" selected":""}>${v}</option>`).join("")}</select></label> `+
      `<span class=ltgrp><b class=ltgl>Lines</b> <label><input type=checkbox id=ltmd${LT.md?" checked":""}> Median</label> <label><input type=checkbox id=ltp85${LT.p85?" checked":""}> p85</label></span>`+   // the average line is the default; the median and p85 are one toggle each (#1332); the chart draws no Scale control, its axis is always log (#1355)
    `<details class=ltmore><summary>More</summary>`+
    grp("Measurement",`<label>Start <select id=lta><option value="">Created</option>${LT.cols.map(k=>`<option${LT.a===k?" selected":""}>${esc(k)}</option>`).join("")}</select></label> `+
      `<label>End <select id=ltb>${LT.cols.map(k=>{const v=k===COLNAME.issue_closed?"":k;return `<option value="${esc(v)}"${LT.b===v?" selected":""}>${esc(k)}</option>`}).join("")}</select></label>`)+   // the span each dot measures, from the recorded transitions (#905)
    " "+grp("Display",`<label>Band <input type=checkbox id=ltbd${LT.band?" checked":""}></label>`)+" "+
    ((LT.zr||c.fit)?grp("View",(LT.zr?`<span class=fchip id=ltz>Back to ${esc(backTo)} <button id=ltzc type=button title="clear the zoom">Clear</button></span> `:"")+
      (c.fit?`<span class=fchip id=ltrng><button id=ltfit type=button>Fit data</button><button id=ltfull type=button>Full window</button> ${esc(printed)}</span>`:""))+" ":"")+   // one chip while zoomed; the range control switches x from the held payload and prints the range (#1147, #1334)
    `<p class=fnote>Set the controls in order: filters, then measurement, then display, then view.</p>`+
    `<p class=fnote>${esc(c.text)}</p>${coverNote(cov)}`+   // the coverage, plotted-cards and ordering notes stay behind More (#1145)
    `${sum||""}${ltKM(c)}</details></div>`;   // the extremes, the survival reading and the exclusion detail sit behind More, and the filters and More share one row (#2171)
  LT.rng=c.range;
  if(!n)return ctl+`<p class=fnote>no closed card in this timeframe · ${ltExOf(c)}</p>`;
  const eX=d=>fitMode&&d.xf!=null?d.xf:d.x;   // the x source of every mark in this view (#1334)
  const bp=!LT.band||!c.band?null:(fitMode&&c.band.fit&&c.dots.every(d=>d.xf!=null)?c.band.fit.polygon:[...c.dots.map(d=>({x:eX(d),y:d.rolling.y_high})),...c.dots.slice().reverse().map(d=>({x:eX(d),y:d.rolling.y_low}))]);   // the band takes the same x as the marks, per dot, never the fit polygon over a mixed payload (#1334)
  const band=bp?`<polygon points="${bp.map(p=>`${ltX(p.x)},${ltY(p.y)}`).join(" ")}" fill="var(--chart-series-3)" fill-opacity=".28"><title>Rolling spread ±1 SD</title></polygon>`:"";   // the band shows at first; when the display hides it, only the reader's choice removes it, so a redraw keeps it; it is the hub's polygon, named apart from the median's CI (#1304, #1334, #1332, #1353)
  const roll=`<polyline fill=none stroke="var(--chart-bar)" stroke-width=3 points="${c.dots.map(d=>`${ltX(eX(d))},${ltY(d.rolling.y)}`).join(" ")}"><title>Rolling mean</title></polyline>`;   // the trend is the strongest line, about 3px (#1146, #1332)
  const ys=c.ticks.map(t=>`<line x1=${ltP-4} y1=${ltY(t.y)} x2=${ltP} y2=${ltY(t.y)} stroke=${mute}></line><text x=${ltP-7} y=${ltY(t.y)+4} text-anchor=end fill=${ink}>${esc(t.text)}</text>`).join("");
  const xs=c.xticks.map(t=>`<text x=${ltX(t.x)} y=${base+17} text-anchor=middle fill=${ink}>${esc(t.text)}</text>`).join("");
  const axes=`<line x1=${ltP} y1=${ltP-10} x2=${ltP} y2=${base} stroke=var(--chart-grid)></line><line x1=${ltP} y1=${base} x2=${ltP+LT_FRAME.width} y2=${base} stroke=var(--chart-grid)></line>`+
    `<text x=${ltP} y=${ltP-26} text-anchor=start fill=${ink}>Lead time (log)</text>`+   // the y axis is titled above it, outside the plot, always log (#1332, #1355)
    `<text x=${ltP} y=${base+31} text-anchor=start fill=${ink}>Closed date</text>`;   // the x axis is titled below it (#1332)
  const lines=ltLine(c.average,"var(--chart-series-8)","")+(LT.md?ltLine(c.median,"var(--chart-ok)","6 3"):"")+(LT.p85?ltLine(c.p85,"var(--chart-series-5)","1 3"):"");   // the average shows at first; the median and p85 are toggles, each with its own 1px pattern, a long dash and a dot (#1332)
  const tags=ltGutter([ltTag(c.average,"var(--chart-series-8)",""),LT.md?ltTag(c.median,"var(--chart-ok)","6 3"):null,LT.p85?ltTag(c.p85,"var(--chart-series-5)","1 3"):null].filter(Boolean),ltP,LT_FRAME.height,ltGap);
  const gut=22+(tags.map(t=>ltTextW(`${t.name} ${t.text}`)).sort((a,b)=>b-a)[0]||0)+8;   // the reserved right gutter fits the longest tag it carries, so no value runs past the viewBox (#1332)
  const gutter=tags.map(t=>`<line x1=${ltP+LT_FRAME.width} x2=${ltP+LT_FRAME.width+6} y1=${t.ly} y2=${t.ty} stroke="${t.col}" stroke-width=1></line>`+   // a short leader joins a moved tag to its line (#1332)
    `<line x1=${ltP+LT_FRAME.width+8} x2=${ltP+LT_FRAME.width+18} y1=${t.ty} y2=${t.ty} stroke="${t.col}" stroke-width=1${t.dash?` stroke-dasharray="${t.dash}"`:""}></line>`+   // the swatch keeps the line's paint and pattern (#1332)
    `<text class=lttg x=${ltP+LT_FRAME.width+22} y=${t.ty} text-anchor=start fill=var(--chart-axis)>${esc(t.name)} ${esc(t.text)}</text>`).join("");   // the name and the value read together in grayscale, whichever line they belong to; each sits in the reserved right gutter, laid out apart (#1332)
  const clus=fitMode&&c.clusters_fit?c.clusters_fit:c.clusters,dots=clus.map((k,i)=>{const m=k.count,shown=k.cards.slice(0,5).map(n=>"#"+n).join(", ")+(m>5?` +${m-5} more`:""),title=m>1?`${m} issues: ${shown}`:`#${k.dots[0].n} ${k.dots[0].title}`;   // a lone card is one hollow dot; a cluster is one outlined filled dot of the payload's one fixed radius (#1331)
    const cx=ltX(k.x),cy=ltY(k.y),strip=k.dots.map(d=>d.stack.map(b=>`${b.column} ${b.text}`).join(", ")).join(" · ");   // the tooltip's stage strip: each dot's column stack, no stacked bar in the plot (#1331)
    return `<circle data-k=${i} cx=${cx} cy=${cy} r=${k.r} fill="${m>1?"var(--chart-ink)":"var(--chart-bg)"}" stroke="var(--chart-ink)" stroke-width=${c.mark?c.mark.outline:1.5}${k.dots.some(d=>d.approx||d.unknown_first)?' stroke-dasharray="3 2"':""} style="cursor:pointer"><title>${esc(title)} · lead ${esc(k.lead.text)} · closed ${esc(k.closed.text)}${strip?` · ${esc(strip)}`:""}${k.dots.some(d=>d.approx)?" · start approximate":""}${k.dots.some(d=>d.unknown_first)?" · first interval unknown":""}${k.dots.some(d=>d.fallback)?" · measured from its first time on the board":""}</title></circle>`}).join("");   // the tooltip names the card or the cluster, the lead time, the closed dates, the stage strip and a card's fallback measure (#1331, #1354); the plot draws no count and no bar
  const legend=`<p class="flg ltlg"><span><i class=lthollow></i>Card</span><span><i class=ltfilled></i>Cluster</span><span><i class=ltroll></i>Rolling mean</span>${LT.band&&bp?`<span><i class=ltband></i>Rolling spread ±1 SD</span>`:""}</p>`;   // the series legend names every mark the plot draws, in the plot's own corner; each reference value sits in the right gutter beside its line (#1304, #1332, #2171)
  const pk=LT.pick!=null&&c.clusters[LT.pick]?c.clusters[LT.pick]:null;
  const rows=pk?`<table class=mtab><thead><tr><th>Issue</th><th>Title</th><th>Elapsed</th><th>Closed</th></tr></thead><tbody>${pk.dots.map(d=>`<tr data-o=${d.n} style="cursor:pointer"><td>#${d.n}</td><td>${esc(d.title)}</td><td>${esc(d.text)}</td><td>${esc(d.closed)}</td></tr>`).join("")}</tbody></table>`:"";   // the page's one table rule (#1146)
  return ctl+`<div class=ltplot>`+legend+`<svg data-lt viewBox="0 0 ${ltP+LT_FRAME.width+gut} ${LT_FRAME.height+2*ltP}">${axes}${ys}${xs}${band}${roll}${lines}${dots}${gutter}</svg></div>`+rows;};   // the series legend sits in the plot's own corner and the right gutter holds the reference tags (#1332, #2171)
const flowLead=l=>{const c=LT.zc||(l.control?l.control[LT.tf]:null);
  const head=`<p class=fhead>${esc((LT_TYPES.find(([k])=>k===l.type)||[0,"all cards"])[1].toLowerCase())} · <b>${esc(c?c.measure:l.measure)}</b> · ${l.window.size} ${esc(l.window.unit)} plotted</p>`;   // one measurement line stands above the plot, in the wording the board filter test reads (#2171)
  const sum=c&&c.summary?`<p class=fnote>min <b>${ltV(c.summary.minimum)}</b> · max <b>${ltV(c.summary.maximum)}</b> · ${esc(c.summary.plotted_text)} · ${ltExOf(c)}${c.fallback&&c.fallback.count?` · ${esc(c.fallback.text)}`:""}</p>`:"";   // the extremes and the exclusion detail sit behind More (#1334, #2171); a card's fallback measure is named (#1354)
  LT.cols=l.columns||[];const ch=ltCardPx(),card=b=>`<div class=ltcard${ch>0?` style="height:${ch}px"`:""}>`+b+`</div>`;   // the card states the room the pane measured, and the plot box takes what its text and the legend's band leave (#2171)
  if(ch>0&&ch!==LT.cardH){LT.cardH=ch;LT.fitted=false}   // a new card height is a new plot box, so the page measures it once more (#2171)
  if(LT.err)return card(head+`<p class=fnote>the lead chart could not be read; try again</p>`);   // a refused read with no good chart behind it paints its own sentence, never the cached payload's chart (#2171)
  if(!c)return card(head+`<p class=fnote>no closed card yet · ${ltEx(l.excluded,l.excluded_cards,l.excluded_total,l.excluded_text,l.excluded_reasons)}</p>`);
  return card(head+ltChart(c,l.coverage,sum));};   // the chart actually drawn decides emptiness: an empty timeframe never hides a zoom that holds cards (#1334)
const ltBoxH=()=>{const r=ltBox("#flowb .ltcard .ltplot>svg");return r&&r.height>0?(r.height+.5)|0:0};   // the svg box the page laid out, in whole pixels, never the legend's band, and zero when no page lays it out (#2171)
const ltFitCard=()=>{LT.fitted=true;const h=ltBoxH();if(h>0&&h!==LT.fitH){LT.fitH=h;drawFlow();if(ltPlotH()!==LT.reqH)ltResize()}};   // the drawn card's own plot box sets the viewBox, one redraw per paint, and the hub is asked again with the height the redraw draws, so its clusters and the marks agree (#2171)
const drawFlow=()=>{{flowW=ltPaneW();flowH=flowHeight(ltWinH())}const cfd=FLOWK==="cfd";
  const a=FLOWD[FLOWK],wait=FLOWK==="lead"&&!!a&&!LT.zc&&!LT.tried;   // the lead waits for the first window-sized read, and a refused read never leaves the wait sentence behind (#2171)
  $("#flowb").innerHTML=!a?"<p class=fnote>Loading the chart from the hub.</p>":(cfd?flowCFD(a.cfd.ranges[CF.r]):wait?"<p class=fnote>Loading the lead chart at the window's plot size.</p>":flowLead(a.lead))+`<p class=flstat role=status>data read ${new Date(a.t*1000).toLocaleTimeString()}</p>`;
  if(FLOWK==="lead"&&!wait&&!LT.fitted)ltFitCard()};   // the painted card measures its own plot box, so the svg's viewBox is the box's rendered height (#2171)
const flowOpen=async k=>{FLOWK=k;$("#flowov").hidden=false;flowTabs();drawFlow();const want=()=>barQuery(k),q=want();try{const a=await (await fetch(`/api/chart/${k}?${q}`)).json();if(q!==want())return;FLOWD[k]=a;if(FLOWK===k)drawFlow()}catch(e){}if(k==="lead"){if(CHART_R){Object.assign(LT,CHART_R);CHART_R=null}if(FLOWK===k){LT.tried=!!LT.zc;LT.fitted=false;LT.fitH=0;ltGet()}}};   // the control chart reads its span and the window's plot frame at open, so the first draw is never the payload's fixed frame (#1275, #2171); a reopen keeps the wait sentence off while the page still holds a chart (#2171)
const ltQ=()=>`&a=${encodeURIComponent(LT.a)}&b=${encodeURIComponent(LT.b)}&${barQuery("lead")}`,ltOwn=()=>LT.a!==LT_START||LT.b!=="";   // a span other than the payload's, Todo to closed, is the hub's chart for it (#905, #1247); the bar's values filter it (#904); the axis is always log, so the route takes no scale (#1355)
const ltSend=async(route,tok)=>{const pw=ltPaneW();flowW=pw;return {pw,c:await (await fetch(`${route}&pw=${pw}&ph=${LT.reqH}`)).json()}};   // one sized read: the pane's width at read time, never a default the layout has not met, and the height the page draws (#2171)
const ltZoom=async(f,t)=>{if(!(ltWinH()>0)){LT.defer=true;return}LT.defer=false;const tok=++LT.tok;LT.err=null;LT.reqH=ltPlotH();const route=`/api/leadtime?from=${encodeURIComponent(f)}&to=${encodeURIComponent(t)}${ltQ()}`;try{const s=await ltSend(route,tok);if(tok!==LT.tok)return;LT.rr=0;if(s.c.error)return;if(ltPaneW()!==s.pw&&LT.rr<2){LT.rr++;LT.reqH=ltPlotH();drawFlow();return ltZoom(f,t)}LT.zc=s.c;LT.zr=[f,t];LT.pick=null;drawFlow()}catch(e){}};   // the hub recomputes the chart for the dragged range, clustered at the plot size the page draws and the box it measured; the pane's width at read time, asked again when it moved under the read (#900, #2171)
const ltGet=async()=>{if(!(ltWinH()>0)){LT.defer=true;return}LT.defer=false;const tok=++LT.tok;LT.zc=null;LT.pick=null;LT.err=null;LT.reqH=ltPlotH();const route=`/api/leadtime?tf=${LT.tf}${ltQ()}`;try{const s=await ltSend(route,tok);if(tok!==LT.tok)return;if(ltPaneW()!==s.pw&&LT.rr<2){LT.rr++;LT.reqH=ltPlotH();drawFlow();return ltGet()}LT.rr=0;if(s.c.error)throw new Error(s.c.error);LT.zc=s.c;LT.last=s.c;drawFlow()}catch(e){if(tok!==LT.tok)return;LT.rr=0;LT.zc=LT.last||null;LT.err=!LT.last;LT.tried=true;drawFlow()}};   // the timeframe's chart for the chosen span, sized by the window, and never read while the window states no height; a refused read keeps the last good chart, and a first one names its own error (#905, #2171)
const ltResize=async()=>{if(!(ltWinH()>0)){LT.defer=true;return}if(!LT_FRAME&&!LT.defer)return;LT.defer=false;const tok=++LT.tok;LT.err=null;LT.reqH=ltPlotH();const route=`/api/leadtime?${LT.zr?`from=${encodeURIComponent(LT.zr[0])}&to=${encodeURIComponent(LT.zr[1])}`:`tf=${LT.tf}`}${ltQ()}`;try{const s=await ltSend(route,tok);if(tok!==LT.tok)return;LT.rr=0;if(s.c.error)return;if(ltPaneW()!==s.pw&&LT.rr<2){LT.rr++;LT.reqH=ltPlotH();drawFlow();return ltResize()}LT.zc=s.c;drawFlow()}catch(e){}};   // a resize re-clusters the marks at the window's new plot size, by the hub's one rule; a deferred read lands on the first resize, and a pane that moved under the read is asked again (#1331, #2171)
const ltPx=(e,s)=>{const r=s.getBoundingClientRect(),v=s.viewBox.baseVal;return v.width>0&&r.width>0?(e.clientX-r.left)*v.width/r.width:null};   // pointer to SVG user units, so the reserved gutter never bends the drag; a detached or unlaid-out svg names no x; ltClamp holds the plot (#1332)
const ltClamp=x=>x<ltP?ltP:x>ltP+LT_FRAME.width?ltP+LT_FRAME.width:x,ltLo=(p,q)=>p<q?p:q,ltHi=(p,q)=>p<q?q:p;
let LTD=null;   // the drag across a control chart's plot in progress (#900)
$("#flowb").onpointerdown=e=>{const s=e.target.closest("svg[data-lt]");LT.noclick=false;if(!s||e.button!==0||!LT.rng)return;const p=ltPx(e,s);if(p==null)return;const x=ltClamp(p);LTD={s,x0:x,x1:x,moved:false}};
document.addEventListener("pointermove",e=>{if(!LTD)return;const p=ltPx(e,LTD.s);if(p==null)return;const x1=ltClamp(p);LTD.x1=x1;const dx=x1-LTD.x0;if(!LTD.moved&&dx*dx<36)return;LTD.moved=true;
  let sel=LTD.s.querySelector("rect.ltsel");if(!sel){sel=document.createElementNS("http://www.w3.org/2000/svg","rect");sel.setAttribute("class","ltsel");sel.setAttribute("y",ltP-10);sel.setAttribute("height",LT_FRAME.height+10);sel.setAttribute("fill","var(--chart-series-3)");sel.setAttribute("fill-opacity",".18");sel.setAttribute("pointer-events","none");LTD.s.appendChild(sel)}
  sel.setAttribute("x",ltLo(LTD.x0,x1));sel.setAttribute("width",dx<0?-dx:dx)});
document.addEventListener("pointerup",()=>{const d=LTD;LTD=null;if(!d||!d.moved)return;d.s.querySelector("rect.ltsel")?.remove();LT.noclick=true;
  const a=(ltLo(d.x0,d.x1)-ltP)/LT_FRAME.width,b=(ltHi(d.x0,d.x1)-ltP)/LT_FRAME.width;if(b-a<0.005||!LT.rng)return;const [f,t]=ltSpan(LT.rng,a,b);ltZoom(f,t)});
$("#flowb").onchange=e=>{if(e.target.id==="cfdr"){CF.r=e.target.value;drawFlow();return}
  if(e.target.id==="cfdy"){CF.ty=e.target.value;flowSave();flowOpen("cfd");return}   // a Type is a new instruction to the chart route, and it stays in the page key (#1356)
  if(e.target.dataset&&e.target.dataset.state){const v=e.target.dataset.state;CF.hidden=e.target.checked?CF.hidden.filter(h=>h!==v):CF.hidden.concat(v);flowSave();drawFlow();const d=$("#flowb").querySelector&&$("#flowb").querySelector("#cfds");if(d)d.open=true;return}   // a state toggle draws the payload the page holds, never a read, and the list stays open for the next one (#1356)
  if(e.target.id==="ltr"){LT.tf=e.target.value;LT.zr=null;ltGet();return}   // the timeframe is this visit's choice, never stored (#1247)
  if(e.target.id==="lty"){LT.ty=e.target.value;barMoved();return}
  if(e.target.id==="lta"||e.target.id==="ltb"){LT[e.target.id==="lta"?"a":"b"]=e.target.value;chartSave();LT.pick=null;if(LT.zr){ltZoom(LT.zr[0],LT.zr[1]);return}ltGet();return}
  if(e.target.id==="ltbd"){LT.band=e.target.checked;drawFlow();return}   // the band redraws what the plot already holds, never a new read; the state keeps the reader's choice, so the next redraw reads it (#1304, #1353)
  if(e.target.id==="ltmd"){LT.md=e.target.checked;drawFlow();return}   // a reference toggle redraws the held payload, never a new read (#1332)
  if(e.target.id==="ltp85"){LT.p85=e.target.checked;drawFlow();return}};
$("#flowb").onclick=e=>{if(LT.noclick){LT.noclick=false;return}
  if(e.target.id==="ltzc"){LT.zr=null;ltGet();return}
  if(e.target.id==="ltfit"){LT.win="fit";drawFlow();return}
  if(e.target.id==="ltfull"){LT.win="full";drawFlow();return}   // the range control redraws the held payload, never a second read (#1334)
  const k=e.target.closest("circle[data-k]");if(k){LT.pick=+k.dataset.k;drawFlow();return}const r=e.target.closest("tr[data-o]");if(r)showIssue(+r.dataset.o,"__REPO__")};
const flowPick=k=>{ls.flowk=k;save();flowOpen(k)};
$("#bflow").onclick=()=>flowPick("cfd");$("#blead").onclick=()=>flowPick("lead");addEventListener("resize",()=>{if(!$("#flowov").hidden){if(FLOWK==="lead"){LT.fitted=false;LT.fitH=0}drawFlow();if(FLOWK==="lead"){if(LT.defer)ltGet();else ltResize()}}});   // a resize drops the measured plot box, so the new paint measures the box the window gives it; a read deferred by a window with no height lands on this first resize (#2171)
function squadChart(k){if(FLOWK===k&&!$("#flowov").hidden)flowOpen(k)}   // a pushed version redraws only the chart it names
function squadStreamLost(){$("#sse").hidden=false;$("#sse").textContent="live stream lost: retrying; the page keeps what it drew"}
function squadStreamBack(){$("#sse").hidden=true;poll();pollBoard();if(FLOWK&&!$("#flowov").hidden)flowOpen(FLOWK)}   // one read each of the runs, the board and the open chart
// flow-charts:end
const barQuery=k=>{const ty=k==="cfd"?CF.ty:LT.ty,every=k==="cfd"&&ty==="all";return `ty=${every?"task":ty}`+(every?"&every=1":"");};   // the chart's own Type; the cumulative flow opens on the tasks default and sends All as the extra every=1, so the fixed ty values stand (#1356); the Board filter left the page, so no chart sends one (owner ask, SPEC 1965)
let boardTag=null,boardBody=null;   // the board the page holds and its ETag: a 304 keeps it (#973)
async function pollBoard(){lim.busy=1;try{const r=await fetch("/api/board",boardTag?{headers:{"If-None-Match":boardTag}}:{});let b;if(r.status===304&&boardBody)b=boardBody;else{b=await r.json();if(b.groups){boardTag=r.headers.get("ETag");boardBody=b}}const FLOW=/*BOARD_FLOW*/,   // development flow, left to right
all=[b.prs,...b.groups.sort((x,y)=>{const i=n=>{const k=FLOW.indexOf(n);return k<0?FLOW.length:k};return i(x.name)-i(y.name)})],f=q.trim().replace(/^#/,"");
if(bo.open===undefined||!all.some(g=>g.name===bo.open))bo.open=(all.find(g=>g.n&&!g.end&&!isPR(g.name))||all[2]).name;
const CN=all.map(g=>g.name);   // every column of this board: what colOf reads the user columns out of, never a colour of its own
const COLS=b.limits||{};   // the card limit of each column (#2287): the hub resolves it, and the header shows the count against it
lastRead=Date.now();lastBoardT=b.t||0;lastBoardOver=!!b.overdue;lastPending=b.pending_new||0;readStale=!!b.err||lastBoardOver;lastBoardErr=b.err||"";$("#bcases").innerHTML=caseRows(b.cases);$("#bt").textContent=["status","structure","prs"].map(k=>{const p=(b.parts||{})[k];return k+" "+(p&&p.t?Math.round(Date.now()/1000-p.t)+"s":"never")+(p&&p.err?" !":"")}).join(" · ");
const boardGroups=all.filter(g=>!isPR(g.name)),allRows=boardGroups.filter(g=>!g.end).flatMap(g=>g.items);   // every open card feeds the swimlane model and the spec colours (#969)
const sOpts=specOptions(boardGroups,b.sum),SLOTS=specSlots(sOpts.filter(s=>s!=="none"));SPECLC=new Map(allRows.map(x=>[String(x.n),cardColor(SLOTS,x)]).filter(p=>p[1]!==SPEC_GREY));try{if(typeof window!=="undefined")window.SPECLC=SPECLC}catch(e){}try{drawList()}catch(e){}   // the board read may arrive after the runs: repaint the card so the numbers take their spec colour   // a finished spec is no option (#981); one color per spec option on the board (#917)
const hidKept=keptHidden(bo.hidden||[],all);
if(!b.err&&hidKept.length!==(bo.hidden||[]).length){bo.hidden=hidKept;saveB()}   // a saved hidden set drops a closed status on load (#969)
const hid=new Set(hidCols()),bottom=true,eye=(g,off=hid.has(g.name))=>`<span class=eye data-eye="${esc(g.name)}" title="${off?"show":"hide"} this column"><svg viewBox="0 0 24 24" width=14 height=14 fill=none stroke=currentColor stroke-width=2 stroke-linecap=round stroke-linejoin=round><path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx=12 cy=12 r=3 />${off?'<path d="M3 3l18 18"/>':""}</svg></span>`;
const LC=b.lc||{},rgb=h=>[0,2,4].map(i=>parseInt(h.slice(i,i+2),16)),
tint=x=>{const cs=(x.l||[]).map(l=>LC[l]).filter(c=>/^[0-9a-f]{6}$/.test(c||"")).map(rgb);if(!cs.length)return"";const avg=[0,1,2].map(i=>cs.reduce((s,c)=>s+c[i],0)/cs.length);return`;--bg:color-mix(in srgb,var(--card) 90%,rgb(${avg}));--bgh:color-mix(in srgb,var(--card) 84%,rgb(${avg}))`},
lbc=l=>/^[0-9a-f]{6}$/.test(LC[l]||"")?` style="--lb:rgba(${rgb(LC[l])},.30)"`:"";
const age=o=>{let m=Math.max(0,Math.floor((Date.now()-Date.parse(o))/60000));const d=Math.floor(m/1440),h=Math.floor(m%1440/60);m%=60;return "⏱ "+(d?d+"d "+h+"h":h?h+"h "+String(m).padStart(2,"0")+"m":m+"m")};
const ageC=o=>{const h=(Date.now()-Date.parse(o))/3600000;return h>=24?"old":h>=4?"warm":""};
setInterval(()=>document.querySelectorAll("#bbody .age").forEach(e=>{e.textContent=age(e.dataset.o);e.className="age "+ageC(e.dataset.o)}),30000);
const gcls=c=>c.v==="?"?"unk":c.v==="debt"?"debt":c.v.startsWith("OK")?"ok":(c.v.startsWith("CHG")||c.v.startsWith("BLK"))?"bad":"";
const gstrip=s=>{const age=Math.round(Date.now()/1000-s.at),old=age>s.max;return `<div class="gstrip${old?" old":""}" title="${esc(old?`these facts are ${age} s old: the strip greys out past 180 s, so nothing here is being claimed as fresh`:"R review · C CI · T threads · B base · H labels and holds")}">`+s.cells.map(c=>`<span class="gc ${gcls(c)}" title="${esc(c.tip||"")}">${c.k}<b>${esc(c.v)}</b></span>`).join("")+`</div>`};
const cards=(g,it,shown)=>{const GHI='<svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path fill="currentColor" d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z"/></svg>';return shown.map(x=>{const pr=x.kind==="pr",tt=String(x.t).replace(/^\s*\[SPEC(?:\s*#?\d+)?\]\s*/i,"");return `<div class=it data-n="${x.n}" data-r="${esc(x.r||"__REPO__")}"${pr?" data-pr":""} data-col="${encodeURIComponent(g.name==null?"":String(g.name))}" data-pm="${x.pm&&!pr?1:""}" data-b="${encodeURIComponent(JSON.stringify(x.b||[]))}" data-bl="${encodeURIComponent(JSON.stringify(x.bl||[]))}" data-ho="${encodeURIComponent(x.ho==null?"":String(x.ho))}" data-hr="${encodeURIComponent(x.hr==null?"":String(x.hr))}" data-prf="${encodeURIComponent(JSON.stringify(x.prf||[]))}" style="--cc:${colOf(g.name,CN)};--lc:${cardColor(SLOTS,x)}${tint(x)}"><a class="id ghl" target=_blank rel=noopener title="open ${pr?"pull request":"issue"} #${x.n} on GitHub" aria-label="open ${pr?"pull request":"issue"} #${x.n} on GitHub" href="https://github.com/${esc(x.r||"__REPO__")}/${pr?"pull":"issues"}/${x.n}">#${x.n} ${GHI}</a>${x.kind!=="pr"&&x.pa!=null&&!x.sp?`<span class=lchip data-k=spec title="spec #${x.pa}">Spec ${x.pa}</span>`:""}${x.gt?`<span class=lchip data-k=gate title="gate attempts: the card's Gate Tries">Testing · ${x.gt}</span>`:""}${loopBugLabel(x)?`<span class="bugc loop">${esc(loopBugLabel(x))}</span>`:(x.l||[]).includes("bug")?'<span class="bugc">bug</span>':""}${x.rt?`<span class=rpo title="the repository this card belongs to: ${esc(x.r)}">${esc(x.rt)}</span>`:""}${x.pr?`<span class="prio ${x.pr.tone}${pr?"":" pbtn"}" data-level="${x.pr.level}" title="${esc(x.pr.label)}${pr?"":" — set the Priority"}" aria-label="${esc(x.pr.label)}" ${pr?"role=img":"role=button tabindex=0 aria-haspopup=listbox aria-expanded=false"}>${({"double-chevron-up":"⇈","arrow-up":"↑","bar":"▬","arrow-down":"↓"})[x.pr.icon]}</span>${x.pr.inherit?`<span class=pinh title="${x.pr.inherit==="pending"?"inheritance pending":"inherited priority"}">${x.pr.inherit==="pending"?"pending":"inherited"}</span>`:""}${x.prp?`<span class=prp title="a Priority write the board read has not carried yet">pending</span>`:""}${x.prr?`<div class="prr pwr">${esc(x.prr)}</div>`:""}`:""}${x.pb?`<span class="pbg ${x.pb.replace(" ","-")}">${esc(x.pb)}</span>`:""}${x.rollup?`<span class="cir ${esc(x.rollup)}" title="CI states on the head${(x.ci||[]).length?": "+esc((x.ci||[]).join(", ")):""}">CI ${esc(x.rollup)}</span>`:""}${x.fz?`<span class=fz title="a freeze is a marker, never a move: the card keeps its column${x.ho?"; a run is live on it, so the freeze holds the column and not the run":""}">${esc(x.fz)}</span>`:""}${x.pd?`<span class=pnd title="pending: the move is not proved yet, because the writer has not answered, or no board read has carried a move the hub did not see proved">pending</span>`:""}${x.mr?`<div class=perr title="the writer refused this move">${esc(x.mr)}</div>`:""}${x.ps?`<span class="pst ${x.ps[0]}"${x.ps[2]?` title="${esc(x.ps[2])}"`:""}>${esc(x.ps[1])}</span>`:""}${x.c?`<span class=dt>${x.c.slice(5,16).replace("T"," ")}</span>`:""}<span class=tt title="${esc(tt).replace(/"/g,"&quot;")}">${esc(tt)}</span>${x.pm&&!pr?`<button class=pmp data-n="${x.n}" title="move this card to ${esc(x.pmt)}: the owner's go signal (ADR-0142)">→ ${esc(x.pmt)}</button>`:""}${pr?"":`<button class=syncb data-n="${x.n}" title="re-read only this card from GitHub now: its column, labels, holder, blockers and sub-issue count">Sync</button>`}${x.sb?`<button class=spb data-n="${x.n}" title="plan this spec's batch: the hub names the count and the cards first, and nothing moves until you confirm">Start this spec</button>`:""}${x.ps&&x.ps[3]?`<div class=prr>${esc(x.ps[3])}</div>`:""}${x.gs?gstrip(x.gs):""}${(x.l||[]).length?`<span class=lbs>${x.l.map(l=>`<span class="${(bo.labels||[]).includes(l)?"on":""}">${esc(l)}</span>`).join("")}</span>`:""}${(x.b&&x.b.length)?`<div class=blk>blocked by ${x.b.map(n=>`<a target=_blank rel=noopener href="https://github.com/__REPO__/issues/${n}">#${n}</a>`).join(", ")}</div>`:""}${x.ho?`<span class=hld title="${esc(x.ho)}">${esc(x.hc||(x.hs==="running"?"Squad · running":"Squad"))}</span>`:""}${(x.cl||[]).length?`<div class=clos title="the issues this pull request closes">closes ${x.cl.map(n=>`<a target=_blank rel=noopener href="https://github.com/${esc(x.r||"__REPO__")}/issues/${n}">#${n}</a>`).join(", ")}</div>`:""}${(x.o||x.sc||x.by)?`<div class=ft>${x.sc?`<span class=tsk title="sub-issues closed / total: ${esc(x.sc)}">${esc(x.sc)}</span>`:""}${x.o?`<span class="age ${ageC(x.o)}" data-o="${x.o}" title="open since ${x.o.slice(0,16).replace("T"," ")} UTC">${age(x.o)}</span>`:""}${x.by?`<span class=au title="opened by ${esc(x.by)}">${esc(x.by)}</span>`:""}</div>`:""}</div>`}).join("")+(it.length>shown.length?`<div class="it m">${it.length-shown.length} more · scroll</div>`:"")};
// Every column the user leaves visible is a lane of every group, even one holding no card at all (#1994): the lane
// keeps its minimum width, its tint and its label with a zero, so a group reads as the board's columns and an empty
// one reads as empty, never as missing. The visible set is derived on every render -- from the user's hidden set,
// which the eye toggles -- and never stored, so the last hidden column leaving brings the rest back on its own.
const laneHtml=m=>{const ccount=c=>m.lanes.reduce((s,l)=>s+(l.counts[c]||0),0),pct=l=>l.total?Math.round(l.closed/l.total*100):0;
  share=x=>l=>l.total?Math.round((x||0)/l.total*100):0;   // a share of the lane bar, drawn after the closed share: the held green of a claimed card, then the review's own colour (owner, 2026-11-02)
 if(!m.lanes.length)return `<div class=lempty>${f?"No card matches this search.":"No card on the board."}</div>`;
 const headCell=(l,c,cl)=>{const k=c.k,pc=c.text?`<span class=pc>${c.text}</span>`:"",
   inner=k==="id"?`<span class=tw>${cl?"&#9656;":"&#9662;"}</span><span class="lid${l.kind==="sw"?" sw":""}"${l.kind==="sw"?` title="${esc(c.text)}"`:""}>${l.kind==="sw"?`<span class=sid>${esc(l.sid)}</span> · ${esc(l.wl)}`:esc(c.text)}</span>`
   :k==="title"?(c.text?`<span class="ltt${l.kind==="s"?"":" subt"}" title="${esc(c.text)}">${esc(c.text)}</span>`:"")
   :k==="bar"?(c.on?`<span class=bar title="${l.closed} of ${l.total} closed${l.doing?`, ${l.doing} doing`:""}${l.review?`, ${l.review} in review`:""}"><i style="width:${pct(l)}%"></i>${l.doing?`<i class=dn style="left:${pct(l)}%;width:${share(l.doing)(l)}%"></i>`:""}${l.review?`<i class=rv style="left:${pct(l)+(l.doing?share(l.doing)(l):0)}%;width:${share(l.review)(l)}%"></i>`:""}</span>`:"")
   :k==="frac"?pc
   :k==="counts"?c.items.map(([n,t])=>`<span>${esc(n)} <b>${esc(t)}</b></span>`).join(" · ")
   :k==="chip"?(c.text?`<span class=lst>${esc(c.text)}</span>`:"")
   :k==="sync"?(c.on?`<button class=syncb data-n="${c.n}" title="re-read only SPEC ${c.n} from GitHub now: its title, column, labels, holder and sub-issue count">Sync</button>`:"")
   :(c.on?`<a class=ghl target=_blank rel=noopener title="open SPEC ${l.num} on GitHub" aria-label="open SPEC ${l.num} on GitHub" href="https://github.com/${esc(l.r||"__REPO__")}/issues/${l.num}"><svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path fill="currentColor" d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z"/></svg></a>`:"");
 return `<span class="lf lf-${k}${c.full?" full":""}" style="justify-content:${c.al}">${inner}</span>`};
 const drawn=c=>m.lanes.reduce((s,l)=>s+(l.collapsed?0:(l.counts[c]||0)),0);   // a collapsed lane draws no cell, so its cards widen no column of the grid
 const tracks=colTracks(m.vc.map(drawn));   // each column's track takes the weight of its card count in the drawn lanes (#1994)
 let h=`<div class=lgrid style="--vc:${Math.max(1,m.vc.length)};--tracks:${tracks}">`+m.vc.map(c=>`<div class=lcolh style="--cc:${colOf(c,CN)}"><span>${esc(c)}</span><b>${colCount(ccount(c),COLS[c])}</b>${eye({name:c})}</div>`).join("");   // the header counts the whole board, a collapsed lane included
 for(const l of m.lanes){const cl=l.collapsed,bugs=l.kind==="bugs",lc=bugs?"var(--err)":l.num!=null?specColor(SLOTS,l.num):"var(--acc)",   // a spec lane takes its card colour (#917)
   hd=laneHead(l,BOARD_VOCAB).rows;   // the lane reads as the mock: a coloured id, then its title or its subtitle (#889)
  h+=`<section class="lane${cl?" coll":""}${l.kind==="bugs"?" alarm":""}${l.done?" done":""}" data-lane="${esc(l.id)}" style="--lc:${lc}"><header class=lh role=button tabindex=0 aria-expanded="${!cl}" data-lane="${esc(l.id)}">`+hd.map((r,i)=>`<span class=lh-r${i+1}>${r.map(c=>headCell(l,c,cl)).join("")}</span>`).join("")+`</header>`;
  if(!cl)for(const c of m.vc){const key=l.id+"|"+c,v=cellView(l.cells[c],!!(bo.cx||{})[key]);h+=`<div class=cell data-lane="${esc(l.id)}" data-col="${esc(c)}" style="--cc:${colOf(c,CN)}"><div class=clh><span>${esc(c)}</span><b>${l.counts[c]||0}</b></div>${cards({name:c},v.shown,v.shown)}${v.more?`<button class=more data-cell="${esc(key)}" title="show every card of this cell">+${v.more} more</button>`:""}</div>`}
  h+="</section>"}
 return h+"</div>"};
const perr=g=>g.err?`<div class=perr title="a repository the hub was told to read and could not: the rows this column still shows for it are its last good ones">&#9888; ${esc(g.err)}</div>`:"";   // a repository the hub cannot read is an error on the column, never an empty column (#1820)
const fits={};all.forEach(g=>fits[g.name]=g.items.filter(x=>(!f||String(x.n).includes(f)||x.t.toLowerCase().includes(f.toLowerCase()))));   // the Specs filter shows, only shows, or hides the spec-labelled items, never a column of its own (#247, #456)
const kept=new Set(bo.kept||[]),
 autoHidden=g=>!hid.has(g.name)&&!kept.has(g.name)&&!fits[g.name].length&&!g.tracking,   // the columns the strip's lines stand for, and the only ones: a column the user hid is a chip, one the user kept is drawn, and the lane is always drawn -- empty, it says 0 (#440)
 vis=all.filter(g=>!g.end&&(!bottom||(!hid.has(g.name)&&!autoHidden(g)))),autos=bottom?all.filter(g=>!g.end&&autoHidden(g)):[];   // a closed status is never a column, so it has no eye and no line to show it (#969)
const badge=g=>`<span class=n>${f?fits[g.name].length+"/"+g.n:colCount(g.n,COLS[g.name])}</span>`;   // the number the header shows, against its card limit (#2287), and the number the strip's line repeats
const one=g=>{const bmv=g.name===COLNAME.unscheduled?`<button class=bmv type=button title="move every Backlog card to Todo: the hub names how many first">Move all to Todo</button>`:"",it=fits[g.name],shown=it.slice(0,lim[g.name]||25),dot=`<i style="background:${colOf(g.name,CN)}"></i>`,cnt=badge(g),isOpen=g.name===COLNAME.pr_open?bo.prOpen!==false:bo.open===g.name;
if(bottom)return `<div class="col${isPR(g.name)?" prs lane":""}" style="--cc:${colOf(g.name,CN)}" data-g="${esc(g.name)}"><div class=ch><span>${dot}${esc(g.name)}</span><span>${cnt} ${eye(g)}${bmv}</span></div><div class=sc data-g="${esc(g.name)}">${perr(g)}${cards(g,it,shown)}</div></div>`;
return `<div class="acc${isPR(g.name)?" prs lane":""} ${hid.has(g.name)?"hidcol":""} ${isOpen?"open":""}" style="--cc:${colOf(g.name,CN)}" data-g="${esc(g.name)}"><div class=ah><span>${dot}${esc(g.name)}</span><span>${cnt} ${eye(g)}${bmv}</span></div><div class=sc data-g="${esc(g.name)}">${perr(g)}${cards(g,it,shown)}</div><div class=pad></div></div>`};
const fmtN=n=>n>=1000?n.toLocaleString("en-US"):""+n;   // "688" is one token; only 1000 and over needs separators
// One tile of the hidden: strip: the name as a caption over the count, the count's colour deciding what the count
// means. data-col names the column, so the colour is a CSS rule per category rather than an inline value; the one
// exception is a column #1793's map does not name, which takes the ring colour and can only come from colOf here.
const hidchip=g=>{const c=colOf(g.name,CN),ring=!Object.hasOwn(COLORS,g.name);return `<span class=chip data-eye="${esc(g.name)}" data-col="${esc(g.name)}" title="${esc(g.name+" — "+fmtN(g.n))}"><span class=lbl>${esc(g.name)}</span><b${ring?` style="color:${c}"`:""}>${fmtN(g.n)}</b></span>`};
const endLines=all.filter(g=>g.end).map(g=>`<span class=hidline><span>${esc(g.name)}</span><b>${fmtN(g.n)}</b></span>`).join("");   // a closed status is a text total: no eye, no show action (#969)
const html=vis.map(one).join("")+(bottom&&(hid.size||autos.length||endLines)?`<div class=hidbar>${all.filter(g=>!g.end&&hid.has(g.name)).map(hidchip).join("")}${hid.size&&autos.length?'<div class=hidsep></div>':""}${autos.map(g=>`<span class=hidline style="--cc:${colOf(g.name,CN)}" data-g="${esc(g.name)}"><span>${esc(g.name)}</span><b>${badge(g)}</b>${eye(g,true)}</span>`).join("")}${endLines}</div>`:"");   // one quiet line per column hidden only because it is empty, behind a divider (#1809)
const model=buildBoard(all.filter(g=>!isPR(g.name)),{closed:COLNAME.issue_closed,doing:BOARD_VOCAB.doing,review:BOARD_VOCAB.review,sum:b.sum,hidden:hidCols(),lc:bo.lc||{},hideDone:!!bo.hideDone,search:!!f,text:x=>!f||String(x.n).includes(f)||x.t.toLowerCase().includes(f.toLowerCase())});   // the lanes read every row; the search and the filters only hide rows (#890)
const lanesHtml=laneHtml(model);lanesNow=model;$("#lhd").checked=!!bo.hideDone;
$("#bbody").classList.add("lanes");
$("#lhc").innerHTML=model.hc.length?`<button class=hcpill id=lhcx title="columns with no card in an expanded lane: click to expand the lanes that hold their cards">+${model.hc.length} hidden: ${model.hc.map(x=>x.user?`<span class=hcu data-eye="${esc(x.name)}" title="show this column">${esc(x.name)} <b>${x.count}</b></span>`:esc(x.name)+" <b>"+x.count+"</b>").join(" · ")}</button>`:"";
if(model){if($("#bbody").dataset.h!==lanesHtml){$("#bbody").dataset.h=lanesHtml;$("#bbody").innerHTML=lanesHtml;restore()}}
else if($("#bbody").dataset.h!==html){$("#bbody").dataset.h=html;$("#bbody").innerHTML=html;restore()}   // no lane model: the columns stand, and a scroll in progress is never interrupted
boardRows=all.flatMap(g=>g.items);paintHL();
}catch(e){}finally{lim.busy=0}}
// highlight:begin
// The highlight follows the selection and is never stored: `hl` is {run: id} for a run, or {n, r, kind} for a card. Its
// matches come from the rows the page already holds, so the rule adds no read.
const hlKind=x=>x.kind==="pr"?"pr":"issue",hlRepo=x=>x.r||HUB_REPO,hlKey=x=>hlRepo(x)+"#"+hlKind(x)+"#"+x.n;
const hlRunNames=(run,x)=>!!run&&hlRepo(x)===HUB_REPO&&(hlKind(x)==="pr"?run.fix!=null&&+x.n===+run.fix:(run.issues||[]).some(i=>+i===+x.n));
const hlCards=(run,items)=>run?items.filter(x=>hlRunNames(run,x)):[];
const hlRuns=(card,runList)=>card?runList.filter(r=>hlRunNames(r,card)):[];
const hlHidden=(matched,rendered)=>matched.filter(x=>!rendered.has(hlKey(x)));
function paintHL(){
  if(hl&&hl.run!=null&&!runs.some(r=>r.id===hl.run))hl=null;
  const run=hl&&hl.run!=null?runs.find(r=>r.id===hl.run):null,card=hl&&hl.run==null?hl:null;
  const matched=hlCards(run,boardRows),mk=new Set(matched.map(hlKey)),rendered=new Set();
  document.querySelectorAll("#bbody .it[data-n]").forEach(el=>{const k=el.dataset.r+"#"+(el.hasAttribute("data-pr")?"pr":"issue")+"#"+el.dataset.n;
    rendered.add(k);   // a card the board draws is in #bbody; a collapsed swimlane and a filter both leave it out
    el.classList.toggle("hl",mk.has(k));el.classList.toggle("dim",!!run&&!mk.has(k))});
  const hidden=hlHidden(matched,rendered),hb=$("#hlrel");hb.hidden=!hidden.length;hb.textContent=hidden.length+" related";
  document.querySelectorAll("#bbody .lane[data-lane]").forEach(e=>{const l=(lanesNow&&lanesNow.lanes||[]).find(x=>x.id===e.dataset.lane),n=l&&run?hlHidden(hlCards(run,l.all),rendered).length:0;
    let s=e.querySelector(".lhrel");if(!n){s&&s.remove();return}
    if(!s){s=document.createElement("span");s.className="hlrel lhrel";e.querySelector(".lh").append(s)}
    s.textContent=n+" related"});
  const hits=new Set(hlRuns(card,runs).map(r=>r.id));
  document.querySelectorAll("#main .r[data-rid]").forEach(e=>e.classList.toggle("hlr",hits.has(e.dataset.rid)));
}
// highlight:end

// A tab only shows one pane; every pane keeps its own poll (#889).
let TAB="board",lastRead=0,readStale=false,lastBoardErr="",lastBoardT=0,lastBoardOver=false,lastPending=0;
const TABS={board:"#tab-board",runs:"#tab-runs",flow:"#tab-flow",prof:"#tab-prof",set:"#tab-set"};
const HASH={board:"#kanban",runs:"#runs",flow:"#flow",prof:"#loop",set:"#settings"},HASH_TAB={"#kanban":"board","#runs":"runs","#flow":"flow","#loop":"prof","#settings":"set","#set":"set"};   // #set is the Settings alias saved in old links (#1274)
function tabFromLocation(){if(/issue=\d+/.test(location.hash))return "board";return HASH_TAB[location.hash]||(/^#set-[a-z]+$/.test(location.hash)?"set":null)}   // a card link opens Kanban, where the card is (#1274); a themed Settings link opens Settings (#1286)
function tabNext(k,i,n){return k==="ArrowRight"?(i+1)%n:k==="ArrowLeft"?(i+n-1)%n:k==="Home"?0:k==="End"?n-1:-1}
function setTab(t,o){if(!TABS[t])t="board";TAB=t;ls.tab=t;save();
 for(const k in TABS)$(TABS[k]).classList.toggle("on",k===t);
 document.querySelectorAll(".tabs button").forEach(b=>{const on=b.dataset.t===t;b.classList.toggle("on",on);b.setAttribute("aria-selected",on);b.tabIndex=on?0:-1});
 if(location.hash!==HASH[t]&&!/issue=\d+/.test(location.hash))history.replaceState(null,"",HASH[t]);   // the address bar names the pane shown; a card link keeps its form
 if(t==="set")setTheme(sTheme);   // entering the tab shows one theme, the last one picked (#913)
 if(t==="flow"&&(!o||!o.keep)&&$("#flowov").hidden)flowOpen(ls.flowk==="lead"?"lead":"cfd");
 if(t==="prof")profPoll();   // one read on entry states the switch; the timer reads on only while it is on (#1743)
 drawMain()}
function tabClick(e){const b=e.target.closest("button[data-t]");if(b)setTab(b.dataset.t)}   // one control names its tab by data-t; the bar and the live-runs card share the handler (#1276)
document.querySelector(".tabs").addEventListener("click",tabClick);
function tabKey(e){const bs=[...document.querySelectorAll(".tabs button[data-t]")],n=tabNext(e.key,bs.indexOf(e.target),bs.length);if(n<0)return;e.preventDefault();e.stopPropagation();setTab(bs[n].dataset.t);bs[n].focus()}
document.querySelector(".tabs").addEventListener("keydown",tabKey);   // the bar is a tab list: focus and the shown pane move together (#1274)
$("#lrun").addEventListener("click",tabClick);   // a control of the live-runs card names its tab by data-t, as the bar does (#1276)
$("#lrun").addEventListener("click",e=>{const r=e.target.closest(".lr[data-id]");if(!r)return;const id=r.dataset.id;hl={run:id};paintHL();openRun(id)});   // a card row opens the run sheet, exactly as the list's row does (#1276)
$("#lrun").addEventListener("keydown",e=>{const r=e.target.closest(".lr[data-id]");if(r&&(e.key==="Enter"||e.key===" ")){e.preventDefault();const id=r.dataset.id;hl={run:id};paintHL();openRun(id)}});   // the card row answers the keyboard too (#1276)
function flowTabs(){const h=$("#flowov").hidden,k=typeof FLOWK==="string"?FLOWK:"cfd";$("#bflow").classList.toggle("on",!h&&k==="cfd");$("#blead").classList.toggle("on",!h&&k==="lead")}   // the two chart tabs mark the open chart
$("#main").addEventListener("click",e=>{const r=e.target.closest(".r[data-rid]");if(!r)return;const id=r.dataset.rid;hl={run:id};paintHL();openRun(id)});   // a run row on the Runs tab opens the sheet (#984) and marks its cards, as the old list row did (#1770)
$("#main").addEventListener("keydown",e=>{const r=e.target.closest(".r[data-rid]");if(r&&(e.key==="Enter"||e.key===" ")){e.preventDefault();openRun(r.dataset.rid)}});
new MutationObserver(flowTabs).observe($("#flowov"),{attributes:true});
{const pn=(location.pathname.match(/^\/([^/?#]+)/)||[])[1]||"";$("#pjn").textContent=pn?decodeURIComponent(pn):"hub";
 const pj=$("#pj"),psw=$("#psw");pj.onclick=e=>{e.stopPropagation();const on=!psw.classList.contains("on");psw.classList.toggle("on",on);pj.setAttribute("aria-expanded",on)};
 document.addEventListener("click",e=>{if(!e.target.closest("#psw")&&psw.classList.contains("on")){psw.classList.remove("on");pj.setAttribute("aria-expanded","false")}})}
// "board items read N s ago": the item read's own stamp (#1661), never the browser's fetch time
setInterval(()=>{const b=$("#bread");if(!b)return;b.classList.toggle("stale",readStale||!!lastPending);$("#bbody").classList.toggle("stale",readStale);b.textContent=lastRead?(lastBoardT?`board items read ${Math.max(0,Math.round(Date.now()/1000-lastBoardT))} s ago${lastBoardOver?" (overdue)":readStale?" (stale: "+lastBoardErr+")":""}${lastPending?` · ${lastPending} card(s) seen, not read yet`:""}`:"board items not read yet"):""},1000);   // the stale class names a card the fast read has seen and the full read has not written (#985)
// loop-switch:start
// The loop switch (#923, ADR-0162): /api/loop is the one read and the one write. The Monitor and the Loop tab draw one view;
// a start posts at once, a stop opens the confirm, which drains by default and lists every live run and gate a stop-now kills.
// The renderers are pure: tests/test_hub_loop_switch_page.py runs them in node.
let loopV=null,loopErr="";
// SETT{ the Settings tab's one shape (#1278). Every theme draws a Now block of tiles and a Settings block of rows through
// these writers, and the whole tab writes a moment and a span one way. Both read the value the hub gives: an epoch second or
// the hub's own date text for a moment, a count of seconds for a span. Run in node by tests.
const SETT_TONES=["ok","warn","err","neutral","muted"];   // the tones the hub names: FOOT_TONES plus the neutral one and the greyed one
function moment(t){const s=String(t??"").replace(" ","T"),m=s.match(/^(\d{4}-\d\d-\d\d)T(\d\d:\d\d)/);if(m)return m[1]+" "+m[2];
 if(typeof t==="number"||/^\d+(\.\d+)?$/.test(s)){const n=typeof t==="number"?t:+s,d=new Date(n*1000),p=x=>String(x).padStart(2,"0");if(isNaN(d))return "?";return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`}
 return s?String(t):"?"}   // a value the hub hands in another shape is shown as it came, never replaced by "?"
function span(s){const n=Number(s);if(s===null||s===undefined||s===""||!Number.isFinite(n)||n<0)return "?";   // a bad reading is never zero
 const x=Math.round(n);if(x<90)return x+"s";const d=Math.floor(x/86400),h=Math.floor(x%86400/3600),m=Math.floor(x%3600/60);return d?`${d}d ${h}h`:h?`${h}h ${m}m`:`${m}m`}
function setTile(k,v,tone,tip){const t=SETT_TONES.includes(tone)?tone:"neutral";return `<div class="tile ${t}"${tip?` title="${esc(tip)}"`:""}><span class=tk>${esc(k)}</span><span class=tv>${esc(v)}</span></div>`}
function setNow(theme,tiles,note){return `<div class=setblk data-blk=now data-theme="${esc(theme)}"><h3 class=blkh>Now</h3><div class=now>${tiles.filter(Boolean).join("")}</div>${note?`<p class=note>${note}</p>`:""}</div>`}   // the note is caller-escaped HTML: a caller passes text only through esc(), and the note holds one row, never a fact
function setRow(label,control,tip){return `<div class=setrow${tip?` title="${esc(tip)}"`:""}><span class=sl>${esc(label)}</span><span class=sc>${control}</span></div>`}
function setSettings(theme,rows,note){return `<div class=setblk data-blk=settings data-theme="${esc(theme)}"><h3 class=blkh>Settings</h3>${rows.filter(Boolean).join("")}${note?`<p class=note>${note}</p>`:""}</div>`}
/* One text in both places, and one err tone when the state needs it: tone defaults to neutral, which clears "err"
   from both chips, so a call site that needs the tone passes it (Spend & limits does, on a paused dispatch). */
function setChip(theme,text,tone){const sr=$("#"+theme+"sr"),nv=$("#"+theme+"nav"),cls=tone==="err"?"err":"";
 if(sr){sr.textContent=text;sr.className="sr"+(cls?" "+cls:"")}if(nv){nv.textContent=text;nv.className=cls}}
/* The Save dialog groups its lines by the file the hub names, so one confirm covers several files without doubt. */
function diffByFile(groups){const out=[];for(const g of groups||[]){if(!g||!(g.lines||[]).length)continue;out.push(g.file||"(the project files)");for(const l of g.lines)out.push("  "+l)}return out.join("\n")}
/* A read-only view disables every control under its roots, an anchor included, and adds any control a render writes later.
   The theme navigation is reading, not writing: the nav buttons and the phone select stay live so every panel opens. */
function roAll(root){if(!root||!root.querySelectorAll)return;for(const el of root.querySelectorAll("input,select,textarea,button,a[href]")){
 if(el.closest&&(el.closest(".snav")||el.closest("#ssel")))continue;
 el.disabled=true;if(el.tagName==="A"){el.setAttribute("aria-disabled","true");el.style.pointerEvents="none";el.tabIndex=-1}}}
function roWatch(root){roAll(root);if(typeof MutationObserver!=="undefined"&&root)new MutationObserver(()=>roAll(root)).observe(root,{subtree:true,childList:true})}
// }SETT
const loopTip=v=>!v.write?v.reason:v.state==="on"||v.state==="draining"?"turn off":v.state==="off"?"turn on":v.state==="starting"?"the loop is starting":"";
const loopSw=v=>`<button type=button role=switch aria-label="Loop" aria-checked="${v.state!=="off"}" data-loop${v.write&&v.state!=="starting"?"":" disabled"} class="lsw${v.state==="draining"?" dra":""}"${loopTip(v)?` title="${esc(loopTip(v))}"`:""}><i aria-hidden="true"></i></button>`;   // a draining loop reads the warn colour on the switch itself, not only on the word beside it
const loopAct=e=>e.failed?`${String(e.action||"")} failed: ${e.failed}`:e.action==="drain"&&e.issue?`drain for the loop-bug #${e.issue}`:e.action==="start"&&e.issue?`start: no loop-bug is open after #${e.issue}`:e.action==="drain"?"stop after the live runs and gates finish":e.action==="stop-now"?`stop now${e.killed?` (killed ${(e.killed.runs||[]).length} runs, ${(e.killed.gates||[]).length} gates${e.survived?`; ${(e.survived.runs||[]).length+(e.survived.gates||[]).length} not ended`:""})`:""}`:String(e.action||"");
const loopRun=r=>String(r).split("/").filter(Boolean).pop()||String(r);
const loopCause=v=>{const n=v.cause&&v.cause.issue;return !n?"":v.state==="draining"?` for the loop-bug #${n}`:v.state==="off"?` until the loop-bug #${n} closes`:""};   // why a drained loop is off (#1708)
const loopSetHtml=v=>{const h=v.health||{},left=v.state==="starting"&&h.starting_left!=null?` · ${span(h.starting_left)} left`:"";
 return `<div class=lnow>${loopSw(v)}<span class="ls ${esc(v.state)}">Loop ${esc(v.state)}${esc(loopCause(v))}${left}</span></div>`
 +(v.write?"":`<div class=lwhy>${esc(v.reason)}. The rule is in <a href="#set" data-sp=acc>Access</a>.</div>`)
 +(v.failed?`<div class=lerr role=alert>The last start failed: ${esc(v.failed)} <button type=button data-loop${v.write?"":" disabled"}>Try again</button></div>`:"")
 +(loopErr?`<div class=lerr role=alert>${esc(loopErr)}</div>`:"")};
const loopLogHtml=v=>{const log=v.log||[];   // the Loop tab draws the change list alone: the state, the live count and the health stay in the Monitor (#2515)
 return `<details class=aud><summary>Changes <span class=m>${log.length}</span></summary><ul class=laud>${log.length?log.map(e=>`<li><b>${esc(moment(e.at))}</b> · ${esc(e.who)} (${esc(e.from)}) · ${esc(loopAct(e))}</li>`).join(""):"<li class=m>no change yet</li>"}</ul></details>`};
const loopConfirmHtml=v=>{const r=v.live.runs,g=v.live.gates,drain=v.state==="on";
 return `<b>Stop the loop?</b><p class=m>Live now: ${r.length} run${r.length===1?"":"s"}, ${g.length} gate${g.length===1?"":"s"}.</p>`
 +(drain?`<label class=lrad><input type=radio name=lm value=drain checked><span><b>Stop after the live runs and gates finish</b><small>No new dispatch. The live work runs to its end, then the loop stops. The safe default.</small></span></label>`:"")
 +`<label class=lrad><input type=radio name=lm value=stop-now${drain?"":" checked"}><span><b>Stop now</b><small>Kills these at once; their work in progress is lost:</small><ul>${r.map(x=>`<li>run ${esc(loopRun(x))}</li>`).join("")}${g.map(x=>`<li>${esc(x)}</li>`).join("")}${r.length||g.length?"":"<li>no live run or gate</li>"}</ul></span></label>`
 +`<div class=lact><button value=ok id=lok>Confirm</button><button value=no id=lno>Back</button></div>`};
const accHtml=v=>{const a=v.access||{},yn=x=>x?"allowed":"refused",ty=x=>x?"ok":"err";
 const rv=x=>v.withheld?"withheld":yn(x),rt=x=>v.withheld?"neutral":ty(x);   // a refused viewer never read a rule: no "refused" for it (#1285)
 const route=v.tailnet?"over the tailnet, through the hub":"from this machine (loopback)";
 const reachTip=v.reach?`the request ${route}`:`the request ${route} was refused${v.reason?`: ${v.reason}`:""}`;   // the tone is the access answer; the route is the transport, in the hover
 const note=v.file?`Read from <code>${esc(v.file)}</code>.`:v.withheld?"This read is refused; the two rules are withheld and stay with the owner.":"";
 return setNow("acc",[
  setTile("Reached",yn(v.reach),ty(v.reach),reachTip),
  setTile("Loop write from the tailnet",rv(a.loop_write),rt(a.loop_write),"SQUAD_TAILNET_LOOP_WRITE (tailnet.loop_write)")],note)
 +setSettings("acc",[],`Read here; the owner sets each value in the project file or <code>.env</code> on the machine (ADR-0152, ADR-0162). A write the tailnet refuses still works from this machine.`)};
// The Access theme reads its own route (#1285): the reach and the two rules never ride on the loop answer, so a stopped
// loop still states them.
let accV=null,accBusy=false;
const accDraw=()=>{if(!accV)return;const reach=accV.tailnet?"tailnet":"loopback";   // the theme's chip and the nav's small line, one live fact
 const sr=$("#accsr");if(sr)sr.textContent=reach;const nv=$("#accnav");if(nv)nv.textContent=reach;
 const c=$("#accb"),ah=accHtml(accV);if(c.dataset.h!==ah){c.dataset.h=ah;c.innerHTML=ah}};
async function accPoll(){if(accBusy)return;accBusy=true;try{accV=await (await fetch("/api/access")).json();accDraw()}catch(e){}finally{accBusy=false}}
// loop-switch:end
// One switch, in the Monitor where the owner looks (owner, from #1071's cleanup): the Loop tab keeps the change log,
// and no second button carries the same id (ADR-0162). The run history left for the bottom of the Runs tab (#1277).
function loopDraw(){if(!loopV)return;
 const a=$("#iloop"),h=loopSetHtml(loopV);if(a.dataset.h!==h){a.dataset.h=h;a.innerHTML=h}
 const b=$("#lopaud"),lg=loopLogHtml(loopV);if(b&&b.dataset.h!==lg){b.dataset.h=lg;b.innerHTML=lg}}
let liveWork=null;   // the hub's Live work answer (/api/live): the loop's state, the tagged rows and the board read's age
async function livePoll(){try{liveWork=await (await fetch("/api/live")).json()}catch(e){liveWork={loop:"unknown",boardAge:null,lines:[{kind:"notice",id:"",text:"live work unreadable"}]}}refresh()}   // a failed read is named in the panel, never drawn as an empty tile (#1697)
async function loopPoll(){try{loopV=await (await fetch("/api/loop")).json();loopDraw()}catch(e){}}
async function loopPost(body){loopErr="";try{const r=await fetch("/api/loop",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({...body,client:"desktop"})}),d=await r.json();if(!r.ok)loopErr=d.reason||d.err||"refused"}catch(e){loopErr="the hub did not answer"};livePoll()   // a start, a drain or a stop changes the live rows: read them now, never on the next timer (#1697)
 await loopPoll();loopDraw()}
document.addEventListener("click",e=>{const b=e.target.closest("button[data-loop]");if(!b||b.disabled||!loopV)return;e.preventDefault();
 if(loopV.state==="off"){loopPost({action:"start"});return}
 const d=$("#ldg");$("#ldgb").innerHTML=loopConfirmHtml(loopV);d.showModal()});
$("#ldg").addEventListener("close",()=>{const d=$("#ldg");if(d.returnValue!=="ok")return;const m=(d.querySelector('input[name="lm"]:checked')||{}).value;
 if(m==="drain")loopPost({action:"drain"});else if(m==="stop-now")loopPost({action:"stop-now",confirm:true})});
setInterval(pollIfVisible(loopPoll),5000);loopPoll();livePoll();   // the live work is read on load, beside the loop's own state (#1697)
setInterval(pollIfVisible(accPoll),5000);accPoll();
/*PROFILER_JS*/
profMount("desktop");
const PROF_MS=Number("__PROFMS__")||2000;   // the view's poll cadence, substituted when the page is served from the sampler's own interval (#2848); the fallback keeps a slice of this script evaluable on its own
setInterval(()=>{if(profDue(TAB,document.hidden,profV))profPoll()},PROF_MS);   // off, or on another tab, the page asks nothing
setInterval(()=>{if(profFileDue(TAB,document.hidden,profV))profPoll()},30000);   // an off switch still refreshes the file views, the trip bar among them, at a slower cadence
/*BOARD_LANES*/
// The swimlanes' state (#891): collapse and open state live in `bo`, so the device keeps them across a reload.
async function laneToggle(id,open){
 if(!open){bo.lc[id]=true;saveB();await pollBoard();return}
 const s=document.querySelector(`#bbody .lane[data-lane="${CSS.escape(id)}"]`);
 if(s&&!s.querySelector(".lload"))s.querySelector(".lh").insertAdjacentHTML("afterend",`<div class=lload role=status aria-live=polite>Loading the lane contents.</div>`);
 bo.lc[id]=false;saveB();await pollBoard()}
let lanesNow=null;   // the model the board last drew: the controls act on its lanes and its hidden columns
$("#lhd").onchange=e=>{bo.hideDone=e.target.checked;saveB();pollBoard()};
const laneSet=v=>{bo.lc=bo.lc||{};for(const l of(lanesNow?lanesNow.lanes:[]))bo.lc[l.id]=v;saveB();pollBoard()};
$("#lcall").onclick=()=>laneSet(true);$("#lexp").onclick=()=>laneSet(false);
document.addEventListener("click",e=>{if(e.target.closest(".ghl"))return;const h=e.target.closest("#bbody .lh");if(h){const id=h.dataset.lane,coll=h.closest(".lane").classList.contains("coll");bo.lc=bo.lc||{};laneToggle(id,coll);return}
 const m=e.target.closest("#bbody .more");if(m){bo.cx=bo.cx||{};bo.cx[m.dataset.cell]=true;saveB();pollBoard();return}   // a more click opens its own cell only
 const p=e.target.closest("#lhcx");if(p&&lanesNow){const names=new Set(lanesNow.hc.map(x=>x.name));bo.lc=bo.lc||{};   // a hidden column's pill expands the lanes that hold its cards
  for(const l of lanesNow.lanes)if(l.collapsed&&Object.keys(l.counts).some(c=>names.has(c)))bo.lc[l.id]=false;saveB();pollBoard()}});
const barMoved=()=>{LT.tok++;LT.zc=null;LT.zr=null;FLOWD={};if(!$("#flowov").hidden&&FLOWK){flowOpen(FLOWK);if(FLOWK==="lead"&&ltOwn())ltGet()}};   // a chart value changes the open chart: it reads its route anew, and a chosen span its own chart (#904, #905)

// The owner's one move (#560): the arrow a Backlog card carries delegates to the hub, and the card shows the reason a
// refusal gives. Which cards carry the arrow is the row's own fact (`x.pm`), never the page's rule.
document.addEventListener("click",async e=>{const b=e.target.closest("#bbody .pmp");if(!b||b.disabled)return;e.preventDefault();e.stopPropagation();const n=+b.dataset.n,card=b.closest(".it"),label=b.textContent;b.disabled=true;b.textContent="sending…";
try{const r=await fetch("/api/promote",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({issue:n})}),d=await r.json();
 if(d.err){card.insertAdjacentHTML("beforeend",`<div class=perr>${esc(d.err)}</div>`);b.textContent=label;b.disabled=false}
 else{b.textContent="sent to Todo";pollBoard()}}catch(x){card.insertAdjacentHTML("beforeend","<div class=perr>the hub did not answer</div>");b.textContent=label;b.disabled=false}});
// The owner's Priority dropdown (#2289): a press on the card's mark opens the four levels, the card's own level is
// selected, arrow keys move and Enter chooses. Escape and a press outside close it with no write. The press draws the new
// mark and a pending chip at once; the hub writes the card's Priority in the background and a refusal names its reason and
// restores the mark the read holds. A pull request card carries no mark and so no dropdown.
const PRD_LEVELS=["urgent","high","normal","low"],PRD_ICON={urgent:"⇈",high:"↑",normal:"▬",low:"↓"},PRD_TONE={urgent:"red",high:"red",normal:"yellow",low:"blue"};
const prdClose=()=>{for(const d of document.querySelectorAll("#bbody .prd"))d.remove();for(const b of document.querySelectorAll("#bbody .prio.pbtn"))b.setAttribute("aria-expanded","false")};
const prdPaint=(b,l)=>{b.dataset.level=l;b.className="prio pbtn "+PRD_TONE[l];b.textContent=PRD_ICON[l];b.title="Priority "+l+" — set the Priority";b.setAttribute("aria-label","Priority "+l)};
const prdShow=b=>{prdClose();const cur=b.dataset.level,dd=document.createElement("div");dd.className="prd";dd.setAttribute("role","listbox");dd.setAttribute("aria-label","Priority");dd.innerHTML=PRD_LEVELS.map(l=>`<div role=option tabindex=-1 data-level="${l}" aria-selected="${l===cur}">${PRD_ICON[l]} ${l[0].toUpperCase()+l.slice(1)}</div>`).join("");b.setAttribute("aria-expanded","true");b.parentElement.insertBefore(dd,b.nextSibling);(dd.querySelector("[aria-selected=true]")||dd.firstElementChild).focus()};
const prdPick=async(b,l)=>{const card=b.closest(".it"),n=+card.dataset.n,old=b.dataset.level;prdClose();if(l===old)return;prdPaint(b,l);
try{const r=await fetch("/api/board/priority",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({issue:n,priority:l})}),d=await r.json();
 if(d.err){prdPaint(b,old);card.insertAdjacentHTML("beforeend",`<div class="prr pwr">${esc(d.err)}${d.reason?" — "+esc(d.reason):""}</div>`)}
 else pollBoard()}catch(x){prdPaint(b,old);card.insertAdjacentHTML("beforeend",'<div class="prr pwr">the hub did not answer</div>')}};
document.addEventListener("click",e=>{const b=e.target.closest("#bbody .prio.pbtn");if(b){e.preventDefault();e.stopPropagation();b.getAttribute("aria-expanded")==="true"?prdClose():prdShow(b);return}const o=e.target.closest("#bbody .prd [role=option]");if(o){e.preventDefault();e.stopPropagation();prdPick(o.closest(".prd").previousElementSibling,o.dataset.level);return}if(!e.target.closest("#bbody .prd"))prdClose()});
document.addEventListener("keydown",e=>{const b=e.target.closest&&e.target.closest("#bbody .prio.pbtn");
 if(e.key==="Escape"){const open=document.querySelector("#bbody .prd");if(open){e.preventDefault();const btn=open.previousElementSibling;prdClose();if(btn)btn.focus()}return}
 if(b&&(e.key==="Enter"||e.key===" "||e.key==="ArrowDown")){e.preventDefault();prdShow(b);return}
 const o=e.target.closest&&e.target.closest("#bbody .prd [role=option]");if(!o)return;
 const opts=[...o.parentElement.querySelectorAll("[role=option]")],i=opts.indexOf(o);
 if(e.key==="ArrowDown"){e.preventDefault();opts[Math.min(i+1,opts.length-1)].focus()}
 else if(e.key==="ArrowUp"){e.preventDefault();opts[Math.max(i-1,0)].focus()}
 else if(e.key==="Enter"){e.preventDefault();prdPick(o.parentElement.previousElementSibling,o.dataset.level)}
 else if(e.key==="Tab")prdClose()});
// Sync one card (#1668, #1669): one read serves the card's Sync control and the sheet's. The answer names the rows it
// refreshed, their count and the instant of that read, never the whole board's. A failed read leaves the rows the page
// already had and names the error where the press began; a card GitHub no longer carries keeps its last good row and is
// named. The sheet and the card draw from that one answer, so they never disagree about a column, a label or a holder.
const syncInstant=d=>String(d.at||"").replace("T"," ").replace("Z"," UTC");
const syncOne=n=>fetch("/api/sync",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({n})}).then(r=>r.json());
const syncFacts=r=>({n:+r.n,r:r.r||"",col:r.st==null?"":String(r.st),labels:r.l||[],ho:r.ho||"",hc:r.hc||"",hs:r.hs||"",b:r.b||[],bl:r.bl||[],prf:r.prf||[],t:r.t||""});
const syncFactsHtml=f=>{const link=m=>`<a href="#" data-o="${m}" data-r="${esc(f.r||irepo)}">#${m}</a>`;return `<div class=row><b>Column</b><span>${esc(f.col||"—")}</span></div><div class=row><b>Labels</b><span>${(f.labels||[]).length?f.labels.map(l=>`<span class=lab>${esc(l)}</span>`).join(""):"none"}</span></div><div class=row><b>Blocked by</b><span>${(f.b||[]).length?f.b.map(link).join(", "):"none"}</span></div><div class=row><b>Blocks</b><span>${(f.bl||[]).length?f.bl.map(link).join(", "):"none"}</span></div><div class=row><b>Runs</b><span>${f.ho?esc(f.ho):"none yet"}</span></div><div class=row><b>PR · CI</b><span>${(f.prf||[]).length?f.prf.map(x=>`PR #${x.n} · ${esc(x.st||"")}${x.ci?` · CI ${esc(x.ci)}`:""}`).join("<br>"):"none"}</span></div>`};
const syncMove=(el,col)=>{if(!col)return;const lane=el.closest(".lane"),cell=lane&&lane.querySelector(`.cell[data-col="${CSS.escape(col)}"]`),box=cell||document.querySelector(`#bbody .col[data-g="${CSS.escape(col)}"]>.sc`)||document.querySelector(`#bbody .acc[data-g="${CSS.escape(col)}"]>.sc`);if(box&&el.parentElement!==box)box.appendChild(el)};
// (#1685) The label's tint rule (`lbc`) belongs to the draw function's scope, so this row paints its labels plain: the board's
// next draw tints them. A press that called it threw on every card that carried a label, and the press reported that as "the
// hub did not answer", because the throw lands after the read answered.
const syncRow=(el,f)=>{if(!el)return;const set=(sel,html)=>{let x=el.querySelector(sel);if(x)x.innerHTML=html;else if(html){x=document.createElement("span");x.className=sel.slice(1);x.innerHTML=html;el.appendChild(x)}};el.dataset.col=encodeURIComponent(f.col||"");el.dataset.b=encodeURIComponent(JSON.stringify(f.b));el.dataset.bl=encodeURIComponent(JSON.stringify(f.bl));el.dataset.ho=encodeURIComponent(f.ho);el.dataset.prf=encodeURIComponent(JSON.stringify(f.prf));const t=el.querySelector(".tt");if(t&&f.t)t.textContent=String(f.t).replace(/^\s*\[SPEC(?:\s*#?\d+)?\]\s*/i,"");set(".lbs",(f.labels||[]).map(l=>`<span class="${(bo.labels||[]).includes(l)?"on":""}">${esc(l)}</span>`).join(""));set(".blk",(f.b||[]).length?"blocked by "+f.b.map(m=>`<a target=_blank rel=noopener href="https://github.com/${esc(el.dataset.r||"__REPO__")}/issues/${m}">#${m}</a>`).join(", "):"");let hd=el.querySelector(".hld");if(f.ho){if(!hd){hd=document.createElement("span");hd.className="hld";el.appendChild(hd)}hd.title=f.ho;hd.textContent=f.hc||(f.hs==="running"?"Squad · running":"Squad")}else if(hd)hd.remove();syncMove(el,f.col)};
const syncApply=d=>{if(d.err||d.missing||!(d.count>0))return false;for(const r of d.rows||[]){const f=syncFacts(r);syncRow(document.querySelector(`#bbody .it[data-n="${f.n}"]`),f);const box=$("#tkfacts");if(box&&$("#tk").classList.contains("on")&&+cur===f.n)box.innerHTML=syncFactsHtml(f)}return true};
const syncNote=d=>{const s=$("#bsync");if(!s)return;const at=syncInstant(d);s.title=d.err||"";s.textContent=d.err?`sync failed: ${d.err}`:d.missing?`0 rows refreshed: ${d.note||"#"+d.n+" is not on GitHub"}`:d.count?`${d.count} row refreshed at ${at}${d.capped?` · capped at the ${d.capped} bound: press again to continue`:""}`:`0 rows refreshed at ${at}${d.note?": "+d.note:""}`};
const syncSheetNote=d=>{const s=$("#tksyncnote");if(!s||!$("#tk").classList.contains("on")||+cur!==+d.n)return;const at=syncInstant(d);s.title=d.err||"";s.textContent=d.err?`sync failed: ${d.err}`:d.missing?`0 rows refreshed: ${d.note||"#"+d.n+" is not on GitHub"}`:`${d.count||0} row${d.count===1?"":"s"} refreshed at ${at}`+((d.count||0)===0&&d.note?": "+d.note:"")};
const syncMark=(n,at,missing,note)=>{const el=document.querySelector(`#bbody .it[data-n="${n}"]`);if(!el)return;el.dataset.ri=at;el.insertAdjacentHTML("beforeend",`<span class=syncage data-ri="${esc(at)}" title="this row was read at ${esc(at)}">${esc("read "+age(at))}</span>`+(missing?`<div class=perr title="the last good row this page holds; GitHub no longer carries the card">${esc(note||"GitHub no longer carries #"+n)}</div>`:""))};
document.addEventListener("click",async e=>{const b=e.target.closest("#bbody .syncb");if(!b||b.disabled)return;e.preventDefault();e.stopPropagation();const n=+b.dataset.n,card=b.closest(".it"),label=b.textContent,say=m=>{if(card)card.insertAdjacentHTML("beforeend",m)};b.disabled=true;b.textContent="syncing…";try{const d=await syncOne(n);syncNote(d);syncSheetNote(d);if(d.err)say(`<div class=perr>sync failed: ${esc(d.err)}</div>`);else{syncApply(d);syncMark(n,d.at,d.missing,d.note);if(!card)pollBoard()}b.disabled=false;b.textContent=label}catch(x){say(`<div class=perr>sync failed: ${esc(x&&x.message?"the row could not be drawn: "+x.message:"the hub did not answer")}</div>`);b.disabled=false;b.textContent=label}});   // (#1685) a throw after the read answered is the page's, never the hub's: the message says which
$("#tksync").onclick=async()=>{const b=$("#tksync");if(b.disabled||!cur)return;const n=cur,label=b.textContent;b.disabled=true;b.textContent="syncing…";try{const d=await syncOne(n);syncNote(d);syncSheetNote(d);if(!d.err){syncApply(d);syncMark(n,d.at,d.missing,d.note)}}catch(x){const d={n,err:x&&x.message?"the row could not be drawn: "+x.message:"the hub did not answer"};syncNote(d);syncSheetNote(d)}b.disabled=false;b.textContent=label};   // the sheet's press draws the sheet and the row from that one answer (#1669)
setInterval(()=>document.querySelectorAll("#bbody .syncage").forEach(e=>{e.textContent="read "+age(e.dataset.ri)}),30000);   // the row's own read age, never the board's
// The batch move (#1098): the hub names how many cards it would move, the owner confirms that count, and one write moves them.
const batchPost=body=>fetch("/api/promote",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({batch:true,...body})}).then(r=>r.json());
document.addEventListener("click",async e=>{const b=e.target.closest("#bbody .bmv");if(!b)return;e.preventDefault();e.stopPropagation();
 const say=m=>b.insertAdjacentHTML("afterend",`<div class=perr>${esc(m)}</div>`);
 try{const plan=await batchPost({});if(plan.err)return say(plan.err);
  if(!plan.count)return say("no card is in Backlog");
  if(!confirm(`Move ${plan.count} card${plan.count===1?"":"s"} from Backlog to Todo?`))return;
  const d=await batchPost({confirm:plan.count});if(d.err)return say(d.err);
  pollBoard()}catch(x){say("the hub did not answer")}});
// Start this spec (#1128): one spec's batch from its card. The press asks the hub for the count and the numbers; the owner
// confirms that count, and one write starts the spec and its Backlog tickets (SPEC 1122). The dialog sits above the board,
// so the poll's repaint never takes it away.
const specPost=body=>fetch("/api/promote",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({batch:true,...body})}).then(r=>r.json());
const specPanel=()=>{let p=$("#spdlg");if(!p){p=document.createElement("div");p.id="spdlg";p.setAttribute("role","dialog");$("#bbody").before(p)}return p};
const specNote=m=>{let s=$("#spnote");if(!s){s=document.createElement("div");s.id="spnote";$("#bbody").before(s)}s.textContent=m};
document.addEventListener("click",async e=>{const b=e.target.closest("#bbody .spb");if(!b)return;e.preventDefault();e.stopPropagation();
 const n=+b.dataset.n,dlg=specPanel();dlg.dataset.n=n;dlg.textContent="reading the plan…";
 try{const plan=await specPost({spec:n});
  if(plan.err){dlg.innerHTML=`<div class=perr>${esc(plan.err)}</div><button class=spx type=button>Close</button>`;return}
  const k=plan.count,cards=`${k} card${k===1?"":"s"}`;
  dlg.innerHTML=`<div>Start spec #${n}: move ${cards} from Backlog to Todo: ${plan.issues.map(x=>"#"+x).join(", ")}.${plan.skipped.length?` Past Backlog, not moved: ${plan.skipped.map(x=>"#"+x).join(", ")}.`:""}</div><button class=spgo type=button data-count="${k}">Start ${cards}</button> <button class=spx type=button>Cancel</button>`;
 }catch(x){dlg.innerHTML="<div class=perr>the hub did not answer</div><button class=spx type=button>Close</button>"}});
document.addEventListener("click",async e=>{const g=e.target.closest("#spdlg .spgo");if(!g)return;e.preventDefault();
 const dlg=specPanel(),n=+dlg.dataset.n;g.disabled=true;
 try{const d=await specPost({spec:n,confirm:+g.dataset.count});
  if(d.err){dlg.innerHTML=`<div class=perr>${esc(d.err)}</div><button class=spx type=button>Close</button>`;return}
  dlg.remove();specNote(`Started spec #${n}: ${d.pending.length} card${d.pending.length===1?"":"s"} sent to Todo; the writer confirms in the background.`);pollBoard()
 }catch(x){dlg.innerHTML="<div class=perr>the hub did not answer</div><button class=spx type=button>Close</button>"}});
document.addEventListener("click",e=>{if(e.target.closest("#spdlg .spx"))$("#spdlg").remove()});

pollBoard();setInterval(pollIfVisible(pollBoard),5000);
function squadChartTick(){if(!window.squadStreamLive&&FLOWK&&!$("#flowov").hidden)flowOpen(FLOWK)}   // with no live stream the open chart is re-read on the board tick
setInterval(pollIfVisible(squadChartTick),5000);
// Last ended: the runs that ended today, read from the hub (#889)
async function endedPoll(){try{const rows=await (await fetch("/api/ended")).json();ENDED=rows;drawMain();rows.slice(0,5).map(r=>`<div class=r data-rid="${esc(r.id)}" tabindex=0 role=button><div><span class="b ${r.state}">${esc(r.badge||r.state)}</span> <b>${esc(name(r))}</b><div class=m>${usd(r.spend)} \u00b7 last write ${age(r.age)} ago</div></div></div>`).join("")||"<div class=m>no run ended today</div>"}catch(e){}}
endedPoll();setInterval(pollIfVisible(endedPoll),30000);
const themeHash=(location.hash.match(/^#set-(lim|sub|mdl|slk|prs|acc)$/)||[])[1]||(location.hash==="#set-lop"?SET_FIRST:null);if(themeHash)sTheme=themeHash;   // the phone's Settings link names the theme (#1286); an old Loop link opens the first theme left (#2515)
setTab(tabFromLocation()||(TABS[ls.tab]?ls.tab:"board"));   // the phone menu links here with #set (#913); a hash picks its tab (#1274)
// The open page follows the hub build that served it (#1114)
const SERVED_BUILD=__BUILD__;let hubNote=null;
function hubStale(msg){if(!msg){if(hubNote)hubNote.remove();hubNote=null;return}if(!hubNote){hubNote=document.createElement("div");hubNote.className="perr";document.body.append(hubNote)}hubNote.textContent=msg}
setInterval(async()=>{if(document.hidden)return;try{const r=await fetch("/api/version");if(!r.ok)throw new Error(String(r.status));const v=(await r.json()).v;hubStale("");if(v!==SERVED_BUILD)location.reload()}catch(e){hubStale("the hub did not answer: this page may be out of date")}},3000);
// A page back in front reads the two fastest polls at once and the rest on their own timers, so a return never bursts the
// hub with one read per endpoint (#2440). A Loop view that is due reads with them.
addEventListener("visibilitychange",()=>{if(document.hidden)return;poll();pollBoard();if(profDue(TAB,document.hidden,profV))profPoll()});
addEventListener("pageshow",e=>{if(e.persisted)location.reload()});
</script>"""
PROFILER_JS = (pathlib.Path(__file__).resolve().parent / "hub-profiler.js").read_text()   # the Loop view, shared with the phone (#1754)
PROFILER_CSS = (pathlib.Path(__file__).resolve().parent / "hub-profiler.css").read_text()
PAGE = PAGE.replace("/*PROFILER_JS*/", PROFILER_JS).replace("/*PROFILER_CSS*/", PROFILER_CSS)
PAGE = PAGE.replace("/*WEB_TOKENS*/", WEB_TOKENS)
SWITCHER_JS = (pathlib.Path(__file__).resolve().parent / "hub-switcher.js").read_text()   # the project switcher, both pages (#876)
PAGE = PAGE.replace("/*SWITCHER_JS*/", SWITCHER_JS)
BOARD_FILTER_JS = (pathlib.Path(__file__).resolve().parent / "board-filter.js").read_text()
PAGE = PAGE.replace("/*BOARD_LANES*/", (pathlib.Path(__file__).resolve().parent / "board-lanes.js").read_text())   # the swimlane rule (#890)
PAGE = PAGE.replace("/*BOARD_LANES_CSS*/", (pathlib.Path(__file__).resolve().parent / "board-lanes.css").read_text())   # its look (#890, #891)
PAGE = PAGE.replace("/*RUN_SHEET*/", (pathlib.Path(__file__).resolve().parent / "run-sheet.js").read_text())   # the run sheet's model (#984)
PAGE = PAGE.replace("/*RUN_SHEET_CSS*/", (pathlib.Path(__file__).resolve().parent / "run-sheet.css").read_text())   # its look (#984)
# The strip paints one rule per category, keyed by the fact that sets the column: the name comes from the table.
HID_CHIP_TOKENS = (("abandoned", "err"), ("unscheduled", "warn"), ("issue_closed", "ok"), ("pr_open", "pin"))
PAGE = PAGE.replace("/*HID_CHIP_RULES*/", "\n".join('.hidbar .chip[data-col="%s"] b{color:var(--%s)}' % (COLUMN[fact], token) for fact, token in HID_CHIP_TOKENS))
PAGE = PAGE.replace("/*BOARD_COLORS*/", BOARD_COLORS_JSON).replace("/*BOARD_FLOW*/", BOARD_FLOW_JSON).replace("/*BOARD_NAMES*/", BOARD_NAMES_JSON).replace("/*BOARD_DOING*/", BOARD_DOING_JSON).replace("/*BOARD_REVIEW*/", BOARD_REVIEW_JSON).replace("/*HUB_REPO*/", json.dumps(REPO))
PAGE = PAGE.replace("/*RUN_STATE_BASE_CSS*/", RUN_STATE_BASE_CSS).replace("/*RUN_STATE_CSS*/", "".join(run_state_css(r) for r in RUN_STATES))
# The lead chart's clusters take their fixed radius from the payload; the page draws one dot per cluster and no count (#1331).
PAGE = PAGE.replace("/*LT_START*/", LEAD_START)

