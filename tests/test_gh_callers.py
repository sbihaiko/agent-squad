"""The way through is the door, and a refusal never reaches a caller as an answer or as success (#8, ADR-0277).

`contrib/squad-goal/scripts/ghcall.sh` is what a loop script reaches GitHub through: `gh_api` for a read, `gh_get` for a
read GitHub answers with another representation, `gh_send` for a write. All three carry the interface's three outcomes
in the exit code -- 0 the answer, 2 GitHub refused, 1 no verdict -- and none of them lets a refusal stand in for data.
Every case here drives a stub `gh` on PATH, so no case touches the network, and the caller case drives a real loop
script (`acscan.sh`) end to end: that is the migration proven at the seam a caller uses, not the absence of a call site.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "contrib/squad-goal/scripts"

# GitHub's own refusal, as it arrives on stdout with a non-zero exit: the body that used to become the open-PR list.
RATE_LIMIT = {
    "message": "API rate limit exceeded for user ID 122891372. Include the request ID 06F8:34CAED:22CD6B:3FC4397:6ACC6C2A and timestamp 2026-10-12 05:12:10 UTC",
    "documentation_url": "https://docs.github.com/en/rest/using-the-rest-api/getting-started-with-the-rest-api",
    "status": "403",
}


def sandbox(tmp_path: Path, gh_body: str, gh_rc: int = 0, *scripts: str) -> Path:
    """A loop sandbox: the door (with the module beside it), a stub `gh`, and the scripts under test."""
    (tmp_path / "bin").mkdir(parents=True, exist_ok=True)
    for name in ("ghcall.sh", "ghread.py"):
        shutil.copy(SCRIPTS / name, tmp_path / name)
    for name in scripts:
        shutil.copy(SCRIPTS / name, tmp_path / name)
    stub = tmp_path / "bin/gh"
    stub.write_text(f"#!/bin/bash\nprintf '%s' '{gh_body}'\nexit {gh_rc}\n")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    (tmp_path / "env.sh").write_text(
        f"export SG_REPO=x/y SG_OWNER=o SG_PROJECT=7 SG_STATE={tmp_path}/st SG_CHECKOUT={tmp_path}/co\n"
    )
    (tmp_path / "st").mkdir(exist_ok=True)
    return tmp_path


def env_for(tmp_path: Path) -> dict[str, str]:
    """The sandbox environment: the stub `gh` first on PATH, HOME under the sandbox, and no inherited SG_*."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("SG_")}
    env["PATH"] = f"{tmp_path}/bin:{env['PATH']}"
    env["HOME"] = str(tmp_path)
    return env


