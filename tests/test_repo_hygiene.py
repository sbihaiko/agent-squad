"""The repository is left clean: no worktree survives the work it was made for.

A worktree made for a piece of work is removed when that work merges into `main`, and its branch goes
with it. A worktree whose HEAD holds no commit that `main` does not already have is finished work left behind:
it costs a checkout, it confuses the next reader, and it can hide a stale build. Twelve such worktrees sat under
`.claude/worktrees/` on 2026-10-02, every one of them fully merged.

The main checkout and the worktree this test runs in are exempt. The current worktree is the one being worked in,
and a fresh one holding no commit yet is not stale — it is in use. A locked worktree is a live agent's, and is exempt too.

Guard for the clean-repository rule.
"""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def git(*args, cwd=ROOT):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True).stdout.strip()


def parse_worktrees(text):
    """Every worktree of `git worktree list --porcelain` as [path, head, locked], the main checkout first."""
    rows = []
    for line in text.splitlines():
        if line.startswith("worktree "):
            rows.append([Path(line.split(" ", 1)[1].strip()), None, False])
        elif line.startswith("HEAD ") and rows:
            rows[-1][1] = line.split(" ", 1)[1].strip()
        elif (line == "locked" or line.startswith("locked ")) and rows:
            rows[-1][2] = True
    return [r for r in rows if r[1]]


def stale_worktrees(rows, here, ahead):
    """The worktrees under .claude/worktrees that hold no commit main lacks. The main checkout, the current worktree and a
    locked worktree are exempt: a locked one is a live agent's, which may hold no commit yet."""
    main, main_head = rows[0][0], rows[0][1]
    stale = []
    for path, head, locked in rows:
        if locked or path.resolve() == here or path.resolve() == main.resolve():
            continue
        if ".claude/worktrees" not in path.as_posix():
            continue  # live sibling checkouts (for example ~/wt-as-*) are other sessions, not leftover work
        if ahead(main_head, head) == "0":
            stale.append(str(path))
    return stale


def test_the_repository_tracks_no_virtualenv():
    """A `.venv` in the index is a symlink one agent committed from its worktree (2026-10-21, #737).

    `.gitignore` held `.venv/`, which does not match a file named `.venv`, so nothing refused it. Every checkout then
    restored the link over the real venv, every gate fell back to a system python3 without mypy, and the promotion
    failed three times before the cause was read.
    """
    assert git("ls-files", "--", ".venv") == "", "the repository tracks .venv: every checkout would replace the real venv with it"
    assert subprocess.run(["git", "check-ignore", "-q", ".venv"], cwd=ROOT).returncode == 0, \
        ".gitignore must ignore the bare name .venv, not only the directory: a symlink named .venv is a file"


def test_the_repository_tracks_no_scratch_output():
    """A child's scratch output is never committed: a 21-byte `select.out` rode a wave landing into dev (#1565).

    A child leaves its debug output in the shared worktree, and a landing that stages the tree carries it, so the file
    reaches every checkout and the promotion. The guard reads the index, so it fails the branch that adds one.
    """
    junk = [p for p in git("ls-files").splitlines() if p.endswith(".out")]
    assert junk == [], f"tracked scratch output: {junk}"


def test_no_finished_worktree_is_left_behind():
    rows = parse_worktrees(git("worktree", "list", "--porcelain"))
    assert rows, "git worktree list returned nothing"
    stale = stale_worktrees(rows, Path.cwd().resolve(), lambda main, head: git("rev-list", "--count", f"{main}..{head}"))
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
