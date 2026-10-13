"""The throttle's first named consumer: the landing pass (#4, ADR-0277).

`forkclose.sh`'s eligible-branch collection is the loop's heaviest reader, and it is the pass the bucket is sized for: it
reads the open-PR list once and then two routes per candidate (the issue's state and its `Blocked by` body). These cases
run the real script against a throwaway fork with a stub `gh` that records every route it is asked for, so they hold two
things at once:

* the burst the module documents (`landing_pass_burst`) IS this pass's burst -- measured from the pass, not asserted
  against a number that was written down twice; and
* the pass reads through the interface: with a cooldown holding, the stub is never asked for a route at all. A pass that
  read around the throttle would reach GitHub here exactly as it did on 2026-10-12, when an exhausted quota read as an
  empty eligible set and the log said nothing.

No network, no board, no model: the stub `gh`, the throwaway fork and the temp state dir are the whole environment.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "contrib/squad-goal/scripts"

RATE_LIMIT_MESSAGE = "API rate limit exceeded for user ID 122891372."
REQUEST_ID = "06F8:34CAED:22CD6B6:3FC4397:6ACC6C2A"

STUB_GH = """#!/bin/bash
# The one place a read reaches "GitHub" here: it records the route it was asked for and answers it.
# ONE line per request: since #5 asks for a fact in one GraphQL request, the query spans many argv lines, and counting
# lines would count one request as twenty-five.
printf '%s\\n' "${{*//$'\\n'/ }}" >> "{log}"
case "$*" in
  *"pulls?state=open"*) printf '%s' '[{{"number":7,"title":"Refs #99","body":""}}]';;
  *issues/*) printf '%s' '{{"state":"open","body":"no blockers here"}}';;
  *) printf '%s' '[]';;
esac
"""


def _module(name: str):
    if str(SCRIPTS) not in sys.path:  # `ghthrottle` imports its neighbour `ghread` by name, as the script does
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec and spec.loader, f"cannot load {SCRIPTS / (name + '.py')}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ghthrottle = _module("ghthrottle")


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["/usr/bin/git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "maintenance.auto=false", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


def _fixture(tmp_path: Path, candidates: tuple[int, ...]) -> Path:
    """A fork with one branch per candidate, a board card for each, a stub `gh`, and the scripts under test."""
    s = tmp_path / "s"
    s.mkdir()
    for name in ("forkclose.sh", "forkonly.sh", "gate-suites.sh", "ghthrottle.py", "ghread.py", "ghcall.sh"):
        if (SCRIPTS / name).exists():
            shutil.copy(SCRIPTS / name, s / name)
    (tmp_path / "st").mkdir()
    (tmp_path / "home").mkdir()
    hub = tmp_path / "co" / "runs" / ".hub"
    hub.mkdir(parents=True)
    (hub / "hub-board.json").write_text(json.dumps({"items": [{"n": n, "st": "Code Complete", "s": "open"} for n in candidates]}))
    b = tmp_path / "bin"
    b.mkdir()
    stub = b / "gh"
    stub.write_text(STUB_GH.format(log=tmp_path / "gh.log"))
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    (s / "env.sh").write_text(
        f"export SG_STATE={tmp_path / 'st'} SG_FORK_DIR={tmp_path / 'fork'} SG_FORK_BASE=local/next SG_FORK_BRANCH=gui/%n"
        f" SG_REPO=x/y SG_CHECKOUT={tmp_path / 'co'} SG_FORK_TEST_CMD=true SG_OWNER=x SG_PROJECT=4 BOARD=true\n"
    )
    fork = tmp_path / "fork"
    fork.mkdir()
    _git(fork, "init", "-q", "-b", "local/next")
    (fork / "f").write_text("0\n")
    _git(fork, "add", "f")
    _git(fork, "commit", "-q", "-m", "base")
    for n in candidates:  # each candidate's fork branch is ahead of the base, which is what makes it eligible
        _git(fork, "checkout", "-q", "-b", f"gui/{n}", "local/next")
        (fork / f"f{n}").write_text(f"{n}\n")
        _git(fork, "add", f"f{n}")
        _git(fork, "commit", "-q", "-m", f"work {n}")
        _git(fork, "checkout", "-q", "local/next")
    (tmp_path / "cands").touch()
    return tmp_path


def _collect(t: Path) -> tuple[str, list[str], list[str]]:
    """Run the eligible-branch collection once: returns the pass's output, the routes the stub was asked for, its set."""
    (t / "gh.log").unlink(missing_ok=True)  # each pass is measured on its own: the routes it asked for, not the ones before
    env = {k: v for k, v in os.environ.items() if not k.startswith(("SG_", "FC_"))}
    env.update(
        PATH=f"{t / 'bin'}:{env['PATH']}",
        HOME=str(t / "home"),
        SG_STATE=str(t / "st"),
        FC_COLLECT=str(t / "cands"),
    )
    p = subprocess.run(
        ["bash", str(t / "s" / "forkclose.sh")], cwd=t, capture_output=True, text=True, env=env, check=False, timeout=120
    )
    log = t / "gh.log"
    routes = [line.split("--include ")[-1] for line in log.read_text().splitlines()] if log.exists() else []
    return p.stdout + p.stderr, routes, (t / "cands").read_text().split()


def test_the_collection_passes_burst_is_the_burst_the_throttle_documents(tmp_path):
    t = _fixture(tmp_path, candidates=(1, 2))
    out, routes, collected = _collect(t)
    assert collected == ["1", "2"], f"the collection read nothing: {out}"
    # The pass reads the open PRs, the open issues and one batch for the candidates the open list lacks: three reads, whatever
    # the candidate count (#45). This stub cannot shape the batch, so each candidate falls back to its own read -- the worst
    # case, which the bucket still covers: the fixed reads plus one per candidate. Against a real GitHub the batch answers.
    assert len(routes) <= ghthrottle.landing_pass_burst(len(collected)) + len(collected), (
        f"the pass asked GitHub for {len(routes)} requests, the throttle sizes its budget for {ghthrottle.landing_pass_burst(2)} plus one per candidate: {routes}"
    )
    assert "pullRequests(states: OPEN" in routes[0], f"the open-PR fact is the first read, in one request: {routes}"
    assert "issues(states: OPEN" in routes[1], f"the candidates' facts come from one list, not one read each: {routes}"


def test_the_collection_sends_nothing_at_all_while_the_cooldown_holds(tmp_path):
    """The pass that read against an exhausted quota must say so, and must not touch the network to find out."""
    t = _fixture(tmp_path, candidates=(1, 2))
    (t / "st" / "gh-cooldown.json").write_text(
        json.dumps({"until": time.time() + 600, "message": RATE_LIMIT_MESSAGE, "request_id": REQUEST_ID, "pid": 1})
    )
    out, routes, collected = _collect(t)
    assert routes == [], f"the cooldown held and the pass still reached GitHub: {routes}"
    assert collected == [], f"nothing is eligible while every read is refused: {collected}"
    assert "refused" in out and RATE_LIMIT_MESSAGE in out, f"the pass did not say why it collected nothing: {out}"


def test_the_cooldown_the_pass_writes_is_the_one_the_next_pass_reads(tmp_path):
    """A pass that is answered with a self-attributed 403 leaves the cooldown behind for whatever runs next."""
    t = _fixture(tmp_path, candidates=(1,))
    stub = t / "bin" / "gh"
    stub.write_text(
        f'#!/bin/bash\nprintf \'%s\\n\' "${{*//$\'\\n\'/ }}" >> "{t}/gh.log"\nprintf \'%s\' \'{{"message":"{RATE_LIMIT_MESSAGE}","status":"403"}}\'\nexit 1\n'
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    out, routes, collected = _collect(t)
    assert routes, f"the first pass has to spend a request to be refused: {out}"
    cooldown = json.loads((t / "st" / "gh-cooldown.json").read_text())
    assert cooldown["until"] > time.time(), "the refusal must leave a cooldown behind"
    assert cooldown["message"] == RATE_LIMIT_MESSAGE
    second_out, second_routes, second_collected = _collect(t)
    assert second_routes == [], f"the second pass ignored the cooldown the first one wrote: {second_routes}"
    assert "refused" in second_out and second_collected == []
