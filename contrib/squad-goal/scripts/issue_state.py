#!/usr/bin/env python3
"""One seam reads and writes the state an issue carries (#236, ADR-0141 clause 4).

A **state** is one name of the flow's vocabulary -- `Todo`, `In Progress`, `Code Complete` -- and never a
storage detail. A **binding** is the carrier a repository works that vocabulary in. Today this repository
binds it to a GitHub Project (v2) Status field, and `BoardBinding` here is that carrier's one client: it
holds every reach the board API takes for state, so no caller needs one of its own.

The seam answers two questions and only these:

    from issue_state import board_state
    state = board_state()               # the binding this repository is configured with (SG_OWNER, SG_PROJECT, SG_REPO)
    state.read(236)                     # the state issue 236 carries, "" when it carries none
    state.read_all()                    # every card, one read: {236: "Todo", ...}
    state.write(236, "Code Review")         # proven by the read that follows; returns the state the issue then carries
    state.holder(236)                   # the run that holds issue 236: {"run": id, "started": time}, None when none does
    state.holders()                     # every holder, one read: {"236": {"run": ..., "started": ...}}
    state.board()                       # every state and holder, one read: ({236: "Todo"}, {"236": {"run": ..., "started": ...}})
    state.hold(236, run, started)       # name the run on the issue, proven by the read that follows
    state.release(236)                  # clear the holder when the run ends, proven by the read that follows
    state.kind(243)                     # the repository's own kind the item carries: bug, task, spec, test case; "" when none
    state.kinds()                       # every kind, one read: {"243": "bug", ...}
    state.set_kind(243, "bug")          # proven by the read that follows; a kind the binding cannot carry is a `KindGap`
    state.kind_gaps()                   # the kinds of the vocabulary the binding cannot carry, reported by name
    state.record_pass(300, "pass", sha) # a finished test pass: one `test case` item (issue, result, merge commit, end time), proven by the read that follows
    state.test_cases()                  # every test case item, one read: [{"issue", "result", "commit", "ended", "age"}]

**A write is proven by the read that follows it.** The answer to the write is no proof -- a field write can
be answered ok and never land (#105) -- so `write` reads the issue back and compares. A write the read does
not carry raises `NotCarried`, which names the issue, the state wanted and the state carried: the caller
reports it, and a write no read carries is never cached as one. The retry is the caller's policy, not the
seam's: board.py writes again, up to its own bound.

The seam is also recorded, because the binding is a decision a run makes once: `squad.engine` records the
binding's `name` at run start and a resume that finds a different name refuses (ADR-0107).

Shell callers reach the same seam through this module's door:

    issue_state.py [--repo R]              print one `<issue>\t<state>` a line, the state the carrier answers
    issue_state.py --go <n,n,…> [--label L]  the rows a dispatch pass reads, and the issues it names go-labelled whose
                                          card the board does not carry: `UNREAD<TAB>n,n` and one `NOTE<TAB>n<TAB>why` a
                                          line for one the issue itself says is closed or no longer carries L (#800, #1031)
    issue_state.py --state <issue>         print the state one issue carries
    issue_state.py --kinds                 print one `<issue>\t<kind>` a line, the kind the carrier answers
    issue_state.py --kind-gaps             print one kind a line, each the binding cannot carry

A **kind** is the repository's own normalized name for what an item is, and never the platform's issue type: a
caller names `bug`, and only the binding knows which field and option carry it (#243).
"""
import json, os, pathlib, re, subprocess, sys, time, urllib.parse

_VALUE_FLAGS = {"-X", "--method", "-f", "-F", "--raw-field", "--field", "--input", "--jq", "-q", "-H", "--header"}
def _is_write(args):
    """A call that changes GitHub: `-X`/`--method` with a verb other than GET. Its one line is the `done` outcome (ADR-0277)."""
    it = iter(args)
    for x in it:
        if x in ("-X", "--method"): return next(it, "GET").upper() != "GET"
    return False

def _log_call(args, outcome):
    """One api.log line for a `gh api` call this seam makes (#847). The board pass is the largest REST reader the loop has,
    and it was the one that spent the quota unlogged; the line is the door's own, so the spend reads the same everywhere.
    The line goes to $SG_STATE, the loop's one call log, even from a seam built with its own state dir (the hub's, #1328):
    that seam still spends the loop's quota. Outside a loop (SG_STATE unset) a call writes nothing."""
    state = os.environ.get("SG_STATE")
    if not state: return
    it = iter(args); route = ""
    for x in it:
        if x in _VALUE_FLAGS: next(it, None); continue
        if not x.startswith("-"): route = x; break
    if not route: return
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path: sys.path.insert(0, here)
    try: import ghread
    except ImportError: return
    cache = ghread.WRITE if _is_write(args) else ghread.MISS   # a write is never a snapshot miss, whatever its outcome (#1328)
    ghread.log_call(state, route, cache, outcome, transport="GraphQL" if route == "graphql" else "REST")

# The binding a repository binds the vocabulary to. A carrier is named here, and a run records the name.
BOARD = "board"

# The kinds an item carries, as the repository names them. A binding that cannot carry one reports it as a gap.
KINDS = ("bug", "task", "spec", "test case")

# The two text fields a run is named in. They are the binding's, not the shared vocabulary's, so the board definition
# does not name them and board_define.py creates them from here (#487).
HOLDER_FIELDS = ("Run", "Run started")

# The number field the gate-attempt journal projects to (#1255, ADR-0170): one count per card.
GATE_TRIES = "Gate Tries"
PRIORITY = "Priority"


class NotCarried(RuntimeError):
    """The write is not carried by the read that follows it: the named failure, never a written card.

    `refused` holds the carrier's own words when it refused the write, because a refusal is no proof
    either way: a write the carrier refuses can still be a state the issue carries (#105, #101), and only
    the read tells the two apart."""

    def __init__(self, issue, wanted, carried, refused=""):
        self.issue, self.wanted, self.carried, self.refused = issue, wanted, carried, refused
        super().__init__(f"issue {issue} does not carry {wanted!r} after the write; it carries {carried!r}" + (f" ({refused})" if refused else ""))


class KindGap(RuntimeError):
    """The binding cannot carry the kind: a reported gap, raised before any write, never a dropped kind."""

    def __init__(self, issue, wanted, carried):
        self.issue, self.wanted, self.carried = issue, wanted, carried
        super().__init__(f"the binding cannot carry kind {wanted!r} for issue {issue}; it carries {', '.join(carried) or 'no kind'}")


class ReadFailure(RuntimeError):
    """A card the board answered "already there" for, which no read then carried: the read is behind the
    write, not the card gone."""

    def __init__(self, reads, count): super().__init__(f"the board read never carried it after {reads} reads ({count} items read); a read failure, not an absence")


RESULTS = ("pass", "fail")   # how a test pass ends; a failed pass is never overwritten by a green one (#300)
TEST_CASE_TITLE = re.compile(r"^test case #(\d+) \| (pass|fail) \| (\S+) \| (\S+)$")

# The one-issue read the REST items route is answered with when it omits a known issue (#1463): the route is
# eventually consistent and sometimes never carries a card GraphQL names, so an omission is asked once of
# the projectItems read before it is called an absence.
FALLBACK_QUERY = ("query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){"
                  "issue(number:$number){projectItems(first:50){nodes{databaseId isArchived project{number} "
                  "fieldValueByName(name:\"Status\"){... on ProjectV2ItemFieldSingleSelectValue{name optionId}}}}}}}")


def _utc(stamp):
    import datetime
    return datetime.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)


