#!/usr/bin/env python3
"""Move Project (v2) cards over REST only (no GraphQL quota): board.py <owner> <project> "<Status>" <issue#>...
       board.py --add <owner> <project> "<Status>" <issue#>...   add the issues to the board first (when missing); idempotent: a refused add is re-read once
       board.py --remove <owner> <project> <issue#>...   take the issues off the board; an issue with no card is skipped, never written (#500)
       board.py --run-fields <owner> <project> <issue#> <run> <run-started>
       board.py --clear-run <owner> <project> <issue#>
       board.py --proof <owner> <project> <issue#> <unproven|green|red>   the card's proof fact: `unproven` while the
         obligation is open, `proof:red` when a settlement measured a defect, neither on a green settlement or a proven landing (#1176)
       board.py --test-case <owner> <project> <issue#> <pass|fail> <merge-commit> [ended]   record a finished test pass as one `test case` item (#300)
       board.py --gate-tries <owner> <project> <issue#> <n>   raise the card's Gate Tries to the journal's count (#1255):
         the field only rises (a rearm is a floor, ADR-0149), and it is written only when the value changes (ADR-0144)
       board.py --priority <owner> <project> <issue#> <urgent|high|normal|low>   set the card's Priority (#1978): written
         only when the value changes (ADR-0144), proven by the read that follows, and a value no read carries exits non-zero
This is the board binding's command door (#236): the state it reads and writes is issue_state's, and this
file holds the carrier's command line, never a second reach of its own. A field is set by sending its value
and cleared by sending null -- "To clear the field, set this to null" -- and an empty string is a value the
field refuses, answered "Invalid field values" (HTTP 400) (#101): a clear, and a Run field with nothing to
carry, are written as null, beside the field id they read. The Run fields are the issue's holder, and the
seam writes it: `--run-fields` is `hold`, `--clear-run` is `release`, each proven by the read that follows (#241).
A Status write is proven by the card, never by the answer to its PATCH (#105): the seam reads the card back
after the write, and the state it carries must be the state written. SG_BOARD_READ_ATTEMPTS and
SG_BOARD_READ_BACKOFF bound the retries (a backoff of 0 sleeps for no read), and a card no read then carries
the Status is a READ FAILURE, named and exiting non-zero, never a card written and never left in the state
cache as one.
A refused Status write drops the cached field and reads it again, so an option the board recreated is written by its
new id and one it dropped is named "no Status option", never retried on the dead id (#311).
A miss on the item map re-reads the items with the same bounded backoff (#96), and a card the board answered
"already exists" for and no read then carried is a READ FAILURE, named by name, never "not on the board".
A test pass is recorded by `--test-case` through the seam (`record_pass`), proven by the read that follows: a write that read
is behind is waited for with the same bounded backoff, and an item no read carries is named on stderr and exits non-zero,
never reported as written. A pass that ended failed stays failed: a later pass on the same merge commit writes nothing.
A new card is eventually consistent (#403): when GitHub accepted the add or the write and the read carries no card or no
Status yet, the read is repeated within SG_BOARD_SETTLE_SECONDS (default 60) before the READ FAILURE is named. A card the
read still shows on its old Status after an accepted write is the same lag, and is read the same way (#434); the settle
only reads, so a card another writer moved meanwhile is never moved back. A card that carried another Status before the write is
read for at most SG_BOARD_OLD_SETTLE_SECONDS (default 10) of that bound: a write it never carries is likelier lost than late (#438).
A move to the Status the card already carries is skipped: one read of the card, no PATCH, no proving read (#327). The card's read is its
item's node read, one call (#1300); the whole-board read answers only when that read does not, and SG_BOARD_ITEM_HINTS (JSON, issue -> item id)
lets the hub name the item so the move reads no whole-project list for the id.
A writer that is not the owner's door (`SG_MOVE_BY` is not `owner`) never moves a card back along the axis, except the loop's own
Doing-to-Todo unclaim, and the same return to Todo when the move names `SG_MOVE_REASON=no-landing`: a run that landed nothing
undoes the column the loop itself set, and that unclaim reaches no other column (#1059). Every other backward move is refused:
the card keeps its column and its issue gets the `rework` label, the refusal is logged to $SG_STATE/board-rejections.jsonl, and
the exit is 3 (ADR-0143). A move from Backlog to Todo is held the same way for every reason, with no rework label (#1909): only the
owner's door starts a card. A compare read that fails leaves an automated writer no Todo write, and the read failure is named (#1909).
Each transition and each refusal records `by`, `pass` (the caller's pass, `hand` when none) and `reason`.
The proof fact (#1176) is two labels on the issue: `unproven` while the proof obligation the gate wrote is open, and
`proof:red` when the settlement measured a defect. A green settlement and a proven landing carry neither. The write is
proven by the issue read that follows it, and a fact the read does not carry is a READ FAILURE, named and exiting
non-zero, never a fact the card claims. A card already in the wanted state is not written again.
Touches $SG_STATE/board-dirty so the hub refreshes within 2 s. Each move it proved is recorded in
$SG_STATE/board-moves.json by that same step: one entry per item, the column it set, the instant and the dispatch context `SG_MOVE_BY` names (`owner` or `loop`, when set), replaced by a
later move of the same item. The file is formed in a temp file beside it and renamed, so a reader sees the old record
or the new one, never a part-written one (ADR-0030). Each proved move also appends one transition (card, from, to, instant, repository)
to `$SG_STATE/board-transitions.<owner>_<name>.jsonl`, the record of its repository (#897). A skipped or unproven move records nothing, and a holder or
test-case write records nothing."""
import fcntl, importlib, json, os, pathlib, sys, time
from board_columns import HOLD, RESET_LABEL, canonical, option_name, past, refused_for_automated, reset_comment
from issue_state import KindGap, NotCarried, ReadFailure, board_state
args = sys.argv[1:]; add = args[:1] == ["--add"]; run_fields = args[:1] == ["--run-fields"]; clear_run = args[:1] == ["--clear-run"]; test_case = args[:1] == ["--test-case"]; remove = args[:1] == ["--remove"]; proof = args[:1] == ["--proof"]; gate_tries = args[:1] == ["--gate-tries"]; priority = args[:1] == ["--priority"]
args = args[1:] if add or run_fields or clear_run or test_case or remove or proof or gate_tries or priority else args
owner, num = args[0], args[1]
st = pathlib.Path(os.environ["SG_STATE"])
state = board_state(owner=owner, project=num, kind=None)   # the move is asked for an owner the caller names, so the carrier says whether it is a user or an org (cached a month)

