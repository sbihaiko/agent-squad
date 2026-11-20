"""The nightly settles the proof obligations a gate could not prove (spec 1163, ADR-0153): it reads the record, runs the
checks the record names over the tree the record names, writes settled-green or settled-red in one settlement line, clears
the record it answered, reports an overdue one by name, and a settled red holds the tip like any red. The nightly's guard
beside the script is `contrib/squad-goal/scripts/test-nightly.sh`; these cases prove the same seam from pytest, with the
suite's two mode commands and the settlement checks stubbed, so no real suite, model or network is touched. The nightly
writes its result before it settles anything, a red settlement is never turned green, a tip obligation reuses the suite the
run already made, a default check over a tree without this repository's layout is reported unmeasured, and a stale red is
pruned and holds only while it is current."""
import os
import shutil
import signal
import subprocess
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
NIGHTLY = SCRIPTS / "nightly.sh"
GHCALL = SCRIPTS / "ghcall.sh"
G = ["git", "-c", "user.email=a@b", "-c", "user.name=t"]
pytestmark = pytest.mark.heavy   # a nightly run in a fixture tree is over 1 s


def _run(cmd, env=None, **kw):
    clean = {k: v for k, v in os.environ.items() if not k.startswith("SG_")}
    clean["SG_NIGHTLY_HOUR"] = datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%H")   # the suite runs in its window whatever the hour
    clean["SG_NIGHTLY_BOUND_SECONDS"] = "3600"   # the real minute never stops a case at its hour's end
    clean["SG_NIGHTLY_NEED_MINUTES"] = "0"   # the real minute never holds a case short of the suite's time (#2527)
    clean["SG_GHREAD"] = "false"   # no case reads GitHub: the nightly verdict reads the GitHub half through this door (#1975)
    clean.update(env or {})
    return subprocess.run(cmd, capture_output=True, text=True, env=clean, timeout=120, **kw)


def _script(path, body):
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(0o755)
    return path


STATES = []   # every state directory a case in this file ran the nightly against


