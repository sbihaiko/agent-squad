"""The nightly settles the proof obligations a gate could not prove (spec 1163, ADR-0153): it reads the record, runs the
checks the record names over the tree the record names, writes settled-green or settled-red in one settlement line, clears
the record it answered, reports an overdue one by name, and a settled red holds the tip like any red. The nightly's guard
beside the script is `contrib/squad-goal/scripts/test-nightly.sh`; these cases prove the same seam from pytest, with the
suite's two mode commands and the settlement checks stubbed, so no real suite, model or network is touched."""
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
NIGHTLY = SCRIPTS / "nightly.sh"
GHCALL = SCRIPTS / "ghcall.sh"
G = ["git", "-c", "user.email=a@b", "-c", "user.name=t"]
pytestmark = pytest.mark.heavy   # a nightly run in a fixture tree is over 1 s


def _run(cmd, env=None, **kw):
    clean = {k: v for k, v in os.environ.items() if not k.startswith("SG_")}
    clean.update(env or {})
    return subprocess.run(cmd, capture_output=True, text=True, env=clean, timeout=120, **kw)


def _script(path, body):
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(0o755)
    return path


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
    sha = _run(["git", "rev-parse", "dev"], cwd=src).stdout.strip()
    st = tmp_path / "st"
    st.mkdir()
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
            "SG_SETTLE_RUFF": f"{settle} ruff", "SG_SETTLE_MYPY": f"{settle} mypy",
            "SG_SETTLE_PYTEST": f"{settle} pytest",
            **extra,
        }
        return _run(["bash", str(NIGHTLY)], env=env)

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

    return {"sha": sha, "st": st, "log": log, "run": run, "oblige": oblige, "settled": settled}


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