def _now():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class State:
    """The seam: what state issue N carries, and a write proven by the read that follows it."""

    def __init__(self, binding):
        self.binding = binding
        self.unanswered = set()   # the go issues whose own read did not answer in the last naming pass

    @property
    def name(self):
        return self.binding.name

    def read(self, issue):
        """The state of one issue, "" when the carrier carries none. A card the whole-board read omits is asked of the
        GraphQL projectItems read once (#1463), so the dispatch path sees a card the items route never carried."""
        carried = self.binding.read(issue)
        if carried: return carried
        fb = self.read_fallback(issue)
        return fb["state"] if fb else ""

    def read_all(self, fresh=False):
        """Every state the carrier answers, in one read: {issue: state}.

        One read for the whole board, never one per issue: a pass that asks about many cards asks once.
        `fresh` asks for a live read, for a caller that gates a write (#1796).
        """
        return self.binding.read_all(fresh=fresh)

    def read_one(self, issue):
        """The state of one card, read by its item's node id in one call (#1300): the compare and the proof of a single move
        cost no whole-board read. The whole-board read answers only when the node read does not (no id is known for the card,
        or the read is refused), and the GraphQL projectItems read answers only when both REST reads omit the card (#1463):
        the items route sometimes never carries a card it names, and an omission is a read failure, never an absence.
        "" when the card carries no Status."""
        node = getattr(self.binding, "node_state", None)   # a binding with no node read answers through its whole read
        got = node(issue) if node else None
        if got is not None: return got
        return self.read(issue)

    def read_fallback(self, issue):
        """The card one issue carries, asked of the GraphQL projectItems read when the REST items read omits an
        issue the issue list names (#1463): one call per issue per binding, {"id", "state"} or None when the read
        refuses or carries no card of this project. A binding with no fallback answers None, as it did."""
        ask = getattr(self.binding, "read_fallback", None)
        return ask(issue) if ask else None

    def rows(self, jq, **kw):
        """Every card in the same one read as `read_all`, projected by the caller's jq: the hub's pane draws titles
        and labels beside the state, and asks for them here rather than at the carrier."""
        return self.binding.rows(jq, **kw)

    def unread(self, go, label=""):
        """The go-labelled issues `go` names that the board read does not carry (#800). One board read; a missing card is
        asked by its known node id, and a node that answers a Status rescues it. Returns (carried, rescued, missing):
        carried is the read {issue: state} with the issues the issue itself denies dropped, rescued {issue: state} the
        node reads, missing the issues nothing reads.

        Every issue `go` names is asked for itself before either its carried row or its node is accepted (#1031): the
        list that named it was read at another time, and the rows it is checked against come from a cache shared by the
        owner and the project, so the issue is the one source that settles whether it still is go-labelled."""
        return self.unread_rows(self.read_all(), go, label)

    def unread_rows(self, carried, go, label=""):
        """`go` against rows the caller already holds (the hub board cache, #800): the naming rule of `unread`, and no
        board read. Each issue is asked for itself before its carried row or its node is accepted: one the issue says is
        closed, or no longer carries `label`, is said once as a NOTE and dropped from the effective go candidates, so
        closing an issue takes it out of the go set on the next cycle. A read that does not answer keeps the row or the
        node's answer, and an issue neither one carries stays in `missing`, so a card the board really cannot read still
        alerts."""
        rescued, missing = {}, []
        effective = dict(carried)
        for n in sorted({str(x) for x in go}, key=int):
            why = self.not_go(n, label)
            if why:
                print(f"NOTE\t{n}\t{why}")
                effective.pop(n, None)   # the rows are an earlier read: the issue itself is what drops it
                continue
            if n in effective: continue
            ask = getattr(self.binding, "node_status", None)
            st = ask(n) if ask else None
            if st: rescued[n] = st; continue
            missing.append(n)
        return effective, rescued, missing

    def not_go(self, issue, label=""):
        """Why the issue itself says it is not go-labelled: `closed`, or `no <label> label`. Empty when it is go-labelled,
        or when the read does not answer -- a premise a failed read never settles one way or the other (#1031, ADR-0155)."""
        facts = getattr(self.binding, "issue_facts", None)
        f = facts(issue) if facts else None
        if f is None:
            self.unanswered.add(str(issue))
            return ""
        state, labels = f
        if state and state != "open": return "closed"
        if label and label not in labels: return f"no {label} label"
        return ""

    def write(self, issue, wanted):
        """Write `wanted` on `issue`, then read it back. The read is the proof, and neither the answer nor
        the refusal is: a write the carrier refuses can be a state the issue already carries (#105, #101),
        and a write answered ok can be one it never carries. The state the issue carries after the write is
        the verdict -- a state other than the one wanted is `NotCarried`, named, and it is never reported
        as written. Returns the state the issue carries."""
        refused = ""
        try: self.binding.write(issue, wanted)
        except RuntimeError as e:
            refused = str(e)   # the refusal is kept, not raised: the read then confirms it or disproves it
            self.binding.item_hints.pop(str(issue), None)   # an id the hub named and the write refused is not trusted again (#1300)
        carried = self.read_one(issue)
        if carried != wanted:
            raise NotCarried(issue, wanted, carried, refused)
        return carried

    def holders(self):
        """Every issue a run holds, in one read: {issue: {"run": id, "started": time}}. An issue no run holds is absent."""
        return self.binding.holders()

    def board(self, fresh=False):
        """Every state and every holder the carrier answers, in one read: ({issue: state}, {issue: {"run", "started"}}).
        `fresh` asks for a live read, for a caller that gates a write (#1796)."""
        return self.binding.board(fresh=fresh)

    def holder(self, issue):
        """The run that holds `issue`, {"run": id, "started": time}; None when no run holds it."""
        return self.holders().get(str(issue))

    def holder_one(self, issue):
        """The holder one card carries, read by its item in one call (#1668). None when the binding has no one-card read,
        no id is known, or the read failed, so a caller keeps the holder it held; {} when the card carries no Run."""
        ask = getattr(self.binding, "node_holder", None)
        return ask(issue) if ask else None

    def hold(self, issue, run, started):
        """Name the run that holds `issue` on the issue, then read it back: the read is the proof, as for a state.
        An empty value is written as no value (#101). Returns the holder the issue then carries."""
        wanted = {"run": run, "started": started or ""} if run else None
        return self._write_holder(issue, run or None, started or None, wanted)

    def release(self, issue):
        """Clear the holder when the run ends, then read it back: a holder the next read still carries is
        `NotCarried`, never a clear. Returns None, the holder the issue then carries."""
        return self._write_holder(issue, None, None, None)

    def set_gate_tries(self, issue, count):
        """Raise the card's Gate Tries to `count`, then prove it by the read (ADR-0170). The field only rises:
        a smaller `count` -- a rearm, or a journal shorter than the field -- is a floor and moves nothing
        (ADR-0149). An unchanged value sends no write (ADR-0144). A crash between the journal and the field
        replays to one count, because `count` is always the journal's own total. Returns the value the card carries."""
        current = self.binding.gate_tries(issue)
        want = max(int(count), current)
        if want == current: return current
        self.binding.write_gate_tries(issue, want)
        def carried():
            try: return self.binding.gate_tries(issue)
            except (RuntimeError, ValueError): return None
        if carried() == want: return want
        if not self.binding.settle(lambda: want if carried() == want else None, self.binding.attempts):
            raise NotCarried(issue, want, carried())
        return want

    def kinds(self):
        """Every kind the carrier answers, in one read: {issue: kind}. An item with no kind is absent."""
        return self.binding.kinds()

    def kind(self, issue):
        """The kind one item carries, "" when it carries none."""
        return self.kinds().get(str(issue), "")

    def kind_gaps(self):
        """The kinds of the vocabulary the binding cannot carry, in the vocabulary's order."""
        carried = self.binding.kind_options()
        return [k for k in KINDS if k not in carried]

    def set_kind(self, issue, wanted):
        """Write `wanted` on `issue`, then read it back, as for a state. A kind the binding cannot carry is a `KindGap`
        and nothing is written; a write no read carries is `NotCarried`. Returns the kind the item then carries."""
        refused = ""
        try: self.binding.write_kind(issue, wanted)
        except KindGap: raise
        except RuntimeError as e: refused = str(e)
        carried = self.kind(issue)
        if carried != wanted:
            raise NotCarried(issue, wanted, carried, refused)
        return carried

    def test_cases(self, now=None):
        """Every test case item, in one read: [{"issue", "result", "commit", "ended", "age"}], the age in seconds
        at `now` (a UTC stamp; the clock when none is given). An item whose title is not a pass is not one."""
        now = _utc(now or _now())
        out = []
        for _, title, kind in self.binding.test_case_rows():
            m = TEST_CASE_TITLE.match(title)
            if m and kind == "test case":
                out.append({"issue": int(m[1]), "result": m[2], "commit": m[3], "ended": m[4], "age": int((now - _utc(m[4])).total_seconds())})
        return out

    def record_pass(self, issue, result, commit, ended=None):
        """Record a finished test pass as one `test case` item: the issue it tested, the result (`pass` or `fail`),
        the merge commit the pass ran on and when it ended. The item is proven by the read that follows the write:
        the items route is eventually consistent, so that read is bounded and retried, and an item no read carries
        is `NotCarried`, named, never reported as written. A pass that ended failed stays failed: a later pass on the
        same merge commit is answered with the failure and writes nothing; a pass on a new commit is its own item.
        A binding that cannot carry the kind is a `KindGap`, raised before any write. Returns the item the read carries."""
        if result not in RESULTS: raise ValueError(f"a pass ends {' or '.join(RESULTS)}, not {result!r}")
        if not commit or not commit.strip(): raise ValueError("a test case names the merge commit the pass ran on")
        ended = ended or _now()
        _utc(ended)
        if "test case" not in self.binding.kind_options(): raise KindGap(issue, "test case", sorted(self.binding.kind_options()))
        held = [r for r in self.test_cases() if r["issue"] == int(issue) and r["commit"] == commit]
        failed = next((r for r in held if r["result"] == "fail"), None)
        if failed: return failed   # failed stays failed: a later pass does not overwrite it, and a second item would say the same twice
        if result == "pass" and held: return held[0]
        title = f"test case #{int(issue)} | {result} | {commit} | {ended}"
        refused = ""
        try: self.binding.write_test_case(title)
        except RuntimeError as e: refused = str(e)
        for k in range(self.binding.attempts):   # the proving read is bounded: the item is waited for, never written a second time
            if k and self.binding.backoff: time.sleep(self.binding.backoff * (2 ** (k - 1)))
            carried = next((r for r in self.test_cases() if (r["issue"], r["result"], r["commit"], r["ended"]) == (int(issue), result, commit, ended)), None)
            if carried: return carried
        raise NotCarried(issue, title, "no test case item", refused)

    def _write_holder(self, issue, run, started, wanted):
        refused = ""
        try: self.binding.write_holder(issue, run, started)
        except RuntimeError as e: refused = str(e)
        carried = self.holder(issue)
        if carried != wanted:
            raise NotCarried(issue, wanted, carried, refused)
        return carried