def todo_limit():
    """The Todo column's card limit (#2437): `columns.Todo.max_cards` from `squad/board_limits.py` in the tree this file
    sits in, then SG_SQUAD, over the project settings file under SG_CHECKOUT. None for no limit, and None when the reader
    does not load, so a tree without the squad package keeps the move it made before and a failed read invents no limit."""
    try: roots = [str(pathlib.Path(__file__).resolve().parents[3]), os.environ.get("SG_SQUAD") or ""]
    except IndexError: roots = [os.environ.get("SG_SQUAD") or ""]   # a copy too near the root names no tree of its own
    target = os.environ.get("SG_CHECKOUT") or "."
    for root in roots:
        if not root or not (pathlib.Path(root) / "squad" / "board_limits.py").is_file(): continue
        try:
            if root not in sys.path: sys.path.insert(0, root)
            bl = importlib.import_module("squad.board_limits")
            if not pathlib.Path(bl.__file__).resolve().is_relative_to(pathlib.Path(root).resolve()): continue
            return bl.limit("Todo", target)
        except Exception: continue
    return None

_TODO = {"max": False, "now": None}   # the limit is one read, and only a move reads it; the count is one read, kept by each proved move
def todo_full():
    """True when the Todo column holds its card limit. The count is one live board read, the read a write that gates another
    write takes (#1796), and each Todo move this pass proves adds one, so a batch never passes the limit it started under."""
    if _TODO["max"] is False: _TODO["max"] = todo_limit()
    if not _TODO["max"]: return False
    if _TODO["now"] is None:
        try: _TODO["now"] = sum(1 for s in state.read_all(fresh=True).values() if canonical(s) == "Todo")
        except (RuntimeError, ValueError): return False   # a count no read answers holds nothing: a failed read settles no premise (ADR-0155)
    return _TODO["now"] >= _TODO["max"]
