"""The hand hold: a person's or an operator session's own work on a card, kept outside the loop (loop-bug #1664).

A hold has two faces, and this module is the one writer of both (#1678). The file face is one file per issue,
`$SG_STATE/auto/hold-<n>`, with three lines: `by <carrier>`, `since <ISO 8601 UTC>` and `until <ISO 8601 UTC>`.
Presence is the hold. The label face is the holder label on the issue: `ready-for-human` for `manual` and
`ready-for-HAL9K` for `hal9k`. A hold, a renew and a release write both faces in one act. A pass reads only the label;
the janitor reads only the file; the reconciler reports a disagreement between them as a defect and rewrites neither.
The hub's Live work tile reads the file face itself, with no `squad` import, because it serves profiles whose checkout
holds no package (#2533).

`until` is the moment the hold stops being trusted; the board pass reports a hold past it as stale and never moves a
card (ADR-0170). The marker's own write time is the folder-activity read the Live work tile uses, measured against the
same one bound.

The carriers are the three the glossary names: `manual` and `hal9k` write a hold; a run (`squad`) never does.
Only the carrier that wrote a hold renews or releases it. A batch is all or nothing: every issue is checked before any
file is written. A hold is refused when a live run holds the issue (the liveness rule of `squad/run_liveness.py`, the
gate and session rule of `squad/reap.py`), when another carrier holds it, or when an `auto/taken-<run>` marker names it.

The label is written before the file: a label write that fails leaves no file that says the issue is held, and names the
failure. A clear that GitHub answers 'Label does not exist' is already clear.

The verbs are hold, renew and release. A release names the next column and records that decision in
`$SG_STATE/auto/release-<n>`, so the board pass is the one writer of the column fact (ADR-0149) and applies it once.
There is no release without a column: a card is never left in a ghost state. A pass releases a stale hold on a closed
issue to Closed; a stale hold on an open issue is cleared by the release verb only.

The gate verb (#2563) records a hand gate in flight: `$SG_STATE/auto/gate-<n>` holds `pid <pid>` and `start <epoch>`. The
hold file keeps its three lines. The verb refuses an issue with no live hold. `gates()` reads the markers and drops one
whose process is gone or started at another time, so a reused pid never reads as a gate. A release removes the marker.

Every write verb touches `$SG_STATE/board-dirty`, so the board pass re-reads at once.

usage: python3 -m squad.handhold hold <n>... [--by manual|hal9k] [--minutes N]
       python3 -m squad.handhold renew <n>... [--by manual|hal9k] [--minutes N]
       python3 -m squad.handhold release <n>... --to Todo|Batching|Closed [--by manual|hal9k]
       python3 -m squad.handhold gate <n> --pid <pid>
       python3 -m squad.handhold holds [--live] [--closed n...]

The bound defaults to `SG_HAND_HOLD_MINUTES` (240). The label writes name the repository `SG_REPO` names (owner/name).
Exit 0 when every write lands, 1 on a refusal (one stderr line each).
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from squad import caps as C
from squad import record
from squad import run_liveness as RL

CARRIERS = ("manual", "hal9k")
LABELS = {"manual": "ready-for-human", "hal9k": "ready-for-HAL9K"}   # the holder label each carrier writes on the issue
COLUMNS = ("Todo", "Batching", "Closed")   # the columns a release may name, in the shipped vocabulary: work is done or it goes back to Todo (#2190)
OLD_COLUMNS = {"Landing": "Batching"}   # a release that names the column's version 2 spelling records the new name (#2315)
DEFAULT_MINUTES = 240
ENV_MINUTES = "SG_HAND_HOLD_MINUTES"
ENV_REPO = "SG_REPO"
ISO = "%Y-%m-%dT%H:%M:%SZ"
CHILD_LINE_ISSUES = re.compile(r"GitHub issue(?:\(s\))? #?([0-9][0-9, ]*)")   # as janitor.issues_named reads a request
ANY_ISSUE = re.compile(r"GitHub issue #(\d+)")


class HandError(Exception):
    """A read the hold cannot make: an unreadable file, a malformed hold or an unreadable process table. The batch is
    refused, never guessed: an unknown answer is never 'no run holds it'."""


def iso(t):
    return datetime.fromtimestamp(t, UTC).strftime(ISO)


def stamp(t):
    return time.strftime("%F %T", time.localtime(t))


def _epoch(s):
    return datetime.strptime(s, ISO).replace(tzinfo=UTC).timestamp()


def _state(state):
    s = state or os.environ.get("SG_STATE")
    if not s:
        raise ValueError("SG_STATE is not set")
    return Path(s)


def _checkout(checkout):
    return Path(checkout or os.environ.get("SG_CHECKOUT") or ".")


def _repo(repo):
    """The repository the holder label is written on: `SG_REPO`, or the repository a caller names."""
    r = repo or os.environ.get(ENV_REPO) or ""
    if not re.fullmatch(r"[^/\s]+/[^/\s]+", r):
        raise HandError(f"{ENV_REPO} names no repository (owner/name), so the holder label has nowhere to go")
    return r


def _gh_label(repo, n, label, add):
    """One holder label on the issue, through the REST door: an add is a POST and a clear is a DELETE. A clear that
    GitHub answers 'Label does not exist' is already clear. Any other refusal raises HandError, and the file is not
    written: a file that says the issue is held and a label that says nothing would be the disagreement this act removes."""
    route = f"repos/{repo}/issues/{n}/labels"
    argv = (["gh", "api", "--method", "POST", route, "-f", f"labels[]={label}"] if add
            else ["gh", "api", "--method", "DELETE", f"{route}/{label}"])
    try:
        r = subprocess.run(argv, capture_output=True, text=True)
    except OSError as e:
        raise HandError(f"cannot reach GitHub to write {label} on #{n}: {e}") from e
    if r.returncode:
        body = f"{r.stdout or ''}{r.stderr or ''}"
        if not add and "Label does not exist" in body:
            return
        raise HandError(f"the write of {label} on #{n} was refused: {' '.join(body.split())[:200] or 'no answer'}")


def _labels_in_one_act(writer, repo, issues, by, add):
    """Every holder label of one batch, or none: a failed write undoes the labels this act already wrote and raises, so a
    file is never written beside a label that never landed."""
    label = LABELS[by]
    done = []
    for n in issues:
        try:
            writer(repo, n, label, add)
        except HandError:
            for m in reversed(done):
                try:
                    writer(repo, m, label, not add)
                except HandError:
                    pass
            raise
        done.append(n)


def _minutes(minutes):
    """The one bound: `SG_HAND_HOLD_MINUTES`, default 240. It is both the `until` bound and the folder-activity bound."""
    if minutes is None:
        raw = os.environ.get(ENV_MINUTES, str(DEFAULT_MINUTES))
        try:
            minutes = int(raw)
        except ValueError:
            raise ValueError(f"{ENV_MINUTES} is not a whole number: {raw!r}") from None
    if minutes <= 0:
        raise ValueError("the hold bound must be a positive number of minutes")
    return minutes


def _numbers(issues):
    out = []
    for raw in issues:
        s = str(raw)
        if not s.isdigit():
            raise ValueError(f"not an issue number: {raw!r}")
        if int(s) not in out:
            out.append(int(s))
    return out


def _path(state, n):
    return state / "auto" / f"hold-{n}"


def _gate_path(state, n):
    return state / "auto" / f"gate-{n}"


def _release_path(state, n):
    return state / "auto" / f"release-{n}"


def _read(state, n):
    """The hold on issue n as a dict, or None when none is written. An unreadable or malformed file raises HandError."""
    try:
        text = _path(state, n).read_text()
    except FileNotFoundError:
        return None
    except OSError as e:
        raise HandError(f"cannot read the hold on #{n}: {e}") from e
    fields = {}
    for line in text.splitlines():
        key, _, value = line.partition(" ")
        fields[key] = value
    try:
        if fields["by"] not in CARRIERS:
            raise KeyError("by")
        _epoch(fields["since"])
        until_t = _epoch(fields["until"])
    except (KeyError, ValueError):
        raise HandError(f"the hold on #{n} is malformed") from None
    return {"issue": n, "by": fields["by"], "since": fields["since"], "until": fields["until"], "until_t": until_t}


def _taken(state):
    """{issue: run} for every `auto/taken-<run>` marker: the issues a takeover left owed to a hand fix (ADR-0166)."""
    out = {}
    d = state / "auto"
    for p in sorted(d.glob("taken-*")) if d.is_dir() else []:
        run = p.name[len("taken-"):]
        try:
            text = p.read_text()
        except OSError as e:
            raise HandError(f"cannot read {p.name}: {e}") from e
        for line in text.splitlines():
            if line.startswith("issues "):
                for i in line.split()[1:]:
                    out.setdefault(i, run)
    return out


def _listed(state):
    try:
        text = (state / "active-runs.txt").read_text()
    except FileNotFoundError:
        return []
    except OSError as e:
        raise HandError(f"cannot read active-runs.txt: {e}") from e
    return list(dict.fromkeys(line.strip() for line in text.splitlines() if line.strip()))


def _request_issues(folder):
    """The issues a run's request names, read as janitor.issues_named reads them: the CHILD lines, then any issue the text names."""
    try:
        text = (folder / "request.txt").read_text()
    except FileNotFoundError:
        return set()
    except OSError as e:
        raise HandError(f"cannot read the request of {folder.name}: {e}") from e
    found = set()
    for line in text.splitlines():
        if line.startswith("CHILD"):
            for m in CHILD_LINE_ISSUES.finditer(line):
                found.update(x.strip() for x in m.group(1).split(",") if x.strip().isdigit())
    found.update(m.group(1) for m in ANY_ISSUE.finditer(text))
    return found