class BoardBinding:
    """The GitHub Project (v2) carrier, behind the seam: the one client of the board API's state.

    Field and option ids are cached a day, the issue -> item id map 10 min (refetched on a miss), under
    $SG_STATE. A miss on the item map re-reads the items with a bounded backoff (#96): the project-items
    route is eventually consistent, so a card written a moment ago is not always in the next read. A card
    the board answered "already exists" for and no read then carried is a `ReadFailure`, named by name,
    never "not on the board".
    """

    name = BOARD

    def __init__(self, owner, project, repo="", *, kind="users", state_dir=None, run=subprocess.run, env=os.environ, timeout=None):
        self.owner, self.project, self.repo = owner, str(project), repo
        self._kind = kind   # the loop's owners are users; an owner the caller does not know is resolved once, cached a month
        self.run, self.env = run, env
        self.timeout = timeout   # a call has a deadline (ADR-0051); a caller that is cut off passes its own
        self.st = pathlib.Path(state_dir or env["SG_STATE"])
        self.st.mkdir(parents=True, exist_ok=True)
        self.attempts = max(1, int(self._num("SG_BOARD_READ_ATTEMPTS", "4")))   # bounded: the items route is eventually consistent (#96)
        self.backoff = max(0.0, self._num("SG_BOARD_READ_BACKOFF", "1"))        # the base delay, doubled per retry; 0 sleeps for no read
        self.settle_seconds = max(0.0, self._num("SG_BOARD_SETTLE_SECONDS", "60"))   # the sleep budget for a new card to reach the read (#403)
        self.server_error = re.compile(r"\bHTTP 5\d\d\b")
        self.items_map = None
        self._fallback = {}   # issue -> the card the GraphQL fallback read (or None), once per issue per binding (#1463)
        self.item_hints = _item_hints(env.get("SG_BOARD_ITEM_HINTS"))   # the item ids the caller holds (#1300)

    def _num(self, name, default):   # a bad value in the environment must not kill a card move
        try: return float(self.env.get(name, default))
        except ValueError: return float(default)

    def settle(self, probe, reads, cap=None):
        """A new card is eventually consistent (#403): an add or a write GitHub accepted can be missing from reads for
        longer than the bounded attempts. After `reads` reads that missed, read again with the doubling backoff (each
        delay at most 16 s) while the sleeps fit in SG_BOARD_SETTLE_SECONDS. Returns the first truthy `probe()`, else
        None. A backoff of 0 sleeps for no read, so it reads no more."""
        slept, k = 0.0, max(1, reads)
        bound = self.settle_seconds if cap is None else min(self.settle_seconds, cap)   # `cap` lowers the bound for one settle (#438)
        while self.backoff:
            d = min(self.backoff * (2 ** (k - 1)), 16.0)
            if slept + d > bound: return None
            time.sleep(d); slept += d; k += 1
            got = probe()
            if got: return got
        return None

    @property
    def kind(self):
        """`users` or `orgs`: the endpoint prefix an owner is read at. A caller that knows its owner's
        kind names it and costs the pass no call; `kind=None` asks the carrier once and caches it a month."""
        if self._kind: return self._kind
        return self._cached(f"owner-kind-{self.owner}.json", 30 * 86400,
                            lambda: "orgs" if json.loads(self.api(f"users/{self.owner}")).get("type") == "Organization" else "users")

    @property
    def base(self):
        return f"/{self.kind}/{self.owner}/projectsV2/{self.project}"

    def api(self, *a, inp=None, timeout=None):
        """One gh api call, the binding's only call site. A server error (HTTP 5xx) is GitHub failing to
        answer, not a verdict on the request: it is asked again with the same bounded backoff, and only an
        error that outlasts the attempts is raised (#226). Any other error is GitHub's verdict, raised at once."""
        err = ""
        for k in range(self.attempts):
            if k and self.backoff: time.sleep(self.backoff * (2 ** (k - 1)))
            r = self.run(["gh", "api", *a], input=inp, capture_output=True, text=True, timeout=self.timeout or timeout)
            if not r.returncode: _log_call(a, "done" if _is_write(a) else "answer"); return r.stdout
            _log_call(a, "failed")   # each attempt is spent quota, so each is one line (ADR-0277)
            err = r.stderr.strip()[:200]
            if not self.server_error.search(err): break
        raise RuntimeError(err)

    def _cached(self, name, ttl, fn):
        f = self.st / name
        try:
            if time.time() - f.stat().st_mtime < ttl: return json.loads(f.read_text())
        except (OSError, ValueError): pass
        d = fn(); f.write_text(json.dumps(d)); return d

    def status_field(self):
        """The Status field: `{"id", "opt"}` -- the field's id, and the option ids a writer sends. Cached a
        day; a carrier that renames or reorders an option is re-read on the same miss path. A carrier that
        answers the field's id alone (the reader's own lookup) is read as a field with no options, and the
        reader that maps no name keeps working."""
        def read_field():
            raw = self.api(f"{self.base}/fields?per_page=100")
            try: fs = json.loads(raw)
            except ValueError: fs = None
            if not isinstance(fs, list): return {"id": raw.strip(), "opt": {}}   # the carrier answered the field's id alone, not the fields
            s = next((x for x in fs if isinstance(x, dict) and x.get("name") == "Status"), None)
            if not s: return {"id": "", "opt": {}}
            return {"id": s["id"], "opt": {o["name"]["raw"] if isinstance(o["name"], dict) else o["name"]: o["id"] for o in s.get("options", [])}}
        return self._cached(f"rest-fields-{self.owner}-{self.project}.json", 86400, read_field)

    def text_fields(self):
        """The two text fields a run is named in: {name: id}."""
        def read_fields():
            fs = json.loads(self.api(f"{self.base}/fields?per_page=100"))
            return {x.get("name"): x.get("id") for x in fs if x.get("name") in HOLDER_FIELDS}
        return self._cached(f"rest-text-fields-{self.owner}-{self.project}.json", 86400, read_fields)

    def number_field(self):
        """The Gate Tries number field: `{"id"}`, the field's id, empty when the board carries none (#1255). Cached a day."""
        def read_field():
            fs = json.loads(self.api(f"{self.base}/fields?per_page=100"))
            f = next((x for x in fs if isinstance(x, dict) and x.get("name") == GATE_TRIES), None) if isinstance(fs, list) else None
            return {"id": f["id"] if f else ""}
        return self._cached(f"rest-number-field-{self.owner}-{self.project}.json", 86400, read_field)

    def gate_tries(self, issue):
        """The value the card carries in Gate Tries, 0 when the card, the field or a value is absent. One items read."""
        fid = self.number_field()["id"]
        if not fid: return 0
        out = self.api("--paginate", f"{self.base}/items?per_page=100&fields[]={fid}", "--jq",
                       '.[]|select(.content.number==%d)|(((.fields[]?|select(.name==%s)|.value))//0)' % (int(issue), json.dumps(GATE_TRIES)))
        for line in out.splitlines():
            try: return max(0, int(float(line.strip())))
            except ValueError: continue
        return 0

    def priority_field(self):
        """The Priority single-select field: `{"id"}`, the field's id, empty when the board carries none (#1977).
        Only a found id is cached a day, so a field added to the board is read at the next call."""
        def read_field():
            fs = json.loads(self.api(f"{self.base}/fields?per_page=100"))
            f = next((x for x in fs if isinstance(x, dict) and x.get("name") == PRIORITY), None) if isinstance(fs, list) else None
            if f and f.get("dataType") != "SINGLE_SELECT": raise RuntimeError(f"{PRIORITY!r} field is not a single select")
            return {"id": f["id"] if f else ""}
        cache = self.st / f"rest-priority-field-{self.owner}-{self.project}.json"
        try:
            if time.time() - cache.stat().st_mtime < 86400:
                d = json.loads(cache.read_text())
                if d.get("id"): return d
        except (OSError, ValueError): pass
        d = read_field()
        if d["id"]: cache.write_text(json.dumps(d))
        return d

    def read_priorities(self):
        """{issue: Priority name} for each card that carries one, in ONE items read (#1977). A card with no value is absent.
        A board with no Priority field raises, so the caller holds the cards it would have ranked."""
        fid = self.priority_field()["id"]
        if not fid: raise RuntimeError(f"no {PRIORITY!r} field on the board")
        out = self.api("--paginate", f"{self.base}/items?per_page=100&fields[]={fid}", "--jq",
                       '.[]|select(.content.number)|[.content.number,(((.fields[]?|select(.name==%s)|.value.name|if type=="object" then .raw else . end))//""),'
                       '(.content.repository_url//"")]|@tsv' % json.dumps(PRIORITY))
        m = {}
        for l in out.splitlines():
            n, p, u = (l.split("\t") + ["", "", ""])[:3]
            n = n.strip()
            if n.isdigit() and p and (not self.repo or not u or u.endswith("/" + self.repo)): m[n] = p
        return m

    def write_gate_tries(self, issue, count):
        """One PATCH of the Gate Tries number. The read that proves it is the seam's (ADR-0144, ADR-0170)."""
        iid = self.item_id(issue)
        if not iid: raise RuntimeError(f"#{issue} not on the board")
        fid = self.number_field()["id"]
        if not fid:
            (self.st / f"rest-number-field-{self.owner}-{self.project}.json").unlink(missing_ok=True)
            fid = self.number_field()["id"]
        if not fid: raise RuntimeError(f"no number field {GATE_TRIES!r}")
        try:
            self.api("-X", "PATCH", f"{self.base}/items/{iid}", "--input", "-",
                     inp=json.dumps({"fields": [{"id": fid, "value": int(count)}]}))
        except RuntimeError:
            # A refused PATCH may name a stale id: a field renamed or recreated within the cache's day is
            # dropped from the cache, so the next call re-reads it and never PATCHes the dead id again.
            (self.st / f"rest-number-field-{self.owner}-{self.project}.json").unlink(missing_ok=True)
            raise

    def items(self):
        """The issue -> item id map, REST only: the items route is a bare array with no total, so name the
        count read and its source, never a number not read (#96)."""
        out = self.api("--paginate", f"{self.base}/items?per_page=100", "--jq",
                       '.[]|select(.content.number)|[.content.number,.id,(.content.repository_url//"")]|@json')
        m = {str(n): i for n, i, ru in (json.loads(l) for l in out.splitlines() if l.strip()) if not self.repo or ru.endswith("/" + self.repo)}
        print(f"issue_state.py: items read {len(m)}; project total: none on the REST items route (source: {self.base}/items, a bare array)", file=sys.stderr)
        return m

    @property
    def _items_file(self):
        """The item map's cache file. The map is filtered to the binding's repository, so the file is named by it too: two
        repositories on one board never read each other's map (#770)."""
        tag = "-" + self.repo.replace("/", "_") if self.repo else ""
        return f"rest-items-{self.owner}-{self.project}{tag}.json"

    def _items(self):
        if self.items_map is None:
            self.items_map = self._cached(self._items_file, 600, self.items)
        return self.items_map

    @property
    def _known_file(self):
        """Every issue -> node id this binding has ever read, kept across reads and never pruned: the node id is the one
        key a missing card still answers by (#800). A read that drops a card does not drop its id."""
        tag = "-" + self.repo.replace("/", "_") if self.repo else ""
        return f"rest-known-{self.owner}-{self.project}{tag}.json"

    def known_ids(self):
        try: return json.loads((self.st / self._known_file).read_text())
        except (OSError, ValueError): return {}

    def _save_items(self):
        (self.st / self._items_file).write_text(json.dumps(self.items_map))
        known = self.known_ids(); known.update(self.items_map)
        (self.st / self._known_file).write_text(json.dumps(known))

    def node_status(self, issue):
        """The Status of the card a known node id names, read by the one-item REST route (#1300, #1303). None when no id is
        known for the issue, or the read is refused, unparsable, archived, or names another issue, or the card carries no
        Status; the caller then falls back to the whole-board read."""
        return self.node_state(issue) or None

    def item_id(self, n, add_missing=False):
        """The one reader every caller uses (move, --add, --run-fields, --clear-run). A miss re-reads the
        items a bounded number of times with a growing delay: the items route is eventually consistent, so
        a card written a moment ago is not always in the next read (#96). None means a card no read ever
        carried and nothing was written for it here."""
        key = str(n)
        if self.item_hints.get(key): return self.item_hints[key]   # the caller's id: no read of the items for it (#1300)
        if key not in self._items(): self.items_map = self.items(); self._save_items()
        if key in self.items_map: return self.items_map[key]
        if add_missing and not self.repo:   # an add with no repository wrote nothing, and "not on the board" then named a write that never ran (#1315)
            raise RuntimeError(f"#{n}: the add needs the repository, and SG_REPO is not set")
        wrote = False   # an add that landed, or a refusal that proves the card is on the board: the write is done, the read is behind
        if add_missing and self.repo:
            iid = json.loads(self.api(f"repos/{self.repo}/issues/{n}"))["id"]
            try: self.items_map[key] = json.loads(self.api("-X", "POST", f"{self.base}/items", "--input", "-", inp=json.dumps({"type": "Issue", "id": iid})))["id"]; self._save_items(); return self.items_map[key]
            except RuntimeError as e:
                if "already exists" not in str(e):   # the add itself was refused, not the read: re-read once to repair a stale cache, and only then is the refusal the add's
                    self.items_map = self.items(); self._save_items()
                    if key in self.items_map: return self.items_map[key]
                    raise
                wrote = True   # the refusal proves the card is there: only the read is behind it
        for k in range(self.attempts):
            if k and self.backoff: time.sleep(self.backoff * (2 ** (k - 1)))
            self.items_map = self.items(); self._save_items()
            if key in self.items_map: return self.items_map[key]
        reads = [self.attempts]
        if wrote:   # the card is on the board and the read lags: a new card, read within the settle bound before the failure (#403)
            def probe():
                reads[0] += 1; self.items_map = self.items(); self._save_items()
                return self.items_map.get(key)
            iid = self.settle(probe, self.attempts)
            if iid: return iid
        fb = self.read_fallback(n)   # REST never carried it: the one-issue GraphQL read names the card the route omitted (#1463)
        if fb and fb.get("id"):
            self.items_map[key] = fb["id"]; self._save_items()
            return fb["id"]
        if wrote: raise ReadFailure(reads[0], len(self.items_map))
        return None   # a read that never carried it, and nothing was written for it here: genuinely absent

    def node_state(self, issue):
        """The Status one card carries, read by the one-item REST route in one call (#1300, #1303). None when no id is known
        for the card, or the read is refused, unparsable or names another issue; "" when the card carries no Status. The
        loop reads GitHub over REST only: the GraphQL quota is shared with every `gh` subcommand.

        The Status field is asked for by id, as the paginated read asks it: a single-select value rides in the answer only
        when its field is named, and without `fields[]` the route answers the title alone (#1305)."""
        iid = self.item_hints.get(str(issue)) or self.known_ids().get(str(issue))
        if not iid: return None
        try: fid = self.status_field().get("id") or ""
        except Exception: fid = ""   # a carrier that cannot name the field still gets the read; it answers no Status
        q = f"?fields[]={fid}" if fid else ""
        try: d = json.loads(self.api(f"{self.base}/items/{iid}{q}"))
        except (RuntimeError, ValueError): return None
        if not isinstance(d, dict) or str((d.get("content") or {}).get("number", "")) != str(issue) or d.get("isArchived"): return None
        for f in d.get("fields") or []:
            if not isinstance(f, dict) or f.get("name") != "Status": continue
            v = f.get("value")
            name = v.get("name") if isinstance(v, dict) else v       # the route answers {name:{raw,html}}; a stub a name
            if isinstance(name, dict): name = name.get("raw") or name.get("html")
            return self.state_name(name) if name else ""
        return ""

    def node_holder(self, issue):
        """The holder one card carries, read by the one-item REST route in one call (#1668), beside the Status that route
        already answers. None when no id is known for the card, or the read is refused, unparsable, archived or names
        another issue, so the caller keeps the holder it held; {} when the card carries no Run."""
        iid = self.item_hints.get(str(issue)) or self.known_ids().get(str(issue))
        if not iid: return None
        try: tf = self.text_fields()
        except Exception: return None
        if "Run" not in tf or "Run started" not in tf: return {}
        q = f"?fields[]={tf['Run']}&fields[]={tf['Run started']}"
        try: d = json.loads(self.api(f"{self.base}/items/{iid}{q}"))
        except (RuntimeError, ValueError): return None
        if not isinstance(d, dict) or str((d.get("content") or {}).get("number", "")) != str(issue) or d.get("isArchived"): return None
        out = {}
        for f in d.get("fields") or []:
            if not isinstance(f, dict): continue
            v = f.get("value")
            val = (v.get("raw") or v.get("text")) if isinstance(v, dict) else v   # the route answers {raw,html}; a stub a name
            if f.get("name") == "Run": out["run"] = val or ""
            elif f.get("name") == "Run started": out["started"] = val or ""
        return out if out.get("run") else {}

    def read_fallback(self, issue):
        """The card of one issue the REST items read omitted, asked of the GraphQL projectItems read (#1463): one
        call per issue per binding -- the route is eventually consistent and sometimes never carries a card it
        names, and an omission is a read failure, never an absence. Answers {"id", "state"} for the unarchived
        card of this project ("state" is "" when the card carries no Status), or None when the read refuses,
        the issue has no card here, or no repository is bound. The query names the binding's own repository,
        so the answer needs no repository filter; a refused or malformed answer is no card, never a guess."""
        key = str(issue)
        if key in self._fallback: return self._fallback[key]
        ans = None
        if self.repo and "/" in self.repo:
            r_owner, _, r_name = self.repo.partition("/")
            try:
                d = json.loads(self.api("graphql", "-f", f"query={FALLBACK_QUERY}",
                                        "-F", f"owner={r_owner}", "-F", f"name={r_name}", "-F", f"number={int(issue)}"))
            except (RuntimeError, ValueError):
                d = None
            nodes = (((d or {}).get("data") or {}).get("repository") or {}).get("issue") or {}
            nodes = (nodes.get("projectItems") or {}).get("nodes")
            try: project = int(self.project)
            except ValueError: project = None
            if isinstance(nodes, list) and project is not None:
                for nd in nodes:
                    if not isinstance(nd, dict) or nd.get("isArchived"): continue
                    if (nd.get("project") or {}).get("number") != project: continue
                    fv = nd.get("fieldValueByName")
                    carried = ""
                    if isinstance(fv, dict): carried = fv.get("optionId") or fv.get("name") or ""
                    rid = nd.get("databaseId")   # the REST items route's id: the integer the status route takes, never the node id
                    ans = {"id": rid if isinstance(rid, int) else "", "state": self.state_name(carried)}
                    break
        self._fallback[key] = ans
        return ans

    def read_all(self, fresh=False):
        """Every card the carrier answers, in ONE read: {issue: state}, a card with no Status absent.

        The Status field is asked for by id -- a single-select value is carried only when the field is
        asked for, the way janitor.py reads it, and without `fields[]` the route answers the title alone.
        A row's repository url filters the map to this binding's repository, and a carrier that answers no
        url (a stub, a carrier without one) is not filtered.

        The states keep their own window snapshot (#1796), the same window `board` reads: a reporting pass
        that asks for the states seconds after another one sends no request. The snapshot is the Status-only
        answer, never `board`'s: a carrier that names no Run field still answers this read."""
        ttl = 0.0 if fresh else self._snapshot_ttl()
        f = self.st / self._states_file
        if ttl > 0:
            try:
                if time.time() - f.stat().st_mtime < ttl:
                    return json.loads(f.read_text())
            except (OSError, ValueError): pass
        states = self._read_all_live()
        tmp = f.with_suffix(f".{os.getpid()}.tmp")
        try:
            tmp.write_text(json.dumps(states))
            os.replace(tmp, f)
        except OSError:
            pass
        return states

    @property
    def _states_file(self):
        """The state snapshot's file, named by the binding's repository as the item map is (#770)."""
        tag = "-" + self.repo.replace("/", "_") if self.repo else ""
        return f"board-states-{self.owner}-{self.project}{tag}.json"

    def _read_all_live(self):
        """The live Status-only read behind `read_all`."""
        out = self.api("--paginate", f"{self.base}/items?per_page=100&fields[]={self.status_field()['id']}", "--jq",
                       '.[]|select(.content.number)|[.content.number,(((.fields[]?|select(.name=="Status")|.value.name|if type=="object" then .raw else . end))//""),'
                       ' (.content.repository_url//""),(.archived_at//"")]|@tsv')
        m = {}
        for l in out.splitlines():
            f = (l.split("\t") + ["", "", "", ""])[:4]
            n, s, u, archived = f[0].strip(), f[1], f[2], f[3]
            if archived: continue   # an archived card is no card to the readers, so none re-adds it (#1931)
            if n.isdigit() and (not self.repo or not u or u.endswith("/" + self.repo)): m[n] = self.state_name(s)
        return m

    def state_name(self, carried):
        """The vocabulary name for what the carrier answered. A carrier that answers the Status option's
        id (the id a write sends) is mapped through the field's options; a carrier that already resolved
        the name answers itself, and an option the field no longer declares is named as it came."""
        if not carried: return ""
        opt = self.status_field().get("opt") or {}
        return next((name for name, oid in opt.items() if oid == carried), carried)

    def read(self, issue):
        """The state one issue carries, "" when the carrier carries no card for it or no Status on it."""
        return self.read_all().get(str(issue), "")

    def rows(self, jq, *, fields=True, kind=False, tries=False, timeout=120):
        """One paginated items read at the carrier, projected by the caller's jq program. The reach is the
        binding's, so a reader that needs more than the state (the pane, with its titles and labels) asks
        here and holds no call of its own. A read the carrier refuses raises, and the caller keeps its last rows."""
        fid = f"&fields[]={self.status_field()['id']}" if fields else ""
        if fields and kind:   # the Kind field rides in the same read, so a pass that draws kinds adds no call (its field id is cached a day)
            try: fid += f"&fields[]={self.kind_field()['id']}" if self.kind_field()["id"] else ""
            except Exception: pass   # a carrier that cannot answer the field draws no kind: the rows are still read
        if fields and tries:   # the Gate Tries number rides the same read (#1255): the card shows it, so no second read
            try: fid += f"&fields[]={self.number_field()['id']}" if self.number_field()["id"] else ""
            except Exception: pass
        return self.api("--paginate", f"{self.base}/items?per_page=100{fid}", "--jq", jq, timeout=timeout)

    def holders(self):
        """Every card a run holds, in ONE read: {issue: {"run", "started"}}, off the Run and Run started text
        fields, asked for by id like Status. A card with no Run value is absent; the repository url filters as in `read_all`."""
        tf = self.text_fields()
        if "Run" not in tf or "Run started" not in tf: return {}
        val = 'if type=="object" then (.raw//.text//"") else (.//"") end'
        out = self.api("--paginate", f"{self.base}/items?per_page=100&fields[]={tf['Run']},{tf['Run started']}", "--jq",
                       '.[]|select(.content.number)|[.content.number,(((.fields[]?|select(.name=="Run")|.value|%s))//""),'
                       '(((.fields[]?|select(.name=="Run started")|.value|%s))//""),(.content.repository_url//"")]|@tsv' % (val, val))
        m = {}
        for l in out.splitlines():
            n, run, started, u = (l.split("\t") + ["", "", "", ""])[:4]
            n = n.strip()
            if n.isdigit() and run and (not self.repo or not u or u.endswith("/" + self.repo)): m[n] = {"run": run, "started": started}
        return m

    @property
    def _snapshot_file(self):
        """The board snapshot's file. The snapshot is filtered to the binding's repository, so the file is named by it too:
        two repositories on one board never read each other's snapshot (#770)."""
        tag = "-" + self.repo.replace("/", "_") if self.repo else ""
        return f"board-snapshot-{self.owner}-{self.project}{tag}.json"

    def _snapshot_ttl(self):   # the window one snapshot answers a reader that does not gate a write (#1796)
        return max(0.0, self._num("SG_BOARD_SNAPSHOT_S", "0"))   # off unless a reporting pass asks for it: a caller that writes never reads an old answer

    def board(self, fresh=False):
        """Every card's state and holder, in ONE read: ({issue: state}, {issue: {"run", "started"}}). The Status and the Run
        fields ride the same items read, so a caller that needs both asks once. The repository url filters as in `read_all`.

        The answer is the window's snapshot (#1796): a second reader inside `SG_BOARD_SNAPSHOT_S` sends no request, and a
        reader that gates a write passes `fresh` so the card it is about to move is never answered from an old answer. A
        window of zero keeps every read live."""
        ttl = 0.0 if fresh else self._snapshot_ttl()
        f = self.st / self._snapshot_file
        if ttl > 0:
            try:
                if time.time() - f.stat().st_mtime < ttl:
                    states, held = json.loads(f.read_text())
                    if held is not None: return states, held   # a snapshot with no holder map is a failed read, never an answer
            except (OSError, ValueError): pass
        states, held = self._read_board()
        if held is not None:   # a field list the carrier could not answer is a miss, and a miss is never cached (#1747)
            tmp = f.with_suffix(f".{os.getpid()}.tmp")
            try:
                tmp.write_text(json.dumps([states, held]))
                os.replace(tmp, f)
            except OSError:
                pass
        return states, held

    def _read_board(self):
        """The live read behind `board`, never cached here: its one items read, folded into the state and holder maps."""
        try: tf = self.text_fields()
        except (RuntimeError, ValueError, TypeError, AttributeError): tf = None   # a carrier that answers no field list (a bare id,
        # a refusal) names no text field: the Status column still answers, the holder map is unknown, and the miss is never cached
        ids = [x for x in (self.status_field()["id"], (tf or {}).get("Run"), (tf or {}).get("Run started")) if x]
        fq = f"&fields[]={','.join(map(str, ids))}" if ids else ""   # the REST route answers a field id as a number
        val = 'if type=="object" then (.raw//.text//"") else (.//"") end'
        out = self.api("--paginate", f"{self.base}/items?per_page=100{fq}", "--jq",
                       '.[]|select(.content.number)|[.content.number,(((.fields[]?|select(.name=="Status")|.value.name|if type=="object" then .raw else . end))//""),'
                       '(((.fields[]?|select(.name=="Run")|.value|%s))//""),(((.fields[]?|select(.name=="Run started")|.value|%s))//""),'
                       '(.content.repository_url//"")]|@tsv' % (val, val))
        states, held = {}, {}
        for l in out.splitlines():
            n, s, run, started, u = (l.split("\t") + ["", "", "", ""])[:5]
            n = n.strip()
            if n.isdigit() and (not self.repo or not u or u.endswith("/" + self.repo)):
                states[n] = self.state_name(s)
                if run: held[n] = {"run": run, "started": started}
        return states, (held if tf is not None else None)   # None: the Run fields were not read, so no card reads as unheld

    def kind_field(self):
        """The Kind field: `{"id", "opt"}`, the field's id and its option ids by normalized name. Cached a day, as Status."""
        def read_field():
            fs = json.loads(self.api(f"{self.base}/fields?per_page=100"))
            k = next((x for x in fs if isinstance(x, dict) and x.get("name") == "Kind"), None)
            if not k: return {"id": "", "opt": {}}
            return {"id": k["id"], "opt": {self._text(o["name"]).lower(): o["id"] for o in k.get("options", [])}}
        return self._cached(f"rest-kind-field-{self.owner}-{self.project}.json", 86400, read_field)

    @staticmethod
    def _text(v):
        return v.get("raw", "") if isinstance(v, dict) else (v or "")

    def kind_options(self):
        """{normalized kind: option id} the carrier can carry."""
        return self.kind_field()["opt"]

    def kinds(self):
        """Every card's kind, in ONE read: {issue: kind}, normalized, a card with no Kind absent. The field is asked for by id,
        and the repository url filters as in `read_all`. A carrier with no Kind field answers no kind."""
        fid = self.kind_field()["id"]
        if not fid: return {}
        out = self.api("--paginate", f"{self.base}/items?per_page=100&fields[]={fid}", "--jq",
                       '.[]|select(.content.number)|[.content.number,(((.fields[]?|select(.name=="Kind")|.value.name|if type=="object" then .raw else . end))//""),'
                       ' (.content.repository_url//"")]|@tsv')
        m = {}
        for l in out.splitlines():
            n, k, u = (l.split("\t") + ["", "", ""])[:3]
            n = n.strip()
            if n.isdigit() and k and (not self.repo or not u or u.endswith("/" + self.repo)): m[n] = k.lower()
        return m

    def write_kind(self, issue, kind):
        """One PATCH of the Kind field. The option id is the field's, never the name. A kind with no option is a `KindGap`."""
        field = self.kind_field()
        if kind not in field["opt"]: raise KindGap(issue, kind, sorted(field["opt"]))
        iid = self.item_id(issue)
        if not iid: raise RuntimeError(f"#{issue} not on the board")
        self._patch_kind(iid, field, kind)

    def _patch_kind(self, iid, field, kind):
        self.api("-X", "PATCH", f"{self.base}/items/{iid}", "--input", "-",
                 inp=json.dumps({"fields": [{"id": field["id"], "value": field["opt"][kind]}]}))

    def issue_facts(self, issue):
        """(state, label names) of one issue, read from the issue itself, or None when the read did not answer. Never
        cached: this is the read a naming decision keeps its premise on, so a list read at another time, or another
        repository's card on a shared board, cannot answer for the issue (#1031).
        An answer whose labels are not label objects is no answer either: read as an empty list it would deny the
        issue its own labels, and that denial is a premise nothing settled (ADR-0155, #1364)."""
        try:
            d = json.loads(self.api(f"repos/{self.repo}/issues/{issue}"))
        except (RuntimeError, OSError, subprocess.SubprocessError, ValueError, TypeError):   # a refused call, a hung one, a body that is not JSON
            return None
        if not isinstance(d, dict) or str(d.get("number", "")) != str(issue): return None
        labels = d.get("labels")
        if not isinstance(labels, list) or not all(isinstance(l, dict) and isinstance(l.get("name"), str) for l in labels): return None
        return str(d.get("state") or "").lower(), [l["name"] for l in labels]

    def drop_label(self, issue, label):
        """One DELETE of a label from an issue. The read that proves it is the seam's."""
        self.api("-X", "DELETE", f"repos/{self.repo}/issues/{issue}/labels/{urllib.parse.quote(label, safe='')}")

    def test_case_rows(self):
        """Every item that is not an issue, in ONE read: [(item id, title, kind)]. A test case is a draft item, so it has
        no issue number; its title carries what the pass recorded. Never cached: it is the read that proves a write."""
        fid = self.kind_field()["id"]
        out = self.api("--paginate", f"{self.base}/items?per_page=100&fields[]={fid}", "--jq",
                       '.[]|select(.content.number|not)|[.id,(.content.title//""),(((.fields[]?|select(.name=="Kind")|.value.name|if type=="object" then .raw else . end))//"")]|@tsv')
        rows = []
        for l in out.splitlines():
            i, t, k = (l.split("\t") + ["", "", ""])[:3]
            if i: rows.append((i, t, k.lower()))
        return rows

    def write_test_case(self, title):
        """One draft item titled `title`, then one PATCH of its Kind to `test case`. The read that proves it is the seam's."""
        field = self.kind_field()
        if "test case" not in field["opt"]: raise KindGap(title, "test case", sorted(field["opt"]))
        iid = json.loads(self.api("-X", "POST", f"{self.base}/items", "--input", "-", inp=json.dumps({"type": "DraftIssue", "title": title})))["id"]
        self._patch_kind(iid, field, "test case")

    def write_holder(self, issue, run, started):
        """One PATCH of the two Run fields. None is JSON null, the value that clears a field; an empty string is
        one GitHub refuses (#101). The read that proves it is the seam's."""
        iid = self.item_id(issue)
        if not iid: raise RuntimeError(f"#{issue} not on the board")
        tf = self.text_fields()
        if "Run" not in tf or "Run started" not in tf:
            (self.st / f"rest-text-fields-{self.owner}-{self.project}.json").unlink(missing_ok=True); tf = self.text_fields()
        missing = [x for x in HOLDER_FIELDS if x not in tf]
        if missing: raise RuntimeError("no text field " + ", ".join(missing))
        self.api("-X", "PATCH", f"{self.base}/items/{iid}", "--input", "-",
                 inp=json.dumps({"fields": [{"id": tf["Run"], "value": run}, {"id": tf["Run started"], "value": started}]}))

    def write(self, issue, state):
        """One PATCH of the Status field, in one write. The option id is the field's, never the name: the
        route takes the id. This is the write alone -- the read that proves it is the seam's."""
        iid = self.item_id(issue)
        if not iid: raise RuntimeError(f"#{issue} not on the board")
        field = self.status_field()
        self.api("-X", "PATCH", f"{self.base}/items/{iid}", "--input", "-",
                 inp=json.dumps({"fields": [{"id": field["id"], "value": field["opt"][state]}]}))


