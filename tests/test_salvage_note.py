"""#771: a failed run's child can leave commits on a local branch that no remote holds. The loop never pushes them (#361);
rule O keeps the tree as auto/salvage-<n>, and the next dispatch of the issue names that branch and sha, so the new run can
reuse the work instead of starting again."""
import os
import subprocess
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "contrib" / "squad-goal" / "scripts"
GIT = ["/usr/bin/git", "-c", "user.email=a@b", "-c", "user.name=t"]


def note(tmp_path, salvage):
    co, s, st = (tmp_path / d for d in ("co", "s", "st"))
    for d in (co, s, st / "auto"):
        d.mkdir(parents=True)
    for f in ("startability.sh", "skiplabels.sh", "ghcall.sh"):
        (s / f).write_bytes((SCRIPTS / f).read_bytes())
    if salvage is not None:
        (st / "auto" / "salvage-34").write_text(salvage)
    env = {k: v for k, v in os.environ.items() if not k.startswith("SG_")}
    env.update(SG_CHECKOUT=str(co), SG_STATE=str(st))
    out = subprocess.run(["bash", "-c", f'. "{s}/startability.sh"; sg_issue_worktree_note 34'],
                         env=env, capture_output=True, text=True, timeout=60)
    return out.stdout + out.stderr


def tree(tmp_path):
    w = tmp_path / "ws"
    w.mkdir()
    subprocess.run([*GIT, "-C", str(w), "init", "-q", "-b", "gui/34-x"], check=True)
    (w / "f").write_text("1\n")
    subprocess.run([*GIT, "-C", str(w), "add", "f"], check=True)
    subprocess.run([*GIT, "-C", str(w), "commit", "-q", "-m", "work"], check=True)
    sha = subprocess.run([*GIT, "-C", str(w), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    return w, sha


def test_the_redispatch_names_the_salvage_branch_and_sha(tmp_path):
    w, sha = tree(tmp_path)
    out = note(tmp_path / "fx", f"gui/34-x {sha[:9]} in {w}\n")
    assert "SALVAGE" in out and "gui/34-x" in out and sha[:9] in out and str(w) in out, out


def test_a_salvage_tree_that_is_gone_is_not_named(tmp_path):
    out = note(tmp_path / "fx", f"gui/34-x abcdef123 in {tmp_path / 'gone'}\n")
    assert "SALVAGE" not in out, out


def test_no_salvage_fact_names_nothing(tmp_path):
    assert "SALVAGE" not in note(tmp_path / "fx", None)