if gate_tries:   # the journal's count is the source; the field is its projection (#1255)
    n = int(args[2])
    try: carried = state.set_gate_tries(n, int(args[3]))   # never lowers, and writes only on a change (#1255, ADR-0144, ADR-0149)
    except (NotCarried, ReadFailure) as e: print(f"#{n}: {e}", file=sys.stderr); sys.exit(1)
    (st / "board-dirty").touch(); print(f"#{n} gate tries {carried}"); sys.exit(0)
if priority:   # the birth Priority (#1978): one write only on a change, then the read that proves it
    n = int(args[2])
    try: wrote = state.set_priority(n, args[3])
    except (NotCarried, ReadFailure, RuntimeError) as e: print(f"#{n}: {e}", file=sys.stderr); sys.exit(1)
    (st / "board-dirty").touch(); print(f"#{n} priority {args[3]}" + ("" if wrote else " (already carried, not written)")); sys.exit(0)
if test_case:
    n = int(args[2])
    try: item = state.record_pass(n, args[3], args[4], args[5] if len(args) > 5 else None)   # one item, then the read that proves it (#300)
    except (ValueError, KindGap, NotCarried, ReadFailure) as e: print(f"#{n}: {e}", file=sys.stderr); sys.exit(1)
    print(f"#{n} test case {item['result']} @ {item['commit']}"); (st / "board-dirty").touch(); sys.exit(0)
if proof:   # the card's proof fact (#1176): a label write, proven by the issue read that follows it
    n = int(args[2]); word = args[3]
    if word not in ("unproven", "green", "red"): sys.exit(f"--proof: unknown word {word!r}: one of unproven, green, red")
    want = {"unproven": {"unproven"}, "red": {"unproven", "proof:red"}, "green": set()}[word]
    before = state.binding.issue_facts(n)
    if before is None: print(f"#{n}: the issue read did not answer; no proof fact written", file=sys.stderr); sys.exit(1)
    have = set(before[1]); add = [l for l in ("unproven", "proof:red") if l in want and l not in have]
    drop = [l for l in ("unproven", "proof:red") if l not in want and l in have]
    try:
        if add: state.binding.api("-X", "POST", f"repos/{state.binding.repo}/issues/{n}/labels", "--input", "-", inp=json.dumps({"labels": add}))
        for l in drop: state.binding.drop_label(n, l)
    except RuntimeError as e: print(f"#{n}: {e}", file=sys.stderr); sys.exit(1)
    after = state.binding.issue_facts(n)
    held = {"unproven", "proof:red"} & set(after[1] if after else ())
    if after is None or held != want:
        print(f"#{n}: the issue does not carry the proof fact {word!r} after the write; a read failure, not a written fact", file=sys.stderr); sys.exit(1)
    (st / "board-dirty").touch(); print(f"#{n} proof {word}"); sys.exit(0)
if run_fields or clear_run:
    n = int(args[2])
    run = None if clear_run else args[3]; started = None if clear_run else (args[4] if len(args) > 4 else "")
    try: state.release(n) if clear_run else state.hold(n, run, started)   # the seam writes the holder, then the next read proves it (#241)
    except ReadFailure as e: print(f"#{n}: {e}", file=sys.stderr); sys.exit(1)
    except NotCarried as e: print(f"#{n}: {e}", file=sys.stderr); sys.exit(1)   # named on stderr, never swallowed: the caller logs what it was given
    (st / "board-dirty").touch(); sys.exit(0)
if remove:
    rc = 0
    for n in map(int, args[2:]):
        try: iid = state.binding.item_id(n)
        except ReadFailure as e: print(f"#{n}: {e}", file=sys.stderr); rc = 1; continue
        if not iid: print(f"#{n} not on the board (nothing removed)"); continue   # no card: the board already holds the wanted value, so no write
        try: state.binding.api("-X", "DELETE", f"{state.binding.base}/items/{iid}")
        except RuntimeError as e: print(f"#{n}: {e}", file=sys.stderr); rc = 1; continue
        state.binding.items_map.pop(str(n), None); state.binding._save_items()
        print(f"#{n} removed from the board")
    (st / "board-dirty").touch(); sys.exit(rc)
