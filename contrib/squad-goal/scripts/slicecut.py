#!/usr/bin/env python3
"""usage: slicecut.py decide <issues.json> <issue> [<issue>...]
          slicecut.py release <issues.json>
The cross-run half of ADR-0150 (#1504): a run never holds two issues of one file, and now two runs never cut two branches
of one file from the base either. The first issue of a file takes the branch it needs (cut from the base); every later
issue of that file is cut from THAT branch -- stacked -- so the file is edited by one line of commits, and only the first
branch lands in the base. The issue-to-file map is the one ADR-0150 already uses (cluster.paths over the issue bodies).

decide reads the open-issue list and, for each issue, prints one line:
  CUT <branch> <sha>              no live holder names a file of this issue: the branch is cut from the base
  STACK <branch> <parent> <sha>   a live branch holds one of its files: the branch is cut from that branch (stacked)
  KEEP <branch> <sha>             the branch already exists: it is never moved, and no work of an earlier run is lost
  HOLD <branch> <file> <parent> mid-land
                                  the holding branch is mid-land: cut nothing now, and a later pass decides again
A KEEP on a branch that sits on a live holder also prints `BASE <parent>`: the work belongs on that branch.

Live means the issue a dispatch marker (auto/disp-<n>, auto/bug-<n>) holds, or a run without a final verdict works it.
Mid-land means the holding run's wave landing pass is alive (auto/wland-<run>.busy, watch.sh). The rule is the one the
owner set for #1504: stack when the holder is live and not landing, hold only while it is mid-land.

Two dispatchers that decide at once must not both cut from the base. The decision and the branch it makes run under one
lock (locks/slice-cut), and the branch is the claim: the facts are read again inside the lock, so the second decision sees
the first one's branch and stacks on it. A branch that another launch makes first is verified, never overwritten.

release runs after a landing: a branch of an open issue that no live run and no worktree holds, and whose commits are all
in the base now (by sha, or by patch after the wave rebased them), moves onto the base. A held issue needs no release:
it has no branch, so the next decide cuts it from the base.

No persisted state of its own: the inputs are the issue list, the dispatch markers, the run requests and records, and
git. The lock is transient and a branch is the only thing this writes.
"""
import json, os, pathlib, re, shutil, subprocess, sys, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cluster import paths  # noqa: E402  the one normal form of a repository path (ADR-0150)

STATE = pathlib.Path(os.environ.get("SG_STATE") or ".")
CO = pathlib.Path(os.environ.get("SG_CHECKOUT") or ".")
BASE = os.environ.get("SG_BASE") or "main"
FORK = os.environ.get("SG_FORK_BRANCH") or "gui/%n"
AUTO = STATE / "auto"
LOCK = STATE / "locks" / "slice-cut"
VERDICT = re.compile(r'"event"\s*:\s*"verdict"')
CHILD = re.compile(r"^CHILD[^\n]*?GitHub issue\(?s?\)? ?#? ?([0-9][0-9, ]*)", re.M)
HOLD_GRACE = 10   # s: a lock with no pid after this is a holder that died between mkdir and its write (single.sh's rule)


def git(*a, cwd=None):
    return subprocess.run(["/usr/bin/git", "-C", str(cwd or CO), *a], capture_output=True, text=True)


def branch_of(n):
    return FORK.replace("%n", str(n))


def tip(ref):
    r = git("rev-parse", "--verify", "--quiet", f"refs/heads/{ref}^{{commit}}")
    return r.stdout.strip() if r.returncode == 0 else None


def base_tip():
    for ref in (f"refs/remotes/origin/{BASE}", f"refs/heads/{BASE}"):
        r = git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        if r.returncode == 0:
            return r.stdout.strip()
    return None


def load(argv):
    by = {}
    for it in json.load(open(argv[0])):
        if isinstance(it, dict) and isinstance(it.get("number"), int):
            by[it["number"]] = it
    return by


def files(n, by):
    return paths(by.get(n, {}).get("body") or "")


