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
    state.write(236, "Testing")         # proven by the read that follows; returns the state the issue then carries
    state.holder(236)                   # the run that holds issue 236: {"run": id, "started": time}, None when none does
    state.holders()                     # every holder, one read: {"236": {"run": ..., "started": ...}}
    state.hold(236, run, started)       # name the run on the issue, proven by the read that follows
    state.release(236)                  # clear the holder when the run ends, proven by the read that follows
    state.kind(243)                     # the repository's own kind the item carries: bug, task, spec, test case; "" when none
    state.kinds()                       # every kind, one read: {"243": "bug", ...}
    state.set_kind(243, "bug")          # proven by the read that follows; a kind the binding cannot carry is a `KindGap`
    state.kind_gaps()                   # the kinds of the vocabulary the binding cannot carry, reported by name
    state.record_pass(300, "pass", sha) # a finished test pass: one `test case` item (issue, result, merge commit, end time), proven by the read that follows
    state.test_cases()                  # every test case item, one read: [{"issue", "result", "commit", "ended", "age"}]

    state.group_run(run, [10, 11])      # the run's children as sub-issues of one parent that names the run; None for fewer than two
    state.children(5)                   # the sub-issues of issue 5, one read: [10, 11]
    state.link(5, 10)                   # issue 10 a sub-issue of issue 5, proven by the read that follows (asked again, bounded)

