#!/usr/bin/env python3
"""Move Project (v2) cards over REST only (no GraphQL quota): board.py <owner> <project> "<Status>" <issue#>...
       board.py --add <owner> <project> "<Status>" <issue#>...   add the issues to the board first (when missing); idempotent: a refused add is re-read once
       board.py --remove <owner> <project> <issue#>...   take the issues off the board; an issue with no card is skipped, never written (#500)
       board.py --run-fields <owner> <project> <issue#> <run> <run-started>
       board.py --clear-run <owner> <project> <issue#>
       board.py --test-case <owner> <project> <issue#> <pass|fail> <merge-commit> [ended]   record a finished test pass as one `test case` item (#300)
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
A move to the Status the card already carries is skipped: one read of the board, no PATCH, no proving read (#327).
Touches $SG_STATE/board-dirty so the hub refreshes within 2 s. Each move it proved is recorded in
$SG_STATE/board-moves.json by that same step: one entry per item, the column it set, the instant and the dispatch context `SG_MOVE_BY` names (`owner` or `loop`, when set), replaced by a
later move of the same item. The file is formed in a temp file beside it and renamed, so a reader sees the old record
or the new one, never a part-written one (ADR-0030). A skipped or unproven move records nothing, and a holder or
test-case write records nothing."""
import json, os, pathlib, sys, time
from issue_state import KindGap, NotCarried, ReadFailure, board_state
args = sys.argv[1:]; add = args[:1] == ["--add"]; run_fields = args[:1] == ["--run-fields"]; clear_run = args[:1] == ["--clear-run"]; test_case = args[:1] == ["--test-case"]; remove = args[:1] == ["--remove"]
args = args[1:] if add or run_fields or clear_run or test_case or remove else args
owner, num = args[0], args[1]
st = pathlib.Path(os.environ["SG_STATE"])
state = board_state(owner=owner, project=num, kind=None)   # the move is asked for an owner the caller names, so the carrier says whether it is a user or an org (cached a month)
if test_case:
    n = int(args[2])
    try: item = state.record_pass(n, args[3], args[4], args[5] if len(args) > 5 else None)   # one item, then the read that proves it (#300)
    except (ValueError, KindGap, NotCarried, ReadFailure) as e: print(f"#{n}: {e}", file=sys.stderr); sys.exit(1)
    print(f"#{n} test case {item['result']} @ {item['commit']}"); (st / "board-dirty").touch(); sys.exit(0)
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
if status not in F["opt"]: (st / f"rest-fields-{owner}-{num}.json").unlink(missing_ok=True); F = state.binding.status_field()
if status not in F["opt"]: sys.exit(f"no Status option {status!r}")
try: carried, tries = state.read_all(), max(1, state.binding.attempts - 1)   # one read of the board for the whole move: a card that already carries the Status is not written (#327). It is one of the reads SG_BOARD_READ_ATTEMPTS bounds, so a write has one attempt fewer
except (RuntimeError, ValueError): carried, tries = {}, state.binding.attempts   # a board that does not answer the read is written on, never blocked by it
rc = 0; proved = []
for n in nums:
    if carried.get(str(n)) == status: print(f"#{n} -> {status} (already carried, not written)"); continue   # no PATCH and no proving read: the read above is the proof
    try: iid = state.binding.item_id(n, add)
    except ReadFailure as e: print(f"#{n}: {e}", file=sys.stderr); rc = 1; continue   # the card is on the board and the read has not caught up: named, never "not on the board"
    except RuntimeError as e: print(f"#{n}: {e}", file=sys.stderr); rc = 1; continue   # the add GitHub refused twice, named: the caller logs it
    if not iid: print(f"#{n} not on the board", file=sys.stderr); rc = 1; continue
    refused, written, last, reads = "", False, None, tries
    for k in range(tries):   # the write is written again, then read back: a PATCH answered ok can be a write the card never carries (#105)
        if k and state.binding.backoff: time.sleep(state.binding.backoff * (2 ** (k - 1)))
        try:
            state.write(n, status)
            written = True
            break
        except NotCarried as e:
            last = e
            refused = e.refused or refused   # the seam carries the refusal it was given: the note names it, the read is the verdict
            if e.refused:   # a refused write can be an option id the cache kept and the board dropped or recreated: the next attempt reads the field again (#311)
                (st / f"rest-fields-{owner}-{num}.json").unlink(missing_ok=True)
                if status not in state.binding.status_field()["opt"]: sys.exit(f"no Status option {status!r}")
    if not written and last is not None and not last.refused:   # GitHub accepted the write and the read is behind it -- no Status yet (#403) or the old one (#434): read again within the settle bound, never written again
        def probe(n=n):
            global reads
            reads += 1
            try: return state.read(n) == status
            except (RuntimeError, ValueError): return False
        old = carried.get(str(n)) not in (None, "", status)   # the card carried another Status before the write: a write it never carries is more likely lost than late, so its settle is capped (#438)
        cap = float(os.environ.get("SG_BOARD_OLD_SETTLE_SECONDS", "10")) if old else None
        written = bool(state.binding.settle(probe, tries, cap))
    if written:
        if refused: print(f"#{n}: {refused}; the card carries {status} and the read proves the write", file=sys.stderr)   # the note, never the verdict
        print(f"#{n} -> {status}"); proved.append((iid, status))
    else:
        state.binding.items_map.pop(str(n), None); state.binding._save_items()   # a card whose write is unproven is never cached as written
        print(f"#{n}: the board carries no {status!r} on it after {reads} reads" + (f" ({refused})" if refused else "") + "; a read failure, not a written card", file=sys.stderr); rc = 1
if proved:   # the record and the refresh flag are one step: one move, one write path, one wake-up (#649)
    p = st / "board-moves.json"
    try: moves = json.loads(p.read_text())
    except (OSError, ValueError): moves = {}
    if not isinstance(moves, dict): moves = {}
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    by = os.environ.get("SG_MOVE_BY") or None   # the dispatch context the caller names; a move with none records none (#861)
    for iid, col in proved: moves[str(iid)] = {"column": col, "at": at, **({"by": by} if by else {})}   # a later move of the same item replaces its entry; the key is text, as the record reads back (#652)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")   # formed whole beside the record, then one atomic rename (ADR-0030)
    tmp.write_text(json.dumps(moves, sort_keys=True) + "\n"); os.replace(tmp, p)
(st / "board-dirty").touch(); sys.exit(rc)
