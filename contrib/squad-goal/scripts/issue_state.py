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
"""
import json, os, pathlib, re, subprocess, sys, time

# The binding a repository binds the vocabulary to. A carrier is named here, and a run records the name.
BOARD = "board"


class NotCarried(RuntimeError):
    """The write is not carried by the read that follows it: the named failure, never a written card.

    `refused` holds the carrier's own words when it refused the write, because a refusal is no proof
    either way: a write the carrier refuses can still be a state the issue carries (#105, #101), and only
    the read tells the two apart."""

    def __init__(self, issue, wanted, carried, refused=""):
        self.issue, self.wanted, self.carried, self.refused = issue, wanted, carried, refused
        super().__init__(f"issue {issue} does not carry {wanted!r} after the write; it carries {carried!r}" + (f" ({refused})" if refused else ""))


class ReadFailure(RuntimeError):
    """A card the board answered "already there" for, which no read then carried: the read is behind the
    write, not the card gone."""

    def __init__(self, reads, count): super().__init__(f"the board read never carried it after {reads} reads ({count} items read); a read failure, not an absence")


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
        self.server_error = re.compile(r"\bHTTP 5\d\d\b")
        self.items_map = None

    def _num(self, name, default):   # a bad value in the environment must not kill a card move
        try: return float(self.env.get(name, default))
        except ValueError: return float(default)

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
        if wrote: raise ReadFailure(self.attempts, len(self.items_map))
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

    def rows(self, jq, *, fields=True, timeout=120):
        """One paginated items read at the carrier, projected by the caller's jq program. The reach is the
        binding's, so a reader that needs more than the state (the pane, with its titles and labels) asks
        here and holds no call of its own. A read the carrier refuses raises, and the caller keeps its last rows."""
        fid = f"&fields[]={self.status_field()['id']}" if fields else ""
        return self.api("--paginate", f"{self.base}/items?per_page=100{fid}", "--jq", jq, timeout=timeout)

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
    if not args or args[0] not in ("--state",):
        state = board_state(repo=repo)
        for n, s in sorted(state.read_all().items(), key=lambda kv: int(kv[0])): print(f"{n}\t{s}")
        return 0
    state = board_state(repo=repo)
    print(state.read(args[1]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