def board_state(owner=None, project=None, repo=None, **kw):
    """The seam this repository binds the vocabulary to: the board carrier, as `SG_OWNER`, `SG_PROJECT`
    and `SG_REPO` name it. No caller learns the carrier: it asks the seam."""
    env = kw.pop("env", os.environ)
    owner = owner if owner is not None else env.get("SG_OWNER", "")
    project = project if project is not None else env.get("SG_PROJECT", "")
    repo = repo if repo is not None else env.get("SG_REPO", "")
    return State(BoardBinding(owner, project, repo, env=env, **kw))


def _item_hints(raw):
    """The issue -> item id map the hub already holds (SG_BOARD_ITEM_HINTS, a JSON object), so a move names its card's item
    without reading the whole project for the id (#1300). A value that is not a JSON object is no hint."""
    try: d = json.loads(raw or "{}")
    except ValueError: return {}
    return {str(k): str(v) for k, v in d.items() if v} if isinstance(d, dict) else {}
def _name_unread(state_dir, missing, unanswered):
    """One alerts.log line for each go issue the pass names unread for a reason it was not named for on the last pass. An
    issue whose own read did not answer is named as that failed read, never as a card the board does not carry."""
    path = pathlib.Path(state_dir) / "frontier-unread.json"
    try: named = json.loads(path.read_text())
    except (OSError, ValueError): named = {}
    reasons = {m: ("issue read" if m in unanswered else "card") for m in missing}
    card = [m for m in missing if reasons[m] == "card" and named.get(m) != "card"]
    failed = [m for m in missing if reasons[m] == "issue read" and named.get(m) != "issue read"]
    with open(pathlib.Path(state_dir) / "alerts.log", "a") as f:
        if card: f.write(f"{_now()} FRONTIER unread: go-labelled {', '.join('#' + m for m in card)} has no readable card on the board\n")
        for m in failed: f.write(f"{_now()} FRONTIER unread: #{m} is in the go list, but its issue read did not answer and the board read carries no card for it\n")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(reasons))
    os.replace(tmp, path)


