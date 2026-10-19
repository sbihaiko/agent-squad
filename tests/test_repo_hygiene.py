"""The repository is left clean: no worktree survives the work it was made for.

A worktree made for a piece of work is removed when that work merges into `main`, and its branch goes
with it. A worktree whose HEAD holds no commit that `main` does not already have is finished work left behind:
it costs a checkout, it confuses the next reader, and it can hide a stale build. Twelve such worktrees sat under
`.claude/worktrees/` on 2026-10-02, every one of them fully merged.

The main checkout and the worktree this test runs in are exempt. The current worktree is the one being worked in,
and a fresh one holding no commit yet is not stale — it is in use.

Guard for the clean-repository rule.
"""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def git(*args, cwd=ROOT):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True).stdout.strip()


def worktrees():
    """Every registered worktree as (path, head), the main checkout first."""
    rows, path = [], None
    for line in git("worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            path = line.split(" ", 1)[1].strip()
        elif line.startswith("HEAD ") and path:
            rows.append((Path(path), line.split(" ", 1)[1].strip()))
    return rows


def test_no_finished_worktree_is_left_behind():
    rows = worktrees()
    assert rows, "git worktree list returned nothing"
    main_head, here = rows[0][1], Path.cwd().resolve()
    stale = []
    for path, head in rows:
        if path.resolve() == here or path.resolve() == rows[0][0].resolve():
            continue
        if ".claude/worktrees" not in path.as_posix():
            continue  # live sibling checkouts (for example ~/wt-as-*) are other sessions, not leftover work
        ahead = git("rev-list", "--count", f"{main_head}..{head}") if path.exists() else "?"
        if ahead == "0":
            stale.append(str(path))
    assert stale == [], f"worktrees holding nothing main does not already have: {stale}"


def test_a_locked_worktree_is_a_live_agents_and_is_exempt():
    """A locked worktree (the porcelain `locked` line) is a live agent's, so it is never stale; an unlocked one at main is."""
    text = "\n".join([
        "worktree /r", "HEAD aaa", "branch refs/heads/dev", "",
        "worktree /r/.claude/worktrees/live", "HEAD aaa", "branch refs/heads/x", "locked", "",
        "worktree /r/.claude/worktrees/done", "HEAD aaa", "branch refs/heads/y", "",
    ])
    rows = parse_worktrees(text)
    assert [r[2] for r in rows] == [False, True, False]
    assert stale_worktrees(rows, Path("/elsewhere"), lambda main, head: "0") == ["/r/.claude/worktrees/done"]
