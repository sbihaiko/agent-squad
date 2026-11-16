#!/usr/bin/env python3
"""The loop's throughput over a time window, read from local records only (#564).

Four numbers, each read from a record on this machine:
  issues landed per day   the `Refs #N` lines of the commits on the base branch, grouped by commit date;
  mean runs per issue     the run folders whose request names the issue, each issue counted once per run;
  child review verdicts   the `CHILD REVIEW ... VERDICT: <v>`, `CHILD REVIEW REQUEUE` and `CHILD FIX REQUEUE` lines of $SG_STATE/watch.log;
  gate time               each finished gate of $SG_STATE/gate-history, split: a selected gate, and a whole-battery gate (no selection, or `all: <reason>`).

No GitHub call, no REST, no network: the commits come from the local clone and the runs from its runs/ folder.
The report does not show the time from Todo to landed: no local record stamps the move of a card to Todo.

usage: throughput.sh [--days N]   the last N calendar days, today included; default 3.
Exit 0 with a report, 2 when the base branch cannot be read.
"""
import datetime, os, pathlib, re, subprocess, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))   # the reader sits beside this script; a run under -I has the script's directory off sys.path
import child_issues   # the issues a request names: the reader the janitor's sibling passes share (#555)

BASE = os.environ.get("SG_BASE") or "dev"
REFS = re.compile(r"\bRefs #(\d+)\b")   # the landing line every commit carries, never a closing keyword
REVIEW = re.compile(r"^CHILD REVIEW \S+ \S+: .*\bVERDICT:\s*(APPROVE|CHANGES|BLOCKED)\b")
REQUEUE = ("CHILD REVIEW REQUEUE ", "CHILD FIX REQUEUE ")   # a CHANGES requeue, and the one after a fix run with unchanged findings (#636, #638)
VERDICTS = ("APPROVE", "CHANGES", "BLOCKED")


def days_window(n, today=None):
    """The n calendar dates of the window, newest first: today, yesterday, and so on."""
    today = today or datetime.date.today()
    return [(today - datetime.timedelta(days=i)).isoformat() for i in range(n)]


def git(*args):
    """Run one local git command: no remote is ever named, so nothing here reaches the network."""
    return subprocess.run(["/usr/bin/git", *args], capture_output=True, text=True)


def base_ref(checkout, base):
    """The local ref the log walks: the base branch, else the remote-tracking branch the local clone already holds."""
    for ref in (base, "origin/" + base):
        if git("-C", str(checkout), "rev-parse", "--verify", "--quiet", ref + "^{commit}").returncode == 0: return ref
    return None


def landed_per_day(checkout, ref, days):
    """Every issue the commits of the window name, per commit date; one issue named by two commits of a day counts once.
    The date is the committer date: a wave rebases its children onto the base as it lands them, so that date is the
    landing, not the day the child wrote the commit."""
    p = git("-C", str(checkout), "log", f"--since={days[-1]}T00:00:00", "--format=%cs%x1f%B%x1e", ref)
    if p.returncode != 0: return None
    window, per_day = set(days), {d: set() for d in days}
    for rec in p.stdout.split("\x1e"):
        date, sep, body = rec.strip("\n").partition("\x1f")
        if not sep or date not in window: continue   # git's own window is coarser than the calendar one
        per_day[date].update(int(n) for n in REFS.findall(body))
    return per_day


def runs_in_window(checkout, days):
    """The issues each run folder of the window names. A run folder with no request, or a request naming no issue,
    counts nothing -- there is no issue to credit the run to."""
    window, out = set(days), []
    for rd in sorted((pathlib.Path(checkout) / "runs").glob("run-*")):
        m = re.match(r"run-(\d{4})(\d{2})(\d{2})-", rd.name)
        if not m or "-".join(m.groups()) not in window: continue
        try: named = child_issues.named((rd / "request.txt").read_text(errors="replace"))
        except OSError: continue
        if named: out.append({int(n) for n in named})
    return out


def mean_runs_per_issue(runs):
    """Each issue's run folders counted, averaged over the issues a run of the window names."""
    count = {}
    for named in runs:
        for n in named: count[n] = count.get(n, 0) + 1
    if not count: return None
    return sum(count.values()) / len(count), sum(count.values()), len(count)


def child_reviews(state):
    """The verdict counts of watch.log, all lines: the log stamps no date, so no window narrows it."""
    counts = {v: 0 for v in VERDICTS}
    counts["REQUEUE"] = 0
    try: lines = (state / "watch.log").read_text(errors="replace").splitlines()
    except (OSError, TypeError): return None
    for line in lines:
        if line.startswith(REQUEUE): counts["REQUEUE"] += 1; continue
        m = REVIEW.search(line)
        if m: counts[m.group(1)] += 1
    return counts