def _fresh_hub_items(require_fresh=True, repo=None):
    """Return verified rows for this repository, optionally requiring a recent refresh stamp.

    The hub stamps every row with its repository (`r`). One project may hold cards of several repositories and an
    issue number repeats between them, so a row that names another repository is that repository's card, never this
    board's (#1429). A cache row with no stamp proves no repository and is a cache written before the stamp: such a
    cache is not trusted at all, and the caller reads the board itself.

    A row the hub marked `nc` is an open issue the board does not carry, not a card: it is dropped here, so a reader
    of board cards never names an issue no card stands for. Membership is the hub's board pass, the read that wrote
    the cache: a card removed from the board leaves the next cache the pass writes, so it is named no longer than the
    freshness bound below (300 s) plus one pass. This door never reads the board to second-guess a fresh cache."""
    path = pathlib.Path(os.environ.get("SG_BOARD_CACHE", "/nonexistent"))
    try:
        data = json.loads(path.read_text())
        age = time.time() - float(data["t"]) if require_fresh else 0
        items = data["items"]
        expected_repo = repo if repo is not None else os.environ.get("SG_REPO", "")
        if not expected_repo or data.get("repo") != expected_repo:
            return None
        if 0 <= age < 300 and (not require_fresh or not data.get("err")) and isinstance(items, list):
            rows = []
            for i in items:
                if not isinstance(i, dict): continue
                if not i.get("r"):
                    if i.get("st") is not None: return None   # an unstamped card: the cache is not this build's
                    continue                                   # an issue with no card carries no state to name
                if str(i["r"]) != expected_repo: continue      # another repository's card, dropped
                if i.get("nc"): continue                       # an open issue the board does not carry: not a card
                rows.append(i)
            return rows
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return None