def _alive(pid):
    """Whether this pid still names a live process."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _named_pids(st):
    """The pids the live check this state directory started leaves: its serial lock names the door first (its TERM trap
    stops the reading's whole group), then its lease names the reading's group leader."""
    pids = []
    for rel in (("locks", "live-check", "pid"), ("live-check.lease", "pid")):
        try:
            raw = st.joinpath(*rel).read_text().strip()
        except OSError:
            continue
        if raw.isdigit():
            pids.append(int(raw))
    return pids


def _end_check(pid):
    """End a check by pid, and the reading it leads by its own process group: serial.sh runs the door in a group of its
    own, never this suite's, so the group is signalled only when it is not this process's group."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            group = os.getpgid(pid)
        except ProcessLookupError:
            return
        try:
            if group != os.getpgid(0):
                os.killpg(group, sig)
            else:
                os.kill(pid, sig)
        except ProcessLookupError:
            return
        for _ in range(20):
            if not _alive(pid):
                return
            time.sleep(0.02)


def _end_checks(st):
    """End every check this state directory still names; bounded, and a check that survives the bound raises, so a leak
    is loud instead of a silent loop."""
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        pids = [pid for pid in _named_pids(st) if _alive(pid)]
        if not pids:
            return
        for pid in pids:
            _end_check(pid)
        time.sleep(0.05)
    left = [pid for pid in _named_pids(st) if _alive(pid)]
    if left:
        raise RuntimeError(f"the live check this case started is still alive: {left}")


@pytest.fixture()
def nightly(tmp_path):
    """A throwaway bare remote with one commit on dev, a state directory, and stubs for the two modes and the checks."""
    src = tmp_path / "src"
    src.mkdir()
    _run(["git", "init", "-q", "-b", "dev"], cwd=src)
    _run([*G, "commit", "-q", "--allow-empty", "-m", "one"], cwd=src)
    origin = tmp_path / "origin.git"
    _run(["git", "init", "-q", "--bare", str(origin)], cwd=tmp_path)
    _run([*G, "push", "-q", str(origin), "dev"], cwd=src)
    old = _run(["git", "rev-parse", "dev"], cwd=src).stdout.strip()
    # A second commit makes `old` an older tree that is not the tip, so an obligation can name either.
    _run([*G, "commit", "-q", "--allow-empty", "-m", "two"], cwd=src)
    _run([*G, "push", "-q", str(origin), "dev"], cwd=src)
    sha = _run(["git", "rev-parse", "dev"], cwd=src).stdout.strip()
    st = tmp_path / "st"
    st.mkdir()
    STATES.append(st)
    mode = _script(tmp_path / "mode.sh", "exit 0\n")
    log = tmp_path / "settle.log"
    settle = _script(
        tmp_path / "settle.sh",
        f'echo "$1 $PWD $(git rev-parse HEAD 2>/dev/null)" >> "{log}"\n'
        'case " ${FAIL_SETTLE:-} " in *" $1 "*) exit 1;; esac\nexit 0\n',
    )

    def run(**extra):
        env = {
            "SG_STATE": str(st), "SG_REPO": "o/r", "SG_BASE": "dev",
            "SG_NIGHTLY_REMOTE": str(origin), "SG_NIGHTLY_TREE": str(tmp_path / "tree"),
            "SG_NIGHTLY_PARALLEL": str(mode), "SG_NIGHTLY_SERIAL": str(mode),
            "HEALTH_NOTIFY": "true",
            # nightly.sh starts its live check detached; the live default window is one hour, so a case gives it seconds
            # of its own and ends it by pid (the fixture's finalizer), instead of leaving it to sample past the suite.
            "SG_LIVE_CHECK_WINDOW_SECONDS": "0", "SG_LIVE_CHECK_INTERVAL_SECONDS": "0.1",
            "SG_SETTLE_RUFF": f"{settle} ruff", "SG_SETTLE_MYPY": f"{settle} mypy",
            "SG_SETTLE_PYTEST": f"{settle} pytest",
            **extra,
        }
        return _run(["bash", str(NIGHTLY)], env=env)

    def mark():
        """Drop the once-a-day marker so a case can run the nightly again."""
        for p in (st / "auto").glob("nightly-*"):
            p.unlink()

    def again(**extra):
        mark()
        return run(**extra)

    def oblige(checks, scope, deadline, tree=None):
        d = st / "obligations"
        d.mkdir(parents=True, exist_ok=True)
        repo, tree_sha, base = "o/r", tree or sha, "-"
        key = _run(["sh", "-c", f'printf "%s\\t%s\\t%s" "{repo}" "{tree_sha}" "{base}" | cksum | cut -d" " -f1']).stdout.strip()
        (d / key).write_text(f"{repo}\t{tree_sha}\t{base}\t{checks}\t{scope}\t{deadline}\n")

    def settled():
        d = st / "obligation-settled"
        files = sorted(p for p in d.glob("*") if p.is_file()) if d.is_dir() else []
        assert len(files) == 1, f"settlements: {files}"
        return files[0].read_text().rstrip("\n").split("\t")

    try:
        yield {"sha": sha, "old": old, "st": st, "log": log, "run": run, "again": again,
               "oblige": oblige, "settled": settled}
    finally:
        _end_checks(st)   # the case ends every check it started, by pid, also when it failed


def test_a_green_settlement_clears_the_obligation_and_names_the_run(nightly):
    """The named checks run over the named tree, the record is cleared, and the settlement names the run that made it."""
    nightly["oblige"]("mypy pytest", "delta", "9999999999")
    res = nightly["run"]()
    assert res.returncode == 0, res.stderr
    assert not list((nightly["st"] / "obligations").glob("*"))
    ts, repo, sha, base, scope, checks, verdict, run = nightly["settled"]()
    assert (repo, sha, scope, verdict) == ("o/r", nightly["sha"], "full", "green")
    result = (nightly["st"] / "nightly-result").read_text().split("\t")
    assert run == f"{result[0]}@{nightly['sha']}"
    lines = nightly["log"].read_text().splitlines()
    assert lines == [f"mypy {nightly['st'].parent / 'tree'} {nightly['sha']}"]
    # The full suite this run already made answers pytest at the tip, so no second suite runs there (ADR-0168).
    assert [line.split()[0] for line in lines] == ["mypy"]


def test_a_case_runs_the_nightly_at_any_minute_of_its_hour(nightly, tmp_path):
    """The hold that keeps a suite from starting too late in its hour reads the wall clock, so a case reads the minute
    through a date that names minute 40 and still runs the nightly."""
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    real_date = shutil.which("date")
    _script(shim_dir / "date", f'if [ "$1" = "+%M:%S" ]; then echo "40:00"; exit 0; fi\nexec {real_date} "$@"\n')
    res = nightly["run"](PATH=f"{shim_dir}{os.pathsep}{os.environ['PATH']}")
    assert res.returncode == 0, res.stderr
    assert "held" not in res.stdout, res.stdout
    assert (nightly["st"] / "nightly-result").is_file()


def test_an_overdue_obligation_is_reported_by_name_and_still_measured(nightly):
    """A nightly is always past the gate's bound, so an overdue record is named in the alert and then settled."""
    nightly["oblige"]("mypy", "delta", "1000000000")
    res = nightly["run"]()
    assert res.returncode == 0, res.stderr
    alert = (nightly["st"] / "alerts.log").read_text()
    assert "OBLIGATION overdue" in alert
    assert nightly["sha"][:7] in alert and "mypy" in alert
    assert nightly["settled"]()[6] == "green"


def test_a_red_settlement_holds_the_tip_like_any_red(nightly):
    """A settlement that measured a defect settles red, and the dispatch hold stops a run at the tree it measured."""
    nightly["oblige"]("ruff", "delta", "9999999999")
    res = nightly["run"](FAIL_SETTLE="ruff")
    assert res.returncode == 1
    assert nightly["settled"]()[6] == "red"
    hold = _run(["bash", "-c", f'. "{GHCALL}"; gh_dispatch_hold'],
                env={"SG_STATE": str(nightly["st"]), "SG_CHECKOUT": str(nightly["st"].parent / "src")})
    assert hold.returncode == 0, hold.stderr
    assert nightly["sha"][:7] in hold.stdout and "obligation" in hold.stdout


def test_a_settled_red_is_never_turned_green_by_a_later_pass(nightly):
    """A red settlement stands when the same entry is measured again and its check passes: the run fails and the tip stays held."""
    nightly["oblige"]("ruff", "delta", "9999999999")
    assert nightly["run"](FAIL_SETTLE="ruff").returncode == 1
    assert nightly["settled"]()[6] == "red"
    nightly["oblige"]("ruff", "delta", "9999999999")
    again = nightly["again"]()
    assert again.returncode == 1, again.stderr
    assert nightly["settled"]()[6] == "red"
    hold = _run(["bash", "-c", f'. "{GHCALL}"; gh_dispatch_hold'],
                env={"SG_STATE": str(nightly["st"]), "SG_CHECKOUT": str(nightly["st"].parent / "src")})
    assert hold.returncode == 0 and "obligation" in hold.stdout


def test_a_hung_settlement_leaves_the_nightly_result_written(nightly, tmp_path):
    """A settlement check that hangs is stopped at the serial hold, and the result main already wrote is there before it runs."""
    hang = _script(tmp_path / "hang.sh", "sleep 60\n")
    nightly["oblige"]("ruff", "delta", "9999999999")
    res = nightly["run"](SERIAL_HOLD_SECONDS="6", SG_SETTLE_RUFF=f"{hang} ruff")
    fields = (nightly["st"] / "nightly-result").read_text().split("\t")
    assert fields[1] == "pass" and fields[2] == nightly["sha"]
    assert list((nightly["st"] / "obligations").glob("*")), "the hung obligation was dropped"
    assert res.returncode == 75, res.stderr


def test_a_tip_obligation_reuses_the_suite_after_an_older_tree(nightly):
    """A tip obligation never runs the full suite twice, whichever tree a record before it measured (ADR-0168)."""
    nightly["oblige"]("ruff", "delta", "9999999999", tree=nightly["old"])
    nightly["oblige"]("pytest", "delta", "9999999999")
    res = nightly["run"]()
    assert res.returncode == 0, res.stderr
    assert len(list((nightly["st"] / "obligation-settled").glob("*"))) == 2
    lines = nightly["log"].read_text().splitlines()
    assert [line.split()[0] for line in lines] == ["ruff"], lines
    assert lines[0] == f"ruff {nightly['st'].parent / 'tree'} {nightly['old']}"


def test_a_default_check_the_tree_cannot_measure_is_reported_not_settled(nightly):
    """The default commands read this repository's layout; a tree without those paths measures nothing, it is never red."""
    nightly["oblige"]("ruff mypy", "delta", "9999999999")
    res = nightly["run"](SG_SETTLE_RUFF="", SG_SETTLE_MYPY="")
    assert res.returncode == 0, res.stderr
    assert list((nightly["st"] / "obligations").glob("*")), "the unmeasured obligation was dropped"
    assert not list((nightly["st"] / "obligation-settled").glob("*"))
    alert = (nightly["st"] / "alerts.log").read_text()
    assert "OBLIGATION unmeasured" in alert and nightly["sha"][:7] in alert


def test_a_mode_that_never_finished_is_retried_and_keeps_its_first_log(nightly, tmp_path):
    """A mode that ends before pytest's summary is run once more; the retry's summary is the record, and the first log,
    with the OSError that names the cause, stays beside it (#1201)."""
    n = tmp_path / "retry.n"
    retry = _script(
        tmp_path / "retry.sh",
        f'n=$(cat "{n}" 2>/dev/null || echo 0); echo $((n+1)) > "{n}"\n'
        "[ \"$n\" = 0 ] && { printf 'F..\\nOSError: cannot send (already closed?)\\n'; exit 143; }\n"
        "printf '22 passed in 1.00s\\n'; exit 0\n",
    )
    res = nightly["run"](SG_NIGHTLY_PARALLEL=str(retry))
    assert res.returncode == 0, res.stderr
    fields = (nightly["st"] / "nightly-result").read_text().split("\t")
    assert (fields[1], fields[3]) == ("pass", "pass")
    assert "22 passed in 1.00s" in (nightly["st"] / "nightly-parallel.log").read_text()
    assert "cannot send" in (nightly["st"] / "nightly-parallel.log.first").read_text()


def test_a_bounded_full_suite_never_settles_a_pytest_obligation_red(nightly, tmp_path):
    """A suite a signal stopped measured no red, so the obligation that needs pytest stays open: the nightly never answers
    it with a red the suite did not find (#1183, ADR-0164)."""
    killed = _script(tmp_path / "killed.sh", "printf 'F..\\nOSError: cannot send (already closed?)\\n'\nexit 143\n")
    nightly["oblige"]("pytest", "delta", "9999999999")
    res = nightly["run"](SG_NIGHTLY_PARALLEL=str(killed))
    fields = (nightly["st"] / "nightly-result").read_text().split("\t")
    assert fields[1] == "bounded"
    assert res.returncode == 1
    assert list((nightly["st"] / "obligations").glob("*")), "a bounded suite dropped the pytest obligation"
    assert not list((nightly["st"] / "obligation-settled").glob("*")), "a bounded suite settled the obligation"


def test_a_stale_settled_red_is_pruned_and_holds_only_while_current(nightly):
    """Red records never collect: one off the tip, or older than a nightly period, goes with a report; a pass with no tip
    holds only for a red from this period."""
    d = nightly["st"] / "obligation-settled"
    d.mkdir()

    def write(tree, age, run):
        key = _run(["sh", "-c", f'printf "%s\\t%s\\t%s" "o/r" "{tree}" "-" | cksum | cut -d" " -f1']).stdout.strip()
        (d / key).write_text(f"{int(time.time()) - age}\to/r\t{tree}\t-\tfull\truff\tred\t{run}\n")
        return d / key

    stale = write(nightly["old"], 90000, "dead@old")
    write(nightly["sha"], 60, f"x@{nightly['sha']}")
    res = nightly["run"]()
    assert res.returncode == 0, res.stderr
    assert not stale.exists(), "the red at a tree that is not the tip was not pruned"
    alert = (nightly["st"] / "alerts.log").read_text()
    assert "OBLIGATION pruned" in alert and nightly["old"][:7] in alert

    def hold():
        return _run(["bash", "-c", f'. "{GHCALL}"; gh_dispatch_hold'], env={"SG_STATE": str(nightly["st"])})

    write(nightly["sha"], 90000, f"dead@{nightly['sha']}")
    assert hold().returncode == 1, "an old red held a pass that cannot read the tip"
    write(nightly["sha"], 0, f"now@{nightly['sha']}")
    current = hold()
    assert current.returncode == 0 and "obligation" in current.stdout


def test_a_check_the_nightly_starts_is_ended_by_pid(nightly):
    """nightly.sh starts the live check detached, so the case that ran it owes its end (#1722). This case keeps the
    production window (one hour), so the check is still alive when the nightly returns: a suite that does not end it leaks
    a `live-check.sh --run` and its `squad.livecheck` reading for that whole hour, orphaned to pid 1. The case proves the
    check is alive, ends it by pid and group, and proves it is gone, so a green run cannot pass on the check's own timer."""
    assert nightly["run"](SG_LIVE_CHECK_WINDOW_SECONDS="3600").returncode == 0
    state = nightly["st"]
    pid = None
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and pid is None:   # the check takes its lease a moment after the nightly body returns
        pids = _named_pids(state)
        pid = pids[0] if pids else None
        if pid is None:
            time.sleep(0.02)
    assert pid is not None, "the nightly started no live check for this case to end"
    time.sleep(0.2)                       # a short fixed wait, far below the hour-long window
    assert _alive(pid), "the default-window check ended on its own, so this case would not prove an end by pid"
    _end_checks(state)
    assert not [p for p in _named_pids(state) if _alive(p)], "the check the nightly started is still alive"


def test_no_check_a_nightly_case_started_is_alive_when_the_file_ends():
    """nightly.sh starts its live check detached, so the case that ran it owes its end (#1722). The live window is one
    hour, and a case that leaves the check to that timer keeps a `live-check.sh --run` and its `squad.livecheck` reading
    alive for the whole hour after the suite is gone, orphaned to pid 1: a promotion proof leaves one such tree per case.
    Every case that ran a nightly is checked here, by the pid its state directory still names."""
    left = [(st, pid) for st in STATES for pid in _named_pids(st) if _alive(pid)]
    assert not left, f"a live check a night case started is still alive after its case: {left}"
