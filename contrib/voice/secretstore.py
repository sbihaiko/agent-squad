"""Resolve a secret in one order: the macOS Keychain, then the project's .env, then a sibling project's .env.

The project is the git repository the caller stands in (`git rev-parse --show-toplevel`), so a script run from a
subfolder still reads the project's own file. A `.env` that git tracks is never a source: a secret there is committed.
A value is never printed or logged. The pipeline scripts share this one lookup instead of keeping a copy each.
"""
import os
import subprocess

TIMEOUT = 15


def keychain(service):
    out = subprocess.run(["security", "find-generic-password", "-s", service, "-w"],
                         capture_output=True, text=True, timeout=TIMEOUT)
    return out.stdout.strip() or None if out.returncode == 0 else None


def tracked(path):
    """True when git tracks `path`: a secret there is committed, so it is not a source. A path outside git is untracked."""
    try:
        out = subprocess.run(["git", "-C", os.path.dirname(os.path.abspath(path)), "ls-files", "--error-unmatch",
                              os.path.basename(path)], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0


def env_value(path, name):
    """The value `name` holds in an env file, or None. It is returned to the caller, never printed."""
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line.startswith(f"{name}="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'") or None
    except OSError:
        return None
    return None


def project_root(cwd=None):
    """The repository the caller stands in, else the working directory: a run from a subfolder still reads its .env."""
    here = cwd or os.getcwd()
    try:
        out = subprocess.run(["git", "-C", here, "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=10)
        top = out.stdout.strip()
        if out.returncode == 0 and top:
            return top
    except (OSError, subprocess.SubprocessError):
        pass
    return here


def env_candidates(root, sibling):
    """The env files in order: the project's own, then the sibling project's, each once."""
    out = [os.path.join(root, ".env")]
    if sibling:
        p = os.path.join(os.path.expanduser(sibling), ".env")
        if os.path.abspath(p) not in {os.path.abspath(x) for x in out}:
            out.append(p)
    return out


def find(names, env_names, sibling=None, cwd=None):
    """The first value found: Keychain entries in `names`, then `env_names` in the project .env, then the sibling's."""
    for n in names:
        v = keychain(n)
        if v:
            return v
    for p in env_candidates(project_root(cwd), sibling):
        if not os.path.isfile(p) or tracked(p):
            continue
        for name in env_names:
            v = env_value(p, name)
            if v:
                return v
    return None
