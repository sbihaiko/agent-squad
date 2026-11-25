"""The nightly live check: the queue's invariants, proven against the running loop and the live state (spec 1163).

The check asserts only what the rules promise (ADR-0153, ADR-0163, ADR-0164):

- **No child starves on infrastructure.** A child that waits for the lock or for a slot beyond the bound is starved when
  every holder of the slots it waits for is itself queued for the lock: the ordering inversion of #1166, where a holder
  takes its slots before the lock. The check reports that child, and it never reports a wait the bound allows.
- **An `unproven` entry is a job, not a verdict.** Every entry the gate recorded `unproven`, and every wave entry admitted
  unproven, carries an obligation with a future deadline, or a settlement that answered it. The check reports an entry
  with no obligation, and an entry whose obligation expired and was never settled.
- **No measured red is released.** A measured red holds its tree until a pass that covers its scope and reran the checks
  that reported it (ADR-0164). The check reports a release the record cannot justify. A red the record still holds is
  correct: the check never fails on it, and it never asserts that a held child is released.

It reads the loop's live state: the state directory (`SG_STATE`), the machine-wide slot leases (the caps folder,
`squad.caps`), the gate history, the obligations and their settlements, and the issue ledgers. It takes its own lock,
never the gate lock; it writes no record a dispatch hold reads, so it pauses no dispatch (ADR-0163).

usage: python3 -m squad.livecheck [--state DIR] [--caps DIR] [--repo OWNER/NAME] [--window SECONDS]
                                  [--interval SECONDS] [--bound SECONDS] [--once] [--now EPOCH]

The live run samples the state every `--interval` seconds for `--window` seconds, one hour by default. `--once` reads it
one time, and `--now` pins the clock one time, as the suite drives it. One record is the check's only output, written by
a temp file and a rename (ADR-0030): `$SG_STATE/live-check-result`, `<epoch> TAB <pass|fail> TAB <start epoch> TAB
<samples> TAB <one JSON array of the finding lines>`. A finding exits 1; a clean run exits 0.

Guard: `contrib/squad-goal/scripts/test-live-check.sh`, `tests/test_livecheck.py`.
"""
import argparse
import json
import os
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from squad import caps as machine_caps

DEFAULT_STATE = "/tmp/sg-state"
DEFAULT_WINDOW = 3600.0    # one hour: the live check's own bound
DEFAULT_INTERVAL = 30.0
DEFAULT_BOUND = 900.0      # one gate attempt (ADR-0163): a wait longer than a whole attempt is no wait
HISTORY_FIELDS = 13        # gate.sh's history line: finish..red, one field per fact
OBLIGATION_KEY = re.compile(r"obligation ([0-9]+)")


@dataclass(frozen=True)
class Finding:
    """One violated invariant, named by its kind and its subject (the child, the tree or the issue it is about)."""

    kind: str
    subject: str
    detail: str

    def line(self) -> str:
        return f"{self.kind} {self.subject}: {self.detail}"


def _env_float(name: str, default: float) -> float:
    """A setting's value, or the default when it is absent or not a non-negative number."""
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value >= 0 else default


def _fields(path: Path, count: int) -> list[str] | None:
    """The record's `<count>` tab-separated fields, or None when the file is absent, unreadable or not that record."""
    try:
        text = path.read_text()
    except OSError:
        return None
    if text.count("\n") > 1:
        return None    # a record is one line: anything after it is not that record
    fields = text.rstrip("\n").split("\t")
    return fields if len(fields) == count else None


def _int_or_none(text: str) -> int | None:
    return int(text) if text.isdigit() else None


def _at_or_after(epoch: str, since: float) -> bool:
    value = _int_or_none(epoch)
    return value is not None and value >= since


def _repo_matches(record: str, repo: str) -> bool:
    """An obligation of this repository, or of no named repository (`-`), answers an entry of this loop."""
    return not repo or record in (repo, "-")


def lock_waits(state: Path) -> list[tuple[int, str, int]]:
    """(since, lock, pid) for each process queued for a lock, as serial.sh records it while the queue lasts. A marker whose
    pid is gone is a queue no one stands in, so it is no proof that a holder waits."""
    out: list[tuple[int, str, int]] = []
    try:
        paths = sorted((state / "locks" / "waiting").iterdir())
    except OSError:
        return out
    for path in paths:
        if not path.is_file():
            continue
        try:
            text = path.read_text()
        except OSError:
            continue
        fields = text.strip().split("\t")
        if len(fields) not in (3, 4):   # serial.sh adds the waiter's priority as a fourth field (#2887)
            continue
        since, lock, pid = fields[:3]
        if not (since.isdigit() and pid.isdigit() and lock):
            continue
        if machine_caps.alive(int(pid)):
            out.append((int(since), lock, int(pid)))
    return out


