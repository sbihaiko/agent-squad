"""The hand claim: a person's or an operator session's own work on a card, kept outside the loop (loop-bug #1664).

One file per issue, `$SG_STATE/auto/hand-<n>`, holds three lines: `by <carrier>`, `since <ISO 8601 UTC>` and
`until <ISO 8601 UTC>`. Presence is the claim. `until` is the moment the claim stops being trusted; the board pass reports a
claim past it as stale and never moves a card (ADR-0170).

The carriers are the three the glossary names: `manual` and `hal9k` make claims; a run (`squad`) never writes one.
Only the carrier that wrote a claim renews or releases it. A batch is all or nothing: every issue is checked before any
file is written. A claim is refused when a live run holds the issue (the liveness rule of `squad/run_liveness.py`, the
gate and session rule of `squad/reap.py`), when another carrier holds it, or when an `auto/taken-<run>` marker names it.

Every write verb touches `$SG_STATE/board-dirty`, so the board pass re-reads at once.

usage: python3 -m squad.handclaim claim <n>... [--by manual|hal9k] [--minutes N]
       python3 -m squad.handclaim renew <n>... [--by manual|hal9k] [--minutes N]
       python3 -m squad.handclaim release <n>... [--by manual|hal9k] [--to Todo|Doing|"Code Complete"|Closed]
       python3 -m squad.handclaim claims

The bound defaults to `SG_HAND_CLAIM_MINUTES` (240). Exit 0 when every write lands, 1 on a refusal (one stderr line each).
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from squad import caps as C
from squad import reap, record
from squad import run_liveness as RL

CARRIERS = ("manual", "hal9k")
COLUMNS = ("Todo", "Doing", "Code Complete", "Closed")
DEFAULT_MINUTES = 240
ISO = "%Y-%m-%dT%H:%M:%SZ"
CHILD_LINE_ISSUES = re.compile(r"GitHub issue(?:\(s\))? #?([0-9][0-9, ]*)")   # as janitor.issues_named reads a request
ANY_ISSUE = re.compile(r"GitHub issue #(\d+)")


class HandError(Exception):
    """A read the claim cannot make: an unreadable file, a malformed claim or an unreadable process table. The batch is
    refused, never guessed: an unknown answer is never 'no run holds it'."""


def iso(t):
    return datetime.fromtimestamp(t, UTC).strftime(ISO)


def _epoch(s):
    return datetime.strptime(s, ISO).replace(tzinfo=UTC).timestamp()


def _state(state):
    s = state or os.environ.get("SG_STATE")
    if not s:
        raise ValueError("SG_STATE is not set")
    return Path(s)


def _checkout(checkout):
    return Path(checkout or os.environ.get("SG_CHECKOUT") or ".")


def _minutes(minutes):
    if minutes is None:
        raw = os.environ.get("SG_HAND_CLAIM_MINUTES", str(DEFAULT_MINUTES))
        try:
            minutes = int(raw)
        except ValueError:
            raise ValueError(f"SG_HAND_CLAIM_MINUTES is not a whole number: {raw!r}") from None
    if minutes <= 0:
        raise ValueError("the claim bound must be a positive number of minutes")
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
    return state / "auto" / f"hand-{n}"


def _read(state, n):
    """The claim on issue n as a dict, or None when none is written. An unreadable or malformed file raises HandError."""
    try:
        text = _path(state, n).read_text()
    except FileNotFoundError:
        return None
    except OSError as e:
        raise HandError(f"cannot read the claim on #{n}: {e}") from e
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
        raise HandError(f"the claim on #{n} is malformed") from None
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


def _live(run, folder, checkout, now, procs, cwd_procs):
    """True while a listed run lives: its record names no verdict and its engine runs (`run_liveness`), or it is still in
    its launch window, or a gate or session process works inside its folder (the rule `reap.LIVE_PROC` names)."""
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
    if C._in_launch_window(folder, now):
        return True
    rows = cwd_procs()
    if rows is None:
        raise HandError(f"cannot read the processes for run {folder.name}")
    here = str(folder)
    return any((cwd == here or cwd.startswith(here + "/")) and reap.LIVE_PROC.search(cmd) for _, cwd, cmd in rows)


def _held(issues, state, checkout, now, procs, cwd_procs):
    """{issue: run} for every issue a live listed run's request names."""
    wanted = {str(n) for n in issues}
    out = {}
    for run in _listed(state):
        folder = Path(run) if run.startswith("/") else checkout / "runs" / run
        named = _request_issues(folder) & wanted
        if named and _live(run, folder, checkout, now, procs, cwd_procs):
            for n in named:
                out.setdefault(n, folder.name)
    return out


