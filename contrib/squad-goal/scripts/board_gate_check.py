#!/usr/bin/env python3
"""The real-project check of a card's Testing path (SPEC 1218, #1258).

One disposable card is taken through Todo -> Doing -> Batching -> Testing -> Closed. This reader proves that path
from the stores the live loop keeps, never from a fixture:

  path                 the card's column intervals run forward through the five columns, in the board's own axis order;
  gating_live          a Testing stretch is closed by a later column, and it overlaps a gate the card's wave ran;
  gate_tries           the card's live Gate Tries number equals the distinct attempts its journal holds;
  timing               the Testing entry and exit instants agree with the gate's own log (gate-history): the card entered
                       Testing no later than the first attempt's start plus the board's settle bound, and it left no
                       earlier than the last verdict and no later than that verdict plus the settle bound; the
                       lead-time reader's `gate time` equals the history's Testing hours;
  ended_closed         the live board carries the card closed in Closed;
  no_backward_movement no proved move of any card since the card entered Todo went back along the axis.

Inputs are plain files: the live board cache (hub-board.json), the history store (hub-history.<owner>_<name>.jsonl), the
attempt journal (hub-flow.<owner>_<name>.jsonl), the gate's own log ($SG_STATE/gate-history) and the proved moves
($SG_STATE/board-transitions.<owner>_<name>.jsonl). The check reads and never writes; taking a disposable card through
the path is a live write of its own and needs the owner's grant (ADR-0024).

usage: board_gate_check.py --issue <n> [--repo <owner/name>] [--wave <run>] [--since <ISO instant>] [--json]
       --board <hub-board.json> --history <store> --journal <journal> --gate-log <gate-history> --transitions <moves>
       board_gate_check.py --record-guard [--since <ISO instant>] [--json] [--board <hub-board.json>]
       [--history <store>] [--journal <journal>]
       The board cache is --board, else SG_BOARD_CACHE: the live profile's own place. The history store and the
       attempt journal sit beside it, so --history and --journal default to the paths derived from it; a named
       --history or --journal wins. The gate log and the proved moves default to SG_STATE, else /tmp/sg-state.
       `--record-guard` reads no board cache, gate log or proved moves: it accepts --board (or SG_BOARD_CACHE) only
       as the file both of its stores are derived from.
Exit: 0 pass, 1 a check failed, 2 an input could not be read (including no path named for one of the record-guard's
two stores, and a history store that holds no record for the named card).

`--record-guard` is the owner's guard (#1430): it compares the attempt journal with the history store and exits 1 naming
every attempt whose card never read Testing. It runs beside the board checks and reads those two stores only, so it
answers the same after a restart and on a re-read; a failure is a defect, never a warning.
"""
import argparse
import json
import os
import pathlib
import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import board_columns
import board_history
import board_leadtime
import gate_attempts

GATING = board_columns.ROLE["gating"]
TODO = board_columns.ROLE["issue_todo"]
CLOSED = board_columns.ROLE["issue_closed"]
PATH_COLUMNS = (TODO, board_columns.DOING, board_columns.BATCHING, GATING, CLOSED)   # the definition's names (#2155, #2190)
SETTLE_SECONDS = 900   # how late a board poll may record a Status change against the gate's own instant
MATCH_SECONDS = 120    # how close an attempt's finish and a gate-history line's finish are one gate run
HOUR = 3600.0