def slot_waits(caps_dir: Path) -> list[tuple[float, str, str]]:
    """(since, ceiling, holder) for each child queued for a slot: caps.take writes the marker once, at the wait's start. A
    marker whose waiter is gone is a wait no one stands in, so it is no proof of a starved child."""
    out: list[tuple[float, str, str]] = []
    try:
        paths = sorted((caps_dir / "waiting").glob("*.json"))
    except OSError:
        return out
    for path in paths:
        try:
            record = json.loads(path.read_text())
            since = path.stat().st_mtime
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        ceiling, holder, pid = record.get("ceiling"), record.get("holder"), record.get("pid")
        if isinstance(ceiling, str) and isinstance(holder, str) and ceiling \
                and isinstance(pid, int) and machine_caps.alive(pid):
            out.append((since, ceiling, holder))
    return out


def slot_holders(caps_dir: Path, kind: str) -> list[dict]:
    """The live leases of one kind. A lease whose holder died is reclaimed by the next count, so it holds nothing here."""
    records = []
    for path in sorted((caps_dir / kind).glob("*.json")):
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(record, dict) and isinstance(record.get("pid"), int):
            records.append(record)
    started = machine_caps.started_at([record["pid"] for record in records])
    return [record for record in records if machine_caps.alive(record["pid"], record.get("since"), started)]


def starvation(state: Path, caps_dir: Path, now: float, bound: float) -> list[Finding]:
    """The children starved by a queued slot holder: a wait past the bound while every holder of those slots is queued
    for the lock. A wait whose holders hold or wait for nothing is a queue the bound allows, and it is no finding."""
    queued = {pid: lock for _since, lock, pid in lock_waits(state)}
    if not queued:
        return []
    out = []
    for since, ceiling, holder in slot_waits(caps_dir):
        waited = now - since
        if waited < bound:
            continue
        kind = ceiling[len("caps."):] if ceiling.startswith("caps.") else ceiling
        holders = slot_holders(caps_dir, kind)
        if not holders or not all(record["pid"] in queued for record in holders):
            continue
        locks = " ".join(sorted({queued[record["pid"]] for record in holders}))
        names = ", ".join(sorted({str(record.get("holder", "?")) for record in holders}))
        out.append(Finding("STARVED", holder,
                           f"waited {int(waited)}s for {ceiling} beyond the {int(bound)}s bound; "
                           f"every holder ({names}) is queued for lock {locks}"))
    return out


def gate_history(state: Path) -> list[list[str]]:
    """The gate's history lines, each one attempt: finish, kind, result, sha, start, selection, scope, outcome, reason,
    ran, not run, slots, red (gate.sh's `gate_history`)."""
    out: list[list[str]] = []
    try:
        text = (state / "gate-history").read_text()
    except OSError:
        return out
    for line in text.splitlines():
        fields = line.split("\t")
        if len(fields) >= HISTORY_FIELDS:
            out.append(fields)
    return out


@dataclass(frozen=True)
class Obligation:
    """One proof obligation a gate left for an unproven entry (gate.sh's `obligation`), with its deadline."""

    repo: str
    sha: str
    base: str
    checks: str
    scope: str
    deadline: int


def obligations(state: Path) -> list[Obligation]:
    """The open obligation records, one per entry; a record the check cannot read answers nothing."""
    out: list[Obligation] = []
    try:
        paths = sorted((state / "obligations").glob("*"))
    except OSError:
        return out
    for path in paths:
        fields = _fields(path, 6)
        if fields is None:
            continue
        deadline = _int_or_none(fields[5])
        if deadline is None:
            continue
        out.append(Obligation(fields[0], fields[1], fields[2], fields[3], fields[4], deadline))
    return out