def _live(run, folder, checkout, now, procs):
    """True while a listed run lives: its record names no verdict and its engine runs (`run_liveness`), or it is still in
    its launch window. A process that only shares the folder's cwd is no evidence of ownership (#1993)."""
    try:
        text = RL.read_text(RL.record_path(run, checkout))
    except RL.ReadError as e:
        raise HandError(f"cannot read run {folder.name}: {e}") from e
    if C.final_verdict(text) is not None:
        return False
    try:
        if RL.run_live_at(run, checkout, procs, now, C.LAUNCH_GRACE):
            return True
    except RL.ReadError as e:
        raise HandError(f"cannot read the processes for run {folder.name}: {e}") from e
    return C._in_launch_window(folder, now)


def _held(issues, state, checkout, now, procs):
    """{issue: run} for every issue a live listed run's request names."""
    wanted = {str(n) for n in issues}
    out = {}
    for run in _listed(state):
        folder = Path(run) if run.startswith("/") else checkout / "runs" / run
        named = _request_issues(folder) & wanted
        if named and _live(run, folder, checkout, now, procs):
            for n in named:
                out.setdefault(n, folder.name)
    return out


def _refusals(verb, issues, by, state, checkout, now, procs):
    """One line per refused issue; an empty list when the whole batch may take the verb. Everything is read before a write."""
    try:
        held = _held(issues, state, checkout, now, procs) if verb == "hold" else {}
        taken = _taken(state) if verb == "hold" else {}
    except HandError as e:
        return [f"refused #{n}: {e}" for n in issues]
    lines = []
    for n in issues:
        key = str(n)
        try:
            c = _read(state, n)
        except HandError as e:
            lines.append(f"refused #{n}: {e}")
            continue
        if verb == "hold":
            if key in taken:
                lines.append(f"refused #{n}: taken over by run {taken[key]} (auto/taken-{taken[key]}), a hand fix owes it")
            elif key in held:
                lines.append(f"refused #{n}: live run {held[key]} holds it")
            elif c and c["by"] != by:
                lines.append(f"refused #{n}: held by {c['by']} since {c['since']}")
        elif c is None:
            lines.append(f"refused #{n}: no hold to {verb}")
        elif c["by"] != by:
            lines.append(f"refused #{n}: held by {c['by']}, not {by}")
    return lines


