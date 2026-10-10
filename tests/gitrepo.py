"""A temporary git repository for action and workspace tests."""
import subprocess
from pathlib import Path


def sh(cwd, *args):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


_TEMPLATE = None


def _template():
    """The base repository, built once per session (an outcome of fewer forks, not a target)."""
    global _TEMPLATE
    if _TEMPLATE is None:
        import atexit
        import shutil
        import tempfile
        root = Path(tempfile.mkdtemp(prefix="squad-repo-template-"))
        atexit.register(shutil.rmtree, root, True)
        _TEMPLATE = root / "base"
        _TEMPLATE.mkdir()
        sh(_TEMPLATE, "git", "init", "-q", "-b", "main")
        sh(_TEMPLATE, "git", "config", "user.email", "t@t")
        sh(_TEMPLATE, "git", "config", "user.name", "t")
        sh(_TEMPLATE, "git", "config", "maintenance.auto", "false")  # no background lock file to race the copy
        sh(_TEMPLATE, "git", "config", "gc.auto", "0")
        (_TEMPLATE / "base.txt").write_text("base\n")
        sh(_TEMPLATE, "git", "add", "-A")
        sh(_TEMPLATE, "git", "commit", "-q", "-m", "base")
    return _TEMPLATE


def make_repo(path):
    """A fresh repo with one commit, copied from the session template: no git init per test."""
    import shutil
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(_template(), path, dirs_exist_ok=True, ignore=shutil.ignore_patterns("*.lock"))
    return path


def sibling_repo(run_dir):
    """A git repo next to a run directory, made once: a target for tests whose dispatch children get worktrees."""
    path = Path(str(run_dir) + "-target")
    return path if (path / ".git").exists() else make_repo(path)