def _refusals(verb, issues, by, state, checkout, now, procs, cwd_procs):
    """One line per refused issue; an empty list when the whole batch may take the verb. Everything is read before a write."""
    try:
        held = _held(issues, state, checkout, now, procs, cwd_procs) if verb == "claim" else {}
        taken = _taken(state) if verb == "claim" else {}
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
        if verb == "claim":
            if key in taken:
                lines.append(f"refused #{n}: taken over by run {taken[key]} (auto/taken-{taken[key]}), a hand fix owes it")
            elif key in held:
                lines.append(f"refused #{n}: live run {held[key]} holds it")
            elif c and c["by"] != by:
                lines.append(f"refused #{n}: claimed by {c['by']} since {c['since']}")
        elif c is None:
            lines.append(f"refused #{n}: no claim to {verb}")
        elif c["by"] != by:
            lines.append(f"refused #{n}: claimed by {c['by']}, not {by}")
    return lines


def _verb(verb, issues, by, minutes, state, checkout, now, procs, cwd_procs, to=None):
    st = _state(state)
    ck = _checkout(checkout)
    now = time.time() if now is None else now
    if by not in CARRIERS:
        raise ValueError(f"--by names one of {', '.join(CARRIERS)}, not {by!r}")
    nums = _numbers(issues)
    snap = RL.read_processes if procs is None else procs
    cwd = reap._procs if cwd_procs is None else cwd_procs
    refusals = _refusals(verb, nums, by, st, ck, now, snap, cwd)
    if refusals:
        for line in refusals:
            print(line, file=sys.stderr)
        return 1
    if verb == "release":
        (st / "auto").mkdir(parents=True, exist_ok=True)
        for n in nums:
            _path(st, n).unlink(missing_ok=True)
            print(f"released #{n}; the card {'moves to ' + to if to else 'stays where it is'}")
    else:
        m = _minutes(minutes)
        (st / "auto").mkdir(parents=True, exist_ok=True)
        for n in nums:
            c = _read(st, n)
            since = c["since"] if c else iso(now)
            until_t = now + m * 60
            if c:
                until_t = max(until_t, c["until_t"])   # a renewal pushes `until` forward, never back
            until = iso(until_t)
            record.atomic_write(_path(st, n), f"by {by}\nsince {since}\nuntil {until}\n")
            print(f"{'claimed' if verb == 'claim' else 'renewed'} #{n} for {by} until {until}")
    (st / "board-dirty").touch()
    return 0


def claim(issues, by="manual", minutes=None, state=None, checkout=None, now=None, procs=None, cwd_procs=None):
    """Write the claim on every issue, or none. Return 0 when all are written, 1 on a refusal."""
    return _verb("claim", issues, by, minutes, state, checkout, now, procs, cwd_procs)


def renew(issues, by="manual", minutes=None, state=None, checkout=None, now=None, procs=None, cwd_procs=None):
    """Push `until` forward on claims the same carrier wrote. Return 0 when all renew, 1 on a refusal."""
    return _verb("renew", issues, by, minutes, state, checkout, now, procs, cwd_procs)


def release(issues, by="manual", to=None, state=None, checkout=None, now=None, procs=None, cwd_procs=None):
    """Remove the claims the same carrier wrote and name the column the card moves to. Return 0 or 1."""
    if to is not None and to not in COLUMNS:
        raise ValueError(f"--to names one of {', '.join(COLUMNS)}, not {to!r}")
    return _verb("release", issues, by, None, state, checkout, now, procs, cwd_procs, to=to)


def claims(state=None, now=None):
    """Every readable claim, by issue number. `stale` is True once `until` has passed."""
    st = _state(state)
    now = time.time() if now is None else now
    d = st / "auto"
    if not d.is_dir():
        return []
    out = []
    for p in d.glob("hand-*"):
        if not p.name[len("hand-"):].isdigit():
            continue
        c = _read(st, int(p.name[len("hand-"):]))
        if c is not None:
            out.append({"issue": c["issue"], "carrier": c["by"], "since": c["since"], "until": c["until"],
                        "stale": now > c["until_t"]})
    return sorted(out, key=lambda r: r["issue"])


def main(argv=None, **kw):
    """The CLI. `kw` carries `state`, `checkout`, `now`, `procs` and `cwd_procs` for a caller that injects them."""
    ap = argparse.ArgumentParser(prog="python3 -m squad.handclaim")
    sub = ap.add_subparsers(dest="verb", required=True)
    for verb in ("claim", "renew", "release"):
        s = sub.add_parser(verb)
        s.add_argument("issues", nargs="+", metavar="n")
        s.add_argument("--by", choices=CARRIERS, default="manual")
        if verb == "release":
            s.add_argument("--to", choices=COLUMNS)
        else:
            s.add_argument("--minutes", type=int)
    sub.add_parser("claims")
    a = ap.parse_args(argv)
    try:
        if a.verb == "claims":
            rows = claims(state=kw.get("state"), now=kw.get("now"))
            for r in rows:
                print(f"#{r['issue']} {r['carrier']} since {r['since']} until {r['until']}{'  stale' if r['stale'] else ''}")
            return 0
        if a.verb == "release":
            return release(a.issues, by=a.by, to=a.to, **kw)
        fn = claim if a.verb == "claim" else renew
        return fn(a.issues, by=a.by, minutes=a.minutes, **kw)
    except ValueError as e:
        print(f"handclaim: {e}", file=sys.stderr)
        return 2
    except HandError as e:
        print(f"handclaim: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