def _verb(verb, issues, by, minutes, state, checkout, now, procs, to=None, label_write=None, repo=None):
    st = _state(state)
    ck = _checkout(checkout)
    now = time.time() if now is None else now
    if by not in CARRIERS:
        raise ValueError(f"--by names one of {', '.join(CARRIERS)}, not {by!r}")
    if verb == "release":
        if to is None:
            raise ValueError('release needs --to Todo|Batching|Closed: a card is never left in a ghost state')
        to = OLD_COLUMNS.get(to, to)
        if to not in COLUMNS:
            raise ValueError(f"--to names one of {', '.join(COLUMNS)}, not {to!r}")
    nums = _numbers(issues)
    m = _minutes(minutes) if verb != "release" else None   # the one bound is validated before any face is written
    snap = RL.read_processes if procs is None else procs
    refusals = _refusals(verb, nums, by, st, ck, now, snap)
    if refusals:
        for line in refusals:
            print(line, file=sys.stderr)
        return 1
    writer = _gh_label if label_write is None else label_write
    try:
        _labels_in_one_act(writer, _repo(repo), nums, by, verb != "release")
    except HandError as e:
        what = "clear" if verb == "release" else "write"
        print(f"handhold: cannot {what} the holder label, so no hold file changed: {e}", file=sys.stderr)
        return 1
    (st / "auto").mkdir(parents=True, exist_ok=True)
    if verb == "release":
        for n in nums:
            record.atomic_write(_release_path(st, n), f"to {to}\nby {by}\n")
            _path(st, n).unlink(missing_ok=True)
            _gate_path(st, n).unlink(missing_ok=True)
            print(f"released #{n}: the card moves to {to}")
    else:
        for n in nums:
            c = _read(st, n)
            since = c["since"] if c else iso(now)
            until_t = now + m * 60
            if c:
                until_t = max(until_t, c["until_t"])   # a renewal pushes `until` forward, never back
            until = iso(until_t)
            record.atomic_write(_path(st, n), f"by {by}\nsince {since}\nuntil {until}\n")
            print(f"{'held' if verb == 'hold' else 'renewed'} #{n} for {by} until {until}")
    (st / "board-dirty").touch()
    return 0