**A write is proven by the read that follows it.** The answer to the write is no proof -- a field write can
be answered ok and never land (#105) -- so `write` reads the issue back and compares. A write the read does
not carry raises `NotCarried`, which names the issue, the state wanted and the state carried: the caller
reports it, and a write no read carries is never cached as one. The retry is the caller's policy, not the
seam's: board.py writes again, up to its own bound.

The seam is also recorded, because the binding is a decision a run makes once: `squad.engine` records the
binding's `name` at run start and a resume that finds a different name refuses (ADR-0107).

Shell callers reach the same seam through this module's door:

    issue_state.py [--repo R]              print one `<issue>\t<state>` a line, the state the carrier answers
    issue_state.py --state <issue>         print the state one issue carries
    issue_state.py --kinds                 print one `<issue>\t<kind>` a line, the kind the carrier answers
    issue_state.py --kind-gaps             print one kind a line, each the binding cannot carry

A **kind** is the repository's own normalized name for what an item is, and never the platform's issue type: a
caller names `bug`, and only the binding knows which field and option carry it (#243).
"""
import json, os, pathlib, re, subprocess, sys, time, urllib.parse

# The binding a repository binds the vocabulary to. A carrier is named here, and a run records the name.
BOARD = "board"

# The kinds an item carries, as the repository names them. A binding that cannot carry one reports it as a gap.
KINDS = ("bug", "task", "spec", "test case")

WAVE = re.compile(r"^wave:\d+$")   # the ordering label, and the title of the milestone that carries a delivery


RUN_PARENT_TITLE = re.compile(r"^Squad run run-[0-9]{8}-[0-9]{6}-[0-9a-f]+$")   # the shell passes (frontier, bug) filter on the same pattern, in jq


def is_run_parent(title):
    """A run parent is a roll-up of its children, never a unit of work and never a child of another run parent (#340)."""
    return bool(RUN_PARENT_TITLE.match(title or ""))


class RunParentChild(RuntimeError):
    """`group_run` was asked to link a run parent as a child. Not a carrier failure: asking again can not change it."""

    def __init__(self, issue):
        self.issue = issue
        super().__init__(f"#{issue} is a run parent, never a child of another run parent")


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


class WaveSpansRepositories(RuntimeError):
    """A wave whose issues sit in more than one repository: refused before any write, because a milestone belongs to one
    repository. The wave stays an ordering label."""

    def __init__(self, wave, repos):
        self.wave, self.repos = wave, sorted(repos)
        super().__init__(f"{wave} spans {', '.join(self.repos)}; a milestone belongs to one repository, so the wave stays a label")


class ReadFailure(RuntimeError):
    """A card the board answered "already there" for, which no read then carried: the read is behind the
    write, not the card gone."""

    def __init__(self, reads, count): super().__init__(f"the board read never carried it after {reads} reads ({count} items read); a read failure, not an absence")


RESULTS = ("pass", "fail")   # how a test pass ends; a failed pass is never overwritten by a green one (#300)
TEST_CASE_TITLE = re.compile(r"^test case #(\d+) \| (pass|fail) \| (\S+) \| (\S+)$")


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

    @property
    def name(self):
        return self.binding.name

    def read(self, issue):
        """The state of one issue, "" when the carrier carries none (or carries no card for it)."""
        return self.binding.read(issue)

    def read_all(self):
        """Every state the carrier answers, in one read: {issue: state}.

        One read for the whole board, never one per issue: a pass that asks about many cards asks once.
        """
        return self.binding.read_all()

    def rows(self, jq, **kw):
        """Every card in the same one read as `read_all`, projected by the caller's jq: the hub's pane draws titles
        and labels beside the state, and asks for them here rather than at the carrier."""
        return self.binding.rows(jq, **kw)

    def write(self, issue, wanted):
        """Write `wanted` on `issue`, then read it back. The read is the proof, and neither the answer nor
        the refusal is: a write the carrier refuses can be a state the issue already carries (#105, #101),
        and a write answered ok can be one it never carries. The state the issue carries after the write is
        the verdict -- a state other than the one wanted is `NotCarried`, named, and it is never reported
        as written. Returns the state the issue carries."""
        refused = ""
        try: self.binding.write(issue, wanted)
        except RuntimeError as e: refused = str(e)   # the refusal is kept, not raised: the read then confirms it or disproves it
        carried = self.read(issue)
        if carried != wanted:
            raise NotCarried(issue, wanted, carried, refused)
        return carried

    def holders(self):
        """Every issue a run holds, in one read: {issue: {"run": id, "started": time}}. An issue no run holds is absent."""
        return self.binding.holders()

    def holder(self, issue):
        """The run that holds `issue`, {"run": id, "started": time}; None when no run holds it."""
        return self.holders().get(str(issue))

    def hold(self, issue, run, started):
        """Name the run that holds `issue` on the issue, then read it back: the read is the proof, as for a state.
        An empty value is written as no value (#101). Returns the holder the issue then carries."""
        wanted = {"run": run, "started": started or ""} if run else None
        return self._write_holder(issue, run or None, started or None, wanted)

    def release(self, issue):
        """Clear the holder when the run ends, then read it back: a holder the next read still carries is
        `NotCarried`, never a clear. Returns None, the holder the issue then carries."""
        return self._write_holder(issue, None, None, None)

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

    def wave(self, issue):
        """The wave one issue carries, "" when it carries none. A milestone that carries a wave answers it, and the label
        is read only where no milestone does: the label is derived from the milestone, never a second source."""
        milestone, labels = self.binding.issue_wave(self._ref(issue)[1])
        if WAVE.match(milestone): return milestone
        return next((l for l in labels if WAVE.match(l)), "")

    def deliver_wave(self, wave, issues, due=None):
        """The owner marks `wave` as a delivery: one milestone carries it, each issue is set on it, and the stored wave label
        is removed so the label is only what the milestone derives. `issues` are numbers in this binding's repository or
        `owner/repo#N`; a set that spans repositories is `WaveSpansRepositories`, raised before any write. A milestone the
        owner did not date gets no due date: `due` is an ISO stamp the owner gave, and is the only way one is set.
        Every write is proven by the read that follows it: a milestone or a label removal that read does not carry is
        `NotCarried`, and the label is removed only after the read carried the milestone. Returns {"milestone", "number", "issues"}."""
        if not WAVE.match(wave): raise ValueError(f"a wave is named wave:NN, not {wave!r}")
        refs = [self._ref(i) for i in issues]
        repos = {r for r, _ in refs}
        if len(repos) > 1: raise WaveSpansRepositories(wave, repos)
        number = self.binding.milestone(wave, due)
        for _, n in refs:
            refused = ""
            try: self.binding.write_milestone(n, number)
            except RuntimeError as e: refused = str(e)
            carried = self.binding.issue_wave(n)[0]
            if carried != wave: raise NotCarried(n, wave, carried, refused)
            if wave in self.binding.issue_wave(n)[1]:
                refused = ""
                try: self.binding.drop_label(n, wave)
                except RuntimeError as e: refused = str(e)
                if wave in self.binding.issue_wave(n)[1]: raise NotCarried(n, f"no {wave} label beside the milestone", wave, refused)
        return {"milestone": wave, "number": number, "issues": [n for _, n in refs]}

    def _ref(self, issue):
        """(repository, number) of an issue named as a number or `owner/repo#N`."""
        m = re.match(r"^(?:(.+)#)?(\d+)$", str(issue))
        if not m: raise ValueError(f"an issue is a number or owner/repo#N, not {issue!r}")
        return (m[1] or self.binding.repo), int(m[2])

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

    def children(self, parent):
        """The numbers of the sub-issues of `parent`, in one read."""
        return self.binding.children(parent)

    def link(self, parent, child):
        """Make `child` a sub-issue of `parent`, then read the sub-issues back. The read is the proof, and it can lag
        the write, so it is asked again a bounded number of times with a growing delay: a link no read carries is
        `NotCarried`, named, never reported as written. A child already linked is not written again."""
        if int(child) in self.children(parent): return
        refused = ""
        try: self.binding.link(parent, child)
        except RuntimeError as e: refused = str(e)   # kept, not raised: the read confirms the link or disproves it
        carried = self.binding.await_child(parent, child)
        if int(child) not in carried:
            raise NotCarried(child, f"sub-issue of {parent}", f"sub-issue of {sorted(carried)}" if carried else "no parent link", refused)

    def group_run(self, run, children):
        """One parent issue that names `run`, with each of `children` linked to it as a sub-issue, so the platform
        rolls the run's progress up. A run with fewer than two distinct children has no parent: None, and nothing
        is written. A child that already has another parent (a spec) is left out, and counts as no child. A child that is itself a run parent is refused (`RunParentChild`), before anything is written. The run's parent is found by its title and reused, never created twice. Returns its number."""
        kids = sorted({int(c) for c in children})
        if len(kids) < 2: return None
        for k in kids:
            if is_run_parent(self.binding.title(k)): raise RunParentChild(k)
        found = self.binding.parent_of(run)
        kids = [k for k in kids if self.binding.parent(k) in (None, found)]   # GitHub gives an issue one parent: a child under a spec is left out (#368)
        if len(kids) < 2: return found
        parent = found or self.binding.create_parent(run)
        for k in kids: self.link(parent, k)
        return parent

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

    def _num(self, name, default):   # a bad value in the environment must not kill a card move
        try: return float(self.env.get(name, default))
        except ValueError: return float(default)

    def settle(self, probe, reads):
        """A new card is eventually consistent (#403): an add or a write GitHub accepted can be missing from reads for
        longer than the bounded attempts. After `reads` reads that missed, read again with the doubling backoff (each
        delay at most 16 s) while the sleeps fit in SG_BOARD_SETTLE_SECONDS. Returns the first truthy `probe()`, else
        None. A backoff of 0 sleeps for no read, so it reads no more."""
        slept, k = 0.0, max(1, reads)
        while self.backoff:
            d = min(self.backoff * (2 ** (k - 1)), 16.0)
            if slept + d > self.settle_seconds: return None
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
            if not r.returncode: return r.stdout
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
            return {x.get("name"): x.get("id") for x in fs if x.get("name") in ("Run", "Run started")}
        return self._cached(f"rest-text-fields-{self.owner}-{self.project}.json", 86400, read_fields)

    def items(self):
        """The issue -> item id map, REST only: the items route is a bare array with no total, so name the
        count read and its source, never a number not read (#96)."""
        out = self.api("--paginate", f"{self.base}/items?per_page=100", "--jq",
                       '.[]|select(.content.number)|[.content.number,.id,(.content.repository_url//"")]|@json')
        m = {str(n): i for n, i, ru in (json.loads(l) for l in out.splitlines() if l.strip()) if not self.repo or ru.endswith("/" + self.repo)}
        print(f"issue_state.py: items read {len(m)}; project total: none on the REST items route (source: {self.base}/items, a bare array)", file=sys.stderr)
        return m

    def _items(self):
        if self.items_map is None:
            self.items_map = self._cached(f"rest-items-{self.owner}-{self.project}.json", 600, self.items)
        return self.items_map

    def _save_items(self):
        (self.st / f"rest-items-{self.owner}-{self.project}.json").write_text(json.dumps(self.items_map))

    def item_id(self, n, add_missing=False):
        """The one reader every caller uses (move, --add, --run-fields, --clear-run). A miss re-reads the
        items a bounded number of times with a growing delay: the items route is eventually consistent, so
        a card written a moment ago is not always in the next read (#96). None means a card no read ever
        carried and nothing was written for it here."""
        key = str(n)
        if key not in self._items(): self.items_map = self.items(); self._save_items()
        if key in self.items_map: return self.items_map[key]
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
        if wrote:   # the card is on the board and the read lags: a new card, read within the settle bound before the failure (#403)
            reads = [self.attempts]
            def probe():
                reads[0] += 1; self.items_map = self.items(); self._save_items()
                return self.items_map.get(key)
            iid = self.settle(probe, self.attempts)
            if iid: return iid
            raise ReadFailure(reads[0], len(self.items_map))
        return None   # a read that never carried it, and nothing was written for it here: genuinely absent

    def read_all(self):
        """Every card the carrier answers, in ONE read: {issue: state}, a card with no Status absent.

        The Status field is asked for by id -- a single-select value is carried only when the field is
        asked for, the way janitor.py reads it, and without `fields[]` the route answers the title alone.
        A row's repository url filters the map to this binding's repository, and a carrier that answers no
        url (a stub, a carrier without one) is not filtered."""
        out = self.api("--paginate", f"{self.base}/items?per_page=100&fields[]={self.status_field()['id']}", "--jq",
                       '.[]|select(.content.number)|[.content.number,(((.fields[]?|select(.name=="Status")|.value.name|if type=="object" then .raw else . end))//""),'
                       ' (.content.repository_url//"")]|@tsv')
        m = {}
        for l in out.splitlines():
            f = (l.split("\t") + ["", "", ""])[:3]
            n, s, u = f[0].strip(), f[1], f[2]
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

    def rows(self, jq, *, fields=True, kind=False, timeout=120):
        """One paginated items read at the carrier, projected by the caller's jq program. The reach is the
        binding's, so a reader that needs more than the state (the pane, with its titles and labels) asks
        here and holds no call of its own. A read the carrier refuses raises, and the caller keeps its last rows."""
        fid = f"&fields[]={self.status_field()['id']}" if fields else ""
        if fields and kind:   # the Kind field rides in the same read, so a pass that draws kinds adds no call (its field id is cached a day)
            try: fid += f"&fields[]={self.kind_field()['id']}" if self.kind_field()["id"] else ""
            except Exception: pass   # a carrier that cannot answer the field draws no kind: the rows are still read
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

    def issue_wave(self, issue):
        """(milestone title, label names) one issue carries, in one read. Never cached: it is the read that proves a write."""
        d = json.loads(self.api(f"repos/{self.repo}/issues/{issue}"))
        return ((d.get("milestone") or {}).get("title") or ""), [l["name"] for l in d.get("labels", []) if isinstance(l, dict)]

    def milestone(self, title, due=None):
        """The number of the one milestone titled `title`: found when it exists (open or closed), created when it does not.
        A create sends no due date unless the owner gave one."""
        found = next((m for m in json.loads(self.api("--paginate", f"repos/{self.repo}/milestones?state=all&per_page=100")) if m.get("title") == title), None)
        if found: return found["number"]
        body = {"title": title, **({"due_on": due} if due else {})}
        return json.loads(self.api("-X", "POST", f"repos/{self.repo}/milestones", "--input", "-", inp=json.dumps(body)))["number"]

    def write_milestone(self, issue, number):
        """One PATCH of the issue's milestone. The read that proves it is the seam's."""
        self.api("-X", "PATCH", f"repos/{self.repo}/issues/{issue}", "--input", "-", inp=json.dumps({"milestone": number}))

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
        missing = [x for x in ("Run", "Run started") if x not in tf]
        if missing: raise RuntimeError("no text field " + ", ".join(missing))
        self.api("-X", "PATCH", f"{self.base}/items/{iid}", "--input", "-",
                 inp=json.dumps({"fields": [{"id": tf["Run"], "value": run}, {"id": tf["Run started"], "value": started}]}))

    @staticmethod
    def parent_title(run):
        return f"Squad run {run}"

    def parent_of(self, run):
        """The number of the issue titled for `run`, None when the run has none. A parent this seam made or found is
        remembered under $SG_STATE and read back by number, so a retry never pages every issue. Otherwise read off the
        issues route, which is not the search index: the index lags a new issue, and a lag here would create a second parent."""
        title = self.parent_title(run)
        known = self.st / f"run-parent-{run}"
        if known.exists():
            n = known.read_text().strip()
            try:
                if n.isdigit() and json.loads(self.api(f"repos/{self.repo}/issues/{n}")).get("title") == title: return int(n)
            except (RuntimeError, ValueError): pass   # a read that fails or does not match: the page below decides
        out = self.api("--paginate", f"repos/{self.repo}/issues?state=all&per_page=100", "--jq",
                       '.[]|select(.pull_request==null)|[.number,.title]|@json')
        for l in out.splitlines():
            if l.strip():
                n, t = json.loads(l)
                if t == title: known.write_text(str(n)); return n
        return None

    def title(self, issue):
        """The title of `issue`, in one read."""
        return json.loads(self.api(f"repos/{self.repo}/issues/{issue}")).get("title") or ""

    def parent(self, issue):
        """The number of the parent issue of `issue`, None when it has none, in one read."""
        u = json.loads(self.api(f"repos/{self.repo}/issues/{issue}")).get("parent_issue_url") or ""
        return int(u.rstrip("/").rsplit("/", 1)[1]) if u else None

    def create_parent(self, run):
        """One POST of the parent issue, titled for the run. Returns its number, which is remembered for `parent_of`."""
        body = f"The children of squad run {run}. Each child is a sub-issue; the progress rolls up here."
        n = json.loads(self.api("-X", "POST", f"repos/{self.repo}/issues", "--input", "-",
                                inp=json.dumps({"title": self.parent_title(run), "body": body, "labels": ["run-parent"]})))["number"]   # the label keeps it out of dispatch (#340)
        (self.st / f"run-parent-{run}").write_text(str(n))
        return n

    def children(self, parent):
        return [int(n) for n in self.api("--paginate", f"repos/{self.repo}/issues/{parent}/sub_issues?per_page=100", "--jq", ".[].number").split()]

    def link(self, parent, child):
        """One POST of the sub-issue. The route takes the child's id, never its number. The read that proves it is the seam's."""
        cid = json.loads(self.api(f"repos/{self.repo}/issues/{child}"))["id"]
        self.api("-X", "POST", f"repos/{self.repo}/issues/{parent}/sub_issues", "--input", "-", inp=json.dumps({"sub_issue_id": cid}))

    def await_child(self, parent, child):
        """The sub-issues of `parent` once one read carries `child`, or the last read after the bounded attempts: the
        route can lag a write, as the items route does (#96)."""
        carried = []
        for k in range(self.attempts):
            if k and self.backoff: time.sleep(self.backoff * (2 ** (k - 1)))
            try: carried = self.children(parent)
            except RuntimeError: continue   # a read the carrier refuses is no proof, and is asked again
            if int(child) in carried: break
        return carried

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


def main(argv):
    """The door for a shell caller: it asks the seam the same way a Python caller does."""
    args = argv[1:]
    repo = ""
    if args[:1] == ["--repo"]: repo = args[1]; args = args[2:]
    if args[:1] == ["--kinds"]:
        for n, k in sorted(board_state(repo=repo).kinds().items(), key=lambda kv: int(kv[0])): print(f"{n}\t{k}")
        return 0
    if args[:1] == ["--kind-gaps"]:
        for k in board_state(repo=repo).kind_gaps(): print(k)
        return 0
    if not args or args[0] not in ("--state",):
        state = board_state(repo=repo)
        for n, s in sorted(state.read_all().items(), key=lambda kv: int(kv[0])): print(f"{n}\t{s}")
        return 0
    state = board_state(repo=repo)
    print(state.read(args[1]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