status, nums = args[2], [int(x) for x in args[3:]]
F = state.binding.status_field()
if option_name(status, F["opt"]) is None: (st / f"rest-fields-{owner}-{num}.json").unlink(missing_ok=True); F = state.binding.status_field()
carried_name = option_name(status, F["opt"])   # the spelling the board carries for the column: either one resolves (#2151)
if carried_name is None: sys.exit(f"no Status option {status!r}")
tries = max(1, state.binding.attempts - 1)   # a write has one attempt fewer than the bound: the compare is one of the reads SG_BOARD_READ_ATTEMPTS bounds (#327)
rc = 0; proved = []; transitions = []; refusals = []   # one card, one node read: no whole-board read answers the compare or the proof (#1300)
automated = os.environ.get("SG_MOVE_BY") != "owner"   # any writer that is not the owner's door is automated (ADR-0143)
by = os.environ.get("SG_MOVE_BY") or None   # the dispatch context the caller names; a move with none records none (#861)
pss = os.environ.get("SG_MOVE_PASS") or None   # the caller's pass, kept by board.sh (#1909)
reason = os.environ.get("SG_MOVE_REASON") or None
def recorded():   # issue -> the column the record's last line names: the value the dispatchers read (#2123)
    t = st / f"board-transitions.{(state.binding.repo or 'board').replace('/', '_')}.jsonl"
    try: rows = t.read_text().splitlines()
    except OSError: return {}
    last = {}
    for line in rows:
        try: d = json.loads(line)
        except ValueError: continue
        if isinstance(d, dict) and d.get("issue") is not None and d.get("to"): last[str(d["issue"])] = d["to"]
    return last
def reset_book():   # -> (posted marks, reset label each issue carries now by "repo#n|label"): what the resets wrote, kept whole in one file (#1904)
    try: d = json.loads((st / "board-resets.json").read_text())
    except (OSError, ValueError): d = {}
    if not isinstance(d, dict): d = {}
    return set(d.get("posted", [])), dict(d.get("active", {}))
def ensure_label(label):   # the reset label exists in the repository before any issue carries it (#1904)
    try: state.binding.api("-X", "POST", f"repos/{state.binding.repo}/labels", "-f", f"name={label}", "-f", "color=ededed")
    except RuntimeError as e:
        if "already_exists" not in str(e): raise
def save_reset_book(posted, active):   # whole, then one rename (ADR-0030), after each step so a failed step never loses a done one
    p = st / "board-resets.json"; tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"posted": sorted(posted), "active": active}, sort_keys=True) + "\n"); os.replace(tmp, p)
def drop_reset_labels(posted, active, card, n):   # a forward move answers every reset label the card carries; the book forgets each one it drops
    for slot, label in list(active.items()):
        if not slot.startswith(f"{card}|"): continue
        try: state.binding.drop_label(n, label)
        except RuntimeError as e: print(f"#{n}: the reset label {label!r} was not dropped: {e}", file=sys.stderr); continue
        del active[slot]; save_reset_book(posted, active)
def settle_reset(posted, active, repo, n, label, have, status, at, event, sha):   # the comment, then the label, each marked once it lands (#1904)
    card = f"{repo}#{n}"; mark = "|".join((card, label, event, sha)); slot = f"{card}|{label}"
    try: ensure_label(label)
    except RuntimeError as e: print(f"#{n}: the reset label {label!r} was not created: {e}", file=sys.stderr); return
    if f"{mark}|comment" not in posted:
        try: state.binding.api("-X", "POST", f"repos/{repo}/issues/{n}/comments", "--input", "-", inp=json.dumps({"body": reset_comment(have, status, reason, at, event)}))
        except RuntimeError as e: print(f"#{n}: the reset comment failed: {e}", file=sys.stderr); return
        posted.add(f"{mark}|comment"); save_reset_book(posted, active)
    if f"{mark}|label" in posted: return
    try: state.binding.api("-X", "POST", f"repos/{repo}/issues/{n}/labels", "-f", f"labels[]={label}")
    except RuntimeError as e: print(f"#{n}: the reset label {label!r} was not applied: {e}", file=sys.stderr); return
    posted.add(f"{mark}|label"); active[slot] = label; save_reset_book(posted, active)