def hold(issues, by="manual", minutes=None, state=None, checkout=None, now=None, procs=None, label_write=None, repo=None):
    """Write the hold file and the holder label on every issue, or neither on any. Return 0 when all are written, 1 on a
    refusal."""
    return _verb("hold", issues, by, minutes, state, checkout, now, procs, label_write=label_write, repo=repo)


def renew(issues, by="manual", minutes=None, state=None, checkout=None, now=None, procs=None, label_write=None, repo=None):
    """Push `until` forward on holds the same carrier wrote, keeping the file and the label consistent. Return 0 when all
    renew, 1 on a refusal."""
    return _verb("renew", issues, by, minutes, state, checkout, now, procs, label_write=label_write, repo=repo)


def release(issues, by="manual", to=None, state=None, checkout=None, now=None, procs=None, label_write=None, repo=None):
    """Clear the hold file and the holder label the same carrier wrote, and record the column the card moves to. Return 0
    or 1: a label the clear does not reach keeps the hold."""
    return _verb("release", issues, by, None, state, checkout, now, procs, to=to, label_write=label_write, repo=repo)


def gate(issue, pid, state=None, now=None, procs=None):
    """Record that a hand gate runs as process `pid` for `issue`: write `auto/gate-<n>` with the pid and its start time.
    Return 0, or 1 with one stderr line when the issue has no live hold or the process is not running (#2563)."""
    st = _state(state)
    now = time.time() if now is None else now
    n = _numbers([issue])[0]
    try:
        c = _read(st, n)
        table = RL.read_processes() if procs is None else (procs() if callable(procs) else procs)
    except (HandError, RL.ReadError) as e:
        print(f"refused #{n}: {e}", file=sys.stderr)
        return 1
    if c is None or now > c["until_t"]:
        print(f"refused #{n}: no live hold to gate", file=sys.stderr)
        return 1
    p = table.get(int(pid))
    if p is None:
        print(f"refused #{n}: process {pid} is not running", file=sys.stderr)
        return 1
    (st / "auto").mkdir(parents=True, exist_ok=True)
    start = "" if p.start is None else int(p.start)
    record.atomic_write(_gate_path(st, n), f"pid {int(pid)}\nstart {start}\n")
    (st / "board-dirty").touch()
    print(f"gating #{n}: process {pid}")
    return 0