def _fast_states(repo):
    """Every open card's state from ONE GraphQL page (#1796), or None when that read does not answer.

    The REST items route answers a hundred cards a page whatever their column, and most of a mature board is closed, so
    the reporting reads pay a dozen pages for the cards that matter. The fast read asks the project for the cards that are
    not terminal, with their Status, in one page: the same read the hub's board already makes (board_fast). A missing
    owner, project or module, a refusal, a partial answer or a read that names no card at all is no answer, and the caller
    falls back to the whole-board read."""
    owner, project = os.environ.get("SG_OWNER"), os.environ.get("SG_PROJECT")
    if not owner or not project: return None
    here = pathlib.Path(__file__).resolve().parent
    try:
        sys.path.insert(0, str(here))
        import board_fast
        import ghread
        kind = board_state(repo=repo).binding.kind
        def gql(query, variables, timeout):
            out = ghread.graphql_read(query, variables, timeout=timeout, pass_name="board-states")
            if not out.is_answer: raise RuntimeError(out.line())
            if out.partial: raise RuntimeError("partial answer: " + ", ".join(out.partial))
            return out.answer
        nodes = board_fast.read_snapshot(gql, "organization" if kind == "orgs" else "user", owner, int(project),
                                         ("Closed", "Abandoned"), board_fast.Budget(300), time.time() + 30)
    except Exception:
        return None
    return _states_of(nodes, repo) or None