def gate_mode(f):
    """The entry mode of one split gate-history row (#1798), '' for a row from before it. The mode ends a 17-field row; a row
    that carries the queue fields (#1856) ends with its reuse word, five fields after the mode. A reason may hold a tab, so
    the mode is found from the row's end."""
    if len(f) >= 22 and f[-1] in ("yes", "no") and f[-6] in ("gated", "reused", "skipped"): return f[-6]
    if len(f) >= 17 and f[-1] in ("gated", "reused", "skipped"): return f[-1]
    return ""


def gate_time(state):
    """The run time of each gate of gate-history, split by selection: a selected gate's selection field starts with
    `selected`; any other gate (an `all: <reason>` field, an empty field, or no field, as an older record has) ran the whole
    battery. A gate that ran no battery (#1798, entry mode `reused` or `skipped`, the seventeenth field: the last field of a
    17-field row, the sixth from the end of a row that carries the queue fields, #1856) is no gate run and counts in neither. A missing or empty file counts nothing. All lines: the history stamps dates, but the report reads every
    line as child_reviews does."""
    if state is None: return None
    groups = {"selected": [], "whole": []}
    try: lines = (state / "gate-history").read_text(errors="replace").splitlines()
    except OSError: lines = []
    for line in lines:
        f = line.split("\t")
        if len(f) < 5: continue
        if gate_mode(f) in ("reused", "skipped"): continue
        try: finish, start = int(f[0]), int(f[4])
        except ValueError: continue
        groups["selected" if len(f) > 5 and f[5].startswith("selected") else "whole"].append(finish - start)
    return groups


def gate_line(label, durations):
    total = sum(durations)
    mean = total / len(durations) if durations else 0.0
    return f"  {label}: {plural(len(durations), 'gate')}, {total} s total, {mean:.1f} s mean"


def plural(n, word): return f"{n} {word}{'' if n == 1 else 's'}"


def main(argv):
    days_n = 3
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-h", "--help"): print(__doc__.strip()); return 0
        if a == "--days" and i + 1 < len(argv): days_n = argv[i + 1]; i += 2; continue
        if a.startswith("--days="): days_n = a.split("=", 1)[1]; i += 1; continue
        print(f"throughput: unknown argument {a}\nusage: throughput.sh [--days N]", file=sys.stderr); return 2
    try: days_n = int(days_n)
    except ValueError: days_n = 0
    if days_n < 1:
        print("throughput: --days needs a whole number of days, at least 1", file=sys.stderr); return 2

    checkout = pathlib.Path(os.environ.get("SG_CHECKOUT") or os.getcwd())
    state = pathlib.Path(os.environ["SG_STATE"]) if os.environ.get("SG_STATE") else None
    days = days_window(days_n)
    ref = base_ref(checkout, BASE)
    if ref is None:
        print(f"throughput: no {BASE} branch in {checkout}: the report reads the base branch of a local clone, and fetches nothing",
              file=sys.stderr); return 2
    per_day = landed_per_day(checkout, ref, days)
    if per_day is None:
        print(f"throughput: git log of {ref} failed in {checkout}", file=sys.stderr); return 2
    issues = set().union(*per_day.values())
    mean = mean_runs_per_issue(runs_in_window(checkout, days))
    reviews = child_reviews(state)

    span = days[0] if len(days) == 1 else f"{days[-1]}..{days[0]}"
    print(f"Throughput {span} ({plural(len(days), 'day')})")
    print()
    print("Issues landed per day")
    for d in days: print(f"  {d}: {len(per_day[d])}")
    print(f"  total: {plural(len(issues), 'issue')}")
    print()
    if mean: print(f"Mean runs per issue: {mean[0]:.2f} ({plural(mean[1], 'run-issue pair')} over {plural(mean[2], 'issue')})")
    else: print("Mean runs per issue: none (no run of the window names an issue)")
    print()
    print("Child review verdicts (watch.log, all lines)")
    if reviews is None and state is None:
        print("  no SG_STATE: no verdict was read")
    elif reviews is None:
        print(f"  no watch.log at {state / 'watch.log'}: no verdict was read")
    else:
        for v in VERDICTS: print(f"  {v}: {reviews[v]}")
        print(f"  REQUEUE: {reviews['REQUEUE']}")
    print()
    print("Gate time (gate-history, all lines)")
    gates = gate_time(state)
    if gates is None: print("  no SG_STATE: no gate time was read")
    else:
        print(gate_line("selected", gates["selected"]))
        print(gate_line("whole battery", gates["whole"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