def gates(state=None, procs=None):
    """The set of issue numbers (as strings) with a live hand gate. A marker is dropped when it is unreadable, when its
    process is gone, or when the process started at another time than the marker names (a reused pid). Nothing is
    written."""
    st = _state(state)
    d = st / "auto"
    out = set()
    markers = sorted(d.glob("gate-*")) if d.is_dir() else []
    if not markers:
        return out
    try:
        table = RL.read_processes() if procs is None else (procs() if callable(procs) else procs)
    except RL.ReadError:
        return out
    for p in markers:
        n = p.name[len("gate-"):]
        try:
            fields = dict(line.partition(" ")[::2] for line in p.read_text().splitlines())
            pid = int(fields["pid"])
        except (OSError, KeyError, ValueError):
            continue
        proc = table.get(pid)
        if proc is None or not n.isdigit():
            continue
        began = fields.get("start", "")
        if proc.start is not None and began.isdigit() and abs(proc.start - int(began)) > RL.START_SLACK:
            continue
        out.add(n)
    return out


def holds(state=None, closed=None, now=None):
    """Every readable hold, by issue number. `stale` is True once `until` has passed; `activity` is the seconds since the
    marker's own write, the folder-activity read; `orphan` is True when the issue is closed or abandoned; `live` is True
    while nothing makes it stale or an orphan, so the Live work tile draws only that."""
    st = _state(state)
    now = time.time() if now is None else now
    closed = {str(n) for n in (closed or ())}
    d = st / "auto"
    if not d.is_dir():
        return []
    out = []
    for p in d.glob("hold-*"):
        if not p.name[len("hold-"):].isdigit():
            continue
        c = _read(st, int(p.name[len("hold-"):]))
        if c is None:
            continue
        try:
            activity = now - p.stat().st_mtime
        except OSError:
            activity = None
        stale = now > c["until_t"]
        orphan = str(c["issue"]) in closed
        out.append({"issue": c["issue"], "carrier": c["by"], "since": c["since"], "until": c["until"],
                    "stale": stale, "orphan": orphan, "live": not stale and not orphan, "activity": activity})
    return sorted(out, key=lambda r: r["issue"])


def holders(state=None):
    """{issue: {"kind", "who"}} for every issue a takeover marker or a hand hold names: the holders the board pass reads so a
    card with a holder is never queued work. A takeover marker wins over a hold on the same issue. A hold stays held past
    its `until`: the marker never moves a card, so it never frees one. An unreadable or malformed file names no holder, and
    the pass then writes nothing for that issue."""
    st = _state(state)
    out = {}
    try:
        for n, run in _taken(st).items():
            if n.isdigit():
                out[n] = {"kind": "taken", "who": run}
    except HandError:
        pass
    d = st / "auto"
    for p in sorted(d.glob("hold-*")) if d.is_dir() else []:
        n = p.name[len("hold-"):]
        if not n.isdigit() or n in out:
            continue
        try:
            c = _read(st, int(n))
        except HandError:
            continue
        if c:
            out[n] = {"kind": "hold", "who": c["by"]}
    return out