def settlements(state: Path) -> set[tuple[str, str]]:
    """(repository, tree sha) for each answered obligation: the nightly settled it green or red, so the entry is no open
    job. The repository is part of the key: a profile works several repositories (ADR-0138), and a settlement of one tree
    in one repository never answers an entry of another."""
    out: set[tuple[str, str]] = set()
    try:
        paths = sorted((state / "obligation-settled").glob("*"))
    except OSError:
        return out
    for path in paths:
        fields = _fields(path, 8)
        if fields is not None and fields[2]:
            out.add((fields[1], fields[2]))
    return out


def _issue_key(stem: str) -> str:
    """The issue key a ledger file name spells (`<owner>__<repo>__<n>` -> `<owner>/<repo>#<n>`), else the stem."""
    parts = stem.rsplit("__", 2)
    if len(parts) == 3 and parts[2].isdigit():
        return f"{parts[0]}/{parts[1]}#{parts[2]}"
    return stem


def entered_unproven(state: Path) -> list[tuple[str, str, str | None]]:
    """(issue key, run, obligation key) for each child a wave admitted unproven, from the ledger's entered fact."""
    out: list[tuple[str, str, str | None]] = []
    try:
        paths = sorted((state / "state").glob("*.json"))
    except OSError:
        return out
    for path in paths:
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        wave = record.get("wave") if isinstance(record, dict) else None
        if not isinstance(wave, dict):
            continue
        key = _issue_key(path.stem)
        for run, fact in wave.items():
            cause = fact.get("entered") if isinstance(fact, dict) else None
            if isinstance(cause, str) and "unproven:" in cause:
                match = OBLIGATION_KEY.search(cause)
                out.append((key, str(run), match.group(1) if match else None))
    return out


def unproven(state: Path, repo: str, since: float, now: float) -> list[Finding]:
    """Every unproven entry of the window carries an obligation with a future deadline, or a settlement that answered it."""
    out = []
    open_obligations = obligations(state)
    answered = settlements(state)
    for fields in gate_history(state):
        finish, sha, outcome = fields[0], fields[3], fields[7]
        if outcome != "unproven" or not sha or not _at_or_after(finish, since):
            continue
        if any(settled == sha and _repo_matches(repo_of, repo) for repo_of, settled in answered):
            continue
        mine = [o for o in open_obligations if o.sha == sha and _repo_matches(o.repo, repo)]
        if not mine:
            out.append(Finding("UNPROVEN-NO-OBLIGATION", sha,
                               "the gate recorded the entry unproven and no obligation answers it"))
        elif all(o.deadline <= now for o in mine):
            deadline = max(o.deadline for o in mine)
            out.append(Finding("UNPROVEN-EXPIRED", sha,
                               f"the obligation's deadline passed at {deadline} and no settlement answered it"))
    return out


def wave_entries(state: Path, now: float) -> list[Finding]:
    """Every wave entry admitted unproven names its obligation, and that obligation has a future deadline or a settlement."""
    out = []
    for key, run, obligation in entered_unproven(state):
        if obligation:
            # The settlement is checked first: the nightly may write it before it removes the obligation record, and an
            # entry the nightly already answered is no finding, whatever its record's deadline says.
            if (state / "obligation-settled" / obligation).exists():
                continue
            fields = _fields(state / "obligations" / obligation, 6)
            if fields is not None:
                deadline = _int_or_none(fields[5])
                if deadline is not None and deadline > now:
                    continue
                if deadline is not None:
                    out.append(Finding("UNPROVEN-EXPIRED", key,
                                       f"the entry of run {run} names obligation {obligation}, whose deadline passed at {deadline}"))
                    continue
        out.append(Finding("UNPROVEN-NO-OBLIGATION", key,
                           f"the entry of run {run} carries no obligation with a future deadline"))
    return out


def _covers(red_scope: str, red_ran: str, scope: str, ran: str) -> bool:
    """gate.sh's rule read back (`red_covers`): the pass measured the red's scope and reran every check it named."""
    if red_scope not in ("", "-") and f"{red_scope}:{scope}" not in ("full:full", "delta:full", "delta:delta"):
        return False
    named = set() if red_ran in ("", "-", "unknown") else set(red_ran.split())
    measured = set() if ran in ("", "-") else set(ran.split())
    return named <= measured


