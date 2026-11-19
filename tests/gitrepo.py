"""A temporary git repository for action and workspace tests."""
import subprocess
from pathlib import Path


def sh(cwd, *args):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def sh_result(cwd, *args):
    """Run a command and answer the finished process, so a caller reads the return code of one it expects to fail."""
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True)


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


def with_origin(co, base="main"):
    """Make `co` a repository whose files are one commit, with a local bare origin that holds `base`: launch.py fetches
    origin/<base> before a run (#580), so its checkout needs an origin, and a file:// path keeps the fetch off the network."""
    co = Path(co)
    origin = co.parent / (co.name + "-origin.git")

    def g(cwd, *a):
        return sh(cwd, "git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "maintenance.auto=false", *a)

    g(co, "init", "-q", "-b", base)
    g(co, "add", "-A")
    g(co, "commit", "-q", "--allow-empty", "-m", "base")
    g(co.parent, "init", "-q", "--bare", str(origin))
    g(co, "remote", "add", "origin", origin.resolve().as_uri())
    g(co, "push", "-q", "origin", base)
    return co