def releases(state=None):
    """{issue: column} for every release record the board pass has not applied yet. A record that is unreadable or names
    a column a release may not name is skipped."""
    st = _state(state)
    out = {}
    d = st / "auto"
    for p in sorted(d.glob("release-*")) if d.is_dir() else []:
        n = p.name[len("release-"):]
        try:
            lines = dict(line.partition(" ")[::2] for line in p.read_text().splitlines())
        except OSError:
            continue
        if n.isdigit() and lines.get("to") in COLUMNS:
            out[n] = lines["to"]
    return out


def clear_release(n, state=None):
    """Remove the release record of issue n: the board pass calls it once the move is on the card, so a release applies once."""
    _release_path(_state(state), n).unlink(missing_ok=True)


def alerts(state=None, closed=None, now=None, path=None):
    """One alerts.log line for each stale hold and each orphan hold the last pass did not name: one alert per episode,
    like a taken-over run. A hold on a closed or abandoned issue is an orphan; it is named here and never drawn as work."""
    st = _state(state)
    now = time.time() if now is None else now
    want = {}
    for r in holds(state=st, closed=closed, now=now):
        key = str(r["issue"])
        if r["orphan"]:
            want[f"orphan-{key}"] = (f"{stamp(now)} HOLD ORPHAN #{key} {r['carrier']} since {r['since']}; "
                                     "the issue is closed or abandoned")
        elif r["stale"]:
            want[f"stale-{key}"] = (f"{stamp(now)} HOLD STALE #{key} {r['carrier']} since {r['since']} "
                                    f"until {r['until']}")
    seen_path = st / "auto" / "hold-alerts"
    try:
        seen = set(seen_path.read_text().split())
    except FileNotFoundError:
        seen = set()
    except OSError as e:
        raise HandError(f"cannot read the hold alerts: {e}") from e
    lines = [line for key, line in want.items() if key not in seen]
    if lines:
        log = Path(path) if path else st / "alerts.log"
        with log.open("a") as f:
            for line in lines:
                f.write(line + "\n")
    if set(want) != seen:   # the dedupe file changes only when an episode starts or ends, never on every read
        record.atomic_write(seen_path, "".join(f"{key}\n" for key in sorted(want)))
    return lines


def main(argv=None, **kw):
    """The CLI. `kw` carries `state`, `checkout`, `now` and `procs` for a caller that injects them."""
    ap = argparse.ArgumentParser(prog="python3 -m squad.handhold")
    sub = ap.add_subparsers(dest="verb", required=True)
    for verb in ("hold", "renew", "release"):
        s = sub.add_parser(verb)
        s.add_argument("issues", nargs="+", metavar="n")
        s.add_argument("--by", choices=CARRIERS, default="manual")
        if verb == "release":
            s.add_argument("--to", choices=COLUMNS + tuple(OLD_COLUMNS), required=True)
        else:
            s.add_argument("--minutes", type=int)
    g = sub.add_parser("gate")
    g.add_argument("issue", metavar="n")
    g.add_argument("--pid", type=int, required=True)
    h = sub.add_parser("holds")
    h.add_argument("--live", action="store_true")
    h.add_argument("--closed", nargs="*", default=[], metavar="n")
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:   # argparse refuses a missing --to with its own exit 2; a caller gets the code, not an exit
        return int(e.code or 0)
    try:
        if a.verb == "holds":
            rows = holds(state=kw.get("state"), closed=a.closed, now=kw.get("now"))
            if a.live:
                rows = [r for r in rows if r["live"]]
            for r in rows:
                print(f"#{r['issue']} {r['carrier']} since {r['since']} until {r['until']}"
                      f"{'  stale' if r['stale'] else ''}{'  orphan' if r['orphan'] else ''}")
            return 0
        if a.verb == "gate":
            return gate(a.issue, a.pid, state=kw.get("state"), now=kw.get("now"), procs=kw.get("procs"))
        if a.verb == "release":
            return release(a.issues, by=a.by, to=a.to, **kw)
        fn = hold if a.verb == "hold" else renew
        return fn(a.issues, by=a.by, minutes=a.minutes, **kw)
    except ValueError as e:
        print(f"handhold: {e}", file=sys.stderr)
        return 2
    except HandError as e:
        print(f"handhold: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