def reds(state: Path, since: float) -> list[Finding]:
    """No measured red is released by anything but a covering pass (ADR-0164). A red the record still holds is correct:
    a `red` line stands, and a `red-stands` line is the record's own statement that it holds, so neither is a finding."""
    out = []
    measured: dict[str, tuple[str, str]] = {}
    for fields in gate_history(state):
        finish, sha, scope, ran, red = fields[0], fields[3], fields[6], fields[9], fields[12]
        if red == "red":
            measured[sha] = (scope, ran)
            continue
        if not red.startswith("cleared-") or not _at_or_after(finish, since):
            continue
        kind, _, target = red.partition(" ")
        tree = target.split("..", 1)[0].strip()
        base = measured.get(tree)
        if base is None:
            out.append(Finding("RED-RELEASED", tree, f"{kind} at {finish} names no measured red at that tree"))
        elif not _covers(base[0], base[1], scope, ran):
            out.append(Finding("RED-RELEASED", tree, f"{kind} at {finish} by a pass that does not cover the red it releases"))
    return out


def run(state: Path, caps_dir: Path, repo: str, window: float, interval: float, bound: float,
        once: bool = False, clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep) -> tuple[list[Finding], int, int]:
    """The window's pass: sample the live state every interval, then read the history of the last window. A finding is
    kept once however many samples see it. `clock` and `sleep` are the seam the suite drives."""
    begin = clock()
    samples = 1
    seen: dict[tuple[str, str], Finding] = {}
    for finding in starvation(state, caps_dir, clock(), bound):
        seen[(finding.kind, finding.subject)] = finding
    while not once:
        elapsed = clock() - begin
        if elapsed >= window:
            break
        sleep(max(0.0, min(interval, window - elapsed)))
        samples += 1
        for finding in starvation(state, caps_dir, clock(), bound):
            seen[(finding.kind, finding.subject)] = finding
    end = clock()
    since = end - window
    for finding in unproven(state, repo, since, end) + wave_entries(state, end) + reds(state, since):
        seen[(finding.kind, finding.subject)] = finding
    return sorted(seen.values(), key=lambda f: (f.kind, f.subject)), samples, int(begin)


def write_record(state: Path, start: int, samples: int, findings: list[Finding], now: float) -> None:
    """The one record, by a temp file and a rename (ADR-0030): a reader never sees half of it."""
    line = "\t".join([str(int(now)), "fail" if findings else "pass", str(start), str(samples),
                      json.dumps([finding.line() for finding in findings])])
    state.mkdir(parents=True, exist_ok=True)
    temp = state / f"live-check-result.{os.getpid()}"
    temp.write_text(line + "\n")
    os.replace(temp, state / "live-check-result")


def main(argv: list[str] | None = None) -> int:
    """Read the live state over the window, write the one record and print each finding. Exit 1 on a finding, 0 clean."""
    parser = argparse.ArgumentParser(prog="squad.livecheck", description="The nightly live check of the queue's invariants.")
    parser.add_argument("--state", default=os.environ.get("SG_STATE") or DEFAULT_STATE)
    parser.add_argument("--caps", default=None, help="the machine-wide slot leases (default: squad.caps's own folder)")
    parser.add_argument("--repo", default=os.environ.get("SG_REPO") or "")
    parser.add_argument("--window", type=float, default=_env_float("SG_LIVE_CHECK_WINDOW_SECONDS", DEFAULT_WINDOW))
    parser.add_argument("--interval", type=float, default=_env_float("SG_LIVE_CHECK_INTERVAL_SECONDS", DEFAULT_INTERVAL))
    parser.add_argument("--bound", type=float, default=_env_float("SG_LIVE_CHECK_BOUND_SECONDS", DEFAULT_BOUND))
    parser.add_argument("--once", action="store_true", help="read the state one time")
    parser.add_argument("--now", type=float, default=None, help="pin the clock one time, as the suite drives it")
    args = parser.parse_args(argv)
    state = Path(args.state)
    caps_dir = Path(args.caps) if args.caps else machine_caps.folder()
    once = args.once or args.now is not None
    if args.now is None:
        clock: Callable[[], float] = time.time
    else:
        pinned = args.now
        clock = lambda: pinned  # noqa: E731  # one value, read as the clock
    findings, samples, start = run(state, caps_dir, args.repo, args.window, args.interval, args.bound,
                                   once=once, clock=clock)
    try:
        write_record(state, start, samples, findings, clock())
    except OSError as exc:
        print(f"livecheck: the result record could not be written: {exc}", file=sys.stderr)
        return 2
    for finding in findings:
        print(finding.line())
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