def _instant(text):
    """One recorded timestamp as a UTC instant: a zoneless record is UTC, an offset converts (board_history's rule)."""
    if text is None: return None
    if isinstance(text, (int, float)): return datetime.fromtimestamp(text, UTC)
    moment = datetime.fromisoformat(str(text))
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def _stamp(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ") if moment is not None else "?"


def _axis():
    """The shipped columns in the definition's order: the board's one axis, read by every other column rule (ADR-0143)."""
    return [s["name"] for s in board_columns.shipped_columns()]


def read_log(path):
    """The gate's own log lines: finish, kind, result, sha, start, and the outcome (the gate runner writes thirteen fields)."""
    path = pathlib.Path(path)
    if not path.exists(): return []
    out = []
    for line in path.read_text().splitlines():
        f = line.split("\t")
        if len(f) < 5: continue
        try: finish, start = int(f[0]), int(f[4])
        except ValueError: continue
        out.append({"finish": finish, "start": start, "kind": f[1], "result": f[2], "sha": f[3],
                    "outcome": f[7] if len(f) > 7 else ""})
    return out


def read_transitions(path):
    path = pathlib.Path(path)
    if not path.exists(): return None
    out = []
    for line in path.read_text().splitlines():
        if not line.strip(): continue
        try: rec = json.loads(line)
        except ValueError: continue
        if isinstance(rec, dict): out.append(rec)
    return out


def item_of(board, repo, issue):
    """The live board's item for one card: its number, and the repository when the row names one."""
    for item in board.get("items") or []:
        try: number = int(item.get("n", -1))
        except (TypeError, ValueError): continue
        if number != issue: continue
        if repo and item.get("r") and item["r"] != repo: continue
        return item
    return None


def gating_stretches(runs):
    """Every contiguous stretch one card spends in Testing, with its instants and hours. A freeze or a spec change ends
    an interval without changing the column, so contiguous runs of the same column merge (SPEC 1218)."""
    out, joined = [], False
    for run in runs:
        if run["on_board"] and run["column"] == GATING:
            if joined and out:
                out[-1]["to"] = run["to"]
            else:
                out.append({"from": run["from"], "to": run["to"]})
            joined = True
        else:
            joined = False
    for stretch in out:
        a, b = _instant(stretch["from"]), _instant(stretch["to"])
        stretch["from_t"], stretch["to_t"] = a, b
        stretch["hours"] = (b - a).total_seconds() / HOUR if a is not None and b is not None else None
    return out


def _card_of(rec):
    try: return int(rec.get("n", -1))
    except (TypeError, ValueError): return -1


def _same_tip(a, b):
    a, b = str(a or ""), str(b or "")
    return bool(a and b) and (a == b or a.startswith(b) or b.startswith(a))


def gate_windows(attempts, log):
    """(windows, unmatched): one gate log line per attempt, matched by tip and a finish instant close to the attempt's
    own. An attempt the log does not answer is reported, never guessed."""
    windows, unmatched = [], []
    for rec in attempts:
        try: finish = int(rec.get("t") or 0)
        except (TypeError, ValueError): finish = 0
        hits = [line for line in log if _same_tip(rec.get("tip"), line["sha"]) and abs(line["finish"] - finish) <= MATCH_SECONDS]
        if not hits:
            unmatched.append(rec)
            continue
        windows.append(min(hits, key=lambda line: abs(line["finish"] - finish)))
    return windows, unmatched


def _covers_gating(run, at, settle):
    """True when one history interval is a Testing stretch that covers the attempt's instant: the interval holds it, its
    entry was recorded no later than the settle bound after it, and it was left no earlier than the settle bound before
    it. The poll may record either end late or early, so the same bound widens both ends."""
    if not run["on_board"] or run["column"] != GATING: return False
    entered, left = _instant(run["from"]), _instant(run["to"])
    return entered is not None and entered <= at + settle and (left is None or left >= at - settle)


def _closed_before(runs, at, settle):
    """True when one Closed interval says the card was already in dev when the gate ran: the interval opened at least
    the settle bound before the attempt and still held the card at it. A Closed entry that arrives within the bound of
    the attempt is the poll recording a landing around the gate, so it settles nothing, and a Closed interval that ended
    before the attempt means the card left dev again (a reopen)."""
    for run in runs:
        if not run["on_board"] or run["column"] != CLOSED: continue
        opened, left = _instant(run["from"]), _instant(run["to"])
        if opened is not None and opened + settle <= at and (left is None or left >= at):
            return True
    return False


def _wave_cards(journal):
    """{wave: {card}} for the waves the journal's own per-card attempts name (#1430). A wave's entry gate journals an
    attempt for the child it admits (wave.sh names SG_GATE_CARD for each), so a wave-level attempt (the tip gate, card 0)
    is read on the cards that journal names for its wave. A wave no attempt names is skipped: the guard reads the journal
    and the store only."""
    out = {}
    for rec in gate_attempts.attempts(journal) if journal else []:
        wave, n = str(rec.get("w") or ""), _card_of(rec)
        if wave and n > 0: out.setdefault(wave, set()).add(n)
    return out


def gating_recorded(journal, store, since=None):
    """The journal-versus-history guard (#1430): (ok, problems).

    Every gate attempt the journal holds, whose card is ahead of dev, leaves a Testing stretch on the board. The attempt
    record carries one instant, the moment it drew its verdict; the card's stretch covers it when the history store's
    Testing interval holds that instant. The board's poll may record either end of the interval late or early, so an
    interval within SETTLE_SECONDS of the attempt still covers it. A card is in dev when a Closed interval opened at least
    SETTLE_SECONDS before the attempt and still held it: the gate re-ran on landed work, and no Testing stretch is expected.
    A wave-level attempt (card 0)
    is read on the cards the journal names for its wave, so a real wave is never waived. The guard reads the journal and
    the store only, so it answers the same after a restart and on a re-read."""
    recs = gate_attempts.attempts(journal) if journal else []
    wave_cards = _wave_cards(journal)
    settle = timedelta(seconds=SETTLE_SECONDS)
    problems = []
    for rec in recs:
        n = _card_of(rec)
        cards = [n] if n > 0 else sorted(wave_cards.get(str(rec.get("w") or ""), ()))
        if not cards: continue   # a wave-level attempt no card of the journal names: no card to read
        try: at = datetime.fromtimestamp(int(rec.get("t") or 0), UTC)
        except (TypeError, ValueError): continue
        if since is not None and at < since: continue
        for card in cards:
            runs = board_history.intervals((store.get(card) or store.get(str(card)) or {}).get("events") or [])
            if _closed_before(runs, at, settle): continue
            if not any(_covers_gating(r, at, settle) for r in runs):
                problems.append(f"#{card} attempt {rec.get('tip') or '-'}/{rec.get('aid') or '-'} at {_stamp(at)}: the card never read {GATING}")
    return not problems, problems


def lead_row(item, runs, rec, now):
    """The lead-time row the chart reads for this card, from its history alone: the hub's own assembly (hub.lead_rows)."""
    obs = board_history.lead_observation(runs, start=TODO, complete=bool(rec.get("complete")), now=now)
    if obs is None: return None, None
    return obs, {"n": item.get("n"), "column": board_columns.view_column(item.get("st"), str(item.get("s", "")).lower() == "closed",
                                                                          item.get("l"), item.get("cr"))[0],
                 "reason": None, "c": item.get("c") or obs["end"], "o": item.get("o") or obs["start"], "t": item.get("t", ""),
                 "hours": obs["flowing"] + obs["held"], "flowing": obs["flowing"], "held": obs["held"],
                 "approx": obs["approx"], "unknown_first": obs["unknown_first"],
                 "columns": obs["columns"], "gate": obs["gate"]}


def check(board, store, journal, log, transitions, repo, issue, wave=None, since=None, now=None):
    """The six checks, in order: [{"name", "ok", "detail"}]."""
    checks = []

    def add(name, ok, detail):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    item = item_of(board, repo, issue)
    rec = store.get(issue) or store.get(str(issue)) or {}
    runs = board_history.intervals(rec.get("events") or []) if rec else []

    # 1. path: the five columns, forward along the board's axis.
    axis = _axis()
    order = {name: i for i, name in enumerate(axis)}
    seq = [board_columns.recorded_column(r["column"]) for r in runs if r["on_board"] and board_columns.recorded_column(r["column"]) in order]   # history keeps the names it recorded (#2190)
    first_order = [order[name] for name in dict.fromkeys(seq)]
    present = all(name in seq for name in PATH_COLUMNS)
    ordered = first_order == sorted(first_order)
    forward = all(order[b] >= order[a] for a, b in zip(seq, seq[1:]))
    path_ok = present and ordered and forward
    add("path", path_ok,
        f"columns {' -> '.join(seq) or 'none'}; the five columns present: {'yes' if present else 'no'}; in axis order: {'yes' if ordered else 'no'}; forward only: {'yes' if forward else 'no'}")

    # 2. gating_live: a closed Testing stretch that overlaps a gate the card's wave ran.
    stretches = gating_stretches(runs)
    closed_stretches = [s for s in stretches if s["to_t"] is not None and s["hours"] is not None]
    all_attempts = gate_attempts.attempts(journal) if journal else []
    mine = [r for r in all_attempts if _card_of(r) == issue]
    # The wave's landing gate journals card 0: when the caller names the wave, its attempts are the card's gate too.
    landing = [r for r in all_attempts if wave and str(r.get("w") or "") == str(wave) and _card_of(r) == 0]
    timing_attempts = mine + landing
    windows, unmatched = gate_windows(timing_attempts, log)
    overlap = [s for s in closed_stretches
               if windows and s["from_t"] <= datetime.fromtimestamp(max(w["finish"] for w in windows), UTC)
               and s["to_t"] >= datetime.fromtimestamp(min(w["start"] for w in windows), UTC)]
    gating_ok = bool(overlap) and bool(windows)
    add("gating_live", gating_ok,
        f"Testing stretches {len(closed_stretches)} of {len(stretches)}; gate runs read {len(windows)}; a stretch overlaps a run: {'yes' if overlap else 'no'}")

    # 3. gate_tries: the live field is the journal's projection.
    try: tries = int(item.get("gt") or 0) if item else 0
    except (TypeError, ValueError): tries = 0
    drawn = gate_attempts.counts(journal).get(issue, 0) if journal else 0
    add("gate_tries", item is not None and tries == drawn,
        f"the board reads {tries} on #{issue}; the journal holds {drawn} attempt(s)")

    # 4. timing: entry and exit against the gate's own log, and gate time against the lead-time reader.
    if not stretches:
        add("timing", False, "the card never reads Testing in the history store")
    elif not windows:
        detail = "the gate's own log answers no attempt" + (f": {unmatched}" if unmatched else "")
        add("timing", False, detail)
    elif not closed_stretches:
        add("timing", False, "the Testing stretch is still open: the card has not left Testing")
    else:
        entry, exit_at = closed_stretches[0]["from_t"], closed_stretches[-1]["to_t"]
        first_start = datetime.fromtimestamp(min(w["start"] for w in windows), UTC)
        last_finish = datetime.fromtimestamp(max(w["finish"] for w in windows), UTC)
        settle = timedelta(seconds=SETTLE_SECONDS)
        entry_ok = entry <= first_start + settle
        exit_ok = last_finish <= exit_at <= last_finish + settle
        hours = sum(s["hours"] for s in closed_stretches)
        obs, row = lead_row(item or {"n": issue}, runs, rec, now)
        chart = None
        if row is not None:
            series = board_leadtime.lead_time([row], now=now)["series"]
            chart = series[0]["gate_hours"] if series else None
        chart_ok = chart is not None and obs is not None and abs(chart - hours) <= 1e-6
        ok = entry_ok and exit_ok and chart_ok
        add("timing", ok,
            f"Testing entry {_stamp(entry)} from a gate start {_stamp(first_start)} (+{SETTLE_SECONDS}s bound): {'ok' if entry_ok else 'late'}; "
            f"exit {_stamp(exit_at)} from the verdict {_stamp(last_finish)} (+{SETTLE_SECONDS}s bound): {'ok' if exit_ok else 'out of bounds'}; "
            f"gate time {hours:.4f}h from the history and {chart if chart is not None else '?'}h from the lead-time chart")

    # 5. ended_closed: the live board carries the card delivered.
    ended = item is not None and str(item.get("s", "")).lower() == "closed" \
        and board_columns.view_column(item.get("st"), True, item.get("l"), item.get("cr"))[0] == CLOSED
    add("ended_closed", ended,
        f"live board: {('#' + str(item.get('n')) + ' ' + str(item.get('st')) + '/' + str(item.get('s'))) if item else 'the card is not on the board'}")

    # 6. no_backward_movement: every proved move since the card entered Todo stays forward.
    if transitions is None:
        add("no_backward_movement", False, "no board-transitions record was read; backward movement cannot be ruled out")
    else:
        start = since or (_instant(runs[0]["from"]) if runs else None)
        moves = [m for m in transitions if start is None or (_instant(m.get("at")) or start) >= start]
        back = [m for m in moves if m.get("from") and m.get("to") and board_columns.past(m["from"], m["to"])]
        def _is_card(m):
            try: return int(m.get("issue", -1)) == issue
            except (TypeError, ValueError): return False
        named = [m for m in moves if _is_card(m)]
        ok = bool(named) and not back
        detail = f"{len(moves)} proved move(s) since {_stamp(start)}; {len(named)} of this card; backward: " + \
                 (", ".join(f"#{m.get('issue')} {m.get('from')}->{m.get('to')}" for m in back) if back else "none")
        if not named: detail += "; the card's own moves are not in the record"
        add("no_backward_movement", ok, detail)
    return checks


def main(argv=None):
    ap = argparse.ArgumentParser(description="The real-project check of a card's Testing path (SPEC 1218, #1258).")
    ap.add_argument("--issue", type=int)
    ap.add_argument("--repo")
    ap.add_argument("--wave")
    ap.add_argument("--since")
    ap.add_argument("--board")
    ap.add_argument("--history")
    ap.add_argument("--journal")
    ap.add_argument("--gate-log")
    ap.add_argument("--transitions")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--record-guard", action="store_true",
                    help="compare the whole attempt journal with the history store: exit 1 naming every attempt whose card never read Testing")
    a = ap.parse_args(argv)
    state = pathlib.Path(os.environ.get("SG_STATE") or "/tmp/sg-state")
    board_path = a.board or os.environ.get("SG_BOARD_CACHE")
    repo = a.repo or os.environ.get("SG_REPO") or ""
    if a.record_guard:   # the guard reads the journal and the store only: no board cache, no gate log, no transitions are read
        if not a.history and not board_path:
            print("board_gate_check: no history store: name --history or SG_BOARD_CACHE", file=sys.stderr)
            return 2
        if not a.journal and not board_path:
            print("board_gate_check: no attempt journal: name --journal or SG_BOARD_CACHE", file=sys.stderr)
            return 2
        history_path = pathlib.Path(a.history) if a.history else board_history.history_path(pathlib.Path(board_path), repo)
        journal_path = pathlib.Path(a.journal) if a.journal else gate_attempts.journal_path(pathlib.Path(board_path), repo)
        ok, problems = gating_recorded(journal_path, board_history.load_store(history_path),
                                       since=_instant(a.since) if a.since else None)
        if a.json:
            print(json.dumps({"ok": ok, "problems": problems}, indent=2, sort_keys=True))
        else:
            for problem in problems: print(f"FAIL gating_recorded: {problem}")
            print(f"gating record guard: {'pass' if ok else 'fail'}")
        return 0 if ok else 1
    if not a.issue:
        print("board_gate_check: name --issue, or --record-guard for the journal-versus-history guard", file=sys.stderr)
        return 2
    if not board_path:
        print("board_gate_check: no board cache: name --board or SG_BOARD_CACHE", file=sys.stderr)
        return 2
    board_path = pathlib.Path(board_path)
    history_path = pathlib.Path(a.history) if a.history else board_history.history_path(board_path, repo)
    journal_path = pathlib.Path(a.journal) if a.journal else gate_attempts.journal_path(board_path, repo)
    log_path = pathlib.Path(a.gate_log) if a.gate_log else state / "gate-history"
    transitions_path = pathlib.Path(a.transitions) if a.transitions else \
        state / f"board-transitions.{repo.replace('/', '_')}.jsonl"
    try:
        board = json.loads(board_path.read_text())
    except (OSError, ValueError) as e:
        print(f"board_gate_check: the live board cache is not readable: {e}", file=sys.stderr)
        return 2
    if not isinstance(board, dict):
        print(f"board_gate_check: the live board cache {board_path} carries no board object", file=sys.stderr)
        return 2
    store = board_history.load_store(history_path)
    if not store.get(a.issue):
        print(f"board_gate_check: the history store holds no record for #{a.issue} in {history_path}", file=sys.stderr)
        return 2
    checks = check(board, store, journal_path, read_log(log_path), read_transitions(transitions_path),
                   repo, a.issue, wave=a.wave, since=_instant(a.since) if a.since else None)
    ok = all(c["ok"] for c in checks)
    if a.json:
        print(json.dumps({"issue": a.issue, "ok": ok, "checks": checks}, indent=2, sort_keys=True))
    else:
        for c in checks:
            print(f"{'ok  ' if c['ok'] else 'FAIL'} {c['name']}: {c['detail']}")
        print(f"gating check #{a.issue}: {'pass' if ok else 'fail'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