def settle_resets(moved, at):   # a reset names its move on the issue; a forward move answers the reset labels the issue carries (#1904)
    repo = state.binding.repo or "board"
    event = os.environ.get("SG_MOVE_EVENT") or ""
    sha = os.environ.get("SG_MOVE_SHA") or ""   # the sha the event happened at, named by its writer: a re-run after a new commit names the same one
    with (st / "board-resets.lock").open("w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)   # one writer reads and writes the book at a time (ADR-0030)
        try:
            posted, active = reset_book()
            for n, have, status in moved:
                if not have: continue
                if reason in RESET_LABEL and past(have, status):
                    if not event or not sha:
                        print(f"#{n}: the reset note needs SG_MOVE_EVENT and SG_MOVE_SHA; none written", file=sys.stderr); continue
                    settle_reset(posted, active, repo, n, RESET_LABEL[reason], have, status, at, event, sha)
                elif not past(have, status):
                    drop_reset_labels(posted, active, f"{repo}#{n}", n)
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)
was = recorded()
moved = []
for n in nums:
    read_failed = None
    try: have = state.read_one(n) or None   # the compare: the card's own read, and no PATCH when it already carries the Status (#327)
    except (RuntimeError, ValueError) as e: have = None; read_failed = str(e)[:200] or "the read did not answer"   # an automated writer that cannot read the card never moves it to Todo (#1909)
    if canonical(have) == canonical(status):
        if was.get(str(n)) not in (None, status):   # the record and the carrier disagree: the compare records the carrier's column (#2123)
            transitions.append({"issue": n, "item": None, "from": was[str(n)], "to": status, "by": by, "pass": pss, "reason": "the carrier already carried it"})
        print(f"#{n} -> {status} (already carried, not written)"); continue
    if automated and read_failed and status == "Todo":
        print(f"#{n}: the compare read failed ({read_failed}); an automated writer does not move the card to 'Todo' unread", file=sys.stderr); rc = 1; continue
    if canonical(status) == "Todo" and canonical(have) == "Backlog" and todo_full():   # a full Todo holds the Backlog to Todo move only (#2437)
        refusals.append({"issue": n, "from": have, "to": status, "by": by, "pass": pss, "reason": "the Todo card limit", "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        print(f"#{n}: refused, the move from {have!r} to 'Todo' passes the Todo limit of {_TODO['max']} card(s); the card keeps {have!r} (#2437)", file=sys.stderr); continue
    if automated and refused_for_automated(have, status, reason):   # a move back along the axis: the card keeps its column and carries rework (ADR-0143)
        refusals.append({"issue": n, "from": have, "to": status, "by": by, "pass": pss, "reason": reason, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        if (have, status) == HOLD:   # the owner starts a card from Backlog: a hold is named, never a rework (#1909)
            print(f"#{n}: held, an automated writer does not move {have!r} to {status!r}; only the owner starts a card (ADR-0142)", file=sys.stderr); continue
        print(f"#{n}: refused, an automated writer moves {have!r} back to {status!r}; the card keeps {have!r} and carries rework", file=sys.stderr)
        if state.binding.repo:
            try: state.binding.api("-X", "POST", f"repos/{state.binding.repo}/issues/{n}/labels", "-f", "labels[]=rework")
            except RuntimeError as e: print(f"#{n}: the rework label write failed: {e}", file=sys.stderr)
        continue
    try: iid = state.binding.item_id(n, add)
    except ReadFailure as e: print(f"#{n}: {e}", file=sys.stderr); rc = 1; continue   # the card is on the board and the read has not caught up: named, never "not on the board"
    except RuntimeError as e: print(f"#{n}: {e}", file=sys.stderr); rc = 1; continue   # the add GitHub refused twice, named: the caller logs it
    if not iid: print(f"#{n} not on the board", file=sys.stderr); rc = 1; continue
    refused, written, last, reads = "", False, None, tries
    for k in range(tries):   # the write is written again, then read back: a PATCH answered ok can be a write the card never carries (#105)
        if k and state.binding.backoff: time.sleep(state.binding.backoff * (2 ** (k - 1)))
        try:
            state.write(n, carried_name)   # the option the board carries for the column, either spelling (#2151)
            written = True
            break
        except NotCarried as e:
            last = e
            refused = e.refused or refused   # the seam carries the refusal it was given: the note names it, the read is the verdict
            if e.refused:   # a refused write can be an option id the cache kept and the board dropped or recreated: the next attempt reads the field again (#311)
                (st / f"rest-fields-{owner}-{num}.json").unlink(missing_ok=True)
                if option_name(status, state.binding.status_field()["opt"]) is None: sys.exit(f"no Status option {status!r}")
    if not written and last is not None and not last.refused:   # GitHub accepted the write and the read is behind it -- no Status yet (#403) or the old one (#434): read again within the settle bound, never written again
        def probe(n=n):
            global reads
            reads += 1
            try: return canonical(state.read_one(n)) == canonical(status)
            except (RuntimeError, ValueError): return False
        old = have is not None and canonical(have) != canonical(status)   # the card carried another Status before the write: a write it never carries is more likely lost than late, so its settle is capped (#438)
        cap = float(os.environ.get("SG_BOARD_OLD_SETTLE_SECONDS", "10")) if old else None
        written = bool(state.binding.settle(probe, tries, cap))
    if written:
        if refused: print(f"#{n}: {refused}; the card carries {status} and the read proves the write", file=sys.stderr)   # the note, never the verdict
        # the record names the column, never the option's spelling (#2315)
        print(f"#{n} -> {status}"); proved.append((iid, canonical(status))); moved.append((n, have, status)); transitions.append({"issue": n, "item": iid, "from": have, "to": canonical(status), "by": by, "pass": pss, "reason": reason})
        if canonical(status) == "Todo" and _TODO["now"] is not None: _TODO["now"] += 1   # the count this pass holds follows each Todo move it proves (#2437)
    else:
        state.binding.items_map.pop(str(n), None); state.binding._save_items()   # a card whose write is unproven is never cached as written
        print(f"#{n}: the board carries no {status!r} on it after {reads} reads" + (f" ({refused})" if refused else "") + "; a read failure, not a written card", file=sys.stderr); rc = 1
at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
if moved: settle_resets(moved, at)
if proved:   # the record and the refresh flag are one step: one move, one write path, one wake-up (#649)
    p = st / "board-moves.json"
    try: moves = json.loads(p.read_text())
    except (OSError, ValueError): moves = {}
    if not isinstance(moves, dict): moves = {}
    for iid, col in proved: moves[str(iid)] = {"column": col, "at": at, **({"by": by} if by else {})}   # a later move of the same item replaces its entry; the key is text, as the record reads back (#652)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")   # formed whole beside the record, then one atomic rename (ADR-0030)
    tmp.write_text(json.dumps(moves, sort_keys=True) + "\n"); os.replace(tmp, p)
if transitions:   # a move the door proved, and the compare's repair of a record that disagreed with the carrier (#2123)
    repo = state.binding.repo or "board"
    t = st / f"board-transitions.{repo.replace('/', '_')}.jsonl"   # one record per repository: two repositories on one board never share it (#897)
    try: lines = t.read_text().splitlines()
    except OSError: lines = []
    lines += [json.dumps({**m, "at": at, "repo": state.binding.repo}, sort_keys=True) for m in transitions]
    tmp = t.with_name(f"{t.name}.{os.getpid()}.tmp")   # the whole record is rewritten beside itself and renamed: a reader never sees a part-written line (ADR-0030)
    tmp.write_text("\n".join(lines) + "\n"); os.replace(tmp, t)
if refusals:   # the refused moves, appended whole beside the record and renamed (ADR-0030); the exit 3 names them to the caller
    r = st / "board-rejections.jsonl"
    try: lines = r.read_text().splitlines()
    except OSError: lines = []
    lines += [json.dumps(x, sort_keys=True) for x in refusals]
    tmp = r.with_name(f"{r.name}.{os.getpid()}.tmp")
    tmp.write_text("\n".join(lines) + "\n"); os.replace(tmp, r)
(st / "board-dirty").touch(); sys.exit(1 if rc else 3 if refusals else 0)