def run(tmp_path: Path, line: str) -> subprocess.CompletedProcess:
    """One shell line in the sandbox, with the stub `gh` first on PATH and HOME off the live environment."""
    env = env_for(tmp_path)
    return subprocess.run(
        ["bash", "-c", f'. ./ghcall.sh\n{line}'],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_a_read_answers_and_the_caller_projects_it(tmp_path: Path) -> None:
    """The filter is the caller's and it is applied here, on the answer, so the transport always speaks raw JSON."""
    t = sandbox(tmp_path, json.dumps([{"number": 7}, {"number": 9}]), 0)
    p = run(t, "gh_api 'repos/o/r/pulls?state=open' -q '.[].number'; echo \"rc=$?\"")
    assert p.stdout.split() == ["7", "9", "rc=0"], f"the answer and its projection: {p.stdout!r} {p.stderr}"


def test_a_refused_read_is_not_an_answer(tmp_path: Path) -> None:
    """The whole point: exit 2, GitHub's own words on the line, and nothing a caller could mistake for an empty list."""
    t = sandbox(tmp_path, json.dumps(RATE_LIMIT), 1)
    p = run(t, "gh_api 'repos/o/r/pulls?state=open' -q '.[].number'; echo \"rc=$?\"")
    assert "rc=2" in p.stdout, f"a refusal is its own outcome: {p.stdout!r} {p.stderr}"
    assert "rate limit exceeded" in p.stdout, f"GitHub's own message must survive: {p.stdout!r}"
    assert "06F8:34CAED:22CD6B:3FC4397:6ACC6C2A" in p.stdout, f"and its request id: {p.stdout!r}"


def test_a_read_that_reached_no_verdict_is_a_failure(tmp_path: Path) -> None:
    """A body that is not GitHub's refusal shape, with a non-zero exit, is `failed`: not an answer and not a refusal."""
    t = sandbox(tmp_path, "", 1)
    p = run(t, "gh_api 'repos/o/r/pulls?state=open'; echo \"rc=$?\"")
    assert "rc=1" in p.stdout, f"no verdict is its own outcome: {p.stdout!r} {p.stderr}"


def test_a_write_through_the_door_reports_success_only_when_github_accepted_it(tmp_path: Path) -> None:
    """A refused write must never look like success: `gh_send … && touch moved` may not mark a card GitHub refused."""
    refused = sandbox(tmp_path / "refused", json.dumps(RATE_LIMIT), 1)
    p = run(refused, "gh_send -X PATCH 'repos/o/r/issues/7' -f state=closed >/dev/null && echo MOVED; echo \"rc=$?\"")
    assert "MOVED" not in p.stdout, f"a refused write is not success: {p.stdout!r} {p.stderr}"
    accepted = sandbox(tmp_path / "accepted", json.dumps({"id": 42}), 0)
    q = run(accepted, "gh_send -X POST 'repos/o/r/issues/7/comments' -q .id")
    assert q.stdout.strip() == "42", f"an accepted write answers, projected: {q.stdout!r} {q.stderr}"


def test_a_read_of_another_representation_still_tells_refusal_from_answer(tmp_path: Path) -> None:
    """The diff the review reads is not JSON: the body passes through, and a refusal is still exit 2, not an empty diff."""
    answer = sandbox(tmp_path / "answer", "diff --git a/x b/x\n+one line\n", 0)
    p = run(answer, "gh_get -H 'Accept: application/vnd.github.diff' repos/o/r/pulls/7")
    assert "+one line" in p.stdout, f"the diff is the answer: {p.stdout!r} {p.stderr}"
    refused = sandbox(tmp_path / "refused", json.dumps(RATE_LIMIT), 1)
    q = run(refused, "gh_get repos/o/r/pulls/7; echo \"rc=$?\"")
    assert "rc=2" in q.stdout, f"a refused diff is not an empty diff: {q.stdout!r} {q.stderr}"


def test_the_migrated_caller_reads_through_the_door_and_keeps_a_refusal_out_of_the_brief(tmp_path: Path) -> None:
    """`acscan.sh`, end to end: the brief carries the issue it read, and a refusal stops it instead of filling the file.

    This is the migration's own proof. The script has no `gh api` of its own any more, and the case that matters is the
    one the interface exists for: GitHub refusing must not put GitHub's words where the brief belongs.
    """
    answer = sandbox(
        tmp_path / "answer",
        json.dumps({"number": 7, "title": "The strip", "body": "It flickers."}),
        0,
        "acscan.sh",
    )
    p = subprocess.run(
        ["bash", str(answer / "acscan.sh"), "7"], capture_output=True, text=True, check=False, env=env_for(answer)
    )
    assert p.returncode == 0, f"the scan ran: {p.stdout} {p.stderr}"
    brief = Path(p.stdout.strip().splitlines()[-1]).read_text()
    assert "## Issue #7: The strip" in brief and "It flickers." in brief, f"the brief carries the issue: {brief[:400]}"
    refused = sandbox(tmp_path / "refused", json.dumps(RATE_LIMIT), 1, "acscan.sh")
    q = subprocess.run(
        ["bash", str(refused / "acscan.sh"), "7"], capture_output=True, text=True, check=False, env=env_for(refused)
    )
    assert q.returncode == 1, f"a refused read stops the scan: rc={q.returncode} {q.stdout} {q.stderr}"
    assert "cannot read issue #7" in q.stderr, f"and says so: {q.stderr!r}"
    written = list((refused / "st").glob("acscan-*.md"))
    assert written, "the brief is written"
    assert "rate limit exceeded" not in written[0].read_text(), "a refusal is not an answer in the brief"