def alive(pid):
    """A live pid, never a zombie: single.sh's rule, since a holder whose parent has not reaped it still answers kill -0.
    A ps that this session may not run is no answer against the pid, so the pid itself decides."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True   # a pid that exists but is not ours: alive
    try:
        r = subprocess.run([os.environ.get("SG_PS") or "ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    except OSError:
        return True
    return r.returncode == 0 and bool(r.stdout.strip()) and not r.stdout.strip().startswith("Z")


def run_folder(name):
    return pathlib.Path(name) if name.startswith("/") else CO / "runs" / name


def live_runs():
    """{run folder basename: set(issues)} of the runs whose delivery has not ended: a record with no verdict, or no record
    yet inside its launch window. A run that cannot be read holds the issues its request names: an unknown delivery state
    is a hold, never a free file."""
    rows = []
    try:
        rows = (STATE / "active-runs.txt").read_text().split()
    except OSError:
        pass
    out = {}
    for row in rows:
        f = run_folder(row)
        rec, rq = f / "record.jsonl", f / "request.txt"
        try:
            text = rec.read_text()
        except OSError:
            text = None
        if text is not None and VERDICT.search(text):
            continue   # the run's last word is in: nothing it held is in flight
        try:
            names = {int(x) for m in CHILD.findall(rq.read_text()) for x in re.split(r"[^0-9]+", m) if x}
        except OSError:
            names = set()
        if names:
            out[f.name] = names
    return out


def live_issues(by):
    """{issue: run name or ""} of every issue whose work is in flight: a dispatch marker of either dispatcher, or a live
    run's request. A marker whose branch is not cut yet is a decision in flight under the same lock, never a reason to
    cut from the base beside it."""
    live = {}
    for n in by:
        if (AUTO / f"disp-{n}").exists() or (AUTO / f"bug-{n}").exists():
            live[n] = ""
    for run, names in live_runs().items():
        for n in names:
            if not live.get(n):   # the run wins over a marker alone: its landing is what a hold reads
                live[n] = run
    return live


def mid_land(run):
    """The holding run's wave landing pass is alive (watch.sh writes auto/wland-<run>.busy). No run named means a marker
    only: nothing is landing."""
    if not run:
        return False
    try:
        pid = (AUTO / f"wland-{run}.busy").read_text().strip()
    except OSError:
        return False
    return alive(pid)


def take_lock():
    try:
        LOCK.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    for _ in range(100):   # 5 s: a decision is milliseconds, so a lock held longer is a stuck holder, named, never waited on forever
        try:
            LOCK.mkdir()
        except FileExistsError:
            try:
                pid = (LOCK / "pid").read_text().strip()
            except OSError:
                pid = ""
            if pid and not alive(pid):
                shutil.rmtree(LOCK, ignore_errors=True)
                continue
            if not pid:
                try:
                    if time.time() - (LOCK / "pid").stat().st_mtime > HOLD_GRACE:
                        shutil.rmtree(LOCK, ignore_errors=True)
                        continue
                except OSError:
                    pass
            time.sleep(0.05)
            continue
        try:
            (LOCK / "pid").write_text(str(os.getpid()))
        except OSError:
            pass
        return True
    return False


def drop_lock():
    try:
        if (LOCK / "pid").read_text().strip() == str(os.getpid()):
            shutil.rmtree(LOCK, ignore_errors=True)
    except OSError:
        pass


def cut(b, sha):
    """Make the branch at sha, and verify it: a branch another launch made first is the one the child uses, never
    overwritten (claim-then-verify). Returns the tip, or None when the branch does not answer."""
    r = git("branch", b, sha)
    t = tip(b)
    if r.returncode and t != sha:
        return None   # the branch exists at another commit: it holds work, and this decision may not move it
    return t or sha


def decide(by, n):
    live = live_issues(by)
    cand_files = files(n, by)
    b = branch_of(n)
    holders = []   # (issue, branch, tip, shared file, run name or "")
    for h in sorted(live):
        if h == n or h not in by:
            continue
        shared = cand_files & files(h, by)
        if not shared:
            continue
        hb, ht = branch_of(h), tip(branch_of(h))
        if not ht:
            continue   # the holder's own decision is in flight under this lock; its branch is not cut yet
        holders.append((h, hb, ht, min(shared), live[h]))
    stack = [x for x in holders if not mid_land(x[4])]
    keep = tip(b)
    if keep:
        print(f"KEEP {b} {keep}")
        if stack:
            print(f"BASE {stack[0][1]}")
        return 0
    if stack:
        h, hb, ht, _, _ = stack[0]
        made = cut(b, ht)
        if not made:
            print(f"NOTE {b}: the branch answers at another commit; it was not moved", file=sys.stderr)
            return 1
        print(f"STACK {b} {hb} {made}")
        return 0
    if holders:
        _, hb, _, f, _ = holders[0]
        print(f"HOLD {b} {f} {hb} mid-land")
        return 0
    base = base_tip()
    if not base:
        print(f"NOTE no {BASE} to cut {b} from", file=sys.stderr)
        return 1
    made = cut(b, base)
    if not made:
        print(f"NOTE {b}: the branch answers at another commit; it was not moved", file=sys.stderr)
        return 1
    print(f"CUT {b} {made}")
    return 0


def branches_with_worktree():
    """{branch name} of the local branches some worktree holds: a child may be running there, so nothing rebases them."""
    out = set()
    r = git("worktree", "list", "--porcelain")
    for line in r.stdout.splitlines():
        if line.startswith("branch refs/heads/"):
            out.add(line[len("branch refs/heads/"):])
    return out


def rebase_onto(b, onto):
    """Rebase branch b onto `onto` in a throwaway detached worktree, so the loop's own checkout is never touched."""
    tmp = tempfile.mkdtemp(prefix="slicecut-")
    try:
        if git("worktree", "add", "-q", "--detach", tmp, f"refs/heads/{b}").returncode:
            return None, "cannot read the branch"
        r = subprocess.run(["/usr/bin/git", "-C", tmp, "rebase", onto], capture_output=True, text=True)
        if r.returncode:
            subprocess.run(["/usr/bin/git", "-C", tmp, "rebase", "--abort"], capture_output=True)
            return None, (r.stderr.strip().splitlines() or ["the rebase failed"])[-1]
        head = subprocess.run(["/usr/bin/git", "-C", tmp, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        if not head:
            return None, "the rebase left no commit"
        git("branch", "-f", b, head)
        return head, ""
    finally:
        subprocess.run(["/usr/bin/git", "-C", str(CO), "worktree", "remove", "--force", tmp], capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)


def release(by):
    base = base_tip()
    if not base:
        print(f"NOTE no {BASE} to release onto", file=sys.stderr)
        return 1
    live, wts, rc = live_issues(by), branches_with_worktree(), 0
    for n in sorted(by):
        b, t = branch_of(n), tip(branch_of(n))
        if not t or n in live or b in wts:
            continue   # a live run or a worktree owns the branch: never moved under it
        ahead = git("rev-list", "--count", f"{base}..{t}").stdout.strip()
        if ahead not in ("", "0"):
            # The branch has commits the base lacks. It is all stacked base when every one of them is already in the base
            # by patch (a landing that rebased them gave them new shas): then a rebase drops the duplicates and keeps
            # nothing else. A branch with one commit of its own is somebody's work and is left alone.
            lines = [ln for ln in git("cherry", base, t).stdout.splitlines() if ln.strip()]
            if not lines or any(ln.startswith("+") for ln in lines):
                continue
        if t == base:
            continue
        new, why = rebase_onto(b, base)
        if not new:
            print(f"RELEASE FAILED {b}: {why}")
            rc = 1
            continue
        print(f"RELEASE {b} {t} {new}")
    return rc


def main(argv):
    if len(argv) < 2 or argv[0] not in ("decide", "release"):
        print(__doc__, file=sys.stderr)
        return 2
    by = load(argv[1:])
    if not isinstance(by, dict):
        print("slicecut: the issue list is not readable", file=sys.stderr)
        return 2
    if not take_lock():
        print(f"NOTE the slice-cut lock is held past its bound: nothing decided this pass ({LOCK})", file=sys.stderr)
        return 3
    try:
        if argv[0] == "decide":
            rc = 0
            for x in argv[2:]:
                rc |= decide(by, int(x))
            return rc
        return release(by)
    finally:
        drop_lock()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