def _states_of(nodes, repo):
    """{issue: state} of the cards a fast-read snapshot carries, a card with no Status as "", and another repository's
    card and an archived card left out (#770). A pure fold, so the mapping is tested without a carrier."""
    m = {}
    for n in nodes or ():
        c = n.get("content") or {}
        if n.get("isArchived") or c.get("number") is None: continue
        if repo and (c.get("repository") or {}).get("nameWithOwner") != repo: continue
        m[str(c["number"])] = (n.get("fieldValueByName") or {}).get("name") or ""
    return m


def main(argv):
    """The door for a shell caller: it asks the seam the same way a Python caller does."""
    args = argv[1:]
    repo, label = "", ""
    if args[:1] == ["--repo"]: repo = args[1]; args = args[2:]
    if "--label" in args:
        k = args.index("--label"); label = args[k + 1] if k + 1 < len(args) else ""; del args[k:k + 2]
    # The binding is this profile's repository: with no --repo the door names SG_REPO, as every other path asks the
    # seam. A name neither the option nor the profile gives is no repository named, never a board read without a
    # filter (#1429).
    repo = repo or os.environ.get("SG_REPO", "") or None
    if args[:1] == ["--hub-todos"]:
        cached = _fresh_hub_items(require_fresh=False, repo=repo)
        if cached is not None:
            todo = os.environ.get("SG_TODO", "Todo")
            for item in sorted(cached, key=lambda i: int(i.get("n") or 0)):
                if item.get("n") is not None and item.get("st") == todo and str(item.get("s") or "").lower() != "closed":
                    print(item["n"])
        return 0
    if args[:1] == ["--go"]:   # the rows a dispatch pass reads, with the go-labelled issues the board does not carry named (#800)
        state = board_state(repo=repo)
        requested = [n for n in (args[1].split(",") if len(args) > 1 and args[1] else []) if n]
        cached = _fresh_hub_items(repo=repo)
        if cached is not None:   # the rows the hub holds, and the same naming rule: an issue they lack is asked for itself (#1031)
            held = {str(i["n"]): i.get("st") or "" for i in cached if i.get("n") is not None}
            # A cached row is a read of another time, and a card added to the board reads the board's default Todo until the
            # add's own column write lands (#1758): a pass that read the cache in that window dispatched a card the owner had
            # put in Backlog. The source decides every Todo row before the pass claims one, and a row the source no longer
            # carries in Todo keeps the state the source answers, so it is never claimed as a card in Todo.
            todo = os.environ.get("SG_TODO", "Todo")
            for n in [k for k, s in held.items() if s == todo]:
                try: held[n] = state.read_one(n)
                except (RuntimeError, ValueError): held[n] = ""   # a read that does not answer proves no Todo card
            carried, rescued, missing = state.unread_rows(held, requested, label)
        else:
            carried, rescued, missing = state.unread(requested, label)
        rows = {**carried, **rescued}
        for n, s in sorted(rows.items(), key=lambda kv: int(kv[0])): print(f"{n}\t{s}")
        if missing:
            print("UNREAD\t" + ",".join(missing))
            if os.environ.get("SG_STATE"): _name_unread(os.environ["SG_STATE"], missing, state.unanswered)
        return 0
    if args[:1] == ["--kinds"]:
        cached = _fresh_hub_items(repo=repo)
        rows = ({str(i["n"]): i["k"] for i in cached if i.get("n") is not None and i.get("k")}
                if cached is not None else board_state(repo=repo).kinds())
        for n, k in sorted(rows.items(), key=lambda kv: int(kv[0])): print(f"{n}\t{k}")
        return 0
    if args[:1] == ["--kind-gaps"]:
        for k in board_state(repo=repo).kind_gaps(): print(k)
        return 0
    if not args or args[0] not in ("--state",):
        cached = _fresh_hub_items(repo=repo)
        fast = None if cached is not None else _fast_states(repo)
        if cached is not None:
            rows = {str(i["n"]): i["st"] for i in cached if i.get("n") is not None and i.get("st") is not None}
        elif fast is not None:
            rows = fast
        else:
            rows = board_state(repo=repo).read_all()
        for n, s in sorted(rows.items(), key=lambda kv: int(kv[0])): print(f"{n}\t{s}")
        return 0
    state = board_state(repo=repo)
    print(state.read(args[1]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
